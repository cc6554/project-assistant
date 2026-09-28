"""领域模型（工具与智能体之间传递的强类型数据契约）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── 环节①：岗位 JD 结构化卡片 ─────────────────────────────────

Proficiency = Literal["aware", "working", "proficient", "expert"]
EvidenceSource = Literal["resume", "self_report", "chat", "work_log", "obsidian"]


class JDCard(BaseModel):
    job_title: str | None = Field(None, description="岗位名称，如 算法工程师/后端开发")
    company: str | None = None
    salary: str | None = Field(None, description="薪资原文，如 25-50K·15薪")
    location: str | None = None
    team: str | None = Field(None, description="所属部门/团队，未写明则为 null")
    hard_requirements: list[str] = Field(
        default_factory=list, description="硬性任职要求，每条一句"
    )
    skill_tags: list[str] = Field(
        default_factory=list, description="规范、简短的技术名词标签，如 PyTorch/RAG/Go"
    )
    responsibilities: list[str] = Field(default_factory=list, description="岗位职责")
    nice_to_haves: list[str] = Field(default_factory=list, description="加分项")
    raw_summary: str = Field("", description="JD 正文的连贯摘要，保留原话语气")
    uncertain_fields: list[str] = Field(
        default_factory=list,
        description="截图看不清、被截断或多张截图间相互矛盾的字段名",
    )


# ── 环节②：用户技能档案 ───────────────────────────────────────

class SkillItem(BaseModel):
    name: str
    proficiency: Proficiency = "working"
    confidence: float = Field(
        0.5, ge=0.0, le=1.0, description="模型对该技能掌握程度判断的置信度"
    )
    sources: list[EvidenceSource] = Field(default_factory=list)
    evidence: str = Field("", description="支撑该判断的事实，如简历项目/日志记录")


class UserSkillProfile(BaseModel):
    user_id: str = "default"
    summary: str = ""
    target_direction: str | None = Field(None, description="求职方向，如 大模型算法/后端")
    skills: list[SkillItem] = Field(default_factory=list)
    open_questions: list[str] = Field(
        default_factory=list, description="证据不足、下一轮访谈需要追问的问题"
    )
    updated_at: datetime = Field(default_factory=datetime.now)


# ── 环节③：差距分析与学习计划 ─────────────────────────────────

GapStatus = Literal["met", "weak", "missing", "evidence_insufficient"]


class SkillGap(BaseModel):
    requirement: str
    matched_skill: str | None = None
    status: GapStatus
    note: str = ""


class LearningPhase(BaseModel):
    phase: str = Field(description="阶段名，如 第一阶段：补基础")
    goal: str
    topics: list[str] = Field(default_factory=list)
    suggested_practice: str = ""
    duration_weeks: int | None = None


class LearningPlan(BaseModel):
    direction: str = ""
    gaps: list[SkillGap] = Field(default_factory=list)
    phases: list[LearningPhase] = Field(default_factory=list)
    github_search_queries: list[str] = Field(
        default_factory=list,
        description="用于在 GitHub 找复现项目的检索词，已按优先级排序",
    )


# ── 环节④：GitHub 项目推荐 ────────────────────────────────────

class GitHubRepo(BaseModel):
    full_name: str
    html_url: str
    description: str = ""
    language: str | None = None
    stargazers_count: int = 0
    updated_at: str = ""
    reason: str = Field("", description="为什么适合该用户复现/学习")
    match_score: float = 0.0
