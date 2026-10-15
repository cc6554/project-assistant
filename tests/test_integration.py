"Offline acceptance: one agent model owns the complete job-radar flow."
from pathlib import Path

from agent_coach.bootstrap import build_engine
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.legacy.domain.schemas import GitHubRepo


class WorkflowRouter:
    def __init__(self):
        self.position = 0
        self.complete_calls: list[tuple[str, int]] = []
        self.message_lengths: list[int] = []
        self.complete_json_calls: list[tuple] = []
        self.requests = [
            ("activate_skill", {"name": "job-radar"}),
            (
                "set_jd",
                {
                    "jd": {
                        "job_title": "Python backend",
                        "hard_requirements": ["HTTP"],
                        "skill_tags": ["Python"],
                        "raw_summary": "Python backend engineer; HTTP and tests required.",
                    },
                    "source": {"kind": "text", "evidence": ["user message"]},
                },
            ),
            (
                "set_profile",
                {
                    "profile": {
                        "summary": "CSV parser experience",
                        "skills": [
                            {
                                "name": "Python",
                                "proficiency": "working",
                                "confidence": 0.6,
                                "sources": ["self_report"],
                                "evidence": "I wrote a CSV parser in Python.",
                            }
                        ],
                    },
                    "source": {"kind": "self_report", "evidence": ["user message"]},
                },
            ),
            (
                "build_plan",
                {
                    "plan": {
                        "direction": "Backend",
                        "gaps": [{"requirement": "HTTP", "status": "missing"}],
                        "phases": [{"phase": "HTTP", "goal": "Implement handler"}],
                        "github_search_queries": ["python http project"],
                    }
                },
            ),
            ("search_projects", {"min_stars": 0}),
            ("select_project", {"full_name": "fixture/http-practice"}),
            ("write_file", {"path": "practice.md", "content": "Implement HTTP handler and tests.\n"}),
            (
                "record_progress",
                {
                    "note": "Plan document reviewed",
                    "evidence": "practice.md reviewed in this offline test",
                    "completed": True,
                },
            ),
        ]

    def complete(self, task, messages, *, tools, **kwargs):
        assert task == "agent"
        self.complete_calls.append((task, len(messages)))
        self.message_lengths.append(len(messages))
        assert any(
            message.role == "user"
            and "Use my JD and experience" in str(message.content)
            for message in messages
        )
        if self.position:
            tool_names = {message.name for message in messages if message.role == "tool"}
            expected = [name for name, _ in self.requests[: self.position]]
            assert set(expected) <= tool_names
            if self.position >= 2:
                tool_text = "\n".join(str(message.content) for message in messages if message.role == "tool")
                assert "Python backend" in tool_text or "job_title" in tool_text
        # Every model request must contain a complete prior tool exchange.
        waiting = []
        for message in messages:
            if message.role == "assistant":
                assert not waiting
                waiting = [call.id for call in message.tool_calls or []]
            elif message.role == "tool":
                assert message.tool_call_id in waiting
                waiting.remove(message.tool_call_id)
        assert not waiting
        assert not any("执行异常" in str(m.content) for m in messages if m.role == "tool")
        if self.position == len(self.requests):
            return LLMResponse(text="Offline fixture workflow completed.")
        name, arguments = self.requests[self.position]
        assert name in {tool.name for tool in tools}
        self.position += 1
        return LLMResponse(tool_calls=[ToolCall(id=f"step-{self.position}", name=name, arguments=arguments)])

    def complete_json(self, *args, **kwargs):
        self.complete_json_calls.append((args, kwargs))
        raise AssertionError("job-radar must not make stage model calls")


def test_full_skill_flow_returns_to_general_tools_and_requires_real_approvals(tmp_path, monkeypatch):
    from agent_coach import job_radar

    monkeypatch.setattr(
        job_radar,
        "search_github_projects",
        lambda *a, **kw: [
            GitHubRepo(full_name="fixture/http-practice", html_url="https://github.com/fixture/http-practice")
        ],
    )
    router = WorkflowRouter()
    engine = build_engine(tmp_path, max_steps=20)
    engine.router = router
    first = engine.run("Use my JD and experience to guide a practice project.")
    assert first.status == "pending_approval", first.message
    assert first.pending.tool_name == "write_file"
    assert not (tmp_path / "practice.md").exists()
    session_id = first.session_id

    # Fresh assembly reads the saved approval and continues the same workflow.
    restored = build_engine(tmp_path, max_steps=20)
    restored.router = router
    second = restored.approve(session_id, first.pending.id)
    assert (tmp_path / "practice.md").read_text() == "Implement HTTP handler and tests.\n"
    assert second.status == "pending_approval"
    assert second.pending.tool_name == "record_progress"
    assert not second.session.skill_state["job-radar"]["progress"]
    final = restored.approve(session_id, second.pending.id)
    assert final.status == "completed"
    assert final.session.skill_state["job-radar"]["progress"][-1]["user_confirmed"] is True
    assert final.session.skill_state["job-radar"]["profile"]["skills"][0]["sources"] == ["self_report"]
    assert router.complete_json_calls == []
    assert router.complete_calls and all(task == "agent" for task, _ in router.complete_calls)
    assert router.message_lengths == sorted(router.message_lengths)
    assert len(set(router.message_lengths)) == len(router.message_lengths)


def test_generic_agent_without_skills_accepts_arbitrary_task(tmp_path):
    class Router:
        def complete(self, task, messages, *, tools, **kwargs):
            assert {"list_dir", "read_file", "write_file", "run_command"} <= {t.name for t in tools}
            assert not {"set_jd", "set_profile", "build_plan", "activate_skill"} & {t.name for t in tools}
            assert "job-radar" not in messages[0].content
            return LLMResponse(text="An ordinary explanation, with no JD prerequisite.")

    engine = build_engine(tmp_path, no_skills=True)
    engine.router = Router()
    result = engine.run("Explain dependency injection.")
    assert result.status == "completed"
    assert result.session.skill_state == {}


def test_custom_prompt_skill_is_discovered_and_mounted(tmp_path):
    folder = tmp_path / ".agent-coach" / "skills" / "review"
    folder.mkdir(parents=True)
    folder.joinpath("SKILL.md").write_text(
        "---\nname: review\ndescription: Review Python\n---\nCUSTOM_REVIEW_GUIDANCE\n",
        encoding="utf-8",
    )
    engine = build_engine(tmp_path)
    session = engine.new_session()
    engine.activate_skill(session.id, "review")
    assert "CUSTOM_REVIEW_GUIDANCE" in engine.skills_instructions(["review"])
    assert engine.get_session(session.id).skill_state == {}
