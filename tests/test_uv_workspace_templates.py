"""Regression tests for the dependency-only research workspace scaffold."""

from pathlib import Path
import re
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from templates.prompt_generator import PromptGenerator  # noqa: E402


TEMPLATE_ROOT = PROJECT_ROOT / "templates"
WORKSPACE_TEMPLATE_PATHS = sorted(
    [
        TEMPLATE_ROOT / "agents" / "resource_finder.txt",
        TEMPLATE_ROOT / "agents" / "session_instructions.txt",
    ]
    + list((TEMPLATE_ROOT / "domains").glob("*/resource_finder.txt"))
    + list((TEMPLATE_ROOT / "domains").glob("*/session_instructions.txt"))
)
DOMAINS = sorted(
    {path.parent.name for path in WORKSPACE_TEMPLATE_PATHS if path.parent.name != "agents"}
)


@pytest.mark.parametrize(
    "template_path",
    WORKSPACE_TEMPLATE_PATHS,
    ids=lambda path: str(path.relative_to(TEMPLATE_ROOT)),
)
def test_research_workspace_is_dependency_only(template_path: Path):
    """The generated workspace must not make uv build a nonexistent package."""
    template = template_path.read_text(encoding="utf-8")

    assert 'name = "research-workspace"' in template
    assert "uv add --project {{ research_env_dir }}" in template
    assert "uv venv {{ research_venv_dir }}" in template
    assert "cat > {{ research_project_path }}" in template
    assert "source .venv" not in template
    assert "cat > pyproject.toml" not in template
    assert "uv venv\n" not in template
    assert not re.search(r"^\s*uv pip install(?! --python)\b", template, re.MULTILINE)
    assert not re.search(r"^\s*(?:pip install|pip freeze)\b", template, re.MULTILINE)
    assert "-m pip" not in template
    assert "[build-system]" not in template
    assert "hatchling" not in template.lower()


@pytest.mark.parametrize(
    "skill_path",
    [
        TEMPLATE_ROOT / "skills" / "literature-review" / "SKILL.md",
        TEMPLATE_ROOT / "skills" / "paper-finder" / "SKILL.md",
    ],
    ids=lambda path: str(path.relative_to(TEMPLATE_ROOT)),
)
def test_bundled_research_skills_do_not_recommend_direct_pip(skill_path: Path):
    skill = skill_path.read_text(encoding="utf-8")

    assert "pip install" not in skill
    assert "uv add --project .neurico/research-env" in skill


@pytest.mark.parametrize("domain", ["general", *DOMAINS])
def test_generated_prompts_use_the_canonical_research_environment(domain: str):
    generator = PromptGenerator()
    idea = {"idea": {"domain": domain}}

    resource_prompt = generator.generate_resource_finder_prompt(idea)
    session_prompt = generator.generate_session_instructions(
        "research prompt",
        "/tmp/research-workspace",
        domain=domain,
        idea_spec=idea["idea"],
    )

    prompts = (resource_prompt, session_prompt)
    for prompt in prompts:
        assert ".neurico/research-env/.venv" in prompt
        assert "uv add --project .neurico/research-env" in prompt
        assert "{{ research_" not in prompt
    assert ".neurico/research-env/.venv/bin/python" in "\n".join(prompts)
