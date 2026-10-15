from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

import agent_coach.engine as engine_module
from agent_coach.contracts import PendingApproval
from agent_coach.web import build_service, make_server
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.legacy.domain.schemas import JDCard, SkillItem, UserSkillProfile


@pytest.fixture
def live_server(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(service: Any, server: Any, method: str, path: str, body: Any = None, **headers: str):
    port = int(server.server_address[1])
    request_headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {service.token}",
    }
    request_headers.update(headers)
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, body=payload, headers=request_headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = raw
    return response.status, data


def wait_job(service: Any, server: Any, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        status, payload = request(service, server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def create_session(service: Any, server: Any) -> str:
    status, payload = request(service, server, "POST", "/api/sessions", {})
    assert status == 201
    return str(payload["id"])


def test_api_guards_and_bounded_paths(live_server):
    service, server = live_server
    port = int(server.server_address[1])
    status, _ = request(
        service,
        server,
        "GET",
        f"http://127.0.0.1:{port}/api/info",
        Authorization="",
    )
    assert status == 400
    status, info = request(service, server, "GET", "/api%2Finfo")
    assert status == 200 and info["app"] == "agent-coach"
    status, _ = request(service, server, "GET", "/api/info", Authorization="Bearer wrong")
    assert status == 401
    status, _ = request(
        service,
        server,
        "GET",
        "/api/info",
        Host=f"127.0.0.1:{port + 1}",
    )
    assert status == 403
    status, _ = request(service, server, "GET", "/api/info", Origin=f"http://evil:{port}")
    assert status == 403
    session_id = create_session(service, server)
    status, _ = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "x" * (1024 * 1024)},
    )
    assert status == 413
    status, _ = request(service, server, "GET", "/api/file?path=../outside.txt")
    assert status == 400


def test_demo_message_approval_and_restart_restore(live_server, tmp_path: Path):
    service, server = live_server
    session_id = create_session(service, server)
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "inspect then write"},
    )
    assert status == 202
    result = wait_job(service, server, queued["job_id"])
    pending = result["result"]["pending"]
    assert pending and "prepared" not in json.dumps(pending)

    server.shutdown()
    server.server_close()
    restored = build_service(tmp_path, demo=True)
    restored_server = make_server(restored, port=0)
    thread = threading.Thread(target=restored_server.serve_forever, daemon=True)
    thread.start()
    try:
        status, detail = request(restored, restored_server, "GET", f"/api/sessions/{session_id}")
        assert status == 200
        assert detail["pending"]["id"] == pending["id"]
        status, queued = request(
            restored,
            restored_server,
            "POST",
            f"/api/sessions/{session_id}/approve",
            {"id": pending["id"]},
        )
        assert status == 202
        completed = wait_job(restored, restored_server, queued["job_id"])
        assert completed["result"]["status"] == "completed"
        assert (tmp_path / "demo-output.txt").read_text(encoding="utf-8")
    finally:
        restored_server.shutdown()
        restored_server.server_close()
        thread.join(timeout=2)


def test_files_upload_and_skill_toggle(live_server, tmp_path: Path):
    service, server = live_server
    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    status, listing = request(service, server, "GET", "/api/files?path=.")
    assert status == 200
    assert any(row["path"] == "hello.txt" and row["type"] == "file" for row in listing["entries"])
    status, content = request(service, server, "GET", "/api/file?path=hello.txt")
    assert status == 200 and content["content"] == "hello" and not content["truncated"]
    encoded = base64.b64encode(b"uploaded").decode("ascii")
    status, uploaded = request(
        service,
        server,
        "POST",
        "/api/uploads",
        {"name": "attachment.txt", "content_base64": encoded},
    )
    assert status == 201
    assert (tmp_path / uploaded["path"]).read_bytes() == b"uploaded"
    session_id = create_session(service, server)
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/skill",
        {"name": "job-radar", "enabled": True},
    )
    assert status == 202
    assert wait_job(service, server, queued["job_id"])["status"] == "completed"
    status, detail = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200 and "job-radar" in detail["active_skills"]


def test_polling_legacy_session_while_worker_holds_store_lock_stays_available(
    live_server,
):
    service, server = live_server
    # Simulate a session created by the pre-product build.  The first product
    # read must migrate it, but a worker may already own the session lock.
    legacy = service.engine.store.create(active_skills=[])
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def hold_session_lock() -> None:
        with service.engine.store.lock(legacy.id):
            entered.set()
            assert release.wait(timeout=5)
        finished.set()

    worker = threading.Thread(target=hold_session_lock, daemon=True)
    worker.start()
    assert entered.wait(timeout=5)
    try:
        status, detail = request(service, server, "GET", f"/api/sessions/{legacy.id}")
        assert status == 200
        assert detail["id"] == legacy.id

        status, listing = request(service, server, "GET", "/api/sessions")
        assert status == 200
        assert any(row["id"] == legacy.id for row in listing["sessions"])
    finally:
        release.set()
        assert finished.wait(timeout=5)
        worker.join(timeout=2)

    # The next action can acquire the lock and persist the one-time migration.
    status, detail = request(service, server, "GET", f"/api/sessions/{legacy.id}")
    assert status == 200
    assert "job-radar" in detail["active_skills"]


