from __future__ import annotations

import pytest

from agent_coach.contracts import PendingApproval, ToolDefinition, ToolOutcome, ToolRegistry
from agent_coach.engine import AgentEngine
from agent_coach.job_radar import ensure_state, workflow_state
from agent_coach.legacy.core.schema import LLMResponse, ToolCall, ToolSpec
from agent_coach.legacy.core.llm.anthropic_adapter import to_anthropic_messages
from agent_coach.legacy.core.llm.openai_compat import to_openai_messages
from agent_coach.store import SessionStore
from agent_coach.web import build_service


class TextRouter:
    def __init__(self, text: str = "已记录"):
        self.text = text
        self.calls: list[list] = []

    def complete(self, task, messages, *, tools=None, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(text=self.text)


def test_practice_metadata_stays_on_transcript_but_never_provider_wire(tmp_path):
    store = SessionStore(tmp_path)
    router = TextRouter()
    engine = AgentEngine(store, ToolRegistry(), router)
    session = engine.new_session()

    practice = engine.run(
        "今天练习",
        session.id,
        message_metadata={
            "scope": "practice",
            "project_key": "repo:a",
            "practice_thread_id": "thread-a",
        },
    )
    assert practice.status == "completed"
    saved = store.get(session.id)
    tagged = [message for message in saved.messages if message.metadata.get("scope") == "practice"]
    assert [message.role for message in tagged] == ["user", "assistant"]
    assert all(message.metadata["project_key"] == "repo:a" for message in tagged)
    # The same session/model call still contains the ordinary main/system
    # context and metadata is not serialized into provider payloads.
    assert any(message.role == "system" for message in router.calls[-1])
    assert all("metadata" not in wire for wire in to_openai_messages(saved.messages))
    _system, anthropic_wire = to_anthropic_messages(saved.messages)
    assert all("metadata" not in wire for wire in anthropic_wire)

    main = engine.run("主对话继续", session.id)
    assert main.status == "completed"
    saved = store.get(session.id)
    assert saved.messages[-2].metadata == {"scope": "main"}
    assert saved.messages[-1].metadata == {"scope": "main"}
    assert saved.last_turn_metadata == {"scope": "main"}


def test_practice_pending_approval_keeps_scope_across_restart_and_approve(tmp_path):
    order: list[str] = []
    registry = ToolRegistry()

    def prepare(arguments, context):
        order.append("prepare")
        return PendingApproval(tool_name="write_file", summary="确认练习写入", preview="练习文件")

    def execute(pending, context):
        order.append("execute")
        return ToolOutcome("练习写入完成")

    registry.register(
        ToolDefinition(
            spec=ToolSpec(name="write_file", description="write", parameters={"type": "object"}),
            handler=prepare,
            execute=execute,
        )
    )

    class Router:
        def __init__(self):
            self.turn = 0

        def complete(self, task, messages, *, tools=None, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return LLMResponse(tool_calls=[ToolCall(id="practice-write", name="write_file", arguments={})])
            return LLMResponse(text="继续完成")

    store = SessionStore(tmp_path)
    router = Router()
    engine = AgentEngine(store, registry, router)
    session = engine.new_session()
    first = engine.run(
        "开始练习",
        session.id,
        message_metadata={
            "scope": "practice",
            "project_key": "repo:a",
            "practice_thread_id": "thread-a",
        },
    )
    assert first.status == "pending_approval"
    assert first.pending is not None
    assert first.pending.metadata == {
        "scope": "practice",
        "project_key": "repo:a",
        "practice_thread_id": "thread-a",
    }
    restored = AgentEngine(store, registry, router)
    finished = restored.approve(session.id, first.pending.id)
    assert finished.status == "completed"
    assert order == ["prepare", "execute"]
    saved = store.get(session.id)
    scoped = [message for message in saved.messages if message.role in {"user", "assistant", "tool"}]
    assert all(message.metadata.get("scope") == "practice" for message in scoped)
    assert all(message.metadata.get("practice_thread_id") == "thread-a" for message in scoped)


def _seed_project(service, session_id: str, key: str = "repo:a") -> None:
    with service.engine.store.lock(session_id):
        session = service.engine.store.get(session_id)
        state = ensure_state(session)
        state["plan"] = {"phases": [{"title": "练习"}]}
        state["selected_project"] = {"project_key": key, "full_name": f"example/{key.rsplit(':', 1)[-1]}"}
        service.engine.store.save(session)


def test_web_practice_thread_is_server_validated_and_filtered(tmp_path):
    service = build_service(tmp_path, demo=True)
    service.engine.router = TextRouter("练习回复")
    session = service.engine.new_session()
    _seed_project(service, session.id)

    opened = service.open_practice(session.id, {"project_key": "repo:a"})
    thread_id = opened["practice_thread_id"]
    assert opened["project_key"] == "repo:a"
    assert opened["messages"] == []

    # A main turn remains in model context but is never visible in the
    # project/thread transcript.
    service.engine.run("主聊天历史", session.id)
    metadata = service.prepare_practice_turn(
        session.id,
        {"scope": "practice", "project_key": "repo:a"},
        thread_id=thread_id,
    )
    service.engine.run(
        "练习输入",
        session.id,
        message_metadata=metadata,
        action_guard=lambda current: service.validate_practice_turn(current, metadata),
    )
    wire_context = service.engine.router.calls[-1]
    assert any(message.role == "user" and message.content == "主聊天历史" for message in wire_context)
    assert any(message.role == "user" and message.content == "练习输入" for message in wire_context)
    filtered = service.practice_snapshot(session.id, thread_id)
    text = [message["content"] for message in filtered["messages"] if isinstance(message.get("content"), str)]
    assert "练习输入" in text
    assert "练习回复" in text
    assert "主聊天历史" not in text
    assert all(message["metadata"]["practice_thread_id"] == thread_id for message in filtered["messages"])

    # A stale thread/project key is rejected before a new user message is
    # appended, even if the selected project changed in the main workflow.
    _seed_project(service, session.id, "repo:b")
    with pytest.raises(ValueError):
        service.prepare_practice_turn(
            session.id,
            {"scope": "practice", "project_key": "repo:a"},
            thread_id=thread_id,
        )


def test_workflow_action_filter_is_persisted_only_after_turn_is_accepted(tmp_path):
    service = build_service(tmp_path, demo=True)
    service.engine.router = TextRouter("已处理筛选偏好")
    session = service.engine.new_session()
    _seed_project(service, session.id)
    body = {
        "text": "按这个筛选项目",
        "workflow_action": {
            "step": "plan",
            "project_filter": {
                "min_stars": 120,
                "include": ["Python"],
                "exclude": ["demo"],
                "other": "必须能在本地复现",
                "paper_projects": False,
                "rerank": True,
            },
        },
    }
    metadata = {"scope": "main"}
    action = service._workflow_action(body)
    result = service.engine.run(
        body["text"],
        session.id,
        message_metadata=metadata,
        action_guard=service._message_action_guard(metadata, action),
    )
    assert result.status == "completed"
    assert workflow_state(service.engine.store.get(session.id))["project_filter"] == {
        "min_stars": 120,
        "include": ["Python"],
        "exclude": ["demo"],
        "other": "必须能在本地复现",
        "paper_projects": False,
        "rerank": True,
    }

    # A blocked new request must not apply its action before the prior turn is
    # approved/resumed.  This is the guard ordering that prevents a stale UI
    # click from changing durable workflow state while an approval is pending.
    with service.engine.store.lock(session.id):
        blocked = service.engine.store.get(session.id)
        blocked.pending = PendingApproval(tool_name="write_file", summary="待确认")
        service.engine.store.save(blocked)
    replacement = {
        "text": "不要应用这个筛选",
        "workflow_action": {"project_filter": {"min_stars": 999}},
    }
    replacement_action = service._workflow_action(replacement)
    pending_result = service.engine.run(
        replacement["text"],
        session.id,
        message_metadata=metadata,
        action_guard=service._message_action_guard(metadata, replacement_action),
    )
    assert pending_result.status == "pending_approval"
    assert workflow_state(service.engine.store.get(session.id))["project_filter"]["min_stars"] == 120
