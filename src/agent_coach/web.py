"""Small, local-only HTTP facade for :mod:`agent_coach`.

The web process is deliberately boring: the Python engine remains the source
of truth, HTTP handlers only validate/serialize requests, and long engine
operations run in daemon threads.  This module has no web framework or build
step so it can be used by the standalone launcher and by an HTTP test harness.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import tempfile
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import job_radar
from .bootstrap import build_engine
from .contracts import AgentSession, ToolContext
from .engine import RunResult
from .integrations import (
    clear_github_token,
    github_token_status,
    import_local_path,
    save_github_token,
)
from .model_profiles import ModelProfilesStore, normalize_profile_id
from .settings import (
    ModelSettings,
    ModelSettingsStore,
    SettingsError,
    is_local_url,
    make_draft,
)
from .legacy.core.schema import Message
from .store import SessionNotFound, StoreError, canonical_path, validate_session_id
from .tools import PathSecurityError, ToolInputError, resolve_path


APP_NAME = "agent-coach"
APP_VERSION = "0.5.0"
MAX_JSON_BODY = 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BODY = 14 * 1024 * 1024
MAX_DISCARD_BODY = 16 * 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
MAX_NOTE_BYTES = 16 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILE_ENTRIES = 500
MAX_STATIC_BYTES = 8 * 1024 * 1024
MAX_JOBS = 512
MAX_IMAGE_ATTACHMENTS = 10
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
STATIC_NAMES = {"index.html", "app.js", "styles.css"}
MODEL_TEST_TIMEOUT = 30.0
MODEL_TEST_MAX_TOKENS = 1024
DEEPSEEK_API_HOST = "api.deepseek.com"

# These messages are deliberately fixed.  Provider SDK exceptions often carry
# request headers, response bodies, or model-specific details, none of which
# belong in a desktop status message.
MODEL_TEST_MESSAGES = {
    "missing_api_key": "未提供 API Key；远程模型需要密钥。",
    "authentication_failed": "认证失败，请检查 API Key。",
    "balance_insufficient": "账户余额不足或额度已用尽，请检查服务商账单。",
    "invalid_request": "请求参数或模型名称无效，请检查协议、地址和模型。",
    "invalid_model": "模型名称无效或当前服务不提供该模型，请检查模型 ID。",
    "endpoint_failed": "API 地址或模型端点不可用，请检查地址和模型。",
    "rate_limited": "请求受到限流，请稍后重试。",
    "service_unavailable": "模型服务暂时不可用，请稍后重试。",
    "timeout": "模型连接超时，请检查网络或稍后重试。",
    "connection_failed": "无法连接模型服务，请检查地址和网络。",
    "length_truncated": "模型响应因输出长度限制被截断。",
    "tool_unsupported": "模型服务不支持工具调用，或未按要求返回探测工具。",
    "no_response": "模型未返回可用的连接探测响应。",
    "tool_call_failed": "模型已响应，但未完成连接探测。",
}
UI_THEME_VALUES = frozenset({"dark", "light"})
UI_SETTINGS_FILENAME = "ui-settings.json"
MODEL_SOURCE_FILENAME = "model-source.json"


def _safe_error(exc: BaseException, *, default: str = "请求失败") -> str:
    """Return a bounded error with obvious credential-shaped values removed."""

    text = str(exc or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[已隐藏凭据]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|password|credential|authorization)\s*[:=]\s*)[^,;\s]+",
        r"\1[已隐藏凭据]",
        text,
    )
    text = re.sub(r"(?i)sk[-_][A-Za-z0-9_-]{8,}", "[已隐藏凭据]", text)
    return (text or default)[:500]


def _ui_settings_path(workspace: Path) -> Path:
    return workspace / ".agent-coach" / UI_SETTINGS_FILENAME


def _read_ui_settings(workspace: Path) -> dict[str, str]:
    """Read the small non-sensitive UI preference envelope safely."""

    path = _ui_settings_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"theme": "dark"}
    theme = payload.get("theme") if isinstance(payload, dict) else None
    if not isinstance(theme, str) or theme not in UI_THEME_VALUES:
        theme = "dark"
    return {"theme": theme}


def _write_ui_settings(workspace: Path, payload: dict[str, str]) -> dict[str, str]:
    """Atomically persist the validated, non-sensitive UI preference envelope."""

    path = _ui_settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return dict(payload)


def _model_source_path(workspace: Path) -> Path:
    return workspace / ".agent-coach" / MODEL_SOURCE_FILENAME


def _read_model_source(workspace: Path) -> Path | None:
    """Read the non-secret config path remembered after explicit selection."""

    path = _model_source_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    candidate = payload.get("config_path") if isinstance(payload, dict) else None
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate.encode("utf-8")) > 4096:
        return None
    try:
        resolved = canonical_path(candidate)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _write_model_source(workspace: Path, config_path: Path) -> None:
    """Atomically remember an explicit YAML source without secrets."""

    path = _model_source_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"config_path": str(config_path)}, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _snapshot_file(path: Path) -> bytes | None:
    """Read a file for a small cross-store rollback snapshot."""

    try:
        return path.read_bytes() if path.is_file() else None
    except OSError as exc:
        raise SettingsError("读取模型配置失败") from exc


def _restore_file(path: Path, previous: bytes | None) -> None:
    """Restore a snapshot atomically without interpreting its contents."""

    if previous is None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise SettingsError("回滚模型配置失败") from exc
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".rollback.tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SettingsError("回滚模型配置失败") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_current_model(path: Path, provider: str, model: str) -> None:
    """Atomically persist the selected YAML target before applying it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {"provider": provider, "model": model},
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _bytes_text(value: Any, *, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{field} 超出长度限制")
    return value.strip()


def _safe_json(value: Any) -> Any:
    """Make pydantic payloads JSON-safe while dropping internal credentials."""

    if isinstance(value, dict):
        hidden = {
            "prepared",
            "api_key",
            "apikey",
            "password",
            "authorization",
            "token",
            "secret",
            "credential",
            "credentials",
            "private_key",
            "access_key",
        }
        return {str(k): _safe_json(v) for k, v in value.items() if str(k).casefold() not in hidden}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _message_payload(message: Any) -> dict[str, Any]:
    try:
        payload = message.model_dump(mode="json")
    except AttributeError:
        payload = dict(message)
    # Reasoning traces are persisted for a resumable model conversation, but
    # they are not part of the user-facing transcript.  In particular, do not
    # let a provider's hidden chain-of-thought become browser-visible merely
    # because the Message schema grew an optional reasoning_content field.
    if isinstance(payload, dict):
        payload.pop("reasoning_content", None)
        payload.pop("reasoning_provider", None)
    # Keep an image marker visible to the UI while withholding the potentially
    # multi-megabyte base64 body from every session response.  The engine's
    # persisted transcript retains the bytes for a resumed model conversation.
    content = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(content, list):
        clean: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                item = {key: value for key, value in block.items() if key not in {"data", "url"}}
                item["attached"] = bool(block.get("data") or block.get("url"))
                clean.append(item)
            else:
                clean.append(block)
        payload["content"] = clean
    return _safe_json(payload)


def _attachments(value: Any) -> list[Any] | None:
    """Validate the HTTP attachment envelope before engine execution."""

    if value is None:
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError("attachments 必须是图片路径列表")
    if len(items) > MAX_IMAGE_ATTACHMENTS:
        raise ValueError(f"一次最多附加 {MAX_IMAGE_ATTACHMENTS} 张图片")
    result: list[Any] = []
    for item in items:
        if isinstance(item, str):
            result.append(_bytes_text(item, limit=1024, field="attachment path"))
            continue
        if not isinstance(item, dict):
            raise ValueError("attachment 必须是图片路径或对象")
        path = item.get("path", item.get("file"))
        clean_path = _bytes_text(path, limit=1024, field="attachment path")
        media_type = item.get("media_type")
        if media_type is None:
            result.append({"path": clean_path})
        else:
            clean_type = _bytes_text(media_type, limit=128, field="attachment media_type")
            result.append({"path": clean_path, "media_type": clean_type})
    return result


def _pending_payload(session: AgentSession) -> dict[str, str] | None:
    pending = session.pending
    if pending is None:
        return None
    return {
        "id": str(pending.id),
        "tool_name": str(pending.tool_name),
        "summary": str(pending.summary)[:1000],
        "preview": str(pending.preview),
    }


def _result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, RunResult):
        return {
            "status": result.status,
            "message": str(result.message or result.assistant_text or "")[:4000],
            "session_id": result.session_id,
            "steps": int(result.steps),
            "exit_code": int(result.exit_code),
            "pending": (
                {
                    "id": str(result.pending.id),
                    "tool_name": str(result.pending.tool_name),
                    "summary": str(result.pending.summary)[:1000],
                    "preview": str(result.pending.preview),
                }
                if result.pending
                else None
            ),
        }
    if isinstance(result, AgentSession):
        return {"status": "completed", "message": "会话已更新", "session_id": result.id}
    if isinstance(result, dict):
        return _safe_json(result)
    return {"status": "completed", "message": str(result or "")[:4000]}


