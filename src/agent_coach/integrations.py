"""Safe integrations used by the job-coaching application.

This module is intentionally separate from the HTTP service.  The service can
call the small token and import APIs for explicit UI actions, while the agent
only receives the read-only GitHub tools registered by ``tools.py``.  GitHub
credentials are loaded for each operation, never captured by a model/router
closure, and are sent only to the fixed ``api.github.com`` origin.
The sole environment compatibility path is the explicitly named
``AGENT_COACH_GITHUB_TOKEN`` variable; Git credential helpers are never read.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import codecs
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
import uuid

import requests

from .legacy.tools.credentials import environment_github_token, github_token_scope
from .settings import (
    MAX_API_KEY_BYTES,
    MAX_PROTECTED_RECORD_BYTES,
    SettingsError,
    protect_key,
    unprotect_key,
)


GITHUB_API_ROOT = "https://api.github.com"
GITHUB_TOKEN_FILENAME = "github-token.key"
GITHUB_TOKEN_DISABLED_FILENAME = "github-token.disabled"
MAX_GITHUB_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_GITHUB_CONTENT_BYTES = 2 * 1024 * 1024
MAX_GITHUB_REDIRECTS = 3
MAX_GITHUB_TIMEOUT = 60.0
MAX_GITHUB_PAGE = 10_000
MAX_GITHUB_PER_PAGE = 100
MAX_IMPORT_FILES = 1000
MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024
MAX_IMPORT_TOTAL_BYTES = 20 * 1024 * 1024
MAX_IMPORT_DIRECTORY_ENTRIES = 4096

_GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_CREDENTIAL_QUERY_KEYS = frozenset(
    {"token", "access_token", "api_key", "apikey", "key", "auth", "authorization"}
)
_GITHUB_RESERVED_PROFILE_ROUTES = frozenset(
    {
        "about",
        "collections",
        "dashboard",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "new",
        "notifications",
        "organizations",
        "pricing",
        "pulls",
        "security",
        "settings",
        "sponsors",
        "topics",
        "trending",
    }
)
_GITHUB_REPOSITORY_LIST_QUERY_KEYS = frozenset({"page", "per_page"})
_BLOCKED_IMPORT_NAMES = frozenset(
    {
        ".agent-coach",
        ".git",
        ".github-workflows",
        ".obsidian",
        ".ssh",
        "__pycache__",
        ".cache",
        "cache",
        "credential",
        "credentials",
        "key",
        "keys",
        "private",
        "secret",
        "secrets",
        "token",
        "tokens",
        "node_modules",
    }
)
_BLOCKED_IMPORT_RE = re.compile(
    r"(?:^|[._-])(?:credential|credentials|secret|secrets|token|tokens|private|password|passwd|api[_-]?key)(?=$|[._-])",
    re.IGNORECASE,
)
_ENV_RE = re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE)
_TEXT_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".conf",
        ".cpp",
        ".css",
        ".csv",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".log",
        ".md",
        ".markdown",
        ".mjs",
        ".py",
        ".ps1",
        ".rst",
        ".rs",
        ".sh",
        ".sql",
        ".svg",
        ".swift",
        ".tex",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
        ".zsh",
    }
)
_BINARY_EXTENSIONS = frozenset(
    {
        ".bmp",
        ".doc",
        ".docx",
        ".gif",
        ".heic",
        ".jpeg",
        ".jpg",
        ".odg",
        ".odp",
        ".ods",
        ".odt",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".tif",
        ".tiff",
        ".webp",
        ".xls",
        ".xlsx",
    }
)


class IntegrationError(ValueError):
    """A safe, user-facing integration validation or transport error."""


def _workspace_root(workspace: str | Path) -> Path:
    raw_root = Path(workspace).expanduser()
    if _is_unc_path(raw_root):
        raise IntegrationError("workspace 不允许 UNC 远程路径")
    if raw_root.exists() and _is_reparse_or_symlink(raw_root):
        raise IntegrationError("workspace 不能是符号链接或目录联接")
    root = raw_root.resolve(strict=False)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise IntegrationError("workspace 不是安全目录")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_dir(workspace: str | Path) -> Path:
    directory = _workspace_root(workspace) / ".agent-coach"
    if directory.exists() and _is_reparse_or_symlink(directory):
        raise IntegrationError("workspace 设置目录不能是符号链接或目录联接")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _token_path(workspace: str | Path) -> Path:
    return _settings_dir(workspace) / GITHUB_TOKEN_FILENAME


def _token_disabled_path(workspace: str | Path) -> Path:
    return _settings_dir(workspace) / GITHUB_TOKEN_DISABLED_FILENAME


def _reject_record_link(path: Path) -> None:
    try:
        if path.is_symlink() or _is_reparse_or_symlink(path):
            raise IntegrationError("workspace Token 记录不能是符号链接或目录联接")
    except FileNotFoundError:
        return


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_token(token: Any) -> str:
    if not isinstance(token, str) or not token.strip():
        raise IntegrationError("GitHub Token 不能为空")
    clean = token.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in clean):
        raise IntegrationError("GitHub Token 包含非法控制字符")
    if len(clean.encode("utf-8")) > MAX_API_KEY_BYTES:
        raise IntegrationError("GitHub Token 超出长度限制")
    return clean


def save_github_token(workspace: str | Path, token: str) -> dict[str, Any]:
    """Encrypt and save a GitHub token in this workspace only."""

    clean = _validate_token(token)
    token_path = _token_path(workspace)
    disabled_path = _token_disabled_path(workspace)
    try:
        _reject_record_link(token_path)
        _reject_record_link(disabled_path)
        encrypted = protect_key(clean)
        _atomic_write(token_path, encrypted)
        disabled_path.unlink(missing_ok=True)
    except (SettingsError, OSError) as exc:
        raise IntegrationError("GitHub Token 保存失败") from exc
    return {"saved": True, "has_token": True}


def clear_github_token(workspace: str | Path) -> dict[str, Any]:
    """Remove the workspace's encrypted GitHub token, if present."""

    path = _token_path(workspace)
    disabled_path = _token_disabled_path(workspace)
    try:
        _reject_record_link(path)
        _reject_record_link(disabled_path)
        # This marker is intentionally non-secret.  It distinguishes an
        # explicit UI clear from a workspace that has never stored a token, so
        # the legacy CLI environment fallback cannot unexpectedly re-enable
        # GitHub access after a clear.
        _atomic_write(disabled_path, b"cleared\n")
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise IntegrationError("GitHub Token 清除失败") from exc
    return {"cleared": True, "has_token": False}


