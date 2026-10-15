from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from agent_coach import integrations
from agent_coach.contracts import AgentSession, ToolContext, ToolRegistry
from agent_coach.legacy.tools.credentials import github_token, github_token_scope
from agent_coach.tools import register_builtin_tools


class _Response:
    def __init__(self, status: int, payload=None, *, location: str | None = None):
        self.status_code = status
        self.headers = {"Content-Length": "0"}
        if location:
            self.headers["Location"] = location
        self.encoding = "utf-8"
        self._body = json.dumps(payload).encode("utf-8") if payload is not None else b""
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


def _patch_session(monkeypatch: pytest.MonkeyPatch, *responses: _Response) -> _Session:
    _Session.instances.clear()
    _Session.responses[:] = list(responses)
    monkeypatch.setattr(integrations.requests, "Session", _Session)
    return _Session


def test_github_token_is_workspace_bound_encrypted_and_scoped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "must-not-be-read")
    assert github_token() == "must-not-be-read"
    assert integrations.github_token_status(tmp_path) == {"has_token": True}
    with github_token_scope(None):
        assert github_token() is None
    monkeypatch.delenv("AGENT_COACH_GITHUB_TOKEN")

    result = integrations.save_github_token(tmp_path, "ghp-test-token")
    key_file = tmp_path / ".agent-coach" / integrations.GITHUB_TOKEN_FILENAME
    assert result == {"saved": True, "has_token": True}
    assert key_file.is_file()
    assert b"ghp-test-token" not in key_file.read_bytes()
    assert integrations.github_token_status(tmp_path) == {"has_token": True}
    assert integrations.load_github_token(tmp_path) == "ghp-test-token"
    assert github_token() is None

    with integrations.github_token_context(tmp_path):
        assert github_token() == "ghp-test-token"
    assert github_token() is None
    with github_token_scope("scoped-token"):
        assert github_token() == "scoped-token"
    assert github_token() is None

    assert integrations.clear_github_token(tmp_path) == {"cleared": True, "has_token": False}
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "legacy-token")
    assert (tmp_path / ".agent-coach" / integrations.GITHUB_TOKEN_DISABLED_FILENAME).is_file()
    assert integrations.github_token_status(tmp_path) == {"has_token": False}
    assert github_token() == "legacy-token"
    with integrations.github_token_context(tmp_path):
        assert github_token() is None


def test_read_github_file_directory_and_readme_use_api_auth(tmp_path: Path, monkeypatch):
    integrations.save_github_token(tmp_path, "ghp-private-token")
    file_payload = {
        "type": "file",
        "path": "docs/guide.md",
        "size": 13,
        "encoding": "base64",
        "content": base64.b64encode("中文 guide\n".encode()).decode(),
    }
    directory_payload = [
        {"name": "README.md", "path": "README.md", "type": "file", "size": 10},
        {"name": "src", "path": "src", "type": "dir", "size": 0},
    ]
    readme_payload = {
        "type": "file",
        "path": "README.md",
        "size": 5,
        "encoding": "base64",
        "content": base64.b64encode(b"hello").decode(),
    }
    session_type = _patch_session(
        monkeypatch,
        _Response(200, file_payload),
        _Response(200, directory_payload),
        _Response(200, readme_payload),
    )

    file_result = integrations.read_github(
        tmp_path, "docs/guide.md", repository="owner/private", ref="main"
    )
    assert file_result["content"] == "中文 guide\n"
    assert file_result["kind"] == "file"
    assert file_result["truncated"] is False
    assert "ghp-private-token" not in json.dumps(file_result, ensure_ascii=False)
    assert session_type.instances[0].trust_env is False
    assert session_type.instances[0].headers["Authorization"] == "Bearer ghp-private-token"
    assert session_type.instances[0].calls[0][0].endswith("/repos/owner/private/contents/docs/guide.md")
    assert session_type.instances[0].calls[0][1]["params"] == {"ref": "main"}

    directory_result = integrations.read_github(tmp_path, "", repository="owner/private")
    assert directory_result["kind"] == "directory"
    assert {entry["path"] for entry in directory_result["entries"]} == {"README.md", "src"}

    readme_result = integrations.read_github(tmp_path, "README", repository="owner/private")
    assert readme_result["content"] == "hello"
    assert session_type.instances[2].calls[0][0].endswith("/repos/owner/private/readme")


def test_read_github_rejects_sensitive_path_and_cross_domain_redirect(tmp_path: Path, monkeypatch):
    integrations.save_github_token(tmp_path, "ghp-private-token")
    with pytest.raises(integrations.IntegrationError, match="敏感"):
        integrations.read_github(tmp_path, ".env", repository="owner/repo")

    session_type = _patch_session(
        monkeypatch,
        _Response(302, location="https://evil.example.test/leak"),
    )
    with pytest.raises(integrations.IntegrationError, match="重定向目标") as error:
        integrations.read_github(tmp_path, "README.md", repository="owner/repo")
    assert "ghp-private-token" not in str(error.value)
    assert len(session_type.instances[0].calls) == 1


