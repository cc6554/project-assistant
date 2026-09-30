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
from .profile_builder import build_profile_from_text, question_similar

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
- 如果该问题已被回答清楚，从 open_questions 中移除它；
- **open_questions 返回空数组 [] 表示「核对完成」**——当剩余问题都已得到充分回答、技能档案已足以支撑求职评估时，请返回空数组，让对话自然结束，不要为了凑问题而生成；
- 出现以下情况应自主判断结束：核心技能缺口（项目真实性、掌握程度、投入量级）都已澄清；剩余疑问属于锦上添花而非求职必需；用户已回答多轮且内容趋于一致；
- 只有当用户明确提到**此前档案完全未覆盖的新项目 / 新领域 / 新方向**时，才追加真正的新问题（最多 3 条，宁缺毋滥）；
- 不要从已答话题里生成细节追问（如"具体参数是什么"），不要用同义改写重问同主题问题；
- 用户明确表示不了解的技能不要加入档案；
- 用户已回答、明确关闭或要求剔除的话题，无论以什么措辞出现都不得再写入 open_questions。"""


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
        max_new_questions=3,
    )
    # 确定性后处理：移除与已答问题相同或高度相似的变体（LLM 常换措辞重写同题）
    updated.open_questions = [
        q
        for q in updated.open_questions
        if not (q.strip() == question.strip() or question_similar(q, question) >= 0.6)
    ]
    return updated


# ── 待澄清自由对话（类 Codex 的灵活一问一答）──────────────────────

CLARIFY_TURN_PROMPT = """你是技术求职教练，正在通过一对一的自然对话完善用户的技能档案（类似 Codex 的交互：用户可以随时追问、解释、跑题，你再自然拉回来）。

你手头有一份「待澄清问题清单」作为提纲（按顺序排列）。你的任务是把这份清单**融进自然的对话**里逐条推进：
- 你的回复（reply）是主力：口语化、有温度，先简短回应用户刚说的内容，再顺势自然地问出清单里的问题，不要生硬念题单；
- 用户随时可以追问"什么意思"、跳过、补充背景，你要自然应对；
- 已经核对完的话题不要再重复问。

待澄清问题清单（提纲，按顺序）：
{outline}
当前技能档案（摘要，供判断答非所问的程度）：
{profile}
历史对话（最近若干条）：
{history}
用户这条消息：{message}

输出判定：
- intent：
  * answer：用户实质回答了清单里某一条（给出事实、经验、程度，哪怕不完整）
  * explain：用户对问题本身有疑问、请求解释或举例（如「什么意思」「举个例子」「怎么答」）
  * skip：用户明确表示跳过/不知道/不想答
  * other：用户补充背景、闲聊或澄清前文
- target_index：intent 为 answer/skip 时，指出用户回应的是清单里的第几条（从 0 开始；对应不上时填 -1，默认第一条）；其他意图填 -1
- reply：你的完整回复（主力）。intent=answer 且清单还有下一条时，确认已记录并自然地问出下一条（≤3 句）；清单已空时表示核对完成；explain/other 时回应并温和拉回话题；skip 时简短安抚。
严格通过 emit_result 工具输出。"""


class ClarifyTurn(BaseModel):
    intent: Literal["answer", "explain", "skip", "other"] = "answer"
    reply: str = ""
    target_index: int = -1


def clarify_turn(
    router: ModelRouter,
    profile: UserSkillProfile,
    outline: list[str],
    message: str,
    history: list[dict],
    *,
    task: str = "interview",
) -> ClarifyTurn:
    """对用户一条自由消息做意图判定；提纲（open_questions）传给 LLM 作参考。"""
    outline_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(outline)) or "（清单为空）"
    history_text = "\n".join(
        f"{h.get('role', '?')}: {h.get('content', '')}" for h in history[-8:]
    ) or "（暂无）"
    result = router.complete_json(
        task,
        [
            Message(
                role="user",
                content=CLARIFY_TURN_PROMPT.format(
                    outline=outline_text,
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


CLARIFY_OPENING_PROMPT = """你是技术求职教练，正在通过自然对话完善用户的技能档案。下面是待澄清问题清单（提纲，按顺序）：
{outline}
请输出一句自然的开场白（口语化、有温度，1~2 句），把第一条问题融进对话里自然地提出来，不要像念题单，不要编号。
严格通过 emit_result 工具输出。"""


class _Opening(BaseModel):
    opening: str


def clarify_opening(
    router: ModelRouter,
    outline: list[str],
    *,
    task: str = "interview",
) -> str:
    """生成自然开场白，把提纲第一条问题口语化地问出来。"""
    if not outline:
        return "没有待澄清问题了。"
    outline_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(outline))
    result = router.complete_json(
        task,
        [
            Message(
                role="user",
                content=CLARIFY_OPENING_PROMPT.format(outline=outline_text),
            )
        ],
        _Opening.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交开场白",
    )
    return _Opening.model_validate(result).opening.strip() or f"先看第一条：{outline[0]}"
