"""Regression tests for the approved audit findings. All services are mocked."""
import base64
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from coach.core.schema import LLMResponse, ToolCall
from coach.domain.schemas import JDCard, LearningPlan, SkillItem, UserSkillProfile
from coach.sidecar.store import SessionStore, SessionConflict
from coach.tools import documents, images, interviewer, planner, profile_builder, github_search, job_sources
from coach.tools.agent import AgentRuntime, PendingAction


@pytest.fixture
def service(tmp_path, monkeypatch):
    import dotenv
    from coach.sidecar import paths
    monkeypatch.setenv("JOB_RADAR_HOME", str(tmp_path))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    from coach.sidecar import main
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "sessions"))
    monkeypatch.setattr(main, "_auto_fetch_context", lambda message: "")
    monkeypatch.setattr(main, "get_router", lambda: SimpleNamespace(complete=lambda *a, **k: LLMResponse(text="完成")))
    from fastapi.testclient import TestClient
    client = TestClient(main.app, base_url="http://127.0.0.1:17689", headers={"X-Job-Radar-Token": main.API_TOKEN})
    return main, client


def test_api_auth_origin_host_and_health(service):
    main, client = service
    assert client.get("/health").json()["instance_id"] == main.INSTANCE_ID
    assert main.API_TOKEN not in client.get("/health").text
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/sessions", headers={"X-Job-Radar-Token": ""}).status_code == 401
    assert client.get("/api/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/sessions", headers={"Host": "evil.example"}).status_code == 403
    assert client.options("/api/sessions", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}).status_code == 403


@pytest.mark.parametrize("identifier", ["../escape", "..\\escape", "C:\\escape", "/escape", "CON", "a:b", "%2e%2e"])
def test_session_paths_rejected(tmp_path, identifier):
    store = SessionStore(tmp_path)
    with pytest.raises(ValueError):
        store.delete(identifier)


def test_stale_save_rejected_and_upload_names_preserved(tmp_path):
    store = SessionStore(tmp_path)
    first = store.create()
    stale = store.get(first.session_id)
    first.jd = JDCard(job_title="new")
    store.save(first)
    stale.profile = UserSkillProfile(summary="stale")
    with pytest.raises(SessionConflict):
        store.save(stale)
    assert store.get(first.session_id).jd.job_title == "new"
    uploads = store.save_uploads(first.session_id, [("shot.png", b"first"), ("shot.png", b"second")])
    from pathlib import Path
    assert len(set(uploads)) == 2
    assert [Path(p).read_bytes() for p in uploads] == [b"first", b"second"]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("url", ["https://evil.example/github.com/private", "https://github.com.evil.example/x", "http://github.com/o/r", "https://user@github.com/o/r", "https://github.com:444/o/r"])
def test_token_never_sent_to_untrusted_url(tmp_path, monkeypatch, url):
    import requests
    runtime = AgentRuntime(tmp_path)
    monkeypatch.setattr(runtime, "_git_proxy", lambda: None)
    monkeypatch.setattr(runtime, "_github_token", lambda: "FAKE_TOKEN")
    seen = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: (seen.append(kw) or SimpleNamespace(status_code=404, text="")))
    runtime.fetch_url(url)
    assert seen and all("Authorization" not in kw.get("headers", {}) for kw in seen)


def test_authenticated_redirects_are_disabled(tmp_path, monkeypatch):
    import requests
    runtime = AgentRuntime(tmp_path)
    monkeypatch.setattr(runtime, "_git_proxy", lambda: None)
    monkeypatch.setattr(runtime, "_github_token", lambda: "FAKE_TOKEN")
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(status_code=404, text="")
    monkeypatch.setattr(requests, "get", get)
    runtime.fetch_url("https://github.com/o/r")
    authenticated = [kw for kw in seen if "Authorization" in kw.get("headers", {})]
    assert authenticated and all(kw.get("allow_redirects") is False for kw in authenticated)


def test_clone_argv_and_write_preview_binding(tmp_path, monkeypatch):
    import subprocess
    runtime = AgentRuntime(tmp_path)
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: (seen.append((args, kw)) or SimpleNamespace(stdout="ok", stderr="", returncode=0)))
    action = runtime.git_clone("call", "https://github.com/o/r.git", "folder with spaces;&")
    runtime.execute(action)
    assert seen[0][0][:3] == ["git", "clone", "--"]
    assert seen[0][0][-1].endswith("folder with spaces;&")
    assert seen[0][1]["shell"] is False
    with pytest.raises(ValueError):
        runtime.git_clone("call", "ext::danger")
    file = tmp_path / "file.txt"
    file.write_text("before")
    write = runtime.write_file("call", str(file), "after")
    assert "before" in write.payload["diff"] and "after" in write.payload["diff"]
    file.write_text("changed")
    with pytest.raises(ValueError):
        runtime.execute(write)
    assert file.read_text() == "changed"


