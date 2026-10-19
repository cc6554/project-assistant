from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_coach import tools
from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolRegistry


@pytest.fixture()
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=None,
    )


def test_run_command_approval_explains_purpose_and_has_no_implicit_deadline(
    context: ToolContext,
) -> None:
    registry = tools.register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    assert "purpose" in registry.get("run_command").spec.parameters["required"]
    pending = registry.get("run_command").handler(
        {
            "purpose": "运行单元测试，确认改动没有破坏项目",
            "command": [sys.executable, "-c", "print('ok')"],
        },
        context,
    )

    assert isinstance(pending, PendingApproval)
    assert pending.prepared["purpose"] == "运行单元测试，确认改动没有破坏项目"
    assert pending.prepared["timeout"] is None
    assert pending.summary.startswith("目的：运行单元测试")
    preview = json.loads(pending.preview)
    assert preview["purpose"] == "运行单元测试，确认改动没有破坏项目"
    assert preview["timeout"] is None


def test_run_command_rejects_string_shell_flags_and_nonfinite_poll_wait(
    context: ToolContext,
) -> None:
    registry = tools.register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    with pytest.raises(ValueError, match="shell must be a boolean"):
        registry.get("run_command").handler(
            {
                "purpose": "验证 shell 参数必须保持布尔类型",
                "command": "echo ok",
                "shell": "false",
            },
            context,
        )

    session = tools.start_command_session(
        [sys.executable, "-c", "print('finite-check')"],
        shell=False,
        cwd=context.workspace,
        timeout=None,
        purpose="验证轮询等待参数",
    )
    try:
        with pytest.raises(ValueError, match="finite"):
            session.poll(wait_seconds=math.nan)
    finally:
        if session.poll()["status"] in {"starting", "running"}:
            session.stop()
        tools.command_sessions.forget(session.id)


def test_clip_bytes_never_expands_output_after_utf8_boundary() -> None:
    text, clipped = tools._clip_bytes("€".encode("utf-8"), 2)
    assert clipped is True
    assert text == ""
    assert len(text.encode("utf-8")) <= 2


def test_timeout_termination_failure_is_uncertain_and_not_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        stdout = None
        stderr = None
        pid = 987654
        returncode = None

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise tools.subprocess.TimeoutExpired("fake", timeout)

    monkeypatch.setattr(tools, "_popen_command", lambda *args, **kwargs: Process())
    monkeypatch.setattr(tools, "_terminate_process_tree", lambda process: False)
    manager = tools.CommandSessionManager(max_sessions=1)
    session = manager.start(
        ["synthetic-timeout"],
        shell=False,
        cwd=tmp_path,
        timeout=0.01,
        output_limit=64,
        purpose="确认无法终止的进程不会被报告为已完成",
    )
    snapshot = session.wait(timeout=2)
    assert snapshot["status"] == "uncertain"
    assert snapshot["termination_failed"] is True
    assert snapshot["finished_at"] is None
    manager.forget(session.id)


def test_explicit_timeout_can_exceed_legacy_two_minute_cap(context: ToolContext) -> None:
    registry = tools.register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    pending = registry.get("run_command").handler(
        {
            "purpose": "给大型构建留出足够时间",
            "command": [sys.executable, "-c", "print('ok')"],
            "timeout": 121,
        },
        context,
    )
    assert isinstance(pending, PendingApproval)
    assert pending.prepared["timeout"] == 121.0


def test_approved_long_command_returns_a_pollable_snapshot_with_approval_binding(
    context: ToolContext,
) -> None:
    registry = tools.register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    definition = registry.get("run_command")
    pending = definition.handler(
        {
            "purpose": "验证审批后的长命令可持续轮询",
            "command": [sys.executable, "-u", "-c", "import time; print('live', flush=True); time.sleep(1)"],
        },
        context,
    )
    assert isinstance(pending, PendingApproval)
    pending.tool_call_id = "call-long"
    outcome = definition.execute(pending, context)
    assert outcome.data is not None
    execution_id = outcome.data["execution_id"]
    assert outcome.data["status"] == "running"
    assert outcome.data["approval_id"] == pending.id
    assert outcome.data["tool_call_id"] == "call-long"
    assert "live" in tools.poll_command_session(execution_id, scope=outcome.data["scope"])["stdout"]
    stopped = tools.stop_command_session(execution_id, scope=outcome.data["scope"])
    assert stopped["status"] == "stopped"


def test_command_session_can_be_polled_and_stopped_without_restarting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream:
        def __init__(self, data: bytes):
            self._data = data

        def read(self, _size: int) -> bytes:
            data, self._data = self._data, b""
            return data

        def close(self) -> None:
            return None

    class Process:
        def __init__(self) -> None:
            self.stdout = Stream(b"started\n")
            self.stderr = Stream(b"")
            self._done = threading.Event()
            self.returncode: int | None = None
            self.wait_calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if not self._done.wait(timeout):
                raise tools.subprocess.TimeoutExpired("fake", timeout)
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9
            self._done.set()

    process = Process()
    monkeypatch.setattr(tools, "_popen_command", lambda *args, **kwargs: process)
    manager = tools.CommandSessionManager(max_sessions=2)
    session = manager.start(
        ["synthetic-long-command"],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        output_limit=64,
        purpose="等待用户决定是否停止测试",
        approval_id="approval-123",
        tool_call_id="call-123",
    )

    first = manager.poll(session.id)
    assert first["status"] == "running"
    assert first["stdout"] == "started\n"
    assert first["approval_id"] == "approval-123"
    assert first["tool_call_id"] == "call-123"

    stopped = manager.stop(session.id)
    assert stopped["status"] == "stopped"
    assert stopped["returncode"] == -9
    assert process.wait_calls >= 1

    with pytest.raises(ValueError, match="not found"):
        # A new ID must never be inferred from a stopped command.
        manager.poll("different-command-id")


