"""Safely copy one Agent Coach workspace to a fresh directory.

The migration boundary is deliberately small.  Workspace files are copied as
bytes, except for persisted session documents where the workspace binding and
the path values prepared by an approval are updated for the new root.  The
source is never modified and a pre-existing destination is never replaced.

This module is intentionally independent of the web application and of the
packaging scripts.  It is suitable for a user initiated, offline migration on
the same Windows account.  DPAPI protected files are copied unchanged; the
operating system can therefore decrypt them after the copy under the same
Windows user.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


# ``scripts/migrate_workspace.py`` is also useful when invoked directly from
# an unpacked checkout, where the package's ``src`` directory is not on
# sys.path yet.  Keep this small bootstrap local to this script; importing it
# never reads configuration, sessions, or credentials.
if __package__ in {None, ""}:  # pragma: no cover - exercised by CLI smoke tests
    _SRC = Path(__file__).resolve().parents[1] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

from agent_coach.contracts import AgentSession


class MigrationError(RuntimeError):
    """A migration was rejected before or during the copy."""


# These entries are generated runtime data rather than user workspace state.
# Matching is case-insensitive because the packaged desktop build may be
# moved between filesystems with different case rules.
_EXCLUDED_COMPONENTS = frozenset(
    {
        "webview",
        "cache",
        "lock",
        "locks",
        "tmp",
        "temp",
    }
)
_PREPARED_PATH_KEYS = frozenset(
    {
        # Current write/command/clone tools use these absolute resolved keys.
        "resolved_path",
        "resolved_destination",
        "resolved_cwd",
        "cwd",
        # Keep compatibility with prepared payloads from earlier builds while
        # still rewriting only values explicitly labelled as paths.
        "path",
        "destination",
        "workspace",
        "workspace_path",
    }
)
_UNFINISHED_EXECUTION_STATUSES = frozenset({"pending", "queued", "running"})
_MAX_SESSION_BYTES = 64 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_MIGRATION_CONFIRMATION = "工作区迁移后需要人工确认，未自动执行未完成操作"
_QUEUE_CONFIRMATION = "迁移时检测到未完成工具队列，需要人工确认，未自动执行"
_EXTERNAL_PREPARED = object()


@dataclass
class _Stats:
    files_copied: int = 0
    bytes_copied: int = 0
    files_skipped: int = 0
    excluded_entries: int = 0
    sessions_migrated: int = 0
    sessions_with_pending: int = 0
    sessions_marked_uncertain: int = 0

    def as_dict(self) -> dict[str, Any]:
        # This is deliberately a count-only result.  In particular, never put
        # source paths, session ids, profile names, or file names in a report.
        return {
            "ok": True,
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "files_skipped": self.files_skipped,
            "excluded_entries": self.excluded_entries,
            "sessions_migrated": self.sessions_migrated,
            "sessions_with_pending": self.sessions_with_pending,
            "sessions_marked_uncertain": self.sessions_marked_uncertain,
        }


def _is_unc_path(raw: str) -> bool:
    # Check the spelling before Path normalisation.  This catches UNC values on
    # Windows as well as test inputs passed from a POSIX host.
    return raw.startswith(("\\\\", "//"))


def _is_reparse_point(path: Path) -> bool:
    """Return True for POSIX symlinks and Windows reparse points."""

    try:
        if path.is_symlink():
            return True
        attributes = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
    except OSError as exc:
        raise MigrationError("无法检查工作区路径") from exc
    # FILE_ATTRIBUTE_REPARSE_POINT.  Do not import Windows-only modules just
    # for this bit; the attribute is exposed by stat on Windows Python.
    return bool(attributes & 0x400)


def _absolute_lexical(path: str | Path) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raw = os.fsdecode(raw)
    if _is_unc_path(raw):
        raise MigrationError("不支持 UNC 工作区路径")
    try:
        return Path(raw).expanduser().absolute()
    except (OSError, RuntimeError, ValueError) as exc:
        raise MigrationError("工作区路径无效") from exc


def _existing_components(path: Path) -> Iterable[Path]:
    """Yield existing components from the anchor to ``path``."""

    # ``Path.parents`` is reverse ordered and behaves differently for drive
    # roots, so build the chain from the anchor using ``parts``.
    current = Path(path.anchor) if path.anchor else Path()
    for part in path.parts:
        if part == path.anchor:
            continue
        current = current / part
        if current.exists() or current.is_symlink():
            yield current


def _assert_no_link_components(path: Path) -> None:
    for component in _existing_components(path):
        if _is_reparse_point(component):
            raise MigrationError("工作区路径包含符号链接或目录联接")


def _canonical_source(path: str | Path) -> Path:
    lexical = _absolute_lexical(path)
    _assert_no_link_components(lexical)
    try:
        source = lexical.resolve(strict=True)
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        raise MigrationError("源工作区不存在或无法读取") from exc
    if not source.is_dir():
        raise MigrationError("源工作区必须是目录")
    # Resolve must not have introduced a link after the lexical check.
    _assert_no_link_components(source)
    return source


def _canonical_target(path: str | Path, source: Path) -> Path:
    lexical = _absolute_lexical(path)
    _assert_no_link_components(lexical)
    if lexical.exists() or lexical.is_symlink():
        raise MigrationError("目标工作区必须是全新的不存在目录")
    try:
        target = lexical.resolve(strict=False)
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise MigrationError("目标工作区不能位于源工作区内")
    return target


def _is_excluded_name(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in _EXCLUDED_COMPONENTS
        or folded.endswith(".lock")
        or folded.endswith(".tmp")
        or folded in {".lock", ".tmp"}
    )


def _is_session_file(relative: Path) -> bool:
    parts = [part.casefold() for part in relative.parts]
    return (
        len(parts) >= 3
        and parts[-3:-1] == [".agent-coach", "sessions"]
        and relative.suffix.casefold() == ".json"
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
    # Relative display paths such as ``notes/role.md`` remain stable.  Only a
    # fully qualified path inside the old root is an internal path that must be
    # rebound.  URLs and command strings therefore remain untouched.
    raw = value.strip()
    if _is_unc_path(raw):
        return _EXTERNAL_PREPARED
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            return value
        candidate = candidate.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return value
    # A raw-copied workspace can be opened from a new physical root while an
    # older session still declares the original root in its JSON.  Prefer the
    # actual source root, then accept the declared root only to derive a
    # relative path that is re-bound inside the actual source/target pair.
    # Nothing from the declared root is read or copied.
    for root in (source, declared_source):
        if root is None:
            continue
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        mapped_source = source / relative
        if not _is_within(source, mapped_source):
            return _EXTERNAL_PREPARED
        return str(target / relative)
    return _EXTERNAL_PREPARED


def _rewrite_prepared(
    value: Any,
    source: Path,
    target: Path,
    *,
    key: str | None = None,
    declared_source: Path | None = None,
) -> Any:
    """Rewrite explicit path fields under a pending ``prepared`` mapping."""

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
            # An absolute prepared path outside the old workspace cannot be
            # rebound safely.  Drop it instead of carrying a stale external
            # path into the new session; the caller marks the session
            # uncertain so approval cannot accidentally execute it.
            if rewritten is _EXTERNAL_PREPARED:
                continue
            result[item_key] = rewritten
        return result
    if isinstance(value, list):
        return [
            _rewrite_prepared(
                item,
                source,
                target,
                key=None,
                declared_source=declared_source,
            )
            for item in value
        ]
    if key is not None:
        return _rewrite_prepared_path(
            value,
            source,
            target,
            declared_source=declared_source,
        )
    return value


def _migrate_execution(data: dict[str, Any], stats: _Stats) -> None:
    """Keep unfinished work recoverable while blocking automatic replay."""

    execution = data.get("execution")
    if isinstance(execution, Mapping):
        current = dict(execution)
        status = str(current.get("status") or "").casefold()
        if status in _UNFINISHED_EXECUTION_STATUSES:
            if status == "uncertain":
                # An existing uncertain result is evidence that must survive
                # migration unchanged; never clear or replay it.
                return
            current["status"] = "uncertain"
            current.setdefault("reason", _MIGRATION_CONFIRMATION)
            current["migration_requires_confirmation"] = True
            data["execution"] = current
            stats.sessions_marked_uncertain += 1

    queued = data.get("queued_calls")
    if isinstance(queued, list) and queued:
        current = dict(data.get("execution") or {})
        status = str(current.get("status") or "").casefold()
        if status != "uncertain":
            current["status"] = "uncertain"
            current.setdefault("reason", _QUEUE_CONFIRMATION)
            current["migration_requires_confirmation"] = True
            data["execution"] = current
            stats.sessions_marked_uncertain += 1


def _declared_workspace(payload: Mapping[str, Any]) -> Path | None:
    """Read an absolute legacy workspace root without touching that path."""

    raw = payload.get("workspace")
    if not isinstance(raw, str) or not raw.strip() or _is_unc_path(raw):
        return None
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            return None
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _migrate_session_payload(
    payload: dict[str, Any],
    source: Path,
    target: Path,
    stats: _Stats,
) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["workspace"] = str(target)
    declared_source = _declared_workspace(payload)
    external_prepared = False
    pending = migrated.get("pending")
    if isinstance(pending, Mapping):
        pending_copy = dict(pending)
        if isinstance(pending_copy.get("prepared"), Mapping):
            original_prepared = pending_copy["prepared"]
            rewritten_prepared = _rewrite_prepared(
                original_prepared,
                source,
                target,
                declared_source=declared_source,
            )
            external_prepared = len(rewritten_prepared) < len(original_prepared)
            if external_prepared:
                # Keep a redacted marker for diagnostics, never the removed
                # path value itself.  The execution claim below blocks replay.
                rewritten_prepared["migration_sanitized"] = True
            pending_copy["prepared"] = rewritten_prepared
        migrated["pending"] = pending_copy
        stats.sessions_with_pending += 1
    _migrate_execution(migrated, stats)
    if external_prepared:
        execution = dict(migrated.get("execution") or {})
        if str(execution.get("status") or "").casefold() != "uncertain":
            execution["status"] = "uncertain"
            execution.setdefault("reason", _MIGRATION_CONFIRMATION)
            execution["migration_requires_confirmation"] = True
            migrated["execution"] = execution
            stats.sessions_marked_uncertain += 1
    return migrated


def _read_session_json(
    source_file: Path,
    source_root: Path,
    target_root: Path,
    stats: _Stats,
) -> bytes:
    try:
        size = source_file.stat().st_size
        if size > _MAX_SESSION_BYTES:
            raise MigrationError("会话文件超过迁移大小上限")
        chunks: list[bytes] = []
        total = 0
        with source_file.open("rb") as reader:
            while True:
                chunk = reader.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_SESSION_BYTES:
                    raise MigrationError("会话文件超过迁移大小上限")
                chunks.append(chunk)
        raw = b"".join(chunks)
        payload = json.loads(raw.decode("utf-8"))
    except MigrationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("会话文件无法安全迁移") from exc
    if not isinstance(payload, dict):
        raise MigrationError("会话文件格式无效")
    # Files in this directory are expected to be sessions.  Requiring the two
    # binding fields prevents a random JSON cache from being silently treated
    # as a session while still allowing older sessions to carry extra fields.
    if not isinstance(payload.get("id"), str) or not isinstance(payload.get("workspace"), str):
        raise MigrationError("会话文件缺少工作区绑定")
    migrated = _migrate_session_payload(payload, source_root, target_root, stats)
    try:
        AgentSession.model_validate(migrated)
    except Exception as exc:
        raise MigrationError("会话文件格式无效") from exc
    encoded = (json.dumps(migrated, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return encoded


def _copy_file(source: Path, target: Path, stats: _Stats, *, content: bytes | None = None) -> None:
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
        # DPAPI key files and the legacy GUI settings remain byte-for-byte
        # copies.  Metadata is best effort because FAT/exFAT can lack some
        # POSIX fields, but copy failures are not allowed to replace content.
        with _suppress_os_errors():
            shutil.copystat(source, target, follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise MigrationError("复制工作区文件失败") from exc
    stats.files_copied += 1
    stats.bytes_copied += len(content) if content is not None else target.stat().st_size


class _suppress_os_errors:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is OSError


def _preflight_tree(source: Path) -> None:
    """Reject links before creating any destination files."""

    stack = [source]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise MigrationError("无法读取源工作区") from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_reparse_point(path):
                raise MigrationError("源工作区包含符号链接或目录联接")
            # Runtime cache directories are never copied.  Checking the entry
            # itself above still rejects a cache junction, while skipping its
            # contents avoids traversing a potentially huge WebView cache.
            if _is_excluded_name(entry.name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    continue
                else:
                    raise MigrationError("源工作区包含不支持的文件类型")
            except OSError as exc:
                raise MigrationError("无法检查源工作区条目") from exc


def _copy_tree(
    source: Path,
    target: Path,
    stats: _Stats,
    relative: Path = Path(""),
    *,
    workspace_source: Path,
    workspace_target: Path,
) -> None:
    try:
        target.mkdir(parents=True, exist_ok=True)
        entries = list(os.scandir(source))
    except OSError as exc:
        raise MigrationError("无法读取源工作区") from exc
    for entry in entries:
        name = entry.name
        source_entry = Path(entry.path)
        relative_entry = relative / name
        if _is_excluded_name(name):
            stats.excluded_entries += 1
            continue
        if _is_reparse_point(source_entry):
            raise MigrationError("源工作区包含符号链接或目录联接")
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError as exc:
            raise MigrationError("无法检查源工作区条目") from exc
        target_entry = target / name
        if is_dir:
            _copy_tree(
                source_entry,
                target_entry,
                stats,
                relative_entry,
                workspace_source=workspace_source,
                workspace_target=workspace_target,
            )
            with _suppress_os_errors():
                shutil.copystat(source_entry, target_entry, follow_symlinks=False)
            continue
        if not is_file:
            raise MigrationError("源工作区包含不支持的文件类型")
        content = (
            _read_session_json(source_entry, workspace_source, workspace_target, stats)
            if _is_session_file(relative_entry)
            else None
        )
        _copy_file(source_entry, target_entry, stats, content=content)
        if _is_session_file(relative_entry):
            stats.sessions_migrated += 1


def migrate_workspace(source: str | Path, target: str | Path) -> dict[str, Any]:
    """Copy ``source`` to a new ``target`` and return count-only statistics.

    No network, provider, model, or credential lookup occurs.  Existing target
    data is rejected before the source is traversed.  A failed copy removes the
    temporary destination, while a successful copy atomically publishes it.
    """

    source_root = _canonical_source(source)
    target_root = _canonical_target(target, source_root)
    # ``target_root`` may have a missing parent.  Check all existing ancestors
    # once more immediately before creating anything, preventing a parent
    # junction from becoming a write escape.
    _assert_no_link_components(target_root.parent)
    _preflight_tree(source_root)

    parent = target_root.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MigrationError("无法创建目标父目录") from exc
    _assert_no_link_components(parent)
    if target_root.exists() or target_root.is_symlink():
        raise MigrationError("目标工作区必须是全新的不存在目录")

    temp_root = parent / f".{target_root.name}.migration-{uuid.uuid4().hex}.tmp"
    stats = _Stats()
    try:
        _copy_tree(
            source_root,
            temp_root,
            stats,
            workspace_source=source_root,
            workspace_target=target_root,
        )
        if target_root.exists() or target_root.is_symlink():
            raise MigrationError("目标工作区在迁移期间已存在")
        # os.rename refuses to replace a directory on Windows.  The target was
        # checked both before and after the copy; this keeps accidental
        # overwrite out of the normal path while publishing the completed tree
        # in one filesystem operation.
        os.rename(temp_root, target_root)
    except MigrationError:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    except (OSError, ValueError) as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise MigrationError("迁移未完成") from exc
    return stats.as_dict()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将 Agent Coach 工作区迁移到全新目录")
    parser.add_argument("source", help="旧工作区绝对路径")
    parser.add_argument("target", help="新工作区绝对路径（必须不存在）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point with no paths, session ids, or file contents in output."""

    try:
        args = _parser().parse_args(argv)
        result = migrate_workspace(args.source, args.target)
    except (MigrationError, OSError, ValueError, SystemExit) as exc:
        # argparse's own --help/usage handling should retain its conventional
        # exit code.  For migration errors do not echo paths or OS details.
        if isinstance(exc, SystemExit):
            raise
        return 2
    # Count-only JSON is safe to show in a launcher log and is sufficient for a
    # caller to report a redacted migration summary.
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by direct invocation
    raise SystemExit(main())


__all__ = ["MigrationError", "main", "migrate_workspace"]
