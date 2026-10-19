from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from agent_coach import integrations
from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolDefinition, ToolOutcome, ToolRegistry
from agent_coach.engine import AgentEngine
from agent_coach.legacy.core.schema import ToolCall, ToolSpec
from agent_coach.tools import register_builtin_tools


QA_TOKEN = "ghp-qa-dummy-token-000"


class _Response:
    def __init__(self, status: int, payload=None, *, location: str | None = None):
        self.status_code = status
        self.headers = {}
        if location is not None:
            self.headers["Location"] = location
        self._body = json.dumps(payload if payload is not None else {}).encode("utf-8")
        self.headers["Content-Length"] = str(len(self._body))

    def iter_content(self, chunk_size=0):
        del chunk_size
        yield self._body

    def close(self):
        return None


class _Session:
    instances: list["_Session"] = []
    responses: list[_Response] = []

    def __init__(self):
        self.headers = {}
        self.trust_env = True
        self.calls: list[tuple[str, dict]] = []
        self.__class__.instances.append(self)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.__class__.responses.pop(0)

    def close(self):
        return None


def _fake_sessions(monkeypatch: pytest.MonkeyPatch, *responses: _Response):
    _Session.instances.clear()
    _Session.responses[:] = list(responses)
    monkeypatch.setattr(integrations.requests, "Session", _Session)
    return _Session


@pytest.mark.parametrize(
    ("status", "message"),
    ((401, "认证失败"), (403, "请求受限或无权访问"), (404, "仓库或路径不存在")),
)
def test_github_errors_are_precise_and_never_echo_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, message: str
):
    integrations.save_github_token(tmp_path, QA_TOKEN)
    sessions = _fake_sessions(
        monkeypatch,
        _Response(status, {"message": f"upstream body {QA_TOKEN}"}),
    )

    with pytest.raises(integrations.IntegrationError, match=message) as caught:
        integrations.github_identity(tmp_path)

    assert QA_TOKEN not in str(caught.value)
    assert sessions.instances[0].trust_env is False
    assert sessions.instances[0].headers["Authorization"] == f"Bearer {QA_TOKEN}"
    assert sessions.instances[0].calls[0][0] == "https://api.github.com/user"
    assert sessions.instances[0].calls[0][1]["allow_redirects"] is False


def test_missing_token_explains_how_to_enable_private_access(tmp_path: Path):
    with pytest.raises(integrations.IntegrationError, match="需要 GitHub Token") as caught:
        integrations.github_identity(tmp_path)
    assert "设置页" in str(caught.value)
    assert "Token" in str(caught.value)


def test_private_repository_pages_are_explicit_and_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    integrations.save_github_token(tmp_path, QA_TOKEN)
    page_one = [{"full_name": "cc6554/private-one", "private": True, "visibility": "private"}]
    sessions = _fake_sessions(
        monkeypatch,
        _Response(200, {"login": "cc6554", "id": 17}),
        _Response(200, page_one),
    )
    first = integrations.list_github_repositories(tmp_path, username="cc6554", page=1, per_page=1)
    assert first["source"] == "authenticated_user"
    assert first["private_available"] is True
    assert first["next_page"] == 2
    assert [row["full_name"] for row in first["repositories"]] == ["cc6554/private-one"]
    assert sessions.instances[-1].calls[0][0] == "https://api.github.com/user/repos"
    assert sessions.instances[-1].calls[0][1]["params"]["page"] == 1

    _Session.responses[:] = [
        _Response(200, {"login": "cc6554", "id": 17}),
        _Response(200, [{"full_name": "cc6554/private-two", "private": True}]),
    ]
    # A short second page is the bounded signal that no further page exists.
    second = integrations.list_github_repositories(tmp_path, username="cc6554", page=2, per_page=2)
    assert second["page"] == 2
    assert second["next_page"] is None
    assert [row["full_name"] for row in second["repositories"]] == ["cc6554/private-two"]
    assert all(
        call[0].startswith("https://api.github.com/")
        for instance in sessions.instances
        for call in instance.calls
    )


