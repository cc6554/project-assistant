from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolRegistry
from agent_coach import tools


@pytest.fixture()
def context(tmp_path: Path) -> ToolContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ToolContext(workspace=workspace, session=AgentSession(workspace=str(workspace)), router=None)


@pytest.fixture()
def registry() -> ToolRegistry:
    registry = ToolRegistry()
    tools.register_builtin_tools(registry)
    return registry


def test_registry_contains_domain_neutral_tools(registry: ToolRegistry) -> None:
    assert set(registry._tools) == {
        "list_dir",
        "read_file",
        "write_file",
        "read_document",
        "run_command",
        "fetch_url",
        "github_identity",
        "list_github_repositories",
        "read_github",
        "search_jobs",
        "git_clone",
        "update_plan",
    }
    assert all(definition.skill is None for definition in registry._tools.values())


def test_github_tools_reject_caller_supplied_credentials(context: ToolContext, registry: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="unsupported tool arguments"):
        registry.get("github_identity").handler({"token": "do-not-accept"}, context)
    with pytest.raises(ValueError, match="unsupported tool arguments"):
        registry.get("list_github_repositories").handler(
            {"username": "owner", "token": "do-not-accept"}, context
        )


def test_resolve_path_rejects_escape_and_sensitive_names(context: ToolContext) -> None:
    outside = context.workspace.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (context.workspace / "tokenizer.py").write_text("print('ok')", encoding="utf-8")
    assert tools.resolve_path(context, "tokenizer.py").name == "tokenizer.py"
    with pytest.raises((PermissionError, ValueError)):
        tools.resolve_path(context, "../outside.txt", must_exist=True)
    for name in (".env", ".git", ".ssh", ".agent-coach", "credentials.json", "api_token.txt"):
        with pytest.raises((PermissionError, ValueError)):
            tools.resolve_path(context, name)


def test_resolve_path_checks_symlink_target(context: ToolContext) -> None:
    outside = context.workspace.parent / "outside-link.txt"
    outside.write_text("outside", encoding="utf-8")
    link = context.workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises((PermissionError, ValueError)):
        tools.resolve_path(context, "link.txt", must_exist=True)


def test_read_and_list_filter_sensitive_entries(context: ToolContext, registry: ToolRegistry) -> None:
    (context.workspace / "visible.txt").write_text("hello", encoding="utf-8")
    (context.workspace / ".env").write_text("SECRET=x", encoding="utf-8")
    result = registry.get("read_file").handler({"path": "visible.txt"}, context)
    assert json.loads(result.content)["content"] == "hello"
    listed = registry.get("list_dir").handler({}, context)
    names = json.loads(listed.content)["entries"]
    assert "visible.txt" in names
    assert ".env" not in names


def test_read_document_uses_legacy_reader_inside_workspace(
    context: ToolContext, registry: ToolRegistry
) -> None:
    path = context.workspace / "resume.txt"
    path.write_text("candidate", encoding="utf-8")
    result = registry.get("read_document").handler({"path": "resume.txt"}, context)
    assert json.loads(result.content)["content"] == "candidate"


@pytest.mark.parametrize(
    ("encoding", "content"),
    [
        ("utf-8", "中文 UTF-8 简历"),
        ("utf-16", "中文 UTF-16 简历"),
        ("gb18030", "中文 GB18030 简历"),
    ],
)
def test_text_tools_round_trip_supported_encodings(
    context: ToolContext,
    registry: ToolRegistry,
    encoding: str,
    content: str,
) -> None:
    path = context.workspace / "resume.txt"
    path.write_bytes(content.encode(encoding))

    direct = registry.get("read_document").handler({"path": "resume.txt"}, context)
    file_result = registry.get("read_file").handler({"path": "resume.txt"}, context)

    assert json.loads(direct.content)["content"] == content
    assert json.loads(file_result.content)["content"] == content


def test_write_prepare_is_side_effect_free_and_execute_is_atomic(
    context: ToolContext, registry: ToolRegistry
) -> None:
    path = context.workspace / "note.txt"
    path.write_text("old\n", encoding="utf-8")
    definition = registry.get("write_file")
    pending = definition.handler({"path": "note.txt", "content": "new\n"}, context)
    assert isinstance(pending, PendingApproval)
    assert path.read_text(encoding="utf-8") == "old\n"
    assert "-old" in pending.preview and "+new" in pending.preview
    assert pending.prepared["before_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    outcome = definition.execute(pending, context)
    assert json.loads(outcome.content)["written"] is True
    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_rechecks_before_hash(context: ToolContext, registry: ToolRegistry) -> None:
    path = context.workspace / "note.txt"
    path.write_text("old", encoding="utf-8")
    definition = registry.get("write_file")
    pending = definition.handler({"path": "note.txt", "content": "new"}, context)
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError):
        definition.execute(pending, context)


def test_write_preview_keeps_records_separate_without_final_newline(
    context: ToolContext, registry: ToolRegistry
) -> None:
    path = context.workspace / "no-final-newline.txt"
    path.write_bytes(b"old")
    pending = registry.get("write_file").handler(
        {"path": path.name, "content": "new"}, context
    )
    assert "@@ -1 +1 @@\n-old\n+new\n" in pending.preview