def test_config_save_preserves_fields_and_invalid_update_is_atomic(service, tmp_path):
    main, client = service
    config = {"providers": [{"name": "demo", "api_mode": "chat_completions", "base_url": "https://example.test/v1", "api_key_env": "DEMO_KEY", "api_key": "fake-key", "timeout": 20, "extra_body": {"thinking": {"type": "enabled"}}}], "tasks": {"default": [{"provider": "demo", "model": "model"}]}}
    assert client.put("/api/config", json=config).status_code == 200
    config["providers"][0].pop("api_key")
    assert client.put("/api/config", json=config).status_code == 200
    saved = client.get("/api/config").json()["providers"]["demo"]
    assert saved["api_key_env"] == "DEMO_KEY"
    assert saved["extra_body"]["thinking"]["type"] == "enabled"
    yaml_before = main.paths.config_path().read_bytes()
    env_before = main.paths.env_path().read_bytes()
    config["tasks"]["default"][0]["provider"] = "missing"
    assert client.put("/api/config", json=config).status_code == 400
    assert main.paths.config_path().read_bytes() == yaml_before
    assert main.paths.env_path().read_bytes() == env_before
    assert client.put("/api/model", json={"provider": "demo", "model": "model"}).status_code == 200
    assert client.put("/api/model", json={"provider": "demo", "model": "missing"}).status_code == 400
    assert client.put("/api/model", json={}).json()["current"] is None


def test_upload_error_and_duplicate_screenshot_api(service, monkeypatch):
    main, client = service
    state = main.store.create()
    response = client.post(f"/api/sessions/{state.session_id}/profile/documents", data={"source": "resume"}, files=[("files", ("a.unsupported", b"data"))])
    assert response.status_code == 400
    assert response.json()["detail"]["failed_files"]
    seen = []
    monkeypatch.setattr(main.jd_parser, "parse_jd_screenshots", lambda router, paths: (seen.extend(paths) or JDCard(job_title="test")))
    response = client.post(f"/api/sessions/{state.session_id}/jd", files=[("files", ("same.png", b"a")), ("files", ("same.png", b"b"))])
    assert response.status_code == 200 and len(set(seen)) == 2


def test_concurrent_confirmation_executes_once_and_keeps_tool_order(service, monkeypatch):
    main, client = service
    state = main.store.create()
    state.plan = LearningPlan(direction="test")
    state.agent_thread = [{"role": "system", "content": "test"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "write_file", "arguments": {}}, {"id": "b", "name": "read_file", "arguments": {}}]}]
    state.agent_pending = {"action_id": "approved", "kind": "write_file", "summary": "test", "tool_call_id": "a", "payload": {}}
    state.agent_queue = [{"id": "b", "name": "read_file", "arguments": {}}]
    main.store.save(state)
    executed = []
    runtime = SimpleNamespace(execute=lambda action: (executed.append(action.action_id) or "wrote"), run_tool=lambda *a: "read")
    monkeypatch.setattr(main, "_agent_runtime", lambda: runtime)
    def call():
        return client.post(f"/api/sessions/{state.session_id}/agent/confirm", json={"action_id": "approved", "approve": True}).status_code
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda _: call(), range(2)))
    assert sorted(statuses) == [200, 409]
    assert executed == ["approved"]
    final = main.store.get(state.session_id)
    assert [m["tool_call_id"] for m in final.agent_thread if m["role"] == "tool"] == ["a", "b"]
    assert final.agent_running is False and final.agent_pending is None
    assert main._agent_thread_messages(final)[-1].content == "完成"
    assert client.post(f"/api/sessions/{state.session_id}/agent/reset").status_code == 200
    assert main.store.get(state.session_id).agent_logs == []