def test_profile_url_uses_api_route_and_rejects_credential_or_redirect_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    accepted = "https://github.com/cc6554?tab=repositories"
    assert integrations.parse_github_user_url(accepted) == "cc6554"
    for url in (
        "http://github.com/cc6554",
        "https://github.com:444/cc6554",
        "https://github.com/cc6554?access_token=leak",
        "https://github.com/cc6554#token=leak",
        "https://evil.example/cc6554",
        "https://github.com/settings",
        "https://user:pass@github.com/cc6554",
    ):
        assert integrations.parse_github_user_url(url) is None

    registry = ToolRegistry()
    register_builtin_tools(registry)
    context = ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=None,
    )
    seen: dict[str, object] = {}

    def fake_list(workspace, **kwargs):
        seen.update({"workspace": workspace, **kwargs})
        return {"ok": True, "kind": "repositories", "repositories": []}

    monkeypatch.setattr(integrations, "list_github_repositories", fake_list)
    result = registry.get("fetch_url").handler({"url": accepted}, context)
    assert json.loads(result.content)["kind"] == "repositories"
    assert seen == {"workspace": tmp_path, "username": "cc6554"}


def test_github_redirect_never_moves_token_to_another_host_or_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    integrations.save_github_token(tmp_path, QA_TOKEN)
    sessions = _fake_sessions(
        monkeypatch,
        _Response(302, location="https://evil.example/collect"),
    )
    with pytest.raises(integrations.IntegrationError, match="重定向目标") as caught:
        integrations.read_github(tmp_path, "README.md", repository="cc6554/private")
    assert QA_TOKEN not in str(caught.value)
    assert len(sessions.instances[0].calls) == 1

    _Session.responses[:] = [_Response(302, location="https://api.github.com/user?access_token=leak")]
    with pytest.raises(integrations.IntegrationError, match="凭据"):
        integrations.read_github(tmp_path, "README.md", repository="cc6554/private")


def test_existing_workspace_session_reads_latest_saved_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def payload(text: str):
        return {
            "type": "file",
            "path": "README.md",
            "size": len(text),
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }

    sessions = _fake_sessions(monkeypatch, _Response(200, payload("first")), _Response(200, payload("second")))
    integrations.save_github_token(tmp_path, "ghp-qa-first-token")
    assert integrations.read_github(tmp_path, "README.md", repository="cc6554/private")["content"] == "first"
    integrations.save_github_token(tmp_path, "ghp-qa-second-token")
    assert integrations.read_github(tmp_path, "README.md", repository="cc6554/private")["content"] == "second"
    assert sessions.instances[0].headers["Authorization"] == "Bearer ghp-qa-first-token"
    assert sessions.instances[1].headers["Authorization"] == "Bearer ghp-qa-second-token"


def test_interrupted_approved_claim_is_resolved_without_replaying_side_effect(tmp_path: Path):
    side_effects: list[str] = []
    registry = ToolRegistry()

    def prepare(args, ctx):
        return PendingApproval(tool_name="write", tool_call_id="write-1", summary="write", arguments=args)

    def execute(pending, ctx):
        side_effects.append("executed")
        return ToolOutcome("done")

    registry.register(
        ToolDefinition(
            spec=ToolSpec(name="write", description="write", parameters={"type": "object"}),
            handler=prepare,
            execute=execute,
        )
    )
    store = __import__("agent_coach.store", fromlist=["SessionStore"]).SessionStore(tmp_path)
    engine = AgentEngine(store, registry, router=None)
    session = engine.new_session()
    session.pending = PendingApproval(
        id="approval-1", tool_call_id="write-1", tool_name="write", summary="write", arguments={}
    )
    session.queued_calls = [ToolCall(id="write-1", name="write", arguments={})]
    session.execution = {
        "status": "running",
        "kind": "approved_tool",
        "call_id": "write-1",
        "approval_id": "approval-1",
        "tool_name": "write",
    }
    store.save(session)

    restored = AgentEngine(store, registry, router=None)
    result = restored.continue_session(session.id)
    assert result.status == "uncertain"
    resolved = restored.resolve_uncertain(session.id, "approval-1", "已人工核对")
    assert resolved.status == "resolved"
    assert side_effects == []
