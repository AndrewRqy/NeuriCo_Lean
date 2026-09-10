from __future__ import annotations

import sys
import subprocess
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import core.hitl_autoresearch as hitl_autoresearch  # noqa: E402
import core.pipeline_orchestrator as pipeline_orchestrator  # noqa: E402
import core.scoring_seal as scoring_seal  # noqa: E402
from core.autoresearch import Checkpoint, CheckpointManager  # noqa: E402
from core.hitl import HitlIdeaLog, HitlRuntime  # noqa: E402
from core.hitl_frontier import HitlFrontierStore  # noqa: E402
from core.hitl_manager_react import HitlManager  # noqa: E402
from core.hitl_runtime_state import HitlRuntimeState, HitlRuntimeStateError  # noqa: E402
from core.manager_callable_agents import manager_agent_provenance  # noqa: E402
from core.pipeline_orchestrator import ResearchPipelineOrchestrator  # noqa: E402


def _request(request_id: str = "request-1") -> dict:
    return {
        "request_id": request_id,
        "agent": "resource_finder",
        "objective": "Find a public benchmark for the unresolved latency claim.",
        "reason": "The next experiment cannot distinguish implementation from data issues.",
    }


def _manager(tmp_path: Path, state: HitlRuntimeState) -> HitlManager:
    manager = HitlManager.__new__(HitlManager)
    manager.work_dir = tmp_path
    manager.runtime_state = state
    manager._resolution_lock = threading.Lock()
    manager._resolutions = {}
    return manager


def test_agent_request_has_independent_scheduler_state(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )

    requested = state.request_manager_agent_action(_request())
    assert requested["parent_sha"] == "parent"
    assert requested["status"] == "pending"
    assert requested["attempt_count"] == 0
    assert requested["recovery_count"] == 0
    assert "requested_agent_run" not in state.snapshot()["next_autoresearch_action"]
    assert HitlRuntimeState(tmp_path).manager_agent_action() == requested
    assert state.request_manager_agent_action(_request()) == requested
    with pytest.raises(HitlRuntimeStateError, match="Another manager agent action"):
        state.request_manager_agent_action(_request("request-2"))


def test_agent_request_completes_independent_proposal_decision(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )

    result = _manager(tmp_path, state).request_agent_run(
        "resource_finder",
        "Find a public benchmark for the unresolved latency claim.",
        "The next experiment cannot distinguish implementation from data issues.",
    )

    assert result.startswith("Runtime recorded the decision to run resource_finder")
    decision = state.snapshot()["next_autoresearch_action"]
    assert decision["kind"] == "prepare_proposal"
    assert decision["status"] == "decision_recorded"
    assert decision["decision"]["choice"] == "insert"
    action = HitlRuntimeState(tmp_path).manager_agent_action()
    assert action["parent_sha"] == "parent"


