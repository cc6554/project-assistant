"""Persistent GUI model settings.

The web UI stores the small, non-secret part of its model configuration as
JSON in the workspace.  The API key is kept in a separate file and protected
with Windows DPAPI for the current user.  Keeping this code independent from
the HTTP handler makes it possible to test validation and persistence without
starting a server.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlsplit


VALID_API_MODES = {"chat_completions", "anthropic_messages"}
SETTINGS_DIRNAME = ".agent-coach"
SETTINGS_FILENAME = "model-settings.json"
KEY_FILENAME = "model-settings.key"
MAX_BASE_URL_BYTES = 2048
MAX_MODEL_BYTES = 512
MAX_API_KEY_BYTES = 8192
MAX_PROTECTED_RECORD_BYTES = MAX_API_KEY_BYTES + MAX_BASE_URL_BYTES + MAX_MODEL_BYTES + 256


class SettingsError(ValueError):
    """A safe, user-facing settings validation or persistence error."""


@dataclass(frozen=True)
class ModelSettings:
    """A validated model configuration.

    ``api_key`` is deliberately excluded from repr/equality so accidental
    logging or comparison of a settings object cannot disclose it.
    """

    api_mode: str
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def public(self) -> dict[str, Any]:
        return {
            "api_mode": self.api_mode,
            "base_url": self.base_url,
            "model": self.model,
            "has_api_key": self.has_api_key,
        }


def _text(value: Any, *, field_name: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{field_name} 必须是非空字符串")
    value = value.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise SettingsError(f"{field_name} 包含非法控制字符")
    if len(value.encode("utf-8")) > max_bytes:
        raise SettingsError(f"{field_name} 超出长度限制")
    return value


def _validate_url(value: Any) -> str:
    result = _text(value, field_name="base_url", max_bytes=MAX_BASE_URL_BYTES)
    if any(char.isspace() for char in result):
        raise SettingsError("base_url 包含非法空白字符")
    try:
        parsed = urlsplit(result)
        # Accessing .port validates malformed/out-of-range port strings.
        _ = parsed.port
    except ValueError as exc:
        raise SettingsError("base_url 格式无效") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise SettingsError("base_url 必须是 http 或 https 地址")
    # Credentials in a URL are easy to leak through errors and logs.  The
    # API key must always use the encrypted field instead.
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise SettingsError("base_url 不得包含凭据或片段")
    return result.rstrip("/")


def validate_draft(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise SettingsError("请求体必须是对象")
    api_mode = payload.get("api_mode")
    if api_mode not in VALID_API_MODES:
        raise SettingsError("api_mode 无效")
    base_url = _validate_url(payload.get("base_url"))
    model = _text(payload.get("model"), field_name="model", max_bytes=MAX_MODEL_BYTES)
    return str(api_mode), base_url, model


def _draft_key(payload: dict[str, Any], current: ModelSettings | None) -> str | None:
    clear = payload.get("clear_api_key", False)
    if not isinstance(clear, bool):
        raise SettingsError("clear_api_key 必须是布尔值")
    supplied = payload.get("api_key", None)
    if supplied is not None and not isinstance(supplied, str):
        raise SettingsError("api_key 必须是字符串")
    if clear and supplied and supplied.strip():
        raise SettingsError("api_key 与 clear_api_key 不能同时使用")
    if clear:
        return None
    # An omitted or empty API key explicitly means preserve the current key.
    if supplied is None or not supplied.strip():
        return current.api_key if current is not None else None
    supplied = supplied.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in supplied):
        raise SettingsError("api_key 包含非法控制字符")
    if len(supplied.encode("utf-8")) > MAX_API_KEY_BYTES:
        raise SettingsError("api_key 超出长度限制")
    return supplied


def make_draft(payload: Any, *, current: ModelSettings | None = None) -> ModelSettings:
    """Validate a save/test payload and resolve its key-preservation rules."""

    if not isinstance(payload, dict):
        raise SettingsError("请求体必须是对象")
    api_mode, base_url, model = validate_draft(payload)
    api_key = _draft_key(payload, current)
    # Never silently send a key to a different endpoint.  The user can either
    # enter a new key or explicitly clear the old one.  Local endpoints may be
    # keyless, so the no-key case remains valid there.
    old_url = current.base_url if current is not None else None
    if (
        current is not None
        and current.has_api_key
        and old_url != base_url
        and not (payload.get("clear_api_key", False) or (isinstance(payload.get("api_key"), str) and payload["api_key"].strip()))
    ):
        raise SettingsError("更换 base_url 时请重新填写 api_key，或显式清除旧密钥")
    return ModelSettings(api_mode=api_mode, base_url=base_url, model=model, api_key=api_key)


def is_local_url(base_url: str) -> bool:
    try:
        host = (urlsplit(base_url).hostname or "").casefold()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_crypto():
    if os.name != "nt":
        raise SettingsError("当前系统不支持 Windows DPAPI 密钥存储")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except AttributeError as exc:  # pragma: no cover - defensive for emulators
        raise SettingsError("当前系统不支持 Windows DPAPI 密钥存储") from exc
    protect = crypt32.CryptProtectData
    unprotect = crypt32.CryptUnprotectData
    protect.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DATA_BLOB),
    ]
    protect.restype = ctypes.c_int
    unprotect.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DATA_BLOB),
    ]
    unprotect.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    return protect, unprotect, local_free


def _protect_text(value: str, *, max_bytes: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise SettingsError("api_key 不能为空")
    raw = value.encode("utf-8")
    if len(raw) > max_bytes:
        raise SettingsError("密钥存储内容超出长度限制")
    protect, _, local_free = _windows_crypto()
    source_buffer = ctypes.create_string_buffer(raw)
    source = _DATA_BLOB(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DATA_BLOB()
    if not protect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        code = ctypes.get_last_error()
        raise SettingsError(f"密钥加密失败（错误 {code}）")
    try:
        return bytes(ctypes.string_at(result.pbData, result.cbData))
    finally:
        local_free(result.pbData)


def protect_key(value: str) -> bytes:
    """Encrypt an API key with Windows DPAPI for the current user."""

    if not isinstance(value, str) or not value:
        raise SettingsError("api_key 不能为空")
    if len(value.encode("utf-8")) > MAX_API_KEY_BYTES:
        raise SettingsError("api_key 超出长度限制")
    return _protect_text(value, max_bytes=MAX_API_KEY_BYTES)


def _unprotect_text(blob: bytes, *, max_bytes: int) -> str:
    if not isinstance(blob, bytes) or not blob:
        raise SettingsError("密钥存储无效")
    _, unprotect, local_free = _windows_crypto()
    source_buffer = ctypes.create_string_buffer(blob)
    source = _DATA_BLOB(len(blob), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _DATA_BLOB()
    description = ctypes.c_wchar_p()
    if not unprotect(ctypes.byref(source), ctypes.byref(description), None, None, None, 1, ctypes.byref(result)):
        code = ctypes.get_last_error()
        raise SettingsError(f"密钥解密失败（错误 {code}）")
    try:
        raw = bytes(ctypes.string_at(result.pbData, result.cbData))
    finally:
        local_free(result.pbData)
        if description:
            local_free(ctypes.cast(description, ctypes.c_void_p))
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SettingsError("密钥存储无效") from exc
    if not value or len(raw) > max_bytes:
        raise SettingsError("密钥存储无效")
    return value


def unprotect_key(blob: bytes) -> str:
    """Decrypt a DPAPI blob and validate the resulting UTF-8 text."""

    return _unprotect_text(blob, max_bytes=MAX_PROTECTED_RECORD_BYTES)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class ModelSettingsStore:
    """Read and write the workspace-scoped GUI model settings."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / SETTINGS_DIRNAME
        self.settings_path = self.directory / SETTINGS_FILENAME
        self.key_path = self.directory / KEY_FILENAME
        self.last_error: str | None = None

    def load(self) -> ModelSettings | None:
        self.last_error = None
        if not self.settings_path.is_file():
            return None
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            api_mode, base_url, model = validate_draft(payload)
        except Exception:
            self.last_error = "saved_settings_invalid"
            return None
        key: str | None = None
        if self.key_path.is_file():
            try:
                # The key record is encrypted as a small JSON envelope.  The
                # binding prevents a partially completed two-file save from
                # attaching a new key to an old endpoint/model.
                record = json.loads(unprotect_key(self.key_path.read_bytes()))
                if (
                    not isinstance(record, dict)
                    or record.get("api_mode") != api_mode
                    or record.get("base_url") != base_url
                    or record.get("model") != model
                    or not isinstance(record.get("api_key"), str)
                    or not record.get("api_key")
                ):
                    raise SettingsError("saved key binding mismatch")
                key = record["api_key"]
            except Exception:
                # A damaged key should not prevent the GUI from opening.  The
                # public settings simply report has_api_key=false.
                self.last_error = "saved_key_unavailable"
                key = None
        return ModelSettings(api_mode=api_mode, base_url=base_url, model=model, api_key=key)

    def save(self, settings: ModelSettings) -> None:
        if not isinstance(settings, ModelSettings):
            raise SettingsError("settings 类型无效")
        payload = {
            "api_mode": settings.api_mode,
            "base_url": settings.base_url,
            "model": settings.model,
        }
        api_mode, base_url, model = validate_draft(payload)
        if (api_mode, base_url, model) != (settings.api_mode, settings.base_url, settings.model):
            raise SettingsError("settings 内容无效")
        # Encrypt before replacing the JSON so a failure leaves the old JSON
        # untouched.  The two files are intentionally separate: the JSON can
        # be inspected for diagnostics without ever containing a key.
        if settings.api_key:
            record = json.dumps(
                {
                    "api_mode": settings.api_mode,
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "api_key": settings.api_key,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            encrypted = _protect_text(record, max_bytes=MAX_PROTECTED_RECORD_BYTES)
            _atomic_write(self.key_path, encrypted)
        else:
            try:
                self.key_path.unlink(missing_ok=True)
            except OSError as exc:
                raise SettingsError("旧密钥清除失败") from exc
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _atomic_write(self.settings_path, raw)

    def public(self) -> dict[str, Any]:
        loaded = self.load()
        if loaded is None:
            return {
                "api_mode": "chat_completions",
                "base_url": "",
                "model": "",
                "has_api_key": False,
            }
        return loaded.public()


__all__ = [
    "KEY_FILENAME",
    "MAX_API_KEY_BYTES",
    "ModelSettings",
    "ModelSettingsStore",
    "SETTINGS_DIRNAME",
    "SETTINGS_FILENAME",
    "SettingsError",
    "is_local_url",
    "make_draft",
    "protect_key",
    "unprotect_key",
    "validate_draft",
]