def test_product_workflow_mount_and_legacy_migration_are_revision_idempotent(tmp_path: Path):
    service = build_service(tmp_path, demo=True)

    current = service.engine.new_session()
    current_revision = current.revision
    for _ in range(3):
        restored = service.engine.ensure_workflow(current.id)
        assert restored.revision == current_revision
        assert restored.active_skills == ["job-radar"]

    legacy = service.engine.store.create(active_skills=[])
    migrated = service.engine.ensure_workflow(legacy.id)
    assert migrated.active_skills == ["job-radar"]
    migrated_revision = migrated.revision
    assert migrated_revision == legacy.revision + 1
    for _ in range(3):
        restored = service.engine.ensure_workflow(legacy.id)
        assert restored.revision == migrated_revision
        assert restored.active_skills == ["job-radar"]


def test_http_image_attachment_is_one_message_and_snapshot_is_redacted(
    live_server, tmp_path: Path
):
    service, server = live_server
    # A valid 1x1 PNG, kept inline so this test exercises the same relative
    # workspace path protocol used by the browser after upload.
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    (tmp_path / "jd.png").write_bytes(image)
    session_id = create_session(service, server)

    routers_seen_by_tools: list[Any] = []
    definition = service.engine.registry._tools["set_jd"]
    original_handler = definition.handler

    def record_context(arguments: dict, context: Any):
        routers_seen_by_tools.append(context.router)
        return original_handler(arguments, context)

    definition.handler = record_context
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "请读取这张 JD 截图", "attachments": ["jd.png"]},
    )
    assert status == 202
    result = wait_job(service, server, queued["job_id"])
    assert result["status"] == "completed"
    assert routers_seen_by_tools == [None]

    status, detail = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200
    user_messages = [message for message in detail["messages"] if message["role"] == "user"]
    assert len(user_messages) == 1
    content = user_messages[0]["content"]
    assert isinstance(content, list)
    image_blocks = [block for block in content if block.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["media_type"] == "image/png"
    assert image_blocks[0]["attached"] is True
    assert "data" not in image_blocks[0]
    assert "jd.png" in content[0]["text"]
    assert "iVBORw0KGgo" not in json.dumps(detail, ensure_ascii=False)

    status, listing = request(service, server, "GET", "/api/sessions")
    assert status == 200
    row = next(item for item in listing["sessions"] if item["id"] == session_id)
    assert row["job_title"]
    assert row["title"].startswith(row["job_title"])


def test_http_fake_image_is_rejected_without_appending_user_message(
    live_server, tmp_path: Path
):
    service, server = live_server
    (tmp_path / "fake.jpg").write_text("this is not a JPEG", encoding="utf-8")
    session_id = create_session(service, server)
    before = service.engine.store.get(session_id)
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "请读取截图", "attachments": ["fake.jpg"]},
    )
    assert status == 202
    failed = wait_job(service, server, queued["job_id"])
    assert failed["status"] == "failed"
    assert "图片附件内容无法验证" in failed["error"]
    after = service.engine.store.get(session_id)
    assert len(after.messages) == len(before.messages)
    assert not any("请读取截图" in str(message.content) for message in after.messages)


def test_http_image_total_bytes_limit_is_per_turn(live_server, tmp_path: Path, monkeypatch):
    service, server = live_server
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    (tmp_path / "first.png").write_bytes(image)
    (tmp_path / "second.png").write_bytes(image)
    # Exercise the aggregate guard with tiny fixtures instead of allocating
    # two 10 MiB files.  The production default remains 20 MiB per user turn.
    monkeypatch.setattr(engine_module, "MAX_IMAGE_TOTAL_BYTES", len(image) + 1)
    session_id = create_session(service, server)
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "请一起读取两张截图", "attachments": ["first.png", "second.png"]},
    )
    assert status == 202
    failed = wait_job(service, server, queued["job_id"])
    assert failed["status"] == "failed"
    assert "本轮图片附件总大小超过" in failed["error"]
    assert not any(
        "请一起读取两张截图" in str(message.content)
        for message in service.engine.store.get(session_id).messages
    )