def test_proposal_preparation_requires_explicit_proceed_or_insert(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    manager = _manager(tmp_path, state)

    assert manager.is_tool_available("request_agent_run")
    assert manager.is_tool_available("proceed_to_proposal")
    response = manager.proceed_to_proposal("The current evidence is sufficient.")
    assert response.startswith("Runtime recorded the decision to proceed")
    assert not manager.is_tool_available("request_agent_run")
    assert not manager.is_tool_available("proceed_to_proposal")


def test_manager_request_is_hidden_when_no_further_proposal_will_run(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action({"kind": "select_frontier"})
    manager = _manager(tmp_path, state)

    assert not manager.is_tool_available("request_agent_run")
    assert manager.is_tool_available("select_frontier")


def test_scheduled_agent_uses_existing_seal_and_records_context(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action("request-1", parent_sha="parent")
    calls = []

    class FakeCheckpoints:
        current = "parent"

        def create_checkpoint(self, message):
            self.current = "context"
            return Checkpoint("context", message)

        def current_sha(self):
            return self.current

        def restore_checkpoint(self, sha, **_kwargs):
            self.current = sha

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    class FakeFrontier:
        def resource_context(self, _parent_sha):
            return None

        def retain_resource_context(self, parent_sha, context_sha):
            calls.append(("retained", parent_sha, context_sha))

    controller.hitl_frontier = FakeFrontier()
    controller.manager_callable_agent_runner = lambda agent, objective, invocation_id: (
        calls.append((agent, objective, invocation_id)) or {"success": True}
    )
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    assert calls == [
        (
            "resource_finder",
            "Find a public benchmark for the unresolved latency claim.",
            "request-1",
        ),
        ("retained", "parent", "context"),
    ]
    completed = HitlRuntimeState(tmp_path).manager_agent_action()
    assert completed["context_sha"] == "context"
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 1


def test_completed_context_is_restored_before_next_proposal(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1", parent_sha="parent", context_sha="context"
    )
    restored = []

    class FakeCheckpoints:
        def current_sha(self):
            return "parent"

        def restore_checkpoint(self, sha, **_kwargs):
            restored.append(sha)

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier", (), {"resource_context": lambda self, _parent: "context"}
    )()
    controller._advance_manager_agent_action("parent")

    assert restored == ["context"]


@pytest.mark.parametrize("between_commands", [False, True])
def test_interrupted_agent_resume_preserves_inflight_workspace(
    tmp_path,
    monkeypatch,
    between_commands,
):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    if not between_commands:
        state.begin_worker_command(
            {
                "request_key": "resource-finder-plan",
                "kind": "phase_finish",
                "pipeline_stage": "resource_finder",
                "hitl_stage": "plan",
            }
        )
    observed = []

    class FakeCheckpoints:
        current = "inflight"

        def current_sha(self):
            return self.current

        def restore_checkpoint(self, sha, **_kwargs):
            self.current = sha

        def create_checkpoint(self, message):
            self.current = "completed"
            return Checkpoint("completed", message)

    class FakeFrontier:
        def resource_context(self, _parent):
            return "prior-context"

        def retain_resource_context(self, _parent, _context):
            pass

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = FakeFrontier()
    controller.manager_callable_agent_runner = lambda *_args: (
        observed.append(controller.checkpoints.current_sha()) or {"success": True}
    )
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    assert observed == ["inflight"]


@pytest.mark.parametrize("with_pending_request", [False, True])
def test_manager_invocation_boundary_rolls_back_before_continuation(
    tmp_path,
    monkeypatch,
    with_pending_request,
):
    checkpoints = CheckpointManager(tmp_path)
    artifact = tmp_path / "resources.md"
    artifact.write_text("baseline\n", encoding="utf-8")
    checkpoints.create_checkpoint("baseline")

    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": checkpoints.current_sha()}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1",
        status="running",
        attempt_count=1,
    )
    orchestrator = ResearchPipelineOrchestrator(tmp_path, hitl_autoresearch=True)
    orchestrator.state.start_stage("resource_finder")
    orchestrator.state.complete_stage("resource_finder", True, {"initial": True})
    orchestrator._stage_rollback(
        "resource_finder",
        "manager invocation boundary",
        provenance=manager_agent_provenance("request-1"),
    )
    orchestrator.state.start_stage("resource_finder")
    artifact.write_text("partial invocation\n", encoding="utf-8")
    if with_pending_request:
        provenance = manager_agent_provenance("request-1")
        runtime_state = HitlRuntimeState(tmp_path)
        runtime_state.record_worker_continuation(
            {
                "pipeline_stage": "resource_finder",
                "hitl_stage": "execution",
                "actor": "resource_finder",
                "provenance": provenance,
                "prompt_block": "resume resource finding",
            }
        )
        runtime_state.begin_worker_command(
            {
                "request_key": "resource-finder-finish",
                "kind": "phase_finish",
                "pipeline_stage": "resource_finder",
                "hitl_stage": "execution",
                "provenance": provenance,
            }
        )

    class FakeRuntime:
        @staticmethod
        def abandon_pending_worker_request_for_rollback(_reason):
            pass

        @staticmethod
        def reload_manager_after_state_restore():
            pass

        @staticmethod
        def clear_idea_tool_context():
            pass

    recovering = ResearchPipelineOrchestrator(tmp_path, hitl_autoresearch=True)
    monkeypatch.setattr(recovering, "_create_hitl_runtime", lambda *_a, **_k: FakeRuntime())

    recovering.prepare_initial_resume()

    assert artifact.read_text(encoding="utf-8") == "baseline\n"
    recovered_action = HitlRuntimeState(tmp_path).manager_agent_action()
    assert recovered_action["request_id"] == "request-1"
    assert recovered_action["status"] == "running"
    assert recovered_action["attempt_count"] == 1
    assert HitlRuntimeState(tmp_path).pending_worker_command() is None
    assert HitlRuntimeState(tmp_path).worker_continuation() is None
    assert recovering.state.is_stage_completed("resource_finder")
    assert recovering.state.get_runtime_recovery("initial_stage") is None


def test_manager_invocation_boundary_rejects_mismatched_provenance(tmp_path):
    checkpoints = CheckpointManager(tmp_path)
    (tmp_path / "resources.md").write_text("baseline\n", encoding="utf-8")
    checkpoints.create_checkpoint("baseline")
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": checkpoints.current_sha()}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action("request-1", status="running", attempt_count=1)
    orchestrator = ResearchPipelineOrchestrator(tmp_path, hitl_autoresearch=True)
    orchestrator.state.start_stage("resource_finder")
    rollback = pipeline_orchestrator.HitlStageRollback.capture(
        tmp_path,
        "manager invocation boundary",
    )
    orchestrator.state.set_runtime_recovery(
        "initial_stage",
        {
            "stage": "resource_finder",
            "provenance": manager_agent_provenance("request-2"),
            **rollback.descriptor(),
        },
    )

    with pytest.raises(RuntimeError, match="does not match its persisted invocation"):
        ResearchPipelineOrchestrator(
            tmp_path,
            hitl_autoresearch=True,
        ).prepare_initial_resume()


def test_first_proposal_after_bootstrap_uses_scored_root_as_premise(tmp_path):
    log = HitlIdeaLog(tmp_path)
    runtime = HitlRuntime.__new__(HitlRuntime)
    runtime.log = log

    class FakeManager:
        @staticmethod
        def begin_proposal_preparation(*, parent_sha, premise_idea_id, on_decision):
            assert parent_sha == "root"
            assert premise_idea_id == log.records()[0]["idea_id"]
            return on_decision(
                {
                    "choice": "proceed",
                    "reason": "The scored root already provides enough evidence.",
                }
            )

    runtime.manager = FakeManager()

    class FakeCheckpoints:
        @staticmethod
        def current_sha():
            return "root"

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier", (), {"resource_context": lambda self, _parent: None}
    )()
    controller._proposal_hitl_runtime = lambda: runtime

    controller._prepare_next_proposal("root")

    records = log.records()
    assert len(records) == 2
    assert records[0]["idea_type"] == "evidence"
    assert records[0]["parent_node_id"] == "root"
    assert records[1]["idea_type"] == "decision"
    assert records[1]["decision"] == "O1"
    assert records[1]["premises"] == [records[0]["idea_id"]]