def test_rejected_action_continues_to_next_pending(service, monkeypatch):
    main, client = service
    state = main.store.create()
    state.agent_thread = [{"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "write_file", "arguments": {}}, {"id": "b", "name": "run_command", "arguments": {}}]}]
    state.agent_pending = {"action_id": "first", "kind": "write_file", "summary": "first", "tool_call_id": "a", "payload": {}}
    state.agent_queue = [{"id": "b", "name": "run_command", "arguments": {}}]
    main.store.save(state)
    monkeypatch.setattr(main, "_agent_runtime", lambda: SimpleNamespace(run_tool=lambda *args: PendingAction("second", "run_command", "second", "b", {})))
    response = client.post(f"/api/sessions/{state.session_id}/agent/confirm", json={"action_id": "first", "approve": False})
    assert response.status_code == 200 and '"action_id": "second"' in response.text
    assert client.get(f"/api/sessions/{state.session_id}").json()["agent_pending"]["action_id"] == "second"


def test_profile_removal_wins_and_interview_retains_full_history():
    profile = UserSkillProfile(skills=[SkillItem(name="Python")])
    delta = profile_builder._ProfileDelta(skills=[SkillItem(name="Python")], remove_skills=[" python "])
    assert profile_builder.merge_profile_delta(profile, delta).skills == []
    captured = []
    responses = iter([{"done": False, "question": None}, {"done": False, "question": "more?"}])
    def complete(task, messages, schema, **kwargs):
        captured.append(messages[0].content)
        return next(responses)
    question = interviewer.next_interview_question(SimpleNamespace(complete_json=complete), profile, [(f"q{i}", f"a{i}") for i in range(30)])
    assert question == "more?" and len(captured) == 2
    assert "Q: q0\nA: a0" in captured[0] and "Q: q29" in captured[0]


def test_missing_plan_queries_retries():
    responses = iter([{"gaps": [{"requirement": "x", "status": "missing"}], "phases": [{"phase": "one", "goal": "x"}], "github_search_queries": []}, {"gaps": [{"requirement": "x", "status": "missing"}], "phases": [{"phase": "one", "goal": "x"}], "github_search_queries": ["query"]}])
    router = SimpleNamespace(complete_json=lambda *args, **kwargs: next(responses))
    assert planner.build_learning_plan(router, JDCard(), UserSkillProfile()).github_search_queries == ["query"]


def test_pptx_relationship_order_and_bmp_transcoding(tmp_path):
    pptx = tmp_path / "sample.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="p" xmlns:r="r"><p:sldIdLst><p:sldId r:id="b"/><p:sldId r:id="a"/></p:sldIdLst></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="a" Type="x/slide" Target="slides/slide1.xml"/><Relationship Id="b" Type="x/slide" Target="slides/slide10.xml"/></Relationships>')
        archive.writestr("ppt/slides/slide1.xml", "<root><t>first-file</t></root>")
        archive.writestr("ppt/slides/slide10.xml", "<root><t>first-visible</t></root>")
    assert documents.read_document(pptx).index("first-visible") < documents.read_document(pptx).index("first-file")
    bmp = tmp_path / "image.bmp"
    Image.new("RGB", (2, 2)).save(bmp)
    block = images.load_image_blocks([bmp])[0]
    assert block.media_type == "image/png" and base64.b64decode(block.data).startswith(b"\x89PNG")


def test_public_github_filter_and_explicit_credential_source(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_RADAR_HOME", str(tmp_path))
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-use-inherited-value")
    from coach.tools.credentials import github_token
    assert github_token() is None
    queries = []
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, params):
            queries.append(params["q"])
            return SimpleNamespace(status_code=200, json=lambda: {"items": [{"private": True, "full_name": "o/private"}, {"private": False, "full_name": "o/public", "html_url": "https://github.com/o/public"}]})
    monkeypatch.setattr(github_search, "_client", Client)
    result = github_search._collect_candidates(["demo"], 0, 365)
    assert [item["full_name"] for item in result] == ["o/public"]
    assert "is:public" in queries[0]
    monkeypatch.setattr(github_search, "_collect_candidates", lambda *args: [{"full_name": "o/ddpg", "description": "", "det_score": 1, "stargazers_count": 5, "updated_at": ""}, {"full_name": "o/isaac-ddpg", "description": "", "det_score": 1, "stargazers_count": 5, "updated_at": ""}])
    # The include/exclude contract is also checked without any network or LLM.
    monkeypatch.setattr(github_search, "_to_repo", lambda item: item)
    selected = github_search.search_github_projects(None, ["x"], llm_rerank=False, preferences={"include": ["ddpg"], "exclude": ["isaac"]})
    assert [item["full_name"] for item in selected] == ["o/ddpg"]


def test_platform_search_validates_real_source_hosts(monkeypatch):
    html = '<a class="result__a" href="https://evil.example/zhipin.com">bad</a><a class="result__snippet">招聘工程师</a><a class="result__a" href="https://www.zhipin.com/job_detail/test.html">工程师</a><a class="result__snippet">Python 岗位职责与要求</a>'
    monkeypatch.setattr(job_sources.web_search, "_fetch", lambda *a, **k: SimpleNamespace(text=html))
    result = job_sources.search("python", "boss")
    assert len(result) == 1 and result[0]["url"].startswith("https://www.zhipin.com/")
    imported = job_sources.import_text(result[0]["url"], "软件工程师，负责 Python 服务开发，需要掌握数据库、网络和自动化测试，欢迎应届毕业生。")
    assert imported["method"] == "用户提供正文"
    with pytest.raises(ValueError):
        job_sources.import_text("https://evil.example/zhipin.com", "provided")


def test_selected_conversation_model_keeps_fallback_and_vision_route(monkeypatch):
    from coach.core.llm.registry import ModelConfig, ProviderEntry, TaskTarget
    from coach.core.llm.router import ModelRouter
    from coach.core.llm.base import ProviderError
    providers = {name: ProviderEntry(name, "chat_completions", "https://example.test", None, key_required=False) for name in ["selected", "backup", "vision"]}
    config = ModelConfig(providers, {"default": [TaskTarget("backup", "b")], "agent": [TaskTarget("backup", "b")], "vision": [TaskTarget("vision", "v")]}, TaskTarget("selected", "s"))
    router = ModelRouter(config)
    calls = []
    def client(entry, model):
        def complete(*args, **kwargs):
            calls.append(entry.name)
            if entry.name == "selected":
                raise ProviderError(entry.name, "rate limited", status_code=429)
            return LLMResponse(text="ok")
        return SimpleNamespace(complete=complete)
    monkeypatch.setattr(router, "_build_client", client)
    router.complete("agent", [])
    assert calls == ["selected", "backup"]
    calls.clear()
    router.complete("vision", [])
    assert calls == ["vision"]


def test_all_failed_local_documents_keep_existing_profile(service, monkeypatch, tmp_path):
    main, client = service
    state = main.store.create()
    state.profile = UserSkillProfile(summary="unchanged")
    main.store.save(state)
    empty = tmp_path / "empty.json"
    empty.write_text("")
    response = client.post(f"/api/sessions/{state.session_id}/profile/path", json={"path": str(empty)})
    assert response.status_code == 400
    assert main.store.get(state.session_id).profile.summary == "unchanged"
    assert {".json", ".csv", ".docx", ".pptx", ".xlsx"} <= main._SUPPORTED_DOC_SUFFIXES


def test_old_process_execution_is_recovered_without_replay(service):
    main, client = service
    state = main.store.create()
    state.agent_running = True
    state.agent_execution = {"instance_id": "old-instance", "status": "claimed"}
    state.agent_thread = [{"role": "assistant", "content": "", "tool_calls": [{"id": "one", "name": "write_file", "arguments": {}}]}]
    main.store.save(state)
    recovered = client.get(f"/api/sessions/{state.session_id}").json()
    assert recovered["agent_running"] is False
    assert recovered["agent_execution"]["status"] == "interrupted"
    assert recovered["agent_thread"][-1]["role"] == "tool"
    assert "未取得结果" in recovered["agent_thread"][-1]["content"]


def test_failed_execution_is_not_replayed(service, monkeypatch):
    main, client = service
    state = main.store.create()
    state.agent_thread = [{"role": "assistant", "content": "", "tool_calls": [{"id": "one", "name": "write_file", "arguments": {}}]}]
    state.agent_pending = {"action_id": "fail", "kind": "write_file", "summary": "test", "tool_call_id": "one", "payload": {}}
    main.store.save(state)
    count = []
    def fail(action):
        count.append(1)
        raise RuntimeError("mock failure")
    monkeypatch.setattr(main, "_agent_runtime", lambda: SimpleNamespace(execute=fail))
    for expected in [200, 409]:
        assert client.post(f"/api/sessions/{state.session_id}/agent/confirm", json={"action_id": "fail", "approve": True}).status_code == expected
    assert count == [1]
    assert main.store.get(state.session_id).agent_execution["status"] == "failed"


def test_restart_keeps_unapproved_action_without_inventing_result(service):
    main, client = service
    state = main.store.create()
    state.agent_running = True
    state.agent_execution = {"instance_id": "old-instance", "status": "conversation"}
    state.agent_thread = [{"role": "assistant", "content": "", "tool_calls": [{"id": "one", "name": "write_file", "arguments": {}}]}]
    state.agent_pending = {"action_id": "unapproved", "kind": "write_file", "summary": "test", "tool_call_id": "one", "payload": {}}
    main.store.save(state)
    recovered = client.get(f"/api/sessions/{state.session_id}").json()
    assert recovered["agent_pending"]["action_id"] == "unapproved"
    assert not any(message["role"] == "tool" for message in recovered["agent_thread"])
    assert recovered["agent_running"] is False


def test_reject_still_works_with_invalid_model_config(service, monkeypatch):
    main, client = service
    from fastapi import HTTPException
    def invalid(): raise HTTPException(400, "bad config")
    monkeypatch.setattr(main, "get_router", invalid)
    state = main.store.create()
    state.agent_thread = [{"role": "assistant", "content": "", "tool_calls": [{"id": "one", "name": "write_file", "arguments": {}}]}]
    state.agent_pending = {"action_id": "unapproved", "kind": "write_file", "summary": "test", "tool_call_id": "one", "payload": {}}
    main.store.save(state)
    response = client.post(f"/api/sessions/{state.session_id}/agent/confirm", json={"action_id": "unapproved", "approve": False})
    assert response.status_code == 200 and "操作已拒绝" in response.text
    assert main.store.get(state.session_id).agent_pending is None
