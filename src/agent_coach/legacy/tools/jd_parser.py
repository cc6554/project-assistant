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

JD_TEXT_PROMPT = """你是招聘信息结构化引擎。下面是围绕一个目标岗位名称检索到的若干条独立招聘样本（标题：摘要，可能来自不同公司、平台或渠道）。不同公司不代表岗位不可比较；只有证据明确显示为不同职类、专业方向或层级时才排除样本。

工作规则：
1. 逐条阅读全部样本并去重；hard_requirements、skill_tags、responsibilities 和 nice_to_haves 只写至少两个独立样本明确支持的共同要求；
2. 只提取文本中明确出现的信息，严禁根据岗位名臆造要求；文本中没有的字段留空或留 null；
3. 只出现在一个样本中的要求写入 raw_summary 的单样本差异，不放入共同字段；文本不完整或互相矛盾时，把涉及的字段名放入 uncertain_fields；
4. 样本少于 3 条时，sample_target 设为 3，sample_count 设为实际可读条数，comparison_status 设为 insufficient_samples，明确说明暂不足形成共性；达到 3 条时设为 compared，并在 raw_summary 标明每项共同要求的支持样本数/总数和样本编号；单条材料使用 single_source；
5. 若这些文本不足以判断具体岗位（比如是聚合页、无关内容），job_title 留空并在 uncertain_fields 标注；
6. skill_tags 使用规范、简短的技术名词（例如 PyTorch、LangChain、RAG、Go、Kubernetes、React），不要句子；
7. 严格通过 emit_result 工具提交结果，不要输出多余解释。"""


def parse_jd_text(
    router: ModelRouter,
    texts: list[str],
    *,
    task: str = "parsing",
    temperature: float = 0.1,
) -> JDCard:
    """从网络搜索到的 JD 文本列表解析出 JDCard（无视觉，纯文本）。"""
    if not texts:
        raise ValueError("至少需要一条岗位文本")

    # The old search-snippet entry point silently kept only 600 characters.
    # This skill also accepts full user-provided JDs, including requirements
    # near the end. Bound the request explicitly instead of losing evidence.
    if any(not isinstance(t, str) for t in texts):
        raise ValueError("岗位材料必须是文本")
    if sum(len(t) for t in texts) > 64000:
        raise ValueError("岗位材料超过 64000 字符，请先拆分或精简后再解析")
    user_content = "\n\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts))
    result = router.complete_json(
        task,
        [
            Message(role="system", content=JD_TEXT_PROMPT),
            Message(
                role="user",
                content=(
                    "以下是同一个目标岗位的独立招聘样本（不同公司可以是可比较样本）：\n\n"
                    f"{user_content}\n\n请按样本交叉核对后，通过工具输出完整的岗位结构化信息。"
                ),
            ),
        ],
        JDCard.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交岗位信息结构化结果",
        temperature=temperature,
    )
    return JDCard.model_validate(result)


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
