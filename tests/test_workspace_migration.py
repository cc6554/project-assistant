from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolRegistry
from agent_coach.engine import AgentEngine
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.store import SessionStore, canonical_path
from agent_coach.tools import PathSecurityError, _write_execute
from scripts.migrate_workspace import MigrationError, main, migrate_workspace


def _snapshot(root: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rows[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def _make_source(root: Path) -> tuple[SessionStore, AgentSession, AgentSession, dict[str, str]]:
    store = SessionStore(root)
    normal = store.create()
    normal.skill_state = {
        "job-radar": {
            "profile": {"name": "Ada", "skills": ["Python"]},
            "history": [{"status": "in_progress", "note": "保留历史"}],
        }
    }
    store.save(normal)

    prepared_path = root / "notes" / "resume.md"
    prepared_path.parent.mkdir(parents=True)
    prepared_path.write_text("old content", encoding="utf-8")
    pending = store.create()
    pending.pending = PendingApproval(
        id="approval-1",
        tool_call_id="write-1",
        tool_name="write_file",
        arguments={"path": str(prepared_path), "content": "new content"},
        summary="write resume",
        prepared={
            "resolved_path": str(prepared_path),
            "path": str(prepared_path),
            "before_hash": hashlib.sha256(b"old content").hexdigest(),
        },
    )
    pending.queued_calls = [ToolCall(id="tail-1", name="list_dir", arguments={})]
    pending.execution = {
        "status": "running",
        "call_id": "write-1",
        "approval_id": "approval-1",
        "tool_name": "write_file",
    }
    store.save(pending)

    # These files represent the legacy GUI settings, the newer profile store,
    # and a DPAPI blob.  Migration copies their bytes without interpreting them.
    (root / "settings.json").write_bytes(b"legacy-gui-settings")
    (root / "profiles").mkdir()
    (root / "profiles" / "default.json").write_bytes(b"new-profile")
    (root / ".agent-coach" / "model-settings.json").write_bytes(b"model-settings")
    (root / ".agent-coach" / "model-settings.key").write_bytes(b"DPAPI-CIPHERTEXT")

    # Runtime content is intentionally excluded.  The session store also
    # creates adjacent .lock files; they must not be carried to the new store.
    for name in ("WebView", "cache", "lock", "tmp"):
        directory = root / name
        directory.mkdir()
        (directory / "runtime.bin").write_bytes(b"runtime")
    (root / "old.lock").write_bytes(b"runtime-lock")
    (root / "old.tmp").write_bytes(b"runtime-tmp")

    return store, normal, pending, _snapshot(root)


def test_migration_rebinds_sessions_preserves_state_and_blocks_old_writes(tmp_path: Path):
    source = tmp_path / "old-work"
    target = tmp_path / "new-work"
    _, normal, pending, before = _make_source(source)

    result = migrate_workspace(source, target)

    assert result["ok"] is True
    assert result["sessions_migrated"] == 2
    assert result["sessions_with_pending"] == 1
    assert result["sessions_marked_uncertain"] == 1
    assert result["excluded_entries"] >= 6
    assert _snapshot(source) == before
    assert (target / "settings.json").read_bytes() == b"legacy-gui-settings"
    assert (target / "profiles" / "default.json").read_bytes() == b"new-profile"
    assert (target / ".agent-coach" / "model-settings.key").read_bytes() == b"DPAPI-CIPHERTEXT"
    assert not (target / "WebView").exists()
    assert not (target / "cache").exists()
    assert not (target / "old.lock").exists()
    assert not (target / "old.tmp").exists()

    migrated_store = SessionStore(target)
    migrated_normal = migrated_store.get(normal.id)
    assert migrated_normal.workspace == str(canonical_path(target))
    assert migrated_normal.skill_state == normal.skill_state

    migrated_pending = migrated_store.get(pending.id)
    assert migrated_pending.workspace == str(canonical_path(target))
    assert migrated_pending.pending is not None
    assert migrated_pending.pending.prepared["resolved_path"] == str(target / "notes" / "resume.md")
    assert migrated_pending.pending.prepared["path"] == str(target / "notes" / "resume.md")
    # Arguments are not broadly rewritten.  The guard receives the target
    # workspace and therefore rejects an old absolute argument before writing.
    assert migrated_pending.pending.arguments["path"] == str(source / "notes" / "resume.md")
    assert migrated_pending.execution["status"] == "uncertain"
    assert migrated_pending.queued_calls[0].id == "tail-1"
    old_bytes = (source / "notes" / "resume.md").read_bytes()
    with pytest.raises(PathSecurityError):
        _write_execute(
            migrated_pending.pending,
            ToolContext(workspace=canonical_path(target), session=migrated_pending, router=None),
        )
    assert (source / "notes" / "resume.md").read_bytes() == old_bytes

    class Router:
        def __init__(self):
            self.calls = 0

        def complete(self, task, messages, *, tools=None, **kwargs):
            self.calls += 1
            return LLMResponse(text="迁移后的正常会话已继续")

    router = Router()
    pending_resume = AgentEngine(migrated_store, ToolRegistry(), router).continue_session(pending.id)
    assert pending_resume.status == "uncertain"
    assert router.calls == 0

    engine = AgentEngine(migrated_store, ToolRegistry(), router)
    continued = engine.run("继续", session_id=normal.id)
    assert continued.status == "completed"
    assert "迁移后的正常会话" in continued.message


def test_migration_rebinds_stale_workspace_and_drops_external_prepared_path(tmp_path: Path):
    source = tmp_path / "old-work"
    target = tmp_path / "new-work"
    store = SessionStore(source)
    session = store.create()
    external = tmp_path / "other-work" / "outside.txt"
    external.parent.mkdir()
    external.write_text("outside", encoding="utf-8")
    session.pending = PendingApproval(
        id="approval-external",
        tool_call_id="write-external",
        tool_name="write_file",
        arguments={"path": str(external), "content": "must not write"},
        summary="external path",
        prepared={"resolved_path": str(external), "path": str(external)},
    )
    store.save(session)

    # Reproduce a copied Desktop session whose binding still points elsewhere.
    session_file = source / ".agent-coach" / "sessions" / f"{session.id}.json"
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    payload["workspace"] = str(tmp_path / "stale-desktop-work")
    session_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    migrate_workspace(source, target)
    migrated = SessionStore(target).get(session.id)
    assert migrated.workspace == str(canonical_path(target))
    assert migrated.pending is not None
    assert "resolved_path" not in migrated.pending.prepared
    assert "path" not in migrated.pending.prepared
    assert migrated.pending.prepared["migration_sanitized"] is True
    assert migrated.execution is not None
    assert migrated.execution["status"] == "uncertain"
    assert str(external) not in json.dumps(migrated.pending.prepared, ensure_ascii=False)


def test_migration_rebinds_prepared_paths_from_raw_copied_declared_root(tmp_path: Path):
    """A second migration can recover a session copied before its root was rebound."""

    source = tmp_path / "DesktopFix"
    target = tmp_path / "DesktopFix-2"
    declared_old_root = tmp_path / "Desktop"
    store = SessionStore(source)
    session = store.create()
    (source / "notes").mkdir(parents=True)
    (source / "notes" / "resume.md").write_text("resume", encoding="utf-8")
    session.pending = PendingApproval(
        id="approval-raw-copy",
        tool_call_id="write-raw-copy",
        tool_name="write_file",
        arguments={"path": str(declared_old_root / "notes" / "resume.md"), "content": "next"},
        summary="raw-copied workspace path",
        prepared={
            "resolved_path": str(declared_old_root / "notes" / "resume.md"),
            "path": str(declared_old_root / "notes" / "resume.md"),
        },
    )
    session.execution = {"status": "running", "call_id": "write-raw-copy"}
    store.save(session)

    # Reproduce the earlier raw copy: files are physically under DesktopFix,
    # but the persisted session still declares the original Desktop root.
    session_file = source / ".agent-coach" / "sessions" / f"{session.id}.json"
    payload = json.loads(session_file.read_text(encoding="utf-8"))
    payload["workspace"] = str(declared_old_root)
    session_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    migrate_workspace(source, target)
    migrated = SessionStore(target).get(session.id)

    assert migrated.workspace == str(canonical_path(target))
    assert migrated.pending is not None
    assert migrated.pending.prepared["resolved_path"] == str(target / "notes" / "resume.md")
    assert migrated.pending.prepared["path"] == str(target / "notes" / "resume.md")
    assert migrated.execution is not None
    assert migrated.execution["status"] == "uncertain"


def test_migration_rejects_existing_or_nested_target_without_touching_it(tmp_path: Path):
    source = tmp_path / "old-work"
    _make_source(source)

    nested = source / "new-work"
    with pytest.raises(MigrationError):
        migrate_workspace(source, nested)
    assert not nested.exists()

    existing = tmp_path / "existing-work"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate_workspace(source, existing)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_migration_rejects_unc_paths_and_source_links(tmp_path: Path):
    source = tmp_path / "old-work"
    source.mkdir()
    (source / "note.txt").write_text("note", encoding="utf-8")

    with pytest.raises(MigrationError, match="UNC"):
        migrate_workspace(r"\\server\share\old-work", tmp_path / "target-unc")

    linked = source / "linked.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        linked.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(MigrationError):
        migrate_workspace(source, tmp_path / "new-work")
    assert not (tmp_path / "new-work").exists()

    # A link in a parent component must be rejected even when the final
    # spelling resolves to an otherwise ordinary directory.
    parent_link = tmp_path / "linked-parent"
    try:
        parent_link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(MigrationError):
        migrate_workspace(parent_link / "old-work", tmp_path / "new-work-parent")


def test_cli_only_reports_redacted_counts(tmp_path: Path, capsys):
    source = tmp_path / "old-work"
    target = tmp_path / "new-work"
    _make_source(source)

    assert main([str(source), str(target)]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert str(source) not in output
    assert str(target) not in output
    assert "approval-1" not in output
