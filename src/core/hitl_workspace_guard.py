"""Runtime checks for HITL workspace-write boundaries."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from core.hitl_util import sha256_file

_EXCLUDED_PUBLIC_PREFIXES = {
    ".claude",
    ".codex",
    ".gemini",
    ".git",
    ".neurico",
    ".venv",
    "__pycache__",
    "logs",
    # Runtime-owned: PipelineState._save() rewrites it during guarded phases,
    # so snapshotting it turns the runtime's own write into a worker violation.
    "STATE.md",
}

_RUNTIME_PRIVATE_DIRECTORY_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".ipynb_checkpoints",
    "node_modules",
}


@dataclass(frozen=True)
class _FileState:
    kind: str
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int
    sha256: str | None = None
    link_target: str | None = None


class HitlWorkspaceWriteGuard:
    """Compare a bounded workspace view at one runtime-owned phase boundary.

    Broad public boundaries use filesystem mutation identity without reading
    research payloads. Explicit protected paths retain content hashing. HITL
    workers are expected to follow the runtime protocol; this mechanical gate
    catches accidental or unauthorized public writes before progression.
    """

    def __init__(
        self,
        work_dir: Path,
        baseline: dict[str, _FileState],
        tracked_paths: tuple[str, ...] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.baseline = dict(baseline)
        self.tracked_paths = tracked_paths

    @classmethod
    def capture_public(cls, work_dir: Path) -> "HitlWorkspaceWriteGuard":
        root = Path(work_dir).resolve()
        return cls(root, cls._snapshot(root, include_hidden=False))

    @classmethod
    def public_fingerprint(cls, work_dir: Path) -> str:
        """Return a stable digest of the public workspace at one boundary."""
        root = Path(work_dir).resolve()
        states = cls._snapshot(root, include_hidden=False)
        digest = hashlib.sha256()
        digest.update(b"neurico-hitl-public-fingerprint-v2\0")
        for path, state in sorted(states.items()):
            digest.update(path.encode("utf-8"))
            digest.update(repr(state).encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def capture_paths(cls, work_dir: Path, paths: Iterable[str]) -> "HitlWorkspaceWriteGuard":
        root = Path(work_dir).resolve()
        normalized = tuple(cls._normalize_relative(path) for path in paths)
        return cls(
            root,
            cls._snapshot_paths(root, normalized, hash_content=True),
            tracked_paths=normalized,
        )

    def allow_only(self, paths: Iterable[str]) -> dict[str, object]:
        allowed = {self._normalize_relative(path) for path in paths}
        return self._validate(allowed=allowed)

    def require_unchanged(self) -> dict[str, object]:
        return self._validate(allowed=set())

    def _validate(self, *, allowed: set[str]) -> dict[str, object]:
        current = self._current_snapshot()
        changed = sorted(
            path
            for path in set(self.baseline) | set(current)
            if self.baseline.get(path) != current.get(path)
            and not self._path_is_allowed(path, allowed)
        )
        if not changed:
            return {"valid": True, "issues": []}
        return {
            "valid": False,
            "issues": ["Runtime detected writes outside this HITL boundary: " + ", ".join(changed)],
        }

    @staticmethod
    def _path_is_allowed(path: str, allowed: set[str]) -> bool:
        """Allow an explicitly permitted path and its required parent directories."""
        return path in allowed or any(
            allowed_path.startswith(path + "/") for allowed_path in allowed
        )

    def _current_snapshot(self) -> dict[str, _FileState]:
        if self.tracked_paths is not None:
            return self._snapshot_paths(self.work_dir, self.tracked_paths, hash_content=True)
        return self._snapshot(self.work_dir, include_hidden=False)

    @staticmethod
    def _snapshot(root: Path, *, include_hidden: bool) -> dict[str, _FileState]:
        states: dict[str, _FileState] = {}
        for current, dir_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            dir_names.sort()
            file_names.sort()

            retained_dirs: list[str] = []
            for name in dir_names:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if not include_hidden and HitlWorkspaceWriteGuard._is_excluded(relative):
                    continue
                try:
                    stats = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(stats.st_mode):
                    states[relative] = HitlWorkspaceWriteGuard._file_state(
                        path,
                        stats,
                        hash_content=False,
                    )
                    continue
                if HitlWorkspaceWriteGuard._is_runtime_private_directory(path):
                    continue
                retained_dirs.append(name)
            dir_names[:] = retained_dirs

            for name in file_names:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if not include_hidden and HitlWorkspaceWriteGuard._is_excluded(relative):
                    continue
                try:
                    stats = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(stats.st_mode) or stat.S_ISREG(stats.st_mode):
                    states[relative] = HitlWorkspaceWriteGuard._file_state(
                        path,
                        stats,
                        hash_content=False,
                    )
        return states

    @staticmethod
    def _snapshot_paths(
        root: Path,
        paths: Iterable[str],
        *,
        hash_content: bool,
    ) -> dict[str, _FileState]:
        states: dict[str, _FileState] = {}
        for raw_path in paths:
            relative = HitlWorkspaceWriteGuard._normalize_relative(raw_path)
            path = root / relative
            try:
                stats = path.lstat()
            except FileNotFoundError:
                continue
            states[relative] = HitlWorkspaceWriteGuard._file_state(
                path,
                stats,
                hash_content=hash_content,
            )
        return states

    @staticmethod
    def _file_state(
        path: Path,
        stats: os.stat_result,
        *,
        hash_content: bool,
    ) -> _FileState:
        if stat.S_ISLNK(stats.st_mode):
            kind = "symlink"
            digest = None
            link_target = os.readlink(path)
        elif stat.S_ISREG(stats.st_mode):
            kind = "file"
            digest = sha256_file(path) if hash_content else None
            link_target = None
        else:
            kind = "other"
            digest = None
            link_target = None
        return _FileState(
            kind=kind,
            mode=stat.S_IMODE(stats.st_mode),
            size=stats.st_size,
            modified_ns=stats.st_mtime_ns,
            changed_ns=stats.st_ctime_ns,
            device=stats.st_dev,
            inode=stats.st_ino,
            sha256=digest,
            link_target=link_target,
        )

    @staticmethod
    def _is_runtime_private_directory(path: Path) -> bool:
        if path.name in _RUNTIME_PRIVATE_DIRECTORY_NAMES:
            return True
        try:
            return (path / "pyvenv.cfg").is_file()
        except OSError:
            return False

    @staticmethod
    def _is_excluded(relative: str) -> bool:
        parts = Path(relative).parts
        return bool(parts and (parts[0] in _EXCLUDED_PUBLIC_PREFIXES or ".git" in parts))

    @staticmethod
    def _normalize_relative(path: str) -> str:
        candidate = Path(str(path).strip())
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ValueError("HITL workspace guard paths must be workspace-relative.")
        return candidate.as_posix()