class WebService:
    """Engine plus process-local job state used by the HTTP handler."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        config_path: str | Path | None = None,
        config: str | Path | None = None,
        demo: bool = False,
        no_skills: bool = False,
        token: str | None = None,
    ) -> None:
        self.workspace = canonical_path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        selected_config = config_path if config_path is not None else config
        if selected_config is None and not demo:
            # The packaged launcher may not know the original YAML path after
            # a restart.  A path explicitly chosen by the user is safe to
            # restore from this non-secret workspace preference; legacy or
            # implicit config files are never discovered here.
            selected_config = _read_model_source(self.workspace)
        self.config_path = canonical_path(selected_config) if selected_config is not None else None
        self.demo = bool(demo)
        self.no_skills = bool(no_skills)
        self.settings_store = ModelSettingsStore(self.workspace)
        self.profiles_store = ModelProfilesStore(self.workspace)
        # An explicit YAML router remains the source of declared providers and
        # fallback chains even while a GUI profile is temporarily active.
        self._declared_router: Any | None = None
        self.settings_error: str | None = None
        try:
            self.engine = build_engine(
                self.workspace,
                config_path=self.config_path,
                demo=self.demo,
                no_skills=self.no_skills,
            )
        except Exception:
            # An old or malformed explicit YAML must not make the desktop
            # settings screen unreachable.  Reassemble the engine without a
            # router and expose only a fixed diagnostic category via /info.
            if self.demo or self.config_path is None:
                raise
            self.settings_error = "config_invalid"
            self.engine = build_engine(
                self.workspace,
                config_path=None,
                demo=False,
                no_skills=self.no_skills,
            )
        if self.config_path is not None and not self.demo and self.engine.router is not None:
            self._declared_router = self.engine.router
            try:
                _write_model_source(self.workspace, self.config_path)
            except OSError:
                # The YAML remains usable for this process; a read-only
                # workspace simply cannot persist the next launcher hint.
                pass
            # An explicit config is the initial source of truth, but a saved
            # GUI profile records the user's last deliberate selection.  Keep
            # the YAML router separately for options/fallbacks while restoring
            # that profile as the active engine router across restarts.
            active_profile = self.profiles_store.active()
            if active_profile is not None:
                self.engine.router = self._build_model_router(active_profile.settings)
                self._model_settings = active_profile.settings
                if self.profiles_store.last_error:
                    self.settings_error = self.profiles_store.last_error
        # Explicit --config and --demo retain their historical precedence.
        # When neither is selected, restore the GUI's workspace-scoped model
        # settings into the already assembled engine without requiring a
        # process restart.
        self._model_settings: ModelSettings | None = self._settings_from_router(self.engine.router)
        if not self.demo and self.config_path is None:
            saved = self.settings_store.load()
            self.settings_error = self.settings_store.last_error
            profile = None
            # A legacy settings file is migrated once.  If the legacy key is
            # corrupt, retain the old fixed error state so existing launchers
            # can still present the repair screen instead of silently hiding
            # that diagnostic behind the profile catalog.
            if self.settings_error is None:
                if self.profiles_store.meta_path.is_file():
                    profile = self.profiles_store.active()
                elif saved is not None:
                    try:
                        profile = self.profiles_store.save(
                            saved,
                            profile_id="default",
                            display_name="默认配置",
                            activate=True,
                        )
                    except Exception:
                        profile = None
            if profile is not None and self.settings_error is None:
                try:
                    self.engine.router = self._build_model_router(profile.settings)
                    self._model_settings = profile.settings
                    if self.profiles_store.last_error:
                        self.settings_error = self.profiles_store.last_error
                except Exception:
                    # Keep startup compatible with an incomplete/corrupt
                    # settings file; the UI can still repair it through POST.
                    self.engine.router = None
                    self._model_settings = profile.settings
                    self.settings_error = self.settings_error or "saved_settings_unavailable"
            elif saved is not None and self.settings_error is None:
                # A profile could not be created (for example, an unexpected
                # storage failure).  Preserve the legacy startup path.
                try:
                    self.engine.router = self._build_model_router(saved)
                    self._model_settings = saved
                except Exception:
                    self.engine.router = None
                    self._model_settings = saved
                    self.settings_error = "saved_settings_unavailable"
        self.mode = "demo" if self.demo else (
            "error" if self.settings_error else ("configured" if self.engine.router is not None else "unconfigured")
        )
        self.token = token or secrets.token_urlsafe(32)
        self.auth_token = self.token
        self._lock = threading.RLock()
        self._busy: dict[str, str] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._last_results: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _settings_from_router(router: Any) -> ModelSettings | None:
        """Extract a public-safe setting snapshot from an existing router."""

        config = getattr(router, "config", None)
        providers = getattr(config, "providers", None)
        tasks = getattr(config, "tasks", None)
        if not isinstance(providers, dict) or not providers:
            # DemoRouter has provider/model markers but no endpoint and must
            # not look like a real configured provider in the settings UI.
            return None
        target = getattr(config, "current", None)
        if target is None:
            for task_name in ("agent", "default"):
                chain = tasks.get(task_name) if isinstance(tasks, dict) else None
                if chain:
                    target = chain[0]
                    break
        if target is None:
            return None
        provider = providers.get(getattr(target, "provider", None))
        if provider is None:
            return None
        try:
            return ModelSettings(
                api_mode=str(provider.api_mode),
                base_url=str(provider.base_url).rstrip("/"),
                model=str(target.model),
                api_key=getattr(provider, "api_key", None) or None,
            )
        except Exception:
            return None

    @staticmethod
    def _build_model_router(settings: ModelSettings):
        """Build a one-provider ``ModelRouter`` from GUI settings in memory."""

        from .legacy.core.llm.registry import ModelConfig, ProviderEntry, TaskTarget
        from .legacy.core.llm.router import ModelRouter

        provider_name = "gui"
        target = TaskTarget(provider=provider_name, model=settings.model)
        provider = ProviderEntry(
            name=provider_name,
            api_mode=settings.api_mode,
            base_url=settings.base_url,
            api_key=settings.api_key,
            # Local endpoints are explicitly allowed to operate without a key;
            # remote endpoints will report a fixed missing-key test result.
            key_required=not is_local_url(settings.base_url),
            timeout=120.0,
            extra_body={},
        )
        config = ModelConfig(
            providers={provider_name: provider},
            tasks={"default": [target], "agent": [target]},
            current=target,
        )
        return ModelRouter(config)

    @staticmethod
    def _router_model_options(router: Any) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        """Expose declared provider/model choices without exposing credentials."""

        config = getattr(router, "config", None)
        providers = getattr(config, "providers", None)
        tasks = getattr(config, "tasks", None)
        if not isinstance(providers, dict) or not isinstance(tasks, dict):
            return [], None
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        for task_name, chain in tasks.items():
            if not isinstance(chain, (list, tuple)):
                continue
            for target in chain:
                provider_name = str(getattr(target, "provider", ""))
                model_name = str(getattr(target, "model", ""))
                provider = providers.get(provider_name)
                if not provider_name or not model_name or provider is None:
                    continue
                key = (provider_name, model_name)
                item = rows.setdefault(
                    key,
                    {
                        "provider": provider_name,
                        "model": model_name,
                        "api_mode": str(getattr(provider, "api_mode", "")),
                        "base_url": str(getattr(provider, "base_url", "")).rstrip("/"),
                        "has_api_key": bool(getattr(provider, "api_key", None)),
                        "tasks": [],
                    },
                )
                if str(task_name) not in item["tasks"]:
                    item["tasks"].append(str(task_name))
        current = getattr(config, "current", None)
        if current is None:
            for task_name in ("agent", "default"):
                chain = tasks.get(task_name)
                if chain:
                    current = chain[0]
                    break
        current_ref = None
        if current is not None:
            candidate = (str(getattr(current, "provider", "")), str(getattr(current, "model", "")))
            if candidate in rows:
                current_ref = {"provider": candidate[0], "model": candidate[1]}
        return sorted(rows.values(), key=lambda item: (item["provider"].casefold(), item["model"].casefold())), current_ref

    def _model_options_payload(self) -> dict[str, Any]:
        declared = self._declared_router
        source = declared if declared is not None else self.engine.router
        options, current = self._router_model_options(source)
        saved_profiles = self.profiles_store.public()
        active_profile_id = self.profiles_store.active_id()
        if active_profile_id is not None and declared is not None:
            active_profile = next(
                (row for row in saved_profiles if row.get("profile_id") == active_profile_id),
                None,
            )
            if active_profile is not None:
                # ``current`` must describe the active GUI profile rather than
                # falsely reporting the YAML router's current target.
                current = {"provider": "gui", "model": str(active_profile["model"])}
        return {
            "options": options,
            "current": current,
            # ``profiles`` is an alias kept for clients that used the
            # shorter name during the profile API rollout.
            "saved_profiles": saved_profiles,
            "profiles": saved_profiles,
            "current_profile_id": active_profile_id,
        }

    def model_options(self) -> dict[str, Any]:
        with self._lock:
            return self._model_options_payload()

    def ui_settings(self) -> dict[str, str]:
        return _read_ui_settings(self.workspace)

    def save_ui_settings(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        theme = payload.get("theme")
        if theme not in UI_THEME_VALUES:
            raise ValueError("theme 必须是 dark 或 light")
        return _write_ui_settings(self.workspace, {"theme": str(theme)})

    def select_model(self, payload: Any) -> dict[str, Any]:
        """Select a saved GUI profile or a declared YAML provider/model."""

        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        with self._lock:
            if self._busy:
                raise RuntimeError("模型设置无法在会话执行期间修改")
            if "profile_id" in payload:
                profile_id = _bytes_text(payload.get("profile_id"), limit=64, field="profile_id")
                profile_id = normalize_profile_id(profile_id)
                profile = self.profiles_store.get(profile_id)
                profile_error = self.profiles_store.last_error
                router = self._build_model_router(profile.settings)
                # Keep the legacy active-settings file synchronized for the
                # standalone launcher.  Profile metadata/key storage remains
                # the source of truth for subsequent profile switches.
                legacy_paths = (self.settings_store.settings_path, self.settings_store.key_path)
                previous_legacy = {path: _snapshot_file(path) for path in legacy_paths}
                previous_active_id = self.profiles_store.active_id()
                try:
                    self.settings_store.save(profile.settings)
                    # Change the active pointer only after validation and
                    # legacy persistence succeed, so a failed switch cannot
                    # leave a catalog pointing at an unapplied profile.
                    self.profiles_store.activate(profile_id)
                except Exception:
                    try:
                        for path, previous in previous_legacy.items():
                            _restore_file(path, previous)
                        if self.profiles_store.active_id() != previous_active_id:
                            if previous_active_id is None:
                                self.profiles_store.clear_active()
                            else:
                                self.profiles_store.activate(previous_active_id)
                    except Exception as rollback_exc:
                        raise SettingsError("模型配置切换失败且回滚失败") from rollback_exc
                    raise
                self.engine.router = router
                self.demo = False
                self._model_settings = profile.settings
                self.settings_error = profile_error
                self.mode = "error" if self.settings_error else "configured"
                return {"ok": True, **self._model_options_payload()}

            provider_name = _bytes_text(payload.get("provider"), limit=128, field="provider")
            model_name = _bytes_text(payload.get("model"), limit=512, field="model")
            # Selecting a declared item always operates on the original YAML
            # router, even after a GUI profile has replaced engine.router.
            source_router = self._declared_router or self.engine.router
            config = getattr(source_router, "config", None)
            providers = getattr(config, "providers", None)
            tasks = getattr(config, "tasks", None)
            if not isinstance(providers, dict) or not isinstance(tasks, dict):
                raise ValueError("当前没有可切换的声明式模型选项")
            declared = False
            for chain in tasks.values():
                if any(
                    str(getattr(target, "provider", "")) == provider_name
                    and str(getattr(target, "model", "")) == model_name
                    for target in (chain or [])
                ):
                    declared = True
                    break
            if not declared or provider_name not in providers:
                raise ValueError("provider/model 不在当前配置声明的模型选项中")
            from .legacy.core.llm.registry import TaskTarget

            # Persist every durable part before changing the live router.  A
            # failed current_model write or active-profile clear must leave
            # the running service and its restart state pointing at the same
            # provider/model pair.
            previous_current = config.current
            previous_router = self.engine.router
            previous_model_settings = self._model_settings
            previous_settings_error = self.settings_error
            previous_mode = self.mode
            previous_active_id = self.profiles_store.active_id()
            target_path = (
                self.config_path.parent / "current_model.json"
                if self.config_path is not None
                else None
            )
            previous_current_file = _snapshot_file(target_path) if target_path is not None else None
            try:
                if target_path is not None:
                    _write_current_model(target_path, provider_name, model_name)
                self.profiles_store.clear_active()

                # The assignments below are intentionally last: they are
                # in-memory state and cannot be allowed to get ahead of the
                # persisted selection.
                config.current = TaskTarget(provider=provider_name, model=model_name)
                self.engine.router = source_router
                self._model_settings = self._settings_from_router(source_router)
                self.settings_error = None
                self.mode = "configured"
            except Exception:
                # Restore both files and the active-profile pointer.  This is
                # best effort only after the original failure; any rollback
                # failure is surfaced as a fixed settings error below.
                try:
                    if target_path is not None:
                        _restore_file(target_path, previous_current_file)
                    if previous_active_id is not None:
                        self.profiles_store.activate(previous_active_id)
                except Exception as rollback_exc:
                    self.engine.router = previous_router
                    self._model_settings = previous_model_settings
                    self.settings_error = previous_settings_error
                    self.mode = previous_mode
                    config.current = previous_current
                    raise SettingsError("模型切换失败且回滚失败") from rollback_exc
                self.engine.router = previous_router
                self._model_settings = previous_model_settings
                self.settings_error = previous_settings_error
                self.mode = previous_mode
                config.current = previous_current
                raise
            return {"ok": True, **self._model_options_payload()}

    def model_settings(self) -> dict[str, Any]:
        with self._lock:
            current = self._model_settings
            if current is None:
                current = self._settings_from_router(self.engine.router)
            if current is None:
                return self.settings_store.public()
            return current.public()

    def save_model_settings(self, payload: Any) -> dict[str, Any]:
        """Validate, persist, and atomically activate GUI model settings."""

        with self._lock:
            if self._busy:
                raise RuntimeError("模型设置无法在会话执行期间修改")
            if not isinstance(payload, dict):
                raise SettingsError("请求体必须是对象")
            current = self._model_settings or self._settings_from_router(self.engine.router)
            settings = make_draft(payload, current=current)
            router = self._build_model_router(settings)
            active_id = self.profiles_store.active_id()
            active_profile = self.profiles_store.active() if active_id else None
            requested_id = payload.get("profile_id")
            if requested_id is not None:
                requested_id = normalize_profile_id(requested_id)
                if active_id != requested_id:
                    raise SettingsError("只能更新当前正在使用的配置；要新建配置请省略 profile_id")
            display_name = payload.get("display_name")
            if display_name is not None and not isinstance(display_name, str):
                raise SettingsError("display_name 必须是字符串")
            identity = (settings.api_mode, settings.base_url, settings.model)
            active_identity = None
            if active_profile is not None:
                old = active_profile.settings
                active_identity = (old.api_mode, old.base_url, old.model)

            # A save without an explicit name updates the current profile only
            # when its identity is unchanged.  Endpoint/model changes and an
            # explicit new name preserve the old profile and create another.
            if requested_id is not None:
                target_profile_id = requested_id if identity == active_identity else None
            elif display_name is None and active_id is not None and identity == active_identity:
                target_profile_id = active_id
            else:
                target_profile_id = None
            # The legacy settings pair and profile catalog are two separate
            # durable stores.  Snapshot the legacy pair and write it first;
            # if profile metadata/key persistence then fails, restore the
            # exact old bytes before re-raising.  This prevents a restart from
            # seeing a new legacy provider with an old active profile (or the
            # reverse) after a fault-injected write failure.
            legacy_paths = (self.settings_store.settings_path, self.settings_store.key_path)
            previous_legacy = {path: _snapshot_file(path) for path in legacy_paths}
            profile_paths = [self.profiles_store.meta_path]
            if self.profiles_store.keys_directory.is_dir():
                profile_paths.extend(self.profiles_store.keys_directory.glob("*.key"))
            previous_profiles = {path: _snapshot_file(path) for path in profile_paths}
            previous_router = self.engine.router
            previous_model_settings = self._model_settings
            previous_demo = self.demo
            previous_settings_error = self.settings_error
            previous_mode = self.mode
            try:
                self.settings_store.save(settings)
                self.profiles_store.save(
                    settings,
                    profile_id=target_profile_id,
                    display_name=display_name,
                    activate=True,
                )
                self.engine.router = router
                self.demo = False
                self._model_settings = settings
                self.settings_error = None
                self.mode = "configured"
            except Exception:
                try:
                    current_profile_paths = [self.profiles_store.meta_path]
                    if self.profiles_store.keys_directory.is_dir():
                        current_profile_paths.extend(self.profiles_store.keys_directory.glob("*.key"))
                    for path in current_profile_paths:
                        if path not in previous_profiles:
                            path.unlink(missing_ok=True)
                    for path, previous in previous_profiles.items():
                        _restore_file(path, previous)
                    for path, previous in previous_legacy.items():
                        _restore_file(path, previous)
                except Exception as rollback_exc:
                    self.engine.router = previous_router
                    self.demo = previous_demo
                    self._model_settings = previous_model_settings
                    self.settings_error = previous_settings_error
                    self.mode = previous_mode
                    raise SettingsError("模型配置保存失败且回滚失败") from rollback_exc
                self.engine.router = previous_router
                self.demo = previous_demo
                self._model_settings = previous_model_settings
                self.settings_error = previous_settings_error
                self.mode = previous_mode
                raise
            return settings.public()

    def delete_model_profile(self, payload: Any) -> dict[str, Any]:
        """Delete a non-active saved profile after an explicit UI confirm."""

        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        if payload.get("confirm") is not True:
            raise ValueError("删除配置需要 confirm=true")
        profile_id = _bytes_text(payload.get("profile_id"), limit=64, field="profile_id")
        profile_id = normalize_profile_id(profile_id)
        with self._lock:
            if self._busy:
                raise RuntimeError("模型设置无法在会话执行期间修改")
            self.profiles_store.delete(profile_id)
            saved_profiles = self.profiles_store.public()
            return {
                "ok": True,
                "profile_id": profile_id,
                "saved_profiles": saved_profiles,
                "profiles": saved_profiles,
                "current_profile_id": self.profiles_store.active_id(),
            }

    def test_model_settings(self, payload: Any) -> dict[str, Any]:
        """Test a draft with one harmless tool call and no tool execution."""

        current = self._model_settings or self._settings_from_router(self.engine.router)
        settings = make_draft(payload, current=current)
        if not settings.api_key and not is_local_url(settings.base_url):
            return self._model_test_failure("missing_api_key", settings=settings)
        router = self._build_model_router(settings)
        self._prepare_model_test_router(router, settings)
        from .legacy.core.schema import Message, ToolSpec

        probe_tool = ToolSpec(
            name="connection_test",
            description="仅用于连接测试；不要执行任何副作用。",
            parameters={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
        probe = Message(
            role="user",
            content="连接测试：请调用 connection_test，并传入 ok=true。",
        )
        try:
            response = router.complete(
                "agent",
                [probe],
                tools=[probe_tool],
                force_tool="connection_test",
                temperature=0,
                max_tokens=MODEL_TEST_MAX_TOKENS,
            )
        except Exception as exc:
            return self._model_test_failure(
                self._classify_model_test_error(exc), settings=settings
            )
        calls = getattr(response, "tool_calls", None) or []
        valid_calls = [
            call
            for call in calls
            if getattr(call, "name", None) == "connection_test"
        ]
        valid_call = any(
            isinstance(getattr(call, "arguments", None), dict)
            and getattr(call, "arguments", {}).get("ok") is True
            for call in valid_calls
        )
        if valid_call:
            return {
                "ok": True,
                "status": "ok",
                "api_mode": settings.api_mode,
                "model": settings.model,
                "tool_call": True,
            }

        stop_reason = str(getattr(response, "stop_reason", "") or "").casefold()
        if stop_reason in {"length", "max_tokens", "max_output_tokens", "token_limit"}:
            return self._model_test_failure("length_truncated", settings=settings)
        if calls and not valid_calls:
            return self._model_test_failure("tool_unsupported", settings=settings)

        # A provider may return a normal text response even when it declines
        # the optional tool call.  Keep that distinction visible: it proves
        # the endpoint answered, while still telling the user that this model
        # cannot support the app's tool-driven workflow.
        response_text = getattr(response, "text", None)
        if isinstance(response_text, str) and response_text.strip():
            return self._model_test_failure("tool_unsupported", settings=settings)
        return self._model_test_failure("no_response", settings=settings)

    @staticmethod
    def _is_official_deepseek_chat(settings: ModelSettings) -> bool:
        if settings.api_mode != "chat_completions":
            return False
        try:
            host = (urlsplit(settings.base_url).hostname or "").casefold()
        except ValueError:
            return False
        return host == DEEPSEEK_API_HOST

    @classmethod
    def _prepare_model_test_router(cls, router: Any, settings: ModelSettings) -> None:
        """Apply probe-only transport settings to a freshly built router.

        The normal GUI router is intentionally left untouched.  DeepSeek's
        official Chat Completions endpoint rejects some tool requests while
        reasoning is enabled, so the harmless connectivity probe disables
        reasoning only for that one request.  Other providers and the
        Anthropic protocol retain their configured request body unchanged.
        """

        router_config = getattr(router, "config", None)
        providers = getattr(router_config, "providers", {}) or {}
        is_deepseek = cls._is_official_deepseek_chat(settings)
        for provider in providers.values():
            try:
                provider.timeout = MODEL_TEST_TIMEOUT
            except Exception:
                pass
            if not is_deepseek:
                continue
            try:
                extra_body = dict(getattr(provider, "extra_body", {}) or {})
                extra_body["thinking"] = {"type": "disabled"}
                provider.extra_body = extra_body
            except Exception:
                # A test double or a future immutable provider entry may not
                # expose writable fields; the regular request remains usable.
                pass

    @staticmethod
    def _exception_statuses(exc: BaseException) -> list[int]:
        """Collect HTTP statuses from SDK and fallback-chain wrappers."""

        statuses: list[int] = []
        seen: set[int] = set()
        pending: list[Any] = [exc]
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            for candidate in (
                getattr(current, "status_code", None),
                getattr(current, "status", None),
                getattr(getattr(current, "response", None), "status_code", None),
            ):
                try:
                    value = int(candidate)
                except (TypeError, ValueError):
                    continue
                if value not in statuses:
                    statuses.append(value)
            # Lightweight test doubles and a few SDK wrappers expose only a
            # formatted ``HTTP 400``/``status_code=402`` string.  Parse the
            # status for classification, but never return that text to the UI.
            for match in re.findall(
                r"(?i)\b(?:status(?:_code)?|http(?:\s+status)?|error\s+code)\D{0,8}([45]\d{2})\b",
                str(current),
            ):
                value = int(match)
                if value not in statuses:
                    statuses.append(value)
            nested = getattr(current, "errors", None)
            if nested:
                try:
                    pending.extend(list(nested))
                except TypeError:
                    pass
            for attr in ("__cause__", "__context__"):
                child = getattr(current, attr, None)
                if child is not None:
                    pending.append(child)
        return statuses

    @staticmethod
    def _exception_text(exc: BaseException) -> str:
        """Return lowercase diagnostic text for classification only."""

        parts: list[str] = []
        pending: list[Any] = [exc]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            for attr in ("reason", "detail", "message"):
                value = getattr(current, attr, None)
                if isinstance(value, str):
                    parts.append(value)
            parts.append(str(current))
            nested = getattr(current, "errors", None)
            if nested:
                try:
                    pending.extend(list(nested))
                except TypeError:
                    pass
            for attr in ("__cause__", "__context__"):
                child = getattr(current, attr, None)
                if child is not None:
                    pending.append(child)
        return " ".join(parts).casefold()

    @classmethod
    def _model_test_failure(
        cls, code: str, *, settings: ModelSettings | None = None
    ) -> dict[str, Any]:
        message = MODEL_TEST_MESSAGES.get(code, MODEL_TEST_MESSAGES["connection_failed"])
        # The bare string "deepseek" is a common GUI typo for the official
        # model IDs, but it remains a valid user-defined/proxy model name.  A
        # hint is therefore attached only to the relevant DeepSeek failure;
        # the test is never blocked based on a model-name allow-list.
        if (
            settings is not None
            and cls._is_official_deepseek_chat(settings)
            and settings.model.casefold() == "deepseek"
            and code in {"invalid_request", "invalid_model", "endpoint_failed"}
        ):
            message += " 官方 DeepSeek 通常需要填写控制台提供的实际模型 ID（例如 deepseek-flash）。"
        return {
            "ok": False,
            "error": code,
            "code": code,
            "reason": code,
            "message": message,
        }

    @staticmethod
    def _classify_model_test_error(exc: BaseException) -> str:
        """Map provider failures to fixed categories without echoing SDK text."""

        statuses = WebService._exception_statuses(exc)
        text = WebService._exception_text(exc)
        if any(code in {401, 403} for code in statuses):
            return "authentication_failed"
        if any(code == 402 for code in statuses):
            return "balance_insufficient"
        if any(code in {400, 422} for code in statuses):
            if any(
                marker in text
                for marker in (
                    "tool_choice",
                    "tool call",
                    "tool_calls",
                    "function calling",
                    "function tool",
                    "does not support tools",
                    "unsupported tool",
                    "工具调用",
                )
            ):
                return "tool_unsupported"
            if any(
                marker in text
                for marker in (
                    "model not found",
                    "model_not_found",
                    "invalid model",
                    "unknown model",
                    "model does not exist",
                    "模型不存在",
                    "模型无效",
                )
            ):
                return "invalid_model"
            return "invalid_request"
        if any(code == 404 for code in statuses):
            if any(
                marker in text
                for marker in ("model not found", "model_not_found", "unknown model", "模型不存在")
            ):
                return "invalid_model"
            return "endpoint_failed"
        if any(code == 429 for code in statuses):
            return "rate_limited"
        if any(500 <= code <= 599 for code in statuses):
            return "service_unavailable"
        if any(code == 408 for code in statuses):
            return "timeout"
        type_text = type(exc).__name__.casefold()
        if "timeout" in type_text or "timedout" in type_text or "timed out" in text:
            return "timeout"
        if any(
            marker in type_text or marker in text
            for marker in ("connection", "connecterror", "disconnect", "network")
        ):
            return "connection_failed"
        return "connection_failed"

    def info(self) -> dict[str, Any]:
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "workspace": str(self.workspace),
            "mode": self.mode,
            "settings_error": self.settings_error,
            "workflow": "job-radar" if not self.no_skills else None,
            "product_mode": bool(self.engine.product_mode),
            "skills": [
                {"name": str(row.get("name", "")), "description": str(row.get("description", ""))}
                for row in self.engine.available_skills()
            ],
        }

    def _session(self, session_id: str) -> AgentSession:
        validate_session_id(session_id)
        # A pre-product session is upgraded only once, when it is actually
        # opened/submitted.  Ordinary reads after that do not change revision.
        return self.engine.ensure_workflow(session_id)

    def _is_busy(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._busy

    def session_snapshot(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        with self._lock:
            busy = session.id in self._busy
            last_result = _safe_json(self._last_results.get(session.id))
        # Keep the browser's existing raw ``skill_state`` contract while
        # exposing the read-only lazy project-history migration for sessions
        # created before project portfolios existed.  ``workflow_state`` does
        # not mutate or save the session; unknown skill extensions remain
        # intact through this shallow overlay.
        skill_state = _safe_json(session.skill_state)
        if self.engine.product_mode and isinstance(skill_state, dict):
            raw_workflow = skill_state.get("job-radar")
            if isinstance(raw_workflow, dict):
                normalized_workflow = job_radar.workflow_state(session)
                merged_workflow = dict(raw_workflow)
                merged_workflow.update(normalized_workflow)
                skill_state["job-radar"] = merged_workflow
        return {
            "id": session.id,
            "workspace": session.workspace,
            "messages": [_message_payload(message) for message in session.messages],
            "plan": _safe_json(session.plan),
            "active_skills": list(session.active_skills),
            "skill_state": skill_state,
            "queued_calls": len(session.queued_calls),
            "pending": _pending_payload(session),
            "execution": _safe_json(session.execution or {}),
            "events": _safe_json(session.events),
            "busy": busy,
            "last_result": last_result,
        }

    def session_summaries(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for session in self.engine.list_sessions():
            if self.engine.product_mode and self.engine._ensure_workflow(session):
                # Persist the migration once so sidebar state and later actions
                # agree.  The helper is idempotent and does not save unchanged
                # sessions.
                session = self.engine.ensure_workflow(session.id)
            workflow = job_radar.workflow_state(session)
            jd = workflow.get("jd") or {}
            job_title = str(jd.get("job_title") or "").strip() if isinstance(jd, dict) else ""
            company = str(jd.get("company") or "").strip() if isinstance(jd, dict) else ""
            jd_confirmed = bool((workflow.get("jd_confirmation") or {}).get("confirmed"))
            if job_title and company:
                title = f"{job_title} · {company}"
            elif job_title or company:
                title = job_title or company
            else:
                title = "新会话"
            for message in session.messages:
                if getattr(message, "role", None) == "user":
                    content = message.content if isinstance(message.content, str) else ""
                    if isinstance(message.content, list):
                        content = " ".join(
                            str(getattr(block, "text", ""))
                            for block in message.content
                            if getattr(block, "type", None) == "text"
                        )
                    if content.strip() and title == "新会话":
                        title = content.strip().replace("\r", " ").replace("\n", " ")[:80]
                        break
            with self._lock:
                busy = session.id in self._busy
            rows.append(
                {
                    "id": session.id,
                    "title": title,
                    "job_title": job_title or None,
                    "company": company or None,
                    "jd_confirmed": jd_confirmed,
                    "has_profile": bool(workflow.get("profile")),
                    "clarification": _safe_json(workflow.get("clarification")),
                    "message_count": len(session.messages),
                    "active_skills": list(session.active_skills),
                    "pending": session.pending is not None,
                    "busy": busy,
                }
            )
        return rows

    def _workflow_context(self, session: AgentSession) -> ToolContext:
        # Product job-radar tools are deterministic and must never receive the
        # provider router, even when called by an HTTP UI mutation.
        return ToolContext(workspace=self.workspace, session=session, router=None)

    def _mutate_session(self, session_id: str, operation: Callable[[AgentSession], Any]) -> Any:
        """Run one synchronous UI mutation under the same busy/Store lock."""

        self._session(session_id)
        with self._lock:
            if session_id in self._busy:
                raise RuntimeError("会话正在执行另一个操作")
        with self.engine.store.lock(session_id):
            # The first check closes the common fast-fail path, while this
            # second check closes the race where submit() claims the session
            # after the first check but before this Store lock is acquired.
            with self._lock:
                if session_id in self._busy:
                    raise RuntimeError("会话正在执行另一个操作")
            session = self.engine.store.get(session_id)
            if session.pending is not None:
                raise RuntimeError("会话有待处理的审批，请先完成或拒绝审批")
            if session.queued_calls:
                raise RuntimeError("会话有待恢复的工具队列，请先继续执行")
            execution_status = (session.execution or {}).get("status")
            if execution_status not in {None, "cancelled", "resolved_uncertain"}:
                raise RuntimeError("会话有未完成的 Agent 执行，请先完成、取消或确认结果")
            result = operation(session)
            self.engine.store.save(session)
            return result

    def create_session(self, source_session_id: str | None = None) -> dict[str, Any]:
        source = None
        if source_session_id is not None:
            source_id = _bytes_text(source_session_id, limit=128, field="source_session_id")
            with self._lock:
                if source_id in self._busy:
                    raise RuntimeError("源会话正在执行另一个操作")
            # Acquire the source lock while taking the deep-copy snapshot so a
            # worker cannot change the profile halfway through a clone.
            with self.engine.store.lock(source_id):
                source = self.engine.store.get(source_id)
                if not job_radar.workflow_state(source).get("profile"):
                    raise ValueError("源会话尚未建立个人档案，请选择已有档案的会话")
        session = self.engine.new_session()
        if self.engine.product_mode:
            # The generic AgentEngine deliberately keeps its session state
            # namespace lazy for non-web callers.  The product HTTP service
            # materializes the job-radar envelope on creation so its state
            # endpoint and model-context bridge have a stable shape.
            job_radar.ensure_state(session)
            self.engine.store.save(session)
        if source is not None:
            with self.engine.store.lock(session.id):
                target = self.engine.store.get(session.id)
                job_radar.copy_profile_state(source, target)
                self.engine.store.save(target)
        return self.session_snapshot(session.id)

    def delete_session(self, session_id: str) -> dict[str, Any]:
        validate_session_id(session_id)
        with self._lock:
            if session_id in self._busy:
                raise RuntimeError("会话正在执行另一个操作")
            # SessionStore.delete removes only the JSON/lock state.  Shared
            # workspace uploads intentionally remain available to other sessions.
            self.engine.store.delete(session_id)
            self._last_results.pop(session_id, None)
        return {"ok": True, "id": session_id}

    def get_jd(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        state = job_radar.workflow_state(session)
        return {
            "session_id": session.id,
            "jd": state.get("jd"),
            "confirmed": bool((state.get("jd_confirmation") or {}).get("confirmed")),
            "confirmation": state.get("jd_confirmation"),
        }

    def update_jd(self, session_id: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        if "jd" not in payload:
            raise ValueError("jd 必须是对象")
        confirmed = payload.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed 必须是布尔值")
        arguments: dict[str, Any] = {
            "jd": payload["jd"],
            "source": {"kind": "conversation", "evidence": ["用户在 UI 中编辑 JD"]},
        }
        for key in ("source_url", "url"):
            if key in payload:
                arguments[key] = payload[key]

        def operation(session: AgentSession) -> dict[str, Any]:
            ctx = self._workflow_context(session)
            job_radar.set_jd(arguments, ctx)
            if confirmed:
                job_radar.confirm_jd({"confirmed": True}, ctx)
            session.messages.append(
                Message(
                    role="user",
                    content="用户通过界面更新了 JD。请以当前 job-radar 工件状态为准。",
                )
            )
            state = job_radar.workflow_state(session)
            return {
                "session_id": session.id,
                "jd": state.get("jd"),
                "confirmed": bool((state.get("jd_confirmation") or {}).get("confirmed")),
                "confirmation": state.get("jd_confirmation"),
                "state": state,
            }

        return self._mutate_session(session_id, operation)

    def get_profile(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        state = job_radar.workflow_state(session)
        return {
            "session_id": session.id,
            "profile": state.get("profile"),
            "clarification": state.get("clarification"),
        }

    def update_profile(self, session_id: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        if "profile" not in payload:
            raise ValueError("profile 必须是对象")
        arguments = {
            "profile": payload["profile"],
            "source": {"kind": "conversation", "evidence": ["用户在 UI 中编辑个人技能档案"]},
        }

        def operation(session: AgentSession) -> dict[str, Any]:
            ctx = self._workflow_context(session)
            result = job_radar.set_profile(arguments, ctx)
            session.messages.append(
                Message(
                    role="user",
                    content="用户通过界面更新了个人技能档案。请以当前 job-radar 工件状态为准。",
                )
            )
            state = job_radar.workflow_state(session)
            return {
                "session_id": session.id,
                "profile": state.get("profile"),
                "clarification": state.get("clarification"),
                "state": state,
                "message": "个人档案已保存",
            }

        return self._mutate_session(session_id, operation)

    def github_token(self) -> dict[str, Any]:
        return github_token_status(self.workspace)

    def save_github_token(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        clear = payload.get("clear", False)
        if not isinstance(clear, bool):
            raise ValueError("clear 必须是布尔值")
        if clear:
            if isinstance(payload.get("token"), str) and payload["token"].strip():
                raise ValueError("clear 与 token 不能同时使用")
            return clear_github_token(self.workspace)
        token = payload.get("token")
        if not isinstance(token, str):
            raise ValueError("token 必须是字符串")
        return save_github_token(self.workspace, token)

    def import_local(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path 必须是非空绝对路径")
        destination = payload.get("destination")
        if destination is not None and not isinstance(destination, str):
            raise ValueError("destination 必须是字符串")
        return import_local_path(self.workspace, path, destination=destination)

    def submit(
        self,
        session_id: str,
        operation: Callable[[], Any],
    ) -> str:
        """Claim a session and run ``operation`` in a daemon thread."""

        self._session(session_id)
        with self._lock:
            if session_id in self._busy:
                raise RuntimeError("会话正在执行另一个操作")
            job_id = uuid.uuid4().hex
            self._busy[session_id] = job_id
            self._jobs[job_id] = {"id": job_id, "status": "running", "result": None}
            # Keep completed history bounded while never evicting a running job.
            if len(self._jobs) > MAX_JOBS:
                for old_id, old_job in list(self._jobs.items()):
                    if old_job.get("status") != "running":
                        self._jobs.pop(old_id, None)
                    if len(self._jobs) <= MAX_JOBS:
                        break

        def worker() -> None:
            try:
                result = operation()
                safe_result = _result_payload(result)
            except Exception as exc:  # keep worker failures inside the job record
                safe_error = _safe_error(exc)
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job.update({"status": "failed", "error": safe_error, "result": None})
                    self._last_results[session_id] = {
                        "status": "failed",
                        "message": safe_error,
                        "session_id": session_id,
                    }
                    self._busy.pop(session_id, None)
                return
            with self._lock:
                self._last_results[session_id] = safe_result
                job = self._jobs.get(job_id)
                if job is not None:
                    job.update({"status": "completed", "result": safe_result})
                self._busy.pop(session_id, None)

        threading.Thread(target=worker, name=f"agent-coach-job-{job_id[:8]}", daemon=True).start()
        return job_id

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._jobs.get(str(job_id))
            if item is None:
                raise KeyError("job 不存在")
            return _safe_json(dict(item))

    def upload(self, name: str, encoded: str) -> dict[str, str]:
        filename = _bytes_text(name, limit=255, field="name")
        if filename in {".", ".."} or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("name 必须是单个文件名")
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_UPLOAD_BODY:
            raise ValueError("content_base64 超出长度限制")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 无效") from exc
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("上传文件超过 10 MiB 限制")
        folder = resolve_path(_FileContext(self.workspace), "uploads", must_exist=False)
        if folder.exists() and not folder.is_dir():
            raise ValueError("uploads 路径不是目录")
        folder.mkdir(parents=True, exist_ok=True)
        # A unique name prevents a browser upload from overwriting an existing file.
        target_name = f"{Path(filename).stem}-{uuid.uuid4().hex[:12]}{Path(filename).suffix}"
        target = resolve_path(_FileContext(self.workspace), f"uploads/{target_name}", must_exist=False)
        with target.open("xb") as handle:
            handle.write(data)
        return {
            "path": f"uploads/{target_name}",
            "name": target_name,
            "media_type": mimetypes.guess_type(target_name)[0] or "application/octet-stream",
        }


class _FileContext:
    def __init__(self, workspace: Path):
        self.workspace = workspace


def build_service(
    workspace: str | Path = ".",
    *,
    config_path: str | Path | None = None,
    config: str | Path | None = None,
    demo: bool = False,
    no_skills: bool = False,
    token: str | None = None,
) -> WebService:
    return WebService(
        workspace,
        config_path=config_path,
        config=config,
        demo=demo,
        no_skills=no_skills,
        token=token,
    )


class AgentHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], service: WebService):
        self.service = service
        super().__init__(server_address, AgentRequestHandler)


def make_server(
    service_or_workspace: WebService | str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    service: WebService | None = None,
    workspace: str | Path | None = None,
    config_path: str | Path | None = None,
    config: str | Path | None = None,
    demo: bool = False,
    no_skills: bool = False,
) -> AgentHTTPServer:
    # A compact ``make_server(service, port)`` form is convenient in tests.
    if isinstance(host, int):
        port, host = host, "127.0.0.1"
    if service is None and isinstance(service_or_workspace, WebService):
        service = service_or_workspace
    if service is None:
        selected_workspace = workspace if workspace is not None else service_or_workspace
        service = build_service(
            selected_workspace or ".",
            config_path=config_path,
            config=config,
            demo=demo,
            no_skills=no_skills,
        )
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("web server 只允许绑定 loopback")
    return AgentHTTPServer((host, int(port)), service)


class AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        # A malformed client must not hold a worker forever while claiming a
        # body that never arrives.  Engine work is dispatched separately.
        self.connection.settimeout(15.0)

    def log_message(self, format: str, *args: Any) -> None:  # no request/token logs by default
        return

    @property
    def service(self) -> WebService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def actual_port(self) -> int:
        return int(self.server.server_address[1])  # type: ignore[attr-defined]

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(_safe_json(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": str(message)[:500]})

    def _request_guards(self, *, api: bool) -> bool:
        try:
            self._target()
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid request target")
            return False
        host_value = self.headers.get("Host", "")
        parsed = None
        try:
            parsed = urlsplit("//" + host_value)
            host = (parsed.hostname or "").casefold()
            host_port = parsed.port
        except ValueError:
            host, host_port = "", None
        if (
            host not in {"127.0.0.1", "localhost"}
            or host_port != self.actual_port
            or parsed is None
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            self._error(HTTPStatus.FORBIDDEN, "invalid host")
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                origin_parts = urlsplit(origin)
                if (
                    origin_parts.scheme.casefold() != "http"
                    or origin_parts.hostname.casefold() != host
                    or origin_parts.port != self.actual_port
                    or origin_parts.username
                    or origin_parts.password
                    or origin_parts.path not in ("", "/")
                    or origin_parts.query
                    or origin_parts.fragment
                ):
                    raise ValueError
            except (AttributeError, ValueError):
                self._error(HTTPStatus.FORBIDDEN, "invalid origin")
                return False
        if api:
            authorization = self.headers.get("Authorization", "")
            expected = f"Bearer {self.service.token}"
            if not secrets.compare_digest(authorization, expected):
                self._error(HTTPStatus.UNAUTHORIZED, "authorization required")
                return False
        return True

    def _target(self):
        """Return a relative request target; reject proxy absolute-form URLs."""

        split = urlsplit(self.path)
        if split.scheme or split.netloc:
            raise ValueError("absolute request target is not allowed")
        return split, unquote(split.path)

    def _api_request(self) -> bool | None:
        try:
            _, path = self._target()
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid request target")
            return None
        return path == "/api" or path.startswith("/api/")

    def _read_json(self, *, limit: int = MAX_JSON_BODY) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.close_connection = True
            raise ValueError("Content-Length required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            self.close_connection = True
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > limit:
            self.close_connection = True
            # Consume a bounded oversized body before closing.  This avoids a
            # TCP reset on platforms that abort a socket closed with unread
            # request data, while keeping pathological declarations bounded.
            if 0 < length <= MAX_DISCARD_BODY:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            raise OverflowError("request body too large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.close_connection = True
            raise ValueError("incomplete request body")
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:
        api = self._api_request()
        if api is None or not self._request_guards(api=api):
            return
        try:
            self._dispatch_get()
        except (SessionNotFound, FileNotFoundError, KeyError):
            self._error(HTTPStatus.NOT_FOUND, "session not found")
        except (ValueError, PathSecurityError, ToolInputError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, _safe_error(exc, default="invalid request"))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "request failed")

    def do_POST(self) -> None:
        api = self._api_request()
        if api is None or not self._request_guards(api=api):
            return
        try:
            self._dispatch_post()
        except (SessionNotFound, FileNotFoundError, KeyError):
            self._error(HTTPStatus.NOT_FOUND, "session not found")
        except RuntimeError as exc:
            self._error(HTTPStatus.CONFLICT, _safe_error(exc, default="session busy"))
        except OverflowError:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        except (ValueError, PathSecurityError, ToolInputError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, _safe_error(exc, default="invalid request"))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "request failed")

    def _reject_method(self) -> None:
        api = self._api_request()
        if api is not None and self._request_guards(api=api):
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def do_OPTIONS(self) -> None:
        self._reject_method()

    def do_HEAD(self) -> None:
        self._reject_method()

    def do_PUT(self) -> None:
        self._reject_method()

    def do_PATCH(self) -> None:
        self._reject_method()

    def do_DELETE(self) -> None:
        api = self._api_request()
        if api is None or not self._request_guards(api=api):
            return
        try:
            self._dispatch_delete()
        except (SessionNotFound, FileNotFoundError, KeyError):
            self._error(HTTPStatus.NOT_FOUND, "session not found")
        except RuntimeError as exc:
            self._error(HTTPStatus.CONFLICT, _safe_error(exc, default="session busy"))
        except (ValueError, PathSecurityError, ToolInputError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, _safe_error(exc, default="invalid request"))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "request failed")

    def _dispatch_get(self) -> None:
        split, path = self._target()
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        if path == "/api/info":
            self._json(HTTPStatus.OK, self.service.info())
            return
        if path == "/api/settings/models":
            # This is intentionally the complete public payload.  In
            # particular, never serialize the in-memory ModelSettings object.
            self._json(HTTPStatus.OK, self.service.model_settings())
            return
        if path == "/api/settings/models/options":
            self._json(HTTPStatus.OK, self.service.model_options())
            return
        if path == "/api/settings/ui":
            self._json(HTTPStatus.OK, self.service.ui_settings())
            return
        if path == "/api/settings/github-token":
            self._json(HTTPStatus.OK, self.service.github_token())
            return
        if path == "/api/sessions":
            self._json(HTTPStatus.OK, {"sessions": self.service.session_summaries()})
            return
        if path.startswith("/api/jobs/"):
            self._json(HTTPStatus.OK, self.service.job(path.rsplit("/", 1)[1]))
            return
        if path == "/api/files":
            query = parse_qs(split.query, keep_blank_values=True)
            requested = query.get("path", ["."])[0]
            self._json(HTTPStatus.OK, self._files(requested))
            return
        if path == "/api/file":
            query = parse_qs(split.query, keep_blank_values=True)
            requested = query.get("path", [""])[0]
            self._json(HTTPStatus.OK, self._file(requested))
            return
        parts = path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "sessions"]:
            session_id, resource = parts[3], parts[4]
            if resource == "jd":
                self._json(HTTPStatus.OK, self.service.get_jd(session_id))
                return
            if resource == "profile":
                self._json(HTTPStatus.OK, self.service.get_profile(session_id))
                return
        if len(parts) == 4 and parts[1:3] == ["api", "sessions"]:
            self._json(HTTPStatus.OK, self.service.session_snapshot(parts[3]))
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _dispatch_post(self) -> None:
        split, path = self._target()
        if path == "/api/sessions":
            body = self._read_json()
            source_session_id = body.get("source_session_id")
            if source_session_id is not None and not isinstance(source_session_id, str):
                raise ValueError("source_session_id 必须是字符串")
            self._json(HTTPStatus.CREATED, self.service.create_session(source_session_id))
            return
        if path == "/api/settings/github-token":
            body = self._read_json()
            self._json(HTTPStatus.OK, self.service.save_github_token(body))
            return
        if path == "/api/settings/ui":
            body = self._read_json()
            self._json(HTTPStatus.OK, self.service.save_ui_settings(body))
            return
        if path == "/api/imports/local":
            body = self._read_json()
            self._json(HTTPStatus.CREATED, self.service.import_local(body))
            return
        if path == "/api/settings/models/select":
            body = self._read_json()
            self._json(HTTPStatus.OK, self.service.select_model(body))
            return
        if path == "/api/settings/models/delete":
            body = self._read_json()
            self._json(HTTPStatus.OK, self.service.delete_model_profile(body))
            return
        if path == "/api/settings/models":
            body = self._read_json()
            if (
                ("provider" in body and "api_mode" not in body and "base_url" not in body)
                or ("profile_id" in body and "api_mode" not in body and "base_url" not in body)
            ):
                self._json(HTTPStatus.OK, self.service.select_model(body))
            else:
                self._json(HTTPStatus.OK, self.service.save_model_settings(body))
            return
        if path == "/api/settings/models/test":
            body = self._read_json()
            self._json(HTTPStatus.OK, self.service.test_model_settings(body))
            return
        if path == "/api/uploads":
            body = self._read_json(limit=MAX_UPLOAD_BODY)
            self._json(HTTPStatus.CREATED, self.service.upload(body.get("name"), body.get("content_base64")))
            return
        parts = path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "sessions"]:
            session_id, resource = parts[3], parts[4]
            if resource == "jd":
                body = self._read_json()
                self._json(HTTPStatus.OK, self.service.update_jd(session_id, body))
                return
            if resource == "profile":
                body = self._read_json()
                self._json(HTTPStatus.OK, self.service.update_profile(session_id, body))
                return
        if len(parts) != 5 or parts[1:3] != ["api", "sessions"]:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        session_id, action = parts[3], parts[4]
        body = self._read_json()
        if action == "message":
            text = _bytes_text(body.get("text"), limit=MAX_TEXT_BYTES, field="text")
            attachments = _attachments(body.get("attachments", body.get("attachment")))
            operation = lambda: self.service.engine.run(
                text,
                session_id=session_id,
                attachments=attachments,
            )
        elif action in {"approve", "reject"}:
            approval_id = _bytes_text(body.get("id"), limit=256, field="id")
            method = self.service.engine.approve if action == "approve" else self.service.engine.reject
            operation = lambda: method(session_id, approval_id)
        elif action == "continue":
            operation = lambda: self.service.engine.continue_session(session_id)
        elif action == "resolve":
            execution_id = _bytes_text(body.get("id"), limit=256, field="id")
            note = body.get("note", "")
            if not isinstance(note, str) or len(note.encode("utf-8")) > MAX_NOTE_BYTES:
                raise ValueError("note 超出长度限制")
            operation = lambda: self.service.engine.resolve_uncertain(session_id, execution_id, note)
        elif action == "skill":
            name = _bytes_text(body.get("name"), limit=128, field="name")
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled 必须是布尔值")
            operation = (
                (lambda: self.service.engine.activate_skill(session_id, name))
                if enabled
                else (lambda: self.service.engine.deactivate_skill(session_id, name))
            )
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        job_id = self.service.submit(session_id, operation)
        self._json(HTTPStatus.ACCEPTED, {"job_id": job_id})

    def _dispatch_delete(self) -> None:
        _, path = self._target()
        parts = path.split("/")
        if len(parts) == 4 and parts[1:3] == ["api", "sessions"]:
            self._json(HTTPStatus.OK, self.service.delete_session(parts[3]))
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _files(self, requested: str) -> dict[str, Any]:
        target = resolve_path(_FileContext(self.service.workspace), requested, must_exist=True)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        entries: list[dict[str, Any]] = []
        truncated = False
        for child in target.iterdir():
            try:
                safe_child = resolve_path(_FileContext(self.service.workspace), str(child), must_exist=True)
            except (OSError, ValueError, PathSecurityError):
                continue
            item: dict[str, Any] = {
                "name": child.name,
                "path": safe_child.relative_to(self.service.workspace).as_posix(),
                "type": "directory" if safe_child.is_dir() else "file",
            }
            if safe_child.is_file():
                try:
                    item["size"] = safe_child.stat().st_size
                except OSError:
                    item["size"] = 0
            if len(entries) >= MAX_FILE_ENTRIES:
                truncated = True
                break
            entries.append(item)
        entries.sort(key=lambda item: str(item.get("name", "")).casefold())
        display = target.relative_to(self.service.workspace).as_posix() or "."
        return {"path": display, "entries": entries, "truncated": truncated}

    def _file(self, requested: str) -> dict[str, Any]:
        if not requested:
            raise ValueError("path is required")
        target = resolve_path(_FileContext(self.service.workspace), requested, must_exist=True)
        if not target.is_file():
            raise ValueError("path is not a regular file")
        with target.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        truncated = len(raw) > MAX_FILE_BYTES
        if truncated:
            raw = raw[:MAX_FILE_BYTES]
        display = target.relative_to(self.service.workspace).as_posix()
        return {"path": display, "content": raw.decode("utf-8", errors="replace"), "truncated": truncated}

    def _serve_static(self, path: str) -> None:
        clean = path or "/index.html"
        if clean == "/":
            clean = "/index.html"
        if clean.startswith("/static/"):
            clean = "/" + clean.removeprefix("/static/")
        name = clean.lstrip("/")
        if name not in STATIC_NAMES or "/" in name or "\\" in name:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        target = Path(__file__).resolve().parent / "web_static" / name
        if not target.is_file() or target.stat().st_size > MAX_STATIC_BYTES:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        raw = target.read_bytes()
        content_type = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8"}[name]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行校招雷达本地界面")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--config", default=None)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-skills", action="store_true", help="开发诊断：禁用求职工作流与领域工具")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = build_service(
        args.workspace,
        config_path=args.config,
        demo=args.demo,
        no_skills=args.no_skills,
    )
    server = make_server(service, host="127.0.0.1", port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/#token={service.token}"
    print(url, flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


__all__ = [
    "AgentHTTPServer",
    "AgentRequestHandler",
    "WebService",
    "build_parser",
    "build_service",
    "main",
    "make_server",
]
