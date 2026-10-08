"""Canonical paths for the NeuriCo-managed research environment."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


RESEARCH_ENV_RELATIVE_ROOT = PurePosixPath(".neurico/research-env")
RESEARCH_PROJECT_RELATIVE_PATH = RESEARCH_ENV_RELATIVE_ROOT / "pyproject.toml"
RESEARCH_LOCK_RELATIVE_PATH = RESEARCH_ENV_RELATIVE_ROOT / "uv.lock"
RESEARCH_REQUIREMENTS_RELATIVE_PATH = RESEARCH_ENV_RELATIVE_ROOT / "requirements.txt"
RESEARCH_VENV_RELATIVE_ROOT = RESEARCH_ENV_RELATIVE_ROOT / ".venv"
RESEARCH_PYTHON_RELATIVE_PATH = RESEARCH_VENV_RELATIVE_ROOT / "bin/python"

RESEARCH_ENV_METADATA_RELATIVE_PATHS = (
    RESEARCH_PROJECT_RELATIVE_PATH,
    RESEARCH_LOCK_RELATIVE_PATH,
    RESEARCH_REQUIREMENTS_RELATIVE_PATH,
)


def research_environment_dir(work_dir: Path) -> Path:
    """Return the workspace-local directory that owns dependency state."""
    return Path(work_dir) / Path(RESEARCH_ENV_RELATIVE_ROOT)


def research_venv_dir(work_dir: Path) -> Path:
    """Return the NeuriCo-managed virtual environment directory."""
    return Path(work_dir) / Path(RESEARCH_VENV_RELATIVE_ROOT)


def research_python_candidates(work_dir: Path) -> tuple[Path, Path]:
    """Return the POSIX and Windows interpreter paths for the research venv."""
    venv_dir = research_venv_dir(work_dir)
    return (
        Path(work_dir) / Path(RESEARCH_PYTHON_RELATIVE_PATH),
        venv_dir / "Scripts" / "python.exe",
    )


def root_venv_python_candidates(work_dir: Path) -> tuple[Path, Path]:
    """Return interpreter paths for the ambiguous legacy/task root venv."""
    venv_dir = Path(work_dir) / ".venv"
    return (
        venv_dir / "bin" / "python",
        venv_dir / "Scripts" / "python.exe",
    )


def reject_ambiguous_root_venv(work_dir: Path) -> None:
    """Reject a root venv only when no canonical research venv is available."""
    if any(path.is_file() for path in research_python_candidates(work_dir)):
        return
    if any(path.is_file() for path in root_venv_python_candidates(work_dir)):
        raise RuntimeError(
            "The NeuriCo research environment is missing, but a root .venv exists. "
            "NeuriCo will not use that ambiguous environment because it may belong "
            "to the task or verifier. For a confirmed legacy NeuriCo workspace, "
            "rebuild its dependencies under .neurico/research-env before continuing."
        )
