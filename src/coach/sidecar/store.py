"""多会话存储：data/sessions/<id>.json + 关联上传目录。"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from ..domain.schemas import JDCard
from ..tools.session import SessionState
from .paths import sessions_dir


class SessionNotFound(KeyError):
    pass


class SessionStore:
    def __init__(self, base: Path | None = None):
        self.base = base or sessions_dir()
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.base / f"{session_id}.json"

    def _uploads(self, session_id: str) -> Path:
        d = self.base / session_id / "uploads"
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
        state.save(self._path(state.session_id))

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
        upload_root = self.base / session_id
        if upload_root.exists():
            import shutil

            shutil.rmtree(upload_root, ignore_errors=True)

    def save_uploads(self, session_id: str, files: dict[str, bytes]) -> list[str]:
        """files: {原始文件名: 内容}，返回落盘绝对路径列表。"""
        upload_dir = self._uploads(session_id)
        paths = []
        used: set[str] = set()
        for name, content in files.items():
            safe = Path(name).name  # 防路径穿越
            target = upload_dir / safe
            i = 1
            while target.name in used or target.exists():
                stem, suffix = Path(safe).stem, Path(safe).suffix
                target = upload_dir / f"{stem}_{i}{suffix}"
                i += 1
            used.add(target.name)
            target.write_bytes(content)
            paths.append(str(target))
        return paths
