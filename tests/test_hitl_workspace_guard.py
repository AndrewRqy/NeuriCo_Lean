"""Unit tests for the HITL workspace write-boundary guard.

Pins the fix for the endless resource_finder recovery loop: the runtime's own
pipeline-state document (STATE.md) is rewritten by PipelineState._save() during
guarded phases, so it must be outside the public write boundary. Before the
fix, the guard flagged that runtime write as a worker violation at phase
finish, and each rejection-triggered recovery rewrote STATE.md again, so the
phase could never pass.

Run: python -m pytest tests/test_hitl_workspace_guard.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.hitl_workspace_guard import HitlWorkspaceWriteGuard  # noqa: E402
import core.hitl_workspace_guard as workspace_guard  # noqa: E402


def _workspace(tmp_path):
    work_dir = tmp_path / "ws"
    (work_dir / "plans").mkdir(parents=True)
    (work_dir / "plans" / "resource_finder_plan.md").write_text("plan v1\n")
    (work_dir / "STATE.md").write_text("# state v1\n")
    (work_dir / "README.md").write_text("readme\n")
    return work_dir


def test_runtime_state_document_is_outside_the_boundary(tmp_path):
    work_dir = _workspace(tmp_path)
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    # The runtime rewrites STATE.md mid-phase (new content AND new mtime).
    (work_dir / "STATE.md").write_text("# state v2 (recovery recorded)\n")
    (work_dir / "plans" / "resource_finder_plan.md").write_text("plan v2\n")

    result = guard.allow_only(["plans/resource_finder_plan.md"])
    assert result["valid"], result["issues"]


def test_worker_writes_outside_boundary_still_caught(tmp_path):
    work_dir = _workspace(tmp_path)
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    (work_dir / "README.md").write_text("tampered\n")

    result = guard.allow_only(["plans/resource_finder_plan.md"])
    assert not result["valid"]
    assert "README.md" in result["issues"][0]


def test_allowed_plan_write_passes(tmp_path):
    work_dir = _workspace(tmp_path)
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    (work_dir / "plans" / "resource_finder_plan.md").write_text("plan v2\n")

    result = guard.allow_only(["plans/resource_finder_plan.md"])
    assert result["valid"], result["issues"]


def test_public_fingerprint_does_not_read_public_file_contents(tmp_path, monkeypatch):
    work_dir = _workspace(tmp_path)
    large_input = work_dir / "datasets" / "input.bin"
    large_input.parent.mkdir()
    with large_input.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024)

    def reject_content_hash(_path):
        raise AssertionError("broad public fingerprints must not read file contents")

    monkeypatch.setattr(workspace_guard, "sha256_file", reject_content_hash)

    fingerprint = HitlWorkspaceWriteGuard.public_fingerprint(work_dir)

    assert len(fingerprint) == 64


def test_public_guard_detects_same_size_write_with_restored_mtime(tmp_path):
    work_dir = _workspace(tmp_path)
    target = work_dir / "README.md"
    original = target.stat()
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    target.write_text("READM3\n")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

    result = guard.require_unchanged()
    assert not result["valid"]
    assert "README.md" in result["issues"][0]


def test_public_guard_prunes_nested_virtual_environments(tmp_path):
    work_dir = _workspace(tmp_path)
    environment = work_dir / "results" / "experiment" / "state_env"
    package = environment / "lib" / "python" / "site-packages" / "package.py"
    package.parent.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("home = /python\n")
    package.write_text("VERSION = 1\n")
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    package.write_text("VERSION = 2\n")

    result = guard.require_unchanged()
    assert result["valid"], result["issues"]


def test_explicit_path_guard_retains_content_hashing(tmp_path):
    work_dir = _workspace(tmp_path)
    target = work_dir / "scoring" / "results.json"
    target.parent.mkdir()
    target.write_text('{"value": 1}\n')
    original = target.stat()
    guard = HitlWorkspaceWriteGuard.capture_paths(work_dir, ["scoring/results.json"])

    target.write_text('{"value": 2}\n')
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

    result = guard.require_unchanged()
    assert not result["valid"]
    assert "scoring/results.json" in result["issues"][0]


def test_public_guard_detects_symlink_target_change(tmp_path):
    work_dir = _workspace(tmp_path)
    first = work_dir / "first.txt"
    second = work_dir / "second.txt"
    first.write_text("first\n")
    second.write_text("second\n")
    link = work_dir / "selected.txt"
    link.symlink_to(first.name)
    guard = HitlWorkspaceWriteGuard.capture_public(work_dir)

    link.unlink()
    link.symlink_to(second.name)

    result = guard.require_unchanged()
    assert not result["valid"]
    assert "selected.txt" in result["issues"][0]
