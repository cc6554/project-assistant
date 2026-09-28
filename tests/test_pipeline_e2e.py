"""用假 LLM 端到端验证：截图解析、档案构建、计划生成、agent 调度与工具分发。

不访问网络：用 StubRouter 分别接管 ``complete``（agent 调度）与
``complete_json``（各工具内部调用），返回预置数据。
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from coach.agent.loop import CoachAgent
from coach.core.schema import LLMResponse, ToolCall
from coach.domain.schemas import SkillItem, UserSkillProfile
from coach.tools.jd_parser import parse_jd_screenshots
from coach.tools.planner import build_learning_plan
from coach.tools.profile_builder import build_profile_from_text
from coach.tools.session import SessionState


class StubRouter:
    """complete / complete_json 各自消费独立脚本。"""

    def __init__(self, chat_script=None, json_scripts=None):
        self.chat_script = list(chat_script or [])
        self.json_scripts = json_scripts or {}
        self.calls = []

    def complete(self, task, messages, **kwargs):
        self.calls.append(("complete", task))
        if not self.chat_script:
            return LLMResponse(text="（脚本已耗尽）")
        item = self.chat_script.pop(0)
        return item

    def complete_json(self, task, messages, schema, **kwargs):
        self.calls.append(("complete_json", task))
        return self.json_scripts[task].pop(0)


@pytest.fixture
def tiny_png(tmp_path):
    p = tmp_path / "shot.png"
    Image.new("RGB", (8, 8), (20, 30, 40)).save(p)
    return str(p)


JD_DICT = {
    "job_title": "大模型算法工程师",
    "company": "测试科技",
    "salary": "25-50K·15薪",
    "location": "北京",
    "hard_requirements": ["熟悉 PyTorch", "有 RAG 项目经验"],
    "skill_tags": ["PyTorch", "RAG"],
    "nice_to_haves": ["有顶会论文"],
    "uncertain_fields": [],
}


def test_parse_jd_screenshots_tool(tiny_png):
    router = StubRouter(json_scripts={"vision": [dict(JD_DICT)]})
    card = parse_jd_screenshots(router, [tiny_png])
    assert card.job_title == "大模型算法工程师"
    assert card.skill_tags == ["PyTorch", "RAG"]
    assert ("complete_json", "vision") in router.calls


def test_profile_build_from_text():
    delta = {
        "summary": "会 PyTorch 的应届生",
        "target_direction": "大模型算法",
        "skills": [
            {
                "name": "PyTorch",
                "proficiency": "working",
                "confidence": 0.55,
                "sources": ["resume"],
                "evidence": "课设",
            }
        ],
        "open_questions": ["做过完整训练流程吗？"],
    }
    router = StubRouter(json_scripts={"parsing": [delta]})
    profile = build_profile_from_text(router, "我会 pytorch", source="self_report")
    assert profile.target_direction == "大模型算法"
    assert profile.skills[0].sources == ["self_report"]  # 来源被强制覆盖


def test_planner_tool():
    from coach.domain.schemas import JDCard

    jd = JDCard(**JD_DICT)
    profile = UserSkillProfile(
        skills=[SkillItem(name="PyTorch", proficiency="working", confidence=0.6, sources=["resume"])]
    )
    plan_dict = {
        "direction": "大模型算法",
        "gaps": [
            {"requirement": "熟悉 PyTorch", "matched_skill": "PyTorch",
             "status": "weak", "note": "深度不足"}
        ],
        "phases": [
            {"phase": "第一阶段", "goal": "补 RAG", "topics": ["检索增强"],
             "suggested_practice": "复现一个 RAG demo", "duration_weeks": 2}
        ],
        "github_search_queries": ["RAG implementation langchain"],
    }
    router = StubRouter(json_scripts={"planning": [plan_dict]})
    plan = build_learning_plan(router, jd, profile)
    assert plan.gaps[0].status == "weak"
    assert plan.github_search_queries == ["RAG implementation langchain"]


def test_agent_loop_dispatches_tool(tiny_png):
    chat_script = [
        LLMResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="parse_jd_screenshots",
                    arguments={"image_paths": [tiny_png]},
                )
            ],
        ),
        LLMResponse(text="岗位识别完成：大模型算法工程师，请确认。"),
    ]
    router = StubRouter(chat_script=chat_script, json_scripts={"vision": [dict(JD_DICT)]})
    state = SessionState()
    agent = CoachAgent(router, state)

    reply = agent.chat("帮我识别这两张图 " + tiny_png)

    assert "大模型算法工程师" in reply
    assert state.jd is not None
    assert state.jd.company == "测试科技"
    # 消息历史：system + user + assistant(tool_call) + tool result + assistant
    roles = [m.role for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    tool_msg = agent.messages[3]
    assert tool_msg.tool_call_id == "call_1"
    assert json.loads(tool_msg.content)["job_title"] == "大模型算法工程师"


def test_registry_reports_precondition_errors():
    router = StubRouter()
    state = SessionState()
    agent = CoachAgent(router, state)

    # 没有 JD → 错误回灌
    result = json.loads(agent.registry["generate_learning_plan"].handler({}))
    assert "岗位" in result["error"]

    # 有 JD 但没技能 → 仍然报错
    from coach.domain.schemas import JDCard

    state.jd = JDCard(**JD_DICT)
    result = json.loads(agent.registry["generate_learning_plan"].handler({}))
    assert "技能档案" in result["error"]
