"""工具②：简历 / 工作日志 / 自填文本 → 用户技能档案（增量）。

LLM 只负责从单份材料里"抽取增量"；跨材料的合并是确定性规则，
保证简历写"精通"不会盖过工作日志里的真实证据，且每次运行结果可复现。
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import SkillItem, UserSkillProfile
from ..tools.documents import read_document

_PROFICIENCY_RANK = {"aware": 0, "working": 1, "proficient": 2, "expert": 3}
_RANK_TO_LEVEL = {v: k for k, v in _PROFICIENCY_RANK.items()}

_SOURCE_LABEL = {
    "resume": "简历",
    "work_log": "工作日志",
    "self_report": "技能自填",
    "chat": "访谈对话",
    "obsidian": "Obsidian 笔记",
    "local_path": "本地路径文件",
}


def question_similar(a: str, b: str) -> float:
    """问题文本相似度（0~1），用于识别 LLM 对同一问题的措辞变体。

    完全相等返回 1.0；先算字符级 SequenceMatcher，再以关键词交集兜底，
    覆盖「改写句式但核心词相同」的情况。
    """
    if not a or not b:
        return 0.0
    norm = lambda s: "".join(s.split())
    a_norm, b_norm = norm(a), norm(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    if len(a_norm) <= 6 or len(b_norm) <= 6:
        # 短问题（<=6 字）一两个字的差异占比过高，只有完全相等才算重复
        return 0.0
    ratio = SequenceMatcher(None, a_norm, b_norm).ratio()
    tok_a = set(re.findall(r"[\w\u4e00-\u9fff]+", a_norm))
    tok_b = set(re.findall(r"[\w\u4e00-\u9fff]+", b_norm))
    if not tok_a or not tok_b:
        return ratio
    inter = len(tok_a & tok_b) / min(len(tok_a), len(tok_b))
    return max(ratio, inter)

PROFILE_SYSTEM_PROMPT = """你是用户技能档案分析引擎。根据用户提供的材料（简历、工作日志或技能自填）抽取技能信息。

规则：
1. 只抽取有事实依据的技能，evidence 必须引用材料中的具体经历（项目/任务/成果），不要空泛描述；
2. proficiency 取值 aware（了解）/ working（能上手）/ proficient（熟练）/ expert（精通），从严判断：
   - 简历里的自我夸赞（如"精通"）若无项目事实支撑，最高给 working；
   - 工作日志中反复、独立完成的实战记录，才可给 proficient 及以上；
3. confidence 反映你对掌握程度判断的把握（0~1）：单一声明 0.4~0.6，有具体项目证据 0.6~0.85；
4. 技能名使用规范技术名词（如 PyTorch、RAG、Go、Kubernetes），同一技能只出现一次；
5. open_questions 列出材料中看不出、需要向用户追问的关键问题（最多 5 个），尤其针对求职方向上的核心技能缺口；
6. 已经存在于旧档案中的技能不要重复抽取，只抽取新材料带来的增量信息；
7. 严格通过 emit_result 工具输出。"""


class _ProfileDelta(BaseModel):
    summary: str = ""
    target_direction: str | None = None
    skills: list[SkillItem] = []
    open_questions: list[str] = []


def _delta_schema() -> dict:
    # Pydantic v2 中 list[SkillItem] 可直接作为字段类型
    return _ProfileDelta.model_json_schema()


def build_profile_from_text(
    router: ModelRouter,
    text: str,
    *,
    source: str,
    existing: UserSkillProfile | None = None,
    task: str = "parsing",
) -> UserSkillProfile:
    """从一段材料文本构建/更新技能档案。

    source: resume / work_log / self_report / chat
    """
    if source not in _SOURCE_LABEL:
        raise ValueError(f"未知技能证据来源：{source}")

    existing = existing or UserSkillProfile()
    user_parts = [
        f"材料类型：{_SOURCE_LABEL[source]}",
        f"材料正文：\n{text[:12000]}",
        "",
        "已有技能档案（避免重复抽取）：",
        existing.model_dump_json(),
    ]

    delta_raw = router.complete_json(
        task,
        [
            Message(role="system", content=PROFILE_SYSTEM_PROMPT),
            Message(role="user", content="\n".join(user_parts)),
        ],
        _delta_schema(),
        tool_name="emit_result",
        tool_description="提交从材料中抽取的技能档案增量",
    )
    delta = _ProfileDelta.model_validate(delta_raw)

    # 强制标注来源（不信任模型对 source 的填写）
    for s in delta.skills:
        s.sources = [source]
    return merge_profile_delta(existing, delta)


def build_profile_from_documents(
    router: ModelRouter,
    paths: list[str | Path],
    *,
    source: str,
    existing: UserSkillProfile | None = None,
    task: str = "parsing",
) -> UserSkillProfile:
    """读取一份或多份同类型文档，逐份增量合并。"""
    profile = existing
    for p in paths:
        text = read_document(p)
        profile = build_profile_from_text(
            router, text, source=source, existing=profile, task=task
        )
    assert profile is not None
    return profile


# ── 确定性合并（纯函数，可单测）─────────────────────────────────

def normalize_skill_name(name: str) -> str:
    return name.strip().lower().replace(" ", "")


def merge_profile_delta(
    profile: UserSkillProfile,
    delta: _ProfileDelta,
) -> UserSkillProfile:
    by_key = {normalize_skill_name(s.name): s for s in profile.skills}

    for incoming in delta.skills:
        key = normalize_skill_name(incoming.name)
        current = by_key.get(key)
        if current is None:
            by_key[key] = incoming
            continue

        merged_sources = sorted(set(current.sources) | set(incoming.sources))
        merged_proficiency = _RANK_TO_LEVEL[
            max(
                _PROFICIENCY_RANK[current.proficiency],
                _PROFICIENCY_RANK[incoming.proficiency],
            )
        ]
        # 多来源交叉印证：置信度在最高值基础上 +0.1（封顶 0.95）
        merged_conf = max(current.confidence, incoming.confidence)
        if len(merged_sources) >= 2:
            merged_conf = min(0.95, merged_conf + 0.1)

        evidences = [e for e in (current.evidence, incoming.evidence) if e]
        merged_evidence = " ｜ ".join(dict.fromkeys(evidences))[:500]

        by_key[key] = SkillItem(
            name=current.name,  # 保留首次采用的写法
            proficiency=merged_proficiency,
            confidence=round(merged_conf, 2),
            sources=merged_sources,  # type: ignore[arg-type]
            evidence=merged_evidence,
        )

    questions: list[str] = []
    for q in [*profile.open_questions, *delta.open_questions]:
        if any(question_similar(q, existing) >= 0.6 for existing in questions):
            continue
        questions.append(q)

    return UserSkillProfile(
        user_id=profile.user_id,
        summary=delta.summary or profile.summary,
        target_direction=delta.target_direction or profile.target_direction,
        skills=list(by_key.values()),
        open_questions=questions[:10],
        updated_at=datetime.now(),
    )
