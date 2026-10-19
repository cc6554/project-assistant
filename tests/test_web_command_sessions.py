from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any

import pytest

from agent_coach.contracts import AgentSession
from agent_coach.engine import RunResult
from agent_coach.tools import (
    command_sessions,
    poll_command_session,
    start_command_session,
    stop_command_session,
)
from agent_coach.web import WebService, make_server


@pytest.fixture
def live_command_server(tmp_path: Path):
    service = WebService(tmp_path, demo=True)
    server = make_server(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(
    service: WebService,
    server: Any,
    method: str,
    path: str,
    body: Any = None,
) -> tuple[int, Any]:
    port = int(server.server_address[1])
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {service.token}",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        client.request(method, path, body=payload, headers=headers)
        response = client.getresponse()
        raw = response.read()
        status = response.status
    finally:
        client.close()
    try:
        detail = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        detail = raw
    return status, detail


def wait_job(service: WebService, server: Any, job_id: str) -> dict[str, Any]:
    for _ in range(300):
        status, payload = request(service, server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError("command job did not finish")


def _start_running_command(service: WebService, session: AgentSession, *, seconds: float = 20.0):
    scope = {
        "workspace": str(service.workspace),
        "session_id": session.id,
        "scope": "main",
    }
    command = start_command_session(
        [
            sys.executable,
            "-u",
            "-c",
            f"import time; print('live', flush=True); time.sleep({seconds})",
        ],
        shell=False,
        cwd=Path(service.workspace),
        timeout=None,
        purpose="HTTP command-session test",
        owner_workspace=service.workspace,
        owner_session_id=session.id,
        scope=scope,
    )
    return command, scope


def _persist_running_claim(service: WebService, session: AgentSession, command: Any, scope: dict[str, str]) -> dict[str, Any]:
    snapshot = command.poll(wait_seconds=0)
    session.execution = {
        "status": "running",
        "kind": "approved_command",
        "command_session_id": command.id,
        "command_status": "running",
        "command_snapshot": snapshot,
        "scope": "main",
        "metadata": {"scope": "main"},
    }
    service.engine.store.save(session)
    return snapshot


def _cleanup_command(command: Any, scope: dict[str, str]) -> None:
    try:
        snapshot = poll_command_session(command.id, wait_seconds=0, scope=scope)
        if snapshot.get("status") in {"starting", "running", "uncertain"}:
            stop_command_session(command.id, scope=scope)
    except Exception:
        pass
    try:
        command_sessions.forget(command.id)
    except Exception:
        pass


def test_http_stop_busy_command_terminates_process_and_resumes_once(live_command_server):
    service, server = live_command_server
    session = service.engine.new_session()
    command, scope = _start_running_command(service, session)
    _persist_running_claim(service, session, command, scope)
    initial_snapshot = command.poll(wait_seconds=0)
    resume_started = threading.Event()
    resume_calls: list[tuple[str, str]] = []

    def poll_command(session_id: str, command_id: str, *, wait_seconds: float = 0.0, **_kwargs: Any) -> RunResult:
        resume_calls.append((session_id, command_id))
        resume_started.set()
        snapshot = poll_command_session(command_id, wait_seconds=10.0, scope=scope)
        return RunResult(
            status="completed",
            session=service.engine.store.get(session_id),
            message="模型已续跑一次",
            data=snapshot,
        )

    original_poll = service.engine.poll_command
    service.engine.poll_command = poll_command
    try:
        job_id = service.submit(
            session.id,
            lambda: RunResult(
                status="running_command",
                session=session,
                message="命令仍在运行",
                data=initial_snapshot,
            ),
        )
        assert resume_started.wait(timeout=2), "后台 command poll did not start"

        status, listing = request(
            service,
            server,
            "GET",
            f"/api/sessions/{session.id}/commands?scope=main",
        )
        assert status == 200
        listed = next(row for row in listing["commands"] if row["execution_id"] == command.id)
        assert listed["status"] == "running"

        status, detail = request(
            service,
            server,
            "GET",
            f"/api/sessions/{session.id}/commands/{command.id}?scope=main&wait_seconds=NaN",
        )
        assert status == 400
        assert "wait_seconds" in detail["error"]

        status, stopped = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session.id}/commands/{command.id}/stop",
            {"scope": "main"},
        )
        assert status == 200
        assert stopped["job_id"] == job_id

        result = wait_job(service, server, job_id)
        assert result["status"] == "completed", result
        assert len(resume_calls) == 1
        assert resume_calls[0] == (session.id, command.id)
        final_snapshot = poll_command_session(command.id, wait_seconds=0, scope=scope)
        assert final_snapshot["status"] == "stopped"
        assert final_snapshot["returncode"] is not None
        assert command._process.poll() is not None
    finally:
        service.engine.poll_command = original_poll
        _cleanup_command(command, scope)


def test_http_command_scope_does_not_leak_between_sessions(live_command_server):
    service, server = live_command_server
    owner = service.engine.new_session()
    other = service.engine.new_session()
    command, scope = _start_running_command(service, owner, seconds=10.0)
    try:
        status, listing = request(
            service,
            server,
            "GET",
            f"/api/sessions/{other.id}/commands?scope=main",
        )
        assert status == 200
        assert all(row["execution_id"] != command.id for row in listing["commands"])

        status, detail = request(
            service,
            server,
            "GET",
            f"/api/sessions/{other.id}/commands/{command.id}?scope=main",
        )
        assert status == 400
        assert "command" in detail["error"] or "执行" in detail["error"]

        status, detail = request(
            service,
            server,
            "POST",
            f"/api/sessions/{other.id}/commands/{command.id}/stop",
            {"scope": "main"},
        )
        assert status == 400
        assert "command" in detail["error"] or "执行" in detail["error"]
        assert poll_command_session(command.id, wait_seconds=0, scope=scope)["status"] in {"starting", "running"}
    finally:
        _cleanup_command(command, scope)
