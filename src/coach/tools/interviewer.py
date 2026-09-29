"""工具③：多轮技能访谈。

智能体每次只问一个问题；问题来自档案的 open_questions 和针对求职方向的
关键缺口。用户回答后抽取技能增量，以 source=chat 回写同一份档案。
"""

from __future__ import annotations

from typing import Literal

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


# ── 待澄清自由对话（类 Codex 的灵活一问一答）──────────────────────

CLARIFY_TURN_PROMPT = """你是技术求职教练，正在通过一对一的自由对话完善用户的技能档案（类似 Codex 的交互：用户可以随时追问、解释、跑题，你再拉回来）。

你刚问了用户一个待澄清问题，用户回复了一条消息。请判断用户意图：

当前待澄清问题：{question}
当前技能档案（摘要，供判断答非所问的程度）：
{profile}
历史对话（最近若干条）：
{history}
用户这条消息：{message}

意图判定规则：
- answer：用户实质回答了当前问题（给出事实、经验、程度，哪怕不完整）。reply 简短确认（如「收到，已记入档案」），不要重复问题；
- explain：用户对问题本身有疑问、请求解释或举例（如「什么意思」「举个例子」「怎么答」）。reply 要通俗解释这个问题在问什么、大概怎么答，并鼓励ta接着回答，**不消耗问题**；
- skip：用户明确表示跳过/不知道/不想答。reply 简短安抚并提示可以随时补充；
- other：用户说了与当前问题不直接相关的内容（补充背景、闲聊、澄清前文）。reply 回应它，然后温和地把话题拉回当前问题，**不消耗问题**；
- 如果消息同时包含回答和追问，按 answer 处理，并在 reply 里顺带解答其疑问。
严格通过 emit_result 工具输出。"""


class ClarifyTurn(BaseModel):
    intent: Literal["answer", "explain", "skip", "other"] = "answer"
    reply: str = ""


def clarify_turn(
    router: ModelRouter,
    profile: UserSkillProfile,
    current_question: str,
    message: str,
    history: list[dict],
    *,
    task: str = "interview",
) -> ClarifyTurn:
    """对用户一条自由消息做意图判定（回答 / 追问 / 跳过 / 其他）。"""
    history_text = "\n".join(
        f"{h.get('role', '?')}: {h.get('content', '')}" for h in history[-8:]
    ) or "（暂无）"
    result = router.complete_json(
        task,
        [
            Message(
                role="user",
                content=CLARIFY_TURN_PROMPT.format(
                    question=current_question,
                    profile=profile.model_dump_json(),
                    history=history_text,
                    message=message,
                ),
            )
        ],
        ClarifyTurn.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交意图判定与回复",
    )
    return ClarifyTurn.model_validate(result)
