from __future__ import annotations

import sys
from pathlib import Path

from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolRegistry
from agent_coach.tools import register_builtin_tools


def test_approved_synthetic_write_and_command_probe(tmp_path: Path) -> None:
    """Exercise the approval boundary with only synthetic workspace data."""

    registry = register_builtin_tools(ToolRegistry(), include_job_integrations=False)
    context = ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=None,
    )

    probe_path = tmp_path / "capability-probe-synthetic.txt"
    write = registry.get("write_file")
    write_pending = write.handler(
        {"path": probe_path.name, "content": "approved synthetic payload\n"},
        context,
    )
    assert isinstance(write_pending, PendingApproval)
    assert not probe_path.exists(), "prepare must not write before approval"
    assert write.execute is not None
    write_result = write.execute(write_pending, context)
    assert write_result.data == {
        "path": probe_path.name,
        "size": 27,
        "sha256": "bfe0ac82d7183327663ef634c6e559e1e564f0fd0f970804261d32ca7c2950e2",
        "written": True,
    }
    assert probe_path.read_text(encoding="utf-8") == "approved synthetic payload\n"

    command = registry.get("run_command")
    command_pending = command.handler(
        {
            "command": [sys.executable, "-c", "print('synthetic-command-ok')"],
            "cwd": ".",
            "timeout": 10,
        },
        context,
    )
    assert isinstance(command_pending, PendingApproval)
    assert command_pending.prepared["shell"] is False
    assert command.execute is not None
    command_result = command.execute(command_pending, context)
    assert command_result.data is not None
    assert command_result.data["returncode"] == 0
    assert command_result.data["stdout"].strip() == "synthetic-command-ok"
    assert command_result.data["stderr"] == ""

    probe_path.unlink()
