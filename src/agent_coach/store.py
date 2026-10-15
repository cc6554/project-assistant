"""Durable, workspace-scoped storage for agent sessions.

The kernel deliberately keeps persistence small and boring: one JSON document per
session, an adjacent lock file, and an atomic replace on every write.  A session
file is the recovery boundary for the engine, so the store does not use a global
database and it never silently falls back to another workspace.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .contracts import AgentSession

try:  # pragma: no cover - which branch runs depends on the host platform
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows CI
    fcntl = None

try:  # pragma: no cover - which branch runs depends on the host platform
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - exercised on POSIX CI
    msvcrt = None


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class StoreError(RuntimeError):
    """Base class for recoverable store errors."""


class InvalidSessionId(StoreError, ValueError):
    pass


class SessionNotFound(StoreError, FileNotFoundError):
    pass


class WorkspaceMismatch(StoreError, ValueError):
    pass


class RevisionConflict(StoreError):
    """The caller tried to save a stale session snapshot."""


class SessionBusy(StoreError, TimeoutError):
    """Another process currently owns the session lock."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_path(path: str | Path) -> Path:
    """Resolve a path without requiring it to exist."""

    return Path(path).expanduser().resolve(strict=False)


def validate_session_id(session_id: str) -> str:
    """Validate a session id before it can become part of a filesystem path."""

    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        raise InvalidSessionId("session id 只能包含字母、数字、下划线和短横线")
    return session_id


