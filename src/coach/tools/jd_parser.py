"""工具：岗位截图 → JDCard（多图合并去重 + 固定 schema 输出）。

这是智能体可调用的原子工具之一，内部把多图合并、prompt 约束、
schema 校验、JSON 修复全部封死，智能体只需要传截图路径。
"""

from __future__ import annotations

from pathlib import Path

from ..core.llm.router import ModelRouter
from ..core.schema import Message, TextBlock
from ..domain.schemas import JDCard
from .images import load_image_blocks

JD_SYSTEM_PROMPT = """你是招聘信息结构化引擎。用户会给你一张或多张同一个岗位的详情截图（可能是长截图分段，彼此有重叠）。

工作规则：
1. 逐张读取全部截图，跨截图的相同内容必须合并去重，按逻辑顺序拼成完整岗位信息；
2. 只提取截图中明确出现的信息，严禁根据岗位名臆造要求；截图中没有的字段留空或留 null；
3. 截图模糊、被截断，或多张截图内容相互矛盾时，把涉及的字段名放入 uncertain_fields；
4. skill_tags 使用规范、简短的技术名词（例如 PyTorch、LangChain、RAG、Go、Kubernetes、React），不要句子；
5. hard_requirements 与 nice_to_haves 按 JD 原文的「任职要求/加分项」区分；
6. 严格通过 emit_result 工具提交结果，不要输出多余解释。"""


def parse_jd_screenshots(
    router: ModelRouter,
    image_paths: list[str | Path],
    *,
    task: str = "vision",
    temperature: float = 0.1,
) -> JDCard:
    """读取一张或多张岗位截图，返回结构化 JDCard。

    调用方（UI/智能体）拿到卡片后应展示给用户确认，允许手改后再进入下一步。
    """
    if not image_paths:
        raise ValueError("至少需要一张岗位截图")

    images = load_image_blocks(image_paths)

    user_blocks: list = [
        TextBlock(
            text=(
                f"这是同一个岗位的 {len(images)} 张连续截图（可能含重叠内容）。"
                "请逐张读取、合并去重后，通过工具输出完整的岗位结构化信息。"
            )
        )
    ]
    user_blocks.extend(images)

    messages = [
        Message(role="system", content=JD_SYSTEM_PROMPT),
        Message(role="user", content=user_blocks),
    ]

    result = router.complete_json(
        task,
        messages,
        JDCard.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交岗位信息结构化结果",
        temperature=temperature,
    )
    return JDCard.model_validate(result)
