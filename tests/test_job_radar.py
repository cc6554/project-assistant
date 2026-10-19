from __future__ import annotations

from pathlib import Path

import pytest

from agent_coach.contracts import AgentSession, PendingApproval, ToolContext, ToolRegistry
from agent_coach.job_radar import register_job_radar_tools
import agent_coach.job_radar as job_radar
from agent_coach.legacy.domain.schemas import (
    GitHubRepo,
    JDCard,
    LearningPhase,
    LearningPlan,
    SkillGap,
    SkillItem,
    UserSkillProfile,
)
from agent_coach.store import SessionStore


class NoStageRouter:
    """A guard proving job-radar never asks a nested model to build artifacts."""

    def __init__(self):
        self.complete_json_calls: list[tuple] = []

    def complete_json(self, *args, **kwargs):
        self.complete_json_calls.append((args, kwargs))
        raise AssertionError("job-radar tools must not call complete_json")


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=NoStageRouter(),
    )


def jd_artifact(title: str = "后端工程师") -> dict:
    return JDCard(
        job_title=title,
        hard_requirements=["Python", "HTTP"],
        skill_tags=["Python", "FastAPI"],
        raw_summary="用户提供的岗位正文摘要",
    ).model_dump(mode="json")


def profile_artifact(summary: str = "完成过 Python 服务") -> dict:
    return UserSkillProfile(
        summary=summary,
        target_direction="后端开发",
        skills=[
            SkillItem(
                name="Python",
                proficiency="working",
                confidence=0.7,
                sources=["self_report"],
                evidence="独立实现过 CSV 解析和 HTTP 服务",
            )
        ],
        open_questions=["是否有上线证据？"],
    ).model_dump(mode="json")


def plan_artifact() -> dict:
    return LearningPlan(
        direction="后端开发",
        gaps=[SkillGap(requirement="HTTP", status="missing", note="需要补充服务端实践")],
        phases=[
            LearningPhase(
                phase="第一阶段：HTTP 基础",
                goal="完成一个可测试的 HTTP 服务",
                topics=["路由", "测试"],
                suggested_practice="实现并测试一个 FastAPI 服务",
                duration_weeks=1,
            )
        ],
        github_search_queries=["python http implementation project"],
    ).model_dump(mode="json")


def verified_repo(full_name: str) -> GitHubRepo:
    """Build a search fixture with the same provenance real candidates carry."""

    source_path = "src/main.py"
    evidence = f"https://github.com/{full_name}/blob/main/{source_path}"
    return GitHubRepo(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        source_verified=True,
        source_paths=[source_path],
        source_evidence=[evidence],
        code_verified=True,
        code_paths=[source_path],
        code_evidence=[evidence],
    )