class _FileLock:
    """A small cross-process advisory lock.

    On Windows ``msvcrt.locking`` locks a byte in a file and releases it when the
    handle closes, which gives us automatic cleanup after a crashed process.  On
    POSIX ``flock`` has the same process-death behaviour.  The lock is acquired
    in non-blocking mode so a CLI can report a useful "session busy" message.
    """

    def __init__(self, path: Path, *, timeout: float = 0.0):
        self.path = path
        self.timeout = max(0.0, float(timeout))
        self._handle = None
        self._windows = os.name == "nt"

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if self._windows and msvcrt is not None:
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"0")
                self._handle.flush()
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        self._close()
                        raise SessionBusy(f"session 正被其他进程占用: {self.path.stem}") from exc
                    time.sleep(0.02)
        elif fcntl is not None:
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN) or time.monotonic() >= deadline:
                        self._close()
                        raise SessionBusy(f"session 正被其他进程占用: {self.path.stem}") from exc
                    time.sleep(0.02)
        else:  # pragma: no cover - only for an unusual Python platform
            return

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if self._windows and msvcrt is not None:
                self._handle.seek(0)
                try:
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            elif fcntl is not None:
                try:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            self._close()

    def _close(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.close()
            self._handle = None

    def __enter__(self) -> "_FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class SessionStore:
    """Store sessions below one workspace-owned state directory.

    Args:
        workspace: The only workspace this store may read or write.
        state_dir: Optional state directory.  By default it is
            ``workspace/.agent-coach/sessions``.
    """

    def __init__(self, workspace: str | Path, state_dir: str | Path | None = None):
        self.workspace = canonical_path(workspace)
        self.state_dir = canonical_path(state_dir or self.workspace / ".agent-coach" / "sessions")
        try:
            self.state_dir.relative_to(self.workspace)
        except ValueError as exc:
            raise WorkspaceMismatch("session store 必须位于 workspace 内") from exc
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def _held(self) -> set[str]:
        held = getattr(self._local, "held", None)
        if held is None:
            held = set()
            self._local.held = held
        return held

    def _path(self, session_id: str) -> Path:
        validate_session_id(session_id)
        return self.state_dir / f"{session_id}.json"

    def _lock_path(self, session_id: str) -> Path:
        validate_session_id(session_id)
        return self.state_dir / f"{session_id}.lock"

    @contextlib.contextmanager
    def lock(self, session_id: str, *, timeout: float = 0.0) -> Iterator[None]:
        """Hold one session mutex; nested use by the same thread is reentrant."""

        validate_session_id(session_id)
        held = self._held()
        if session_id in held:
            yield
            return
        with _FileLock(self._lock_path(session_id), timeout=timeout):
            held.add(session_id)
            try:
                yield
            finally:
                held.discard(session_id)

    def _read_path(self, path: Path, session_id: str | None = None) -> AgentSession:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionNotFound(f"session 不存在: {session_id or path.stem}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"session 文件损坏: {path.name}") from exc
        try:
            session = AgentSession.model_validate(payload)
        except Exception as exc:  # pydantic validation errors are implementation details
            raise StoreError(f"session 文件格式无效: {path.name}") from exc
        if canonical_path(session.workspace) != self.workspace:
            raise WorkspaceMismatch("session 属于另一个 workspace，拒绝读取")
        expected_id = validate_session_id(session_id or path.stem)
        if session.id != expected_id:
            raise StoreError("session 文件名与 payload id 不一致")
        return session

    def get(self, session_id: str) -> AgentSession:
        """Read a session after validating its id and workspace binding."""

        return self._read_path(self._path(session_id), session_id)

    load = get

    def exists(self, session_id: str) -> bool:
        try:
            return self._path(session_id).is_file()
        except InvalidSessionId:
            return False

    def create(self, *, active_skills: list[str] | None = None) -> AgentSession:
        """Create and persist a new session with a collision-safe id."""

        for _ in range(20):
            session = AgentSession(
                id=uuid.uuid4().hex,
                workspace=str(self.workspace),
                active_skills=list(active_skills or []),
            )
            path = self._path(session.id)
            try:
                # Creation is exclusive and does not overwrite a pre-existing id.
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2))
                    handle.flush()
                    os.fsync(handle.fileno())
                return session
            except FileExistsError:
                continue
        raise StoreError("无法生成唯一 session id")

    def save(self, session: AgentSession, *, expected_revision: int | None = None) -> AgentSession:
        """Save with optimistic revision checking and an atomic replace.

        The returned object is the canonical snapshot and has its revision
        incremented.  The input object is updated too, which keeps a caller's
        in-memory reference useful while still making stale writers fail.
        """

        if canonical_path(session.workspace) != self.workspace:
            raise WorkspaceMismatch("session workspace 与 store 不一致")
        sid = validate_session_id(session.id)
        # A caller that does not pass an explicit precondition still saves the
        # snapshot it loaded.  This makes the normal API optimistic-CAS by
        # default; ``expected_revision=-1`` remains the explicit new-session
        # escape hatch.
        if expected_revision is None:
            expected_revision = int(session.revision)
        with self.lock(sid):
            return self._save_locked(session, expected_revision=expected_revision)

    def _save_locked(self, session: AgentSession, *, expected_revision: int | None = None) -> AgentSession:
        sid = validate_session_id(session.id)
        path = self._path(sid)
        current = None
        if path.exists():
            current = self._read_path(path, sid)
        if current is None:
            if expected_revision not in (None, -1):
                raise RevisionConflict(f"session {sid} 不存在，revision 不匹配")
            next_revision = max(0, int(session.revision))
        else:
            if expected_revision is not None and current.revision != expected_revision:
                raise RevisionConflict(
                    f"session {sid} revision 已从 {expected_revision} 变为 {current.revision}"
                )
            # When no precondition is supplied, saving the same snapshot remains
            # safe but still advances the revision to make writes observable.
            next_revision = current.revision + 1
        session.revision = next_revision
        payload = session.model_dump(mode="json")
        temp = path.with_name(f".{sid}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            # Ensure the directory entry is durable where the platform permits it.
            with contextlib.suppress(OSError):
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
        return session

    def list(self) -> list[AgentSession]:
        """Load all valid sessions in this workspace, newest first."""

        rows: list[tuple[float, AgentSession]] = []
        for path in self.state_dir.glob("*.json"):
            try:
                rows.append((path.stat().st_mtime, self._read_path(path)))
            except (StoreError, OSError):
                # A malformed/stale file should not make the sessions command
                # unusable; callers can inspect the file themselves if needed.
                continue
        rows.sort(key=lambda item: item[0], reverse=True)
        return [session for _, session in rows]

    list_sessions = list

    def recover_running(self, session: AgentSession, *, reason: str | None = None) -> AgentSession:
        """Convert a persisted in-flight claim into an uncertain outcome.

        This is intentionally explicit.  The engine calls it while it owns the
        session mutex at startup; a normal read never changes state.  An uncertain
        claim can never be replayed automatically.
        """

        if not session.execution or session.execution.get("status") != "running":
            return session
        execution = dict(session.execution)
        execution["status"] = "uncertain"
        execution["uncertain_at"] = _utc_now()
        execution["reason"] = reason or "上一次进程在工具执行期间退出，外部副作用状态未知"
        session.execution = execution
        return session

    def delete(self, session_id: str) -> None:
        """Delete one session after validating its id."""

        sid = validate_session_id(session_id)
        with self.lock(sid):
            try:
                self._path(sid).unlink()
            except FileNotFoundError:
                raise SessionNotFound(f"session 不存在: {sid}")


__all__ = [
    "InvalidSessionId",
    "RevisionConflict",
    "SessionBusy",
    "SessionNotFound",
    "SessionStore",
    "StoreError",
    "WorkspaceMismatch",
    "canonical_path",
    "validate_session_id",
]
