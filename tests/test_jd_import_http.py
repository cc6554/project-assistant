"""HTTP contract tests for the structured background JD import route."""

from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from agent_coach import integrations, job_radar
from agent_coach.contracts import AgentSession, PendingApproval, ToolOutcome
from agent_coach.legacy.core.schema import LLMResponse, Message, ToolCall
from agent_coach.web import _jd_search_progress, build_service, make_server


class CaptureJDRouter:
    provider = "jd-import-fixture"
    model = "jd-import-fixture"
    search_url = "https://careers.acme.test/jobs/backend-fixture"

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        # The search importer now has a strict evidence boundary: even this
        # deterministic fixture must exercise search_jobs -> fetch_url before
        # submitting a sample_count.  Source/image modes remain the compact
        # one-call fixture below because they are not public-search flows.
        mode = ""
        for message in reversed(messages):
            if message.role != "user" or not isinstance(message.content, str):
                continue
            marker = "模式："
            if marker in message.content:
                mode = message.content.split(marker, 1)[1].split("）", 1)[0].strip()
            break
        if mode == "search":
            last = messages[-1] if messages else None
            if last is not None and last.role == "user":
                call = ToolCall(
                    id="jd-import-search-jobs",
                    name="search_jobs",
                    arguments={"query": "Python 后端工程师", "limit": 5},
                )
            elif last is not None and last.role == "tool" and last.name == "search_jobs":
                call = ToolCall(
                    id="jd-import-fetch-search",
                    name="fetch_url",
                    arguments={"url": self.search_url},
                )
            elif last is not None and last.role == "tool" and last.name == "fetch_url":
                call = ToolCall(
                    id="jd-import-set-search-jd",
                    name="set_jd",
                    arguments={
                        "jd": {
                            "job_title": "后端工程师",
                            "company": "Fixture Labs",
                            "hard_requirements": ["Python 服务开发"],
                            "skill_tags": ["Python"],
                            "raw_summary": "已读取一个公开岗位详情页；暂不足形成共性。",
                            "uncertain_fields": [],
                            "sample_target": 5,
                            "sample_count": 1,
                            "comparison_status": "insufficient_samples",
                        },
                        "source_urls": [self.search_url],
                        "source": {
                            "kind": "public_source",
                            "evidence": ["公开岗位详情页已通过 fetch_url 读取"],
                        },
                    },
                )
            else:
                return LLMResponse(text="JD 已整理，等待你核对确认。")
            return LLMResponse(tool_calls=[call])
        # A session retains prior turns. Only the current turn's trailing
        # boundary determines whether this fixture should submit set_jd again.
        if messages and messages[-1].role == "tool":
            return LLMResponse(text="JD 已整理，等待你核对确认。")
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="jd-import-set-jd",
                    name="set_jd",
                    arguments={
                        "jd": {
                            "job_title": "后端工程师",
                            "company": "Fixture Labs",
                            "hard_requirements": ["Python 服务开发"],
                            "skill_tags": ["Python"],
                            "raw_summary": "负责 Python 服务开发。",
                            "uncertain_fields": [],
                        },
                        "source": {"kind": "text", "evidence": ["岗位导入材料"]},
                    },
                )
            ]
        )


