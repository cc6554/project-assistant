"""Agent function-calling 调度循环。

模型负责对话与"调哪个工具"的决策；工具内部的确定性逻辑模型改不了。
循环：模型响应 → 有 tool_calls 就执行并把结果回灌 → 再请求，直到纯文本回答。
"""

from __future__ import annotations

import json
from typing import Callable

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..tools.session import SessionState
from .tools_registry import CoachToolRegistry

AGENT_SYSTEM_PROMPT = """你是「校招雷达·学习规划师」，通过对话帮助用户完成求职准备。

标准工作流（按用户情况灵活推进，不要机械念步骤）：
1. 拿到用户的岗位截图路径后，调用 parse_jd_screenshots 识别岗位，并向用户简要复述关键信息请其确认；
2. 通过简历/工作日志（ingest_skill_documents）、用户自述（record_self_reported_skills）、
   或访谈问答（start_interview / answer_interview_question）建立技能档案；
3. 技能档案不足时主动访谈：一次只问一个问题，等用户回答后再继续，最多问几轮，不要连环追问；
4. 用户确认岗位信息且档案基本完整后，调用 generate_learning_plan 一次性产出
   差距分析、学习计划和 GitHub 复现项目；
5. 用清晰的中文向用户展示结果：差距表格、分阶段计划、项目名称+链接+推荐理由。

纪律：
- 不要自己编造岗位要求或 GitHub 项目，所有结构化信息必须来自工具返回；
- 用户没给文件路径时不要假设路径，直接向用户要；
- 工具返回 error 时，用通俗语言告诉用户缺什么、下一步做什么；
- 回答简洁，计划和项目用 Markdown 表格呈现。"""

MAX_TOOL_ROUNDS = 8


class CoachAgent:
    def __init__(
        self,
        router: ModelRouter,
        state: SessionState | None = None,
        *,
        task: str = "default",
        on_tool: Callable[[str], None] | None = None,
    ):
        self.router = router
        self.state = state or SessionState()
        self.registry = CoachToolRegistry(router, self.state)
        self.task = task
        self.on_tool = on_tool
        self.messages: list[Message] = [
            Message(role="system", content=AGENT_SYSTEM_PROMPT)
        ]

    def chat(self, user_text: str) -> str:
        self.messages.append(Message(role="user", content=user_text))

        for _ in range(MAX_TOOL_ROUNDS):
            resp = self.router.complete(
                self.task,
                self.messages,
                tools=self.registry.specs(),
                force_tool=False,
                temperature=0.3,
                max_tokens=4096,
            )

            if not resp.tool_calls:
                self.messages.append(Message(role="assistant", content=resp.text))
                return resp.text

            # 记录带工具调用的 assistant 回合
            self.messages.append(
                Message(
                    role="assistant",
                    content=resp.text,
                    tool_calls=resp.tool_calls,
                )
            )

            for call in resp.tool_calls:
                if self.on_tool:
                    self.on_tool(f"{call.name}({_brief_args(call.name, call.arguments)})")
                result_text = self._dispatch(call.name, call.arguments)
                self.messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=result_text,
                    )
                )

        # 工具轮次用尽：兜底再问一次模型，不给工具，强制总结
        summary = self.router.complete(
            self.task,
            self.messages,
            temperature=0.3,
            max_tokens=2048,
        )
        self.messages.append(Message(role="assistant", content=summary.text))
        return summary.text

    def _dispatch(self, name: str, arguments: dict) -> str:
        if not self.registry.has(name):
            return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
        try:
            return self.registry[name].handler(arguments)
        except FileNotFoundError as exc:
            return json.dumps({"error": f"文件找不到：{exc}"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - 任何工具异常都回灌给模型处理
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )


def _brief_args(name: str, args: dict) -> str:
    if name == "record_self_reported_skills":
        text = args.get("text", "")
        return f"{text[:30]}..." if len(text) > 30 else text
    if name == "answer_interview_question":
        answer = args.get("answer", "")
        return f"{answer[:30]}..." if len(answer) > 30 else answer
    return json.dumps(args, ensure_ascii=False)[:120]
