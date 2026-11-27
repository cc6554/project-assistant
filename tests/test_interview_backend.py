"""Regression tests for the dedicated skills interview scope."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from agent_coach import job_radar
from agent_coach.contracts import PendingApproval, ToolDefinition, ToolOutcome
from agent_coach.engine import AgentEngine
from agent_coach.legacy.core.schema import LLMResponse, ToolCall, ToolSpec
from agent_coach.web import INTERVIEW_RUNTIME_PROMPT, build_service


class TextRouter:
    provider = "interview-fixture"
    model = "interview-fixture"

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts) or ["下一个问题？"]
        self.calls: list[list[Any]] = []

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        self.calls.append([item.model_copy(deep=True) for item in messages])
        return LLMResponse(text=self.texts[min(len(self.calls) - 1, len(self.texts) - 1)])


def _seed_context(service: Any, session_id: str) -> None:
    with service.engine.store.lock(session_id):
        session = service.engine.store.get(session_id)
        state = job_radar.ensure_state(session)
        state["jd"] = {
            "job_title": "后端工程师",
            "hard_requirements": ["Python 服务部署"],
            "skill_tags": ["Python"],
        }
        state["profile"] = {
            "user_id": "default",
            "summary": "有 Python 项目经验",
            "target_direction": "后端开发",
            "skills": [],
            "open_questions": ["是否有上线证据？"],
            "updated_at": "2026-01-01T00:00:00",
        }
        state["clarification"] = {
            "status": "active",
            "open_questions": ["是否有上线证据？"],
            "answered": [],
            "updated_at": None,
        }
        service.engine.store.save(session)


def _wait(service: Any, session_id: str) -> None:
    for _ in range(300):
        with service._lock:
            busy = session_id in service._busy
        if not busy:
            return
        time.sleep(0.005)
    raise AssertionError("interview fixture job did not finish")


def test_interview_open_is_idempotent_and_filters_unrelated_history(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    router = TextRouter("请讲一个你负责上线的 Python 服务。", "上线后如何验证稳定性？")
    service.engine.router = router
    session = service.engine.new_session()
    _seed_context(service, session.id)

    # The root transcript retains the main memory, but the interview window
    # gets its own durable transcript and receives the live JD/profile bridge.
    service.engine.run("主聊天里的无关历史", session.id)
    opened = service.open_interview(session.id, {})
    thread_id = opened["interview_thread_id"]
    assert opened["job_id"]
    _wait(service, session.id)
    first = service.interview_snapshot(session.id, thread_id)
    assert first["opening_completed"] is True
    assert [item["role"] for item in first["messages"]] == ["user", "assistant"]
    assert all(item["metadata"]["scope"] == "interview" for item in first["messages"])
    assert "主聊天里的无关历史" not in [item.get("content") for item in first["messages"]]
    assert any(INTERVIEW_RUNTIME_PROMPT in message.content for call in router.calls for message in call if message.role == "system")
    assert any("后端工程师" in message.content and "Python" in message.content for call in router.calls for message in call if message.role == "system")

    reopened = service.open_interview(session.id, {"interview_thread_id": thread_id})
    assert reopened["interview_thread_id"] == thread_id
    assert "job_id" not in reopened
    assert len(reopened["messages"]) == len(first["messages"])

    accepted = service.send_interview_message(session.id, thread_id, {"text": "我负责 Docker 部署和上线后的监控。"})
    assert accepted["interview_thread_id"] == thread_id
    _wait(service, session.id)
    after = service.interview_snapshot(session.id, thread_id)
    assert [item["role"] for item in after["messages"]] == ["user", "assistant", "user", "assistant"]
    assert all(item["metadata"]["interview_thread_id"] == thread_id for item in after["messages"])
    main = service.session_snapshot(session.id)
    assert main["interview_filter"] == {"scope": "interview", "interview_thread_id": thread_id}


def test_interview_answer_updates_profile_through_toolengine(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    _seed_context(service, session.id)
    profile = {
        "user_id": "default",
        "summary": "有 Python 项目经验和上线证据",
        "target_direction": "后端开发",
        "skills": [
            {
                "name": "Python",
                "proficiency": "working",
                "confidence": 0.8,
                "sources": ["chat"],
                "evidence": "在项目中负责 Docker 部署和上线监控。",
            }
        ],
        "open_questions": [],
        "updated_at": "2026-01-01T00:00:00",
    }

    class ProfileRouter(TextRouter):
        def complete(self, task: str, messages: list[Any], **kwargs: Any) -> LLMResponse:
            self.calls.append([item.model_copy(deep=True) for item in messages])
            # Opening asks a question.  The answer turn submits a complete
            # profile through the normal deterministic job-radar tool, then
            # the Agent gets one final response after the tool result.
            if len(self.calls) == 1:
                return LLMResponse(text="请讲一个你负责上线的 Python 服务。")
            if len(self.calls) == 2:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="profile-update",
                            name="set_profile",
                            arguments={
                                "profile": profile,
                                "source": {"kind": "conversation", "evidence": ["当前技能访谈"]},
                            },
                        )
                    ]
                )
            return LLMResponse(text="已记录。你在上线后如何验证稳定性？")

    router = ProfileRouter()
    service.engine.router = router
    opened = service.open_interview(session.id, {})
    _wait(service, session.id)
    accepted = service.send_interview_message(
        session.id,
        opened["interview_thread_id"],
        {"text": "我负责 Docker 部署和上线后的监控。"},
    )
    _wait(service, session.id)
    assert accepted["scope"] == "interview"
    state = job_radar.workflow_state(service.engine.store.get(session.id))
    assert state["profile"]["open_questions"] == []
    assert state["clarification"]["status"] == "completed"
    snapshot = service.interview_snapshot(session.id, opened["interview_thread_id"])
    assert "已记录。" in "\n".join(str(item.get("content") or "") for item in snapshot["messages"])


def test_interview_pending_claim_is_scoped_and_requires_normal_approval(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    _seed_context(service, session.id)

    def prepare(_arguments: dict, _context: Any):
        from agent_coach.contracts import PendingApproval

        return PendingApproval(tool_name="interview_fixture_write", summary="访谈写入", preview="fixture")

    def execute(_pending: Any, _context: Any) -> ToolOutcome:
        return ToolOutcome("访谈写入完成")

    service.engine.registry.register(
        ToolDefinition(
            spec=ToolSpec(name="interview_fixture_write", description="fixture", parameters={"type": "object"}),
            handler=prepare,
            execute=execute,
        )
    )

    class ApprovalRouter(TextRouter):
        def complete(self, task: str, messages: list[Any], **kwargs: Any) -> LLMResponse:
            self.calls.append([item.model_copy(deep=True) for item in messages])
            if not any(message.role == "tool" for message in messages):
                return LLMResponse(tool_calls=[ToolCall(id="interview-write", name="interview_fixture_write", arguments={})])
            return LLMResponse(text="继续追问一个问题？")

    service.engine.router = ApprovalRouter()
    opened = service.open_interview(session.id, {})
    _wait(service, session.id)
    thread_id = opened["interview_thread_id"]
    snapshot = service.interview_snapshot(session.id, thread_id)
    assert snapshot["pending"] is not None
    assert snapshot["pending"]["scope"] == "interview"
    assert snapshot["pending"]["interview_thread_id"] == thread_id
    # A second interview message cannot bypass the pending approval or append
    # a user turn before the saved action is resolved.
    before = len(snapshot["messages"])
    try:
        service.send_interview_message(session.id, thread_id, {"text": "继续"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("pending interview action must block a new message")
    assert len(service.interview_snapshot(session.id, thread_id)["messages"]) == before

    approval_job = service.submit(
        session.id,
        lambda: service._resume_clarification_action(
            session.id,
            lambda: service.engine.approve(session.id, snapshot["pending"]["id"]),
        ),
    )
    _wait(service, session.id)
    approval_result = service.job(approval_job)["result"]
    assert approval_result["metadata"] == {
        "scope": "interview",
        "interview_thread_id": thread_id,
    }
    resumed = service.interview_snapshot(session.id, thread_id)
    assert resumed["pending"] is None
    assert resumed["opening_completed"] is True


def test_interview_open_retries_after_approval_continuation_model_error(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    _seed_context(service, session.id)

    def prepare(_arguments: dict, _context: Any) -> PendingApproval:
        return PendingApproval(
            tool_name="interview_fixture_write",
            summary="访谈写入",
            preview="fixture",
        )

    def execute(_pending: Any, _context: Any) -> ToolOutcome:
        return ToolOutcome("访谈写入完成")

    service.engine.registry.register(
        ToolDefinition(
            spec=ToolSpec(name="interview_fixture_write", description="fixture", parameters={"type": "object"}),
            handler=prepare,
            execute=execute,
        )
    )

    class ApprovalThenModelErrorRouter(TextRouter):
        def complete(self, task: str, messages: list[Any], **kwargs: Any) -> LLMResponse:
            self.calls.append([item.model_copy(deep=True) for item in messages])
            if len(self.calls) == 1:
                return LLMResponse(
                    tool_calls=[ToolCall(id="interview-opening-write", name="interview_fixture_write", arguments={})]
                )
            if len(self.calls) == 2:
                raise RuntimeError("fixture resumed opening model failure")
            return LLMResponse(text="请讲一个你负责上线的服务。")

    service.engine.router = ApprovalThenModelErrorRouter()
    opened = service.open_interview(session.id, {})
    _wait(service, session.id)
    thread_id = opened["interview_thread_id"]
    pending = service.interview_snapshot(session.id, thread_id)["pending"]
    assert pending is not None

    approval_job = service.submit(
        session.id,
        lambda: service._resume_clarification_action(
            session.id,
            lambda: service.engine.approve(session.id, pending["id"]),
        ),
    )
    _wait(service, session.id)
    approval_terminal = service.job(approval_job)
    assert approval_terminal["status"] == "completed"
    approval_result = approval_terminal["result"]
    assert approval_result["status"] == "error"
    assert approval_result["metadata"] == {
        "scope": "interview",
        "interview_thread_id": thread_id,
    }

    failed = service.interview_snapshot(session.id, thread_id)
    assert failed["opening_started"] is False
    assert failed["opening_completed"] is False
    assert failed["execution"]["scope"] == "interview"
    assert failed["execution"]["interview_thread_id"] == thread_id

    retry = service.open_interview(session.id, {"interview_thread_id": thread_id})
    assert retry["job_id"]
    _wait(service, session.id)
    completed = service.interview_snapshot(session.id, thread_id)
    assert completed["opening_started"] is True
    assert completed["opening_completed"] is True


def test_interview_open_respects_existing_root_pending_claim(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    _seed_context(service, session.id)
    with service.engine.store.lock(session.id):
        current = service.engine.store.get(session.id)
        current.pending = PendingApproval(
            tool_name="main_fixture_write",
            summary="主会话待审批",
            preview="fixture",
            metadata={"scope": "main"},
        )
        service.engine.store.save(current)

    with pytest.raises(RuntimeError, match="待处理的审批"):
        service.open_interview(session.id, {})
    assert service.interview_threads(session.id)["interview_threads"] == []


def test_interview_snapshot_hides_other_scope_results_and_busy_threads(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    service.engine.router = TextRouter("请讲一个你负责上线的服务。")
    session = service.engine.new_session()
    _seed_context(service, session.id)
    opened = service.open_interview(session.id, {})
    _wait(service, session.id)
    thread_id = opened["interview_thread_id"]
    other_thread_id = uuid.uuid4().hex
    with service.engine.store.lock(session.id):
        current = service.engine.store.get(session.id)
        current.interview_threads[other_thread_id] = {
            "id": other_thread_id,
            "scope": "interview",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "opening_started": True,
            "opening_completed": False,
            "turn_count": 1,
        }
        service.engine.store.save(current)

    with service._lock:
        service._last_results[session.id] = {
            "status": "completed",
            "metadata": {"scope": "main"},
        }
    assert service.interview_snapshot(session.id, thread_id)["last_result"] is None
    with service._lock:
        service._last_results[session.id] = {
            "status": "completed",
            "metadata": {"scope": "practice", "project_key": "p", "practice_thread_id": "p-thread"},
        }
    assert service.interview_snapshot(session.id, thread_id)["last_result"] is None

    with service.engine.store.lock(session.id):
        current = service.engine.store.get(session.id)
        current.active_turn_metadata = {
            "scope": "interview",
            "interview_thread_id": other_thread_id,
        }
        service.engine.store.save(current)
    with service._lock:
        service._busy[session.id] = "other-thread-job"
    try:
        assert service.interview_snapshot(session.id, thread_id)["interview_busy"] is False
    finally:
        with service._lock:
            service._busy.pop(session.id, None)


def test_interview_open_can_retry_after_async_model_error(tmp_path: Path) -> None:
    service = build_service(tmp_path, demo=True)
    session = service.engine.new_session()
    _seed_context(service, session.id)

    class FlakyRouter(TextRouter):
        def complete(self, task: str, messages: list[Any], **kwargs: Any) -> LLMResponse:
            if not self.calls:
                self.calls.append([item.model_copy(deep=True) for item in messages])
                raise RuntimeError("fixture model failure")
            return super().complete(task, messages, **kwargs)

    service.engine.router = FlakyRouter("请讲一个你负责上线的服务。")
    first = service.open_interview(session.id, {})
    _wait(service, session.id)
    first_snapshot = service.interview_snapshot(session.id, first["interview_thread_id"])
    assert first_snapshot["opening_started"] is False
    assert first_snapshot["opening_completed"] is False

    second = service.open_interview(session.id, {"interview_thread_id": first["interview_thread_id"]})
    assert second["job_id"]
    _wait(service, session.id)
    assert service.interview_snapshot(session.id, first["interview_thread_id"])["opening_completed"] is True
