"""Private snapshots for evaluator-declared artifacts excluded from Git.

HITL AutoResearch checkpoints own tracked workspace state through Git.  This
store complements those checkpoints with only the ignored files below the
runtime-frozen artifact contract, so a frontier node can restore model weights
without capturing unrelated datasets, environments, or caches.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from core.hitl_git import run_git
from core.hitl_paths import hitl_artifact_snapshots_dir
from core.hitl_util import atomic_write_json, fsync_directory


_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MANIFEST_NAME = "manifest.json"
_FILES_DIR = "files"


class HitlArtifactSnapshotError(RuntimeError):
    """Raised when a private artifact snapshot cannot be captured or restored."""


def _safe_snapshot_id(value: str) -> str:
    snapshot_id = str(value).strip()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise HitlArtifactSnapshotError(f"Invalid HITL artifact snapshot id: {value!r}")
    return snapshot_id


def _safe_relative_path(value: str, *, label: str) -> Path:
    raw = str(value)
    posix = PurePosixPath(raw)
    if posix.is_absolute() or not posix.parts or any(
        part in {"", ".", ".."} for part in posix.parts
    ):
        raise HitlArtifactSnapshotError(f"Unsafe {label}: {value!r}")
    return Path(*posix.parts)


def _artifact_roots(work_dir: Path) -> list[str]:
    from core.hitl import load_hitl_required_artifact_contract

    roots = [
        artifact.path.rstrip("/")
        for artifact in load_hitl_required_artifact_contract(work_dir)
    ]
    for root in roots:
        if root in {".git", ".neurico"} or root.startswith((".git/", ".neurico/")):
            raise HitlArtifactSnapshotError(
                f"Evaluator-declared artifacts cannot use runtime-private path: {root}"
            )
    return roots


def _snapshot_dir(work_dir: Path, snapshot_id: str) -> Path:
    return hitl_artifact_snapshots_dir(work_dir) / _safe_snapshot_id(snapshot_id)


def _path_without_symlink_ancestors(root: Path, relative: Path, *, label: str) -> Path:
    current = Path(root).resolve()
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise HitlArtifactSnapshotError(
                f"{label} crosses a symlinked directory: {relative.as_posix()}"
            )
    target = Path(root).resolve() / relative
    try:
        target.parent.resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise HitlArtifactSnapshotError(
            f"{label} escapes its workspace: {relative.as_posix()}"
        ) from exc
    return target


def _copy_entry(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        raise HitlArtifactSnapshotError(
            f"Evaluator-declared ignored artifact cannot be a symlink: {source}"
        )
    mode = source.lstat().st_mode
    if stat.S_ISREG(mode):
        shutil.copy2(source, destination, follow_symlinks=False)
        return
    raise HitlArtifactSnapshotError(
        f"Evaluator-declared ignored artifact is not a regular file: {source}"
    )


def _reject_gitlink_artifact_roots(
    work_dir: Path,
    *,
    source_checkpoint_sha: str,
    artifact_roots: list[str],
) -> None:
    """Reject contract roots whose state is owned by a nested repository."""
    listed = run_git(
        work_dir,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        source_checkpoint_sha,
        text=False,
        check=False,
    )
    if listed.returncode != 0:
        detail = bytes(listed.stderr or listed.stdout or b"unknown Git error").decode(
            "utf-8", errors="replace"
        ).strip()
        raise HitlArtifactSnapshotError(
            f"Runtime could not inspect the artifact checkpoint: {detail}"
        )
    gitlinks: list[PurePosixPath] = []
    for raw_record in bytes(listed.stdout or b"").split(b"\0"):
        if not raw_record:
            continue
        metadata, separator, raw_path = raw_record.partition(b"\t")
        fields = metadata.split()
        if separator and len(fields) == 3 and fields[0] == b"160000":
            gitlinks.append(PurePosixPath(os.fsdecode(raw_path)))
    for raw_root in artifact_roots:
        root = PurePosixPath(raw_root)
        for gitlink in gitlinks:
            if root == gitlink or root.is_relative_to(gitlink) or gitlink.is_relative_to(root):
                raise HitlArtifactSnapshotError(
                    "Evaluator-declared artifacts cannot cross checkpointed nested "
                    f"repository {gitlink.as_posix()}: {raw_root}"
                )


def _read_manifest(work_dir: Path, snapshot_id: str) -> dict[str, Any]:
    normalized = _safe_snapshot_id(snapshot_id)
    path = _snapshot_dir(work_dir, normalized) / _MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HitlArtifactSnapshotError(
            f"HITL artifact snapshot {normalized} is missing or unreadable."
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("artifact_snapshot_id") != normalized
        or not isinstance(payload.get("source_checkpoint_sha"), str)
        or not isinstance(payload.get("artifact_roots"), list)
        or not isinstance(payload.get("captured_paths"), list)
    ):
        raise HitlArtifactSnapshotError(
            f"HITL artifact snapshot {normalized} has an invalid manifest."
        )
    artifact_roots = [
        _safe_relative_path(str(root), label="artifact root")
        for root in payload["artifact_roots"]
    ]
    for relative in payload["captured_paths"]:
        captured = _safe_relative_path(str(relative), label="captured artifact path")
        if not any(captured == root or root in captured.parents for root in artifact_roots):
            raise HitlArtifactSnapshotError(
                f"HITL artifact snapshot {normalized} contains an undeclared path."
            )
    return payload


def validate_hitl_artifact_snapshot(
    work_dir: Path,
    snapshot_id: str,
    *,
    source_checkpoint_sha: str | None = None,
) -> dict[str, Any]:
    """Validate one stored snapshot and its optional source checkpoint."""
    workspace = Path(work_dir).resolve()
    manifest = _read_manifest(workspace, snapshot_id)
    expected = str(source_checkpoint_sha or "").strip()
    if expected and manifest["source_checkpoint_sha"] != expected:
        raise HitlArtifactSnapshotError(
            f"HITL artifact snapshot {snapshot_id} belongs to a different checkpoint."
        )
    files_root = _snapshot_dir(workspace, snapshot_id) / _FILES_DIR
    for relative_text in manifest["captured_paths"]:
        relative = _safe_relative_path(
            relative_text,
            label="captured artifact path",
        )
        source = _path_without_symlink_ancestors(
            files_root,
            relative,
            label="Stored evaluator artifact",
        )
        try:
            mode = source.lstat().st_mode
        except OSError as exc:
            raise HitlArtifactSnapshotError(
                f"HITL artifact snapshot {snapshot_id} is missing {relative_text}."
            ) from exc
        if not stat.S_ISREG(mode):
            raise HitlArtifactSnapshotError(
                f"HITL artifact snapshot {snapshot_id} has an invalid stored file: "
                f"{relative_text}."
            )
    return manifest


def capture_hitl_artifact_snapshot(
    work_dir: Path,
    *,
    snapshot_id: str,
    source_checkpoint_sha: str,
) -> str:
    """Atomically capture ignored files beneath the frozen artifact contract."""
    workspace = Path(work_dir).resolve()
    normalized = _safe_snapshot_id(snapshot_id)
    source_sha = str(source_checkpoint_sha).strip()
    if not source_sha:
        raise HitlArtifactSnapshotError("Artifact snapshot requires a source checkpoint SHA.")
    roots = _artifact_roots(workspace)
    if not roots:
        raise HitlArtifactSnapshotError("Artifact snapshot requires a non-empty frozen contract.")
    _reject_gitlink_artifact_roots(
        workspace,
        source_checkpoint_sha=source_sha,
        artifact_roots=roots,
    )

    store = hitl_artifact_snapshots_dir(workspace)
    store.mkdir(parents=True, exist_ok=True)
    final = store / normalized
    if final.exists():
        manifest = validate_hitl_artifact_snapshot(workspace, normalized)
        if (
            manifest["source_checkpoint_sha"] != source_sha
            or manifest["artifact_roots"] != roots
        ):
            raise HitlArtifactSnapshotError(
                f"HITL artifact snapshot id {normalized} already names a different boundary."
            )
        return normalized

    listed = run_git(
        workspace,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        *roots,
        text=False,
        check=False,
    )
    if listed.returncode != 0:
        detail = bytes(listed.stderr or listed.stdout or b"unknown Git error").decode(
            "utf-8", errors="replace"
        ).strip()
        raise HitlArtifactSnapshotError(
            f"Runtime could not enumerate ignored evaluator artifacts: {detail}"
        )

    captured: list[str] = []
    seen: set[str] = set()
    for raw in bytes(listed.stdout or b"").split(b"\0"):
        if not raw:
            continue
        relative_text = os.fsdecode(raw)
        relative = _safe_relative_path(relative_text, label="ignored artifact path")
        normalized_relative = relative.as_posix()
        if normalized_relative not in seen:
            seen.add(normalized_relative)
            captured.append(normalized_relative)
    captured.sort()

    staging = store / f".{normalized}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        files_root = staging / _FILES_DIR
        files_root.mkdir(parents=True)
        for relative_text in captured:
            relative = _safe_relative_path(relative_text, label="ignored artifact path")
            source = _path_without_symlink_ancestors(
                workspace,
                relative,
                label="Ignored evaluator artifact",
            )
            if not source.exists() and not source.is_symlink():
                raise HitlArtifactSnapshotError(
                    f"Ignored evaluator artifact disappeared during capture: {relative_text}"
                )
            _copy_entry(source, files_root / relative)
        atomic_write_json(
            staging / _MANIFEST_NAME,
            {
                "version": 1,
                "artifact_snapshot_id": normalized,
                "source_checkpoint_sha": source_sha,
                "artifact_roots": roots,
                "captured_paths": captured,
            },
            fsync_parent=False,
        )
        os.replace(staging, final)
        fsync_directory(store)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return normalized


def materialize_hitl_artifact_snapshot(
    work_dir: Path,
    *,
    snapshot_id: str,
    destination: Path,
) -> None:
    """Copy one private snapshot into a workspace without following symlinks."""
    workspace = Path(work_dir).resolve()
    manifest = _read_manifest(workspace, snapshot_id)
    files_root = _snapshot_dir(workspace, snapshot_id) / _FILES_DIR
    target_root = Path(destination).resolve()
    for relative_text in manifest["captured_paths"]:
        relative = _safe_relative_path(relative_text, label="captured artifact path")
        source = _path_without_symlink_ancestors(
            files_root,
            relative,
            label="Stored evaluator artifact",
        )
        if not source.exists() and not source.is_symlink():
            raise HitlArtifactSnapshotError(
                f"HITL artifact snapshot {snapshot_id} is missing {relative_text}."
            )
        target = _path_without_symlink_ancestors(
            target_root,
            relative,
            label="Restored evaluator artifact",
        )
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        _copy_entry(source, target)


def discard_hitl_artifact_snapshot(work_dir: Path, snapshot_id: str) -> None:
    """Delete one runtime-owned transient snapshot."""
    target = _snapshot_dir(Path(work_dir).resolve(), snapshot_id)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def restore_hitl_checkpoint_with_artifacts(
    work_dir: Path,
    *,
    checkpoint_sha: str,
    artifact_snapshot_id: str | None,
) -> str:
    """Restore one composite HITL checkpoint and verify its Git component."""
    from core.autoresearch import CheckpointManager

    workspace = Path(work_dir).resolve()
    checkpoints = CheckpointManager(workspace)
    sha = str(checkpoint_sha).strip()
    if not checkpoints.checkpoint_exists(sha):
        raise HitlArtifactSnapshotError("The HITL node is not a workspace checkpoint.")
    snapshot_id = str(artifact_snapshot_id or "").strip()
    manifest = (
        validate_hitl_artifact_snapshot(workspace, snapshot_id)
        if snapshot_id
        else None
    )
    checkpoints.restore_checkpoint(sha, clean_untracked_public=True)
    if manifest is not None:
        cleaned = run_git(
            workspace,
            "clean",
            "-fdX",
            "--",
            *[str(root) for root in manifest["artifact_roots"]],
            check=False,
        )
        if cleaned.returncode != 0:
            detail = str(cleaned.stderr or cleaned.stdout or "unknown Git error").strip()
            raise HitlArtifactSnapshotError(
                f"Runtime could not clear stale ignored evaluator artifacts: {detail}"
            )
        materialize_hitl_artifact_snapshot(
            workspace,
            snapshot_id=snapshot_id,
            destination=workspace,
        )
    if checkpoints.current_sha() != sha:
        raise HitlArtifactSnapshotError("Runtime could not restore the HITL node checkpoint.")
    return sha


def restore_hitl_frontier_node(work_dir: Path, node_sha: str) -> str:
    """Restore a retained frontier node, including its optional legacy snapshot."""
    from core.hitl_frontier import HitlFrontierStore

    frontier = HitlFrontierStore(work_dir)
    return restore_hitl_checkpoint_with_artifacts(
        work_dir,
        checkpoint_sha=node_sha,
        artifact_snapshot_id=frontier.artifact_snapshot_id(node_sha),
    )
