from __future__ import annotations

from pathlib import Path

import pytest

import agent_coach.job_radar as job_radar
from agent_coach.contracts import AgentSession, ToolContext, ToolRegistry
from agent_coach.legacy.domain.schemas import JDCard, LearningPlan, SkillItem, UserSkillProfile
from agent_coach.job_radar import register_job_radar_tools
from agent_coach.tools import PathSecurityError


class _Router:
    def complete_json(self, *_args, **_kwargs):  # pragma: no cover - guard only
        raise AssertionError("job-radar must not invoke a nested model")


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=_Router(),
    )


def _prepare(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch, projects=None):
    projects = projects if projects is not None else []
    seen: list[dict] = []

    def search(_router, queries, _profile, **options):
        seen.append({"queries": list(queries), "options": dict(options)})
        return list(projects)

    monkeypatch.setattr(job_radar, "search_github_projects", search)
    registry = register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]
    registry.get("set_jd", active_skills=active).handler(
        {
            "jd": JDCard(job_title="后端工程师", skill_tags=["Python"]).model_dump(mode="json"),
            "source": {"kind": "text", "evidence": ["用户 JD"]},
        },
        ctx,
    )
    registry.get("set_profile", active_skills=active).handler(
        {
            "profile": UserSkillProfile(
                summary="完成 Python 服务",
                skills=[SkillItem(name="Python", sources=["self_report"], evidence="做过服务")],
            ).model_dump(mode="json"),
            "source": {"kind": "self_report", "evidence": ["用户经历"]},
        },
        ctx,
    )
    registry.get("build_plan", active_skills=active).handler(
        {
            "plan": LearningPlan(github_search_queries=["python http"]).model_dump(mode="json"),
        },
        ctx,
    )
    return registry, active, seen


def test_search_forwards_paper_discovery_and_keeps_signal_unverified(tmp_path: Path, monkeypatch):
    ctx = _context(tmp_path)
    registry, active, seen = _prepare(ctx, monkeypatch)
    result = registry.get("search_projects", active_skills=active).handler({}, ctx)

    assert seen[0]["queries"] == ["python http"]
    assert seen[0]["options"]["paper_queries"] == ["python http paper implementation in:readme"]
    assert seen[0]["options"]["paper_recency_days"] >= 3650
    assert result.data["paper_queries"] == ["python http paper implementation in:readme"]


def test_legacy_paper_channel_marks_only_discovery_signal(monkeypatch):
    from agent_coach.legacy.tools import github_search

    calls: list[tuple] = []

    def collect(queries, min_stars, recency_days):
        calls.append((list(queries), min_stars, recency_days))
        return [
            {
                "full_name": "demo/paper",
                "html_url": "https://github.com/demo/paper",
                "description": "paper implementation",
                "language": "Python",
                "stargazers_count": 1,
                "updated_at": "",
                "det_score": 0.5,
                "source_verified": True,
                "source_paths": ["src/main.py"],
                "source_evidence": ["https://github.com/demo/paper/blob/main/src/main.py"],
                "code_verified": True,
                "code_paths": ["src/main.py"],
                "code_evidence": ["https://github.com/demo/paper/blob/main/src/main.py"],
            }
        ]

    monkeypatch.setattr(github_search, "_collect_candidates", collect)
    repos = github_search.search_github_projects(
        None,
        ["python"],
        llm_rerank=False,
        paper_queries=["python paper implementation"],
        recency_days=30,
    )
    assert len(calls) == 2
    assert calls[0][2] == 30 and calls[1][2] >= 3650
    assert repos[0].paper_candidate is True
    assert repos[0].paper_verified is False
    assert repos[0].venue is None and repos[0].citation_count is None
    assert "核验" in repos[0].reason


def test_user_project_has_stable_source_key_and_survives_search_refresh(tmp_path: Path, monkeypatch):
    material = tmp_path / "imported" / "main.py"
    material.parent.mkdir()
    material.write_text("print('my reproduction')", encoding="utf-8")
    ctx = _context(tmp_path)
    registry, active, _seen = _prepare(ctx, monkeypatch)
    register = registry.get("register_project", active_skills=active).handler
    first = register(
        {
            "project": {
                "name": "我的复现",
                "description": "用户上传的复现项目",
                "source": {
                    "kind": "local_import",
                    "evidence": ["主体已读取 imported/main.py"],
                    "paths": ["imported/main.py"],
                },
            }
        },
        ctx,
    )
    first_project = first.data["project"]
    assert first_project["project_key"].startswith("user:")
    assert first_project["source"]["user_confirmed"] is False
    assert ctx.session.skill_state["job-radar"]["repos"][-1]["project_key"] == first_project["project_key"]

    # Re-registering the same source with a different display name updates the
    # same durable identity rather than colliding on the title.
    second = register(
        {
            "project": {
                "name": "另一个显示名",
                "source": {
                    "kind": "local_import",
                    "evidence": ["主体已再次读取 imported/main.py"],
                    "paths": ["imported/main.py"],
                },
            }
        },
        ctx,
    )
    assert second.data["project"]["project_key"] == first_project["project_key"]
    assert len(ctx.session.skill_state["job-radar"]["user_projects"]) == 1

    registry.get("search_projects", active_skills=active).handler({}, ctx)
    state = ctx.session.skill_state["job-radar"]
    assert any(item.get("project_key") == first_project["project_key"] for item in state["repos"])
    registry.get("select_project", active_skills=active).handler(
        {"project_key": first_project["project_key"]}, ctx
    )
    assert state["selected_project"]["project_key"] == first_project["project_key"]