def test_registry_gates_and_runs_structured_single_agent_workflow(monkeypatch, tmp_path: Path):
    calls: list[dict] = []

    def deterministic_search(router, queries, profile, **kwargs):
        calls.append({"router": router, "queries": queries, "profile": profile, "kwargs": kwargs})
        assert router is None
        assert kwargs["llm_rerank"] is False
        return [verified_repo("demo/project")]

    monkeypatch.setattr(job_radar, "search_github_projects", deterministic_search)
    registry = register_job_radar_tools(ToolRegistry())
    ctx = context(tmp_path)

    with pytest.raises(PermissionError):
        registry.get("job_radar_status", active_skills=[])
    status = registry.get("job_radar_status", active_skills=["job-radar"]).handler({}, ctx)
    assert status.data["state"]["jd"] is None

    active = ["job-radar"]
    set_jd = registry.get("set_jd", active_skills=active).handler
    set_profile = registry.get("set_profile", active_skills=active).handler
    build_plan = registry.get("build_plan", active_skills=active).handler
    search = registry.get("search_projects", active_skills=active).handler
    select = registry.get("select_project", active_skills=active).handler
    progress = registry.get("record_progress", active_skills=active).handler

    set_jd(
        {
            "jd": jd_artifact(),
            "source": {"kind": "text", "evidence": ["user supplied JD"]},
        },
        ctx,
    )
    set_profile(
        {
            "profile": profile_artifact(),
            "source": {"kind": "self_report", "evidence": ["conversation answer"]},
        },
        ctx,
    )
    build_plan({"plan": plan_artifact()}, ctx)
    search({}, ctx)
    select({"full_name": "demo/project"}, ctx)
    progress({"note": "完成安装", "evidence": "pytest 12 passed"}, ctx)

    pending = progress(
        {
            "note": "复现并通过测试",
            "evidence": "pytest 12 passed",
            "completed": True,
            "user_confirmed": True,
        },
        ctx,
    )
    assert isinstance(pending, PendingApproval)
    assert all(value in pending.preview for value in ("demo/project", "复现并通过测试", "pytest 12 passed"))
    registry.get("record_progress", active_skills=active).execute(pending, ctx)

    state = ctx.session.skill_state["job-radar"]
    assert state["jd"] and state["profile"] and state["plan"]
    assert state["repos"] and state["selected_project"]
    assert state["progress"][-1]["status"] == "completed"
    assert state["progress"][-1]["user_confirmed"] is True
    assert state["jd_source"]["user_confirmed"] is False
    assert state["profile_source"]["user_confirmed"] is False
    assert calls and calls[0]["kwargs"]["llm_rerank"] is False
    assert ctx.router.complete_json_calls == []


def test_artifact_validation_is_atomic_and_changes_invalidate_downstream(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        job_radar,
        "search_github_projects",
        lambda *_args, **_kwargs: [verified_repo("demo/project")],
    )
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]

    def call(name, args):
        return registry.get(name, active_skills=active).handler(args, ctx)

    call("set_jd", {"jd": jd_artifact(), "source": {"kind": "text", "evidence": ["chat"]}})
    call(
        "set_profile",
        {
            "profile": profile_artifact(),
            "source": {"kind": "self_report", "evidence": ["chat"]},
        },
    )
    call("build_plan", {"plan": plan_artifact()})
    call("search_projects", {})
    call("select_project", {"full_name": "demo/project"})
    call("record_progress", {"note": "started"})
    state = ctx.session.skill_state["job-radar"]
    old_jd = state["jd"]
    old_profile = state["profile"]

    with pytest.raises(ValueError, match="schema"):
        call("set_jd", {"jd": {"hard_requirements": "must be a list"}})
    assert state["jd"] == old_jd
    assert state["plan"] and state["repos"] and state["selected_project"] and state["progress"]

    invalid_profile = profile_artifact()
    invalid_profile["skills"][0].pop("evidence")
    with pytest.raises(ValueError, match="evidence"):
        call(
            "set_profile",
            {
                "profile": invalid_profile,
                "source": {"kind": "self_report", "evidence": ["chat"]},
            },
        )
    assert state["profile"] == old_profile
    assert state["plan"] and state["repos"] and state["selected_project"] and state["progress"]

    call("set_jd", {"jd": jd_artifact("新岗位"), "source": {"kind": "text", "evidence": ["chat"]}})
    assert state["plan"] is None
    assert state["repos"] == []
    assert state["selected_project"] is None
    assert state["progress"] == []


def test_source_paths_use_shared_guard_without_reading_documents(monkeypatch, tmp_path: Path):
    document = tmp_path / "resume.txt"
    document.write_text("candidate evidence", encoding="utf-8")
    calls = []

    import agent_coach.tools as common_tools

    def resolve_path(ctx, path, *, must_exist=False):
        calls.append((path, must_exist))
        return Path(path)

    monkeypatch.setattr(common_tools, "resolve_path", resolve_path)
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    registry.get("set_profile", active_skills=["job-radar"]).handler(
        {
            "profile": profile_artifact(),
            "source": {
                "kind": "local_path",
                "evidence": ["主体 read_document 的结果"],
                "paths": [str(document)],
            },
        },
        ctx,
    )
    assert calls == [(str(document), True)]

    sensitive = tmp_path / "api_token.txt"
    sensitive.write_text("secret", encoding="utf-8")
    with pytest.raises(PermissionError, match="敏感"):
        registry.get("set_profile", active_skills=["job-radar"]).handler(
            {
                "profile": profile_artifact(),
                "source": {
                    "kind": "local_path",
                    "evidence": ["主体 read_document 的结果"],
                    "paths": [str(sensitive)],
                },
            },
            ctx,
        )


