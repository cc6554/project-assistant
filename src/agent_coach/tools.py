"""Built-in, domain-neutral tools for the agent kernel.

The functions in this module deliberately do not pretend to be an operating
system sandbox.  They put a small, explicit policy boundary around paths and
network requests, and leave approval of mutating operations to the kernel:
handlers prepare an operation and ``ToolDefinition.execute`` performs it only
after the returned :class:`~agent_coach.contracts.PendingApproval` is accepted.
"""

from __future__ import annotations

import atexit
import difflib
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urljoin, urlsplit

from .contracts import (
    PendingApproval,
    ToolContext,
    ToolDefinition,
    ToolOutcome,
    ToolRegistry,
)
from .legacy.core.schema import ToolSpec
from .legacy.tools.text_codec import TextDecodingError, decode_text_bytes


# These limits are deliberately finite.  A tool result is sent back through an
# LLM context, so accepting an unbounded file or command output is both a
# reliability and a data-exposure problem.
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_WRITE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_FETCH_BYTES = 4 * 1024 * 1024
MAX_FETCH_REDIRECTS = 5
# A command has no implicit deadline.  A caller may still opt into a bounded
# timeout, but the limit is deliberately measured in days rather than the old
# arbitrary two-minute cap.  This keeps ordinary command sessions compatible
# with long-running developer commands while retaining a finite guardrail for
# malformed/model-generated values.
DEFAULT_COMMAND_TIMEOUT: float | None = None
MAX_COMMAND_TIMEOUT = 7 * 24 * 60 * 60.0
DEFAULT_FETCH_TIMEOUT = 15.0
MAX_FETCH_TIMEOUT = 60.0
MAX_COMMAND_PURPOSE_BYTES = 2 * 1024
MAX_COMMAND_SESSIONS = 128
COMMAND_STOP_GRACE_SECONDS = 2.0
MAX_COMMAND_POLL_WAIT_SECONDS = 30.0
DEFAULT_COMMAND_POLL_WAIT_SECONDS = 0.0
COMMAND_INITIAL_HANDOFF_WAIT_SECONDS = 0.25


class PathSecurityError(PermissionError, ValueError):
    """Raised when a requested path violates the workspace policy."""


class ToolInputError(ValueError):
    """Raised when a tool argument cannot be interpreted safely."""


_BLOCKED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".ssh",
        ".agent-coach",
        "private",
        "session",
        "sessions",
        "credential",
        "credentials",
        "token",
        "tokens",
        "key",
        "keys",
        "secret",
        "secrets",
    }
)
_BLOCKED_EXACT_NAMES = frozenset(
    {
        "credential",
        "credentials",
        "token",
        "tokens",
        "key",
        "keys",
        "secret",
        "secrets",
        "private",
        "password",
        "passwd",
    }
)
_PRIVATE_NAME_RE = re.compile(
    r"(?:^|[._-])(?:credential|credentials|secret|secrets|token|tokens|private|key|keys)(?=$|[._-])",
    re.IGNORECASE,
)
_ENV_NAME_RE = re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE)
_SENSITIVE_ENV_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|API_KEY|AUTH)",
    re.IGNORECASE,
)
_METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.google",
        "instance-data",
        "instance-data.ec2.internal",
        "169.254.169.254",
        "100.100.100.200",  # common cloud metadata endpoint
    }
)


