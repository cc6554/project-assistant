"""HTTP contract checks for the dedicated skills interview window."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import quote

from agent_coach import job_radar
from agent_coach.legacy.core.schema import LLMResponse
from agent_coach.web import build_service, make_server


class HttpInterviewRouter:
    provider = "http-interview-fixture"
    model = "http-interview-fixture"

    def complete(self, _task: str, _messages: list[Any], **_kwargs: Any) -> LLMResponse:
        return LLMResponse(text="请讲一个你负责上线的服务。")


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
    return response.status, json.loads(raw.decode("utf-8"))


def wait_job(service: Any, server: Any, job_id: str) -> dict[str, Any]:
    for _ in range(300):
        status, payload = request(service, server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if payload["status"] != "running":
            return payload
        time.sleep(0.005)
    raise AssertionError("HTTP interview fixture job did not finish")


def _service(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    service.engine.router = HttpInterviewRouter()
    session = service.engine.new_session()
    state = job_radar.ensure_state(session)
    state["jd"] = {"job_title": "后端工程师", "skill_tags": ["Python"]}
    state["profile"] = {
        "summary": "Python 项目经验",
        "skills": [],
        "open_questions": ["是否有上线证据？"],
        "updated_at": "2026-01-01T00:00:00",
    }
    service.engine.store.save(session)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return service, server, thread, session.id


def test_http_interview_open_reopen_and_message_scope(tmp_path: Path) -> None:
    service, server, thread, session_id = _service(tmp_path)
    try:
        status, opened = request(service, server, "POST", f"/api/sessions/{session_id}/interview", {})
        assert status == 200
        assert opened["job_id"]
        thread_id = opened["interview_thread_id"]
        terminal = wait_job(service, server, opened["job_id"])
        assert terminal["status"] == "completed"

        status, snapshot = request(
            service,
            server,
            "GET",
            f"/api/sessions/{session_id}/interview/{quote(thread_id, safe='')}",
        )
        assert status == 200
        assert snapshot["opening_completed"] is True
        assert [item["role"] for item in snapshot["messages"]] == ["user", "assistant"]

        status, reopened = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/interview",
            {"interview_thread_id": thread_id},
        )
        assert status == 200
        assert "job_id" not in reopened
        assert len(reopened["messages"]) == 2

        status, accepted = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/interview/{quote(thread_id, safe='')}/message",
            {"text": "我负责 Docker 部署和上线监控。"},
        )
        assert status == 202
        terminal = wait_job(service, server, accepted["job_id"])
        assert terminal["status"] == "completed"

        status, final = request(
            service,
            server,
            "GET",
            f"/api/sessions/{session_id}/interview/{quote(thread_id, safe='')}",
        )
        assert status == 200
        assert [item["role"] for item in final["messages"]] == ["user", "assistant", "user", "assistant"]
        assert all(item["metadata"]["scope"] == "interview" for item in final["messages"])

        status, root = request(service, server, "GET", f"/api/sessions/{session_id}")
        assert status == 200
        assert root["interview_filter"] == {"scope": "interview", "interview_thread_id": thread_id}
        assert root["interview_busy"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
