"""组合技能：把原子工具串成确定性管线。

智能体可以一句话调用整条管线，也可以拆开调用单个工具。
管线内部不做任何"决策"，只按顺序执行并校验前置条件。
"""

from __future__ import annotations

from pathlib import Path

from ..core.llm.router import ModelRouter
from ..domain.schemas import GitHubRepo, JDCard, LearningPlan, UserSkillProfile
from .github_search import search_github_projects
from .jd_parser import parse_jd_screenshots
from .planner import build_learning_plan


class PipelinePreconditionError(RuntimeError):
    pass


def plan_and_recommend(
    router: ModelRouter,
    jd: JDCard,
    profile: UserSkillProfile,
    *,
    min_stars: int = 50,
    llm_rerank: bool = True,
) -> tuple[LearningPlan, list[GitHubRepo]]:
    """差距分析 + 学习计划 → 按计划检索词找 GitHub 复现项目。"""
    plan = build_learning_plan(router, jd, profile)
    repos = search_github_projects(
        router,
        plan.github_search_queries,
        profile,
        min_stars=min_stars,
        llm_rerank=llm_rerank,
    )
    return plan, repos


def full_pipeline_from_screenshots(
    router: ModelRouter,
    image_paths: list[str | Path],
    profile: UserSkillProfile,
) -> tuple[JDCard, LearningPlan, list[GitHubRepo]]:
    """截图 → JD 卡片 → 计划 → 项目（用户确认 JD 的环节由调用方/UI 负责）。"""
    jd = parse_jd_screenshots(router, image_paths)
    plan, repos = plan_and_recommend(router, jd, profile)
    return jd, plan, repos
