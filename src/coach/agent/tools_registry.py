"""智能体工具注册表：把确定性工具包装成 LLM 可调用的 function，共享会话状态。

每个工具：名称 + JSON Schema + handler。handler 返回字符串作为 tool 结果；
校验失败不抛异常给模型，而是返回 {"error": ...} 让模型自行纠正或向用户追问。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..core.schema import ToolSpec
from ..tools.interviewer import apply_interview_answer, next_interview_question
from ..tools.jd_parser import parse_jd_screenshots
from ..tools.pipeline import plan_and_recommend
from ..tools.profile_builder import build_profile_from_documents, build_profile_from_text
from ..tools.session import SessionState
from ..core.llm.router import ModelRouter
from ..domain.schemas import UserSkillProfile

ToolHandler = Callable[[dict], str]


@dataclass
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class CoachToolRegistry:
    def __init__(self, router: ModelRouter, state: SessionState):
        self.router = router
        self.state = state
        self.pending_question: str | None = None
        self._tools = self._build()

    def __getitem__(self, name: str) -> RegisteredTool:
        return self._tools[name]

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    # ── handlers ───────────────────────────────────────────────

    def _parse_jd(self, args: dict) -> str:
        paths = args.get("image_paths") or []
        if not paths:
            return json.dumps({"error": "image_paths 不能为空"}, ensure_ascii=False)
        card = parse_jd_screenshots(self.router, paths)
        self.state.jd = card
        return card.model_dump_json()

    def _ingest_docs(self, args: dict) -> str:
        paths = args.get("paths") or []
        source = args.get("source", "resume")
        if not paths:
            return json.dumps({"error": "paths 不能为空"}, ensure_ascii=False)
        profile = build_profile_from_documents(
            self.router, paths, source=source, existing=self.state.profile
        )
        self.state.profile = profile
        return _profile_brief(profile)

    def _self_skills(self, args: dict) -> str:
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"error": "text 不能为空"}, ensure_ascii=False)
        profile = build_profile_from_text(
            self.router, text, source="self_report", existing=self.state.profile
        )
        self.state.profile = profile
        return _profile_brief(profile)

    def _next_question(self, args: dict) -> str:
        if self.state.profile is None:
            self.state.profile = UserSkillProfile()
        q = next_interview_question(
            self.router, self.state.profile, self.state.history_pairs()
        )
        self.pending_question = q
        if q is None:
            return json.dumps({"done": True, "question": None}, ensure_ascii=False)
        return json.dumps({"done": False, "question": q}, ensure_ascii=False)

    def _answer(self, args: dict) -> str:
        answer = (args.get("answer") or "").strip()
        if not answer:
            return json.dumps({"error": "answer 不能为空"}, ensure_ascii=False)
        if self.pending_question is None:
            return json.dumps(
                {"error": "没有进行中的问题，请先调用 start_interview"},
                ensure_ascii=False,
            )
        assert self.state.profile is not None
        self.state.profile = apply_interview_answer(
            self.router,
            self.state.profile,
            self.pending_question,
            answer,
        )
        self.state.interview_history.append([self.pending_question, answer])
        self.pending_question = None
        return _profile_brief(self.state.profile)

    def _make_plan(self, args: dict) -> str:
        if self.state.jd is None:
            return json.dumps({"error": "还没有岗位信息，请先解析岗位截图"}, ensure_ascii=False)
        if self.state.profile is None or not self.state.profile.skills:
            return json.dumps(
                {"error": "技能档案为空，请先上传简历/自填技能/完成访谈"},
                ensure_ascii=False,
            )
        plan, repos = plan_and_recommend(
            self.router,
            self.state.jd,
            self.state.profile,
            min_stars=int(args.get("min_stars", 50)),
            llm_rerank=bool(args.get("llm_rerank", True)),
        )
        self.state.plan = plan
        self.state.repos = repos
        return json.dumps(
            {
                "plan": plan.model_dump(),
                "github_projects": [r.model_dump() for r in repos],
            },
            ensure_ascii=False,
        )

    def _status(self, args: dict) -> str:
        return json.dumps(
            {
                "has_jd": self.state.jd is not None,
                "job_title": self.state.jd.job_title if self.state.jd else None,
                "company": self.state.jd.company if self.state.jd else None,
                "profile_skills": len(self.state.profile.skills)
                if self.state.profile
                else 0,
                "interview_rounds": len(self.state.interview_history),
                "has_plan": self.state.plan is not None,
                "github_projects": len(self.state.repos),
            },
            ensure_ascii=False,
        )

    # ── 注册 ───────────────────────────────────────────────────

    def _build(self) -> dict[str, RegisteredTool]:
        def reg(name: str, description: str, parameters: dict, handler: ToolHandler):
            return RegisteredTool(
                spec=ToolSpec(name=name, description=description, parameters=parameters),
                handler=handler,
            )

        return {
            t.spec.name: t
            for t in [
                reg(
                    "parse_jd_screenshots",
                    "解析一张或多张招聘岗位截图，生成结构化岗位卡片。参数为本地图片路径列表。",
                    {
                        "type": "object",
                        "properties": {
                            "image_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "岗位截图的本地文件路径，长截图可分多张",
                            }
                        },
                        "required": ["image_paths"],
                    },
                    self._parse_jd,
                ),
                reg(
                    "ingest_skill_documents",
                    "读取用户的简历或工作日志文件（txt/md/pdf），增量更新技能档案。",
                    {
                        "type": "object",
                        "properties": {
                            "paths": {"type": "array", "items": {"type": "string"}},
                            "source": {
                                "type": "string",
                                "enum": ["resume", "work_log"],
                                "description": "resume=简历，work_log=工作日志",
                            },
                        },
                        "required": ["paths", "source"],
                    },
                    self._ingest_docs,
                ),
                reg(
                    "record_self_reported_skills",
                    "用户用一段话自述掌握的技能与项目经历，增量更新技能档案。",
                    {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                    self._self_skills,
                ),
                reg(
                    "start_interview",
                    "生成下一个技能访谈问题。每次只问一个，直到返回 done=true。",
                    {"type": "object", "properties": {}},
                    self._next_question,
                ),
                reg(
                    "answer_interview_question",
                    "回传用户对当前访谈问题的回答，系统抽取技能并更新档案。",
                    {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                    self._answer,
                ),
                reg(
                    "generate_learning_plan",
                    "基于已确认的岗位卡片和技能档案，生成差距分析、学习计划并检索 GitHub 复现项目。",
                    {
                        "type": "object",
                        "properties": {
                            "min_stars": {
                                "type": "integer",
                                "description": "GitHub 项目最低 star 数，默认 50",
                            },
                            "llm_rerank": {
                                "type": "boolean",
                                "description": "是否让 LLM 读 README 重排，默认 true",
                            },
                        },
                    },
                    self._make_plan,
                ),
                reg(
                    "get_current_status",
                    "查看当前会话进度（岗位、档案、访谈轮数、计划、项目数）。",
                    {"type": "object", "properties": {}},
                    self._status,
                ),
            ]
        }


def _profile_brief(profile: UserSkillProfile) -> str:
    return json.dumps(
        {
            "summary": profile.summary,
            "target_direction": profile.target_direction,
            "skill_count": len(profile.skills),
            "skills": [
                {
                    "name": s.name,
                    "proficiency": s.proficiency,
                    "confidence": s.confidence,
                    "sources": s.sources,
                }
                for s in profile.skills
            ],
            "open_questions": profile.open_questions,
        },
        ensure_ascii=False,
    )
