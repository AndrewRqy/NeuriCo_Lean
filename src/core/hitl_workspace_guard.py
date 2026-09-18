"""Runtime checks for HITL workspace-write boundaries."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.hitl_util import sha256_file

_BUILTIN_RUNTIME_PRIVATE_ROOTS = {
    ".claude",
    ".codex",
    ".gemini",
    ".git",
    ".neurico",
    ".venv",
    "__pycache__",
    "logs",
}

_EXCLUDED_PUBLIC_FILES = {
    # Runtime-owned: PipelineState._save() rewrites it during guarded phases,
    # so snapshotting it turns the runtime's own write into a worker violation.
    "STATE.md",
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


@dataclass(frozen=True)
class WorkspaceGuardScope:
    """Runtime-owned roots omitted from recursive public traversal.

    These roots are explicit protocol state.  Filesystem names and marker
    files are never consulted when deciding whether a directory is private or
    immutable, so a worker cannot hide a new subtree after capture.
    """

    runtime_private_roots: tuple[str, ...] = ()
    immutable_resource_roots: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "WorkspaceGuardScope":
        if value is None:
            return cls()
        if isinstance(value, cls):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise ValueError("HITL workspace guard scope must be a mapping.")
        return cls(
            runtime_private_roots=cls._normalize_roots(
                value.get("runtime_private_roots", ()),
                field="runtime_private_roots",
            ),
            immutable_resource_roots=cls._normalize_roots(
                value.get("immutable_resource_roots", ()),
                field="immutable_resource_roots",
            ),
        )._validated()

    @staticmethod
    def _normalize_roots(values: Any, *, field: str) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"HITL workspace guard {field} must be a list.")
        normalized = {HitlWorkspaceWriteGuard._normalize_relative(str(value)) for value in values}
        return tuple(sorted(normalized))

    def _validated(self) -> "WorkspaceGuardScope":
        private = set(self.runtime_private_roots)
        immutable = set(self.immutable_resource_roots)
        overlap = sorted(private & immutable)
        if overlap:
            raise ValueError(
                "HITL workspace guard roots cannot be both runtime-private and immutable: "
                + ", ".join(overlap)
            )
        roots = sorted(private | immutable)
        for index, root in enumerate(roots):
            for other in roots[index + 1 :]:
                if other.startswith(root + "/"):
                    raise ValueError(
                        "HITL workspace guard roots cannot overlap: " f"{root}, {other}"
                    )
        return self

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "runtime_private_roots": list(self.runtime_private_roots),
            "immutable_resource_roots": list(self.immutable_resource_roots),
        }

    @property
    def excluded_roots(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.runtime_private_roots) | set(self.immutable_resource_roots)))


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
        scope: WorkspaceGuardScope | Mapping[str, Any] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir).resolve()
        self.baseline = dict(baseline)
        self.tracked_paths = tracked_paths
        self.scope = WorkspaceGuardScope.from_value(scope)

    @classmethod
    def capture_public(
        cls,
        work_dir: Path,
        *,
        scope: WorkspaceGuardScope | Mapping[str, Any] | None = None,
    ) -> "HitlWorkspaceWriteGuard":
        root = Path(work_dir).resolve()
        normalized_scope = WorkspaceGuardScope.from_value(scope)
        return cls(
            root,
            cls._snapshot(root, include_hidden=False, scope=normalized_scope),
            scope=normalized_scope,
        )

    @classmethod
    def public_fingerprint(
        cls,
        work_dir: Path,
        *,
        scope: WorkspaceGuardScope | Mapping[str, Any] | None = None,
    ) -> str:
        """Return a stable digest of the public workspace at one boundary."""
        root = Path(work_dir).resolve()
        normalized_scope = WorkspaceGuardScope.from_value(scope)
        states = cls._snapshot(root, include_hidden=False, scope=normalized_scope)
        digest = hashlib.sha256()
        digest.update(b"neurico-hitl-public-fingerprint-v3\0")
        digest.update(repr(normalized_scope.to_dict()).encode("utf-8"))
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
        return self._snapshot(
            self.work_dir,
            include_hidden=False,
            scope=self.scope,
        )

    @staticmethod
    def _snapshot(
        root: Path,
        *,
        include_hidden: bool,
        scope: WorkspaceGuardScope,
    ) -> dict[str, _FileState]:
        states: dict[str, _FileState] = {}
        excluded_roots = set(scope.excluded_roots) | _BUILTIN_RUNTIME_PRIVATE_ROOTS
        for relative in sorted(excluded_roots):
            path = root / relative
            try:
                stats = path.lstat()
            except FileNotFoundError:
                continue
            states[relative] = HitlWorkspaceWriteGuard._root_state(path, stats)

        pending_directories = [root]
        while pending_directories:
            current_path = pending_directories.pop()
            try:
                with os.scandir(current_path) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError:
                continue

            retained_dirs: list[Path] = []
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if not include_hidden and HitlWorkspaceWriteGuard._is_excluded(relative):
                    continue
                if relative in excluded_roots:
                    continue
                try:
                    stats = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(stats.st_mode):
                    if entry.name == ".git":
                        states[relative] = HitlWorkspaceWriteGuard._root_state(path, stats)
                        continue
                    states[relative] = HitlWorkspaceWriteGuard._file_state(
                        path,
                        stats,
                        hash_content=False,
                    )
                    retained_dirs.append(path)
                elif stat.S_ISLNK(stats.st_mode) or stat.S_ISREG(stats.st_mode):
                    states[relative] = HitlWorkspaceWriteGuard._file_state(
                        path,
                        stats,
                        hash_content=False,
                    )
            pending_directories.extend(reversed(retained_dirs))
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
        elif stat.S_ISDIR(stats.st_mode):
            kind = "directory"
            digest = None
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
    def _root_state(path: Path, stats: os.stat_result) -> _FileState:
        """Record root identity without observing mutable contents beneath it."""
        state = HitlWorkspaceWriteGuard._file_state(
            path,
            stats,
            hash_content=False,
        )
        return _FileState(
            kind=state.kind,
            mode=state.mode,
            size=0,
            modified_ns=0,
            changed_ns=0,
            device=state.device,
            inode=state.inode,
            link_target=state.link_target,
        )

    @staticmethod
    def _is_excluded(relative: str) -> bool:
        parts = Path(relative).parts
        return bool(len(parts) == 1 and parts[0] in _EXCLUDED_PUBLIC_FILES)

    @staticmethod
    def _normalize_relative(path: str) -> str:
        candidate = Path(str(path).strip())
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ValueError("HITL workspace guard paths must be workspace-relative.")
        return candidate.as_posix()