def test_replayed_proceed_decision_reuses_persisted_premise(tmp_path):
    log = HitlIdeaLog(tmp_path)
    runtime = HitlRuntime.__new__(HitlRuntime)
    runtime.log = log
    state = HitlRuntimeState(tmp_path)
    runtime.manager = _manager(tmp_path, state)

    premise = hitl_autoresearch.HitlAutoResearchController._proposal_preparation_premise_id(
        runtime,
        "root",
    )
    state.begin_next_autoresearch_action(
        {
            "kind": "prepare_proposal",
            "parent_sha": "root",
            "premise_idea_id": premise,
        }
    )
    decision = {
        "choice": "proceed",
        "reason": "The scored root already provides enough evidence.",
        "parent_sha": "root",
    }
    state.record_next_autoresearch_action_decision("prepare_proposal", decision)
    logged = runtime.log_proposal_preparation_decision(
        choice="proceed",
        reason=decision["reason"],
        parent_sha="root",
        premise_idea_id=premise,
    )

    class FakeCheckpoints:
        @staticmethod
        def current_sha():
            return "root"

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier", (), {"resource_context": lambda self, _parent: None}
    )()
    controller._proposal_hitl_runtime = lambda: runtime

    controller._prepare_next_proposal("root")

    records = log.records()
    assert len(records) == 2
    assert records[1]["idea_id"] == logged["idea_id"]
    assert records[1]["premises"] == [premise]
    assert state.snapshot()["next_autoresearch_action"] is None