class MultiSourceJDRouter:
    """Drive the real action/tool loop through search, fetches, and set_jd."""

    provider = "jd-multi-source-fixture"
    model = "jd-multi-source-fixture"

    def __init__(self, source_urls: list[str], *, declared_sample_count: int | None = None) -> None:
        self.source_urls = source_urls
        self.declared_sample_count = declared_sample_count
        self.calls: list[list[Any]] = []
        self.tool_names: list[str] = []

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        index = len(self.calls) - 1
        if index == 0:
            call = ToolCall(
                id="jd-search-jobs",
                name="search_jobs",
                arguments={"query": "Python 后端工程师", "platform": "boss", "limit": 3},
            )
        elif 1 <= index <= len(self.source_urls):
            source_index = index - 1
            call = ToolCall(
                id=f"jd-fetch-{source_index + 1}",
                name="fetch_url",
                arguments={"url": self.source_urls[source_index]},
            )
        elif index == len(self.source_urls) + 1:
            declared_sample_count = (
                self.declared_sample_count
                if self.declared_sample_count is not None
                else len(self.source_urls)
            )
            call = ToolCall(
                id="jd-set-jd",
                name="set_jd",
                arguments={
                    "jd": {
                        "job_title": "后端工程师",
                        "company": "多来源公司",
                        "hard_requirements": ["Python 服务开发", "三年以上后端经验"],
                        "skill_tags": ["Python", "FastAPI"],
                        "raw_summary": (
                            "共同要求（source-1、source-2、source-3）：Python 服务开发；"
                            "差异：source-1 强调 FastAPI，source-2 强调云服务。"
                        ),
                        "uncertain_fields": ["薪资范围：source-2 未披露"],
                            "sample_target": 5,
                            "sample_count": declared_sample_count,
                            "comparison_status": (
                                "compared" if declared_sample_count >= 3 else "insufficient_samples"
                            ),
                    },
                    "source_urls": self.source_urls,
                    "source": {
                        "kind": "public_source",
                        "evidence": [
                            f"source-{source_index} {url} 成功读取并参与共性/差异核对"
                            for source_index, url in enumerate(self.source_urls, start=1)
                        ],
                    },
                },
            )
        else:
            return LLMResponse(text="已读取多个岗位来源，保存共同要求与来源差异，等待你核对。")
        self.tool_names.append(call.name)
        return LLMResponse(tool_calls=[call])


def test_search_progress_counts_plain_tool_errors_and_rejects_http200_challenge_pages() -> None:
    """Progress must report raw fetch failures without calling challenge HTML a JD."""

    messages = [
        Message(
            role="tool",
            name="search_jobs",
            content=json.dumps({"diagnostics": {"candidate_count": 20}}),
        ),
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps(
                {
                    "url": "https://jobs.example/readable",
                    "status": 200,
                    "content": "真实岗位职责与任职要求",
                },
                ensure_ascii=False,
            ),
        ),
    ]
    messages.extend(
        Message(
            role="tool",
            name="fetch_url",
            content=f"工具结果：执行异常（ToolInputError：HTTP request failed {index}）",
        )
        for index in range(10)
    )
    messages.append(
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps(
                {
                    "url": "https://jobs.example/challenge",
                    "status": 200,
                    "content": "<title>Security Verification</title><p>请稍候</p>",
                },
                ensure_ascii=False,
            ),
        )
    )
    session = AgentSession(id="progress-test", workspace="C:/temporary", messages=messages)
    result = {
        "mode": "search",
        "jd": {"sample_count": 1, "comparison_status": "insufficient_samples"},
    }

    progress = _jd_search_progress(session=session, result=result, status="completed")

    assert progress["candidate_count"] == 20
    assert progress["readable_count"] == 1
    assert progress["fetched_count"] == 1
    assert progress["failed_count"] == 11
    assert progress["phase"] == "insufficient"


def test_search_progress_counts_expired_job_pages_as_failed_fetches() -> None:
    """Expired HTTP-200 recruitment pages are not readable JD samples."""

    messages = [
        Message(
            role="tool",
            name="search_jobs",
            content=json.dumps({"diagnostics": {"candidate_count": 20}}),
        ),
    ]
    messages.extend(
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps(
                {
                    "url": f"https://www.liepin.com/job/expired-{index}",
                    "status": 200,
                    "content": "该职位已暂停招聘",
                },
                ensure_ascii=False,
            ),
        )
        for index in range(5)
    )
    session = AgentSession(id="progress-expired-test", workspace="C:/temporary", messages=messages)

    progress = _jd_search_progress(
        session=session,
        result={"mode": "search", "jd": {"sample_count": 0}},
        status="completed",
    )

    assert progress["candidate_count"] == 20
    assert progress["readable_count"] == 0
    assert progress["fetched_count"] == 0
    assert progress["failed_count"] == 5
    assert progress["phase"] == "insufficient"