def load_github_token(workspace: str | Path) -> str | None:
    """Load a valid token for an internal, explicitly scoped operation.

    Corrupt or unavailable DPAPI records are treated as absent.  The token is
    never included in an exception or public status payload.
    """

    path = _token_path(workspace)
    disabled_path = _token_disabled_path(workspace)
    try:
        _reject_record_link(path)
        _reject_record_link(disabled_path)
        if not path.is_file() or path.is_symlink():
            if disabled_path.is_file():
                return None
            return environment_github_token()
        if path.stat().st_size > MAX_PROTECTED_RECORD_BYTES:
            raise SettingsError("GitHub Token 记录超出大小限制")
        value = unprotect_key(path.read_bytes())
        return _validate_token(value)
    except (OSError, SettingsError, IntegrationError, ValueError):
        # A damaged saved record is unavailable; preserve CLI compatibility by
        # falling back only when the workspace was not explicitly cleared.
        try:
            if disabled_path.is_file():
                return None
        except OSError:
            return None
        return environment_github_token()


def get_github_token(workspace: str | Path) -> str | None:
    """Compatibility alias for internal callers that need workspace lookup."""

    return load_github_token(workspace)


def github_token_status(workspace: str | Path) -> dict[str, Any]:
    """Return token presence without returning the token itself."""

    return {"has_token": load_github_token(workspace) is not None}


@contextmanager
def github_token_context(workspace: str | Path) -> Iterator[None]:
    """Install this workspace's token in a ContextVar for one operation."""

    with github_token_scope(load_github_token(workspace)):
        yield


