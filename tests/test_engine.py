from __future__ import annotations

import pytest

from agent_coach.contracts import PendingApproval, ToolDefinition, ToolOutcome, ToolRegistry
from agent_coach.engine import (
    DEFAULT_LANGUAGE_DIRECTIVE,
    AgentEngine,
    ApprovalMismatch,
    RunResult,
)
from agent_coach.legacy.core.schema import LLMResponse, ToolCall, ToolSpec
from agent_coach.store import RevisionConflict, SessionStore


class ScriptRouter:
    def __init__(self):
        self.calls = 0

    def complete(self, task, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="我会按顺序处理。",
                tool_calls=[
                    ToolCall(id="safe-1", name="safe", arguments={}),
                    ToolCall(id="write-1", name="write", arguments={"value": "x"}),
                    ToolCall(id="safe-2", name="safe2", arguments={}),
                ],
            )
        return LLMResponse(text="全部处理完成。")


def make_registry(order: list[str]):
    registry = ToolRegistry()

    def safe(args, ctx):
        order.append("safe")
        return ToolOutcome("safe result")

    def safe2(args, ctx):
        order.append("safe2")
        return ToolOutcome("safe2 result")

    def prepare(args, ctx):
        order.append("prepare")
        return PendingApproval(
            tool_name="write",
            arguments=args,
            summary="write one value",
            preview="value=x",
        )

    def execute(pending, ctx):
        order.append("execute")
        return ToolOutcome("write result")

    for name, description, handler, execute_handler in (
        ("safe", "safe", safe, None),
        ("write", "write", prepare, execute),
        ("safe2", "safe2", safe2, None),
    ):
        registry.register(
            ToolDefinition(
                spec=ToolSpec(name=name, description=description, parameters={"type": "object"}),
                handler=handler,
                execute=execute_handler,
            )
        )
    return registry


def test_default_simplified_chinese_directive_reaches_every_restored_model_turn(tmp_path):
    class Router:
        def __init__(self):
            self.system_prompts = []
            self.calls = 0

        def complete(self, task, messages, *, tools=None, **kwargs):
            self.calls += 1
            assert task == "agent"
            assert messages[0].role == "system"
            self.system_prompts.append(str(messages[0].content))
            if self.calls == 1:
                return LLMResponse(tool_calls=[ToolCall(id="safe-1", name="safe", arguments={})])
            return LLMResponse(text="已完成")

    router = Router()
    store = SessionStore(tmp_path)
    engine = AgentEngine(store, make_registry([]), router, max_steps=1)
    first = engine.run("继续处理")
    assert first.status == "paused"

    # A fresh engine simulates a restored process and supplies a custom prompt;
    # the engine must still append the product-wide language contract.
    restored = AgentEngine(
        store,
        make_registry([]),
        router,
        max_steps=1,
        system_prompt="Answer in English unless told otherwise.",
    )
    second = restored.continue_session(first.session.id)
    assert second.status == "completed"
    assert len(router.system_prompts) == 2
    for prompt in router.system_prompts:
        assert DEFAULT_LANGUAGE_DIRECTIVE in prompt
        assert prompt.count(DEFAULT_LANGUAGE_DIRECTIVE) == 1


def test_multitool_queue_and_approval_preserve_transcript(tmp_path):
    order: list[str] = []
    store = SessionStore(tmp_path)
    engine = AgentEngine(store, make_registry(order), ScriptRouter(), max_steps=4)

    first = engine.run("do it")
    assert first.status == "pending_approval"
    assert first.pending is not None
    assert first.session.queued_calls[0].id == "safe-2"
    assert order == ["safe", "prepare"]
    assert [m.role for m in first.session.messages].count("assistant") == 1
    tool_messages = [m for m in first.session.messages if m.role == "tool"]
    assert tool_messages[0].tool_call_id == "safe-1"
    assert tool_messages[0].name == "safe"

    with pytest.raises(ApprovalMismatch):
        engine.approve(first.session.id, "wrong-id")

    finished = engine.approve(first.session.id, first.pending.id)
    assert finished.status == "completed"
    assert order == ["safe", "prepare", "execute", "safe2"]
    saved = store.get(first.session.id)
    assert saved.pending is None
    assert saved.queued_calls == []
    assert any(m.tool_call_id == "write-1" and "write result" in str(m.content) for m in saved.messages)
    assert any(m.tool_call_id == "safe-2" and "safe2 result" in str(m.content) for m in saved.messages)
    assert any(m.role == "assistant" and "全部处理完成" in str(m.content) for m in saved.messages)