def test_search_progress_uses_observed_tool_counts_over_model_comparison_counts() -> None:
    """A final model comparison must not inflate the observed search/fetch counters."""

    candidates = [f"https://jobs.example/role-{index}" for index in range(20)]
    messages = [
        Message(
            role="tool",
            name="search_jobs",
            content=json.dumps(
                {
                    "diagnostics": {"candidate_count": 20},
                    "results": [{"url": url} for url in candidates],
                },
                ensure_ascii=False,
            ),
        )
    ]
    messages.extend(
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps(
                {
                    "url": candidates[index],
                    "status": 200,
                    "content": f"岗位原文 {index}",
                },
                ensure_ascii=False,
            ),
        )
        for index in range(3)
    )
    messages.extend(
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps(
                {"url": candidates[index], "ok": False, "error": "页面不可读取"},
                ensure_ascii=False,
            ),
        )
        for index in range(3, 15)
    )
    session = AgentSession(
        id="progress-model-count-conflict",
        workspace="C:/temporary",
        messages=messages,
    )

    progress = _jd_search_progress(
        session=session,
        result={
            "mode": "search",
            "comparison": {
                "candidate_count": 25,
                "readable_fetches": 13,
                "failed_count": 0,
            },
            "jd": {"sample_count": 3, "comparison_status": "compared"},
        },
        status="completed",
    )

    assert progress["candidate_count"] == 20
    assert progress["readable_count"] == 3
    assert progress["fetched_count"] == 3
    assert progress["failed_count"] == 12
    assert "已找到 20 个候选来源" in progress["message"]
    assert "已读取独立 JD 3 份" in progress["message"]


def test_search_progress_ignores_prior_search_messages() -> None:
    prior = [
        Message(
            role="tool",
            name="search_jobs",
            content=json.dumps({"diagnostics": {"candidate_count": 20}}),
        ),
        Message(
            role="tool",
            name="fetch_url",
            content=json.dumps({"url": "https://jobs.example/old", "status": 200, "content": "old JD"}),
        ),
    ]
    current_start = len(prior)
    messages = [
        *prior,
        Message(
            role="tool",
            name="search_jobs",
            content=json.dumps({"diagnostics": {"candidate_count": 0}, "results": []}),
        ),
    ]
    session = AgentSession(id="progress-scope-test", workspace="C:/temporary", messages=messages)

    progress = _jd_search_progress(
        session=session,
        result={"mode": "search", "jd": {"sample_count": 0}},
        status="completed",
        message_start=current_start,
    )

    assert progress["candidate_count"] == 0
    assert progress["readable_count"] == 0
    assert progress["fetched_count"] == 0
    assert progress["failed_count"] == 0
    assert progress["phase"] == "failed"


def test_search_progress_running_zero_records_stays_searching() -> None:
    """An empty intermediate engine response is not a terminal no-results state."""

    session = AgentSession(
        id="progress-running-empty",
        workspace="C:/temporary",
        messages=[
            Message(
                role="tool",
                name="search_jobs",
                content=json.dumps(
                    {
                        "ok": False,
                        "message": "first search engine unavailable",
                        "diagnostics": {"candidate_count": 0, "status": "failed"},
                    },
                    ensure_ascii=False,
                ),
            )
        ],
    )

    progress = _jd_search_progress(session=session, status="running")

    assert progress["status"] == "running"
    assert progress["phase"] == "searching"
    assert "未找到" not in progress["message"]


def test_search_progress_running_failed_first_engine_can_continue() -> None:
    """A failed search record plus a readable page remains an active synthesis."""

    session = AgentSession(
        id="progress-running-fallback",
        workspace="C:/temporary",
        messages=[
            Message(
                role="tool",
                name="search_jobs",
                content=json.dumps(
                    {
                        "ok": False,
                        "diagnostics": {"candidate_count": 0, "status": "failed"},
                    },
                    ensure_ascii=False,
                ),
            ),
            Message(
                role="tool",
                name="fetch_url",
                content=json.dumps(
                    {
                        "url": "https://jobs.example/fallback",
                        "status": 200,
                        "content": "岗位职责与任职要求",
                    },
                    ensure_ascii=False,
                ),
            ),
        ],
    )

    progress = _jd_search_progress(session=session, status="running")

    assert progress["status"] == "running"
    assert progress["phase"] == "reading"
    assert "任务失败" not in progress["message"]