def test_structured_tool_specs_inline_domain_schema_refs():
    registry = register_job_radar_tools(ToolRegistry())

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value["$ref"]
            for item in value.values():
                yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)

    for name, key in (("set_jd", "jd"), ("set_profile", "profile"), ("build_plan", "plan")):
        schema = registry.get(name, active_skills=["job-radar"]).spec.parameters
        assert schema["properties"][key]["type"] == "object"
        assert list(refs(schema)) == []
    source_schema = registry.get("set_jd", active_skills=["job-radar"]).spec.parameters["properties"]["source"]
    assert source_schema["required"] == ["kind"]
    assert source_schema["anyOf"] == [
        {"required": ["evidence"]},
        {"required": ["source_evidence"]},
        {"required": ["code_evidence"]},
    ]


def test_search_rejects_nested_rerank_request(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(job_radar, "search_github_projects", lambda *_args, **_kwargs: [])
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]
    registry.get("set_jd", active_skills=active).handler(
        {"jd": jd_artifact(), "source": {"kind": "text", "evidence": ["chat"]}}, ctx
    )
    registry.get("set_profile", active_skills=active).handler(
        {"profile": profile_artifact(), "source": {"kind": "self_report", "evidence": ["chat"]}}, ctx
    )
    registry.get("build_plan", active_skills=active).handler({"plan": plan_artifact()}, ctx)
    with pytest.raises(ValueError, match="llm_rerank"):
        registry.get("search_projects", active_skills=active).handler({"llm_rerank": True}, ctx)


def test_search_preferences_keep_ddpg_and_exclude_isaac_deterministically(monkeypatch, tmp_path: Path):
    from agent_coach.legacy.tools import github_search

    monkeypatch.setattr(
        github_search,
        "_collect_candidates",
        lambda *_args: [
            {
                "full_name": "o/ddpg",
                "html_url": "https://github.com/o/ddpg",
                "description": "DDPG reproduction",
                "language": "Python",
                "stargazers_count": 5,
                "updated_at": "",
                "det_score": 1,
                "source_verified": True,
                "source_paths": ["src/main.py"],
                "source_evidence": ["https://github.com/o/ddpg/blob/main/src/main.py"],
                "code_verified": True,
                "code_paths": ["src/main.py"],
                "code_evidence": ["https://github.com/o/ddpg/blob/main/src/main.py"],
            },
            {
                "full_name": "o/isaac-ddpg",
                "html_url": "https://github.com/o/isaac-ddpg",
                "description": "Isaac simulator DDPG",
                "language": "Python",
                "stargazers_count": 5,
                "updated_at": "",
                "det_score": 1,
                "source_verified": True,
                "source_paths": ["src/main.py"],
                "source_evidence": ["https://github.com/o/isaac-ddpg/blob/main/src/main.py"],
                "code_verified": True,
                "code_paths": ["src/main.py"],
                "code_evidence": ["https://github.com/o/isaac-ddpg/blob/main/src/main.py"],
            },
        ],
    )
    repos = job_radar.search_github_projects(
        None,
        ["reinforcement learning"],
        llm_rerank=False,
        preferences={"include": ["ddpg"], "exclude": ["isaac"]},
        workspace=tmp_path,
    )
    assert [repo.full_name for repo in repos] == ["o/ddpg"]


