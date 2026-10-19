"""HTTP-level regression checks for scoped practice and workflow actions.

These tests use a loopback server with an offline router and synthetic session
state.  They exercise the browser-facing asynchronous protocol rather than
calling ``WebService`` methods directly.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import quote

import pytest

from agent_coach import job_radar
from agent_coach.contracts import PendingApproval, ToolDefinition, ToolOutcome
from agent_coach.engine import AgentEngine
from agent_coach.legacy.core.schema import LLMResponse, ToolCall, ToolSpec
from agent_coach.web import build_service, make_server


class StaticRouter:
    provider = "http-fixture"
    model = "http-fixture"

    def complete(self, task, messages, *, tools=None, **kwargs):
        return LLMResponse(text="合成 HTTP 练习回复")


class ApprovalRouter:
    provider = "http-fixture"
    model = "http-fixture"

    def complete(self, task, messages, *, tools=None, **kwargs):
        if not any(message.role == "tool" for message in messages):
            return LLMResponse(
                text="需要确认一个合成练习操作。",
                tool_calls=[
                    ToolCall(
                        id="http-fixture-call",
                        name="http_fixture_approval",
                        arguments={},
                    )
                ],
            )
        return LLMResponse(text="合成练习操作已完成。")


@pytest.fixture
def live_http_service(tmp_path: Path):
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


def request(service: Any, server: Any, method: str, path: str, body: Any = None):
    headers = {
        "Host": f"127.0.0.1:{server.server_address[1]}",
        "Authorization": f"Bearer {service.token}",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    client.request(method, path, body=payload, headers=headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = raw
    return response.status, parsed


def wait_job(service: Any, server: Any, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        status, payload = request(service, server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError("HTTP fixture job did not finish")


def seed_practice(service: Any, server: Any, router: Any) -> tuple[str, str]:
    service.engine.router = router
    session = service.engine.new_session()
    state = job_radar.ensure_state(session)
    state["plan"] = {"phases": [{"title": "合成练习"}]}
    state["selected_project"] = {
        "project_key": "fixture:http-practice",
        "full_name": "fixture/http-practice",
        "name": "HTTP practice fixture",
        "language": "Python",
    }
    service.engine.store.save(session)
    status, opened = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session.id}/practice",
        {
            "project_key": "fixture:http-practice",
            "practice_thread_id": "http-practice-thread",
        },
    )
    assert status == 200, opened
    assert opened["project_key"] == "fixture:http-practice"
    return session.id, str(opened["practice_thread_id"])


def register_approval_tool(service: Any) -> None:
    def prepare(arguments, context):
        return PendingApproval(
            tool_name="http_fixture_approval",
            summary="确认合成 HTTP 练习操作",
            preview="不会写入真实工作区",
        )

    def execute(pending, context):
        return ToolOutcome("合成练习操作已确认")

    service.engine.registry.register(
        ToolDefinition(
            spec=ToolSpec(
                name="http_fixture_approval",
                description="synthetic approval tool used only by an HTTP regression",
                parameters={"type": "object", "properties": {}},
            ),
            handler=prepare,
            execute=execute,
        )
    )


def test_http_practice_message_persists_scope_and_main_reset(live_http_service):
    service, server = live_http_service
    session_id, thread_id = seed_practice(service, server, StaticRouter())
    path = f"/api/sessions/{session_id}/practice/{quote(thread_id, safe='')}/message"

    status, accepted = request(
        service,
        server,
        "POST",
        path,
        {
            "text": "练习消息：请记录合成证据。",
            "scope": "practice",
            "project_key": "fixture:http-practice",
        },
    )
    assert status == 202, accepted
    terminal = wait_job(service, server, accepted["job_id"])
    assert terminal["status"] == "completed", terminal
    assert terminal["result"]["status"] == "completed", terminal

    status, practice = request(
        service,
        server,
        "GET",
        f"/api/sessions/{session_id}/practice/{quote(thread_id, safe='')}",
    )
    assert status == 200
    assert practice["project_key"] == "fixture:http-practice"
    assert [message["role"] for message in practice["messages"]] == ["user", "assistant"]
    assert all(
        message["metadata"]
        == {
            "scope": "practice",
            "project_key": "fixture:http-practice",
            "practice_thread_id": "http-practice-thread",
        }
        for message in practice["messages"]
    )

    status, accepted = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {"text": "主会话消息：回到岗位计划。"},
    )
    assert status == 202, accepted
    terminal = wait_job(service, server, accepted["job_id"])
    assert terminal["status"] == "completed", terminal

    status, snapshot = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200
    assert snapshot["practice_filter"] == {"scope": "main"}
    main_messages = [
        message
        for message in snapshot["messages"]
        if message.get("content") == "主会话消息：回到岗位计划。"
    ]
    assert main_messages and all(message["metadata"] == {"scope": "main"} for message in main_messages)

    status, practice_after_main = request(
        service,
        server,
        "GET",
        f"/api/sessions/{session_id}/practice/{quote(thread_id, safe='')}",
    )
    assert status == 200
    assert all(message["metadata"]["scope"] == "practice" for message in practice_after_main["messages"])
    assert all(message.get("content") != "主会话消息：回到岗位计划。" for message in practice_after_main["messages"])


def test_http_practice_approval_survives_engine_restart(live_http_service):
    service, server = live_http_service
    register_approval_tool(service)
    session_id, thread_id = seed_practice(service, server, ApprovalRouter())
    path = f"/api/sessions/{session_id}/practice/{quote(thread_id, safe='')}/message"

    status, accepted = request(
        service,
        server,
        "POST",
        path,
        {"text": "开始需要确认的合成练习。", "scope": "practice", "project_key": "fixture:http-practice"},
    )
    assert status == 202, accepted
    terminal = wait_job(service, server, accepted["job_id"])
    assert terminal["status"] == "completed", terminal
    assert terminal["result"]["status"] == "pending_approval", terminal

    status, pending_snapshot = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200
    pending = pending_snapshot["pending"]
    assert pending["scope"] == "practice"
    assert pending["project_key"] == "fixture:http-practice"
    assert pending["practice_thread_id"] == "http-practice-thread"

    # Recreate the engine around the same durable store before approving.  The
    # registry and router are process-local test fixtures; the pending claim
    # and transcript must come from the persisted session.
    service.engine = AgentEngine(service.engine.store, service.engine.registry, ApprovalRouter())
    status, accepted = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/approve",
        {"id": pending["id"]},
    )
    assert status == 202, accepted
    terminal = wait_job(service, server, accepted["job_id"])
    assert terminal["status"] == "completed", terminal
    assert terminal["result"]["status"] == "completed", terminal

    status, practice = request(
        service,
        server,
        "GET",
        f"/api/sessions/{session_id}/practice/{quote(thread_id, safe='')}",
    )
    assert status == 200
    assert practice["pending"] is None
    assert [message["role"] for message in practice["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert all(
        message["metadata"]["scope"] == "practice"
        and message["metadata"]["project_key"] == "fixture:http-practice"
        and message["metadata"]["practice_thread_id"] == "http-practice-thread"
        for message in practice["messages"]
    )


def test_http_workflow_filter_persists_and_busy_request_does_not_mutate(live_http_service):
    service, server = live_http_service
    session_id, _thread_id = seed_practice(service, server, StaticRouter())
    first_filter = {
        "min_stars": 120,
        "include": ["Python", "评估"],
        "exclude": ["demo"],
        "other": "必须有完整文档和 Windows 本地复现证据",
        "paper_projects": True,
        "rerank": False,
    }
    status, accepted = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/message",
        {
            "text": "按新的合成筛选要求重新搜索项目。",
            "workflow_action": {
                "step": "plan",
                "summary": "保存合成筛选要求",
                "project_filter": first_filter,
            },
        },
    )
    assert status == 202, accepted
    terminal = wait_job(service, server, accepted["job_id"])
    assert terminal["status"] == "completed", terminal
    status, snapshot = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200
    assert snapshot["skill_state"]["job-radar"]["project_filter"] == first_filter

    second_filter = {**first_filter, "other": "这次请求不应在忙时写入"}
    service._busy[session_id] = "synthetic-busy"
    try:
        status, rejected = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/message",
            {
                "text": "忙时尝试修改合成筛选要求。",
                "workflow_action": {
                    "step": "plan",
                    "summary": "忙时修改",
                    "project_filter": second_filter,
                },
            },
        )
    finally:
        service._busy.pop(session_id, None)
    assert status == 409, rejected

    status, snapshot = request(service, server, "GET", f"/api/sessions/{session_id}")
    assert status == 200
    assert snapshot["skill_state"]["job-radar"]["project_filter"] == first_filter
