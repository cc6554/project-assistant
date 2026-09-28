"""真实 API 验证：planning 环节（deepseek-v4-pro，thinking 模式）。"""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from coach.core.llm.router import ModelRouter
from coach.domain.schemas import JDCard, SkillItem, UserSkillProfile
from coach.tools.planner import build_learning_plan

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv(ROOT / ".env")
    router = ModelRouter.from_yaml(ROOT / "config" / "providers.yaml")

    jd = JDCard(
        job_title="大模型应用开发工程师",
        company="深圳智链科技",
        hard_requirements=[
            "熟练 Python，熟悉 PyTorch",
            "有 RAG 或 Agent 项目落地经验",
            "熟悉向量数据库 Milvus/Faiss",
        ],
        skill_tags=["Python", "PyTorch", "RAG", "Agent", "Milvus"],
    )
    profile = UserSkillProfile(
        target_direction="大模型应用开发",
        summary="计算机应届生，用过 PyTorch 做课设，没有完整大模型项目",
        skills=[
            SkillItem(name="Python", proficiency="proficient", confidence=0.8, sources=["resume", "chat"]),
            SkillItem(name="PyTorch", proficiency="working", confidence=0.55, sources=["resume"],
                      evidence="课设做过图像分类"),
        ],
    )

    plan = build_learning_plan(router, jd, profile)
    print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