def test_command_session_scope_prevents_cross_session_poll_or_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = None
            self.stderr = None
            self._done = threading.Event()
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            if not self._done.wait(timeout):
                raise tools.subprocess.TimeoutExpired("fake", timeout)
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9
            self._done.set()

    process = Process()
    monkeypatch.setattr(tools, "_popen_command", lambda *args, **kwargs: process)
    manager = tools.CommandSessionManager(max_sessions=2)
    session = manager.start(
        ["synthetic"],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        output_limit=64,
        purpose="验证会话归属",
        owner_workspace=tmp_path,
        owner_session_id="session-a",
        scope={"scope": "practice", "project_key": "project-a", "practice_thread_id": "thread-a"},
    )

    with pytest.raises(ValueError, match="does not belong"):
        manager.poll(
            session.id,
            scope={
                "workspace": str(tmp_path),
                "session_id": "session-b",
                "scope": "practice",
                "project_key": "project-a",
                "practice_thread_id": "thread-a",
            },
        )
    with pytest.raises(ValueError, match="does not belong"):
        manager.stop(session.id, scope={"session_id": "session-b"})
    with pytest.raises(ValueError, match="does not belong"):
        manager.stop(
            session.id,
            scope={
                "session_id": "session-a",
                "scope": "practice",
                "project_key": "project-a",
                "practice_thread_id": "thread-b",
            },
        )
    manager.stop(session.id, scope={"session_id": "session-a", "project_key": "project-a"})


def test_command_session_listing_is_filtered_by_owner_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = None
            self.stderr = None
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(tools, "_popen_command", lambda *args, **kwargs: Process())
    manager = tools.CommandSessionManager(max_sessions=4)
    first = manager.start(
        ["first"],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        output_limit=64,
        purpose="列出当前会话的命令结果",
        owner_workspace=tmp_path,
        owner_session_id="session-a",
        scope={"project_key": "project-a"},
    )
    second = manager.start(
        ["second"],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        output_limit=64,
        purpose="验证其他会话不会泄露到列表",
        owner_workspace=tmp_path,
        owner_session_id="session-b",
        scope={"project_key": "project-b"},
    )

    visible = manager.list_sessions(
        scope={"workspace": str(tmp_path), "session_id": "session-a"}
    )
    assert [item["execution_id"] for item in visible] == [first.id]
    assert manager.list_sessions(
        scope={"workspace": str(tmp_path), "session_id": "session-a"},
        include_finished=False,
    ) == []
    assert manager.list_sessions(scope={"session_id": "session-b"})[0]["execution_id"] == second.id


def test_real_command_session_keeps_running_past_short_poll_window(tmp_path: Path) -> None:
    session = tools.start_command_session(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; print('ready', flush=True); time.sleep(0.35); print('done', flush=True)",
        ],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        purpose="验证命令会话不会被轮询窗口提前终止",
    )
    running = session.poll(wait_seconds=0.15)
    assert running["status"] in {"running", "completed"}
    finished = session.wait()
    assert finished["status"] == "completed"
    assert finished["returncode"] == 0
    assert finished["stdout"].replace("\r\n", "\n").endswith("done\n")
    tools.command_sessions.forget(session.id)


def test_stop_terminates_a_spawned_descendant_process_tree(tmp_path: Path) -> None:
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    session = tools.start_command_session(
        [sys.executable, "-u", "-c", parent_code, child_code],
        shell=False,
        cwd=tmp_path,
        timeout=None,
        purpose="验证停止操作会关闭命令的子进程",
    )
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            snapshot = session.poll(wait_seconds=0.1)
            match = re.search(r"\b(\d+)\b", snapshot["stdout"])
            if match:
                child_pid = int(match.group(1))
                break
            if snapshot["status"] != "running":
                break
        assert child_pid is not None, "the parent command did not report its child PID"
        stopped = session.stop()
        assert stopped["status"] == "stopped"

        for _ in range(20):
            if os.name == "nt":
                probe = tools.subprocess.run(
                    ["tasklist", "/FI", f"PID eq {child_pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                alive = bool(re.search(rf"\b{child_pid}\b", probe.stdout or ""))
            else:
                try:
                    os.kill(child_pid, 0)
                except OSError:
                    alive = False
                else:
                    alive = True
            if not alive:
                break
            time.sleep(0.05)
        assert not alive, "stopping the command left its child process alive"
    finally:
        if session.poll()["status"] == "running":
            session.stop()
        tools.command_sessions.forget(session.id)
