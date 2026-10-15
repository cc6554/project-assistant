"""Built-in, domain-neutral tools for the agent kernel.

The functions in this module deliberately do not pretend to be an operating
system sandbox.  They put a small, explicit policy boundary around paths and
network requests, and leave approval of mutating operations to the kernel:
handlers prepare an operation and ``ToolDefinition.execute`` performs it only
after the returned :class:`~agent_coach.contracts.PendingApproval` is accepted.
"""

from __future__ import annotations

import difflib
import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

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
DEFAULT_COMMAND_TIMEOUT = 30.0
MAX_COMMAND_TIMEOUT = 120.0
DEFAULT_FETCH_TIMEOUT = 15.0
MAX_FETCH_TIMEOUT = 60.0


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


def _safe_timeout(value: Any, *, default: float, maximum: float) -> float:
    if value is None:
        return default
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError("timeout must be a number") from exc
    if timeout <= 0 or timeout > maximum:
        raise ToolInputError(f"timeout must be greater than 0 and at most {maximum}")
    return timeout


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
        summary=f"Write {len(new)} UTF-8 bytes to {display}",
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
    return raw.decode("utf-8", errors="replace"), clipped


def _drain_pipe(stream: Any, sink: dict[str, Any], limit: int) -> None:
    """Drain one child pipe while retaining only a bounded prefix.

    Keeping the reader alive after the cap is reached prevents a chatty child
    from blocking on a full pipe, while the bounded bytearray prevents command
    output from consuming unbounded agent memory.
    """

    kept = bytearray()
    total = 0
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            chunk = bytes(chunk)
            total += len(chunk)
            if len(kept) < limit:
                kept.extend(chunk[: limit - len(kept)])
    except (OSError, ValueError):
        # The process may be killed while a reader is blocked.  Preserve the
        # bytes already collected and let the caller report timeout state.
        pass
    finally:
        sink["data"] = bytes(kept)
        sink["truncated"] = total > limit
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass


def _run_subprocess(
    command: list[str] | str,
    *,
    shell: bool,
    cwd: Path,
    timeout: float,
    output_limit: int,
    git: bool = False,
) -> dict[str, Any]:
    display = _command_display(command, shell)
    stdout_sink: dict[str, Any] = {"data": b"", "truncated": False}
    stderr_sink: dict[str, Any] = {"data": b"", "truncated": False}
    process: Any = None
    timed_out = False
    try:
        process = subprocess.Popen(
            command,
            shell=shell,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_safe_environment(git=git),
        )
        readers = [
            threading.Thread(
                target=_drain_pipe,
                args=(process.stdout, stdout_sink, output_limit),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_pipe,
                args=(process.stderr, stderr_sink, output_limit),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # This kills the direct child.  Descendants that detach or retain
            # the pipe may outlive it; this tool makes no process-tree sandbox
            # promise, so the result says so explicitly.
            try:
                process.kill()
            except OSError:
                # It may have exited between wait timeout and kill.
                pass
            finally:
                try:
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            returncode = None
        for reader in readers:
            reader.join(timeout=2)
        stdout, stdout_clipped = _clip_bytes(stdout_sink["data"], output_limit)
        stderr, stderr_clipped = _clip_bytes(stderr_sink["data"], output_limit)
        stdout_truncated = bool(stdout_sink.get("truncated")) or stdout_clipped
        stderr_truncated = bool(stderr_sink.get("truncated")) or stderr_clipped
        return {
            "command": display,
            "shell": shell,
            "cwd": str(cwd),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timed_out": timed_out,
            **(
                {
                    "timeout": timeout,
                    "timeout_note": "Only the direct child was killed; descendants may outlive it.",
                }
                if timed_out
                else {}
            ),
        }
    except OSError:
        # Preserve the normal subprocess error semantics for callers while
        # making sure a failed spawn cannot leave reader threads around.
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
        raise


def _run_prepare(args: dict, ctx: ToolContext) -> PendingApproval:
    args = _require_mapping(args)
    command = _arg(args, "command", "cmd", "argv")
    if command is None:
        raise ToolInputError("command is required")
    shell = bool(_arg(args, "shell", default=False))
    normalised = _normalise_argv(command, shell)
    cwd_arg = _arg(args, "cwd", default=".")
    cwd = resolve_path(ctx, cwd_arg, must_exist=True)
    if not cwd.is_dir():
        raise ToolInputError("cwd is not a directory")
    timeout = _safe_timeout(
        _arg(args, "timeout", default=None),
        default=DEFAULT_COMMAND_TIMEOUT,
        maximum=MAX_COMMAND_TIMEOUT,
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
    }
    return PendingApproval(
        tool_name="run_command",
        arguments=dict(args),
        summary=f"Run command (shell={shell}, cwd={_display_path(ctx, cwd)}): {display}",
        preview=_json(
            {
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
    shell = bool(_arg(args, "shell", default=False))
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
    timeout = _safe_timeout(
        _arg(args, "timeout", default=None),
        default=DEFAULT_COMMAND_TIMEOUT,
        maximum=MAX_COMMAND_TIMEOUT,
    )
    if pending.prepared.get("timeout") is not None and timeout != pending.prepared.get("timeout"):
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
    result = _run_subprocess(
        normalised,
        shell=shell,
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
    )
    result["cwd"] = _display_path(ctx, cwd)
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
    from .integrations import IntegrationError, parse_github_url, read_github

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
        summary=f"Clone {url} into {_display_path(ctx, destination)}",
        preview=_json(
            {
                "shell": False,
                "cwd": _display_path(ctx, _workspace_root(ctx)),
                "command": display,
                "destination": _display_path(ctx, destination),
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
    if pending.prepared.get("timeout") is not None and timeout != pending.prepared.get("timeout"):
        raise ToolInputError("approved clone timeout changed before execution")
    argv = ["git", "-c", "credential.helper=", "clone", "--", url, str(destination)]
    result = _run_subprocess(
        argv,
        shell=False,
        cwd=_workspace_root(ctx),
        timeout=timeout,
        output_limit=MAX_COMMAND_OUTPUT_BYTES,
        git=True,
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
                description="Prepare a command for approval; execution is not an OS sandbox and shows shell, cwd, and command.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": ["string", "array"]},
                        "shell": {"type": "boolean", "default": False},
                        "cwd": {"type": "string"},
                        "timeout": {"type": "number"},
                        "max_output_bytes": {"type": "integer"},
                    },
                    "required": ["command"],
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
                    "and no URL credentials; this is not an OS or network sandbox."
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
    "MAX_COMMAND_OUTPUT_BYTES",
    "MAX_DOCUMENT_BYTES",
    "MAX_FETCH_BYTES",
    "MAX_READ_BYTES",
    "MAX_WRITE_BYTES",
    "PathSecurityError",
    "ToolInputError",
    "ensure_public_url",
    "register_builtin_tools",
    "resolve_path",
]