@pytest.mark.parametrize("suffix, media_type", [(".bmp", "image/bmp"), (".tiff", "image/tiff"), (".avif", "image/avif")])
def test_http_non_common_image_formats_are_rejected(
    live_server, tmp_path: Path, suffix: str, media_type: str
):
    service, server = live_server
    # The bytes need not be decoded: the explicit media type is rejected before
    # an unsupported format could be sent to a provider.
    (tmp_path / f"unsupported{suffix}").write_bytes(b"unsupported-format")
    session_id = create_session(service, server)
    status, queued = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {
            "text": "请读取这张截图",
            "attachments": [{"path": f"unsupported{suffix}", "media_type": media_type}],
        },
    )
    assert status == 202
    failed = wait_job(service, server, queued["job_id"])
    assert failed["status"] == "failed"
    assert "仅支持 PNG、JPEG、WEBP、GIF" in failed["error"]


def _web_profile_payload(*, open_questions=None):
    return UserSkillProfile(
        summary="完成过 Python 服务",
        target_direction="后端开发",
        skills=[
            SkillItem(
                name="Python",
                proficiency="working",
                confidence=0.7,
                sources=["self_report"],
                evidence="独立实现过 HTTP 服务",
            )
        ],
        open_questions=list(open_questions if open_questions is not None else ["是否有上线证据？"]),
    ).model_dump(mode="json")


def _web_jd_payload(title="后端工程师", company="Example Labs"):
    return JDCard(
        job_title=title,
        company=company,
        hard_requirements=["Python"],
        skill_tags=["Python"],
        raw_summary="岗位正文",
    ).model_dump(mode="json")


class _CaptureRouter:
    """Small provider double that records the exact messages sent to a turn."""

    def __init__(self) -> None:
        self.messages: list[list[Any]] = []

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        self.messages.append([message.model_copy(deep=True) for message in messages])
        return LLMResponse(text="已读取当前求职工件")


def test_ui_mutation_messages_and_dynamic_workflow_context_reach_one_router(
    tmp_path: Path,
):
    service = build_service(tmp_path, demo=True)
    router = _CaptureRouter()
    service.engine.router = router
    session = service.create_session()
    session_id = str(session["id"])

    service.update_profile(session_id, {"profile": _web_profile_payload(open_questions=[])})
    service.update_jd(session_id, {"jd": _web_jd_payload(), "confirmed": True})
    before = service.engine.store.get(session_id)
    assert any("用户通过界面更新了个人技能档案" in str(item.content) for item in before.messages)
    assert any("用户通过界面更新了 JD" in str(item.content) for item in before.messages)

    result = service.engine.run("请继续", session_id=session_id)
    assert result.status == "completed"
    system = router.messages[-1][0]
    assert system.role == "system"
    assert "当前求职工件" in system.content
    assert "后端工程师" in system.content
    assert "Example Labs" in system.content
    assert "完成过 Python 服务" in system.content
    assert '"clarification"' in system.content
    assert '"project"' in system.content
    assert '"progress"' in system.content


