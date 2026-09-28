"""技能档案的确定性合并规则测试。"""

from coach.domain.schemas import SkillItem, UserSkillProfile
from coach.tools.profile_builder import (
    _ProfileDelta,
    merge_profile_delta,
    normalize_skill_name,
)


def test_normalize_case_insensitive():
    assert normalize_skill_name(" PyTorch ") == "pytorch"
    assert normalize_skill_name("Vue JS") == "vuejs"


def test_merge_same_skill_cross_source():
    profile = UserSkillProfile(
        skills=[
            SkillItem(
                name="PyTorch",
                proficiency="working",
                confidence=0.5,
                sources=["resume"],
                evidence="课设：图像分类",
            )
        ]
    )
    delta = _ProfileDelta(
        skills=[
            SkillItem(
                name="pytorch",
                proficiency="proficient",
                confidence=0.7,
                sources=["work_log"],
                evidence="独立完成模型训练",
            )
        ],
        open_questions=["会分布式训练吗？"],
    )

    merged = merge_profile_delta(profile, delta)

    assert len(merged.skills) == 1
    skill = merged.skills[0]
    assert skill.name == "PyTorch"  # 保留首次写法
    assert skill.proficiency == "proficient"  # 取较高等级
    assert sorted(skill.sources) == ["resume", "work_log"]
    assert skill.confidence == 0.8  # 0.7 + 多来源印证 0.1
    assert "图像分类" in skill.evidence
    assert "独立完成模型训练" in skill.evidence
    assert merged.open_questions == ["会分布式训练吗？"]


def test_merge_adds_new_skill_and_keeps_summary():
    profile = UserSkillProfile(summary="旧摘要", target_direction="后端")
    delta = _ProfileDelta(
        summary="",
        skills=[SkillItem(name="Go", proficiency="working", confidence=0.6, sources=["chat"])],
    )
    merged = merge_profile_delta(profile, delta)
    assert merged.summary == "旧摘要"  # 空摘要不覆盖
    assert merged.target_direction == "后端"
    assert len(merged.skills) == 1
    assert merged.skills[0].name == "Go"


def test_confidence_caps_at_095():
    profile = UserSkillProfile(
        skills=[
            SkillItem(name="RAG", proficiency="expert", confidence=0.92, sources=["resume"])
        ]
    )
    delta = _ProfileDelta(
        skills=[
            SkillItem(name="rag", proficiency="expert", confidence=0.9, sources=["work_log"])
        ]
    )
    merged = merge_profile_delta(profile, delta)
    assert merged.skills[0].confidence == 0.95


def test_open_questions_dedup():
    profile = UserSkillProfile(open_questions=["问题A"])
    delta = _ProfileDelta(open_questions=["问题A", "问题B", "问题B"])
    merged = merge_profile_delta(profile, delta)
    assert merged.open_questions == ["问题A", "问题B"]