def _json(value: Any) -> str:
    """Encode a result in a stable, JSON-compatible content string."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _outcome(data: Mapping[str, Any]) -> ToolOutcome:
    payload = dict(data)
    return ToolOutcome(content=_json(payload), data=payload)


def _arg(args: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in args:
            return args[name]
    return default


def _require_mapping(args: Any) -> Mapping[str, Any]:
    if not isinstance(args, Mapping):
        raise ToolInputError("tool arguments must be an object")
    return args


def _reject_unknown_arguments(args: Mapping[str, Any], allowed: set[str]) -> None:
    """Keep sensitive integration arguments out of silently ignored fields."""

    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ToolInputError(f"unsupported tool arguments: {', '.join(unknown)}")


def _workspace_root(ctx: ToolContext) -> Path:
    root = Path(ctx.workspace)
    try:
        root = root.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"cannot resolve workspace: {exc}") from exc
    if root.exists() and not root.is_dir():
        raise PathSecurityError("workspace is not a directory")
    return root


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _sensitive_component(name: str, *, is_final: bool) -> bool:
    folded = name.casefold()
    if folded in _BLOCKED_DIRECTORY_NAMES:
        return True
    if _ENV_NAME_RE.match(name) or folded == ".envrc":
        return True
    if folded in _BLOCKED_EXACT_NAMES:
        return True
    # Keep the common credential-bearing file names out of the tool surface.
    # The separator-aware key check avoids treating a normal ``tokenizer.py``
    # or ``keyboard.py`` as a secret merely because of a substring.
    if _PRIVATE_NAME_RE.search(name):
        return True
    if is_final and folded.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def _relative_parts(root: Path, candidate: Path) -> tuple[str, ...]:
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return ()
    return tuple(part for part in rel.parts if part not in ("", "."))


def _reject_sensitive(root: Path, candidate: Path) -> None:
    parts = _relative_parts(root, candidate)
    for index, part in enumerate(parts):
        if _sensitive_component(part, is_final=index == len(parts) - 1):
            raise PathSecurityError(f"path is protected: {part}")


def resolve_path(ctx: ToolContext, path: str, must_exist: bool = False) -> Path:
    """Resolve ``path`` under ``ctx.workspace`` with symlink containment.

    ``Path.resolve(strict=False)`` resolves existing symlinks in every parent,
    including a final symlink, so a link that points outside the workspace is
    rejected before a caller can open it.  The returned path is canonical and
    can safely be used by a skill for subsequent read-only work.
    """

    if not isinstance(path, (str, os.PathLike)):
        raise ToolInputError("path must be a string")
    raw_value = os.fspath(path)
    if "\x00" in raw_value:
        raise PathSecurityError("NUL is not allowed in a path")

    root = _workspace_root(ctx)
    raw = Path(raw_value)
    lexical = raw if raw.is_absolute() else root / raw
    try:
        candidate = lexical.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"cannot resolve path: {exc}") from exc
    if not _within(root, candidate):
        raise PathSecurityError("path escapes workspace")

    # Check both the canonical target and lexical path.  The latter prevents a
    # sensitive link name from being exposed even when it points to a benign
    # location inside the workspace.
    _reject_sensitive(root, candidate)
    lexical_for_parts = lexical
    if _within(root, lexical_for_parts):
        _reject_sensitive(root, lexical_for_parts)

    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"path does not exist: {path}")
    return candidate


def _display_path(ctx: ToolContext, path: Path) -> str:
    root = _workspace_root(ctx)
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    return "." if str(relative) == "." else relative.as_posix()


def _safe_limit(value: Any, *, default: int, maximum: int, minimum: int = 1) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolInputError("limit must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError("limit must be an integer") from exc
    if number < minimum or number > maximum:
        raise ToolInputError(f"limit must be between {minimum} and {maximum}")
    return number


def _safe_bool(value: Any, *, default: bool, field: str) -> bool:
    """Accept only a real JSON/Python boolean for boolean tool arguments.

    ``bool('false')`` is ``True`` in Python and would make a model or HTTP
    adapter accidentally change command semantics.  Keep coercion out of the
    approval boundary so the prepared value and the approved value compare
    exactly.
    """

    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolInputError(f"{field} must be a boolean")
    return value


def _safe_timeout(value: Any, *, default: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolInputError("timeout must be a number")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError("timeout must be a number") from exc
    if not timeout == timeout or timeout in (float("inf"), float("-inf")):
        raise ToolInputError("timeout must be a finite number")
    if timeout <= 0 or timeout > maximum:
        raise ToolInputError(f"timeout must be greater than 0 and at most {maximum}")
    return timeout


def _safe_optional_timeout(value: Any, *, maximum: float) -> float | None:
    """Parse a command timeout where omitted/null means run until completion.

    ``run_command`` is intentionally a session-style operation.  It must not
    silently terminate a useful build/test command merely because a default
    HTTP or model request window elapsed.  An explicit positive timeout still
    gives callers a deterministic deadline.
    """

    if value is None:
        return None
    return _safe_timeout(value, default=0.0, maximum=maximum)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_bounded(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolInputError(f"cannot stat file: {exc}") from exc
    if size > limit:
        raise ToolInputError(f"file is larger than the {limit}-byte limit")
    try:
        with path.open("rb") as handle:
            value = handle.read(limit + 1)
    except OSError as exc:
        raise ToolInputError(f"cannot read file: {exc}") from exc
    if len(value) > limit:
        raise ToolInputError(f"file is larger than the {limit}-byte limit")
    return value


def _check_regular_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"file does not exist: {path}")
    if not path.is_file():
        raise ToolInputError("path is not a regular file")


def _check_parent(ctx: ToolContext, path: Path) -> Path:
    parent = resolve_path(ctx, str(path.parent), must_exist=True)
    if not parent.is_dir():
        raise ToolInputError("parent path is not a directory")
    return parent


def _read_file_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(args)
    path_arg = _arg(args, "path", "file")
    if path_arg is None:
        raise ToolInputError("path is required")
    path = resolve_path(ctx, path_arg, must_exist=True)
    _check_regular_file(path)
    raw = _read_bounded(path, MAX_READ_BYTES)
    try:
        text = decode_text_bytes(raw)
    except TextDecodingError as exc:
        raise ToolInputError(str(exc)) from exc
    return _outcome(
        {
            "path": _display_path(ctx, path),
            "size": len(raw),
            "content": text,
        }
    )


def _list_dir_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(args)
    path_arg = _arg(args, "path", default=".")
    path = resolve_path(ctx, path_arg, must_exist=True)
    if not path.is_dir():
        raise ToolInputError("path is not a directory")
    limit = _safe_limit(
        _arg(args, "limit", "max_entries"), default=500, maximum=5000
    )

    names: list[str] = []
    truncated = False
    # ``iterdir`` is intentionally bounded so a directory with millions of
    # entries cannot create an unbounded tool response.
    for child in path.iterdir():
        try:
            safe_child = resolve_path(ctx, str(child), must_exist=False)
        except (PathSecurityError, OSError, ValueError):
            # A protected or outside symlink is hidden from a directory listing.
            continue
        if not safe_child.exists():
            continue
        names.append(child.name)
        if len(names) > limit:
            truncated = True
            break
    names.sort(key=lambda value: value.casefold())
    if truncated:
        names = names[:limit]
    return _outcome(
        {
            "path": _display_path(ctx, path),
            "entries": names,
            "count": len(names),
            "truncated": truncated,
        }
    )


def _document_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(args)
    path_arg = _arg(args, "path", "file")
    if path_arg is None:
        raise ToolInputError("path is required")
    path = resolve_path(ctx, path_arg, must_exist=True)
    _check_regular_file(path)
    # The legacy reader handles PDF/Word/Excel/PPT as well as text.  Check the
    # source size before handing it over so a document reader cannot consume an
    # unbounded archive or text file.
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ToolInputError(f"document exceeds the {MAX_DOCUMENT_BYTES}-byte limit")
    from .legacy.tools.documents import read_document

    try:
        text = read_document(path)
    except TextDecodingError as exc:
        raise ToolInputError(str(exc)) from exc
    if not isinstance(text, str):
        text = str(text)
    truncated = len(text.encode("utf-8")) > MAX_DOCUMENT_OUTPUT_BYTES
    if truncated:
        encoded = text.encode("utf-8")[:MAX_DOCUMENT_OUTPUT_BYTES]
        text = encoded.decode("utf-8", errors="ignore")
    return _outcome(
        {
            "path": _display_path(ctx, path),
            "content": text,
            "truncated": truncated,
        }
    )


def _write_preview(old: bytes, new: bytes, display: str) -> str:
    old_text = old.decode("utf-8", errors="replace")
    new_text = new.decode("utf-8", errors="replace")
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{display}",
        tofile=f"b/{display}",
        lineterm="\n",
    )
    # ``difflib`` preserves a source line's missing final newline.  Add a
    # display newline to each emitted record so headers/hunks/changed lines
    # cannot run together in an approval preview.  The source bytes themselves
    # are still written exactly as supplied after approval.
    rendered = "".join(line if line.endswith("\n") else line + "\n" for line in diff)
    return rendered if rendered else "(no changes)"


def _write_prepare(args: dict, ctx: ToolContext) -> PendingApproval:
    args = _require_mapping(args)
    path_arg = _arg(args, "path", "file")
    if path_arg is None:
        raise ToolInputError("path is required")
    content = _arg(args, "content", "text")
    if not isinstance(content, str):
        raise ToolInputError("content must be a UTF-8 string")
    new = content.encode("utf-8")
    if len(new) > MAX_WRITE_BYTES:
        raise ToolInputError(f"content exceeds the {MAX_WRITE_BYTES}-byte limit")

    path = resolve_path(ctx, path_arg, must_exist=False)
    parent = _check_parent(ctx, path)
    if path.exists() and not path.is_file():
        raise ToolInputError("path is not a regular file")
    # A final symlink is rejected even when it points back into the workspace;
    # this keeps the approval's canonical target stable until execution.
    lexical = Path(os.fspath(path_arg))
    if not lexical.is_absolute():
        lexical = Path(ctx.workspace) / lexical
    if lexical.is_symlink():
        raise PathSecurityError("writing through a symlink is not allowed")

    old = _read_bounded(path, MAX_WRITE_BYTES) if path.exists() else b""
    before_hash = _hash_bytes(old) if path.exists() else None
    display = _display_path(ctx, path)
    preview = _write_preview(old, new, display)
    prepared = {
        "resolved_path": str(path),
        "path": display,
        "before_hash": before_hash,
        "after_hash": _hash_bytes(new),
        "before_exists": path.exists(),
        "size": len(new),
    }
    original = dict(args)
    return PendingApproval(
        tool_name="write_file",
        arguments=original,
        summary=f"目的：将审批后的 UTF-8 内容写入工作区文件 {display}（{len(new)} 字节）\n预期结果：原子替换目标文件并保留完整内容。",
        preview=preview,
        prepared=prepared,
    )


def _write_execute(pending: PendingApproval, ctx: ToolContext) -> ToolOutcome:
    arguments = _require_mapping(pending.arguments)
    path_arg = _arg(arguments, "path", "file")
    content = _arg(arguments, "content", "text")
    if path_arg is None or not isinstance(content, str):
        raise ToolInputError("invalid write approval arguments")
    new = content.encode("utf-8")
    if len(new) > MAX_WRITE_BYTES:
        raise ToolInputError(f"content exceeds the {MAX_WRITE_BYTES}-byte limit")

    path = resolve_path(ctx, path_arg, must_exist=False)
    expected_path = pending.prepared.get("resolved_path")
    if expected_path is not None and not _same_path(path, Path(expected_path)):
        raise PathSecurityError("approved path changed before execution")
    parent = _check_parent(ctx, path)
    lexical = Path(os.fspath(path_arg))
    if not lexical.is_absolute():
        lexical = Path(ctx.workspace) / lexical
    if lexical.is_symlink() or path.is_symlink():
        raise PathSecurityError("writing through a symlink is not allowed")
    if path.exists() and not path.is_file():
        raise ToolInputError("path is not a regular file")

    current = _read_bounded(path, MAX_WRITE_BYTES) if path.exists() else b""
    actual_hash = _hash_bytes(current) if path.exists() else None
    expected_hash = pending.prepared.get("before_hash")
    if actual_hash != expected_hash:
        raise ToolInputError("file changed after approval; before_hash no longer matches")
    expected_after_hash = pending.prepared.get("after_hash")
    if expected_after_hash is not None and _hash_bytes(new) != expected_after_hash:
        raise ToolInputError("approved file content changed before execution")
    expected_exists = bool(pending.prepared.get("before_exists", expected_hash is not None))
    if path.exists() != expected_exists:
        raise ToolInputError("file existence changed after approval")

    fd, temporary_name = tempfile.mkstemp(
        prefix=".agent-coach-write-", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
            handle.flush()
            os.fsync(handle.fileno())
        # The destination was re-resolved and hash checked above.  Replacing a
        # path atomically avoids exposing a partially-written UTF-8 file.
        os.replace(temporary_name, str(path))
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return _outcome(
        {
            "path": _display_path(ctx, path),
            "size": len(new),
            "sha256": _hash_bytes(new),
            "written": True,
        }
    )


def _normalise_argv(command: Any, shell: bool) -> list[str] | str:
    if shell:
        if not isinstance(command, str) or not command.strip():
            raise ToolInputError("shell commands require a non-empty command string")
        return command
    if isinstance(command, str):
        # ``posix=False`` preserves Windows executable quoting when this CLI is
        # run on Windows; POSIX uses the conventional shell lexer.
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError as exc:
            raise ToolInputError(f"invalid command quoting: {exc}") from exc
    elif isinstance(command, (list, tuple)):
        argv = list(command)
    else:
        raise ToolInputError("command must be a string or argv list")
    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise ToolInputError("command argv must contain non-empty strings")
    return argv


def _command_purpose(value: Any) -> str:
    """Return the short human-readable reason shown at the approval gate."""

    if value is None:
        # Keep compatibility with older model prompts while ensuring every
        # approval still explains why the operation is being requested.  New
        # callers should always provide ``purpose`` explicitly.
        return "执行这条经过批准的本地命令，以完成当前任务"
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError("purpose must be a non-empty human-readable string")
    if len(value.encode("utf-8")) > MAX_COMMAND_PURPOSE_BYTES:
        raise ToolInputError(f"purpose exceeds the {MAX_COMMAND_PURPOSE_BYTES}-byte limit")
    return value.strip()


def _command_scope(ctx: ToolContext) -> dict[str, str]:
    """Build the owner binding copied into every command-session snapshot."""

    metadata = getattr(ctx.session, "active_turn_metadata", None)
    scope: dict[str, str] = {
        "workspace": str(_workspace_root(ctx)),
        "session_id": str(getattr(ctx.session, "id", "")),
    }
    if isinstance(metadata, Mapping):
        for key in ("scope", "project_key", "practice_thread_id"):
            value = metadata.get(key)
            if value is not None and str(value).strip():
                scope[key] = str(value).strip()
    return scope


def _command_display(command: list[str] | str, shell: bool) -> str:
    if isinstance(command, str):
        return command
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _safe_environment(*, git: bool = False) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not _SENSITIVE_ENV_RE.search(key)
        and key.casefold()
        not in {
            "git_askpass",
            "git_credential_helper",
            "git_config_count",
            "git_config_parameters",
            "git_config_global",
            "git_config_system",
            "git_ssh_command",
        }
    }
    if git:
        # Public clone operations must not wait for an interactive credential
        # prompt.  SSH keys/agents remain the user's explicit choice for SSH.
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
    return env


def _clip_bytes(value: Any, limit: int) -> tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="replace")
    else:
        raw = bytes(value)
    clipped = len(raw) > limit
    if clipped:
        raw = raw[:limit]
    # Do not decode a partial UTF-8 code point with ``errors='replace'``:
    # U+FFFD takes three bytes and could make the returned text exceed the
    # advertised byte bound.  Ignoring only an incomplete trailing sequence
    # preserves valid UTF-8 and keeps the encoded result within ``limit``.
    return raw.decode("utf-8", errors="ignore"), clipped


def _drain_pipe(stream: Any, sink: dict[str, Any], limit: int) -> None:
    """Drain one child pipe into a bounded, pollable capture.

    The reader keeps consuming after the output cap is reached.  That is
    necessary for a chatty process: stopping the reader would fill the OS pipe
    buffer and make the child appear hung.  ``sink['data']`` is updated after
    every chunk so a command session can be polled while it is still running.
    """

    sink.setdefault("_lock", threading.Lock())
    sink.setdefault("data", b"")
    sink.setdefault("total", 0)
    sink.setdefault("truncated", False)
    try:
        read_chunk = getattr(stream, "read1", None)
        if not callable(read_chunk):
            read_chunk = getattr(stream, "read", None)
        if not callable(read_chunk):
            return
        while True:
            # BufferedReader.read1() returns bytes already available from the
            # OS pipe, which makes Windows polling genuinely incremental;
            # read() may wait for its whole requested size or EOF.
            chunk = read_chunk(64 * 1024)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            chunk = bytes(chunk)
            with sink["_lock"]:
                sink["total"] = int(sink.get("total", 0)) + len(chunk)
                current = bytes(sink.get("data", b""))
                if len(current) < limit:
                    sink["data"] = current + chunk[: limit - len(current)]
                sink["truncated"] = int(sink["total"]) > limit
    except (AttributeError, OSError, ValueError):
        # The process may be killed while a reader is blocked.  Preserve the
        # bytes already collected and let the caller report timeout state.
        pass
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass


def _sink_snapshot(sink: Mapping[str, Any], limit: int) -> tuple[str, bool, int]:
    lock = sink.get("_lock")
    if lock is None:
        raw = bytes(sink.get("data", b""))
        total = int(sink.get("total", len(raw)))
        truncated = bool(sink.get("truncated")) or len(raw) > limit or total > limit
    else:
        with lock:
            raw = bytes(sink.get("data", b""))
            total = int(sink.get("total", len(raw)))
            truncated = bool(sink.get("truncated")) or len(raw) > limit or total > limit
    text, clipped = _clip_bytes(raw, limit)
    return text, bool(truncated or clipped), total


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_returncode(process: Any, fallback: int | None = None) -> int | None:
    value = getattr(process, "returncode", None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback
    return fallback


def _process_running(process: Any) -> bool:
    poll = getattr(process, "poll", None)
    if callable(poll):
        try:
            return poll() is None
        except (OSError, ValueError):
            pass
    value = getattr(process, "returncode", None)
    # A small fake process used by callers/tests may not expose poll or
    # returncode.  Treat it as running so the termination fallback is tried.
    return value is None


def _terminate_process_tree(process: Any, *, grace: float = COMMAND_STOP_GRACE_SECONDS) -> bool:
    """Terminate a command and its descendants where the platform permits.

    Commands are launched in their own process group/session.  POSIX can then
    signal the entire group directly.  Windows has no equivalent portable
    Python process-tree API, so ``taskkill /T`` is invoked with a fixed argv;
    the direct ``kill`` fallback still handles test doubles and unusual child
    process implementations.  The function returns whether the process was
    observed stopped before the caller's final snapshot.
    """

    if process is None:
        return True

    pid = getattr(process, "pid", None)
    process_group_owned = bool(getattr(process, "_agent_coach_process_group", False))
    if not _process_running(process) and not process_group_owned:
        return True
    if os.name == "nt":
        # CTRL_BREAK gives a well-behaved console process a chance to clean up
        # before taskkill forcibly closes every descendant in its tree.
        send_signal = getattr(process, "send_signal", None)
        ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
        if callable(send_signal) and ctrl_break is not None:
            try:
                send_signal(ctrl_break)
            except (OSError, ValueError):
                pass
        if pid is not None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=max(1.0, float(grace)),
                )
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
    else:
        # ``start_new_session=True`` makes the child PID the process-group ID.
        # If a test double has no PID, the direct kill fallback below is used.
        if pid is not None:
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except (AttributeError, OSError, ValueError):
                pass

    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=max(0.05, float(grace)))
        except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
            pass

    # A shell can exit as soon as it receives SIGTERM while a descendant that
    # inherited the process group keeps running.  Escalate the group even when
    # the group leader has already reaped; ``killpg`` simply reports ESRCH when
    # no member remains.  This is the part that prevents a timed-out command
    # from leaving a detached ``sleep``/worker behind with open pipes.
    if os.name != "nt" and pid is not None:
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except (AttributeError, OSError, ValueError):
            pass

    if _process_running(process):
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
            except (OSError, ValueError):
                pass
        if callable(wait):
            try:
                wait(timeout=max(0.05, float(grace)))
            except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
                pass
    return not _process_running(process)


def _popen_command(
    command: list[str] | str,
    *,
    shell: bool,
    cwd: Path,
    git: bool = False,
) -> Any:
    kwargs: dict[str, Any] = {
        "shell": shell,
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": _safe_environment(git=git),
    }
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
        if flags:
            kwargs["creationflags"] = flags
    else:
        # Make process-tree stop deterministic for commands that spawn child
        # workers.  This is intentionally independent of shell=True/False.
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    # Mark only processes whose group/session was created by this tool.  The
    # marker lets a timeout clean up descendants even if the group leader
    # exits in the small race between wait() and tree termination, without
    # ever sending a guessed PID signal to an unrelated process.
    try:
        process._agent_coach_process_group = bool(os.name != "nt" or kwargs.get("creationflags"))
    except Exception:
        pass
    return process


class CommandSession:
    """A bounded, pollable subprocess session.

    The command starts exactly once.  ``poll`` only observes it, ``stop`` is
    the sole explicit termination path, and ``wait`` is useful to the legacy
    synchronous ToolDefinition adapter.  A session never retries a process,
    which lets the engine keep its durable approval/execution claim as the
    single replay guard across restarts.
    """

    def __init__(
        self,
        command: list[str] | str,
        *,
        shell: bool,
        cwd: Path,
        timeout: float | None,
        output_limit: int,
        purpose: str = "",
        approval_id: str | None = None,
        tool_call_id: str | None = None,
        owner_workspace: Path | str | None = None,
        owner_session_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
        git: bool = False,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.execution_id = self.id
        self.command = command
        self.shell = bool(shell)
        self.cwd = Path(cwd)
        self.timeout = timeout
        self.output_limit = output_limit
        self.purpose = str(purpose or "").strip()
        self.approval_id = str(approval_id or "") or None
        self.tool_call_id = str(tool_call_id or "") or None
        self.owner_workspace = str(owner_workspace or "")
        self.owner_session_id = str(owner_session_id or "")
        self.scope = {
            str(key): str(value)
            for key, value in dict(scope or {}).items()
            if value is not None and str(value).strip()
        }
        if self.owner_workspace:
            self.scope.setdefault("workspace", self.owner_workspace)
        if self.owner_session_id:
            self.scope.setdefault("session_id", self.owner_session_id)
        if self.approval_id:
            self.scope.setdefault("approval_id", self.approval_id)
        if self.tool_call_id:
            self.scope.setdefault("tool_call_id", self.tool_call_id)
        self.display = _command_display(command, self.shell)
        self.started_at = _utc_now()
        self.finished_at: str | None = None
        self._state_lock = threading.RLock()
        self._done = threading.Event()
        self._stop_requested = False
        self._timed_out = False
        self._termination_failed = False
        self._returncode: int | None = None
        self._status = "starting"
        self._stdout_sink: dict[str, Any] = {"_lock": threading.Lock(), "data": b"", "total": 0}
        self._stderr_sink: dict[str, Any] = {"_lock": threading.Lock(), "data": b"", "total": 0}
        self._process = _popen_command(command, shell=self.shell, cwd=self.cwd, git=git)
        self._status = "running"
        self._readers: list[threading.Thread] = []
        for stream, sink, name in (
            (getattr(self._process, "stdout", None), self._stdout_sink, "stdout"),
            (getattr(self._process, "stderr", None), self._stderr_sink, "stderr"),
        ):
            if stream is None:
                continue
            reader = threading.Thread(
                target=_drain_pipe,
                args=(stream, sink, output_limit),
                name=f"agent-coach-command-{self.id[:8]}-{name}",
                daemon=True,
            )
            self._readers.append(reader)
            reader.start()
        self._watcher = threading.Thread(
            target=self._watch,
            name=f"agent-coach-command-watch-{self.id[:8]}",
            daemon=True,
        )
        self._watcher.start()

    def _watch(self) -> None:
        wait_result: int | None = None
        timed_out = False
        termination_failed = False
        try:
            if self.timeout is None:
                wait_result = self._process.wait()
            else:
                wait_result = self._process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            termination_failed = not _terminate_process_tree(self._process)
            try:
                wait_result = self._process.wait(timeout=max(1.0, COMMAND_STOP_GRACE_SECONDS))
            except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
                wait_result = None
        except (OSError, ValueError):
            # A process that exits between poll/stop calls can make wait raise;
            # retain its best available return code and finish the session.
            wait_result = _process_returncode(self._process)
        finally:
            # Descendants that inherited the pipe can delay EOF.  Joining for a
            # short bounded interval keeps final polling responsive while the
            # daemon readers continue to enforce the output cap if needed.
            for reader in self._readers:
                reader.join(timeout=COMMAND_STOP_GRACE_SECONDS)
            returncode = _process_returncode(self._process, wait_result)
            process_alive = _process_running(self._process)
            with self._state_lock:
                self._timed_out = bool(timed_out)
                self._termination_failed = bool(
                    self._termination_failed or termination_failed
                )
                self._returncode = returncode
                # A failed stop/timeout is deliberately not reported as a
                # normal terminal status.  The child may still be running,
                # and allowing the engine to resume the model would make the
                # saved transcript claim a completed side effect too early.
                uncertain = bool(self._termination_failed or process_alive)
                self.finished_at = None if process_alive else _utc_now()
                if uncertain:
                    self._status = "uncertain"
                elif timed_out:
                    self._status = "timed_out"
                elif self._stop_requested:
                    self._status = "stopped"
                elif returncode == 0:
                    self._status = "completed"
                else:
                    self._status = "failed"
                self._done.set()

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """Wait for completion, or return the current snapshot after a bound."""

        self._done.wait(timeout=timeout)
        return self.poll()

    def poll(self, *, wait_seconds: float = DEFAULT_COMMAND_POLL_WAIT_SECONDS) -> dict[str, Any]:
        if isinstance(wait_seconds, bool):
            raise ToolInputError("wait_seconds must be a number")
        try:
            wait_value = float(wait_seconds)
        except (TypeError, ValueError) as exc:
            raise ToolInputError("wait_seconds must be a number") from exc
        if not math.isfinite(wait_value):
            raise ToolInputError("wait_seconds must be a finite number")
        if wait_value < 0 or wait_value > MAX_COMMAND_POLL_WAIT_SECONDS:
            raise ToolInputError(
                f"wait_seconds must be between 0 and {MAX_COMMAND_POLL_WAIT_SECONDS}"
            )
        if wait_value:
            self._done.wait(timeout=wait_value)
        with self._state_lock:
            status = self._status
            returncode = self._returncode
            finished_at = self.finished_at
            timed_out = self._timed_out
            termination_failed = self._termination_failed
            stop_requested = self._stop_requested
        stdout, stdout_truncated, stdout_total = _sink_snapshot(self._stdout_sink, self.output_limit)
        stderr, stderr_truncated, stderr_total = _sink_snapshot(self._stderr_sink, self.output_limit)
        payload: dict[str, Any] = {
            "execution_id": self.id,
            "command_session_id": self.id,
            # ``session_id`` mirrors the long-running command terminology used
            # by the host runner; ``execution_id`` remains the durable engine
            # claim binding and is the preferred approval/recovery key.
            "session_id": self.id,
            "status": status,
            "purpose": self.purpose,
            "command": self.display,
            "shell": self.shell,
            "cwd": str(self.cwd),
            "timeout": self.timeout,
            "output_limit": self.output_limit,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "stdout_total_bytes": stdout_total,
            "stderr_total_bytes": stderr_total,
            "returncode": returncode,
            "timed_out": timed_out,
            "termination_failed": termination_failed,
            "stop_requested": stop_requested,
            "started_at": self.started_at,
            "finished_at": finished_at,
        }
        if self.scope:
            payload["scope"] = dict(self.scope)
        if self.approval_id is not None:
            payload["approval_id"] = self.approval_id
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if timed_out:
            if termination_failed or status == "uncertain":
                payload["timeout_note"] = (
                    "The explicit command timeout elapsed, but stopping the direct child or its process tree could not be confirmed; the result is uncertain."
                )
            else:
                payload["timeout_note"] = (
                    "The explicit command timeout elapsed; the direct child and its entire process tree were asked to stop."
                )
        elif termination_failed:
            payload["stop_note"] = (
                "Stopping the direct child or its process tree could not be confirmed; the result is uncertain."
            )
        return payload

    def stop(self) -> dict[str, Any]:
        with self._state_lock:
            done = self._done.is_set()
            if done:
                snapshot = self.poll()
                # A watcher can publish ``uncertain`` after a failed timeout
                # kill while the child remains alive.  Retry the owned
                # process-tree termination during an orderly shutdown, but
                # retain the uncertain claim even if the retry succeeds: the
                # durable engine must still require human acknowledgement.
                if snapshot.get("status") != "uncertain":
                    return snapshot
            else:
                self._stop_requested = True
            process = self._process
        stopped = _terminate_process_tree(process)
        if not stopped:
            with self._state_lock:
                self._termination_failed = True
        if not done:
            self._done.wait(timeout=max(1.0, COMMAND_STOP_GRACE_SECONDS + 1.0))
        return self.poll()


class CommandSessionManager:
    """Process-local registry for approved command sessions.

    The registry is deliberately not a durable replay queue.  The engine's
    persisted ``execution`` claim remains authoritative after a restart; a
    missing in-memory session is therefore an unknown result, never an
    instruction to start the command again.
    """

    def __init__(self, *, max_sessions: int = MAX_COMMAND_SESSIONS) -> None:
        self.max_sessions = max(1, int(max_sessions))
        self._lock = threading.RLock()
        self._sessions: dict[str, CommandSession] = {}

    def start(
        self,
        command: list[str] | str,
        *,
        shell: bool,
        cwd: Path,
        timeout: float | None,
        output_limit: int,
        purpose: str = "",
        approval_id: str | None = None,
        tool_call_id: str | None = None,
        owner_workspace: Path | str | None = None,
        owner_session_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
        git: bool = False,
    ) -> CommandSession:
        timeout = _safe_optional_timeout(timeout, maximum=MAX_COMMAND_TIMEOUT)
        output_limit = _safe_limit(
            output_limit,
            default=MAX_COMMAND_OUTPUT_BYTES,
            maximum=MAX_COMMAND_OUTPUT_BYTES,
        )
        purpose = _command_purpose(purpose or None)
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                completed = [
                    key
                    for key, session in self._sessions.items()
                    if session.poll().get("status") != "running"
                ]
                for key in completed:
                    self._sessions.pop(key, None)
                if len(self._sessions) >= self.max_sessions:
                    raise ToolInputError("too many command sessions; stop or collect an existing session first")
            session = CommandSession(
                command,
                shell=shell,
                cwd=cwd,
                timeout=timeout,
                output_limit=output_limit,
                purpose=purpose,
                approval_id=approval_id,
                tool_call_id=tool_call_id,
                owner_workspace=owner_workspace,
                owner_session_id=owner_session_id,
                scope=scope,
                git=git,
            )
            self._sessions[session.id] = session
            return session

    @staticmethod
    def _scope_matches(session: CommandSession, scope: Mapping[str, Any] | None) -> bool:
        if scope is None:
            return True
        expected = {
            str(key): str(value)
            for key, value in dict(scope).items()
            if value is not None and str(value).strip()
        }
        actual = session.scope
        for key, value in expected.items():
            if key == "workspace":
                try:
                    if os.path.normcase(str(Path(value).resolve(strict=False))) != os.path.normcase(
                        str(Path(actual.get(key, "")).resolve(strict=False))
                    ):
                        return False
                except (OSError, RuntimeError):
                    if os.path.normcase(value) != os.path.normcase(actual.get(key, "")):
                        return False
            elif actual.get(key) != value:
                return False
        return True

    def get(
        self,
        execution_id: str,
        *,
        scope: Mapping[str, Any] | None = None,
    ) -> CommandSession:
        key = str(execution_id or "").strip()
        if not key:
            raise ToolInputError("execution_id is required")
        with self._lock:
            try:
                session = self._sessions[key]
            except KeyError as exc:
                raise ToolInputError("command session was not found; it may belong to a previous process") from exc
            if not self._scope_matches(session, scope):
                raise ToolInputError("command session does not belong to this workspace or session")
            return session

    def poll(
        self,
        execution_id: str,
        *,
        wait_seconds: float = DEFAULT_COMMAND_POLL_WAIT_SECONDS,
        scope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.get(execution_id, scope=scope).poll(wait_seconds=wait_seconds)

    def stop(
        self,
        execution_id: str,
        *,
        scope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.get(execution_id, scope=scope).stop()

    def list_sessions(
        self,
        *,
        scope: Mapping[str, Any] | None = None,
        include_finished: bool = True,
    ) -> list[dict[str, Any]]:
        """Return bounded snapshots owned by the requested scope.

        Listing is intentionally a filter rather than a lookup: callers that
        provide a workspace/session/project scope only see sessions matching
        every supplied field.  This lets a UI worker refresh its own command
        panel without learning identifiers from another workspace or thread.
        ``poll`` and ``stop`` still perform the strict single-session scope
        check before returning or terminating anything.
        """

        if isinstance(include_finished, bool) is False:
            raise ToolInputError("include_finished must be a boolean")
        with self._lock:
            sessions = list(self._sessions.values())
        snapshots: list[dict[str, Any]] = []
        for session in sessions:
            if not self._scope_matches(session, scope):
                continue
            snapshot = session.poll()
            if not include_finished and snapshot.get("status") not in {
                "starting",
                "running",
                # An uncertain process may still be alive and always needs
                # operator visibility even though it is not safely stoppable.
                "uncertain",
            }:
                continue
            snapshots.append(snapshot)
        snapshots.sort(key=lambda item: str(item.get("started_at") or ""))
        return snapshots

    def forget(self, execution_id: str) -> None:
        key = str(execution_id or "").strip()
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.poll().get("status") == "running":
                raise ToolInputError("cannot forget a running command session")
            self._sessions.pop(key, None)

    def stop_all(self) -> None:
        """Stop every owned live command during an orderly process shutdown."""

        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                if session.poll().get("status") in {"starting", "running", "uncertain"}:
                    session.stop()
            except Exception:
                # Shutdown must not mask the interpreter's original exit;
                # the durable engine claim remains available for uncertain
                # recovery if the OS refused termination.
                pass


command_sessions = CommandSessionManager()
atexit.register(command_sessions.stop_all)


def start_command_session(
    command: list[str] | str,
    *,
    shell: bool,
    cwd: Path,
    timeout: float | None,
    output_limit: int = MAX_COMMAND_OUTPUT_BYTES,
    purpose: str = "",
    approval_id: str | None = None,
    tool_call_id: str | None = None,
    owner_workspace: Path | str | None = None,
    owner_session_id: str | None = None,
    scope: Mapping[str, Any] | None = None,
    git: bool = False,
) -> CommandSession:
    """Start one approved command for callers that need UI polling."""

    return command_sessions.start(
        command,
        shell=shell,
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
        purpose=purpose,
        approval_id=approval_id,
        tool_call_id=tool_call_id,
        owner_workspace=owner_workspace,
        owner_session_id=owner_session_id,
        scope=scope,
        git=git,
    )


def poll_command_session(
    execution_id: str,
    *,
    wait_seconds: float = DEFAULT_COMMAND_POLL_WAIT_SECONDS,
    scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Poll one command, optionally waiting up to 30 seconds for a change."""

    return command_sessions.poll(execution_id, wait_seconds=wait_seconds, scope=scope)