def test_cloned_profile_is_visible_without_old_chat_in_router_context(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    router = _CaptureRouter()
    service.engine.router = router
    source_id = str(service.create_session()["id"])
    service.update_profile(source_id, {"profile": _web_profile_payload(open_questions=[])})
    old_turn = service.engine.run("源会话旧聊天", session_id=source_id)
    assert old_turn.status == "completed"

    cloned_id = str(service.create_session(source_id)["id"])
    clone_turn = service.engine.run("继续", session_id=cloned_id)
    assert clone_turn.status == "completed"
    clone_messages = service.engine.store.get(cloned_id).messages
    assert not any("源会话旧聊天" in str(message.content) for message in clone_messages)
    system = router.messages[-1][0]
    assert "完成过 Python 服务" in system.content
    assert "job-radar-workflow-state" in system.content


def test_jd_profile_routes_clone_and_delete_preserve_uploads(live_server, tmp_path: Path):
    service, server = live_server
    source_id = create_session(service, server)

    status, profile = request(
        service,
        server,
        "POST",
        f"/api/sessions/{source_id}/profile",
        {"profile": _web_profile_payload()},
    )
    assert status == 200
    assert profile["clarification"]["status"] == "active"

    status, jd = request(
        service,
        server,
        "POST",
        f"/api/sessions/{source_id}/jd",
        {"jd": _web_jd_payload(), "confirmed": True},
    )
    assert status == 200
    assert jd["confirmed"] is True
    assert jd["confirmation"]["actor"] == "user"

    status, listing = request(service, server, "GET", "/api/sessions")
    assert status == 200
    source_summary = next(row for row in listing["sessions"] if row["id"] == source_id)
    assert source_summary["title"] == "后端工程师 · Example Labs"
    assert source_summary["jd_confirmed"] is True

    status, cloned = request(
        service,
        server,
        "POST",
        "/api/sessions",
        {"source_session_id": source_id},
    )
    assert status == 201
    clone_id = cloned["id"]
    assert clone_id != source_id
    status, clone_profile = request(service, server, "GET", f"/api/sessions/{clone_id}/profile")
    assert status == 200
    assert clone_profile["profile"] == profile["profile"]
    status, clone_jd = request(service, server, "GET", f"/api/sessions/{clone_id}/jd")
    assert status == 200
    assert clone_jd["jd"] is None
    assert clone_jd["confirmed"] is False
    snapshot_status, snapshot = request(service, server, "GET", f"/api/sessions/{clone_id}")
    assert snapshot_status == 200
    assert not [message for message in snapshot["messages"] if message["role"] == "user"]

    uploaded = service.upload("shared.txt", base64.b64encode(b"shared").decode("ascii"))
    status, deleted = request(service, server, "DELETE", f"/api/sessions/{source_id}")
    assert status == 200 and deleted["ok"] is True
    assert (tmp_path / uploaded["path"]).is_file()
    status, _ = request(service, server, "GET", f"/api/sessions/{source_id}")
    assert status == 404


def test_jd_edit_clears_confirmation_and_profile_answered_diff(live_server):
    service, server = live_server
    session_id = create_session(service, server)
    status, _ = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/jd",
        {"jd": _web_jd_payload(), "confirmed": True},
    )
    assert status == 200
    status, edited = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/jd",
        {"jd": _web_jd_payload(title="数据工程师"), "confirmed": False},
    )
    assert status == 200 and edited["confirmed"] is False

    status, _ = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/profile",
        {"profile": _web_profile_payload()},
    )
    assert status == 200
    status, completed = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/profile",
        {"profile": _web_profile_payload(open_questions=[])},
    )
    assert status == 200
    assert completed["clarification"]["status"] == "completed"
    assert completed["clarification"]["answered"] == ["是否有上线证据？"]


@pytest.mark.parametrize("blocked", ["pending", "queued", "execution"])
def test_ui_artifact_mutation_rejects_incomplete_agent_turn(live_server, blocked: str):
    service, server = live_server
    session_id = create_session(service, server)
    session = service.engine.store.get(session_id)
    if blocked == "pending":
        session.pending = PendingApproval(tool_name="write_file", summary="等待审批")
    elif blocked == "queued":
        session.queued_calls = [ToolCall(id="queued-1", name="read_file", arguments={})]
    else:
        session.execution = {"status": "running", "kind": "approved_tool"}
    service.engine.store.save(session)
    status, _ = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/jd",
        {"jd": _web_jd_payload(), "confirmed": True},
    )
    assert status == 409
    after = service.engine.store.get(session_id)
    assert not any("用户通过界面更新了 JD" in str(item.content) for item in after.messages)


def test_github_token_and_local_import_routes_do_not_echo_secret(live_server, tmp_path: Path):
    service, server = live_server
    source = tmp_path / "outside" / "resume.txt"
    source.parent.mkdir()
    source.write_text("candidate evidence", encoding="utf-8")

    status, imported = request(
        service,
        server,
        "POST",
        "/api/imports/local",
        {"path": str(source)},
    )
    assert status == 201
    assert imported["manifest"]["imported_count"] == 1
    imported_path = tmp_path / imported["imported"][0]
    assert imported_path.read_text(encoding="utf-8") == "candidate evidence"

    secret = "ghp-test-secret-value"
    status, payload = request(
        service,
        server,
        "POST",
        "/api/settings/github-token",
        {"token": secret},
    )
    assert status == 200 and payload["has_token"] is True
    assert secret not in json.dumps(payload)
    status, payload = request(service, server, "GET", "/api/settings/github-token")
    assert status == 200 and payload == {"has_token": True}
    status, payload = request(
        service,
        server,
        "POST",
        "/api/settings/github-token",
        {"clear": True},
    )
    assert status == 200 and payload["has_token"] is False


def test_ui_theme_settings_are_validated_and_persisted_atomically(live_server, tmp_path: Path):
    service, server = live_server
    status, payload = request(service, server, "GET", "/api/settings/ui")
    assert status == 200 and payload == {"theme": "dark"}
    status, payload = request(service, server, "POST", "/api/settings/ui", {"theme": "light"})
    assert status == 200 and payload == {"theme": "light"}
    settings_path = tmp_path / ".agent-coach" / "ui-settings.json"
    assert settings_path.read_text(encoding="utf-8").strip() == '{"theme":"light"}'
    status, payload = request(service, server, "GET", "/api/settings/ui")
    assert status == 200 and payload == {"theme": "light"}
    status, _ = request(service, server, "POST", "/api/settings/ui", {"theme": "system"})
    assert status == 400
