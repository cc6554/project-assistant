"""工具④：JD 要求 × 用户技能档案 → 差距分析 + 分阶段学习计划 + GitHub 检索词。"""

from __future__ import annotations

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import JDCard, LearningPlan, UserSkillProfile

PLAN_SYSTEM_PROMPT = """你是资深技术求职教练，要根据岗位要求和用户现有技能档案，产出可执行的学习计划。

差距判定规则（status）：
- met：用户档案中有对应技能，且 proficiency≥proficient 或有多来源/项目证据；
- evidence_insufficient：档案里有这项技能，但只有简历/自填声明、confidence 偏低、看不到项目证据；
- weak：技能存在但仅 aware/working，达不到岗位要求的深度；
- missing：档案中完全没有。

计划要求：
1. gaps 覆盖 JD 的全部 hard_requirements 和关键 skill_tags，每条给出 matched_skill（档案中对应技能名，没有则 null）和一句话 note；
2. phases 按优先级分 2~5 个阶段，先补 missing 的核心硬技能，再补 weak，evidence_insufficient 通过做项目补证据而不是重学；
3. 每个阶段 topics 具体到知识点，suggested_practice 给出可动手的练习，duration_weeks 给出合理估计；
4. github_search_queries 输出 3~8 条用于找「可复现项目」的英文检索词，聚焦最大的技能缺口，
   包含技术名+实现形态，例如 "RAG implementation langchain"、"distributed key value go"，按优先级排序；
5. 严格通过 emit_result 工具输出。"""


def build_learning_plan(
    router: ModelRouter,
    jd: JDCard,
    profile: UserSkillProfile,
    *,
    task: str = "planning",
    max_attempts: int = 3,
) -> LearningPlan:
    """差距分析 + 分阶段计划（结构化 JSON 经工具调用输出）。

    实测 DeepSeek 偶发返回「只有 direction、gaps/phases 全空」的残缺结果，
    因此对空结果做有限重试（每次重新请求，模型输出有随机性）。
    """
    user_content = (
        f"【岗位信息】\n{jd.model_dump_json()}\n\n"
        f"【用户技能档案】\n{profile.model_dump_json()}"
    )
    last_plan: LearningPlan | None = None
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            result = router.complete_json(
                task,
                [
                    Message(role="system", content=PLAN_SYSTEM_PROMPT),
                    Message(role="user", content=user_content),
                ],
                LearningPlan.model_json_schema(),
                tool_name="emit_result",
                tool_description="提交差距分析与分阶段学习计划",
                temperature=0.3,
                max_tokens=8192,
            )
            plan = LearningPlan.model_validate(result)
        except Exception as exc:  # noqa: BLE001 - 模型输出异常/JSON 解析失败都算一次失败
            last_error = exc
            continue
        if not plan.direction:
            plan.direction = jd.job_title or profile.target_direction or "目标岗位"
        if plan.gaps and plan.phases:
            return plan
        last_plan = plan  # 残缺结果留作兜底
    reason = f"最后一次错误：{last_error}" if last_error else "gaps/phases 为空"
    raise ValueError(
        f"模型连续 {max_attempts} 次未产出有效计划（{reason}），请重试或更换模型配置"
    )