def stop_command_session(
    execution_id: str,
    *,
    scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return command_sessions.stop(execution_id, scope=scope)


def list_command_sessions(
    *,
    scope: Mapping[str, Any] | None = None,
    include_finished: bool = True,
) -> list[dict[str, Any]]:
    """List bounded command snapshots visible to a workspace/session scope."""

    return command_sessions.list_sessions(
        scope=scope,
        include_finished=include_finished,
    )


def _run_subprocess(
    command: list[str] | str,
    *,
    shell: bool,
    cwd: Path,
    timeout: float | None,
    output_limit: int,
    git: bool = False,
    purpose: str = "",
    approval_id: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    session: CommandSession | None = None
    try:
        session = CommandSession(
            command,
            shell=shell,
            cwd=str(cwd),
            timeout=timeout,
            output_limit=output_limit,
            purpose=purpose,
            approval_id=approval_id,
            tool_call_id=tool_call_id,
            git=git,
        )
        result = session.wait()
        # The synchronous git-clone adapter shares the same process-tree
        # lifecycle as run_command.  Mark its snapshot so the engine can keep
        # a timeout/termination failure as an uncertain claim instead of
        # resuming the model while git may still be alive.
        result["command_session"] = True
        return result
    except OSError:
        # Preserve the normal subprocess error semantics for callers while
        # making sure a failed spawn cannot leave reader threads around.
        if session is not None:
            _terminate_process_tree(session._process)
        raise


def _run_prepare(args: dict, ctx: ToolContext) -> PendingApproval:
    args = _require_mapping(args)
    command = _arg(args, "command", "cmd", "argv")
    if command is None:
        raise ToolInputError("command is required")
    shell = _safe_bool(_arg(args, "shell", default=False), default=False, field="shell")
    normalised = _normalise_argv(command, shell)
    cwd_arg = _arg(args, "cwd", default=".")
    cwd = resolve_path(ctx, cwd_arg, must_exist=True)
    if not cwd.is_dir():
        raise ToolInputError("cwd is not a directory")
    purpose = _command_purpose(_arg(args, "purpose", "reason", "description"))
    timeout = _safe_optional_timeout(
        _arg(args, "timeout", default=None), maximum=MAX_COMMAND_TIMEOUT
    )
    output_limit = _safe_limit(
        _arg(args, "max_output_bytes", "output_limit"),
        default=MAX_COMMAND_OUTPUT_BYTES,
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    display = _command_display(normalised, shell)
    prepared = {
        "command": normalised,
        "shell": shell,
        "cwd": str(cwd),
        "timeout": timeout,
        "max_output_bytes": output_limit,
        "purpose": purpose,
    }
    return PendingApproval(
        tool_name="run_command",
        arguments=dict(args),
        summary=(
            f"目的：{purpose}\n"
            f"预期结果：在工作区 {_display_path(ctx, cwd)} 执行命令并返回受限输出。\n"
            f"命令（shell={shell}）：{display}"
        ),
        preview=_json(
            {
                "purpose": purpose,
                "shell": shell,
                "cwd": _display_path(ctx, cwd),
                "command": display,
                "timeout": timeout,
                "max_output_bytes": output_limit,
            }
        ),
        prepared=prepared,
    )


def _run_execute(pending: PendingApproval, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(pending.arguments)
    command = _arg(args, "command", "cmd", "argv")
    shell = _safe_bool(_arg(args, "shell", default=False), default=False, field="shell")
    normalised = _normalise_argv(command, shell)
    cwd = resolve_path(ctx, _arg(args, "cwd", default="."), must_exist=True)
    if not cwd.is_dir():
        raise ToolInputError("cwd is not a directory")
    prepared_cwd = pending.prepared.get("cwd")
    if prepared_cwd is not None and not _same_path(cwd, Path(prepared_cwd)):
        raise PathSecurityError("approved command cwd changed before execution")
    prepared_command = pending.prepared.get("command")
    if prepared_command is not None and normalised != prepared_command:
        raise ToolInputError("approved command changed before execution")
    prepared_shell = pending.prepared.get("shell")
    if prepared_shell is not None and shell != prepared_shell:
        raise ToolInputError("approved shell mode changed before execution")
    purpose = _command_purpose(_arg(args, "purpose", "reason", "description"))
    if pending.prepared.get("purpose") is not None and purpose != pending.prepared.get("purpose"):
        raise ToolInputError("approved command purpose changed before execution")
    timeout = _safe_optional_timeout(
        _arg(args, "timeout", default=None), maximum=MAX_COMMAND_TIMEOUT
    )
    if "timeout" in pending.prepared and timeout != pending.prepared.get("timeout"):
        raise ToolInputError("approved command timeout changed before execution")
    output_limit = _safe_limit(
        _arg(args, "max_output_bytes", "output_limit"),
        default=MAX_COMMAND_OUTPUT_BYTES,
        maximum=MAX_COMMAND_OUTPUT_BYTES,
    )
    if (
        pending.prepared.get("max_output_bytes") is not None
        and output_limit != pending.prepared.get("max_output_bytes")
    ):
        raise ToolInputError("approved command output limit changed before execution")
    # Start once and return a bounded initial snapshot.  A short command often
    # finishes during this hand-off, preserving the synchronous ToolDefinition
    # behavior used by the CLI; a long command stays registered for UI/model
    # polling and is never started again by a later poll.
    command_session = start_command_session(
        normalised,
        shell=shell,
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
        purpose=purpose,
        approval_id=pending.id,
        tool_call_id=pending.tool_call_id,
        owner_workspace=_workspace_root(ctx),
        owner_session_id=ctx.session.id,
        scope=_command_scope(ctx),
    )
    initial_wait = COMMAND_INITIAL_HANDOFF_WAIT_SECONDS
    if timeout is not None:
        initial_wait = min(timeout, COMMAND_INITIAL_HANDOFF_WAIT_SECONDS)
    result = command_session.poll(wait_seconds=initial_wait)
    result["cwd"] = _display_path(ctx, cwd)
    # Distinguish the registered, pollable run_command session from the
    # synchronous CommandSession used internally by git_clone.
    result["command_session"] = True
    return _outcome(result)


def _reject_local_host(host: str) -> None:
    folded = host.rstrip(".").casefold()
    if (
        folded in _METADATA_HOSTS
        or folded == "localhost"
        or folded.endswith(".localhost")
        or folded.endswith(".local")
        or folded.endswith(".internal")
    ):
        raise ToolInputError("local, metadata, and internal hosts are not allowed")
    try:
        address = ipaddress.ip_address(folded)
    except ValueError:
        return
    if not address.is_global:
        raise ToolInputError("private, loopback, or reserved addresses are not allowed")


def _resolved_public_addresses(host: str, port: int) -> list[ipaddress._BaseAddress]:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ToolInputError("private, loopback, or reserved addresses are not allowed")
        return [address]

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror) as exc:
        raise ToolInputError(f"could not resolve public host: {host}") from exc
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        candidate = sockaddr[0]
        try:
            parsed = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise ToolInputError("host resolved to an invalid address") from exc
        if not parsed.is_global:
            raise ToolInputError("host resolves to a private, loopback, or reserved address")
        addresses.append(parsed)
    if not addresses:
        raise ToolInputError("host did not resolve to a public address")
    return addresses


def ensure_public_url(url: str) -> str:
    """Validate and return an HTTP(S) URL whose host resolves publicly.

    This function intentionally rejects URL userinfo, fragments, metadata
    names, and non-public DNS answers.  Call it again for every redirect.
    """

    if not isinstance(url, str) or not url.strip():
        raise ToolInputError("url must be a non-empty string")
    if any(ord(char) < 32 for char in url):
        raise ToolInputError("url contains control characters")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.casefold()
        port = parsed.port
    except ValueError as exc:
        raise ToolInputError(f"invalid URL: {exc}") from exc
    if scheme not in {"http", "https"}:
        raise ToolInputError("only http and https URLs are allowed")
    if port is not None and not (1 <= port <= 65535):
        raise ToolInputError("URL port must be between 1 and 65535")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ToolInputError("URL userinfo is not allowed")
    query_keys = {
        key.strip().casefold().replace("-", "_")
        for key in parse_qs(parsed.query, keep_blank_values=True)
    }
    if query_keys & {
        "token",
        "access_token",
        "api_key",
        "apikey",
        "key",
        "auth",
        "authorization",
    }:
        raise ToolInputError("URL query credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise ToolInputError("URL must include a hostname")
    host = host.rstrip(".")
    if not host:
        raise ToolInputError("URL must include a hostname")
    _reject_local_host(host)
    # IDNA conversion also gives a stable host form for DNS validation.
    try:
        dns_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ToolInputError("invalid internationalized hostname") from exc
    _resolved_public_addresses(dns_host, port or (443 if scheme == "https" else 80))
    if parsed.fragment:
        raise ToolInputError("URL fragments are not sent to the server")
    return url


def _response_body(response: Any, limit: int) -> tuple[bytes, bool]:
    content_length = None
    headers = getattr(response, "headers", {}) or {}
    try:
        if headers.get("Content-Length") is not None:
            content_length = int(headers.get("Content-Length"))
    except (TypeError, ValueError):
        content_length = None
    if content_length is not None and content_length > limit:
        raise ToolInputError(f"response exceeds the {limit}-byte limit")

    chunks: list[bytes] = []
    size = 0
    truncated = False
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        try:
            stream = iterator(chunk_size=64 * 1024)
            iter(stream)
        except (TypeError, AttributeError):
            stream = [getattr(response, "content", b"")]
    else:
        stream = [getattr(response, "content", b"")]
    for chunk in stream:
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        remaining = limit - size
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(bytes(chunk[:remaining]))
            size += remaining
            truncated = True
            break
        chunks.append(bytes(chunk))
        size += len(chunk)
    return b"".join(chunks), truncated


def _fetch_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(args)
    max_bytes = _safe_limit(
        _arg(args, "max_bytes", "max_size"),
        default=MAX_FETCH_BYTES,
        maximum=MAX_FETCH_BYTES,
    )
    timeout = _safe_timeout(
        _arg(args, "timeout", default=None),
        default=DEFAULT_FETCH_TIMEOUT,
        maximum=MAX_FETCH_TIMEOUT,
    )
    raw_url = _arg(args, "url")
    # GitHub file links are routed through the API so a workspace-scoped token
    # can be used for private repositories without ever placing credentials in
    # a browser/raw-content redirect. Other URLs retain the public fetch path.
    from .integrations import (
        IntegrationError,
        list_github_repositories,
        parse_github_repository_list_url,
        parse_github_url,
        parse_github_user_url,
        read_github,
    )

    github_user = parse_github_user_url(raw_url) if isinstance(raw_url, str) else None
    if github_user is not None:
        try:
            payload = list_github_repositories(ctx.workspace, username=github_user)
        except IntegrationError as exc:
            raise ToolInputError(str(exc)) from exc
        # Keep the original URL as provenance while returning only the bounded
        # API projection.  No browser fetch or URL credential is involved.
        return _outcome({"url": raw_url, **payload})

    try:
        github_list_target = (
            parse_github_repository_list_url(raw_url) if isinstance(raw_url, str) else None
        )
    except IntegrationError as exc:
        raise ToolInputError(str(exc)) from exc
    if github_list_target is not None:
        github_user, page, per_page = github_list_target
        try:
            payload = list_github_repositories(
                ctx.workspace,
                username=github_user,
                page=page,
                per_page=per_page,
                timeout=timeout,
            )
        except IntegrationError as exc:
            raise ToolInputError(str(exc)) from exc
        return _outcome({"url": raw_url, **payload})

    github_target = parse_github_url(raw_url) if isinstance(raw_url, str) else None
    if github_target is not None:
        repository, path, ref = github_target
        try:
            payload = read_github(
                ctx.workspace,
                path,
                repository=repository,
                ref=ref,
                max_bytes=min(max_bytes, 2 * 1024 * 1024),
                timeout=timeout,
            )
        except IntegrationError as exc:
            raise ToolInputError(str(exc)) from exc
        return _outcome({"url": raw_url, **payload})

    url = ensure_public_url(raw_url)
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency is in pyproject
        raise ToolInputError("requests is required for fetch_url") from exc

    session = requests.Session()
    # A fresh session has no cookies/auth.  Disable environment trust so a
    # proxy URL containing credentials cannot be copied into this request.
    session.trust_env = False
    session_headers = getattr(session, "headers", None)
    if session_headers is not None:
        session_headers.clear()
        session_headers.update({"User-Agent": "agent-coach/1.0"})
    current = url
    redirects = 0
    try:
        while True:
            current = ensure_public_url(current)
            try:
                response = session.get(
                    current,
                    allow_redirects=False,
                    stream=True,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise ToolInputError(f"HTTP request failed: {exc}") from exc
            status = int(getattr(response, "status_code", 0))
            if 300 <= status < 400:
                location = (getattr(response, "headers", {}) or {}).get("Location")
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                if not location:
                    raise ToolInputError("redirect response has no Location header")
                redirects += 1
                if redirects > MAX_FETCH_REDIRECTS:
                    raise ToolInputError("too many redirects")
                current = urljoin(current, location)
                continue
            if status >= 400:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                raise ToolInputError(f"HTTP request returned status {status}")
            body, truncated = _response_body(response, max_bytes)
            close = getattr(response, "close", None)
            if callable(close):
                close()
            encoding = getattr(response, "encoding", None) or "utf-8"
            try:
                text = body.decode(encoding, errors="replace")
            except (LookupError, UnicodeError):
                text = body.decode("utf-8", errors="replace")
            return _outcome(
                {
                    "url": current,
                    "status": status,
                    "content": text,
                    "bytes": len(body),
                    "truncated": truncated,
                    "redirects": redirects,
                }
            )
    finally:
        close_session = getattr(session, "close", None)
        if callable(close_session):
            close_session()


def _read_github_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    """Read one bounded GitHub path through the workspace integration."""

    args = _require_mapping(args)
    _reject_unknown_arguments(args, {"repository", "repo", "path", "ref", "max_bytes"})
    from .integrations import IntegrationError, read_github

    repository = _arg(args, "repository", "repo")
    path = _arg(args, "path", default="")
    ref = _arg(args, "ref", default=None)
    max_bytes = _arg(args, "max_bytes", default=None)
    try:
        payload = read_github(
            ctx.workspace,
            path,
            repository=repository,
            ref=ref,
            max_bytes=(2 * 1024 * 1024 if max_bytes is None else max_bytes),
        )
    except IntegrationError as exc:
        raise ToolInputError(str(exc)) from exc
    return _outcome(payload)


def _github_identity_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    """Return the authenticated identity through GitHub's /user endpoint."""

    args = _require_mapping(args)
    _reject_unknown_arguments(args, set())
    from .integrations import IntegrationError, github_identity

    try:
        payload = github_identity(ctx.workspace)
    except IntegrationError as exc:
        raise ToolInputError(str(exc)) from exc
    return _outcome(payload)


def _list_github_repositories_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    """List public or current-user private repositories without exposing a Token."""

    args = _require_mapping(args)
    _reject_unknown_arguments(args, {"username", "page", "per_page"})
    from .integrations import IntegrationError, list_github_repositories

    try:
        payload = list_github_repositories(
            ctx.workspace,
            username=_arg(args, "username", default=None),
            page=_arg(args, "page", default=1),
            per_page=_arg(args, "per_page", default=30),
        )
    except IntegrationError as exc:
        raise ToolInputError(str(exc)) from exc
    return _outcome(payload)


def _search_jobs_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    del ctx
    args = _require_mapping(args)
    from .integrations import IntegrationError, search_jobs

    try:
        payload = search_jobs(
            _arg(args, "query"),
            platform=_arg(args, "platform", default="boss"),
            limit=_arg(args, "limit", "n", default=6),
        )
    except IntegrationError as exc:
        raise ToolInputError(str(exc)) from exc
    return _outcome(payload)


def _clone_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ToolInputError("url is required")
    if any(ord(char) < 32 for char in url):
        raise ToolInputError("clone URL contains control characters")
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme == "https":
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise ToolInputError("Git HTTPS URL userinfo is not allowed")
        if not parsed.hostname or parsed.query or parsed.fragment:
            raise ToolInputError("Git HTTPS URL must not contain query, fragment, or missing host")
        _reject_local_host(parsed.hostname)
        return url
    if scheme in {"ssh", "git+ssh"}:
        if parsed.password is not None or not parsed.hostname or parsed.query or parsed.fragment:
            raise ToolInputError("invalid Git SSH URL")
        _reject_local_host(parsed.hostname)
        return url
    # Git's common scp-like SSH spelling: git@github.com:org/repo.git.
    scp = re.match(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[^/:\s]+):(?P<path>[^\s]+)$", url)
    if scp:
        _reject_local_host(scp.group("host"))
        return url
    raise ToolInputError("git clone accepts HTTPS or SSH URLs only")


def _clone_prepare(args: dict, ctx: ToolContext) -> PendingApproval:
    args = _require_mapping(args)
    url = _clone_url(_arg(args, "url", "repository", "repo"))
    destination_arg = _arg(args, "destination", "path", "directory", "dir")
    if destination_arg is None:
        raise ToolInputError("destination is required")
    destination = resolve_path(ctx, destination_arg, must_exist=False)
    parent = _check_parent(ctx, destination)
    if destination.exists() or destination.is_symlink():
        raise ToolInputError("clone destination must not already exist")
    prepared = {
        "url": url,
        "resolved_destination": str(destination),
        "destination": _display_path(ctx, destination),
        "cwd": str(_workspace_root(ctx)),
        "timeout": _safe_timeout(
            _arg(args, "timeout", default=None),
            default=MAX_COMMAND_TIMEOUT,
            maximum=MAX_COMMAND_TIMEOUT,
        ),
    }
    del parent  # parent validation is the side-effect-free safety check.
    # The empty helper overrides any user/global credential.helper configured
    # on the host.  The URL still remains free of embedded credentials.
    argv = ["git", "-c", "credential.helper=", "clone", "--", url, str(destination)]
    display = _command_display(argv, shell=False)
    return PendingApproval(
        tool_name="git_clone",
        arguments=dict(args),
        summary=(
            f"目的：把公开仓库 {url} 下载到工作区 {_display_path(ctx, destination)}。\n"
            "预期结果：创建新的仓库目录并保留 Git 历史。"
        ),
        preview=_json(
            {
                "purpose": f"把公开仓库下载到工作区 {_display_path(ctx, destination)}",
                "shell": False,
                "cwd": _display_path(ctx, _workspace_root(ctx)),
                "command": display,
                "destination": _display_path(ctx, destination),
                "timeout": prepared["timeout"],
            }
        ),
        prepared=prepared,
    )


def _clone_execute(pending: PendingApproval, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(pending.arguments)
    url = _clone_url(_arg(args, "url", "repository", "repo"))
    if pending.prepared.get("url") is not None and url != pending.prepared.get("url"):
        raise ToolInputError("approved clone URL changed before execution")
    destination_arg = _arg(args, "destination", "path", "directory", "dir")
    destination = resolve_path(ctx, destination_arg, must_exist=False)
    expected_destination = pending.prepared.get("resolved_destination")
    if expected_destination is not None and not _same_path(
        destination, Path(expected_destination)
    ):
        raise PathSecurityError("approved clone destination changed before execution")
    _check_parent(ctx, destination)
    if destination.exists() or destination.is_symlink():
        raise ToolInputError("clone destination must not already exist")
    timeout = _safe_timeout(
        _arg(args, "timeout", default=None),
        default=MAX_COMMAND_TIMEOUT,
        maximum=MAX_COMMAND_TIMEOUT,
    )
    if "timeout" in pending.prepared and timeout != pending.prepared.get("timeout"):
        raise ToolInputError("approved clone timeout changed before execution")
    argv = ["git", "-c", "credential.helper=", "clone", "--", url, str(destination)]
    result = _run_subprocess(
        argv,
        shell=False,
        cwd=_workspace_root(ctx),
        timeout=timeout,
        output_limit=MAX_COMMAND_OUTPUT_BYTES,
        git=True,
        purpose=f"把公开仓库下载到工作区 {_display_path(ctx, destination)}",
        approval_id=pending.id,
        tool_call_id=pending.tool_call_id,
    )
    result["destination"] = _display_path(ctx, destination)
    result["url"] = url
    return _outcome(result)


def _update_plan_handler(args: dict, ctx: ToolContext) -> ToolOutcome:
    args = _require_mapping(args)
    supplied = _arg(args, "plan", "steps", default=[])
    if not isinstance(supplied, list):
        raise ToolInputError("plan must be a list")
    if len(supplied) > 100:
        raise ToolInputError("plan may contain at most 100 steps")
    plan: list[dict[str, Any]] = []
    for index, item in enumerate(supplied):
        if isinstance(item, str):
            plan.append({"step": item, "status": "pending"})
        elif isinstance(item, Mapping):
            plan.append(dict(item))
        else:
            raise ToolInputError(f"plan item {index} must be an object or string")
    ctx.session.plan = plan
    return _outcome({"plan": plan, "count": len(plan), "updated": True})


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
            str(right.resolve(strict=False))
        )
    except (OSError, RuntimeError):
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _specs(*, include_job_integrations: bool = True) -> list[ToolDefinition]:
    definitions = [
        ToolDefinition(
            spec=ToolSpec(
                name="list_dir",
                description="List visible entries under the workspace; protected and outside symlinks are hidden.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Workspace-relative directory."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
                    },
                },
            ),
            handler=_list_dir_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="read_file",
                description="Read a bounded UTF-8 text file inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            handler=_read_file_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="write_file",
                description="Prepare an approved UTF-8 atomic file write with a complete diff and before hash.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            handler=_write_prepare,
            execute=_write_execute,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="read_document",
                description="Read a bounded supported text, PDF, Word, Excel, or PowerPoint document.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            handler=_document_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="run_command",
                description=(
                    "准备执行本地命令并等待用户审批。必须提供简短、用户可读的 purpose，审批界面会先说明操作目的；"
                    "这不是操作系统沙箱。长时间命令可省略 timeout 后轮询 command session，也可以提供明确的正数 timeout。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "purpose": {
                            "type": "string",
                            "description": "Why this command is needed, shown to the user before approval.",
                            "minLength": 1,
                            "maxLength": MAX_COMMAND_PURPOSE_BYTES,
                        },
                        "command": {"type": ["string", "array"]},
                        "shell": {"type": "boolean", "default": False},
                        "cwd": {"type": "string"},
                        "timeout": {
                            "type": ["number", "null"],
                            "description": "Optional explicit deadline in seconds; null/omitted means no deadline.",
                        },
                        "max_output_bytes": {"type": "integer"},
                    },
                    # Legacy persisted calls without purpose remain accepted
                    # by the handler with a clear fallback, but new model
                    # calls must supply the approval explanation explicitly.
                    "required": ["command", "purpose"],
                },
            ),
            handler=_run_prepare,
            execute=_run_execute,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="fetch_url",
                description=(
                    "Fetch bounded public HTTP(S) content with validation on every redirect "
                    "and no URL credentials; this is not an OS or network sandbox. "
                    "For GitHub profiles use list_github_repositories and for GitHub identity "
                    "use github_identity; GitHub links are routed through the controlled API "
                    "compatibility path and never receive a Token in the URL. The fixed "
                    "api.github.com user-repository list paths accept only page/per_page "
                    "and never act as a general API proxy."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "timeout": {"type": "number"},
                        "max_bytes": {"type": "integer"},
                    },
                    "required": ["url"],
                },
            ),
            handler=_fetch_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="github_identity",
                description=(
                    "Return the authenticated GitHub login for the current workspace Token. "
                    "The Token is kept local and is never returned to the model."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            handler=_github_identity_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="list_github_repositories",
                description=(
                    "List bounded GitHub repositories through api.github.com. "
                    "With username equal to the authenticated Token login, include repositories "
                    "the Token can read, including private ones; a different username uses only "
                    "that user's public repositories. The Token is never a tool argument or result."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "Optional GitHub login from a profile URL.",
                        },
                        "page": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
            ),
            handler=_list_github_repositories_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="read_github",
                description=(
                    "Read a bounded public or workspace-authorized private GitHub file, README, "
                    "or directory through api.github.com. Sensitive paths are rejected."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "repository": {"type": "string", "description": "owner/repo"},
                        "path": {"type": "string", "description": "Repository-relative path; empty means root."},
                        "ref": {"type": "string"},
                        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2097152},
                    },
                    "required": ["repository", "path"],
                    "additionalProperties": False,
                },
            ),
            handler=_read_github_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="search_jobs",
                description=(
                    "Search only indexed public BOSS 直聘, 猎聘, or 智联招聘 links and summaries "
                    "deterministically; never synthesize a JD and report login/insufficient results."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "platform": {"type": "string", "enum": ["boss", "liepin", "zhilian"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            handler=_search_jobs_handler,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="git_clone",
                description="Prepare an approved HTTPS/SSH git clone into a new workspace directory.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "destination": {"type": "string"},
                        "timeout": {"type": "number"},
                    },
                    "required": ["url", "destination"],
                },
            ),
            handler=_clone_prepare,
            execute=_clone_execute,
            skill=None,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="update_plan",
                description="Replace the generic task plan stored in the agent session.",
                parameters={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "array"},
                        "steps": {"type": "array"},
                    },
                },
            ),
            handler=_update_plan_handler,
            skill=None,
        ),
    ]
    if not include_job_integrations:
        definitions = [item for item in definitions if item.spec.name != "search_jobs"]
    return definitions


