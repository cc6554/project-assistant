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


def test_merge_remove_skills():
    """merge_profile_delta 支持 remove_skills：用户明确否定的技能从档案移除。"""
    from coach.domain.schemas import SkillItem, UserSkillProfile
    from coach.tools.profile_builder import _ProfileDelta, merge_profile_delta

    base = UserSkillProfile(
        skills=[
            SkillItem(name="Apache ORC", proficiency="working", confidence=0.6, sources=["chat"], evidence="学习笔记"),
            SkillItem(name="JSON", proficiency="working", confidence=0.6, sources=["chat"], evidence="解析工具"),
            SkillItem(name="PyTorch", proficiency="proficient", confidence=0.8, sources=["resume"], evidence="训练"),
        ]
    )
    delta = _ProfileDelta(
        remove_skills=["Apache ORC", "json"],
        skills=[SkillItem(name="PyTorch", proficiency="expert", confidence=0.9, sources=["chat"], evidence="新增证据")],
    )
    out = merge_profile_delta(base, delta)
    names = {s.name for s in out.skills}
    assert "Apache ORC" not in names and "JSON" not in names
    assert "PyTorch" in names
    pt = next(s for s in out.skills if s.name == "PyTorch")
    assert pt.proficiency == "expert" and pt.confidence >= 0.8


def test_merge_remove_questions():
    """merge_profile_delta 支持 remove_questions：已澄清问题（含措辞变体）从清单移除。"""
    from coach.domain.schemas import SkillItem, UserSkillProfile
    from coach.tools.profile_builder import _ProfileDelta, merge_profile_delta

    base = UserSkillProfile(
        skills=[SkillItem(name="PyTorch", proficiency="working", confidence=0.5, sources=["resume"], evidence="")],
        open_questions=[
            "你之前跑过 153 万步的 Isaac Gym + PPO 训练吗？用的什么框架？",
            "DDPG 是你自研实现还是用的现成库？",
            "抓取卡点是什么？",
        ],
    )
    delta = _ProfileDelta(
        remove_questions=["你之前跑过 153 万步的 Isaac Gym + PPO 训练吗？用的什么框架？", "DDPG 是你自研实现还是用的现成库？"],
        open_questions=[],
    )
    out = merge_profile_delta(base, delta)
    assert len(out.open_questions) == 1
    assert "卡点" in out.open_questions[0]

def test_merge_remove_questions_variant():
    """措辞变体也能被 remove_questions 移除（模糊匹配）。"""
    from coach.domain.schemas import SkillItem, UserSkillProfile
    from coach.tools.profile_builder import _ProfileDelta, merge_profile_delta

    base = UserSkillProfile(
        skills=[SkillItem(name="PyTorch", proficiency="working", confidence=0.5, sources=["resume"], evidence="")],
        open_questions=["你用的哪个 RL 库（Stable-Baselines3 / Ray RLlib / rl_games）？"],
    )
    delta = _ProfileDelta(remove_questions=["你用的是哪个 RL 库（Stable-Baselines3 / Ray RLlib / rl_games）"])
    out = merge_profile_delta(base, delta)
    assert out.open_questions == []
