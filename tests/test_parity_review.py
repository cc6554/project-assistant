"""Independent parity checks for the product workflow/context boundary.

These tests intentionally exercise the public WebService mutation APIs and the
model-context bridge together.  They are kept separate from the broader web
suite so a later product change can distinguish a parity regression from a
transport or rendering failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_coach import job_radar
from agent_coach.legacy.core.schema import LLMResponse, Message
from agent_coach.legacy.domain.schemas import JDCard, SkillItem, UserSkillProfile
from agent_coach.web import build_service, _message_payload


def _profile(*, questions: list[str]) -> dict[str, Any]:
    return UserSkillProfile(
        summary="完成过 Python 服务",
        target_direction="后端开发",
        skills=[
            SkillItem(
                name="Python",
                proficiency="working",
                confidence=0.8,
                sources=["self_report"],
                evidence="独立实现过 HTTP 服务",
            )
        ],
        open_questions=questions,
    ).model_dump(mode="json")


def _jd(title: str = "后端工程师") -> dict[str, Any]:
    return JDCard(
        job_title=title,
        company="Example Labs",
        hard_requirements=["Python"],
        skill_tags=["Python"],
        raw_summary="岗位正文",
    ).model_dump(mode="json")


class _CaptureRouter:
    def __init__(self) -> None:
        self.messages: list[list[Any]] = []

    def complete(self, _task: str, messages: list[Any], **_kwargs: Any) -> LLMResponse:
        self.messages.append([message.model_copy(deep=True) for message in messages])
        return LLMResponse(text="已读取当前工件")


def test_profile_clarification_and_router_context_use_current_state(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    router = _CaptureRouter()
    service.engine.router = router
    session_id = str(service.create_session()["id"])

    service.update_profile(session_id, {"profile": _profile(questions=["是否有上线证据？"])})
    service.update_profile(session_id, {"profile": _profile(questions=[])})
    service.update_jd(session_id, {"jd": _jd(), "confirmed": True})

    state = job_radar.workflow_state(service.engine.store.get(session_id))
    assert state["clarification"]["status"] == "completed"
    assert state["clarification"]["open_questions"] == []
    assert state["clarification"]["answered"] == ["是否有上线证据？"]

    result = service.engine.run("请继续", session_id=session_id)
    assert result.status == "completed"
    system = router.messages[-1][0]
    assert system.role == "system"
    assert "后端工程师" in system.content
    prefix = "<job-radar-workflow-state>"
    suffix = "</job-radar-workflow-state>"
    snapshot = json.loads(system.content.split(prefix, 1)[1].split(suffix, 1)[0])
    assert snapshot["profile"]["open_questions"] == []
    assert snapshot["clarification"]["status"] == "completed"
    assert snapshot["clarification"]["answered"] == ["是否有上线证据？"]


def test_profile_clone_drops_job_transcript_and_derived_context(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    source_id = str(service.create_session()["id"])
    source = service.engine.store.get(source_id)
    source.skill_state["job-radar"] = {
        "profile": _profile(questions=[]),
        "profile_source": {"kind": "self_report", "evidence": ["用户输入"]},
        "clarification": {"status": "completed", "open_questions": [], "answered": []},
        "jd": _jd("旧岗位"),
        "plan": {"direction": "旧方向"},
        "repos": [{"full_name": "old/repo"}],
        "selected_project": {"full_name": "old/repo"},
        "progress": [{"note": "旧进度"}],
        "history": [{"type": "assistant", "secret": "old transcript"}],
    }
    source.messages.append(Message(role="user", content="旧岗位聊天"))
    service.engine.store.save(source)

    cloned_id = str(service.create_session(source_id)["id"])
    cloned = service.engine.store.get(cloned_id)
    state = job_radar.workflow_state(cloned)
    context = job_radar.model_context(cloned)

    assert state["profile"] == source.skill_state["job-radar"]["profile"]
    assert state["jd"] is None
    assert state["plan"] is None
    assert state["repos"] == []
    assert state["selected_project"] is None
    assert state["progress"] == []
    assert cloned.messages == []
    assert "旧岗位" not in json.dumps(context, ensure_ascii=False)
    assert "old/repo" not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize("execution", [{"status": "uncertain"}, {"status": "running"}])
def test_uncertain_or_running_session_rejects_artifact_mutation_atomically(
    tmp_path: Path, execution: dict[str, str]
):
    service = build_service(tmp_path, demo=True)
    session_id = str(service.create_session()["id"])
    session = service.engine.ensure_workflow(session_id)
    session.execution = execution
    service.engine.store.save(session)
    before = service.engine.store.get(session_id).model_dump(mode="json")

    with pytest.raises(RuntimeError):
        service.update_jd(session_id, {"jd": _jd(), "confirmed": True})

    after = service.engine.store.get(session_id).model_dump(mode="json")
    assert after == before


def test_model_context_excludes_sources_history_and_credentials(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    session_id = str(service.create_session()["id"])
    session = service.engine.ensure_workflow(session_id)
    state = session.skill_state["job-radar"]
    state["profile_source"] = {"token": "ghp-source-secret"}
    state["jd_source"] = {"api_key": "jd-source-secret"}
    state["history"] = [{"content": "history-secret", "access_token": "history-token"}]
    service.engine.store.save(session)

    context = job_radar.model_context(service.engine.store.get(session_id))
    encoded = json.dumps(context, ensure_ascii=False)
    assert "ghp-source-secret" not in encoded
    assert "jd-source-secret" not in encoded
    assert "history-secret" not in encoded
    assert "history-token" not in encoded


def test_message_payload_drops_reasoning_provenance_from_user_surface():
    """The UI serializer must hide the new internal provider marker too."""

    payload = _message_payload(
        Message(
            role="assistant",
            content="visible answer",
            reasoning_content="private trace",
            reasoning_provider="deepseek",
        )
    )
    assert "reasoning_content" not in payload
    assert "reasoning_provider" not in payload


def test_copy_profile_requires_existing_profile_without_creating_empty_session(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    source_id = str(service.create_session()["id"])
    before_ids = {row["id"] for row in service.session_summaries()}
    assert service.session_summaries()[0]["has_profile"] is False
    with pytest.raises(ValueError, match="尚未建立个人档案"):
        service.create_session(source_id)
    assert {row["id"] for row in service.session_summaries()} == before_ids
    service.update_profile(source_id, {"profile": _profile(questions=[])})
    assert service.session_summaries()[0]["has_profile"] is True
    clone_id = str(service.create_session(source_id)["id"])
    clone = service.engine.store.get(clone_id)
    clone.skill_state["job-radar"]["profile"]["summary"] = "新会话独立修改"
    service.engine.store.save(clone)
    assert service.engine.store.get(source_id).skill_state["job-radar"]["profile"]["summary"] != "新会话独立修改"