def register_builtin_tools(
    registry: ToolRegistry,
    *,
    include_job_integrations: bool = True,
) -> ToolRegistry:
    """Register all domain-neutral built-ins on ``registry``.

    Returning the registry is convenient for a CLI bootstrap while retaining
    the requested mutating registration API.
    """

    if not hasattr(registry, "register"):
        raise TypeError("registry must provide register(ToolDefinition)")
    for definition in _specs(include_job_integrations=include_job_integrations):
        registry.register(definition)
    return registry


__all__ = [
    "CommandSession",
    "CommandSessionManager",
    "COMMAND_INITIAL_HANDOFF_WAIT_SECONDS",
    "DEFAULT_COMMAND_TIMEOUT",
    "DEFAULT_COMMAND_POLL_WAIT_SECONDS",
    "MAX_COMMAND_OUTPUT_BYTES",
    "MAX_COMMAND_TIMEOUT",
    "MAX_COMMAND_POLL_WAIT_SECONDS",
    "MAX_COMMAND_PURPOSE_BYTES",
    "MAX_DOCUMENT_BYTES",
    "MAX_FETCH_BYTES",
    "MAX_READ_BYTES",
    "MAX_WRITE_BYTES",
    "PathSecurityError",
    "ToolInputError",
    "command_sessions",
    "ensure_public_url",
    "list_command_sessions",
    "poll_command_session",
    "register_builtin_tools",
    "resolve_path",
    "start_command_session",
    "stop_command_session",
]
