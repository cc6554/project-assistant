from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading

import pytest

import packaged_launcher
import agent_coach.workspace as workspace_module
from agent_coach.store import SessionStore
from agent_coach.web import build_service, make_server
from agent_coach.workspace import WorkspaceError, copy_or_open_workspace, write_selection_marker


def _request(server, token: str, method: str, path: str, body=None):
    port = int(server.server_address[1])
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {token}",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, body=payload, headers=headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    return response.status, json.loads(raw.decode("utf-8"))


@pytest.fixture
def running_service(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    marker = tmp_path / "portable" / ".agent-coach" / "workspace-selection.json"
    service = build_service(source, demo=True, workspace_marker=marker)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server, source, marker
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_workspace_switch_copies_sessions_keeps_source_and_rejects_stale_token(
    running_service,
    tmp_path: Path,
):
    service, server, source, marker = running_service
    created = service.create_session()
    session_id = str(created["id"])
    (source / "notes.txt").write_text("source stays intact", encoding="utf-8")
    settings_dir = source / ".agent-coach"
    (settings_dir / "model-settings.json").write_bytes(b"{\"model\":\"local\"}\n")
    (settings_dir / "model-settings.key").write_bytes(b"encrypted-key-placeholder")
    old_token = service.token
    target = tmp_path / "new-work"

    status, switched = _request(
        server,
        old_token,
        "POST",
        "/api/workspace",
        {"path": str(target)},
    )

    assert status == 200
    assert switched["ok"] is True
    assert switched["created"] is True
    assert switched["copied"] is True
    assert switched["requires_reload"] is True
    assert switched["token"] != old_token
    assert Path(switched["workspace"]) == target.resolve()
    assert (source / "notes.txt").read_text(encoding="utf-8") == "source stays intact"
    assert (source / ".agent-coach" / "sessions" / f"{session_id}.json").is_file()
    copied = SessionStore(target).get(session_id)
    assert copied.workspace == str(target.resolve())
    assert (target / ".agent-coach" / "model-settings.json").read_bytes() == b"{\"model\":\"local\"}\n"
    assert (target / ".agent-coach" / "model-settings.key").read_bytes() == b"encrypted-key-placeholder"
    assert marker.is_file()

    status, _ = _request(server, old_token, "GET", "/api/info")
    assert status == 401
    status, info = _request(server, switched["token"], "GET", "/api/info")
    assert status == 200
    assert Path(info["workspace"]) == target.resolve()


def test_workspace_switch_reuses_process_speech_service(running_service, tmp_path: Path):
    service, server, _source, _marker = running_service
    speech = service.speech
    target = tmp_path / "speech-workspace"

    status, switched = _request(
        server,
        service.token,
        "POST",
        "/api/workspace",
        {"path": str(target)},
    )

    assert status == 200
    assert switched["ok"] is True
    assert server.service.speech is speech
    assert service.speech is speech


def test_workspace_switch_rejects_busy_or_pending_session_without_touching_target(
    running_service,
    tmp_path: Path,
):
    service, server, source, _marker = running_service
    snapshot = service.create_session()
    session_id = str(snapshot["id"])
    target = tmp_path / "busy-target"
    with service._lock:
        service._busy[session_id] = "job-under-test"
    try:
        status, payload = _request(
            server,
            service.token,
            "POST",
            "/api/workspace",
            {"path": str(target)},
        )
    finally:
        with service._lock:
            service._busy.pop(session_id, None)
    assert status == 409
    assert "执行" in payload["error"]
    assert not target.exists()
    assert service.workspace == source.resolve()

    session = service.engine.store.get(session_id)
    from agent_coach.contracts import PendingApproval

    session.pending = PendingApproval(
        id="approval-switch",
        tool_call_id="call-switch",
        tool_name="write_file",
        arguments={},
        summary="等待审批",
        prepared={},
    )
    service.engine.store.save(session)
    status, payload = _request(
        server,
        service.token,
        "POST",
        "/api/workspace",
        {"path": str(target)},
    )
    assert status == 409
    assert "审批" in payload["error"]
    assert not target.exists()


def test_existing_workspace_is_opened_without_overwrite(running_service, tmp_path: Path):
    service, server, source, _marker = running_service
    source_session = service.create_session()
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    target_session = SessionStore(target).create()
    target_id = target_session.id

    status, payload = _request(
        server,
        service.token,
        "POST",
        "/api/workspace",
        {"path": str(target)},
    )
    assert status == 200
    assert payload["target_existing"] is True
    assert payload["copied"] is False
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert SessionStore(target).get(target_id).id == target_id
    assert not SessionStore(target).exists(source_session["id"])


def test_launcher_uses_remembered_workspace_after_restart(tmp_path: Path, monkeypatch):
    root = tmp_path / "portable"
    target = tmp_path / "selected"
    root.mkdir()
    target.mkdir()
    monkeypatch.setattr(packaged_launcher, "_application_root", lambda: root)
    write_selection_marker(
        packaged_launcher._workspace_selection_marker(root),
        target,
    )

    arguments = packaged_launcher._default_arguments(["--demo"])
    index = arguments.index("--workspace")
    assert Path(arguments[index + 1]) == target.resolve()

    explicit = packaged_launcher._default_arguments(["--workspace", str(root / "manual"), "--demo"])
    index = explicit.index("--workspace")
    assert Path(explicit[index + 1]) == (root / "manual")


def test_copy_excludes_only_runtime_entries_under_agent_coach(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    (source / "cache").mkdir()
    (source / "cache" / "project.txt").write_text("keep", encoding="utf-8")
    (source / "temp").mkdir()
    (source / "temp" / "project.tmp").write_text("keep", encoding="utf-8")
    (source / "lock").mkdir()
    (source / "lock" / "project.lock").write_text("keep", encoding="utf-8")
    (source / "headless-url.txt").write_text("Bearer user-data", encoding="utf-8")

    runtime = source / ".agent-coach"
    (runtime / "webview").mkdir(parents=True)
    (runtime / "webview" / "profile.bin").write_bytes(b"runtime")
    (runtime / "cache").mkdir()
    (runtime / "cache" / "profile.bin").write_bytes(b"runtime")
    (runtime / "headless-url.txt").write_text(
        "http://127.0.0.1:1/#token=Bearer-secret", encoding="utf-8"
    )

    result = copy_or_open_workspace(source, target)

    assert result["copied"] is True
    assert (target / "cache" / "project.txt").read_text(encoding="utf-8") == "keep"
    assert (target / "temp" / "project.tmp").read_text(encoding="utf-8") == "keep"
    assert (target / "lock" / "project.lock").read_text(encoding="utf-8") == "keep"
    assert (target / "headless-url.txt").read_text(encoding="utf-8") == "Bearer user-data"
    assert not (target / ".agent-coach" / "webview").exists()
    assert not (target / ".agent-coach" / "cache").exists()
    assert not (target / ".agent-coach" / "headless-url.txt").exists()


def test_failed_empty_target_copy_leaves_marker_and_rejects_retry(tmp_path: Path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "a.txt").write_text("first", encoding="utf-8")
    (source / "b.txt").write_text("second", encoding="utf-8")

    original_copy_file = workspace_module._copy_file
    calls = 0

    def fail_after_one(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkspaceError("injected copy failure")
        return original_copy_file(*args, **kwargs)

    monkeypatch.setattr(workspace_module, "_copy_file", fail_after_one)
    with pytest.raises(WorkspaceError, match="injected copy failure"):
        copy_or_open_workspace(source, target)

    marker = target / ".agent-coach" / ".workspace-copy.incomplete"
    assert marker.is_file()
    assert any(path.is_file() for path in target.iterdir() if path.name != ".agent-coach")

    monkeypatch.setattr(workspace_module, "_copy_file", original_copy_file)
    with pytest.raises(WorkspaceError, match="复制未完成"):
        copy_or_open_workspace(source, target)
    assert marker.is_file()


def test_fallback_demo_switches_to_saved_model_workspace_but_explicit_demo_stays_demo(
    tmp_path: Path,
):
    settings_payload = {
        "api_mode": "chat_completions",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "local-model",
    }

    fallback_source = tmp_path / "fallback-source"
    fallback_target = tmp_path / "configured-target"
    fallback_source.mkdir()
    fallback_target.mkdir()
    settings_dir = fallback_target / ".agent-coach"
    settings_dir.mkdir()
    (settings_dir / "model-settings.json").write_text(
        json.dumps(settings_payload), encoding="utf-8"
    )

    fallback = build_service(fallback_source, demo=True, demo_explicit=False)
    switched = fallback.switch_workspace({"path": str(fallback_target)})
    assert switched["target_existing"] is True
    assert fallback.demo is False
    assert fallback.mode == "configured"
    assert fallback.model_options()["current"]["model"] == "local-model"

    explicit_source = tmp_path / "explicit-source"
    explicit_target = tmp_path / "explicit-target"
    explicit_source.mkdir()
    explicit_target.mkdir()
    explicit_settings = explicit_target / ".agent-coach"
    explicit_settings.mkdir()
    (explicit_settings / "model-settings.json").write_text(
        json.dumps(settings_payload), encoding="utf-8"
    )

    explicit = build_service(explicit_source, demo=True)
    explicit.switch_workspace({"path": str(explicit_target)})
    assert explicit.demo is True
    assert explicit.mode == "demo"
