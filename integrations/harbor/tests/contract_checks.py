import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import neurico_harbor_agent.agent as agent_module
import neurico_harbor_agent.run_autoresearch as runner_module
import pytest
from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.interfaces import Client
from neurico_harbor_agent.agent import (
    NeuricoHarborAgent,
    _autoresearch_iterations,
    _terminate_process_tree,
    _text_from_prompt,
)
from neurico_harbor_agent.run_autoresearch import execute_autoresearch
from neurico_harbor_agent.runtime import (
    CodexInferenceConnection,
    HarborAutoResearchTask,
    build_autoresearch_environment,
    build_harbor_idea,
)


def test_prompt_text_is_preserved_verbatim() -> None:
    instruction = "First line\n\n  indented line with : and #\n"
    assert _text_from_prompt([{"type": "text", "text": instruction}]) == instruction


def test_harbor_instruction_uses_neurico_idea_contract(tmp_path: Path) -> None:
    instruction = "# Compare cache policies\n\nTest LRU and LFU on the supplied workload.\n"
    task = HarborAutoResearchTask(instruction=instruction, workspace=tmp_path)
    result = build_harbor_idea(task)
    idea = result["idea"]

    assert idea["title"] == "Compare cache policies"
    assert idea["background"]["description"] == instruction
    assert idea["hypothesis"].startswith("# Compare cache policies")
    assert idea["metadata"]["source"] == "harbor"
    assert idea["metadata"]["local_workspace"] == str(tmp_path)
    assert len(idea["metadata"]["instruction_sha256"]) == 64


def test_hosted_gateway_uses_neutral_credential_and_full_model(tmp_path: Path) -> None:
    connection = CodexInferenceConnection.from_environment(
        {
            "HOSTED_INFERENCE_TOKEN": "secret",
            "HOSTED_INFERENCE_URL": "https://gateway.example/v1",
        },
        requested_model="openrouter/example/model",
    )
    assert connection.backend_model == "openrouter/example/model"
    environment = connection.prepare_codex_home(tmp_path / "codex-home")
    config = (tmp_path / "codex-home" / "config.toml").read_text()
    assert environment["HOSTED_INFERENCE_TOKEN"] == "secret"
    assert 'model = "openrouter/example/model"' in config
    assert 'base_url = "https://gateway.example/v1"' in config
    assert 'env_key = "HOSTED_INFERENCE_TOKEN"' in config
    assert "secret" not in config


def test_chatgpt_auth_mode_strips_openai_provider_prefix(tmp_path: Path) -> None:
    auth_file = tmp_path / "mounted-auth.json"
    auth_file.write_text('{"tokens": {}}')
    connection = CodexInferenceConnection.from_environment(
        {"NEURICO_CODEX_AUTH_FILE": str(auth_file)},
        requested_model="openai/gpt-5.6-sol",
    )
    assert connection.mode == "chatgpt"
    assert connection.backend_model == "gpt-5.6-sol"


def test_direct_non_openai_model_is_rejected(tmp_path: Path) -> None:
    auth_file = tmp_path / "mounted-auth.json"
    auth_file.write_text('{"tokens": {}}')
    with pytest.raises(ValueError, match="requires an OpenAI model"):
        CodexInferenceConnection.from_environment(
            {"NEURICO_CODEX_AUTH_FILE": str(auth_file)},
            requested_model="anthropic/claude-sonnet-5",
        )


def test_environment_exposes_locked_codex_and_isolated_home(tmp_path: Path) -> None:
    auth_file = tmp_path / "mounted-auth.json"
    auth_file.write_text('{"tokens": {}}')
    connection = CodexInferenceConnection(
        requested_model="openai/test-model",
        mode="chatgpt",
        auth_file=auth_file,
    )
    ideas_dir = tmp_path / "control" / "ideas"
    codex_home = tmp_path / "control" / "codex-home"
    environment = build_autoresearch_environment(
        {"PATH": "/usr/bin"},
        connection,
        ideas_dir=ideas_dir,
        codex_home=codex_home,
    )
    assert environment["NEURICO_IDEAS"] == str(ideas_dir)
    assert environment["CODEX_HOME"] == str(codex_home)
    assert (codex_home / "auth.json").read_text() == '{"tokens": {}}'
    assert 'model = "test-model"' in (codex_home / "config.toml").read_text()
    first_path = Path(environment["PATH"].split(os.pathsep)[0])
    assert (first_path / "codex").is_file()


def test_iteration_override_must_be_positive() -> None:
    assert _autoresearch_iterations({}) == 1
    assert _autoresearch_iterations({"NEURICO_HARBOR_AUTORESEARCH_ITERATIONS": "3"}) == 3
    with pytest.raises(ValueError, match="at least 1"):
        _autoresearch_iterations({"NEURICO_HARBOR_AUTORESEARCH_ITERATIONS": "0"})