def test_search_progress_terminal_zero_records_reports_final_failure() -> None:
    session = AgentSession(
        id="progress-terminal-empty",
        workspace="C:/temporary",
        messages=[
            Message(
                role="tool",
                name="search_jobs",
                content=json.dumps(
                    {"ok": True, "diagnostics": {"candidate_count": 0}, "results": []},
                    ensure_ascii=False,
                ),
            )
        ],
    )

    progress = _jd_search_progress(session=session, status="completed")

    assert progress["status"] == "completed"
    assert progress["phase"] == "failed"
    assert "已结束" in progress["message"]


def test_search_progress_failure_message_does_not_leak_exception_or_model_text() -> None:
    progress = _jd_search_progress(
        status="failed",
        error="ValueError: provider failed\n# raw model reply\nlong internal traceback",
    )

    assert progress["phase"] == "failed"
    assert "岗位搜索与页面核验未能完成" in progress["message"]
    assert "ValueError" not in progress["message"]
    assert "raw model reply" not in progress["message"]
    assert "traceback" not in progress["message"]


def test_search_progress_terminal_explicit_insufficient_three_samples_is_accurate() -> None:
    session = AgentSession(
        id="progress-terminal-heterogeneous",
        workspace="C:/temporary",
        messages=[
            Message(
                role="tool",
                name="search_jobs",
                content=json.dumps(
                    {"diagnostics": {"candidate_count": 3}},
                    ensure_ascii=False,
                ),
            ),
            *[
                Message(
                    role="tool",
                    name="fetch_url",
                    content=json.dumps(
                        {
                            "url": f"https://jobs.example/heterogeneous-{index}",
                            "status": 200,
                            "content": "岗位正文",
                        },
                        ensure_ascii=False,
                    ),
                )
                for index in range(1, 4)
            ],
        ],
    )

    progress = _jd_search_progress(
        session=session,
        result={
            "mode": "search",
            "jd": {"sample_count": 3, "comparison_status": "insufficient_samples"},
        },
        status="completed",
    )

    assert progress["phase"] == "insufficient"
    assert "样本未形成可靠共性" in progress["message"]
    assert "可读样本不足 3 份" not in progress["message"]


