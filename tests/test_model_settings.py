from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading

import pytest

import agent_coach.web as web_module
from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.model_profiles import ModelProfilesStore
from agent_coach.settings import ModelSettings, ModelSettingsStore, SettingsError, make_draft
from agent_coach.web import build_service, make_server


def _request(service, server, method: str, path: str, body=None, *, auth=True):
    port = int(server.server_address[1])
    headers = {"Host": f"127.0.0.1:{port}"}
    if auth:
        headers["Authorization"] = f"Bearer {service.token}"
    raw = None if body is None else json.dumps(body).encode("utf-8")
    if raw is not None:
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, raw, headers)
    response = client.getresponse()
    data = response.read()
    client.close()
    return response.status, json.loads(data.decode("utf-8"))


@pytest.fixture
def settings_server(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _draft(**overrides):
    value = {
        "api_mode": "chat_completions",
        "base_url": "http://localhost:11434/v1",
        "model": "tool-model",
    }
    value.update(overrides)
    return value


def test_settings_api_is_authenticated_and_never_returns_key(settings_server):
    service, server = settings_server
    status, payload = _request(service, server, "GET", "/api/settings/models", auth=False)
    assert status == 401

    status, payload = _request(
        service,
        server,
        "POST",
        "/api/settings/models",
        _draft(api_key="top-secret-test-key"),
    )
    assert status == 200
    assert set(payload) == {"api_mode", "base_url", "model", "has_api_key"}
    assert "top-secret-test-key" not in json.dumps(payload)

    status, payload = _request(service, server, "GET", "/api/settings/models")
    assert status == 200
    assert payload["has_api_key"] is True
    assert "top-secret-test-key" not in json.dumps(payload)

    settings_file = service.workspace / ".agent-coach" / "model-settings.json"
    key_file = service.workspace / ".agent-coach" / "model-settings.key"
    assert "top-secret-test-key" not in settings_file.read_text(encoding="utf-8")
    assert key_file.read_bytes()
    assert b"top-secret-test-key" not in key_file.read_bytes()


def test_saved_settings_restore_and_empty_key_preserves_then_clear(settings_server):
    service, server = settings_server
    _request(service, server, "POST", "/api/settings/models", _draft(api_key="keep-me"))
    restored = build_service(service.workspace)
    assert restored.mode == "configured"
    assert restored.model_settings()["has_api_key"] is True
    assert restored.engine.router.config.providers["gui"].api_key == "keep-me"

    restored.save_model_settings(_draft(model="next-model", api_key=""))
    assert restored.engine.router.config.providers["gui"].api_key == "keep-me"
    restored.save_model_settings(_draft(model="next-model", clear_api_key=True))
    assert restored.model_settings()["has_api_key"] is False
    assert not (restored.workspace / ".agent-coach" / "model-settings.key").exists()


def test_corrupt_saved_key_keeps_ui_open_with_fixed_error(settings_server):
    service, _ = settings_server
    service.save_model_settings(_draft(api_key="key-that-is-never-returned"))
    key_file = service.workspace / ".agent-coach" / "model-settings.key"
    key_file.write_bytes(b"corrupt")
    restored = build_service(service.workspace)
    assert restored.mode == "error"
    assert restored.info()["settings_error"] == "saved_key_unavailable"
    assert restored.model_settings()["has_api_key"] is False


def test_validation_and_endpoint_change_require_new_key_or_clear():
    current = ModelSettings(
        api_mode="chat_completions",
        base_url="https://api.example.test/v1",
        model="old",
        api_key="old-key",
    )
    with pytest.raises(SettingsError):
        make_draft(_draft(base_url="http://evil.example.test/v1"), current=current)
    with pytest.raises(SettingsError):
        make_draft(_draft(base_url="https://api.example.test/v1?token=leak"))
    with pytest.raises(SettingsError):
        make_draft({"api_mode": "invalid", "base_url": "http://localhost/v1", "model": "m"})
    replacement = make_draft(
        _draft(base_url="https://new.example.test/v1", api_key="new-key"), current=current
    )
    assert replacement.api_key == "new-key"


def test_busy_model_save_is_rejected_atomically(settings_server):
    service, _ = settings_server
    service._busy["session"] = "job"
    with pytest.raises(RuntimeError):
        service.save_model_settings(_draft(api_key="secret"))
    assert service.engine.router.provider == "offline-demo"
    assert not (service.workspace / ".agent-coach" / "model-settings.json").exists()


class _ToolCallingRouter:
    def complete(self, *args, **kwargs):
        return LLMResponse(
            text="",
            tool_calls=[ToolCall(id="probe", name="connection_test", arguments={"ok": True})],
        )


def test_connection_test_is_minimal_and_does_not_execute_tools(settings_server):
    service, server = settings_server
    service._build_model_router = lambda settings: _ToolCallingRouter()
    status, payload = _request(service, server, "POST", "/api/settings/models/test", _draft(api_key="secret"))
    assert status == 200
    assert payload["ok"] is True
    assert payload["tool_call"] is True
    assert "secret" not in json.dumps(payload)


def test_connection_test_failure_is_classified_without_sdk_body(settings_server):
    service, server = settings_server

    class FailingRouter:
        def complete(self, *args, **kwargs):
            raise RuntimeError("Authorization: Bearer top-secret-test-key")

    service._build_model_router = lambda settings: FailingRouter()
    status, payload = _request(service, server, "POST", "/api/settings/models/test", _draft(api_key="secret"))
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "connection_failed"
    assert payload["code"] == "connection_failed"
    assert payload["reason"] == "connection_failed"
    assert payload["message"] == "无法连接模型服务，请检查地址和网络。"
    assert "top-secret-test-key" not in json.dumps(payload)


def test_invalid_explicit_yaml_keeps_settings_service_alive(tmp_path: Path):
    config = tmp_path / "providers.yaml"
    config.write_text("providers: [broken", encoding="utf-8")
    service = build_service(tmp_path, config_path=config)
    assert service.mode == "error"
    assert service.info()["settings_error"] == "config_invalid"
    assert service.model_settings()["has_api_key"] is False


def test_declared_model_options_select_without_detaching_provider_endpoint(tmp_path: Path):
    config = tmp_path / "providers.yaml"
    config.write_text(
        """
providers:
  local-a:
    api_mode: chat_completions
    base_url: http://127.0.0.1:11434/v1
  local-b:
    api_mode: chat_completions
    base_url: http://127.0.0.1:22434/v1
tasks:
  default:
    - {provider: local-a, model: alpha}
  agent:
    - {provider: local-b, model: beta}
""".strip(),
        encoding="utf-8",
    )
    service = build_service(tmp_path, config_path=config)
    options = service.model_options()
    assert {row["provider"] for row in options["options"]} == {"local-a", "local-b"}
    assert options["current"] == {"provider": "local-b", "model": "beta"}

    selected = service.select_model({"provider": "local-a", "model": "alpha"})
    assert selected["current"] == {"provider": "local-a", "model": "alpha"}
    provider = service.engine.router.config.providers["local-a"]
    assert service.engine.router.config.current.provider == "local-a"
    assert service.engine.router.config.current.model == "alpha"
    assert provider.base_url == "http://127.0.0.1:11434/v1"
    assert (config.parent / "current_model.json").read_text(encoding="utf-8")


def test_gui_profiles_keep_independent_keys_and_never_return_them(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(
        _draft(
            base_url="http://127.0.0.1:11434/v1",
            model="alpha",
            api_key="profile-key-a",
            display_name="本地 A",
        )
    )
    profile_a = service.profiles_store.active_id()
    service.save_model_settings(
        _draft(
            base_url="https://provider.example.test/v1",
            model="beta",
            api_key="profile-key-b",
            display_name="远程 B",
        )
    )
    profile_b = service.profiles_store.active_id()
    assert profile_a and profile_b and profile_a != profile_b

    options = service.model_options()
    assert {row["display_name"] for row in options["saved_profiles"]} == {"本地 A", "远程 B"}
    serialized = json.dumps(options, ensure_ascii=False)
    assert "profile-key-a" not in serialized
    assert "profile-key-b" not in serialized
    metadata = (tmp_path / ".agent-coach" / "model-profiles.json").read_text(encoding="utf-8")
    assert "profile-key-a" not in metadata and "profile-key-b" not in metadata
    for key_path in (tmp_path / ".agent-coach" / "model-profiles").glob("*.key"):
        assert b"profile-key-a" not in key_path.read_bytes()
        assert b"profile-key-b" not in key_path.read_bytes()

    service.select_model({"profile_id": profile_a})
    assert service.engine.router.config.providers["gui"].api_key == "profile-key-a"
    service.select_model({"profile_id": profile_b})
    assert service.engine.router.config.providers["gui"].api_key == "profile-key-b"


def test_named_profile_switch_is_shared_by_existing_and_new_sessions(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path, demo=True)
    first = service.create_session()["id"]
    second = service.create_session()["id"]
    service.save_model_settings(_draft(model="alpha", api_key="key-a", display_name="First API"))
    first_profile = service.profiles_store.active_id()
    service.save_model_settings(
        _draft(profile_id=first_profile, model="alpha", api_key="", display_name="Renamed API")
    )
    assert service.profiles_store.active_id() == first_profile
    service.save_model_settings(_draft(model="beta", api_key="key-b", display_name="Second API"))
    second_profile = service.profiles_store.active_id()

    def selected_for(session_id):
        router = service.engine.router
        provider = router.config.providers["gui"]
        observed = []
        monkeypatch.setattr(router, "complete", lambda *args, **kwargs: observed.append(
            (provider.base_url, router.config.current.model, provider.api_key)
        ))
        service.engine._complete(service.engine.store.get(session_id))
        return observed[0]

    service.select_model({"profile_id": first_profile})
    third = service.create_session()["id"]
    for session_id in (first, second, third):
        assert selected_for(session_id) == ("http://localhost:11434/v1", "alpha", "key-a")
    options = service.model_options()
    assert next(row for row in options["saved_profiles"] if row["active"])["display_name"] == "Renamed API"
    service.select_model({"profile_id": second_profile})
    for session_id in (first, second, third):
        assert selected_for(session_id)[1:] == ("beta", "key-b")
    restored = build_service(tmp_path)
    assert restored.profiles_store.active_id() == second_profile
    assert restored.profiles_store.get(first_profile).display_name == "Renamed API"


def test_any_busy_session_blocks_global_profile_switch_and_rename(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    first = service.create_session()["id"]
    service.create_session()
    service.save_model_settings(_draft(display_name="Original"))
    profile_id = service.profiles_store.active_id()
    original_router = service.engine.router
    service._busy[first] = "running-job"
    with pytest.raises(RuntimeError, match="会话执行期间"):
        service.select_model({"profile_id": profile_id})
    with pytest.raises(RuntimeError, match="会话执行期间"):
        service.save_model_settings(_draft(profile_id=profile_id, display_name="Renamed"))
    assert service.engine.router is original_router
    assert service.profiles_store.get(profile_id).display_name == "Original"


def test_gui_profiles_restore_after_restart_and_migrate_legacy_single_config(tmp_path: Path):
    first = build_service(tmp_path, demo=True)
    first.save_model_settings(_draft(api_key="restart-key", display_name="Restart"))
    profile_id = first.profiles_store.active_id()
    restored = build_service(tmp_path)
    assert restored.mode == "configured"
    assert restored.profiles_store.active_id() == profile_id
    assert restored.engine.router.config.providers["gui"].api_key == "restart-key"
    assert restored.model_options()["saved_profiles"][0]["profile_id"] == profile_id

    legacy_workspace = tmp_path / "legacy-only"
    legacy_workspace.mkdir()
    ModelSettingsStore(legacy_workspace).save(
        ModelSettings(
            api_mode="chat_completions",
            base_url="http://localhost:11434/v1",
            model="legacy-model",
            api_key="legacy-key",
        )
    )
    migrated = build_service(legacy_workspace)
    assert migrated.profiles_store.active_id() == "default"
    assert migrated.engine.router.config.providers["gui"].api_key == "legacy-key"
    assert migrated.model_options()["saved_profiles"][0]["display_name"] == "默认配置"


def test_profile_endpoint_change_never_borrows_active_key(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(
        _draft(
            base_url="https://first.example.test/v1",
            model="first",
            api_key="first-key",
            display_name="First",
        )
    )
    with pytest.raises(SettingsError):
        service.save_model_settings(
            _draft(
                base_url="https://second.example.test/v1",
                model="second",
                display_name="Second",
            )
        )
    assert len(service.model_options()["saved_profiles"]) == 1
    assert service.engine.router.config.providers["gui"].api_key == "first-key"


def test_profile_save_rolls_back_key_and_metadata_when_catalog_write_fails(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(_draft(model="first", api_key="first-key", display_name="First"))
    profile_id = service.profiles_store.active_id()
    assert profile_id
    metadata_path = tmp_path / ".agent-coach" / "model-profiles.json"
    key_path = tmp_path / ".agent-coach" / "model-profiles" / f"{profile_id}.key"
    old_metadata = metadata_path.read_bytes()
    old_key = key_path.read_bytes()

    def fail_write(_meta):
        raise OSError("injected catalog failure")

    monkeypatch.setattr(service.profiles_store, "_write_meta", fail_write)
    with pytest.raises(OSError, match="injected catalog failure"):
        service.profiles_store.save(
            ModelSettings(
                api_mode="chat_completions",
                base_url="http://localhost:11434/v1",
                model="second",
                api_key="second-key",
            ),
            profile_id=profile_id,
            activate=True,
        )
    assert metadata_path.read_bytes() == old_metadata
    assert key_path.read_bytes() == old_key
    restored = ModelProfilesStore(tmp_path).get(profile_id)
    assert restored.settings.model == "first"
    assert restored.settings.api_key == "first-key"


def test_gui_save_failure_does_not_activate_new_profile_or_legacy_pair(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(_draft(model="first", api_key="first-key", display_name="First"))
    profile_id = service.profiles_store.active_id()
    assert profile_id
    metadata_path = tmp_path / ".agent-coach" / "model-profiles.json"
    settings_path = tmp_path / ".agent-coach" / "model-settings.json"
    key_path = tmp_path / ".agent-coach" / "model-settings.key"
    old_metadata = metadata_path.read_bytes()
    old_settings = settings_path.read_bytes()
    old_key = key_path.read_bytes()

    def fail_legacy_save(_settings):
        raise OSError("injected legacy save failure")

    monkeypatch.setattr(service.settings_store, "save", fail_legacy_save)
    with pytest.raises(OSError, match="injected legacy save failure"):
        service.save_model_settings(_draft(model="second", api_key="second-key", display_name="Second"))
    assert service.profiles_store.active_id() == profile_id
    assert metadata_path.read_bytes() == old_metadata
    assert settings_path.read_bytes() == old_settings
    assert key_path.read_bytes() == old_key
    assert service.engine.router.config.providers["gui"].api_key == "first-key"


def test_profile_switch_failure_rolls_back_legacy_settings(tmp_path: Path, monkeypatch):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(_draft(model="first", api_key="first-key", display_name="First"))
    first_id = service.profiles_store.active_id()
    service.save_model_settings(_draft(model="second", api_key="second-key", display_name="Second"))
    second_id = service.profiles_store.active_id()
    assert first_id and second_id and first_id != second_id
    service.select_model({"profile_id": first_id})
    old_settings = (tmp_path / ".agent-coach" / "model-settings.json").read_bytes()
    old_key = (tmp_path / ".agent-coach" / "model-settings.key").read_bytes()

    def fail_activate(_profile_id):
        raise OSError("injected profile activation failure")

    monkeypatch.setattr(service.profiles_store, "activate", fail_activate)
    with pytest.raises(OSError, match="injected profile activation failure"):
        service.select_model({"profile_id": second_id})
    assert service.profiles_store.active_id() == first_id
    assert (tmp_path / ".agent-coach" / "model-settings.json").read_bytes() == old_settings
    assert (tmp_path / ".agent-coach" / "model-settings.key").read_bytes() == old_key
    assert service.engine.router.config.providers["gui"].api_key == "first-key"


def test_yaml_selection_rolls_back_memory_files_and_active_profile_on_write_failure(
    tmp_path: Path, monkeypatch
):
    config = tmp_path / "providers.yaml"
    config.write_text(
        """
providers:
  local-a:
    api_mode: chat_completions
    base_url: http://127.0.0.1:11434/v1
tasks:
  default:
    - {provider: local-a, model: alpha}
""".strip(),
        encoding="utf-8",
    )
    service = build_service(tmp_path, config_path=config)
    service.save_model_settings(_draft(model="gui", api_key="gui-key", display_name="GUI"))
    active_id = service.profiles_store.active_id()
    old_router = service.engine.router
    current_model = config.parent / "current_model.json"
    assert not current_model.exists()

    def fail_current_model(*_args, **_kwargs):
        raise OSError("injected current-model failure")

    monkeypatch.setattr(web_module, "_write_current_model", fail_current_model)
    with pytest.raises(OSError, match="injected current-model failure"):
        service.select_model({"provider": "local-a", "model": "alpha"})
    assert service.profiles_store.active_id() == active_id
    assert service.engine.router is old_router
    assert service.engine.router.config.providers["gui"].api_key == "gui-key"
    assert not current_model.exists()


def test_yaml_options_survive_gui_profile_save_and_switch(tmp_path: Path):
    config = tmp_path / "providers.yaml"
    config.write_text(
        """
providers:
  local-a:
    api_mode: chat_completions
    base_url: http://127.0.0.1:11434/v1
  local-b:
    api_mode: chat_completions
    base_url: http://127.0.0.1:22434/v1
tasks:
  default:
    - {provider: local-a, model: alpha}
  agent:
    - {provider: local-b, model: beta}
""".strip(),
        encoding="utf-8",
    )
    service = build_service(tmp_path, config_path=config)
    service.save_model_settings(
        _draft(
            base_url="http://127.0.0.1:33434/v1",
            model="gui-model",
            display_name="GUI",
        )
    )
    options = service.model_options()
    assert {row["provider"] for row in options["options"]} == {"local-a", "local-b"}
    assert service.config_path == config.resolve()
    assert options["saved_profiles"][0]["display_name"] == "GUI"
    assert options["current"] == {"provider": "gui", "model": "gui-model"}

    restarted = build_service(tmp_path, config_path=config)
    assert restarted.engine.router.config.providers["gui"].base_url == "http://127.0.0.1:33434/v1"
    assert restarted.model_options()["current"] == {"provider": "gui", "model": "gui-model"}
    assert {row["provider"] for row in restarted.model_options()["options"]} == {"local-a", "local-b"}
    source_record = json.loads(
        (tmp_path / ".agent-coach" / "model-source.json").read_text(encoding="utf-8")
    )
    assert source_record == {"config_path": str(config.resolve())}
    implicit_restart = build_service(tmp_path)
    assert implicit_restart.config_path == config.resolve()
    assert implicit_restart.model_options()["current"] == {"provider": "gui", "model": "gui-model"}
    assert {row["provider"] for row in implicit_restart.model_options()["options"]} == {"local-a", "local-b"}

    selected = service.select_model({"provider": "local-a", "model": "alpha"})
    assert selected["current"] == {"provider": "local-a", "model": "alpha"}
    assert selected["current_profile_id"] is None
    assert {row["provider"] for row in service.model_options()["options"]} == {"local-a", "local-b"}


def test_delete_requires_confirmation_and_cannot_delete_active_profile(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    service.save_model_settings(_draft(model="one", display_name="One"))
    profile_one = service.profiles_store.active_id()
    service.save_model_settings(_draft(model="two", display_name="Two"))
    profile_two = service.profiles_store.active_id()
    with pytest.raises(ValueError):
        service.delete_model_profile({"profile_id": profile_one})
    deleted = service.delete_model_profile({"profile_id": profile_one, "confirm": True})
    assert deleted["current_profile_id"] == profile_two
    with pytest.raises(SettingsError):
        service.delete_model_profile({"profile_id": profile_two, "confirm": True})


def test_profile_delete_http_route_returns_public_catalog(settings_server):
    service, server = settings_server
    status, _ = _request(
        service,
        server,
        "POST",
        "/api/settings/models",
        _draft(model="one", display_name="One"),
    )
    assert status == 200
    status, _ = _request(
        service,
        server,
        "POST",
        "/api/settings/models",
        _draft(model="two", display_name="Two"),
    )
    assert status == 200
    catalog = service.model_options()["saved_profiles"]
    old_id = next(row["profile_id"] for row in catalog if row["display_name"] == "One")
    status, payload = _request(
        service,
        server,
        "POST",
        "/api/settings/models/delete",
        {"profile_id": old_id, "confirm": True},
    )
    assert status == 200
    assert payload["ok"] is True
    assert all(row["profile_id"] != old_id for row in payload["saved_profiles"])