def test_cancellation_terminates_autoresearch_process_group() -> None:
    async def exercise() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            start_new_session=True,
        )
        await _terminate_process_tree(process)
        assert process.returncode is not None

    asyncio.run(exercise())


def test_child_entrypoint_invokes_existing_autoresearch_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeManager:
        def __init__(self, ideas_dir: Path):
            captured["ideas_dir"] = ideas_dir

        def submit_idea(self, idea: dict[str, Any], validate: bool) -> str:
            captured["idea"] = idea
            captured["validate"] = validate
            return "harbor-idea"

    class FakeRunner:
        def __init__(self, *, use_github: bool):
            captured["use_github"] = use_github

        def run_research(self, **kwargs: Any) -> dict[str, Any]:
            captured["run"] = kwargs
            return {"success": True, "work_dir": str(tmp_path)}

    monkeypatch.setattr(runner_module, "IdeaManager", FakeManager)
    monkeypatch.setattr(runner_module, "ResearchRunner", FakeRunner)

    result = execute_autoresearch(
        instruction="Investigate whether the supplied cache can reduce tail latency.",
        workspace=tmp_path,
        ideas_dir=tmp_path / "ideas",
        iterations=2,
    )

    assert result["success"] is True
    assert captured["validate"] is True
    assert captured["use_github"] is False
    assert captured["idea"]["idea"]["metadata"]["local_workspace"] == str(tmp_path)
    assert captured["run"] == {
        "idea_id": "harbor-idea",
        "provider": "codex",
        "full_permissions": True,
        "multi_agent": True,
        "skip_resource_finder": True,
        "use_scribe": False,
        "write_paper": False,
        "scoring_enabled": True,
        "autoresearch": True,
        "autoresearch_iterations": 2,
    }


def test_acp_prompt_launches_autoresearch_not_direct_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instruction = "Keep this exact:\n\n  value: '# literal'\n"
    captured: dict[str, Any] = {}

    async def fake_autoresearch(**kwargs: Any) -> int:
        captured.update(kwargs)
        await kwargs["emit"]("AutoResearch output\n")
        return 0

    monkeypatch.setattr(agent_module, "run_autoresearch_process", fake_autoresearch)

    async def run_prompt() -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text('{"tokens": {}}')
        agent = NeuricoHarborAgent(
            {
                "NEURICO_MODEL": "openai/test-model",
                "NEURICO_CODEX_AUTH_FILE": str(auth_file),
            }
        )
        session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        response = await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": instruction}],
        )
        assert response.stop_reason == "end_turn"

    asyncio.run(run_prompt())
    assert captured["task"].instruction == instruction
    assert captured["task"].workspace == tmp_path
    assert captured["iterations"] == 1
    assert captured["connection"].backend_model == "test-model"
    assert captured["connection"].mode == "chatgpt"


def test_autoresearch_failure_fails_the_acp_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_autoresearch(**kwargs: Any) -> int:
        return 7

    monkeypatch.setattr(agent_module, "run_autoresearch_process", failing_autoresearch)

    async def run_prompt() -> None:
        auth_file = tmp_path / "auth.json"
        auth_file.write_text('{"tokens": {}}')
        agent = NeuricoHarborAgent(
            {
                "NEURICO_MODEL": "openai/test-model",
                "NEURICO_CODEX_AUTH_FILE": str(auth_file),
            }
        )
        session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        with pytest.raises(RuntimeError, match="status 7"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "Run AutoResearch on this task"}],
            )

    asyncio.run(run_prompt())


def test_stdio_acp_handshake_and_model_selection(tmp_path: Path) -> None:
    class RecordingClient(Client):
        async def request_permission(self, session_id, tool_call, options, **kwargs: Any):
            return {"outcome": {"outcome": "cancelled"}}

        async def session_update(self, session_id, update, **kwargs: Any) -> None:
            return None

    async def exercise_protocol() -> None:
        environment = dict(os.environ)
        environment["NEURICO_MODEL"] = "openai/test-model"
        async with spawn_agent_process(
            RecordingClient(),
            sys.executable,
            "-m",
            "neurico_harbor_agent",
            env=environment,
            cwd=tmp_path,
        ) as (connection, _process):
            initialized = await connection.initialize(protocol_version=PROTOCOL_VERSION)
            assert initialized.agent_info is not None
            assert initialized.agent_info.name == "neurico"

            session = await connection.new_session(cwd=str(tmp_path), mcp_servers=[])
            assert session.config_options is not None
            model_option = session.config_options[0]
            assert model_option.id == "model"
            assert model_option.current_value == "openai/test-model"

            response = await connection.set_config_option(
                session_id=session.session_id,
                config_id="model",
                value="openai/test-model",
            )
            assert response.config_options[0].current_value == "openai/test-model"

    asyncio.run(exercise_protocol())