def test_ui_confirmation_is_separate_and_new_jd_clears_it(tmp_path: Path):
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]
    set_jd = registry.get("set_jd", active_skills=active).handler

    set_jd({"jd": jd_artifact(), "source": {"kind": "text", "evidence": ["chat"]}}, ctx)
    assert ctx.session.skill_state["job-radar"]["jd_confirmation"] is None

    job_radar.confirm_jd({"confirmed": True}, ctx)
    confirmation = ctx.session.skill_state["job-radar"]["jd_confirmation"]
    assert confirmation["confirmed"] is True
    assert confirmation["actor"] == "user"

    set_jd(
        {"jd": jd_artifact("新岗位"), "source": {"kind": "text", "evidence": ["chat"]}},
        ctx,
    )
    assert ctx.session.skill_state["job-radar"]["jd_confirmation"] is None


def test_profile_updates_persist_clarification_status_and_answered_diff(tmp_path: Path):
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    handler = registry.get("set_profile", active_skills=["job-radar"]).handler

    first = profile_artifact()
    handler(
        {"profile": first, "source": {"kind": "self_report", "evidence": ["chat"]}},
        ctx,
    )
    state = ctx.session.skill_state["job-radar"]
    assert state["clarification"]["status"] == "active"
    assert state["clarification"]["answered"] == []

    second = profile_artifact()
    second["open_questions"] = []
    handler(
        {"profile": second, "source": {"kind": "chat", "evidence": ["answer"]}},
        ctx,
    )
    state = ctx.session.skill_state["job-radar"]
    assert state["clarification"]["status"] == "completed"
    assert state["clarification"]["answered"] == ["是否有上线证据？"]


def test_copy_profile_state_does_not_copy_job_context(tmp_path: Path):
    source = context(tmp_path).session
    target = AgentSession(workspace=str(tmp_path))
    source.skill_state["job-radar"] = {
        "profile": profile_artifact(),
        "profile_source": {"kind": "self_report", "evidence": ["chat"]},
        "clarification": {"status": "active", "open_questions": ["q"], "answered": []},
        "jd": jd_artifact(),
        "plan": plan_artifact(),
        "repos": [{"full_name": "old/repo"}],
        "selected_project": {"full_name": "old/repo"},
        "progress": [{"note": "old"}],
    }
    job_radar.copy_profile_state(source, target)
    state = target.skill_state["job-radar"]
    assert state["profile"] == source.skill_state["job-radar"]["profile"]
    assert state["profile"] is not source.skill_state["job-radar"]["profile"]
    assert state["jd"] is None
    assert state["plan"] is None
    assert state["repos"] == []
    assert state["selected_project"] is None
    assert state["progress"] == []


def _prepare_projects(monkeypatch, tmp_path: Path):
    projects = {
        "A": verified_repo("demo/a"),
        "B": verified_repo("demo/b"),
    }
    searches = iter([[projects["A"], projects["B"]], [projects["B"]]])

    def deterministic_search(*_args, **_kwargs):
        return next(searches)

    monkeypatch.setattr(job_radar, "search_github_projects", deterministic_search)
    ctx = context(tmp_path)
    registry = register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]

    def call(name: str, arguments: dict):
        return registry.get(name, active_skills=active).handler(arguments, ctx)

    call("set_jd", {"jd": jd_artifact(), "source": {"kind": "text", "evidence": ["chat"]}})
    call("set_profile", {"profile": profile_artifact(), "source": {"kind": "self_report", "evidence": ["chat"]}})
    call("build_plan", {"plan": plan_artifact()})
    call("search_projects", {})
    return ctx, registry, active, call