def test_running_claim_is_uncertain_and_resolve_never_replays(tmp_path):
    order: list[str] = []
    store = SessionStore(tmp_path)
    engine = AgentEngine(store, make_registry(order), ScriptRouter(), max_steps=2)
    session = engine.new_session()
    session.execution = {
        "status": "running",
        "call_id": "write-1",
        "approval_id": "approval-1",
        "tool_name": "write",
    }
    session.pending = PendingApproval(id="approval-1", tool_call_id="write-1", tool_name="write", summary="write")
    store.save(session)

    stopped = engine.continue_session(session.id)
    assert stopped.status == "uncertain"
    assert store.get(session.id).execution["status"] == "uncertain"
    resolved = engine.resolve_uncertain(session.id, "approval-1", "已核对外部系统")
    assert resolved.status == "resolved"
    saved = store.get(session.id)
    assert saved.execution["status"] == "resolved_uncertain"
    assert saved.pending is None
    assert order == []
    assert any(m.role == "tool" and m.tool_call_id == "write-1" and "未知" in str(m.content) for m in saved.messages)


def test_store_uses_revision_cas(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create()
    stale = store.get(first.id)
    first.plan.append({"step": 1})
    store.save(first)
    stale.plan.append({"step": 2})
    with pytest.raises(RevisionConflict):
        store.save(stale)


def test_unknown_and_failing_tools_become_tool_messages(tmp_path):
    registry = ToolRegistry()

    def bad(args, ctx):
        raise RuntimeError("boom")

    registry.register(
        ToolDefinition(
            spec=ToolSpec(name="bad", description="bad", parameters={"type": "object"}),
            handler=bad,
        )
    )

    class Router:
        def __init__(self):
            self.turn = 0

        def complete(self, task, messages, *, tools=None, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id="missing-1", name="missing", arguments={}),
                        ToolCall(id="bad-1", name="bad", arguments={}),
                    ]
                )
            return LLMResponse(text="已处理错误")

    engine = AgentEngine(SessionStore(tmp_path), registry, Router())
    result = engine.run("test errors")
    assert result.status == "completed"
    messages = result.session.messages
    assert any(m.tool_call_id == "missing-1" and "未知工具" in str(m.content) for m in messages)
    assert any(m.tool_call_id == "bad-1" and "执行异常" in str(m.content) for m in messages)


def test_no_skills_hides_domain_tools(tmp_path):
    from agent_coach.bootstrap import build_engine

    engine = build_engine(tmp_path, no_skills=True)
    names = {spec.name for spec in engine.registry.specs([])}
    assert "list_dir" in names
    assert "job_radar_status" not in names
    assert "search_jobs" not in names
    assert engine.available_skills() == []


def test_model_can_activate_skill_and_receive_domain_tools(tmp_path):
    from agent_coach.bootstrap import build_engine

    class Router:
        def __init__(self):
            self.seen = []
            self.turn = 0

        def complete(self, task, messages, *, tools=None, **kwargs):
            self.turn += 1
            self.seen.append({spec.name for spec in tools or []})
            if self.turn == 1:
                return LLMResponse(
                    tool_calls=[ToolCall(id="activate-1", name="activate_skill", arguments={"name": "job-radar"})]
                )
            assert "job_radar_status" in self.seen[-1]
            return LLMResponse(text="skill tool visible")

    engine = build_engine(tmp_path)
    router = Router()
    engine.router = router
    result = engine.run("discover a domain tool")
    assert result.status == "completed"
    assert "job_radar_status" in router.seen[-1]
    assert result.session.active_skills == ["job-radar"]


def test_reject_continues_saved_queue_and_max_steps_pauses(tmp_path):
    order: list[str] = []
    store = SessionStore(tmp_path)
    engine = AgentEngine(store, make_registry(order), ScriptRouter(), max_steps=1)
    first = engine.run("reject it")
    assert first.status == "pending_approval"
    rejected = engine.reject(first.session.id, first.pending.id)
    assert rejected.status == "completed"
    assert order == ["safe", "prepare", "safe2"]
    assert any(m.tool_call_id == "write-1" and "拒绝" in str(m.content) for m in rejected.session.messages)

    class Endless:
        def complete(self, task, messages, *, tools=None, **kwargs):
            return LLMResponse(tool_calls=[ToolCall(id="safe-loop", name="safe", arguments={})])

    order.clear()
    paused_engine = AgentEngine(store, make_registry(order), Endless(), max_steps=1)
    paused = paused_engine.run("never finish")
    assert paused.status == "paused"
    assert paused.session.execution["reason"] == "max_steps"
    assert "完成" not in paused.message


def test_new_prompt_does_not_jump_ahead_of_recoverable_queue(tmp_path):
    order: list[str] = []
    store = SessionStore(tmp_path)
    engine = AgentEngine(store, make_registry(order), ScriptRouter(), max_steps=1)
    first = engine.run("first")
    assert first.status == "pending_approval"
    # Resolve the pending call only far enough to leave its following safe call
    # in the queue; the next user prompt must not be inserted before it.
    resolved = engine.reject(first.session.id, first.pending.id)
    assert resolved.status == "completed"

    # Simulate a durable queue left by a process boundary.
    session = store.get(first.session.id)
    session.queued_calls = [ToolCall(id="safe-tail", name="safe", arguments={})]
    store.save(session)
    before = len(session.messages)
    paused = engine.run("second", session_id=session.id)
    assert paused.status == "paused"
    assert len(store.get(session.id).messages) == before
    continued = engine.continue_session(session.id)
    assert continued.status in {"completed", "paused"}
