from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_coach import tools
from agent_coach.contracts import PendingApproval, ToolRegistry
from agent_coach.engine import AgentEngine, ApprovalMismatch
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.store import SessionStore


class CommandRouter:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.calls = 0

    def complete(self, task, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="command-call-1",
                        name="run_command",
                        arguments={
                            "purpose": "运行本地验证命令并读取测试输出",
                            "command": self.command,
                        },
                    )
                ]
            )
        return LLMResponse(text="命令结果已收到，后续步骤可以继续。")


def _engine(tmp_path: Path, router: CommandRouter) -> AgentEngine:
    registry = tools.register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    return AgentEngine(SessionStore(tmp_path), registry, router, max_steps=4)


def test_delayed_command_poll_commits_once_and_resumes_model(tmp_path: Path) -> None:
    router = CommandRouter(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; print('engine-live', flush=True); time.sleep(0.8); print('engine-done', flush=True)",
        ]
    )
    engine = _engine(tmp_path, router)

    pending = engine.run("请执行验证命令")
    assert pending.status == "pending_approval"
    assert pending.pending is not None

    handed_off = engine.approve(pending.session.id, pending.pending.id)
    assert handed_off.status == "running_command"
    assert handed_off.data is not None
    command_id = handed_off.data["command_session_id"]
    assert handed_off.data["status"] == "running"
    saved = engine.store.get(pending.session.id)
    assert saved.execution["status"] == "running"
    assert saved.execution["command_session_id"] == command_id
    assert saved.pending is None
    assert not any(message.tool_call_id == "command-call-1" for message in saved.messages)

    finished = engine.poll_command(pending.session.id, command_id, wait_seconds=3)
    assert finished.data is not None
    assert finished.data["command_session_id"] == command_id
    assert finished.data["status"] == "completed"
    assert finished.status == "completed"
    assert router.calls == 2

    saved = engine.store.get(pending.session.id)
    command_messages = [
        message
        for message in saved.messages
        if message.role == "tool" and message.tool_call_id == "command-call-1"
    ]
    assert len(command_messages) == 1
    assert "engine-done" in str(command_messages[0].content)
    assert saved.execution is None
    assert saved.last_command["command_session_id"] == command_id

    # A browser retry after the model continuation gets the same bounded
    # snapshot and never invokes execute or the model a second time.
    repeated = engine.poll_command(pending.session.id, command_id)
    assert repeated.status == "completed_command"
    assert repeated.data["stdout"].replace("\r\n", "\n").endswith("engine-done\n")
    assert router.calls == 2
    assert len(
        [
            message
            for message in engine.store.get(pending.session.id).messages
            if message.role == "tool" and message.tool_call_id == "command-call-1"
        ]
    ) == 1


def test_stop_command_commits_stopped_snapshot_and_continues_model(tmp_path: Path) -> None:
    router = CommandRouter(
        [sys.executable, "-u", "-c", "import time; print('before-stop', flush=True); time.sleep(30)"]
    )
    engine = _engine(tmp_path, router)
    pending = engine.run("请启动可停止的验证命令")
    assert pending.pending is not None
    handed_off = engine.approve(pending.session.id, pending.pending.id)
    assert handed_off.status == "running_command"
    command_id = handed_off.data["command_session_id"]

    stopped = engine.stop_command(pending.session.id, command_id)
    assert stopped.data is not None
    assert stopped.data["status"] == "stopped"
    assert stopped.status == "completed"
    assert router.calls == 2
    saved = engine.store.get(pending.session.id)
    assert saved.execution is None
    assert len(
        [message for message in saved.messages if message.tool_call_id == "command-call-1"]
    ) == 1


