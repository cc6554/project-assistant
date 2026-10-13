"""多会话存储：data/sessions/<id>.json + 关联上传目录。"""

from __future__ import annotations

import uuid
import re
from threading import RLock
from datetime import datetime
from pathlib import Path

from ..domain.schemas import JDCard
from ..tools.session import SessionState
from .paths import sessions_dir


class SessionNotFound(KeyError):
    pass


class SessionConflict(RuntimeError):
    pass


class SessionStore:
    def __init__(self, base: Path | None = None):
        self.base = base or sessions_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.base = self.base.resolve()
        self._lock = RLock()

    def lock(self, session_id: str):
        self._path(session_id)
        return self._lock

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.base) or resolved == self.base:
            raise ValueError("会话路径超出数据目录")
        return resolved

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", session_id):
            raise ValueError("无效会话 ID")
        if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", session_id):
            raise ValueError("无效会话 ID")
        return self._contained(self.base / f"{session_id}.json")

    def _uploads(self, session_id: str) -> Path:
        self._path(session_id)
        d = self._contained(self.base / session_id / "uploads")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_sessions(self) -> list[dict]:
        items: list[dict] = []
        for f in sorted(self.base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                state = SessionState.load(f)
            except Exception:  # noqa: BLE001 - 损坏文件不影响列表
                continue
            items.append(
                {
                    "id": state.session_id,
                    "job_title": state.jd.job_title if state.jd else None,
                    "company": state.jd.company if state.jd else None,
                    "skill_count": len(state.profile.skills) if state.profile else 0,
                    "interview_rounds": len(state.interview_history),
                    "has_plan": state.plan is not None,
                    "repo_count": len(state.repos),
                    "updated_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                }
            )
        return items

    def create(self) -> SessionState:
        session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        state = SessionState(session_id=session_id)
        self.save(state)
        return state

    def get(self, session_id: str) -> SessionState:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFound(session_id)
        return SessionState.load(path)

    def save(self, state: SessionState) -> None:
        with self._lock:
            path = self._path(state.session_id)
            current = SessionState.load(path) if path.exists() else None
            if current is not None and current.agent_running:
                protected = {"jd", "profile", "plan", "repos", "interview_history", "coach_history", "repository_preferences", "jd_sources"}
                if any(getattr(state, key) != getattr(current, key) for key in protected):
                    raise SessionConflict("Agent 正在运行，请等待结束后修改会话资料")
            if (current is not None and current.revision != state.revision) or (current is None and state.revision != 0):
                raise SessionConflict("会话已更新，请刷新后重试；此次修改未覆盖新数据")
            state.revision += 1
            try:
                state.save(path)
            except Exception:
                state.revision -= 1
                raise

    def delete(self, session_id: str) -> None:
        with self._lock:
            path = self._path(session_id)
            if path.exists() and self.get(session_id).agent_running:
                raise SessionConflict("Agent 正在运行，请等待结束后删除会话")
            upload_root = self._contained(self.base / session_id)
            if path.exists():
                path.unlink()
            if upload_root.exists():
                import shutil
                shutil.rmtree(upload_root)

    def save_uploads(self, session_id: str, files: dict[str, bytes] | list[tuple[str, bytes]]) -> list[str]:
        """files: {原始文件名: 内容}，返回落盘绝对路径列表。"""
        upload_dir = self._uploads(session_id)
        paths = []
        used: set[str] = set()
        for name, content in (files.items() if isinstance(files, dict) else files):
            safe = Path(name.replace("\\", "/")).name
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe).strip(" .") or "upload"
            safe = f"{uuid.uuid4().hex[:8]}_{safe}"
            target = upload_dir / safe
            i = 1
            while target.name in used or target.exists():
                stem, suffix = Path(safe).stem, Path(safe).suffix
                target = upload_dir / f"{stem}_{i}{suffix}"
                i += 1
            used.add(target.name)
            target = self._contained(target)
            with target.open("xb") as output:
                output.write(content)
            paths.append(str(target))
        return paths