def request(
    service: Any,
    server: Any,
    method: str,
    path: str,
    body: Any = None,
    *,
    authorization: str | None = None,
):
    headers = {
        "Host": f"127.0.0.1:{server.server_address[1]}",
        "Authorization": authorization or f"Bearer {service.token}",
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
    raise AssertionError("JD import fixture job did not finish")


def _service(tmp_path: Path, router: Any | None = None):
    service = build_service(tmp_path, demo=True)
    router = router or CaptureJDRouter()
    service.engine.router = router
    session = service.engine.new_session()
    job_radar.ensure_state(session)
    service.engine.store.save(session)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return service, router, server, thread, session.id


def test_jd_import_source_search_and_image_forward_user_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, router, server, thread, session_id = _service(tmp_path)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    (tmp_path / "jd.png").write_bytes(image)
    (tmp_path / "jd-2.png").write_bytes(image)

    def fake_search_jobs(query: str, *, platform: str = "web", limit: int = 5) -> dict[str, Any]:
        assert query == "Python 后端工程师"
        assert platform == "web"
        assert limit == 5
        return {
            "ok": True,
            "platform": "web",
            "results": [
                {
                    "platform": "全网公开网页",
                    "url": router.search_url,
                    "title": "Python 后端工程师招聘",
                    "text": "公开岗位要求",
                    "method": "fixture",
                    "result_kind": "job_detail",
                }
            ],
            "partial": True,
        }

    monkeypatch.setattr(integrations, "search_jobs", fake_search_jobs)
    fetch_definition = service.engine.registry._tools["fetch_url"]

    def fake_fetch(args: dict[str, Any], _ctx: Any) -> ToolOutcome:
        url = str(args["url"])
        assert url == router.search_url
        return ToolOutcome(
            content=json.dumps(
                {"url": url, "status": 200, "content": "公开岗位详情：Python 服务开发。"},
                ensure_ascii=False,
            ),
            data={"url": url, "status": 200, "content": "公开岗位详情：Python 服务开发。"},
        )

    fetch_definition.handler = fake_fetch
    try:
        requests = [
            {
                "mode": "source",
                "source_url": "https://www.zhipin.com/job_detail/fixture",
                "source_urls": ["https://www.liepin.com/job/fixture-2"],
                "source_text": "岗位正文：负责 Python 服务开发和上线。",
                "platform": "boss",
                "query": "",
                "image_note": "",
                "attachments": [],
            },
            {
                "mode": "search",
                "source_url": "",
                "source_text": "",
                "platform": "liepin",
                "query": "Python 后端工程师",
                "image_note": "",
                "attachments": [],
            },
            {
                "mode": "image",
                "source_url": "",
                "source_text": "",
                "platform": "boss",
                "query": "",
                "image_note": "这是目标岗位截图",
                "attachments": ["jd.png", "jd-2.png"],
            },
        ]
        for payload in requests:
            status, accepted = request(
                service,
                server,
                "POST",
                f"/api/sessions/{session_id}/jd/import",
                payload,
            )
            assert status == 202, accepted
            assert accepted["status"] == "queued"
            terminal = wait_job(service, server, accepted["job_id"])
            assert terminal["status"] == "completed", terminal
            result = terminal["result"]
            assert result["status"] == "completed"
            assert result["jd"]["job_title"] == "后端工程师"
            assert result["confirmed"] is False
            assert "instruction" not in json.dumps(result, ensure_ascii=False)
            if payload["mode"] == "source":
                assert accepted["result"]["metadata"]["source_urls"] == [
                    "https://www.zhipin.com/job_detail/fixture",
                    "https://www.liepin.com/job/fixture-2",
                ]
                assert accepted["result"]["metadata"]["source_url_count"] == 2
            if payload["mode"] == "image":
                assert accepted["result"]["metadata"]["attachment_count"] == 2

        # The exact material is ordinary user content. It is visible to the
        # provider, while the public result exposes only the short display
        # label and never the fixed runtime prompt.
        user_messages = [
            message
            for call in router.calls
            for message in call
            if message.role == "user"
        ]
        system_messages = [
            message
            for call in router.calls
            for message in call
            if message.role == "system" and isinstance(message.content, str)
        ]
        assert any(
            "source/image 模式按实际正文或图片保留单份材料中的要求" in message.content
            and "不套用 search 的至少两个样本门槛" in message.content
            for message in system_messages
        )
        assert any(
            "最多调用 search_jobs 3 次（首轮加最多两次补搜）" in message.content
            and "整个任务累计最多尝试 30 个页面" in message.content
            and "不能只凭标题不同排除" in message.content
            for message in system_messages
        )
        assert any(
            isinstance(message.content, str)
            and "岗位正文：负责 Python 服务开发" in message.content
            and "https://www.zhipin.com/job_detail/fixture" in message.content
            and "https://www.liepin.com/job/fixture-2" in message.content
            for message in user_messages
        )
        assert any(
            isinstance(message.content, str)
            and "公开搜索平台：liepin" in message.content
            and "Python 后端工程师" in message.content
            for message in user_messages
        )
        image_messages = [message for message in user_messages if isinstance(message.content, list)]
        assert image_messages
        assert any(
            sum(block.type == "image" for block in message.content) == 2
            for message in image_messages
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jd_import_search_fetches_multiple_sources_and_persists_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_urls = [
        "https://www.zhipin.com/job_detail/multi-source-1",
        "https://www.liepin.com/job/multi-source-2",
        "https://www.zhaopin.com/job/multi-source-3",
    ]
    router = MultiSourceJDRouter(source_urls)
    service, _router, server, thread, session_id = _service(tmp_path, router)
    fetched: list[str] = []

    def fake_search_jobs(query: str, *, platform: str = "boss", limit: int = 6) -> dict[str, Any]:
        assert query == "Python 后端工程师"
        assert platform == "boss"
        assert limit == 3
        return {
            "query": query,
            "platform": platform,
            "results": [
                {"url": url, "title": f"{query}（来源 {index}）", "text": "公开索引摘要"}
                for index, url in enumerate(source_urls, start=1)
            ],
        }

    monkeypatch.setattr(integrations, "search_jobs", fake_search_jobs)

    # Keep the real registry/engine/tool loop, but make each public fetch
    # deterministic so this test never relies on external recruitment sites.
    fetch_definition = service.engine.registry._tools["fetch_url"]

    def fake_fetch(args: dict[str, Any], _ctx: Any) -> ToolOutcome:
        url = str(args["url"])
        fetched.append(url)
        return ToolOutcome(
            content=json.dumps(
                {
                    "url": url,
                    "content": f"{url}：Python 服务开发；岗位来源原文。",
                },
                ensure_ascii=False,
            ),
            data={"url": url, "content": f"Python 服务开发（{url}）"},
        )

    fetch_definition.handler = fake_fetch
    try:
        status, accepted = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            {
                "mode": "search",
                "platform": "boss",
                "query": "Python 后端工程师",
            },
        )
        assert status == 202, accepted
        assert accepted["result"]["metadata"]["max_steps"] == 36
        assert accepted["result"]["metadata"]["search_round_limit"] == 3
        assert accepted["result"]["metadata"]["fetch_attempt_limit"] == 30
        assert accepted["result"]["metadata"]["fetch_batch_limit"] == 5
        terminal = wait_job(service, server, accepted["job_id"])
        assert terminal["status"] == "completed", terminal
        assert router.tool_names == ["search_jobs", "fetch_url", "fetch_url", "fetch_url", "set_jd"]
        assert fetched == source_urls

        result = terminal["result"]
        assert result["jd"]["hard_requirements"] == ["Python 服务开发", "三年以上后端经验"]
        assert "共同要求" in result["jd"]["raw_summary"]
        assert "source-2" in result["jd"]["raw_summary"]
        assert result["jd"]["uncertain_fields"] == ["薪资范围：source-2 未披露"]
        assert result["source"]["source_urls"] == source_urls
        assert result["comparison"]["status"] == "compared"
        assert result["comparison"]["sample_count"] == 3
        assert result["comparison"]["readable_fetches"] == 3
        assert result["comparison"]["readable_upper_bound"] == 3

        state = job_radar.workflow_state(service.engine.store.get(session_id))
        assert state["jd_source"]["source_urls"] == source_urls
        assert "source-1" in state["jd"]["raw_summary"]
        assert "source-2" in state["jd"]["uncertain_fields"][0]
        raw_state = service.engine.store.get(session_id).skill_state["job-radar"]
        assert raw_state["jd_history"][-1]["source"]["source_urls"] == source_urls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jd_import_search_marks_insufficient_readable_samples_instead_of_single_jd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_urls = [
        "https://www.zhipin.com/job_detail/short-1",
        "https://www.liepin.com/job/short-2",
    ]
    router = MultiSourceJDRouter(source_urls)
    service, _router, server, thread, session_id = _service(tmp_path, router)

    def fake_search_jobs(query: str, *, platform: str = "boss", limit: int = 6) -> dict[str, Any]:
        return {
            "query": query,
            "platform": platform,
            "results": [
                {"url": url, "title": f"{query}（来源 {index}）", "text": "公开索引摘要"}
                for index, url in enumerate(source_urls, start=1)
            ],
        }

    monkeypatch.setattr(integrations, "search_jobs", fake_search_jobs)
    fetch_definition = service.engine.registry._tools["fetch_url"]

    def fake_fetch(args: dict[str, Any], _ctx: Any) -> ToolOutcome:
        url = str(args["url"])
        return ToolOutcome(
            content=json.dumps({"url": url, "content": f"{url}：Python 服务开发；岗位来源原文。"}, ensure_ascii=False),
            data={"url": url, "content": f"Python 服务开发（{url}）"},
        )

    fetch_definition.handler = fake_fetch
    try:
        status, accepted = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            {"mode": "search", "platform": "boss", "query": "Python 后端工程师"},
        )
        assert status == 202, accepted
        terminal = wait_job(service, server, accepted["job_id"])
        assert terminal["status"] == "completed", terminal
        result = terminal["result"]
        assert result["jd"]["sample_target"] == 5
        assert result["jd"]["sample_count"] == 2
        assert result["jd"]["comparison_status"] == "insufficient_samples"
        assert result["jd"]["hard_requirements"] == []
        assert "暂不足形成共性" in result["jd"]["raw_summary"]
        assert result["comparison"] == {
            "mode": "search",
            "status": "insufficient_samples",
            "sample_target": 5,
            "sample_count": 2,
            "readable_samples": 2,
            "readable_fetches": 2,
            "readable_upper_bound": 2,
            "minimum_samples": 3,
            "evidence_complete": False,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jd_import_search_does_not_promote_fetches_to_declared_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_urls = [
        "https://www.zhipin.com/job_detail/declared-one",
        "https://www.liepin.com/job/declared-two",
        "https://www.zhaopin.com/job/declared-three",
    ]
    # All three pages are readable, while the model only says that one page is
    # a matching JD.  The action must preserve that conservative declaration.
    router = MultiSourceJDRouter(source_urls, declared_sample_count=1)
    service, _router, server, thread, session_id = _service(tmp_path, router)

    def fake_search_jobs(query: str, *, platform: str = "boss", limit: int = 6) -> dict[str, Any]:
        return {
            "query": query,
            "platform": platform,
            "results": [
                {"url": url, "title": f"{query}（来源 {index}）", "text": "公开索引摘要"}
                for index, url in enumerate(source_urls, start=1)
            ],
        }

    monkeypatch.setattr(integrations, "search_jobs", fake_search_jobs)
    fetch_definition = service.engine.registry._tools["fetch_url"]

    def fake_fetch(args: dict[str, Any], _ctx: Any) -> ToolOutcome:
        url = str(args["url"])
        return ToolOutcome(
            content=json.dumps({"url": url, "content": f"{url}：岗位来源原文。"}, ensure_ascii=False),
            data={"url": url, "content": f"岗位原文（{url}）"},
        )

    fetch_definition.handler = fake_fetch
    try:
        status, accepted = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            {"mode": "search", "platform": "boss", "query": "Python 后端工程师"},
        )
        assert status == 202, accepted
        terminal = wait_job(service, server, accepted["job_id"])
        assert terminal["status"] == "completed", terminal
        result = terminal["result"]
        assert result["jd"]["sample_count"] == 1
        assert result["jd"]["comparison_status"] == "insufficient_samples"
        assert result["jd"]["hard_requirements"] == []
        assert result["comparison"] == {
            "mode": "search",
            "status": "insufficient_samples",
            "sample_target": 5,
            "sample_count": 1,
            "readable_samples": 1,
            "readable_fetches": 3,
            "readable_upper_bound": 3,
            "minimum_samples": 3,
            "evidence_complete": False,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "unknown"},
        {"mode": "source"},
        {"mode": "source", "source_url": "http://www.zhipin.com/job_detail/1"},
        {"mode": "source", "source_urls": ["http://www.zhipin.com/job_detail/1"]},
        {"mode": "source", "source_urls": ["https://example.com/job/1"]},
        {"mode": "source", "source_urls": {"url": "https://www.zhipin.com/job_detail/1"}},
        {
            "mode": "source",
            "source_urls": [f"https://www.zhipin.com/job_detail/{index}" for index in range(11)],
        },
        {"mode": "search", "platform": "boss"},
        {"mode": "search", "platform": "boss", "query": "后端工程师\n算法工程师"},
        {"mode": "search", "platform": "unsupported", "query": "后端"},
        {"mode": "image", "image_note": "没有附件"},
        {"mode": "source", "source_text": "ok", "unexpected": True},
    ],
)
def test_jd_import_validates_mode_fields_before_claiming_job(tmp_path: Path, payload: dict[str, Any]) -> None:
    service, _router, server, thread, session_id = _service(tmp_path)
    try:
        status, response = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            payload,
        )
        assert status == 400, response
        assert "job_id" not in response
        assert not service._busy
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jd_import_enforces_auth_and_busy_gate(tmp_path: Path) -> None:
    service, _router, server, thread, session_id = _service(tmp_path)
    payload = {"mode": "search", "platform": "boss", "query": "后端工程师"}
    try:
        status, response = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            payload,
            authorization="Bearer wrong",
        )
        assert status == 401
        assert "job_id" not in response

        service._busy[session_id] = "existing-job"
        status, response = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            payload,
        )
        assert status == 409
        assert "job_id" not in response
    finally:
        service._busy.pop(session_id, None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("blocker", ["pending", "queued", "uncertain", "running"])
def test_jd_import_preflights_existing_turn_before_claiming_job(
    tmp_path: Path,
    blocker: str,
) -> None:
    service, _router, server, thread, session_id = _service(tmp_path)
    payload = {"mode": "search", "platform": "boss", "query": "后端工程师"}
    try:
        session = service.engine.store.get(session_id)
        if blocker == "pending":
            session.pending = PendingApproval(
                id="old-approval",
                tool_name="fixture_write",
                summary="旧主会话审批",
                preview="fixture",
                metadata={"scope": "main"},
            )
        elif blocker == "queued":
            session.queued_calls = [ToolCall(id="old-queued", name="fixture_read", arguments={})]
        else:
            session.execution = {"status": blocker, "kind": "fixture"}
        service.engine.store.save(session)
        before_jobs = set(service._jobs)

        status, response = request(
            service,
            server,
            "POST",
            f"/api/sessions/{session_id}/jd/import",
            payload,
        )

        assert status == 409, response
        assert "job_id" not in response
        assert not service._busy
        assert set(service._jobs) == before_jobs
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jd_import_preflight_store_lock_order_does_not_deadlock_with_practice_snapshot(
    tmp_path: Path,
) -> None:
    """The preflight and scoped snapshot paths acquire Store before service lock."""

    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    state = job_radar.ensure_state(session)
    state["selected_project"] = {"project_key": "repo:fixture", "full_name": "example/fixture"}
    service.engine.store.save(session)

    original_store_lock = service.engine.store.lock
    original_snapshot = service.practice_snapshot
    snapshot_entered = threading.Event()
    release_snapshot = threading.Event()
    store_attempted = threading.Event()
    import_thread_id: dict[str, int] = {}
    errors: list[tuple[str, BaseException]] = []

    def instrumented_store_lock(session_id: str, *args: Any, **kwargs: Any):
        if threading.get_ident() == import_thread_id.get("id"):
            store_attempted.set()
        return original_store_lock(session_id, *args, **kwargs)

    def blocked_snapshot(session_id: str, thread_id: str) -> dict[str, Any]:
        snapshot_entered.set()
        if not release_snapshot.wait(timeout=3):
            raise AssertionError("practice snapshot was not released")
        return original_snapshot(session_id, thread_id)

    service.engine.store.lock = instrumented_store_lock
    service.practice_snapshot = blocked_snapshot

    def run_open_practice() -> None:
        try:
            service.open_practice(session.id, {})
        except BaseException as exc:  # report worker failures after both joins
            errors.append(("practice", exc))

    def run_import() -> None:
        import_thread_id["id"] = threading.get_ident()
        try:
            service.import_jd(
                session.id,
                {"mode": "search", "platform": "boss", "query": "后端工程师"},
            )
        except BaseException as exc:  # report worker failures after both joins
            errors.append(("import", exc))

    practice_thread = threading.Thread(target=run_open_practice, daemon=True)
    practice_thread.start()
    assert snapshot_entered.wait(timeout=3)

    import_thread = threading.Thread(target=run_import, daemon=True)
    import_thread.start()
    # This event fires before the import thread blocks on the Store lock.  With
    # the old service-lock-first order, it would hold self._lock here while the
    # practice thread waits for that same lock inside practice_snapshot.
    assert store_attempted.wait(timeout=3)
    release_snapshot.set()
    practice_thread.join(timeout=3)
    import_thread.join(timeout=3)
    try:
        assert not practice_thread.is_alive()
        assert not import_thread.is_alive()
        assert not errors
        for _ in range(300):
            with service._lock:
                busy = session.id in service._busy
            if not busy:
                break
            time.sleep(0.005)
        else:
            raise AssertionError("JD import worker did not finish")
    finally:
        service.practice_snapshot = original_snapshot
        service.engine.store.lock = original_store_lock
