"""工具③：多轮技能访谈。

智能体每次只问一个问题；问题来自档案的 open_questions 和针对求职方向的
关键缺口。用户回答后抽取技能增量，以 source=chat 回写同一份档案。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import UserSkillProfile
from .profile_builder import build_profile_from_text

NEXT_QUESTION_PROMPT = """你是技术求职教练，正在通过访谈补全用户的技能档案。

当前技能档案：
{profile}

历史访谈记录：
{history}

要求：
1. 只输出一个最关键、最具体的问题（优先问 open_questions 和核心技能的真实项目经验），不要一次问多个；
2. 问题要能从回答判断出真实掌握程度，例如「你在 XX 项目里具体负责哪部分？遇到过什么难点？」；
3. 已经问过的问题不要重复；
4. 如果档案已足够支撑求职方向的差距分析，返回 done=true、question=null；
5. 严格通过 emit_result 工具输出。"""

APPLY_ANSWER_HINT = """以下是技能访谈中的一轮对话。请从用户回答中抽取技能增量：
- source 一律视为 chat（系统会强制覆盖）；
- 回答中体现的真实项目细节可以提高 confidence；
- 如果该问题已被回答清楚，从 open_questions 中移除它；回答引出新的疑问才加入 open_questions；
- 用户明确表示不了解的技能不要加入档案。"""


class _NextQuestion(BaseModel):
    question: str | None = None
    done: bool = False


def next_interview_question(
    router: ModelRouter,
    profile: UserSkillProfile,
    history: list[tuple[str, str]],
    *,
    task: str = "interview",
    max_rounds: int = 8,
) -> str | None:
    if len(history) >= max_rounds:
        return None

    history_text = (
        "\n".join(f"Q: {q}\nA: {a}" for q, a in history[-6:]) or "（暂无）"
    )
    result = router.complete_json(
        task,
        [
            Message(
                role="user",
                content=NEXT_QUESTION_PROMPT.format(
                    profile=profile.model_dump_json(),
                    history=history_text,
                ),
            )
        ],
        _NextQuestion.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交下一个访谈问题或结束标记",
    )
    decision = _NextQuestion.model_validate(result)
    if decision.done:
        return None
    return decision.question


def apply_interview_answer(
    router: ModelRouter,
    profile: UserSkillProfile,
    question: str,
    answer: str,
    *,
    task: str = "interview",
) -> UserSkillProfile:
    """把一轮问答并入档案；已回答清楚的问题从 open_questions 移除。"""
    updated = build_profile_from_text(
        router,
        f"问题：{question}\n用户回答：{answer}\n\n{APPLY_ANSWER_HINT}",
        source="chat",
        existing=profile,
        task=task,
    )
    # 确定性后处理：本轮问题若仍被模型保留，直接移除（已问过）
    updated.open_questions = [
        q for q in updated.open_questions if q.strip() != question.strip()
    ]
    return updated
