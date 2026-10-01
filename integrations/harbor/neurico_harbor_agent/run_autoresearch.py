"""Child-process entrypoint for one manager-driven NeuriCo AutoResearch run."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.core.idea_manager import IdeaManager
from src.core.runner import ResearchRunner

from .runtime import HarborAutoResearchTask, build_harbor_idea


def execute_autoresearch(
    *,
    instruction: str,
    workspace: Path,
    ideas_dir: Path,
    iterations: int = 1,
) -> dict[str, Any]:
    """Submit a Harbor task through NeuriCo and run headless Auto HITL AutoResearch."""
    if iterations < 1:
        raise ValueError("AutoResearch iterations must be at least 1")

    task = HarborAutoResearchTask(instruction=instruction, workspace=workspace)
    idea_manager = IdeaManager(ideas_dir)
    idea_id = idea_manager.submit_idea(build_harbor_idea(task), validate=True)

    # The idea metadata points at Harbor's existing repository, so the local
    # runner selects it as the authoritative AutoResearch workspace. A direct
    # runner invocation hosts the manager headlessly; "cli" selects the managed
    # entry surface without starting a browser, while Auto mode forbids human
    # escalation and lets the manager resolve every review boundary itself.
    runner = ResearchRunner(use_github=False)
    return runner.run_research(
        idea_id=idea_id,
        provider="codex",
        full_permissions=True,
        multi_agent=True,
        use_scribe=False,
        write_paper=False,
        scoring_enabled=True,
        benchmark_mode=True,
        hitl_autoresearch="cli",
        hitl_manager_no_browser=True,
        hitl_mode="auto",
        autoresearch_iterations=iterations,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Harbor task with NeuriCo AutoResearch")
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ideas-dir", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=1)
    args = parser.parse_args()

    instruction = args.instruction_file.read_text(encoding="utf-8")
    result = execute_autoresearch(
        instruction=instruction,
        workspace=args.workspace,
        ideas_dir=args.ideas_dir,
        iterations=args.iterations,
    )
    if not result.get("success", False):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
