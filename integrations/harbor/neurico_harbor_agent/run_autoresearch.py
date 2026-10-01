"""Child-process entrypoint for one manager-driven NeuriCo AutoResearch run."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
import threading
from typing import Any
from uuid import uuid4

# NeuriCo's core modules use the historical top-level ``core`` namespace.
# The ACP child starts from Harbor's task workspace, so establish the same
# import roots as NeuriCo's own runner before importing any core module.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _PROJECT_ROOT / "src"
sys.path.insert(0, str(_SRC_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT))

from core.hitl_run_control import (  # noqa: E402
    HitlRunStopControl,
    HitlRunStopRequested,
    activate_hitl_run_stop_control,
)
from core.idea_manager import IdeaManager  # noqa: E402
from core.runner import ResearchRunner  # noqa: E402

from .runtime import HarborAutoResearchTask, build_harbor_idea


TIME_LIMIT_EXIT_CODE = 124


def execute_autoresearch(
    *,
    instruction: str,
    workspace: Path,
    ideas_dir: Path,
    iterations: int = 1,
    time_limit_seconds: float | None = None,
) -> dict[str, Any]:
    """Submit a Harbor task through NeuriCo and run headless Auto HITL AutoResearch."""
    if iterations < 1:
        raise ValueError("AutoResearch iterations must be at least 1")
    if time_limit_seconds is not None and time_limit_seconds <= 0:
        raise ValueError("AutoResearch time limit must be greater than 0")

    task = HarborAutoResearchTask(instruction=instruction, workspace=workspace)
    control = HitlRunStopControl(workspace, f"harbor-{uuid4().hex}")
    deadline_timer: threading.Timer | None = None
    deadline_reached = threading.Event()
    previous_sigint: Any = None

    def request_stop(requested_by: str) -> None:
        control.request(requested_by=requested_by)

    def request_deadline_stop() -> None:
        deadline_reached.set()
        request_stop("harbor_time_limit")

    try:
        if threading.current_thread() is threading.main_thread():
            previous_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda _signum, _frame: request_stop("signal:sigint"))
        if time_limit_seconds is not None:
            deadline_timer = threading.Timer(
                time_limit_seconds,
                request_deadline_stop,
            )
            deadline_timer.daemon = True
            deadline_timer.start()

        with activate_hitl_run_stop_control(control):
            idea_manager = IdeaManager(ideas_dir)
            idea_id = idea_manager.submit_idea(build_harbor_idea(task), validate=True)

            # The idea metadata points at Harbor's existing repository, so the local
            # runner selects it as the authoritative AutoResearch workspace. A direct
            # runner invocation hosts the manager headlessly; "cli" selects the managed
            # entry surface without starting a browser, while Auto mode forbids human
            # escalation and lets the manager resolve every review boundary itself.
            runner = ResearchRunner(use_github=False)
            try:
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
            except HitlRunStopRequested:
                from core.hitl_autoresearch import (
                    recover_interrupted_hitl_autoresearch_attempt,
                )

                recovery = recover_interrupted_hitl_autoresearch_attempt(workspace)
                timed_out = deadline_reached.is_set()
                reason = "configured time limit" if timed_out else "cancellation request"
                print(
                    f"NeuriCo stopped cleanly after the {reason}; "
                    "the last retained checkpoint remains in the Harbor workspace.",
                    flush=True,
                )
                return {
                    "success": False,
                    "stopped": True,
                    "time_limit_reached": timed_out,
                    "recovery": recovery,
                }
    finally:
        if deadline_timer is not None:
            deadline_timer.cancel()
            deadline_timer.join()
        control.clear()
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Harbor task with NeuriCo AutoResearch")
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ideas-dir", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--time-limit-seconds", type=float)
    args = parser.parse_args()

    instruction = args.instruction_file.read_text(encoding="utf-8")
    result = execute_autoresearch(
        instruction=instruction,
        workspace=args.workspace,
        ideas_dir=args.ideas_dir,
        iterations=args.iterations,
        time_limit_seconds=args.time_limit_seconds,
    )
    if result.get("time_limit_reached", False):
        raise SystemExit(TIME_LIMIT_EXIT_CODE)
    if not result.get("success", False):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