def test_plan_approval_is_scoped_to_manager_invocation():
    ordinary = HitlRuntime.__new__(HitlRuntime)
    ordinary.pipeline_stage = "resource_finder"
    ordinary.invocation_id = ""
    first = HitlRuntime.__new__(HitlRuntime)
    first.pipeline_stage = "resource_finder"
    first.invocation_id = "request-1"
    second = HitlRuntime.__new__(HitlRuntime)
    second.pipeline_stage = "resource_finder"
    second.invocation_id = "request-2"

    assert ordinary._plan_approval_scope() == "resource_finder"
    assert first._plan_approval_scope() == "resource_finder:request-1"
    assert second._plan_approval_scope() == "resource_finder:request-2"


def test_manager_invocation_uses_standard_pipeline_stage_tracking(tmp_path, monkeypatch):
    orchestrator = ResearchPipelineOrchestrator(tmp_path)
    orchestrator.state.start_stage("resource_finder")
    orchestrator.state.complete_stage("resource_finder", True, {"initial": True})

    class FakeRuntime:
        def clear_idea_tool_context(self):
            pass

    class FakeRollback:
        def discard(self, **_kwargs):
            pass

    monkeypatch.setattr(orchestrator, "_create_hitl_runtime", lambda *_a, **_k: FakeRuntime())
    monkeypatch.setattr(
        pipeline_orchestrator,
        "generate_resource_finder_prompt",
        lambda *_a, **_k: "resource prompt",
    )
    monkeypatch.setattr(
        pipeline_orchestrator.HitlStageRollback,
        "capture",
        lambda *_a, **_k: FakeRollback(),
    )
    stage_calls = []
    monkeypatch.setattr(
        pipeline_orchestrator,
        "run_plan_centered_hitl_stage",
        lambda **kwargs: (
            stage_calls.append(kwargs)
            or kwargs["on_approved"](
                {"success": True, "outputs": {"refresh": True}},
                {"approved": True},
            )
        ),
    )

    result = orchestrator.run_hitl_agent_stage(
        "resource_finder",
        idea={"title": "demo"},
        provider="codex",
        timeout=None,
        full_permissions=False,
        manager_objective="Find a benchmark.",
        invocation_id="request-1",
    )

    assert result["success"]
    assert b'"refresh": true' in orchestrator.state.state_file.read_bytes()
    assert stage_calls[0]["plan_log_prefix"].endswith("_request-1")
    assert stage_calls[0]["execution_log_prefix"].endswith("_request-1")
    assert stage_calls[0]["provenance"] == {
        "kind": "manager_agent",
        "invocation_id": "request-1",
    }


def test_manager_invocation_retries_once_after_clean_rollback(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    calls = []

    class FakeCheckpoints:
        def create_checkpoint(self, message):
            return Checkpoint("context", message)

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier",
        (),
        {"retain_resource_context": lambda self, parent, context: None},
    )()
    results = [
        {"success": False, "error": "transient", "hitl_rollback_completed": True},
        {"success": True},
    ]
    controller.manager_callable_agent_runner = lambda *_args: (
        calls.append(_args) or results.pop(0)
    )
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    assert len(calls) == 2
    completed = state.manager_agent_action()
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert completed["recovery_count"] == 1
    assert completed["context_sha"] == "context"


def test_manager_invocation_stops_after_one_automatic_retry(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    calls = []

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.manager_callable_agent_runner = lambda *_args: (
        calls.append(_args)
        or {"success": False, "error": "deterministic", "hitl_rollback_completed": True}
    )
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    assert len(calls) == 2
    failed = state.manager_agent_action()
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 2
    assert failed["recovery_count"] == 1
    assert failed["last_error"] == "deterministic"


def test_terminal_manager_invocation_failure_is_not_retried(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    calls = []

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.manager_callable_agent_runner = lambda *_args: (
        calls.append(_args)
        or {
            "success": False,
            "error": "manager backend exhausted",
            "hitl_rollback_completed": True,
            "hitl_terminal_failure": True,
        }
    )
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    assert len(calls) == 1
    failed = state.manager_agent_action()
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert failed["recovery_count"] == 0
    assert failed["last_error"] == "manager backend exhausted"


def test_manager_invocation_does_not_continue_without_clean_rollback(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.manager_callable_agent_runner = lambda *_args: {
        "success": False,
        "error": "rollback failed",
    }
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="without completing rollback"):
        controller._advance_manager_agent_action("parent")

    interrupted = state.manager_agent_action()
    assert interrupted["status"] == "running"
    assert interrupted["attempt_count"] == 1
    assert interrupted["last_error"] == "rollback failed"


def test_interrupted_manager_invocation_consumes_its_only_retry(tmp_path, monkeypatch):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1",
        status="running",
        attempt_count=1,
    )

    class FakeCheckpoints:
        def create_checkpoint(self, message):
            return Checkpoint("context", message)

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier",
        (),
        {"retain_resource_context": lambda self, parent, context: None},
    )()
    controller.manager_callable_agent_runner = lambda *_args: {"success": True}
    monkeypatch.setattr(hitl_autoresearch, "seal_scoring_files", lambda *_a, **_k: Path("seal"))
    monkeypatch.setattr(scoring_seal, "unseal_scoring_files", lambda *_a, **_k: None)

    controller._advance_manager_agent_action("parent")

    completed = state.manager_agent_action()
    assert completed["status"] == "completed"
    assert completed["attempt_count"] == 2
    assert completed["recovery_count"] == 1


