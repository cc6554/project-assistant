"""Regression checks for durable sidebar session metadata."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import threading
from typing import Any

from agent_coach.web import build_service, make_server
from agent_coach.legacy.domain.schemas import JDCard


def _request(service: Any, server: Any, method: str, path: str, body: Any = None):
    port = int(server.server_address[1])
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {service.token}",
    }
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if payload is not None:
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, body=payload, headers=headers)
    response = client.getresponse()
    raw = response.read()
    client.close()
    return response.status, json.loads(raw.decode("utf-8"))


def _start(service):
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _create(service, server) -> str:
    status, payload = _request(service, server, "POST", "/api/sessions", {})
    assert status == 201
    return str(payload["id"])


def _jd(title: str, company: str) -> dict[str, Any]:
    return JDCard(
        job_title=title,
        company=company,
        hard_requirements=["Python"],
        skill_tags=["Python"],
        raw_summary="岗位正文",
    ).model_dump(mode="json")


def test_title_pin_and_jd_updates_survive_restart(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    server, thread = _start(service)
    try:
        first = _create(service, server)
        second = _create(service, server)
        status, _ = _request(
            service,
            server,
            "POST",
            f"/api/sessions/{first}/jd",
            {"jd": _jd("后端工程师", "甲公司"), "confirmed": True},
        )
        assert status == 200

        status, renamed = _request(
            service,
            server,
            "PATCH",
            f"/api/sessions/{first}",
            {"title": "我的目标岗位", "pinned": True},
        )
        assert status == 200
        assert renamed["title"] == "我的目标岗位"
        assert renamed["custom_title"] == "我的目标岗位"
        assert renamed["pinned"] is True

        status, listing = _request(service, server, "GET", "/api/sessions")
        assert status == 200
        assert [row["id"] for row in listing["sessions"]][:2] == [first, second]
        assert listing["sessions"][0]["title"] == "我的目标岗位"
        assert listing["sessions"][0]["company"] == "甲公司"

        # A later generated JD title must not overwrite the user name.
        status, _ = _request(
            service,
            server,
            "POST",
            f"/api/sessions/{first}/jd",
            {"jd": _jd("数据工程师", "乙公司"), "confirmed": True},
        )
        assert status == 200
        status, snapshot = _request(service, server, "GET", f"/api/sessions/{first}")
        assert status == 200
        assert snapshot["title"] == "我的目标岗位"
        assert snapshot["skill_state"]["job-radar"]["jd"]["company"] == "乙公司"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    restored = build_service(tmp_path, demo=True)
    restored_server, restored_thread = _start(restored)
    try:
        status, listing = _request(restored, restored_server, "GET", "/api/sessions")
        assert status == 200
        row = next(item for item in listing["sessions"] if item["id"] == first)
        assert row["title"] == "我的目标岗位"
        assert row["custom_title"] == "我的目标岗位"
        assert row["pinned"] is True

        # Clearing the custom title returns to the new job-only automatic
        # title while leaving the company artifact untouched.
        status, cleared = _request(
            restored,
            restored_server,
            "PATCH",
            f"/api/sessions/{first}/metadata",
            {"title": None, "pinned": False},
        )
        assert status == 200
        assert cleared["title"] == "数据工程师"
        assert cleared["custom_title"] is None
        assert cleared["pinned"] is False
        assert cleared["company"] == "乙公司"
    finally:
        restored_server.shutdown()
        restored_server.server_close()
        restored_thread.join(timeout=2)


def test_practice_window_is_nested_and_never_listed_as_a_session(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    service.engine.store.save(session)
    service.update_jd(
        session.id,
        {
            "jd": _jd("算法工程师", "丙公司"),
            "confirmed": True,
        },
    )
    # A practice thread is represented inside the owner AgentSession.  Opening
    # one must not add another row to the root session listing.
    stored = service.engine.store.get(session.id)
    stored.skill_state["job-radar"]["selected_project"] = {
        "full_name": "demo/repo",
        "html_url": "https://github.com/demo/repo",
    }
    service.engine.store.save(stored)
    opened = service.open_practice(session.id, {"project_key": "demo/repo"})
    assert opened["practice_thread_id"]
    rows = service.session_summaries()
    assert [row["id"] for row in rows] == [session.id]
    assert rows[0]["is_practice"] is False
    assert all("practice_thread_id" not in row for row in rows)


def test_legacy_session_timestamps_use_mtime_and_repeated_listing_is_stable(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    first = service.engine.new_session()
    second = service.engine.new_session()
    paths = [service.engine.store._path(first.id), service.engine.store._path(second.id)]
    # Simulate files written before created_at/updated_at existed.  Keep
    # explicit, different mtimes so the expected store order is unambiguous.
    for index, path in enumerate(paths, start=1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        timestamp = 1_700_000_000 + index
        os.utime(path, (timestamp, timestamp))

    first_listing = service.session_summaries()
    second_listing = service.session_summaries()
    assert [row["id"] for row in first_listing] == [second.id, first.id]
    assert [row["id"] for row in second_listing] == [second.id, first.id]
    first_times = {
        row["id"]: (row["created_at"], row["updated_at"])
        for row in first_listing
    }
    second_times = {
        row["id"]: (row["created_at"], row["updated_at"])
        for row in second_listing
    }
    assert first_times == second_times
    assert all(created == updated for created, updated in first_times.values())
