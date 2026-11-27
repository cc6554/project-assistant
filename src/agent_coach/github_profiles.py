"""Workspace-scoped GitHub token profiles.

The public catalog stores only profile ids, display names, and the active id.
Every profile has its own DPAPI-protected record under ``.agent-coach``.  The
legacy ``github-token.key`` remains an encrypted mirror of the active token so
older consumers continue to work while the profile catalog is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
import uuid

from .legacy.tools.credentials import environment_github_token
from .settings import MAX_API_KEY_BYTES, SettingsError, protect_key, unprotect_key


GITHUB_TOKEN_FILENAME = "github-token.key"
GITHUB_TOKEN_DISABLED_FILENAME = "github-token.disabled"
GITHUB_PROFILES_FILENAME = "github-profiles.json"
GITHUB_PROFILES_DIRNAME = "github-profiles"
MAX_PROFILE_NAME_BYTES = 128
MAX_PROFILE_RECORD_BYTES = MAX_API_KEY_BYTES + 512
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PROFILE_TOKEN_UNSET = object()


def _is_unc_path(path: str | Path) -> bool:
    raw = os.fspath(path)
    return raw.startswith("\\\\") or raw.startswith("//")


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SettingsError("无法检查 GitHub 配置路径") from exc
    attrs = int(getattr(info, "st_file_attributes", 0))
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)


def _workspace_root(workspace: str | Path) -> Path:
    raw = Path(workspace).expanduser()
    if _is_unc_path(raw):
        raise SettingsError("workspace 不允许 UNC 远程路径")
    if raw.exists() and _is_reparse_or_symlink(raw):
        raise SettingsError("workspace 不能是符号链接或目录联接")
    root = raw.resolve(strict=False)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise SettingsError("workspace 不是安全目录")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_dir(workspace: str | Path) -> Path:
    directory = _workspace_root(workspace) / ".agent-coach"
    if directory.exists() and _is_reparse_or_symlink(directory):
        raise SettingsError("workspace 设置目录不能是符号链接或目录联接")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _reject_record_link(path: Path) -> None:
    try:
        if path.is_symlink() or _is_reparse_or_symlink(path):
            raise SettingsError("GitHub 配置记录不能是符号链接或目录联接")
    except FileNotFoundError:
        return


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
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, previous)


def _snapshot(path: Path) -> bytes | None:
    _reject_record_link(path)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError as exc:
        raise SettingsError("读取 GitHub 配置失败") from exc


def _validate_token(token: Any) -> str:
    if not isinstance(token, str) or not token.strip():
        raise SettingsError("GitHub Token 不能为空")
    clean = token.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in clean):
        raise SettingsError("GitHub Token 包含非法控制字符")
    if len(clean.encode("utf-8")) > MAX_API_KEY_BYTES:
        raise SettingsError("GitHub Token 超出长度限制")
    return clean


def _validate_profile_id(value: Any, *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        value = default or uuid.uuid4().hex
    if not isinstance(value, str):
        raise SettingsError("profile_id 必须是字符串")
    value = value.strip()
    if not PROFILE_ID_RE.fullmatch(value):
        raise SettingsError("profile_id 格式无效")
    return value


def _validate_display_name(value: Any, *, default: str = "默认 GitHub") -> str:
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


@dataclass(frozen=True)
class _Profile:
    profile_id: str
    display_name: str
    has_token: bool

    def public(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "has_token": self.has_token,
        }


class GitHubProfilesStore:
    """Read and transactionally update the GitHub profile catalog."""

    def __init__(self, workspace: str | Path):
        self.workspace = _workspace_root(workspace)
        self.directory = _settings_dir(self.workspace)
        self.meta_path = self.directory / GITHUB_PROFILES_FILENAME
        self.keys_directory = self.directory / GITHUB_PROFILES_DIRNAME
        self.mirror_path = self.directory / GITHUB_TOKEN_FILENAME
        self.disabled_path = self.directory / GITHUB_TOKEN_DISABLED_FILENAME

    def _key_path(self, profile_id: str) -> Path:
        return self.keys_directory / f"{profile_id}.key"

    def _ensure_keys_directory(self) -> None:
        if self.keys_directory.exists() and _is_reparse_or_symlink(self.keys_directory):
            raise SettingsError("GitHub profile 密钥目录不能是符号链接或目录联接")
        self.keys_directory.mkdir(parents=True, exist_ok=True)

    def _empty_meta(self) -> dict[str, Any]:
        return {"version": 1, "active_profile_id": None, "profiles": []}

    def _read_meta(self) -> dict[str, Any]:
        _reject_record_link(self.meta_path)
        if not self.meta_path.is_file():
            return self._empty_meta()
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return self._empty_meta()
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            return self._empty_meta()
        profiles: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw["profiles"]:
            if not isinstance(item, dict):
                continue
            try:
                raw_profile_id = item.get("profile_id")
                if not isinstance(raw_profile_id, str) or not raw_profile_id.strip():
                    raise SettingsError("profile_id 缺失")
                profile_id = _validate_profile_id(raw_profile_id)
                if profile_id in seen:
                    raise SettingsError("profile_id 重复")
                display_name = _validate_display_name(item.get("display_name"))
            except SettingsError:
                continue
            seen.add(profile_id)
            profiles.append({"profile_id": profile_id, "display_name": display_name})
        active = raw.get("active_profile_id")
        if active is not None and (not isinstance(active, str) or active not in seen):
            active = None
        return {"version": 1, "active_profile_id": active, "profiles": profiles}

    def _write_meta(self, meta: dict[str, Any]) -> None:
        profiles = []
        seen: set[str] = set()
        for item in meta.get("profiles", []):
            profile_id = _validate_profile_id(item.get("profile_id"))
            if profile_id in seen:
                raise SettingsError("profile_id 重复")
            seen.add(profile_id)
            profiles.append(
                {
                    "profile_id": profile_id,
                    "display_name": _validate_display_name(item.get("display_name")),
                }
            )
        active = meta.get("active_profile_id")
        if active is not None and active not in seen:
            raise SettingsError("active_profile_id 不存在")
        raw = json.dumps(
            {"version": 1, "active_profile_id": active, "profiles": profiles},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_write(self.meta_path, raw)

    def _read_profile_token(self, profile_id: str) -> str | None:
        # Checking only the leaf is insufficient when the profile directory
        # itself is a junction/symlink to another workspace.
        _reject_record_link(self.keys_directory)
        path = self._key_path(profile_id)
        try:
            _reject_record_link(path)
            if not path.is_file() or path.stat().st_size > MAX_PROFILE_RECORD_BYTES:
                return None
            record = json.loads(unprotect_key(path.read_bytes()))
            if not isinstance(record, dict) or record.get("profile_id") != profile_id:
                return None
            return _validate_token(record.get("token"))
        except (OSError, SettingsError, ValueError, json.JSONDecodeError):
            return None

    def _read_mirror_token(self) -> str | None:
        try:
            _reject_record_link(self.mirror_path)
            if not self.mirror_path.is_file() or self.mirror_path.stat().st_size > MAX_PROFILE_RECORD_BYTES:
                return None
            return _validate_token(unprotect_key(self.mirror_path.read_bytes()))
        except (OSError, SettingsError, ValueError):
            return None

    def _profile_items(self, meta: dict[str, Any]) -> list[_Profile]:
        return [
            _Profile(
                profile_id=item["profile_id"],
                display_name=item["display_name"],
                has_token=self._read_profile_token(item["profile_id"]) is not None,
            )
            for item in meta["profiles"]
        ]

    def _migrate_legacy(self) -> None:
        _reject_record_link(self.meta_path)
        _reject_record_link(self.disabled_path)
        if self.meta_path.is_file() or self.disabled_path.is_file():
            return
        token = self._read_mirror_token()
        if token is None:
            return
        profile_id = "default"
        self._ensure_keys_directory()
        key_path = self._key_path(profile_id)
        previous = {
            self.meta_path: _snapshot(self.meta_path),
            key_path: _snapshot(key_path),
        }
        meta = self._empty_meta()
        meta["active_profile_id"] = profile_id
        meta["profiles"] = [{"profile_id": profile_id, "display_name": "默认 GitHub"}]
        try:
            self._write_profile_key(profile_id, token)
            self._write_meta(meta)
        except Exception:
            try:
                for path, old in previous.items():
                    _restore_file(path, old)
            except Exception as rollback_exc:
                raise SettingsError("GitHub Token 迁移失败且回滚失败") from rollback_exc
            raise

    def catalog(self) -> dict[str, Any]:
        # This migration reads only the encrypted legacy file.  It never
        # persists or imports AGENT_COACH_GITHUB_TOKEN.
        self._migrate_legacy()
        _reject_record_link(self.keys_directory)
        meta = self._read_meta()
        profiles = self._profile_items(meta)
        active_id = meta.get("active_profile_id")
        active = next((item for item in profiles if item.profile_id == active_id), None)
        return {
            "profiles": [item.public() for item in profiles],
            "active_profile_id": active_id,
            "has_token": bool(active and active.has_token),
        }

    def active_token(self) -> str | None:
        self._migrate_legacy()
        _reject_record_link(self.keys_directory)
        meta = self._read_meta()
        active_id = meta.get("active_profile_id")
        if isinstance(active_id, str):
            token = self._read_profile_token(active_id)
            if token is not None:
                return token
        # The mirror is retained for old consumers and can recover a profile
        # key after an interrupted metadata write.  An explicit clear removes
        # it and leaves the disabled marker, so it cannot resurrect a token.
        return self._read_mirror_token()

    def _transaction_paths(self, profile_id: str | None = None) -> dict[Path, bytes | None]:
        paths = {
            self.meta_path: _snapshot(self.meta_path),
            self.mirror_path: _snapshot(self.mirror_path),
            self.disabled_path: _snapshot(self.disabled_path),
        }
        if profile_id is not None:
            self._ensure_keys_directory()
            paths[self._key_path(profile_id)] = _snapshot(self._key_path(profile_id))
        return paths

    @staticmethod
    def _rollback(previous: dict[Path, bytes | None]) -> None:
        for path, old in previous.items():
            _restore_file(path, old)

    def _write_profile_key(self, profile_id: str, token: str) -> None:
        self._ensure_keys_directory()
        record = json.dumps(
            {"profile_id": profile_id, "token": token},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(record.encode("utf-8")) > MAX_PROFILE_RECORD_BYTES:
            raise SettingsError("GitHub profile 密钥记录超出长度限制")
        _atomic_write(self._key_path(profile_id), protect_key(record))

    def create_profile(self, display_name: Any, token: Any) -> dict[str, Any]:
        clean_token = _validate_token(token)
        clean_name = _validate_display_name(display_name)
        # A direct POST can be the first read of a pre-profile workspace.
        # Migrate the encrypted legacy mirror before appending a new profile.
        self._migrate_legacy()
        meta = self._read_meta()
        profile_id = _validate_profile_id(None)
        while any(item["profile_id"] == profile_id for item in meta["profiles"]):
            profile_id = _validate_profile_id(None)
        previous = self._transaction_paths(profile_id)
        meta["profiles"].append({"profile_id": profile_id, "display_name": clean_name})
        meta["active_profile_id"] = profile_id
        try:
            self._write_profile_key(profile_id, clean_token)
            _atomic_write(self.mirror_path, protect_key(clean_token))
            self.disabled_path.unlink(missing_ok=True)
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub profile 保存失败且回滚失败") from rollback_exc
            raise
        return self.catalog()

    def update_profile(
        self,
        profile_id: Any,
        display_name: Any = None,
        token: Any = _PROFILE_TOKEN_UNSET,
    ) -> dict[str, Any]:
        """Update one profile while keeping its stable id and encrypted key.

        The token is write-only.  A missing, ``None``, or blank token means
        "keep the existing encrypted value" so an edit form never has to
        round-trip a credential back through the browser.  The profile name
        is also retained when omitted or blank, which makes a partial edit
        safe for callers that only want to rotate a token.
        """

        if profile_id is None or (isinstance(profile_id, str) and not profile_id.strip()):
            raise SettingsError("profile_id 不能为空")
        wanted = _validate_profile_id(profile_id)
        self._migrate_legacy()
        meta = self._read_meta()
        existing = next(
            (item for item in meta["profiles"] if item["profile_id"] == wanted),
            None,
        )
        if existing is None:
            raise KeyError("GitHub profile 不存在")

        clean_name = existing["display_name"]
        if display_name is not None and not (
            isinstance(display_name, str) and not display_name.strip()
        ):
            clean_name = _validate_display_name(display_name, default=clean_name)

        replacement: str | None = None
        if token is not _PROFILE_TOKEN_UNSET and token is not None:
            if not isinstance(token, str):
                raise SettingsError("GitHub Token 必须是字符串")
            if token.strip():
                replacement = _validate_token(token)

        previous = self._transaction_paths(wanted)
        updated = {
            "profile_id": wanted,
            "display_name": clean_name,
        }
        index = meta["profiles"].index(existing)
        meta["profiles"][index] = updated
        active = meta.get("active_profile_id") == wanted
        try:
            if replacement is not None:
                self._write_profile_key(wanted, replacement)
                if active:
                    _atomic_write(self.mirror_path, protect_key(replacement))
                    self.disabled_path.unlink(missing_ok=True)
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub profile 更新失败且回滚失败") from rollback_exc
            raise
        return self.catalog()

    def save_legacy_token(self, token: Any) -> dict[str, Any]:
        clean_token = _validate_token(token)
        self._migrate_legacy()
        meta = self._read_meta()
        active_id = meta.get("active_profile_id")
        if not isinstance(active_id, str):
            active_id = "default"
            if any(item["profile_id"] == active_id for item in meta["profiles"]):
                active_id = _validate_profile_id(None)
            meta["profiles"].append({"profile_id": active_id, "display_name": "默认 GitHub"})
        previous = self._transaction_paths(active_id)
        meta["active_profile_id"] = active_id
        try:
            self._write_profile_key(active_id, clean_token)
            _atomic_write(self.mirror_path, protect_key(clean_token))
            self.disabled_path.unlink(missing_ok=True)
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub Token 保存失败且回滚失败") from rollback_exc
            raise
        return {"saved": True, "has_token": True}

    def select(self, profile_id: Any) -> dict[str, Any]:
        if profile_id is None or (isinstance(profile_id, str) and not profile_id.strip()):
            raise SettingsError("profile_id 不能为空")
        wanted = _validate_profile_id(profile_id)
        # A direct select can target the migrated ``default`` profile without
        # a preceding GET request.
        self._migrate_legacy()
        meta = self._read_meta()
        if not any(item["profile_id"] == wanted for item in meta["profiles"]):
            raise KeyError("GitHub profile 不存在")
        token = self._read_profile_token(wanted)
        if token is None:
            raise SettingsError("GitHub profile Token 不可用")
        previous = self._transaction_paths()
        meta["active_profile_id"] = wanted
        try:
            _atomic_write(self.mirror_path, protect_key(token))
            self.disabled_path.unlink(missing_ok=True)
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub profile 切换失败且回滚失败") from rollback_exc
            raise
        return self.catalog()

    def delete_profile(self, profile_id: Any) -> dict[str, Any]:
        """Delete a profile and keep the active mirror/router state coherent.

        If the deleted profile is active, the first remaining profile with an
        available encrypted token becomes active.  Profile order is stable,
        so fallback selection is deterministic.  When no usable profile
        remains, the active id and legacy mirror are removed and the disabled
        marker prevents an environment token from silently reappearing.
        """

        if profile_id is None or (isinstance(profile_id, str) and not profile_id.strip()):
            raise SettingsError("profile_id 不能为空")
        wanted = _validate_profile_id(profile_id)
        self._migrate_legacy()
        meta = self._read_meta()
        existing = next(
            (item for item in meta["profiles"] if item["profile_id"] == wanted),
            None,
        )
        if existing is None:
            raise KeyError("GitHub profile 不存在")

        active = meta.get("active_profile_id") == wanted
        remaining = [item for item in meta["profiles"] if item["profile_id"] != wanted]
        fallback_id: str | None = None
        fallback_token: str | None = None
        if active:
            for item in remaining:
                candidate = self._read_profile_token(item["profile_id"])
                if candidate is not None:
                    fallback_id = item["profile_id"]
                    fallback_token = candidate
                    break

        previous = self._transaction_paths(wanted)
        meta["profiles"] = remaining
        meta["active_profile_id"] = fallback_id if active else meta.get("active_profile_id")
        try:
            self._key_path(wanted).unlink(missing_ok=True)
            if active:
                if fallback_id is not None and fallback_token is not None:
                    _atomic_write(self.mirror_path, protect_key(fallback_token))
                    self.disabled_path.unlink(missing_ok=True)
                else:
                    self.mirror_path.unlink(missing_ok=True)
                    _atomic_write(self.disabled_path, b"cleared\n")
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub profile 删除失败且回滚失败") from rollback_exc
            raise
        return self.catalog()

    def clear_active(self) -> dict[str, Any]:
        meta = self._read_meta()
        active_id = meta.get("active_profile_id")
        previous = self._transaction_paths(active_id if isinstance(active_id, str) else None)
        if isinstance(active_id, str):
            meta["profiles"] = [item for item in meta["profiles"] if item["profile_id"] != active_id]
        meta["active_profile_id"] = None
        try:
            if isinstance(active_id, str):
                self._key_path(active_id).unlink(missing_ok=True)
            self.mirror_path.unlink(missing_ok=True)
            _atomic_write(self.disabled_path, b"cleared\n")
            self._write_meta(meta)
        except Exception:
            try:
                self._rollback(previous)
            except Exception as rollback_exc:
                raise SettingsError("GitHub Token 清除失败且回滚失败") from rollback_exc
            raise
        return {"cleared": True, "has_token": False}


def github_profile_catalog(workspace: str | Path) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).catalog()


def save_github_profile(workspace: str | Path, display_name: Any, token: Any) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).create_profile(display_name, token)


def update_github_profile(
    workspace: str | Path,
    profile_id: Any,
    display_name: Any = None,
    token: Any = None,
) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).update_profile(profile_id, display_name, token)


def delete_github_profile(workspace: str | Path, profile_id: Any) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).delete_profile(profile_id)


def select_github_profile(workspace: str | Path, profile_id: Any) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).select(profile_id)


def save_legacy_github_token(workspace: str | Path, token: Any) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).save_legacy_token(token)


def clear_github_token(workspace: str | Path) -> dict[str, Any]:
    return GitHubProfilesStore(workspace).clear_active()


def load_github_token(workspace: str | Path) -> str | None:
    store = GitHubProfilesStore(workspace)
    token = store.active_token()
    if token is not None:
        return token
    if store.disabled_path.is_file():
        return None
    return environment_github_token()


__all__ = [
    "GITHUB_PROFILES_DIRNAME",
    "GITHUB_PROFILES_FILENAME",
    "GitHubProfilesStore",
    "clear_github_token",
    "delete_github_profile",
    "github_profile_catalog",
    "load_github_token",
    "save_github_profile",
    "save_legacy_github_token",
    "select_github_profile",
    "update_github_profile",
]
