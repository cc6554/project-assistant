"""Workspace-scoped GUI model profiles.

The legacy GUI settings file stores one active provider.  This module adds a
small profile catalog without changing that compatibility file: profile
metadata is public and contains no key, while each profile key is encrypted
with the same Windows DPAPI helpers used by :mod:`agent_coach.settings`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit
import uuid

from .settings import (
    ModelSettings,
    ModelSettingsStore,
    SettingsError,
    protect_key,
    unprotect_key,
    validate_draft,
)


PROFILES_FILENAME = "model-profiles.json"
PROFILES_DIRNAME = "model-profiles"
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_PROFILE_NAME_BYTES = 128
MAX_PROFILE_RECORD_BYTES = 12 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(name)
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


def _restore_file(path: Path, previous: bytes | None) -> None:
    """Restore one file after a multi-file profile write fails.

    ``None`` means the file did not exist before the transaction.  Restoring
    the encrypted blob as bytes keeps the operation independent of DPAPI and
    avoids ever reconstructing a credential during rollback.
    """

    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, previous)


def _safe_name(value: Any, *, default: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str):
        raise SettingsError("display_name 必须是字符串")
    value = value.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise SettingsError("display_name 包含非法控制字符")
    if len(value.encode("utf-8")) > MAX_PROFILE_NAME_BYTES:
        raise SettingsError("display_name 超出长度限制")
    return value


def normalize_profile_id(value: Any, *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        value = default or uuid.uuid4().hex
    if not isinstance(value, str):
        raise SettingsError("profile_id 必须是字符串")
    value = value.strip()
    if not PROFILE_ID_RE.fullmatch(value):
        raise SettingsError("profile_id 格式无效")
    return value


def _default_name(settings: ModelSettings) -> str:
    host = urlsplit(settings.base_url).hostname or settings.base_url
    return f"{settings.model} · {host}"[:MAX_PROFILE_NAME_BYTES]


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    display_name: str
    settings: ModelSettings
    created_at: str
    updated_at: str

    def public(self, *, active: bool = False) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "name": self.display_name,
            "api_mode": self.settings.api_mode,
            "base_url": self.settings.base_url,
            "model": self.settings.model,
            "has_api_key": self.settings.has_api_key,
            "active": bool(active),
        }


class ModelProfilesStore:
    """Persist validated profile metadata and independently bound key blobs."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.directory = self.workspace / ".agent-coach"
        self.meta_path = self.directory / PROFILES_FILENAME
        self.keys_directory = self.directory / PROFILES_DIRNAME
        self.last_error: str | None = None

    def _key_path(self, profile_id: str) -> Path:
        return self.keys_directory / f"{profile_id}.key"

    def _empty_meta(self) -> dict[str, Any]:
        return {"version": 1, "active_profile_id": None, "profiles": []}

    def _read_meta(self) -> dict[str, Any]:
        self.last_error = None
        if not self.meta_path.is_file():
            return self._empty_meta()
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.last_error = "saved_profiles_invalid"
            return self._empty_meta()
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles", []), list):
            self.last_error = "saved_profiles_invalid"
            return self._empty_meta()
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw["profiles"]:
            if not isinstance(item, dict):
                self.last_error = "saved_profiles_invalid"
                continue
            try:
                # A missing id must invalidate the record rather than silently
                # generating a new id while reading persisted metadata.
                raw_profile_id = item.get("profile_id")
                if not isinstance(raw_profile_id, str) or not raw_profile_id.strip():
                    raise SettingsError("profile_id 缺失")
                profile_id = normalize_profile_id(raw_profile_id)
                if profile_id in seen:
                    raise SettingsError("profile_id 重复")
                api_mode, base_url, model = validate_draft(item)
                display_name = _safe_name(item.get("display_name", item.get("name")), default=model)
            except Exception:
                self.last_error = "saved_profiles_invalid"
                continue
            seen.add(profile_id)
            profiles.append(
                {
                    "profile_id": profile_id,
                    "display_name": display_name,
                    "api_mode": api_mode,
                    "base_url": base_url,
                    "model": model,
                    "created_at": str(item.get("created_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                }
            )
        active = raw.get("active_profile_id")
        # ``None`` is an intentional state: it means the YAML router is
        # selected while saved profiles remain available.  Only an explicit
        # stale/invalid id should fall back to the first surviving profile.
        if active is not None and active not in seen:
            active = profiles[0]["profile_id"] if profiles else None
        return {"version": 1, "active_profile_id": active, "profiles": profiles}

    def _write_meta(self, meta: dict[str, Any]) -> None:
        clean = {
            "version": 1,
            "active_profile_id": meta.get("active_profile_id"),
            "profiles": [
                {
                    "profile_id": item["profile_id"],
                    "display_name": item["display_name"],
                    "api_mode": item["api_mode"],
                    "base_url": item["base_url"],
                    "model": item["model"],
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                }
                for item in meta.get("profiles", [])
            ],
        }
        raw = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _atomic_write(self.meta_path, raw)

    def _read_profile(self, item: dict[str, str]) -> ModelProfile:
        settings = ModelSettings(
            api_mode=item["api_mode"],
            base_url=item["base_url"],
            model=item["model"],
            api_key=None,
        )
        key_path = self._key_path(item["profile_id"])
        if key_path.is_file():
            try:
                record = json.loads(unprotect_key(key_path.read_bytes()))
                if (
                    not isinstance(record, dict)
                    or record.get("profile_id") != item["profile_id"]
                    or record.get("api_mode") != settings.api_mode
                    or record.get("base_url") != settings.base_url
                    or record.get("model") != settings.model
                    or not isinstance(record.get("api_key"), str)
                    or not record["api_key"]
                    or len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_PROFILE_RECORD_BYTES
                ):
                    raise SettingsError("profile key binding mismatch")
                settings = ModelSettings(
                    api_mode=settings.api_mode,
                    base_url=settings.base_url,
                    model=settings.model,
                    api_key=record["api_key"],
                )
            except Exception:
                self.last_error = self.last_error or "saved_profile_key_unavailable"
        return ModelProfile(
            profile_id=item["profile_id"],
            display_name=item["display_name"],
            settings=settings,
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
        )

    def profiles(self) -> list[ModelProfile]:
        meta = self._read_meta()
        return [self._read_profile(item) for item in meta["profiles"]]

    def active_id(self) -> str | None:
        return self._read_meta().get("active_profile_id")

    def active(self) -> ModelProfile | None:
        meta = self._read_meta()
        active = meta.get("active_profile_id")
        for item in meta["profiles"]:
            if item["profile_id"] == active:
                return self._read_profile(item)
        return None

    def get(self, profile_id: str) -> ModelProfile:
        wanted = normalize_profile_id(profile_id)
        meta = self._read_meta()
        for item in meta["profiles"]:
            if item["profile_id"] == wanted:
                return self._read_profile(item)
        raise KeyError("profile 不存在")

    def public(self) -> list[dict[str, Any]]:
        meta = self._read_meta()
        active = meta.get("active_profile_id")
        return [self._read_profile(item).public(active=item["profile_id"] == active) for item in meta["profiles"]]

    def save(
        self,
        settings: ModelSettings,
        *,
        profile_id: str | None = None,
        display_name: str | None = None,
        activate: bool = True,
    ) -> ModelProfile:
        api_mode, base_url, model = validate_draft(
            {"api_mode": settings.api_mode, "base_url": settings.base_url, "model": settings.model}
        )
        profile_id = normalize_profile_id(profile_id)
        meta = self._read_meta()
        old = next((item for item in meta["profiles"] if item["profile_id"] == profile_id), None)
        now = _now()
        name = _safe_name(display_name, default=(old or {}).get("display_name") or _default_name(settings))
        created_at = (old or {}).get("created_at") or now
        item = {
            "profile_id": profile_id,
            "display_name": name,
            "api_mode": api_mode,
            "base_url": base_url,
            "model": model,
            "created_at": created_at,
            "updated_at": now,
        }
        if old is None:
            meta["profiles"].append(item)
        else:
            index = meta["profiles"].index(old)
            meta["profiles"][index] = item
        key_path = self._key_path(profile_id)
        # Save both old bytes before touching either file.  If DPAPI, the
        # metadata replace, or a filesystem operation fails, the previous
        # profile and its key binding must remain a usable pair.
        try:
            previous_key = key_path.read_bytes() if key_path.is_file() else None
            previous_meta = self.meta_path.read_bytes() if self.meta_path.is_file() else None
        except OSError as exc:
            raise SettingsError("读取旧 profile 配置失败") from exc
        try:
            if settings.api_key:
                record = json.dumps(
                    {
                        "profile_id": profile_id,
                        "api_mode": api_mode,
                        "base_url": base_url,
                        "model": model,
                        "api_key": settings.api_key,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if len(record.encode("utf-8")) > MAX_PROFILE_RECORD_BYTES:
                    raise SettingsError("profile 密钥记录超出长度限制")
                _atomic_write(key_path, protect_key(record))
            else:
                try:
                    key_path.unlink(missing_ok=True)
                except OSError as exc:
                    raise SettingsError("旧 profile 密钥清除失败") from exc
            if activate:
                meta["active_profile_id"] = profile_id
            self._write_meta(meta)
        except Exception:
            try:
                _restore_file(key_path, previous_key)
                _restore_file(self.meta_path, previous_meta)
            except Exception as rollback_exc:
                raise SettingsError("profile 保存失败且回滚失败") from rollback_exc
            raise
        return ModelProfile(
            profile_id=profile_id,
            display_name=name,
            settings=settings,
            created_at=created_at,
            updated_at=now,
        )

    def activate(self, profile_id: str) -> ModelProfile:
        profile = self.get(profile_id)
        meta = self._read_meta()
        meta["active_profile_id"] = profile.profile_id
        self._write_meta(meta)
        return profile

    def clear_active(self) -> None:
        """Leave saved profiles available while selecting a YAML provider."""

        meta = self._read_meta()
        if meta.get("active_profile_id") is None:
            return
        meta["active_profile_id"] = None
        self._write_meta(meta)

    def delete(self, profile_id: str) -> None:
        wanted = normalize_profile_id(profile_id)
        meta = self._read_meta()
        if meta.get("active_profile_id") == wanted:
            raise SettingsError("不能删除当前正在使用的配置")
        remaining = [item for item in meta["profiles"] if item["profile_id"] != wanted]
        if len(remaining) == len(meta["profiles"]):
            raise KeyError("profile 不存在")
        self._key_path(wanted).unlink(missing_ok=True)
        meta["profiles"] = remaining
        self._write_meta(meta)

    def migrate_legacy(self, legacy_store: ModelSettingsStore) -> ModelProfile | None:
        """Create the stable ``default`` profile from old single settings once."""

        if self.meta_path.is_file():
            return self.active()
        legacy = legacy_store.load()
        if legacy is None:
            return None
        return self.save(legacy, profile_id="default", display_name="默认配置", activate=True)


__all__ = [
    "MAX_PROFILE_NAME_BYTES",
    "ModelProfile",
    "ModelProfilesStore",
    "PROFILES_DIRNAME",
    "PROFILES_FILENAME",
    "normalize_profile_id",
]