def test_user_project_paper_claim_requires_explicit_evidence(tmp_path: Path, monkeypatch):
    from agent_coach.legacy.tools import github_search

    monkeypatch.setattr(
        github_search,
        "verify_github_repository_source",
        lambda full_name: {
            "verified": True,
            "private": False,
            "branch": "main",
            "paths": ["src/main.py"],
            "evidence": [f"https://github.com/{full_name}/blob/main/src/main.py"],
        },
    )
    ctx = _context(tmp_path)
    registry, active, _seen = _prepare(ctx, monkeypatch)
    register = registry.get("register_project", active_skills=active).handler
    with pytest.raises(ValueError, match="paper.evidence"):
        register(
            {
                "project": {
                    "name": "paper impl",
                    "url": "https://example.org/paper-impl",
                    "source": {"kind": "user_url", "url": "https://example.org/paper-impl", "evidence": ["用户 URL"]},
                    "paper": {"venue": "某会议"},
                }
            },
            ctx,
        )

    ok = register(
        {
            "project": {
                "name": "paper impl",
                "url": "https://github.com/owner/paper-impl",
                "source": {"kind": "paper_implementation", "url": "https://github.com/owner/paper-impl", "evidence": ["README"]},
                "paper": {
                    "title": "A paper",
                    "url": "https://example.org/paper",
                    "venue": "某会议",
                    "citation_count": 12,
                    "evidence": ["https://example.org/paper"],
                },
            }
        },
        ctx,
    )
    assert ok.data["project"]["paper_verified"] is True
    assert ok.data["project"]["citation_count"] == 12

    # Evidence text by itself does not turn a generic project into a verified
    # paper implementation.
    with pytest.raises(ValueError, match="无法核验项目源码"):
        register(
            {
                "project": {
                    "name": "unidentified implementation",
                    "source": {"kind": "paper_implementation", "evidence": ["README says paper"]},
                    "paper": {"evidence": ["README says paper"]},
                }
            },
            ctx,
        )


def test_user_project_rejects_insecure_url_and_outside_path(tmp_path: Path, monkeypatch):
    ctx = _context(tmp_path)
    registry, active, _seen = _prepare(ctx, monkeypatch)
    register = registry.get("register_project", active_skills=active).handler
    with pytest.raises(ValueError, match="HTTPS"):
        register(
            {
                "project": {
                    "name": "unsafe",
                    "url": "http://example.org/project",
                    "source": {"kind": "user_url", "url": "http://example.org/project", "evidence": ["URL"]},
                }
            },
            ctx,
        )
    with pytest.raises(ValueError, match="凭据"):
        register(
            {
                "project": {
                    "name": "credential query",
                    "url": "https://example.org/project?ToKeN=secret",
                    "source": {
                        "kind": "user_url",
                        "url": "https://example.org/project?ToKeN=secret",
                        "evidence": ["URL"],
                    },
                }
            },
            ctx,
        )
    with pytest.raises(ValueError, match="IPv6"):
        register(
            {
                "project": {
                    "name": "ipv6",
                    "url": "https://[::1]/project",
                    "source": {"kind": "user_url", "url": "https://[::1]/project", "evidence": ["URL"]},
                }
            },
            ctx,
        )
    with pytest.raises((PermissionError, ValueError, PathSecurityError)):
        register(
            {
                "project": {
                    "name": "outside",
                    "source": {"kind": "local_import", "evidence": ["outside"], "paths": [str(tmp_path.parent / "outside.md")]},
                }
            },
            ctx,
        )


def test_local_project_source_paths_are_relative_and_identity_is_portable(tmp_path: Path, monkeypatch):
    first_root = tmp_path / "workspace-a"
    second_root = tmp_path / "workspace-b"
    for root in (first_root, second_root):
        material = root / "imports" / "main.py"
        material.parent.mkdir(parents=True)
        material.write_text("print('same imported layout')", encoding="utf-8")

    first_ctx = _context(first_root)
    first_registry, active, _seen = _prepare(first_ctx, monkeypatch)
    first = first_registry.get("register_project", active_skills=active).handler(
        {
            "project": {
                "name": "local reproduction",
                "source": {
                    "kind": "local_import",
                    "evidence": ["imports/main.py inspected"],
                    "paths": ["imports/main.py"],
                },
            }
        },
        first_ctx,
    )

    second_ctx = _context(second_root)
    second_registry, active, _seen = _prepare(second_ctx, monkeypatch)
    second = second_registry.get("register_project", active_skills=active).handler(
        {
            "project": {
                "name": "same project renamed",
                "source": {
                    "kind": "local_import",
                    "evidence": ["imports/main.py inspected again"],
                    "paths": ["imports/main.py"],
                },
            }
        },
        second_ctx,
    )

    first_project = first.data["project"]
    second_project = second.data["project"]
    assert first_project["source"]["paths"] == ["imports/main.py"]
    assert second_project["source"]["paths"] == ["imports/main.py"]
    assert first_project["project_key"] == second_project["project_key"]