def _safe_limit(value: Any, *, default: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise IntegrationError(f"{name} 超出允许范围")
    return value


def _safe_timeout(value: Any, *, default: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise IntegrationError("timeout 无效")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise IntegrationError("timeout 无效") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > maximum:
        raise IntegrationError("timeout 超出允许范围")
    return timeout


def _normalize_repository(repository: Any) -> str:
    if not isinstance(repository, str) or not repository.strip():
        raise IntegrationError("repository 必须是 owner/repo")
    value = repository.strip().strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not _GITHUB_REPOSITORY_RE.fullmatch(value):
        raise IntegrationError("repository 必须是 owner/repo")
    owner, repo = value.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."}:
        raise IntegrationError("repository 无效")
    return f"{owner}/{repo}"


def _normalize_github_username(username: Any) -> str:
    """Validate a GitHub login before putting it into an API path."""

    if not isinstance(username, str) or not username.strip():
        raise IntegrationError("GitHub 用户名不能为空")
    value = username.strip()
    if not _GITHUB_USERNAME_RE.fullmatch(value):
        raise IntegrationError("GitHub 用户名格式无效")
    return value


def _query_has_credential_key(query: str) -> bool:
    keys = {
        unquote(key).strip().casefold().replace("-", "_")
        for key in parse_qs(query, keep_blank_values=True)
    }
    return bool(keys & _CREDENTIAL_QUERY_KEYS)


def _normalize_repo_path(path: Any) -> str:
    if path is None:
        return ""
    if not isinstance(path, str):
        raise IntegrationError("path 必须是字符串")
    value = path.strip().replace("\\", "/").strip("/")
    # Tool callers and GitHub web URLs may provide percent-encoded path
    # segments.  Decode repeatedly before validation so encoded ``..`` or
    # encoded slashes cannot evade the traversal check.
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    if any(ord(char) < 0x20 for char in value):
        raise IntegrationError("path 包含非法控制字符")
    parts = [part for part in value.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise IntegrationError("GitHub path 不允许目录跳转")
    for part in parts:
        folded = part.casefold()
        if (
            folded in {".git", ".ssh", ".env", "credentials", "credential", "secret", "secrets", "token", "tokens", "private"}
            or _ENV_RE.fullmatch(part)
            or _BLOCKED_IMPORT_RE.search(part)
            or folded.endswith((".pem", ".key", ".p12", ".pfx"))
        ):
            raise IntegrationError(f"GitHub path 含敏感路径：{part}")
    return "/".join(parts)


def _repository_and_path(repository: Any, path: Any) -> tuple[str, str]:
    if repository is None:
        raw = path if isinstance(path, str) else ""
        parts = raw.replace("\\", "/").strip("/").split("/")
        if len(parts) < 2:
            raise IntegrationError("请提供 repository=owner/repo")
        repository, path = "/".join(parts[:2]), "/".join(parts[2:])
    return _normalize_repository(repository), _normalize_repo_path(path)


def _github_endpoint(repository: str, path: str) -> str:
    suffix = f"/repos/{quote(repository, safe='/')}/contents"
    if path:
        suffix += "/" + quote(path, safe="/")
    return GITHUB_API_ROOT + suffix


def _response_bytes(response: Any, limit: int) -> bytes:
    headers = getattr(response, "headers", {}) or {}
    try:
        content_length = headers.get("Content-Length")
        if content_length is not None and int(content_length) > limit:
            raise IntegrationError("GitHub 响应超过大小限制")
    except (TypeError, ValueError):
        pass
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        try:
            chunks = iterator(chunk_size=64 * 1024)
        except (TypeError, AttributeError):
            chunks = [getattr(response, "content", b"")]
    else:
        chunks = [getattr(response, "content", b"")]
    output: list[bytes] = []
    size = 0
    for chunk in chunks:
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        data = bytes(chunk)
        if size + len(data) > limit:
            raise IntegrationError("GitHub 响应超过大小限制")
        output.append(data)
        size += len(data)
    return b"".join(output)


def _redirect_target(current: str, location: Any) -> str:
    if not isinstance(location, str) or not location.strip():
        raise IntegrationError("GitHub 重定向缺少目标")
    target = urljoin(current, location)
    try:
        parsed = urlsplit(target)
        port = parsed.port
    except ValueError as exc:
        raise IntegrationError("GitHub 重定向目标不受信任") from exc
    # A redirect is allowed only inside the API origin.  Reject credential-
    # looking query keys as an additional guard against accepting a URL that
    # asks the client to carry a secret in the address bar or access logs.
    if _query_has_credential_key(parsed.query):
        raise IntegrationError("GitHub 重定向不得携带凭据")
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "api.github.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise IntegrationError("GitHub 重定向目标不受信任")
    return target


def _github_json(
    endpoint: str,
    *,
    token: str | None,
    params: Mapping[str, Any] | None,
    max_bytes: int,
    timeout: float,
) -> tuple[dict[str, Any] | list[Any], int, int]:
    session = requests.Session()
    try:
        session.trust_env = False
        headers = getattr(session, "headers", None)
        if headers is not None:
            headers.clear()
            headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "agent-coach/1.0",
                }
            )
            if token:
                headers["Authorization"] = f"Bearer {token}"
        current = endpoint
        redirects = 0
        while True:
            try:
                response = session.get(
                    current,
                    params=params if redirects == 0 else None,
                    allow_redirects=False,
                    stream=True,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise IntegrationError("GitHub 请求失败") from exc
            status = int(getattr(response, "status_code", 0))
            if 300 <= status < 400:
                location = (getattr(response, "headers", {}) or {}).get("Location")
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                redirects += 1
                if redirects > MAX_GITHUB_REDIRECTS:
                    raise IntegrationError("GitHub 重定向次数过多")
                current = _redirect_target(current, location)
                continue
            body = _response_bytes(response, max_bytes)
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if status == 401:
                raise IntegrationError(
                    "GitHub 认证失败：Token 无效或已过期，请在工作区设置页重新保存 GitHub Token"
                )
            if status == 403:
                raise IntegrationError(
                    "GitHub 请求受限或无权访问：请检查 Token 权限，或稍后重试"
                )
            if status == 404:
                raise IntegrationError("GitHub 仓库或路径不存在，或当前 Token 无权访问")
            if status >= 400:
                raise IntegrationError(f"GitHub 请求失败（HTTP {status}）")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrationError("GitHub 响应格式无效") from exc
            if not isinstance(payload, (dict, list)):
                raise IntegrationError("GitHub 响应格式无效")
            return payload, status, len(body)
    finally:
        close_session = getattr(session, "close", None)
        if callable(close_session):
            close_session()


def read_github(
    workspace: str | Path,
    path: str,
    *,
    repository: str | None = None,
    ref: str | None = None,
    max_bytes: int = MAX_GITHUB_CONTENT_BYTES,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Read a GitHub file, README, or directory through the GitHub API.

    ``repository`` is ``owner/repo``.  For compatibility, callers may omit it
    and pass ``owner/repo/path`` as ``path``.  Only ``api.github.com`` is ever
    contacted with the workspace token.
    """

    repository, path = _repository_and_path(repository, path)
    max_bytes = _safe_limit(
        max_bytes,
        default=MAX_GITHUB_CONTENT_BYTES,
        maximum=MAX_GITHUB_CONTENT_BYTES,
        name="max_bytes",
    )
    timeout = _safe_timeout(timeout, default=20.0, maximum=MAX_GITHUB_TIMEOUT)
    if isinstance(ref, str):
        ref = unquote(ref.strip())
        if any(ord(char) < 0x20 for char in ref) or len(ref.encode("utf-8")) > 512:
            raise IntegrationError("ref 无效")
    elif ref is not None:
        raise IntegrationError("ref 必须是字符串")
    use_readme_endpoint = path.casefold() in {"readme", "readme.md"}
    endpoint = (
        f"{GITHUB_API_ROOT}/repos/{quote(repository, safe='/')}/readme"
        if use_readme_endpoint
        else _github_endpoint(repository, path)
    )
    token = load_github_token(workspace)
    params = {"ref": ref} if ref else None
    with github_token_scope(token):
        payload, status, response_bytes = _github_json(
            endpoint,
            token=token,
            params=params,
            max_bytes=MAX_GITHUB_RESPONSE_BYTES,
            timeout=timeout,
        )

    if isinstance(payload, list):
        entries: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            try:
                safe_path = _normalize_repo_path(str(item.get("path") or name))
            except IntegrationError:
                continue
            entries.append(
                {
                    "name": name,
                    "path": safe_path,
                    "type": str(item.get("type") or ""),
                    "size": int(item.get("size") or 0),
                }
            )
        truncated = len(entries) > 500
        entries = entries[:500]
        return {
            "ok": True,
            "kind": "directory",
            "repository": repository,
            "path": path,
            "ref": ref,
            "entries": entries,
            "truncated": truncated,
            "size_bytes": response_bytes,
            "status": status,
        }

    item_type = str(payload.get("type") or "file")
    actual_path = _normalize_repo_path(str(payload.get("path") or path))
    if item_type == "dir":
        return {
            "ok": True,
            "kind": "directory",
            "repository": repository,
            "path": actual_path,
            "ref": ref,
            "entries": [],
            "truncated": False,
            "size_bytes": response_bytes,
            "status": status,
        }
    encoded = str(payload.get("content") or "").replace("\n", "").replace("\r", "")
    try:
        raw = base64.b64decode(encoded, validate=True) if encoded else b""
    except (ValueError, base64.binascii.Error) as exc:
        raise IntegrationError("GitHub 文件内容格式无效") from exc
    file_size = int(payload.get("size") or len(raw))
    truncated = len(raw) > max_bytes or file_size > max_bytes
    raw = raw[:max_bytes]
    try:
        text = raw.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = ""
        binary = True
    if b"\x00" in raw:
        binary = True
        text = ""
    return {
        "ok": True,
        "kind": "file",
        "repository": repository,
        "path": actual_path,
        "ref": ref,
        "content": text,
        "binary": binary,
        "truncated": truncated,
        "size_bytes": file_size,
        "response_bytes": response_bytes,
        "status": status,
    }


def _require_github_token(workspace: str | Path) -> str:
    """Load the current workspace token with an actionable missing-token error."""

    token = load_github_token(workspace)
    if token is None:
        raise IntegrationError(
            "当前操作需要 GitHub Token；请先在工作区设置页保存 Token，"
            "并确认它至少有读取目标私有仓库的权限"
        )
    return token


def _github_identity_row(payload: Any) -> dict[str, Any]:
    """Project the /user response to a small, non-secret identity record."""

    if not isinstance(payload, dict):
        raise IntegrationError("GitHub 身份响应格式无效")
    login = payload.get("login")
    try:
        login = _normalize_github_username(login)
    except IntegrationError as exc:
        raise IntegrationError("GitHub 身份响应缺少有效 login") from exc
    row: dict[str, Any] = {"login": login}
    account_id = payload.get("id")
    if isinstance(account_id, int) and not isinstance(account_id, bool) and account_id >= 0:
        row["id"] = account_id
    for key in ("name", "company", "blog", "location", "type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            row[key] = value.strip()[:512]
    return row


def github_identity(
    workspace: str | Path,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Return the authenticated GitHub identity without exposing its Token."""

    timeout = _safe_timeout(timeout, default=20.0, maximum=MAX_GITHUB_TIMEOUT)
    token = _require_github_token(workspace)
    with github_token_scope(token):
        payload, status, response_bytes = _github_json(
            f"{GITHUB_API_ROOT}/user",
            token=token,
            params=None,
            max_bytes=MAX_GITHUB_RESPONSE_BYTES,
            timeout=timeout,
        )
    identity = _github_identity_row(payload)
    return {
        "ok": True,
        "kind": "identity",
        "identity": identity,
        "status": status,
        "response_bytes": response_bytes,
    }


def _github_repository_row(item: Any) -> dict[str, Any] | None:
    """Keep only bounded, non-credential repository metadata from GitHub."""

    if not isinstance(item, dict):
        return None
    full_name = item.get("full_name")
    if not isinstance(full_name, str):
        owner = item.get("owner")
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        name = item.get("name")
        if isinstance(owner_login, str) and isinstance(name, str):
            full_name = f"{owner_login}/{name}"
    try:
        full_name = _normalize_repository(full_name)
    except IntegrationError:
        return None
    owner_login = full_name.split("/", 1)[0]
    row: dict[str, Any] = {
        "full_name": full_name,
        "name": full_name.split("/", 1)[1],
        "owner": owner_login,
        "private": bool(item.get("private")),
        "visibility": str(item.get("visibility") or ("private" if item.get("private") else "public")),
    }
    for key in ("description", "default_branch", "language", "updated_at", "pushed_at"):
        value = item.get(key)
        if isinstance(value, str) and value:
            row[key] = value[:4096] if key == "description" else value[:512]
    for key in ("archived", "disabled", "fork"):
        if isinstance(item.get(key), bool):
            row[key] = item[key]
    for source_key, result_key in (
        ("stargazers_count", "stargazers_count"),
        ("forks_count", "forks_count"),
        ("open_issues_count", "open_issues_count"),
    ):
        value = item.get(source_key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            row[result_key] = value
    for key in ("html_url", "url"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("https://github.com/"):
            row[key] = value[:2048]
    return row


def list_github_repositories(
    workspace: str | Path,
    *,
    username: str | None = None,
    page: int = 1,
    per_page: int = 30,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """List public repositories or the authenticated user's private repositories.

    When ``username`` matches the current Token's login, the authenticated
    ``/user/repos`` endpoint is used and private repositories are included.
    A different username always uses ``/users/{username}/repos`` so a caller
    can never accidentally receive the authenticated user's unrelated private
    repositories.  Each invocation loads the current workspace Token again;
    changing the setting therefore takes effect on the next call.
    """

    if username is not None:
        username = _normalize_github_username(username)
    page = _safe_limit(page, default=1, maximum=MAX_GITHUB_PAGE, name="page")
    per_page = _safe_limit(
        per_page,
        default=30,
        maximum=MAX_GITHUB_PER_PAGE,
        name="per_page",
    )
    timeout = _safe_timeout(timeout, default=20.0, maximum=MAX_GITHUB_TIMEOUT)
    token = load_github_token(workspace)
    authenticated_login: str | None = None
    source: str
    if username is None:
        if token is None:
            raise IntegrationError(
                "当前操作需要 GitHub Token；请先在工作区设置页保存 Token，"
                "并确认它至少有读取目标私有仓库的权限"
            )
        source = "authenticated_user"
    elif token is None:
        source = "public_user"
    else:
        # Resolve the login once before choosing the endpoint.  This keeps a
        # request for another user's repositories from being served by
        # /user/repos, which would otherwise reveal the Token owner's private
        # repository names to the model.
        with github_token_scope(token):
            identity_payload, _identity_status, _identity_bytes = _github_json(
                f"{GITHUB_API_ROOT}/user",
                token=token,
                params=None,
                max_bytes=MAX_GITHUB_RESPONSE_BYTES,
                timeout=timeout,
            )
        authenticated_login = _github_identity_row(identity_payload)["login"]
        source = (
            "authenticated_user"
            if authenticated_login.casefold() == username.casefold()
            else "public_user"
        )

    if source == "authenticated_user":
        endpoint = f"{GITHUB_API_ROOT}/user/repos"
        params: dict[str, Any] = {
            "visibility": "all",
            "affiliation": (
                "owner"
                if username is not None
                else "owner,collaborator,organization_member"
            ),
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
            "page": page,
        }
    else:
        # username is guaranteed non-None in this branch.
        assert username is not None
        endpoint = f"{GITHUB_API_ROOT}/users/{quote(username, safe='')}/repos"
        params = {
            "type": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
            "page": page,
        }

    with github_token_scope(token):
        payload, status, response_bytes = _github_json(
            endpoint,
            token=token,
            params=params,
            max_bytes=MAX_GITHUB_RESPONSE_BYTES,
            timeout=timeout,
        )
    if not isinstance(payload, list):
        raise IntegrationError("GitHub 仓库列表响应格式无效")

    repositories: list[dict[str, Any]] = []
    for item in payload:
        row = _github_repository_row(item)
        if row is None:
            continue
        if username is not None and row["owner"].casefold() != username.casefold():
            continue
        repositories.append(row)
    next_page = page + 1 if len(payload) >= per_page else None
    note = (
        "当前列表来自 workspace Token 可访问的 GitHub 仓库范围，可能包含私有仓库；"
        "private_available 不代表该 Token 可以访问账户的全部私有仓库。"
        if source == "authenticated_user"
        else "当前仅返回公开仓库；未配置 Token 或 Token 身份与目标用户不同，"
        "不能据此判断该用户没有私有仓库。"
    )
    return {
        "ok": True,
        "kind": "repositories",
        "username": username,
        "authenticated_login": authenticated_login,
        "source": source,
        "private_available": source == "authenticated_user" and token is not None,
        "note": note,
        "page": page,
        "per_page": per_page,
        "next_page": next_page,
        "count": len(repositories),
        "repositories": repositories,
        "status": status,
        "response_bytes": response_bytes,
    }


def search_jobs(query: str, *, platform: str = "boss", limit: int = 6) -> dict[str, Any]:
    """Search a supported public recruitment index without model synthesis."""

    if not isinstance(query, str) or not query.strip():
        raise IntegrationError("query 不能为空")
    if len(query.encode("utf-8")) > 4096:
        raise IntegrationError("query 超出长度限制")
    if not isinstance(platform, str) or platform not in {"boss", "liepin", "zhilian"}:
        raise IntegrationError("platform 不受支持")
    limit = _safe_limit(limit, default=6, maximum=10, name="limit")
    from .legacy.tools import job_sources

    try:
        rows = job_sources.search(query.strip(), platform=platform, n=limit)
    except ValueError as exc:
        return {
            "ok": False,
            "platform": platform,
            "results": [],
            "partial": False,
            "message": str(exc),
        }
    except Exception:
        return {
            "ok": False,
            "platform": platform,
            "results": [],
            "partial": False,
            "message": "公开招聘平台暂时无法访问，请粘贴可核实的岗位链接或正文。",
        }

    safe_rows: list[dict[str, str]] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "")
        if job_sources.platform_for(url) != platform:
            continue
        safe_rows.append(
            {
                "platform": str(item.get("platform") or platform),
                "url": url,
                "title": str(item.get("title") or "")[:500],
                "text": str(item.get("text") or "")[:4000],
                "method": str(item.get("method") or "公开搜索索引"),
            }
        )
    if not safe_rows:
        return {
            "ok": False,
            "platform": platform,
            "results": [],
            "partial": False,
            "message": "公开索引没有返回可核实的平台岗位链接；平台可能需要登录。请粘贴岗位正文或来源链接。",
        }
    return {
        "ok": True,
        "platform": platform,
        "results": safe_rows[:limit],
        "partial": len(safe_rows) < limit,
        "message": "公开搜索索引结果，仅包含可核实链接和摘要。",
    }


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise IntegrationError("无法检查导入路径") from exc
    attrs = int(getattr(info, "st_file_attributes", 0))
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)


def _reject_reparse_chain(path: Path) -> None:
    """Reject links in a lexical path before ``resolve`` can hide them."""

    # Checking the lexical path is necessary because ``Path.resolve`` follows
    # a link before callers can inspect the original final component.  Walk
    # all existing ancestors as well so a link in a parent cannot redirect an
    # otherwise ordinary-looking child into or out of the workspace.
    for candidate in (path, *path.parents):
        try:
            if _is_reparse_or_symlink(candidate):
                raise IntegrationError("路径不能经过符号链接或目录联接")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise IntegrationError("无法检查导入路径") from exc


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _relative(root: Path, candidate: Path) -> str:
    return candidate.relative_to(root).as_posix()


def _blocked_import_name(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in _BLOCKED_IMPORT_NAMES
        or _ENV_RE.fullmatch(name) is not None
        or _BLOCKED_IMPORT_RE.search(name) is not None
        or folded.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def _safe_destination(root: Path, destination: str | Path | None) -> Path:
    if destination is None:
        candidate = root / "imports"
    else:
        raw = Path(destination)
        if _is_unc_path(raw):
            raise IntegrationError("目标路径不允许 UNC 远程路径")
        if not raw.is_absolute() and any(part == ".." for part in raw.parts):
            raise IntegrationError("目标路径不能跳出 workspace")
        candidate = raw if raw.is_absolute() else root / raw
    _reject_reparse_chain(candidate)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise IntegrationError("目标路径无法解析") from exc
    if not _within(root, resolved):
        raise IntegrationError("目标路径必须位于 workspace 内")
    for parent in [resolved, *resolved.parents]:
        if parent == root.parent:
            break
        if parent.exists() and _is_reparse_or_symlink(parent):
            raise IntegrationError("目标路径不能经过符号链接或目录联接")
        if parent == root:
            break
    for part in resolved.relative_to(root).parts:
        if _blocked_import_name(part):
            raise IntegrationError(f"目标路径含敏感目录：{part}")
    return resolved


def _iter_source_files(source: Path) -> Iterator[tuple[Path, str | None]]:
    if source.is_file():
        yield source, None
        return
    stack = [source]
    while stack:
        current = stack.pop()
        try:
            # Do not materialize an arbitrarily large directory merely to
            # discover that it exceeds the per-directory limit.  We only
            # need one entry beyond the bound to reject it; sorting this
            # bounded slice keeps the old deterministic traversal order.
            entries = list(islice(current.iterdir(), MAX_IMPORT_DIRECTORY_ENTRIES + 1))
        except OSError as exc:
            raise IntegrationError("无法读取待导入目录") from exc
        if len(entries) > MAX_IMPORT_DIRECTORY_ENTRIES:
            raise IntegrationError("待导入目录条目超过限制")
        entries.sort(key=lambda item: item.name.casefold(), reverse=True)
        for item in entries:
            if _is_reparse_or_symlink(item):
                raise IntegrationError("导入源包含符号链接或目录联接，已拒绝")
            relative = item.relative_to(source).as_posix()
            if item.is_dir():
                if _blocked_import_name(item.name):
                    yield item, "excluded_directory"
                else:
                    stack.append(item)
            elif item.is_file():
                yield item, None


def _is_unc_path(path: str | Path) -> bool:
    raw = os.fspath(path)
    return raw.startswith("\\\\") or raw.startswith("//")


def _reject_sensitive_source_path(path: Path) -> None:
    for part in path.parts:
        if part in {path.anchor, path.drive, "", "."}:
            continue
        if _blocked_import_name(part):
            raise IntegrationError(f"导入源路径含敏感目录：{part}")


def _classify_import_file(path: Path) -> tuple[str | None, str | None, str | None]:
    """Return ``(kind, skip_reason, note)`` using only bounded inspection."""

    suffix = path.suffix.casefold()
    if suffix in _BINARY_EXTENSIONS:
        return "binary", None, "已按受限二进制复制，未在导入阶段解析"
    if suffix not in _TEXT_EXTENSIONS and suffix:
        return None, "unsupported_binary_or_extension", None
    try:
        with path.open("rb") as handle:
            sample = handle.read(8192)
    except OSError as exc:
        raise IntegrationError("无法读取导入文件") from exc
    # A bounded probe can end in the middle of a UTF-8 code point.  Decode
    # incrementally with ``final=False`` for files that continue past the
    # probe, so ordinary long Chinese Markdown is not mistaken for binary.
    # BOM-marked UTF-16/32 files are accepted for the same reason: the legacy
    # document readers can decode them after the bounded copy.
    if sample.startswith(
        (
            codecs.BOM_UTF8,
            codecs.BOM_UTF16_LE,
            codecs.BOM_UTF16_BE,
            codecs.BOM_UTF32_LE,
            codecs.BOM_UTF32_BE,
        )
    ):
        return "text", None, None
    if b"\x00" in sample:
        return None, "unsupported_binary", None
    try:
        size = path.stat().st_size
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        decoder.decode(sample, final=size <= len(sample))
    except UnicodeDecodeError:
        # Existing document readers are permissive about legacy Chinese text;
        # allow a valid GB18030 probe through so the bounded import can retain
        # it for that reader instead of rejecting it as binary at the gate.
        try:
            decoder = codecs.getincrementaldecoder("gb18030")("strict")
            decoder.decode(sample, final=size <= len(sample))
        except UnicodeDecodeError:
            return None, "unsupported_binary", None
    except OSError as exc:
        raise IntegrationError("无法读取导入文件元数据") from exc
    return "text", None, None


def _copy_bounded(source: Path, target: Path, limit: int) -> int:
    """Atomically copy a regular file while enforcing a live byte limit."""

    if limit < 1:
        raise IntegrationError("导入剩余空间不足")
    _reject_reparse_chain(source)
    _reject_reparse_chain(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(target)
    if target.exists() and _is_reparse_or_symlink(target):
        raise IntegrationError("目标文件不能是符号链接或目录联接")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    temporary = Path(temporary_name)
    copied = 0
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            while True:
                chunk = input_file.read(min(64 * 1024, limit - copied + 1))
                if not chunk:
                    break
                if copied + len(chunk) > limit:
                    raise IntegrationError("导入文件在复制时超过大小限制")
                output.write(chunk)
                copied += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        return copied
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def import_local_path(
    workspace: str | Path,
    path: str | Path,
    *,
    destination: str | Path | None = None,
    max_files: int = MAX_IMPORT_FILES,
    max_bytes: int = MAX_IMPORT_TOTAL_BYTES,
) -> dict[str, Any]:
    """Import a user-selected absolute file or Obsidian folder safely.

    The source must be an absolute path supplied by the human-facing UI.  The
    return value only exposes workspace-relative destination paths.
    """

    root = _workspace_root(workspace)
    source_input = Path(path)
    if not source_input.is_absolute():
        raise IntegrationError("导入源必须是人工 UI 提供的绝对路径")
    if _is_unc_path(source_input):
        raise IntegrationError("导入源不允许 UNC 远程路径")
    _reject_sensitive_source_path(source_input)
    _reject_reparse_chain(source_input)
    try:
        source = source_input.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IntegrationError("导入源不存在或无法解析") from exc
    if _is_reparse_or_symlink(source):
        raise IntegrationError("导入源不能是符号链接或目录联接")
    if not source.is_file() and not source.is_dir():
        raise IntegrationError("导入源必须是文件或目录")
    max_files = _safe_limit(max_files, default=MAX_IMPORT_FILES, maximum=MAX_IMPORT_FILES, name="max_files")
    max_bytes = _safe_limit(max_bytes, default=MAX_IMPORT_TOTAL_BYTES, maximum=MAX_IMPORT_TOTAL_BYTES, name="max_bytes")
    target_root = _safe_destination(root, destination)
    if _within(source, target_root) or _within(target_root, source):
        raise IntegrationError("导入源与目标不能互相包含")
    import_id = uuid.uuid4().hex
    import_root = target_root / import_id
    target_probe = import_root / source.name
    try:
        target_probe_resolved = target_probe.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise IntegrationError("目标路径无法解析") from exc
    if _within(source, target_probe_resolved) or _within(target_probe_resolved, source):
        raise IntegrationError("导入源与目标不能互相包含")

    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    planned_bytes = 0
    considered = 0
    truncated = False
    source_is_file = source.is_file()
    for item, directory_reason in _iter_source_files(source):
        relative_source = item.name if source_is_file else item.relative_to(source).as_posix()
        if directory_reason is not None:
            skipped.append({"source_path": relative_source, "reason": directory_reason})
            continue
        if considered >= max_files:
            truncated = True
            skipped.append({"source_path": relative_source, "reason": "max_files"})
            break
        considered += 1
        if _blocked_import_name(item.name):
            skipped.append({"source_path": relative_source, "reason": "sensitive_path"})
            continue
        try:
            size = item.stat().st_size
        except OSError as exc:
            raise IntegrationError("无法读取导入文件元数据") from exc
        if size > MAX_IMPORT_FILE_BYTES:
            skipped.append({"source_path": relative_source, "reason": "file_too_large", "bytes": size})
            continue
        if planned_bytes + size > max_bytes:
            truncated = True
            skipped.append({"source_path": relative_source, "reason": "max_bytes", "bytes": size})
            break
        kind, reason, note = _classify_import_file(item)
        if kind is None:
            skipped.append({"source_path": relative_source, "reason": reason or "unsupported_binary"})
            continue
        target = import_root / source.name / relative_source if not source_is_file else import_root / source.name
        _reject_reparse_chain(target)
        try:
            resolved_target = target.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise IntegrationError("目标文件无法解析") from exc
        if not _within(root, resolved_target):
            raise IntegrationError("导入目标逃出 workspace")
        if _blocked_import_name(target.name):
            skipped.append({"source_path": relative_source, "reason": "sensitive_path"})
            continue
        planned.append({"source": item, "target": target, "source_path": relative_source, "kind": kind, "note": note, "size": size})
        planned_bytes += size

    imported: list[dict[str, Any]] = []
    total_bytes = 0
    for plan in planned:
        actual_size = _copy_bounded(
            plan["source"],
            plan["target"],
            min(MAX_IMPORT_FILE_BYTES, max_bytes - total_bytes),
        )
        row = {
            "path": _relative(root, plan["target"]),
            "source_path": plan["source_path"],
            "bytes": actual_size,
            "kind": plan["kind"],
        }
        if plan["note"]:
            row["note"] = plan["note"]
        imported.append(row)
        total_bytes += actual_size

    manifest = {
        "source_name": source.name,
        "destination": _relative(root, import_root),
        "import_id": import_id,
        "imported": imported,
        "skipped": skipped,
        "truncated": truncated,
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "total_bytes": total_bytes,
    }
    return {
        "ok": True,
        "manifest": manifest,
        "imported": [item["path"] for item in imported],
        "skipped": skipped,
        "truncated": truncated,
    }


def parse_github_repository_list_url(
    url: str,
) -> tuple[str | None, int, int] | None:
    """Parse the two fixed GitHub repository-list API URLs used by old calls.

    This compatibility parser deliberately accepts only ``page`` and
    ``per_page`` query parameters.  It never turns an arbitrary API URL into
    a model-controlled request, and callers still route the resulting target
    through :func:`list_github_repositories` so the workspace token remains
    internal.
    """

    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "api.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        return None
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    username: str | None
    if segments == ["user", "repos"]:
        username = None
    elif len(segments) == 3 and segments[0] == "users" and segments[2] == "repos":
        try:
            username = _normalize_github_username(segments[1])
        except IntegrationError:
            return None
    else:
        return None

    if _query_has_credential_key(parsed.query):
        raise IntegrationError("GitHub URL 查询参数不得包含凭据")
    query = parse_qs(parsed.query, keep_blank_values=True)
    unknown = set(query) - _GITHUB_REPOSITORY_LIST_QUERY_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise IntegrationError(
            f"GitHub 仓库列表 URL 只允许 page/per_page 参数（不支持：{names}）"
        )

    def query_limit(name: str, default: int, maximum: int) -> int:
        values = query.get(name)
        if values is None:
            return default
        if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]):
            raise IntegrationError(f"GitHub {name} 参数无效")
        try:
            numeric = int(values[0])
        except (TypeError, ValueError) as exc:
            raise IntegrationError(f"GitHub {name} 参数无效") from exc
        return _safe_limit(numeric, default=default, maximum=maximum, name=name)

    return (
        username,
        query_limit("page", 1, MAX_GITHUB_PAGE),
        query_limit("per_page", 30, MAX_GITHUB_PER_PAGE),
    )


def parse_github_user_url(url: str) -> str | None:
    """Return a GitHub profile login from a safe one-segment profile URL."""

    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or host not in {"github.com", "www.github.com"}
        or parsed.username
        or parsed.password
        or parsed.fragment
        or _query_has_credential_key(parsed.query)
        or port not in (None, 443)
    ):
        return None
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if len(segments) != 1:
        return None
    if segments[0].casefold() in _GITHUB_RESERVED_PROFILE_ROUTES:
        return None
    try:
        return _normalize_github_username(segments[0])
    except IntegrationError:
        return None


def parse_github_url(url: str) -> tuple[str, str, str | None] | None:
    """Translate a safe GitHub web/API file URL to ``(repo, path, ref)``."""

    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or _query_has_credential_key(parsed.query)
        or port not in (None, 443)
    ):
        return None
    segments = [part for part in parsed.path.split("/") if part]
    if host in {"github.com", "www.github.com"}:
        if len(segments) < 2:
            return None
        repository = f"{segments[0]}/{segments[1]}"
        try:
            safe_repository = _normalize_repository(repository)
        except IntegrationError:
            return None
        if len(segments) == 2:
            return safe_repository, "", None
        if segments[2] in {"blob", "raw", "tree"} and len(segments) >= 5:
            try:
                safe_path = _normalize_repo_path(unquote("/".join(segments[4:])))
            except IntegrationError:
                return None
            return safe_repository, safe_path, unquote(segments[3])
        return None
    if host == "raw.githubusercontent.com" and len(segments) >= 4:
        try:
            safe_repository = _normalize_repository(f"{segments[0]}/{segments[1]}")
            safe_path = _normalize_repo_path(unquote("/".join(segments[3:])))
        except IntegrationError:
            return None
        return safe_repository, safe_path, unquote(segments[2])
    if host == "api.github.com" and len(segments) >= 4 and segments[0] == "repos":
        try:
            repository = _normalize_repository(f"{segments[1]}/{segments[2]}")
        except IntegrationError:
            return None
        if segments[3] == "contents":
            try:
                return repository, _normalize_repo_path(unquote("/".join(segments[4:]))), None
            except IntegrationError:
                return None
        if segments[3] == "readme":
            return repository, "README.md", None
    return None


__all__ = [
    "GITHUB_API_ROOT",
    "GITHUB_TOKEN_FILENAME",
    "GITHUB_TOKEN_DISABLED_FILENAME",
    "IntegrationError",
    "MAX_GITHUB_CONTENT_BYTES",
    "MAX_GITHUB_PAGE",
    "MAX_GITHUB_PER_PAGE",
    "MAX_GITHUB_RESPONSE_BYTES",
    "MAX_GITHUB_TIMEOUT",
    "MAX_IMPORT_FILE_BYTES",
    "MAX_IMPORT_DIRECTORY_ENTRIES",
    "MAX_IMPORT_FILES",
    "MAX_IMPORT_TOTAL_BYTES",
    "clear_github_token",
    "get_github_token",
    "github_token_context",
    "github_token_status",
    "github_identity",
    "import_local_path",
    "list_github_repositories",
    "load_github_token",
    "parse_github_url",
    "parse_github_repository_list_url",
    "parse_github_user_url",
    "read_github",
    "save_github_token",
    "search_jobs",
]