def test_read_github_rejects_decoded_parent_path_and_preserves_branch_ref(tmp_path: Path, monkeypatch):
    with pytest.raises(integrations.IntegrationError, match="跳转"):
        integrations.read_github(tmp_path, "%2e%2e/private.txt", repository="owner/repo")
    assert integrations.parse_github_url(
        "https://github.com/owner/repo/blob/release%2F2025/%2e%2e/private.txt"
    ) is None

    payload = {
        "type": "file",
        "path": "README.md",
        "size": 2,
        "encoding": "base64",
        "content": base64.b64encode(b"ok").decode(),
    }
    session_type = _patch_session(monkeypatch, _Response(200, payload))
    result = integrations.read_github(
        tmp_path,
        "README.md",
        repository="owner/repo",
        ref="release/2025/q4",
    )
    assert result["content"] == "ok"
    assert session_type.instances[0].calls[0][1]["params"] == {"ref": "release/2025/q4"}


def test_import_local_path_rejects_unc_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(integrations.IntegrationError, match="UNC"):
        integrations.import_local_path(workspace, r"\\server\share\vault")


def test_bootstrap_registers_search_jobs_with_integrations():
    from agent_coach.bootstrap import build_registry

    names = {spec.name for spec in build_registry().specs([])}
    assert {"read_github", "search_jobs"}.issubset(names)


def test_fetch_url_delegates_github_file_to_api(monkeypatch, tmp_path: Path):
    registry = ToolRegistry()
    register_builtin_tools(registry)
    context = ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=None,
    )
    seen = {}

    def fake_read(workspace, path, **kwargs):
        seen.update({"workspace": workspace, "path": path, **kwargs})
        return {"ok": True, "kind": "file", "content": "private text", "truncated": False}

    monkeypatch.setattr(integrations, "read_github", fake_read)
    result = registry.get("fetch_url").handler(
        {"url": "https://github.com/owner/repo/blob/main/docs/guide.md"}, context
    )
    payload = json.loads(result.content)
    assert payload["content"] == "private text"
    assert seen["workspace"] == tmp_path
    assert seen["repository"] == "owner/repo"
    assert seen["path"] == "docs/guide.md"
    assert seen["ref"] == "main"