def test_command_prepare_does_not_run_and_execute_is_approved(
    context: ToolContext, registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    class Stream:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, _size: int) -> bytes:
            data, self.data = self.data, b""
            return data

        def close(self) -> None:
            return None

    class Process:
        def __init__(self):
            self.stdout = Stream(b"ok\n")
            self.stderr = Stream(b"")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Process()

    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen)
    definition = registry.get("run_command")
    pending = definition.handler({"command": ["echo", "ok"], "cwd": "."}, context)
    assert isinstance(pending, PendingApproval)
    assert calls == []
    assert "shell=False" in pending.summary
    result = definition.execute(pending, context)
    assert calls and calls[0]["kwargs"]["shell"] is False
    assert json.loads(result.content)["stdout"] == "ok\n"


def test_command_output_is_bounded_and_timeout_reports_direct_child_limit(
    context: ToolContext, registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Stream:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, _size: int) -> bytes:
            data, self.data = self.data, b""
            return data

        def close(self) -> None:
            return None

    class Process:
        def __init__(self):
            self.stdout = Stream(b"x" * 100)
            self.stderr = Stream(b"")
            self.killed = False

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise tools.subprocess.TimeoutExpired("fake", timeout)
            return -9

        def kill(self):
            self.killed = True

    monkeypatch.setattr(tools.subprocess, "Popen", lambda *args, **kwargs: Process())
    definition = registry.get("run_command")
    pending = definition.handler(
        {"command": ["noisy"], "timeout": 0.1, "max_output_bytes": 10}, context
    )
    result = definition.execute(pending, context)
    payload = json.loads(result.content)
    assert payload["timed_out"] is True
    assert payload["stdout_truncated"] is True
    assert len(payload["stdout"].encode()) <= 10
    assert "direct child" in payload["timeout_note"]


def test_git_clone_prepares_expected_argv_and_rechecks_destination(
    context: ToolContext, registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    class Stream:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, _size: int) -> bytes:
            data, self.data = self.data, b""
            return data

        def close(self) -> None:
            return None

    class Process:
        def __init__(self):
            self.stdout = Stream(b"cloned")
            self.stderr = Stream(b"")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return Process()

    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen)
    definition = registry.get("git_clone")
    pending = definition.handler(
        {"url": "https://github.com/example/project.git", "destination": "project"}, context
    )
    assert isinstance(pending, PendingApproval)
    assert "credential.helper=" in pending.preview and "clone" in pending.preview
    result = definition.execute(pending, context)
    assert calls[0]["args"][0] == [
        "git",
        "-c",
        "credential.helper=",
        "clone",
        "--",
        "https://github.com/example/project.git",
        str(context.workspace / "project"),
    ]
    assert json.loads(result.content)["returncode"] == 0

    context.workspace.joinpath("existing").mkdir()
    with pytest.raises(ValueError):
        definition.handler(
            {"url": "https://github.com/example/project.git", "destination": "existing"}, context
        )


def test_fetch_rejects_private_and_credential_urls() -> None:
    for url in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/private",
        "https://example.com/?access_token=should-not-be-sent",
    ):
        with pytest.raises(ValueError):
            tools.ensure_public_url(url)


def test_fetch_validates_each_redirect_and_bounds_response(
    context: ToolContext, registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    class Response:
        def __init__(self, status: int, *, location: str | None = None, body: bytes = b""):
            self.status_code = status
            self.headers = {"Location": location} if location else {}
            self.encoding = "utf-8"
            self._body = body

        def iter_content(self, chunk_size=0):
            del chunk_size
            yield self._body

        def close(self):
            return None

    class Session:
        instances: list["Session"] = []

        def __init__(self):
            self.trust_env = True
            self.headers = {}
            self.calls: list[str] = []
            self.instances.append(self)

        def get(self, url, **kwargs):
            self.calls.append(url)
            assert kwargs["allow_redirects"] is False
            assert kwargs["stream"] is True
            if len(self.calls) == 1:
                return Response(302, location="https://example.org/final")
            return Response(200, body=b"ok")

        def close(self):
            return None

    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda host, port, type=0: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )
    monkeypatch.setattr(requests, "Session", Session)
    result = registry.get("fetch_url").handler({"url": "https://example.org/start", "max_bytes": 2}, context)
    payload = json.loads(result.content)
    assert payload["content"] == "ok"
    assert payload["truncated"] is False
    assert Session.instances[0].trust_env is False
    assert len(Session.instances[0].calls) == 2


def test_update_plan_stores_generic_session_plan(context: ToolContext, registry: ToolRegistry) -> None:
    result = registry.get("update_plan").handler(
        {"plan": ["inspect", {"step": "implement", "status": "pending"}]}, context
    )
    assert context.session.plan == [
        {"step": "inspect", "status": "pending"},
        {"step": "implement", "status": "pending"},
    ]
    assert json.loads(result.content)["count"] == 2