def test_short_command_completion_keeps_idempotent_tombstone(tmp_path: Path) -> None:
    router = CommandRouter([sys.executable, "-u", "-c", "print('short-done', flush=True)"])
    engine = _engine(tmp_path, router)
    pending = engine.run("请执行短验证命令")
    assert pending.pending is not None

    finished = engine.approve(pending.session.id, pending.pending.id)
    command_id = finished.data["command_session_id"]
    if finished.status == "running_command":
        finished = engine.poll_command(pending.session.id, command_id, wait_seconds=2)
    assert finished.status == "completed"
    assert finished.data["status"] == "completed"
    assert engine.store.get(pending.session.id).last_command["command_session_id"] == command_id
    repeated = engine.poll_command(pending.session.id, command_id)
    assert repeated.status == "completed_command"
    assert repeated.data["stdout"].strip() == "short-done"
    assert router.calls == 2


def test_cancel_command_stops_process_without_orphaning_or_resuming(tmp_path: Path) -> None:
    router = CommandRouter([sys.executable, "-u", "-c", "import time; time.sleep(30)"])
    engine = _engine(tmp_path, router)
    pending = engine.run("请启动可取消的验证命令")
    assert pending.pending is not None
    handed_off = engine.approve(pending.session.id, pending.pending.id)
    assert handed_off.status == "running_command"
    command_id = handed_off.data["command_session_id"]

    cancelled = engine.cancel(pending.session.id)
    assert cancelled.status == "cancelled"
    assert cancelled.data["command_session_id"] == command_id
    assert cancelled.data["status"] == "stopped"
    assert router.calls == 1
    saved = engine.store.get(pending.session.id)
    assert saved.execution is None
    assert len(
        [message for message in saved.messages if message.tool_call_id == "command-call-1"]
    ) == 1


def test_restart_of_running_command_is_uncertain_and_never_replays(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create()
    session.execution = {
        "status": "running",
        "kind": "approved_command",
        "command_session_id": "missing-after-restart",
        "call_id": "command-call-1",
        "approval_id": "approval-1",
        "tool_name": "run_command",
        "arguments": {},
        "scope": "main",
        "metadata": {"scope": "main"},
    }
    store.save(session)
    router = CommandRouter([sys.executable, "-c", "print('must-not-run')"])
    engine = _engine(tmp_path, router)

    recovered = engine.continue_session(session.id)
    assert recovered.status == "uncertain"
    saved = store.get(session.id)
    assert saved.execution["status"] == "uncertain"
    assert router.calls == 0
    assert not any(message.tool_call_id == "command-call-1" for message in saved.messages)

    with pytest.raises(ApprovalMismatch):
        engine.poll_command(session.id, "different-id")


def test_cancel_uncertain_preserves_claim_and_blocks_stale_approval(tmp_path: Path) -> None:
    router = CommandRouter([sys.executable, "-c", "print('must-not-run')"])
    store = SessionStore(tmp_path)
    engine = _engine(tmp_path, router)
    session = store.create()
    pending = PendingApproval(
        id="approval-uncertain",
        tool_call_id="command-call-uncertain",
        tool_name="run_command",
        arguments={"purpose": "不应再次执行", "command": [sys.executable, "-c", "print('bad')"]},
        summary="目的：不应再次执行",
    )
    session.pending = pending
    session.execution = {
        "status": "uncertain",
        "kind": "approved_command",
        "command_session_id": "unknown-command",
        "call_id": pending.tool_call_id,
        "approval_id": pending.id,
        "tool_name": pending.tool_name,
        "arguments": dict(pending.arguments),
        "command_status": "uncertain",
        "scope": "main",
        "metadata": {"scope": "main"},
    }
    store.save(session)

    cancelled = engine.cancel(session.id)
    assert cancelled.status == "uncertain"
    saved = store.get(session.id)
    assert saved.execution["status"] == "uncertain"
    assert saved.pending is not None

    approved = engine.approve(session.id, pending.id)
    assert approved.status == "uncertain"
    saved = store.get(session.id)
    assert saved.execution["status"] == "uncertain"
    assert saved.pending is not None
    assert router.calls == 0
