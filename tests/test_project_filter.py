from __future__ import annotations

from pathlib import Path

import pytest

from agent_coach import job_radar
from agent_coach.contracts import AgentSession, ToolContext, ToolRegistry


def _context(tmp_path: Path) -> ToolContext:
    session = AgentSession(workspace=str(tmp_path))
    return ToolContext(workspace=tmp_path, session=session, router=None)


def test_project_filter_normalizes_defaults_and_rejects_invalid_values() -> None:
    assert job_radar.normalize_project_filter(None) == {
        "min_stars": 50,
        "include": [],
        "exclude": [],
        "other": "",
        "paper_projects": True,
        "rerank": True,
    }
    assert job_radar.normalize_project_filter(
        {
            "min_stars": 120,
            "include": ["  Python ", "python", "检索"],
            "exclude": [" Isaac "],
            "other": "  需要可复现实验  ",
            "paper_projects": False,
            "rerank": False,
        }
    ) == {
        "min_stars": 120,
        "include": ["Python", "检索"],
        "exclude": ["Isaac"],
        "other": "需要可复现实验",
        "paper_projects": False,
        "rerank": False,
    }
    with pytest.raises(ValueError, match="min_stars"):
        job_radar.normalize_project_filter({"min_stars": True})
    with pytest.raises(ValueError, match="include"):
        job_radar.normalize_project_filter({"include": "Python"})
    with pytest.raises(ValueError, match="未知字段"):
        job_radar.normalize_project_filter({"unexpected": True})


def test_set_project_filter_is_a_tool_and_survives_public_model_context(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    registry = job_radar.register_job_radar_tools(ToolRegistry())
    tool = registry.get("set_project_filter", active_skills=["job-radar"])

    result = tool.handler(
        {
            "project_filter": {
                "min_stars": 88,
                "include": ["Python"],
                "exclude": ["demo"],
                "other": "需要真实来源",
                "paper_projects": False,
                "rerank": True,
            }
        },
        ctx,
    )
    expected = {
        "min_stars": 88,
        "include": ["Python"],
        "exclude": ["demo"],
        "other": "需要真实来源",
        "paper_projects": False,
        "rerank": True,
    }
    assert result.data["project_filter"] == expected
    assert ctx.session.skill_state["job-radar"]["project_filter"] == expected
    assert job_radar.workflow_state(ctx.session)["project_filter"] == expected
    assert job_radar.model_context(ctx.session)["project_filter"] == expected
    assert tool.spec.parameters["properties"]["project_filter"]["properties"]["min_stars"]["type"] == "integer"


def test_search_projects_inherits_saved_user_constraints(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    def fake_search(_router, _queries, _profile, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(job_radar, "search_github_projects", fake_search)
    ctx = _context(tmp_path)
    state = job_radar.ensure_state(ctx.session)
    state["jd"] = {"job_title": "Python 工程师"}
    state["profile"] = {"summary": "有 Python 项目经验", "skills": []}
    state["plan"] = {
        "direction": "Backend",
        "gaps": [],
        "phases": [],
        "github_search_queries": ["python http"],
    }
    job_radar.set_project_filter(
        {
            "project_filter": {
                "min_stars": 75,
                "include": ["Python"],
                "exclude": ["demo"],
                "other": "需要真实来源",
                "paper_projects": False,
                "rerank": True,
            }
        },
        ctx,
    )

    job_radar.search_projects(
        {
            "min_stars": 10,
            "preferences": {"include": ["RAG"], "exclude": ["实验"]},
            "include_paper_implementations": True,
        },
        ctx,
    )

    assert captured["min_stars"] == 75
    assert captured["preferences"] == {
        "include": ["Python", "RAG"],
        "exclude": ["demo", "实验"],
    }
    assert captured["paper_queries"] == []