def test_interrupted_context_publication_finishes_without_rerunning_agent(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1",
        status="publishing",
        attempt_count=1,
        candidate_context_sha="context",
    )
    retained = []

    class FakeCheckpoints:
        @staticmethod
        def checkpoint_exists(sha):
            return sha == "context"

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier",
        (),
        {
            "retain_resource_context": (
                lambda self, parent, context: retained.append((parent, context))
            )
        },
    )()
    controller.manager_callable_agent_runner = lambda *_args: pytest.fail(
        "publishing recovery must not rerun the agent"
    )

    controller._advance_manager_agent_action("parent")

    assert retained == [("parent", "context")]
    completed = state.manager_agent_action()
    assert completed["status"] == "completed"
    assert completed["context_sha"] == "context"
    assert completed["attempt_count"] == 1


def test_failed_manager_invocation_returns_to_existing_decision_boundary(tmp_path):
    log = HitlIdeaLog(tmp_path)
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1",
        status="failed",
        attempt_count=2,
        recovery_count=1,
        last_error="deterministic",
    )
    state.record_next_autoresearch_action_decision(
        "prepare_proposal",
        {"choice": "insert", "parent_sha": "parent"},
    )
    state.complete_next_autoresearch_action("prepare_proposal", {"choice": "insert"})
    state.clear_completed_next_autoresearch_action("prepare_proposal")
    observed = []

    runtime = HitlRuntime.__new__(HitlRuntime)
    runtime.log = log

    class FakeManager:
        @staticmethod
        def begin_proposal_preparation(**kwargs):
            observed.append(kwargs["prior_agent_failure"])
            return kwargs["on_decision"](
                {"choice": "proceed", "reason": "Continue with existing evidence."}
            )

    runtime.manager = FakeManager()

    class FakeCheckpoints:
        @staticmethod
        def current_sha():
            return "parent"

    controller = hitl_autoresearch.HitlAutoResearchController.__new__(
        hitl_autoresearch.HitlAutoResearchController
    )
    controller.work_dir = tmp_path
    controller.checkpoints = FakeCheckpoints()
    controller.hitl_frontier = type(
        "FakeFrontier", (), {"resource_context": lambda self, _parent: None}
    )()
    controller._proposal_hitl_runtime = lambda: runtime

    controller._prepare_next_proposal("parent")

    assert observed[0]["request_id"] == "request-1"
    assert observed[0]["last_error"] == "deterministic"
    assert state.manager_agent_action() is None


def test_next_request_uses_selected_parent_without_copying_completed_context(tmp_path):
    state = HitlRuntimeState(tmp_path)
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )
    state.request_manager_agent_action(_request())
    state.update_manager_agent_action(
        "request-1", parent_sha="parent", context_sha="context-1"
    )
    state.record_next_autoresearch_action_decision(
        "prepare_proposal",
        {"choice": "insert", "parent_sha": "parent"},
    )
    state.complete_next_autoresearch_action("prepare_proposal", {"choice": "insert"})
    state.clear_completed_next_autoresearch_action("prepare_proposal")
    state.begin_next_autoresearch_action(
        {"kind": "prepare_proposal", "parent_sha": "parent"}
    )

    second = state.request_manager_agent_action(_request("request-2"))

    assert second["parent_sha"] == "parent"
    assert "base_parent_sha" not in second
    assert "base_context_sha" not in second


