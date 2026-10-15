"""Domain-neutral contracts: the agent kernel knows no job-specific state."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field
from .legacy.core.schema import Message, ToolCall, ToolSpec


class PendingApproval(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tool_call_id: str = ""
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    summary: str
    preview: str = ""
    prepared: dict = Field(default_factory=dict)


class AgentSession(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    workspace: str
    revision: int = 0
    messages: list[Message] = Field(default_factory=list)
    active_skills: list[str] = Field(default_factory=list)
    skill_state: dict[str, dict] = Field(default_factory=dict)
    plan: list[dict] = Field(default_factory=list)
    pending: PendingApproval | None = None
    queued_calls: list[ToolCall] = Field(default_factory=list)
    execution: dict | None = None
    events: list[dict] = Field(default_factory=list)


@dataclass
class ToolContext:
    workspace: Path
    session: AgentSession
    router: Any


@dataclass
class ToolOutcome:
    content: str
    data: dict | None = None


@dataclass
class ToolDefinition:
    spec: ToolSpec
    handler: Callable[[dict, ToolContext], ToolOutcome | PendingApproval]
    execute: Callable[[PendingApproval, ToolContext], ToolOutcome] | None = None
    skill: str | None = None


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.spec.name in self._tools:
            raise ValueError(f"Duplicate tool: {definition.spec.name}")
        self._tools[definition.spec.name] = definition

    def get(self, name: str, active_skills: list[str] | None = None) -> ToolDefinition:
        tool = self._tools[name]
        if tool.skill is not None and (active_skills is None or tool.skill not in active_skills):
            raise PermissionError(f"Activate skill {tool.skill} before calling {name}")
        return tool

    def specs(self, active_skills: list[str]) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values() if t.skill is None or t.skill in active_skills]