def test_import_local_path_returns_workspace_relative_manifest_and_skips_unsafe_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "Obsidian Vault"
    (source / "notes").mkdir(parents=True)
    (source / ".obsidian").mkdir()
    (source / "notes" / "first.md").write_text("first", encoding="utf-8")
    (source / "notes" / "second.txt").write_text("second", encoding="utf-8")
    (source / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (source / "image.png").write_bytes(b"\x89PNG\x00binary")
    (source / "archive.bin").write_bytes(b"binary")

    result = integrations.import_local_path(workspace, source)
    assert result["ok"] is True
    assert result["truncated"] is False
    assert any(item.endswith("/Obsidian Vault/notes/first.md") for item in result["imported"])
    assert any(item.endswith("/Obsidian Vault/notes/second.txt") for item in result["imported"])
    assert any(item.endswith("/Obsidian Vault/image.png") for item in result["imported"])
    assert all(not Path(item).is_absolute() for item in result["imported"])
    imported_root = workspace / result["manifest"]["destination"]
    assert (imported_root / "Obsidian Vault/notes/first.md").read_text(encoding="utf-8") == "first"
    image_row = next(row for row in result["manifest"]["imported"] if row["source_path"] == "image.png")
    assert image_row["kind"] == "binary"
    assert "未在导入阶段解析" in image_row["note"]
    reasons = {item["reason"] for item in result["skipped"]}
    assert "excluded_directory" in reasons
    assert "sensitive_path" in reasons
    assert "unsupported_binary_or_extension" in reasons


def test_import_local_path_rejects_links_overlap_and_marks_limits(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "notes"
    source.mkdir()
    (source / "a.md").write_text("a", encoding="utf-8")
    (source / "b.md").write_text("b", encoding="utf-8")
    assert integrations.import_local_path(workspace, source, max_files=1)["truncated"] is True
    with pytest.raises(integrations.IntegrationError, match="绝对路径"):
        integrations.import_local_path(workspace, "notes")
    workspace_source = workspace / "inside"
    workspace_source.mkdir()
    (workspace_source / "a.md").write_text("a", encoding="utf-8")
    with pytest.raises(integrations.IntegrationError, match="互相包含"):
        integrations.import_local_path(workspace, workspace_source, destination=workspace)

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = source / "escape.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(integrations.IntegrationError, match="符号链接"):
        integrations.import_local_path(workspace, source)

    clean_source = tmp_path / "clean-notes"
    clean_source.mkdir()
    (clean_source / "note.md").write_text("note", encoding="utf-8")
    source_link = tmp_path / "source-link"
    try:
        source_link.symlink_to(clean_source, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(integrations.IntegrationError, match="符号链接"):
        integrations.import_local_path(workspace, source_link)

    parent_link = tmp_path / "parent-link"
    try:
        parent_link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(integrations.IntegrationError, match="符号链接"):
        integrations.import_local_path(workspace, parent_link / "clean-notes")

    destination_link = workspace / "destination-link"
    destination_target = workspace / "destination-target"
    destination_target.mkdir()
    try:
        destination_link.symlink_to(destination_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable in this test environment")
    with pytest.raises(integrations.IntegrationError, match="符号链接"):
        integrations.import_local_path(workspace, clean_source, destination=destination_link)


def test_parse_github_url_accepts_safe_file_forms():
    assert integrations.parse_github_url(
        "https://github.com/owner/repo/blob/main/docs/readme.md"
    ) == ("owner/repo", "docs/readme.md", "main")
    assert integrations.parse_github_url(
        "https://raw.githubusercontent.com/owner/repo/main/docs/readme.md"
    ) == ("owner/repo", "docs/readme.md", "main")
    assert integrations.parse_github_url("https://github.com/owner/repo") == (
        "owner/repo",
        "",
        None,
    )
    assert integrations.parse_github_url("https://evil.example.test/owner/repo") is None


def test_search_jobs_keeps_only_real_platform_links_and_reports_empty(monkeypatch):
    from agent_coach.legacy.tools import job_sources

    monkeypatch.setattr(
        job_sources,
        "search",
        lambda query, platform, n: [
            {
                "platform": "BOSS 直聘",
                "url": "https://example.com/job_detail/wrong",
                "title": "伪平台",
                "text": "must be filtered",
            },
            {
                "platform": "BOSS 直聘",
                "url": "https://jobs.zhipin.com/job_detail/real",
                "title": "Python 后端",
                "text": "公开摘要",
                "method": "公开搜索索引",
            },
        ],
    )
    result = integrations.search_jobs("Python 后端", platform="boss", limit=2)
    assert result["ok"] is True
    assert [row["url"] for row in result["results"]] == ["https://jobs.zhipin.com/job_detail/real"]
    assert result["partial"] is True

    monkeypatch.setattr(job_sources, "search", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("平台需要登录")))
    unavailable = integrations.search_jobs("Python", platform="boss")
    assert unavailable["ok"] is False
    assert unavailable["results"] == []
    assert "平台需要登录" in unavailable["message"]


def test_search_jobs_tool_is_deterministic_and_does_not_call_model(monkeypatch, tmp_path: Path):
    from agent_coach.legacy.tools import job_sources

    calls = []

    def fake_search(query, platform, n):
        calls.append((query, platform, n))
        return [
            {
                "platform": "猎聘",
                "url": "https://www.liepin.com/job/123",
                "title": "后端工程师",
                "text": "公开摘要",
            }
        ]

    monkeypatch.setattr(job_sources, "search", fake_search)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    context = ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=object(),
    )
    result = registry.get("search_jobs").handler(
        {"query": "后端", "platform": "liepin", "limit": 1}, context
    )
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert calls == [("后端", "liepin", 1)]


def test_import_local_path_accepts_utf8_probe_split_and_bom_text(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "documents"
    source.mkdir()
    # 8191 ASCII bytes leave exactly one byte of the three-byte Chinese code
    # point in the bounded probe.  Incremental decoding must defer final
    # validation until the rest of the file is copied.
    (source / "long.md").write_text("a" * 8191 + "中\n", encoding="utf-8")
    (source / "resume.txt").write_bytes(
        b"\xff\xfe" + "中文简历\n".encode("utf-16-le")
    )
    (source / "legacy.txt").write_bytes("中文旧简历\n".encode("gb18030"))

    result = integrations.import_local_path(workspace, source)

    assert result["manifest"]["imported_count"] == 3
    imported_root = workspace / result["manifest"]["destination"]
    assert (imported_root / "documents/long.md").read_text(encoding="utf-8").endswith("中\n")
    assert (imported_root / "documents/resume.txt").read_bytes().startswith(b"\xff\xfe")


def test_import_local_path_rechecks_per_file_limit_when_source_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "documents"
    source.mkdir()
    file_path = source / "growing.md"
    file_path.write_text("small", encoding="utf-8")

    original_copy = integrations._copy_bounded
    seen_limit = {}

    def grow_before_copy(source_path, target_path, limit):
        seen_limit["value"] = limit
        source_path.write_bytes(b"x" * (integrations.MAX_IMPORT_FILE_BYTES + 1))
        return original_copy(source_path, target_path, limit)

    monkeypatch.setattr(integrations, "_copy_bounded", grow_before_copy)
    with pytest.raises(integrations.IntegrationError, match="超过大小限制"):
        integrations.import_local_path(workspace, source)
    assert seen_limit["value"] == integrations.MAX_IMPORT_FILE_BYTES