def test_resource_context_refs_survive_git_gc_for_each_frontier(tmp_path):
    checkpoints = CheckpointManager(tmp_path)
    (tmp_path / "artifact.txt").write_text("A\n", encoding="utf-8")
    parent_a = checkpoints.create_checkpoint("parent A").sha
    (tmp_path / "resource.txt").write_text("context A\n", encoding="utf-8")
    context_a = checkpoints.create_checkpoint("context A").sha
    checkpoints.restore_checkpoint(parent_a, clean_untracked_public=True)
    (tmp_path / "artifact.txt").write_text("B\n", encoding="utf-8")
    parent_b = checkpoints.create_checkpoint("parent B").sha
    (tmp_path / "resource.txt").write_text("context B\n", encoding="utf-8")
    context_b = checkpoints.create_checkpoint("context B").sha
    frontier = HitlFrontierStore(tmp_path)
    frontier.retain_resource_context(parent_a, context_a)
    frontier.retain_resource_context(parent_b, context_b)

    subprocess.run(
        ["git", "reflog", "expire", "--expire=now", "--all"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "gc", "--prune=now"], cwd=tmp_path, check=True)

    assert frontier.resource_context(parent_a) == context_a
    assert frontier.resource_context(parent_b) == context_b
    assert checkpoints.checkpoint_exists(context_a)
    assert checkpoints.checkpoint_exists(context_b)


def test_verification_artifact_is_excluded_from_public_checkpoint(tmp_path):
    (tmp_path / "artifact.txt").write_text("public\n", encoding="utf-8")
    verification = tmp_path / "scoring" / "verification.json"
    verification.parent.mkdir(parents=True)
    verification.write_text('{"secret": true}\n', encoding="utf-8")

    checkpoint = CheckpointManager(tmp_path).create_checkpoint("public")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{checkpoint.sha}:scoring/verification.json"],
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode != 0


def test_proceed_and_insert_choices_are_logged_as_manager_decisions(tmp_path):
    log = HitlIdeaLog(tmp_path)
    premise = log.append(
        {
            "pipeline_stage": "experiment_runner",
            "hitl_stage": "review",
            "idea_type": "evidence",
            "idea_category": "experiment_result",
            "level": "C",
            "actor": "experiment_runner",
            "premises": [],
            "context": "A scored frontier node is available.",
            "evidence": "The runtime retained the scored node.",
            "related_artifacts": [],
            "raised": False,
        }
    )
    runtime = HitlRuntime.__new__(HitlRuntime)
    runtime.log = log

    inserted = runtime.log_proposal_preparation_decision(
        choice="insert",
        reason="External evidence is still missing.",
        parent_sha="parent",
        premise_idea_id=premise["idea_id"],
        agent="resource_finder",
        objective="Find a public benchmark.",
        request_id="request-1",
    )
    proceeded = runtime.log_proposal_preparation_decision(
        choice="proceed",
        reason="The refreshed evidence is sufficient.",
        parent_sha="parent",
        premise_idea_id=inserted["idea_id"],
    )

    assert inserted["decision"] == "O2"
    assert proceeded["decision"] == "O1"
    assert proceeded["premises"] == [inserted["idea_id"]]


def test_shared_dispatcher_routes_resource_finder_to_existing_hitl_stage():
    orchestrator = ResearchPipelineOrchestrator.__new__(ResearchPipelineOrchestrator)
    calls = []
    orchestrator._run_resource_finder_hitl = lambda **kwargs: calls.append(kwargs) or {
        "success": True
    }

    result = orchestrator.run_hitl_agent_stage(
        "resource_finder",
        idea={"title": "demo"},
        provider="codex",
        timeout=None,
        full_permissions=False,
        manager_objective="Find a benchmark.",
        invocation_id="request-1",
    )

    assert result["success"]
    assert calls[0]["invocation_id"] == "request-1"


def test_manager_objective_is_appended_to_each_resource_prompt():
    prompt = ResearchPipelineOrchestrator._with_manager_resource_objective(
        "base prompt", "Find licensing evidence only."
    )
    assert "base prompt" in prompt
    assert "MANAGER-REQUESTED RESOURCE REFRESH" in prompt
    assert "Find licensing evidence only." in prompt