def test_project_history_switch_refresh_revisit_and_approval_isolation(monkeypatch, tmp_path: Path):
    ctx, registry, active, call = _prepare_projects(monkeypatch, tmp_path)

    call("select_project", {"full_name": "demo/a"})
    call("record_progress", {"note": "A 的第一步", "evidence": "a-proof"})
    stale = call(
        "record_progress",
        {"note": "A 的待验收步骤", "evidence": "a-pending-proof", "completed": True},
    )

    # Refreshing candidates may remove A, but the active project and its
    # compatibility progress remain intact.
    call("search_projects", {})
    state = ctx.session.skill_state["job-radar"]
    assert state["selected_project"]["full_name"] == "demo/a"
    assert [item["note"] for item in state["progress"]] == ["A 的第一步"]

    call("select_project", {"full_name": "demo/b"})
    call("record_progress", {"note": "B 的第一步", "evidence": "b-proof"})
    call("select_project", {"full_name": "demo/a"})
    assert [item["note"] for item in state["progress"]] == ["A 的第一步"]

    history = {item["project_key"]: item for item in state["project_history"]}
    assert set(history) == {"demo/a", "demo/b"}
    assert [item["note"] for item in history["demo/b"]["progress"]] == ["B 的第一步"]
    assert history["demo/a"]["status"] == "in_progress"

    # Re-entering A after visiting B cannot authorize the old completion.
    with pytest.raises(ValueError, match="已切换"):
        registry.get("record_progress", active_skills=active).execute(stale, ctx)

    approved = call(
        "record_progress",
        {"note": "A 的新验收步骤", "evidence": "a-approved-proof", "completed": True},
    )
    registry.get("record_progress", active_skills=active).execute(approved, ctx)
    registry.get("record_progress", active_skills=active).execute(approved, ctx)
    history = {item["project_key"]: item for item in state["project_history"]}
    assert history["demo/a"]["status"] == "completed"
    assert sum(item.get("approval_id") == approved.id for item in history["demo/a"]["progress"]) == 1


def test_project_history_survives_plan_and_jd_invalidation(tmp_path: Path, monkeypatch):
    ctx, _registry, _active, call = _prepare_projects(monkeypatch, tmp_path)
    call("select_project", {"full_name": "demo/a"})
    call("record_progress", {"note": "保留的证据", "evidence": "proof"})

    call("build_plan", {"plan": plan_artifact()})
    state = ctx.session.skill_state["job-radar"]
    assert state["selected_project"] is None and state["progress"] == []
    assert state["project_history"][0]["progress"][0]["evidence"] == "proof"

    call("set_jd", {"jd": jd_artifact("新岗位"), "source": {"kind": "text", "evidence": ["chat"]}})
    assert state["project_history"][0]["project_key"] == "demo/a"
    assert state["project_history"][0]["progress"][0]["note"] == "保留的证据"


def test_legacy_project_history_migrates_lazily_and_survives_restart(tmp_path: Path):
    store = SessionStore(tmp_path)
    session = store.create()
    legacy = {
        "selected_project": {"full_name": "demo/a", "html_url": "https://github.com/demo/a"},
        "progress": [{"project": "demo/a", "note": "当前进度", "status": "in_progress"}],
        "progress_history": [
            {"project": "demo/a", "note": "早期证据", "status": "in_progress"},
            {"project": "demo/b", "note": "B 证据", "status": "in_progress"},
        ],
        "repos": [
            {"full_name": "demo/a", "html_url": "https://github.com/demo/a"},
            {"full_name": "demo/b", "html_url": "https://github.com/demo/b"},
        ],
    }
    session.skill_state["job-radar"] = legacy
    view = job_radar.workflow_state(session)
    assert "project_history" not in legacy
    assert {item["project_key"] for item in view["project_history"]} == {"demo/a", "demo/b"}

    ctx = ToolContext(workspace=tmp_path, session=session, router=None)
    registry = register_job_radar_tools(ToolRegistry())
    # A stale historical metadata-only candidate cannot be selected after the
    # provenance gate was introduced.  The exact active project remains a
    # compatibility continuation so migration does not strand existing work.
    registry.get("select_project", active_skills=["job-radar"]).handler({"full_name": "demo/a"}, ctx)
    with pytest.raises(ValueError):
        registry.get("select_project", active_skills=["job-radar"]).handler({"full_name": "demo/b"}, ctx)
    assert {item["project_key"] for item in session.skill_state["job-radar"]["project_history"]} == {
        "demo/a",
        "demo/b",
    }
    store.save(session)

    restored = store.get(session.id)
    restored_view = job_radar.workflow_state(restored)
    records = {item["project_key"]: item for item in restored_view["project_history"]}
    assert [item["note"] for item in records["demo/a"]["progress"]] == ["早期证据", "当前进度"]
    assert [item["note"] for item in records["demo/b"]["progress"]] == ["B 证据"]
