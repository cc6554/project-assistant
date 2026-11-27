"""Safe, local workspace selection and copy helpers.

The desktop UI can select a different local workspace while the HTTP service
is running.  A selection is deliberately a copy operation: the old directory
is never moved, overwritten, or removed.  Existing destination data is kept
as-is and is opened as its own workspace; an empty destination is populated
with a non-overwriting copy of the current durable data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping


class WorkspaceError(ValueError):
    """The requested workspace cannot be selected safely."""


_UNC_PREFIXES = ("\\\\", "//")
_RUNTIME_COMPONENTS = frozenset(
    {
        "webview",
        "cache",
        "lock",
        "locks",
        "tmp",
        "temp",
    }
)
_RUNTIME_ROOT = ".agent-coach"
_INCOMPLETE_MARKER = ".workspace-copy.incomplete"
_PREPARED_PATH_KEYS = frozenset(
    {
        "resolved_path",
        "resolved_destination",
        "resolved_cwd",
        "cwd",
        "path",
        "destination",
        "workspace",
        "workspace_path",
    }
)
_SESSION_RELATIVE = (".agent-coach", "sessions")
_MAX_SESSION_BYTES = 64 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_EXTERNAL = object()


def _is_unc_path(value: str) -> bool:
    return value.startswith(_UNC_PREFIXES)


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
    except OSError as exc:
        raise WorkspaceError("无法检查工作区路径") from exc
    return bool(attributes & 0x400)


def _absolute_lexical(value: str | Path) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raw = os.fsdecode(raw)
    if _is_unc_path(raw):
        raise WorkspaceError("不支持 UNC 工作区路径")
    if not raw.strip():
        raise WorkspaceError("工作区路径不能为空")
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise WorkspaceError("工作区路径必须是绝对路径")
        return path.absolute()
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspaceError("工作区路径无效") from exc


def _existing_components(path: Path):
    current = Path(path.anchor) if path.anchor else Path()
    for part in path.parts:
        if part == path.anchor:
            continue
        current = current / part
        if current.exists() or current.is_symlink():
            yield current


def _assert_no_links(path: Path) -> None:
    for component in _existing_components(path):
        if _is_reparse_point(component):
            raise WorkspaceError("工作区路径包含符号链接或目录联接")


def canonical_workspace(value: str | Path, *, must_exist: bool = False) -> Path:
    """Validate a local absolute workspace path and return its canonical root."""

    lexical = _absolute_lexical(value)
    _assert_no_links(lexical)
    try:
        resolved = lexical.resolve(strict=must_exist)
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        raise WorkspaceError("工作区路径不存在或无法读取") from exc
    if must_exist and not resolved.is_dir():
        raise WorkspaceError("工作区必须是目录")
    return resolved


def _is_runtime_entry(relative: Path) -> bool:
    """Return whether ``relative`` is generated runtime content.

    User workspaces are allowed to contain ordinary project directories named
    ``cache``, ``temp``, or ``lock``.  Those names are runtime exclusions only
    below the application's ``.agent-coach`` state directory.
    """

    parts = tuple(part.casefold() for part in relative.parts)
    if not parts or parts[0] != _RUNTIME_ROOT:
        return False
    name = parts[-1]
    return (
        name == "headless-url.txt"
        or name == _INCOMPLETE_MARKER
        or name in _RUNTIME_COMPONENTS
        or name.endswith(".lock")
        or name.endswith(".tmp")
        or name in {".lock", ".tmp"}
    )


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _rewrite_prepared_path(
    value: Any,
    source: Path,
    target: Path,
    *,
    declared_source: Path | None = None,
) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    if _is_unc_path(raw):
        return _EXTERNAL
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            return value
        candidate = candidate.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return value
    for root in (source, declared_source):
        if root is None:
            continue
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        mapped = source / relative
        if not _is_within(source, mapped):
            return _EXTERNAL
        return str(target / relative)
    return _EXTERNAL


def _rewrite_prepared(
    value: Any,
    source: Path,
    target: Path,
    *,
    key: str | None = None,
    declared_source: Path | None = None,
) -> Any:
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for item_key, item_value in value.items():
            item_key_text = str(item_key)
            rewritten = _rewrite_prepared(
                item_value,
                source,
                target,
                key=item_key_text if item_key_text in _PREPARED_PATH_KEYS else None,
                declared_source=declared_source,
            )
            if rewritten is _EXTERNAL:
                continue
            result[item_key] = rewritten
        return result
    if isinstance(value, list):
        return [
            _rewrite_prepared(item, source, target, declared_source=declared_source)
            for item in value
        ]
    if key is not None:
        return _rewrite_prepared_path(value, source, target, declared_source=declared_source)
    return value


def _declared_workspace(payload: Mapping[str, Any]) -> Path | None:
    raw = payload.get("workspace")
    if not isinstance(raw, str) or not raw.strip() or _is_unc_path(raw):
        return None
    try:
        candidate = Path(raw)
        return candidate.resolve(strict=False) if candidate.is_absolute() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _rewrite_session(payload: dict[str, Any], source: Path, target: Path) -> bytes:
    migrated = dict(payload)
    migrated["workspace"] = str(target)
    pending = migrated.get("pending")
    if isinstance(pending, Mapping):
        pending_copy = dict(pending)
        prepared = pending_copy.get("prepared")
        if isinstance(prepared, Mapping):
            declared = _declared_workspace(payload)
            rewritten = _rewrite_prepared(prepared, source, target, declared_source=declared)
            if len(rewritten) < len(prepared):
                rewritten["migration_sanitized"] = True
                execution = dict(migrated.get("execution") or {})
                execution["status"] = "uncertain"
                execution.setdefault("reason", "工作区切换后待人工确认，未自动执行未完成操作")
                execution["migration_requires_confirmation"] = True
                migrated["execution"] = execution
            pending_copy["prepared"] = rewritten
        migrated["pending"] = pending_copy
    if not isinstance(migrated.get("id"), str) or not _SESSION_ID_RE.fullmatch(migrated["id"]):
        raise WorkspaceError("会话文件格式无效")
    try:
        # Import lazily so the helper stays usable during launcher startup.
        from .contracts import AgentSession

        AgentSession.model_validate(migrated)
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError("会话文件格式无效") from exc
    return (json.dumps(migrated, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _is_session_file(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return len(parts) >= 3 and parts[-3:-1] == _SESSION_RELATIVE and relative.suffix.casefold() == ".json"


def _read_session(source: Path, workspace_source: Path, workspace_target: Path) -> bytes:
    try:
        if source.stat().st_size > _MAX_SESSION_BYTES:
            raise WorkspaceError("会话文件超过切换大小上限")
        raw = source.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except WorkspaceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("会话文件无法安全切换") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("workspace"), str):
        raise WorkspaceError("会话文件缺少工作区绑定")
    return _rewrite_session(payload, workspace_source, workspace_target)


def _copy_file(source: Path, target: Path, *, content: bytes | None = None) -> tuple[int, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if content is None:
            with source.open("rb") as reader, target.open("xb") as writer:
                while True:
                    chunk = reader.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    writer.write(chunk)
        else:
            with target.open("xb") as writer:
                writer.write(content)
        with _suppress_os_errors():
            shutil.copystat(source, target, follow_symlinks=False)
        return 1, len(content) if content is not None else target.stat().st_size
    except (OSError, ValueError) as exc:
        raise WorkspaceError("复制工作区文件失败") from exc


class _suppress_os_errors:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is OSError


def _copy_tree(
    source: Path,
    target: Path,
    workspace_source: Path,
    workspace_target: Path,
    *,
    relative: Path = Path(""),
) -> tuple[int, int, int]:
    files = 0
    copied_bytes = 0
    excluded = 0
    target.mkdir(parents=True, exist_ok=True)
    try:
        entries = list(os.scandir(source))
    except OSError as exc:
        raise WorkspaceError("无法读取源工作区") from exc
    for entry in entries:
        name = entry.name
        if _is_runtime_entry(relative / name):
            excluded += 1
            continue
        source_entry = Path(entry.path)
        if _is_reparse_point(source_entry):
            raise WorkspaceError("源工作区包含符号链接或目录联接")
        relative_entry = relative / name
        target_entry = target / name
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceError("无法检查源工作区条目") from exc
        if is_dir:
            child_files, child_bytes, child_excluded = _copy_tree(
                source_entry,
                target_entry,
                workspace_source,
                workspace_target,
                relative=relative_entry,
            )
            files += child_files
            copied_bytes += child_bytes
            excluded += child_excluded
            with _suppress_os_errors():
                shutil.copystat(source_entry, target_entry, follow_symlinks=False)
            continue
        if not is_file:
            raise WorkspaceError("源工作区包含不支持的文件类型")
        content = _read_session(source_entry, workspace_source, workspace_target) if _is_session_file(relative_entry) else None
        count, size = _copy_file(source_entry, target_entry, content=content)
        files += count
        copied_bytes += size
    return files, copied_bytes, excluded


def _incomplete_marker(root: Path) -> Path:
    return root / _RUNTIME_ROOT / _INCOMPLETE_MARKER


def _assert_no_incomplete_copy(root: Path) -> None:
    """Reject a target left behind by an interrupted in-place population."""

    runtime = root / _RUNTIME_ROOT
    if runtime.exists() or runtime.is_symlink():
        if _is_reparse_point(runtime) or not runtime.is_dir():
            raise WorkspaceError("目标工作区运行目录无效")
    marker = _incomplete_marker(root)
    if marker.exists() or marker.is_symlink():
        raise WorkspaceError("目标工作区上一次复制未完成，请清理半成品后重试")


def _has_durable_entry(path: Path, relative: Path) -> bool:
    """Find durable content below the application state directory."""

    if _is_runtime_entry(relative):
        return False
    if _is_reparse_point(path):
        raise WorkspaceError("目标工作区包含符号链接或目录联接")
    try:
        if path.is_file():
            return True
        if not path.is_dir():
            raise WorkspaceError("目标工作区包含不支持的文件类型")
        children = list(path.iterdir())
    except WorkspaceError:
        raise
    except OSError as exc:
        raise WorkspaceError("无法读取目标工作区") from exc
    return any(
        _has_durable_entry(child, relative / child.name)
        for child in children
    )


def _persistent_entries(root: Path) -> list[Path]:
    """Return entries that count as user data for an existing target."""

    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise WorkspaceError("无法读取目标工作区") from exc
    result: list[Path] = []
    for entry in entries:
        if _is_reparse_point(entry):
            raise WorkspaceError("目标工作区包含符号链接或目录联接")
        # A newly-created destination may already contain the empty runtime
        # envelope.  It still counts as empty until it contains settings,
        # sessions, tokens, or another durable item.
        if entry.name.casefold() == ".agent-coach" and entry.is_dir():
            if not _has_durable_entry(entry, Path(_RUNTIME_ROOT)):
                continue
        result.append(entry)
    return result


def _begin_in_place_copy(root: Path) -> Path:
    """Install a durable marker before populating an existing empty target."""

    runtime = root / _RUNTIME_ROOT
    try:
        runtime.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError("无法创建目标工作区运行目录") from exc
    marker = _incomplete_marker(root)
    try:
        with marker.open("x", encoding="utf-8") as handle:
            handle.write("workspace copy in progress\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise WorkspaceError("目标工作区上一次复制未完成，请清理半成品后重试") from exc
    except OSError as exc:
        raise WorkspaceError("无法标记工作区复制状态") from exc
    return marker


def _validate_pair(source: Path, target: str | Path) -> Path:
    source_root = canonical_workspace(source, must_exist=True)
    target_lexical = _absolute_lexical(target)
    _assert_no_links(target_lexical)
    try:
        target_root = target_lexical.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("目标工作区路径无效") from exc
    if target_root == source_root:
        raise WorkspaceError("目标工作区必须与当前工作区不同")
    if _is_within(source_root, target_root):
        raise WorkspaceError("目标工作区不能位于当前工作区内")
    if target_root.exists() and not target_root.is_dir():
        raise WorkspaceError("目标工作区必须是目录")
    if target_root.exists() and _is_reparse_point(target_root):
        raise WorkspaceError("目标工作区不能是符号链接或目录联接")
    return target_root


def copy_or_open_workspace(source: str | Path, target: str | Path) -> dict[str, Any]:
    """Copy durable data into a new/empty target, or open existing target data.

    The returned payload contains only counts and booleans suitable for the
    HTTP API; it never exposes source file names, session IDs, or credentials.
    """

    source_root = canonical_workspace(source, must_exist=True)
    _assert_no_incomplete_copy(source_root)
    target_root = _validate_pair(source_root, target)
    created = not target_root.exists()
    copied = False
    files = 0
    copied_bytes = 0
    excluded = 0
    if target_root.exists():
        _assert_no_incomplete_copy(target_root)
        if _persistent_entries(target_root):
            return {
                "created": False,
                "copied": False,
                "target_existing": True,
                "files_copied": 0,
                "bytes_copied": 0,
                "excluded_entries": 0,
            }
        # An empty directory is a valid user-selected destination.  Populate
        # it in place, but still use exclusive file creation for every item.
        # The marker makes a mid-copy failure fail closed on the next attempt;
        # a partial destination must never be silently opened as a workspace.
        marker = _begin_in_place_copy(target_root)
        try:
            files, copied_bytes, excluded = _copy_tree(
                source_root,
                target_root,
                source_root,
                target_root,
            )
        except Exception:
            # Keep both the partial files and marker for an explicit recovery
            # decision.  Removing them here would make a process crash during
            # cleanup indistinguishable from a successful copy.
            raise
        try:
            marker.unlink()
        except OSError as exc:
            raise WorkspaceError("工作区复制完成但状态标记无法清理") from exc
        copied = True
    else:
        parent = target_root.parent
        parent.mkdir(parents=True, exist_ok=True)
        _assert_no_links(parent)
        temporary = parent / f".{target_root.name}.workspace-{uuid.uuid4().hex}.tmp"
        try:
            files, copied_bytes, excluded = _copy_tree(
                source_root,
                temporary,
                source_root,
                target_root,
            )
            if target_root.exists() or target_root.is_symlink():
                raise WorkspaceError("目标工作区在切换期间已存在")
            os.rename(temporary, target_root)
            copied = True
        except WorkspaceError:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        except (OSError, ValueError) as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise WorkspaceError("工作区切换未完成") from exc
    return {
        "created": created,
        "copied": copied,
        "target_existing": not created,
        "files_copied": files,
        "bytes_copied": copied_bytes,
        "excluded_entries": excluded,
    }


def write_selection_marker(path: str | Path, workspace: str | Path) -> None:
    """Atomically write a non-sensitive launcher workspace selection marker."""

    marker = Path(path).expanduser().resolve(strict=False)
    target = canonical_workspace(workspace, must_exist=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{marker.name}.", suffix=".tmp", dir=str(marker.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"workspace": str(target)}, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_selection_marker(path: str | Path) -> Path | None:
    marker = Path(path).expanduser()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    value = payload.get("workspace") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 4096:
        return None
    try:
        return canonical_workspace(value, must_exist=True)
    except WorkspaceError:
        return None


__all__ = [
    "WorkspaceError",
    "canonical_workspace",
    "copy_or_open_workspace",
    "read_selection_marker",
    "write_selection_marker",
]
