from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agent_coach.contracts import PendingApproval
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.legacy.domain.schemas import SkillItem, UserSkillProfile
from agent_coach.web import build_service


def _profile(question: str, *, open_questions: list[str] | None = None) -> dict[str, Any]:
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
        open_questions=list(open_questions if open_questions is not None else [question]),
    ).model_dump(mode="json")


def _session(service: Any, question: str) -> str:
    session_id = str(service.create_session()["id"])
    service.update_profile(session_id, {"profile": _profile(question)})
    return session_id


def _wait(service: Any, job_id: str) -> dict[str, Any]:
    for _ in range(400):
        job = service.job(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.005)
    raise AssertionError("job did not finish")


class _IsolatedRouter:
    """A deterministic fake model that can hold one session while another runs."""

    def __init__(self, updates: dict[str, dict[str, Any]], blocked_question: str):
        self.updates = updates
        self.blocked_question = blocked_question
        self.started = threading.Event()
        self.release = threading.Event()
        self.counts: dict[str, int] = {}

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        user = next((str(item.content) for item in reversed(messages) if item.role == "user"), "")
        question = next((item for item in self.updates if item in user), "")
        if question == self.blocked_question and self.counts.get(question, 0) == 0:
            self.started.set()
            assert self.release.wait(5), "blocked fake model was not released"
        count = self.counts.get(question, 0)
        self.counts[question] = count + 1
        if count == 0 and question in self.updates:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id=f"set-profile-{question}",
                        name="set_profile",
                        arguments={
                            "profile": self.updates[question],
                            "source": {"kind": "chat", "evidence": ["deterministic fake router"]},
                        },
                    )
                ]
            )
        return LLMResponse(text=f"已处理 {question}")


class _FailingRouter:
    def complete(self, _task: str, _messages: list[Any], **_kwargs: Any) -> LLMResponse:
        raise RuntimeError("deterministic fake model failure")


def test_two_sessions_keep_late_clarification_results_isolated(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    question_a, question_b = "A 是否有上线证据？", "B 是否有上线证据？"
    session_a = _session(service, question_a)
    session_b = _session(service, question_b)
    router = _IsolatedRouter(
        {
            question_a: _profile(question_a, open_questions=[]),
            question_b: _profile(question_b, open_questions=[]),
        },
        question_a,
    )
    service.engine.router = router

    accepted_a = service.answer_clarification(session_a, {"question": question_a, "answer": "A 的部署日志"})
    assert router.started.wait(2)
    accepted_b = service.answer_clarification(session_b, {"question": question_b, "answer": "B 的监控截图"})
    result_b = _wait(service, accepted_b["job_id"])["result"]
    assert result_b["session_id"] == session_b
    assert result_b["metadata"]["confirmed"] is True
    assert question_a in service.engine.store.get(session_a).skill_state["job-radar"]["profile"]["open_questions"]
    router.release.set()
    result_a = _wait(service, accepted_a["job_id"])["result"]
    assert result_a["session_id"] == session_a
    assert result_a["metadata"]["confirmed"] is True
    saved_a = service.engine.store.get(session_a)
    saved_b = service.engine.store.get(session_b)
    assert saved_a.skill_state["job-radar"]["profile"]["open_questions"] == []
    assert saved_b.skill_state["job-radar"]["profile"]["open_questions"] == []
    assert all("A 的部署日志" not in str(message.content) for message in saved_b.messages)


def test_inner_model_error_is_not_a_completed_clarification(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    question = "是否有上线证据？"
    session_id = _session(service, question)
    service.engine.router = _FailingRouter()

    accepted = service.answer_clarification(session_id, {"question": question, "answer": "暂时没有"})
    job = _wait(service, accepted["job_id"])
    assert job["status"] == "completed"  # worker returned a structured RunResult
    assert job["result"]["status"] == "error"
    assert job["result"]["metadata"]["confirmed"] is False
    assert question in job["result"]["clarification"]["open_questions"]


def test_pending_queue_and_busy_reject_before_claiming_job(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    question = "是否有上线证据？"
    session_id = _session(service, question)
    before = len(service._jobs)
    session = service.engine.store.get(session_id)
    session.pending = PendingApproval(tool_name="write_file", summary="等待审批")
    service.engine.store.save(session)
    with pytest.raises(RuntimeError, match="审批"):
        service.answer_clarification(session_id, {"question": question, "answer": "事实"})
    assert len(service._jobs) == before

    session = service.engine.store.get(session_id)
    session.pending = None
    session.queued_calls = [ToolCall(id="queued", name="write_file", arguments={})]
    service.engine.store.save(session)
    with pytest.raises(RuntimeError, match="队列"):
        service.answer_clarification(session_id, {"question": question, "answer": "事实"})
    assert len(service._jobs) == before

    session.queued_calls = []
    service.engine.store.save(session)
    service._busy[session_id] = "already-running"
    try:
        with pytest.raises(RuntimeError, match="另一个操作"):
            service.answer_clarification(session_id, {"question": question, "answer": "事实"})
        assert len(service._jobs) == before
    finally:
        service._busy.pop(session_id, None)


def test_clarification_review_dom_scenarios() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for clarification review DOM scenarios")
    script = Path(__file__).with_name("clarification_review_dom_regression.js")
    result = subprocess.run(
        [node, str(script)],
        cwd=script.parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr).strip()
