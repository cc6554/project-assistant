"""Offline scripted model used by ``--demo``.

The normal demo follows the product's one-agent job-coaching protocol with
clearly labelled fictional material.  It still calls the real kernel tools, so
path policy, approval handling, atomic writes, and transcript persistence are
exercised without contacting a provider or GitHub.  ``--no-skills`` retains a
small generic diagnostic script for kernel developers.
"""

from __future__ import annotations

from .legacy.core.schema import LLMResponse, Message, ToolCall, ToolSpec


DEMO_MARKER = "离线演示模型（脚本，不联网；以下 JD、技能和计划均为虚构示例）"
INSPECT_TOOL = "list_dir"
WRITE_TOOL = "write_file"
SET_JD_TOOL = "set_jd"
SET_PROFILE_TOOL = "set_profile"
BUILD_PLAN_TOOL = "build_plan"


class DemoRouter:
    """A deterministic ``ModelRouter``-shaped offline script."""

    provider = "offline-demo"
    model = "scripted-demo"

    def complete(
        self,
        task: str,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        force_tool: bool | str = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        available = {spec.name for spec in tools or []}
        tool_results = [message for message in messages if message.role == "tool"]

        # The product path uses structured artifacts submitted by the single
        # Agent.  The fixture is intentionally explicit and fictional; it is
        # never inferred from the user's prompt or written into their profile.
        if {SET_JD_TOOL, SET_PROFILE_TOOL, BUILD_PLAN_TOOL} <= available:
            completed = {str(message.name or "") for message in tool_results}
            if SET_JD_TOOL not in completed:
                return LLMResponse(
                    text=f"{DEMO_MARKER}：先提交岗位卡片。",
                    tool_calls=[
                        ToolCall(
                            id="demo-jd-1",
                            name=SET_JD_TOOL,
                            arguments={
                                "jd": {
                                    "job_title": "Python 后端工程师（演示）",
                                    "company": "Example Labs（虚构）",
                                    "hard_requirements": ["Python", "HTTP API", "自动化测试"],
                                    "skill_tags": ["Python", "HTTP", "pytest"],
                                    "responsibilities": ["实现可测试的 HTTP 服务"],
                                    "raw_summary": "这是离线演示使用的虚构岗位材料。",
                                },
                                "source": {
                                    "kind": "agent_argument",
                                    "evidence": ["离线演示脚本内置的虚构 JD"],
                                },
                            },
                        )
                    ],
                    stop_reason="tool_call",
                    provider=self.provider,
                    model=self.model,
                )
            if SET_PROFILE_TOOL not in completed:
                return LLMResponse(
                    text="已读取演示岗位。下一步提交一份同样虚构的技能档案。",
                    tool_calls=[
                        ToolCall(
                            id="demo-profile-1",
                            name=SET_PROFILE_TOOL,
                            arguments={
                                "profile": {
                                    "user_id": "offline-demo",
                                    "summary": "脚本虚构的 Python 入门后端经历。",
                                    "target_direction": "Python 后端",
                                    "skills": [
                                        {
                                            "name": "Python",
                                            "proficiency": "working",
                                            "confidence": 0.8,
                                            "sources": ["self_report"],
                                            "evidence": "离线演示脚本虚构：写过 CSV 解析器。",
                                        }
                                    ],
                                    "open_questions": ["真实项目中是否使用过 HTTP 测试？"],
                                },
                                "source": {
                                    "kind": "agent_argument",
                                    "evidence": ["离线演示脚本内置的虚构技能经历"],
                                },
                            },
                        )
                    ],
                    stop_reason="tool_call",
                    provider=self.provider,
                    model=self.model,
                )
            if BUILD_PLAN_TOOL not in completed:
                return LLMResponse(
                    text="演示技能档案已保存。下一步提交差距分析和练习计划。",
                    tool_calls=[
                        ToolCall(
                            id="demo-plan-1",
                            name=BUILD_PLAN_TOOL,
                            arguments={
                                "plan": {
                                    "direction": "Python 后端",
                                    "gaps": [
                                        {
                                            "requirement": "HTTP API",
                                            "status": "missing",
                                            "note": "演示材料没有 HTTP 项目证据。",
                                        },
                                        {
                                            "requirement": "自动化测试",
                                            "status": "weak",
                                            "matched_skill": "Python",
                                            "note": "需要补充可复现的测试证据。",
                                        },
                                    ],
                                    "phases": [
                                        {
                                            "phase": "实现 HTTP 服务",
                                            "goal": "完成一个带测试的最小 HTTP API",
                                            "topics": ["请求路由", "pytest"],
                                            "suggested_practice": "为一个公开练习项目补齐端点和测试。",
                                            "duration_weeks": 1,
                                        }
                                    ],
                                    "github_search_queries": ["python http api pytest"],
                                }
                            },
                        )
                    ],
                    stop_reason="tool_call",
                    provider=self.provider,
                    model=self.model,
                )
            if WRITE_TOOL in available and not any(message.name == WRITE_TOOL for message in tool_results):
                return LLMResponse(
                    text="演示计划已保存。下一步把计划写入工作区，仍需你的审批。",
                    tool_calls=[
                        ToolCall(
                            id="demo-write-job-1",
                            name=WRITE_TOOL,
                            arguments={
                                "path": "demo-output.txt",
                                "content": (
                                    "# 求职辅导演示（虚构）\n\n"
                                    "岗位：Python 后端工程师（Example Labs，虚构）\n"
                                    "练习：实现 HTTP API，并用 pytest 留下可复现证据。\n"
                                ),
                            },
                        )
                    ],
                    stop_reason="tool_call",
                    provider=self.provider,
                    model=self.model,
                )
            return LLMResponse(
                text=f"{DEMO_MARKER}：JD、技能档案和练习计划已按同一会话提交；工作区工件可在审批后查看。",
                stop_reason="stop",
                provider=self.provider,
                model=self.model,
            )

        # ``--no-skills`` is an explicit developer diagnostic mode.  Keep its
        # old generic kernel exercise available without making it the product
        # onboarding flow.
        if INSPECT_TOOL in available and not any(message.name == INSPECT_TOOL for message in tool_results):
            return LLMResponse(
                text=f"{DEMO_MARKER}：先读取 workspace 目录，再展示审批。",
                tool_calls=[
                    ToolCall(
                        id="demo-list-1",
                        name=INSPECT_TOOL,
                        arguments={"path": ".", "limit": 20},
                    )
                ],
                stop_reason="tool_call",
                provider=self.provider,
                model=self.model,
            )
        if WRITE_TOOL in available and not any(message.name == WRITE_TOOL for message in tool_results):
            return LLMResponse(
                text="目录读取完成。下一步写入一个演示文件，需要你的审批。",
                tool_calls=[
                    ToolCall(
                        id="demo-write-1",
                        name=WRITE_TOOL,
                        arguments={
                            "path": "demo-output.txt",
                            "content": "Generated by the offline agent-coach demo.\n",
                        },
                    )
                ],
                stop_reason="tool_call",
                provider=self.provider,
                model=self.model,
            )
        return LLMResponse(
            text=f"{DEMO_MARKER}：流程已结束。",
            stop_reason="stop",
            provider=self.provider,
            model=self.model,
        )


__all__ = [
    "BUILD_PLAN_TOOL",
    "DEMO_MARKER",
    "DemoRouter",
    "INSPECT_TOOL",
    "SET_JD_TOOL",
    "SET_PROFILE_TOOL",
    "WRITE_TOOL",
]
