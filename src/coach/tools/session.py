"""会话状态：一次完整咨询中的 JD 卡片、技能档案、计划、项目、访谈记录。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..domain.schemas import GitHubRepo, JDCard, LearningPlan, UserSkillProfile


class SessionState(BaseModel):
    session_id: str = "default"
    jd: JDCard | None = None
    profile: UserSkillProfile | None = None
    plan: LearningPlan | None = None
    repos: list[GitHubRepo] = []
    interview_history: list[list[str]] = []  # [[question, answer], ...]
    coach_history: list[list[str]] = []  # [[user, assistant], ...] 导师对话
    agent_thread: list[dict] | None = None  # Agent 会话消息（内部格式，含 tool 消息）
    agent_pending: dict | None = None  # 等待用户确认的动作 {action_id, kind, summary, tool_call_id, payload}
    agent_logs: list[dict] = []  # Agent 操作时间线（Codex 式展示）：[{ts, type, role, content, kind, summary, output}]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            self.model_dump_json(indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "SessionState":
        path = Path(path)
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def history_pairs(self) -> list[tuple[str, str]]:
        return [(qa[0], qa[1]) for qa in self.interview_history]
