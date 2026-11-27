from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import threading

import pytest

from agent_coach import github_profiles, integrations
from agent_coach.legacy.tools.credentials import github_token
from agent_coach.settings import SettingsError, protect_key
from agent_coach.web import build_service, make_server


def _request(service, server, method: str, path: str, body=None):
    port = int(server.server_address[1])
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {service.token}",
    }
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    if raw is not None:
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, raw, headers)
    response = client.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    client.close()
    return response.status, payload


@pytest.fixture
def github_server(tmp_path: Path):
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


def _key_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_saved_token_migrates_without_persisting_secret(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AGENT_COACH_GITHUB_TOKEN", raising=False)
    settings_dir = tmp_path / ".agent-coach"
    settings_dir.mkdir()
    legacy_token = "ghp-legacy-profile-token"
    (settings_dir / github_profiles.GITHUB_TOKEN_FILENAME).write_bytes(protect_key(legacy_token))

    catalog = integrations.github_profile_catalog(tmp_path)
    assert catalog["active_profile_id"] == "default"
    assert catalog["has_token"] is True
    assert catalog["profiles"] == [
        {"profile_id": "default", "display_name": "默认 GitHub", "has_token": True}
    ]
    assert integrations.load_github_token(tmp_path) == legacy_token
    metadata = (settings_dir / github_profiles.GITHUB_PROFILES_FILENAME).read_text(encoding="utf-8")
    profile_blob = (settings_dir / github_profiles.GITHUB_PROFILES_DIRNAME / "default.key").read_bytes()
    assert legacy_token not in metadata
    assert legacy_token.encode() not in profile_blob

    restored = github_profiles.GitHubProfilesStore(tmp_path).catalog()
    assert restored == catalog


def test_direct_profile_create_and_select_migrate_legacy_mirror(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AGENT_COACH_GITHUB_TOKEN", raising=False)
    settings_dir = tmp_path / ".agent-coach"
    settings_dir.mkdir()
    legacy_token = "ghp-direct-legacy-token"
    (settings_dir / github_profiles.GITHUB_TOKEN_FILENAME).write_bytes(protect_key(legacy_token))

    created = integrations.save_github_profile(tmp_path, "New profile", "ghp-new-profile")
    assert len(created["profiles"]) == 2
    assert {item["display_name"] for item in created["profiles"]} == {"默认 GitHub", "New profile"}

    # A fresh store can select the migrated default without a preceding GET.
    fresh = tmp_path / "fresh"
    fresh_settings = fresh / ".agent-coach"
    fresh_settings.mkdir(parents=True)
    (fresh_settings / github_profiles.GITHUB_TOKEN_FILENAME).write_bytes(protect_key(legacy_token))
    selected = integrations.select_github_profile(fresh, "default")
    assert selected["active_profile_id"] == "default"
    assert integrations.load_github_token(fresh) == legacy_token


def test_catalog_does_not_import_environment_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "ghp-environment-only")
    catalog = integrations.github_profile_catalog(tmp_path)
    assert catalog == {"profiles": [], "active_profile_id": None, "has_token": False}
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_PROFILES_FILENAME).exists()
    assert integrations.load_github_token(tmp_path) == "ghp-environment-only"


def test_profiles_switch_token_chain_and_restart(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AGENT_COACH_GITHUB_TOKEN", raising=False)
    first = integrations.save_github_profile(tmp_path, "Personal", "ghp-first-profile")
    second = integrations.save_github_profile(tmp_path, "Work", "ghp-second-profile")
    assert second["active_profile_id"] != first["active_profile_id"]
    assert integrations.load_github_token(tmp_path) == "ghp-second-profile"
    selected = integrations.select_github_profile(tmp_path, first["active_profile_id"])
    assert selected["active_profile_id"] == first["active_profile_id"]
    assert integrations.load_github_token(tmp_path) == "ghp-first-profile"
    with integrations.github_token_context(tmp_path):
        assert github_token() == "ghp-first-profile"

    restored = github_profiles.GitHubProfilesStore(tmp_path).catalog()
    assert restored["active_profile_id"] == first["active_profile_id"]
    assert restored["has_token"] is True
    profile_keys = list((tmp_path / ".agent-coach" / github_profiles.GITHUB_PROFILES_DIRNAME).glob("*.key"))
    assert len(profile_keys) == 2
    assert all(b"ghp-" not in path.read_bytes() for path in profile_keys)


def test_profile_edit_keeps_stable_id_and_blank_token_preserves_secret(tmp_path: Path):
    saved = integrations.save_github_profile(tmp_path, "Original", "ghp-edit-secret")
    profile_id = saved["active_profile_id"]
    key_path = (
        tmp_path
        / ".agent-coach"
        / github_profiles.GITHUB_PROFILES_DIRNAME
        / f"{profile_id}.key"
    )
    before_key = _key_digest(key_path)

    edited = integrations.update_github_profile(tmp_path, profile_id, "Renamed", "")
    assert edited["active_profile_id"] == profile_id
    assert edited["profiles"] == [
        {"profile_id": profile_id, "display_name": "Renamed", "has_token": True}
    ]
    assert integrations.load_github_token(tmp_path) == "ghp-edit-secret"
    assert _key_digest(key_path) == before_key
    assert "ghp-edit-secret" not in json.dumps(edited)

    rotated = integrations.update_github_profile(tmp_path, profile_id, None, "ghp-rotated")
    assert rotated["active_profile_id"] == profile_id
    assert integrations.load_github_token(tmp_path) == "ghp-rotated"
    assert _key_digest(key_path) != before_key
    assert "ghp-rotated" not in json.dumps(rotated)


def test_delete_active_profile_selects_first_remaining_token_and_keeps_mirror(tmp_path: Path):
    first = integrations.save_github_profile(tmp_path, "First", "ghp-first")
    second = integrations.save_github_profile(tmp_path, "Second", "ghp-second")
    second_key = (
        tmp_path
        / ".agent-coach"
        / github_profiles.GITHUB_PROFILES_DIRNAME
        / f"{second['active_profile_id']}.key"
    )

    deleted = integrations.delete_github_profile(tmp_path, second["active_profile_id"])
    assert deleted["active_profile_id"] == first["active_profile_id"]
    assert [item["profile_id"] for item in deleted["profiles"]] == [first["active_profile_id"]]
    assert integrations.load_github_token(tmp_path) == "ghp-first"
    assert not second_key.exists()
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_TOKEN_DISABLED_FILENAME).exists()


def test_delete_final_active_profile_leaves_explicit_unconfigured_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "ghp-environment-must-stay-disabled")
    saved = integrations.save_github_profile(tmp_path, "Only", "ghp-only")
    deleted = integrations.delete_github_profile(tmp_path, saved["active_profile_id"])

    assert deleted == {"profiles": [], "active_profile_id": None, "has_token": False}
    assert integrations.load_github_token(tmp_path) is None
    settings_dir = tmp_path / ".agent-coach"
    assert not (settings_dir / github_profiles.GITHUB_TOKEN_FILENAME).exists()
    assert (settings_dir / github_profiles.GITHUB_TOKEN_DISABLED_FILENAME).is_file()
    assert "ghp-only" not in json.dumps(deleted)


def test_delete_non_active_profile_keeps_active_profile(tmp_path: Path):
    first = integrations.save_github_profile(tmp_path, "First", "ghp-first")
    second = integrations.save_github_profile(tmp_path, "Second", "ghp-second")
    deleted = integrations.delete_github_profile(tmp_path, first["active_profile_id"])

    assert deleted["active_profile_id"] == second["active_profile_id"]
    assert [item["profile_id"] for item in deleted["profiles"]] == [second["active_profile_id"]]
    assert integrations.load_github_token(tmp_path) == "ghp-second"


def test_profile_edit_and_delete_roll_back_on_metadata_failure(tmp_path: Path, monkeypatch):
    saved = integrations.save_github_profile(tmp_path, "Original", "ghp-rollback")
    profile_id = saved["active_profile_id"]
    settings_dir = tmp_path / ".agent-coach"
    key_path = settings_dir / github_profiles.GITHUB_PROFILES_DIRNAME / f"{profile_id}.key"
    tracked = [
        settings_dir / github_profiles.GITHUB_PROFILES_FILENAME,
        settings_dir / github_profiles.GITHUB_TOKEN_FILENAME,
        key_path,
    ]
    before = {path: path.read_bytes() for path in tracked}
    original_write_meta = github_profiles.GitHubProfilesStore._write_meta

    def fail_metadata(self, meta):
        del meta
        raise SettingsError("injected metadata failure")

    monkeypatch.setattr(github_profiles.GitHubProfilesStore, "_write_meta", fail_metadata)
    with pytest.raises(integrations.IntegrationError, match="injected metadata failure"):
        integrations.update_github_profile(tmp_path, profile_id, "Changed", "ghp-new")
    assert {path: path.read_bytes() for path in tracked} == before
    assert integrations.load_github_token(tmp_path) == "ghp-rollback"

    monkeypatch.setattr(github_profiles.GitHubProfilesStore, "_write_meta", original_write_meta)
    monkeypatch.setattr(github_profiles.GitHubProfilesStore, "_write_meta", fail_metadata)
    with pytest.raises(integrations.IntegrationError, match="injected metadata failure"):
        integrations.delete_github_profile(tmp_path, profile_id)
    assert {path: path.read_bytes() for path in tracked} == before
    assert integrations.github_profile_catalog(tmp_path)["active_profile_id"] == profile_id
    assert integrations.load_github_token(tmp_path) == "ghp-rollback"


def test_legacy_clear_removes_active_profile_and_suppresses_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "ghp-should-not-return")
    first = integrations.save_github_profile(tmp_path, "Keep", "ghp-keep")
    second = integrations.save_github_profile(tmp_path, "Clear me", "ghp-clear")
    clear_result = integrations.clear_github_token(tmp_path)
    assert clear_result == {"cleared": True, "has_token": False}
    catalog = integrations.github_profile_catalog(tmp_path)
    assert catalog["active_profile_id"] is None
    assert catalog["has_token"] is False
    assert [item["profile_id"] for item in catalog["profiles"]] == [first["active_profile_id"]]
    assert integrations.load_github_token(tmp_path) is None
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_TOKEN_FILENAME).exists()
    assert not (
        tmp_path / ".agent-coach" / github_profiles.GITHUB_PROFILES_DIRNAME / f"{second['active_profile_id']}.key"
    ).exists()
    assert (tmp_path / ".agent-coach" / github_profiles.GITHUB_TOKEN_DISABLED_FILENAME).is_file()

    selected = integrations.select_github_profile(tmp_path, first["active_profile_id"])
    assert selected["has_token"] is True
    assert integrations.load_github_token(tmp_path) == "ghp-keep"
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_TOKEN_DISABLED_FILENAME).exists()


def test_disabled_marker_survives_loader_error_without_environment_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_COACH_GITHUB_TOKEN", "ghp-should-stay-disabled")
    integrations.clear_github_token(tmp_path)

    def fail_loader(_workspace):
        raise SettingsError("injected loader failure")

    monkeypatch.setattr(integrations, "_load_github_profile_token", fail_loader)
    assert integrations.load_github_token(tmp_path) is None


def test_invalid_profile_selection_does_not_change_active_files(tmp_path: Path):
    saved = integrations.save_github_profile(tmp_path, "Only", "ghp-only")
    settings_dir = tmp_path / ".agent-coach"
    metadata_path = settings_dir / github_profiles.GITHUB_PROFILES_FILENAME
    mirror_path = settings_dir / github_profiles.GITHUB_TOKEN_FILENAME
    before_metadata = _key_digest(metadata_path)
    before_mirror = _key_digest(mirror_path)
    with pytest.raises(KeyError):
        integrations.select_github_profile(tmp_path, "missing-profile")
    assert _key_digest(metadata_path) == before_metadata
    assert _key_digest(mirror_path) == before_mirror
    assert integrations.github_profile_catalog(tmp_path)["active_profile_id"] == saved["active_profile_id"]


def test_empty_profile_values_are_rejected_without_files(tmp_path: Path):
    with pytest.raises(integrations.IntegrationError):
        integrations.save_github_profile(tmp_path, "", "")
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_PROFILES_FILENAME).exists()
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_TOKEN_FILENAME).exists()


def test_empty_profile_id_is_rejected_without_generating_one(tmp_path: Path):
    with pytest.raises(integrations.IntegrationError):
        integrations.select_github_profile(tmp_path, "")
    assert not (tmp_path / ".agent-coach" / github_profiles.GITHUB_PROFILES_FILENAME).exists()


def test_profile_write_rolls_back_all_files_on_metadata_failure(tmp_path: Path, monkeypatch):
    original = github_profiles.GitHubProfilesStore._write_meta

    def fail_metadata(self, meta):
        del meta
        raise SettingsError("injected metadata failure")

    monkeypatch.setattr(github_profiles.GitHubProfilesStore, "_write_meta", fail_metadata)
    with pytest.raises(SettingsError, match="injected metadata failure"):
        github_profiles.GitHubProfilesStore(tmp_path).create_profile("Broken", "ghp-transaction")
    monkeypatch.setattr(github_profiles.GitHubProfilesStore, "_write_meta", original)
    settings_dir = tmp_path / ".agent-coach"
    assert not (settings_dir / github_profiles.GITHUB_PROFILES_FILENAME).exists()
    assert not (settings_dir / github_profiles.GITHUB_TOKEN_FILENAME).exists()
    assert not list((settings_dir / github_profiles.GITHUB_PROFILES_DIRNAME).glob("*.key"))


def test_github_profile_http_routes_return_catalog_without_secrets(github_server):
    service, server = github_server
    status, payload = _request(service, server, "GET", "/api/settings/github-profiles")
    assert status == 200
    assert payload == {"profiles": [], "active_profile_id": None, "has_token": False}

    status, created = _request(
        service,
        server,
        "POST",
        "/api/settings/github-profiles",
        {"display_name": "Work", "token": "ghp-http-secret"},
    )
    assert status == 200
    assert created["has_token"] is True
    assert created["profiles"][0]["display_name"] == "Work"
    assert "ghp-http-secret" not in json.dumps(created)

    status, selected = _request(
        service,
        server,
        "POST",
        "/api/settings/github-profiles/select",
        {"profile_id": created["active_profile_id"]},
    )
    assert status == 200
    assert selected == created

    status, legacy = _request(service, server, "GET", "/api/settings/github-token")
    assert status == 200 and legacy == {"has_token": True}


def test_github_profile_http_edit_and_delete_routes(github_server):
    service, server = github_server
    status, first = _request(
        service,
        server,
        "POST",
        "/api/settings/github-profiles",
        {"display_name": "First", "token": "ghp-http-first"},
    )
    assert status == 200
    first_id = first["active_profile_id"]
    status, second = _request(
        service,
        server,
        "POST",
        "/api/settings/github-profiles",
        {"display_name": "Second", "token": "ghp-http-second"},
    )
    assert status == 200
    second_id = second["active_profile_id"]

    status, edited = _request(
        service,
        server,
        "PATCH",
        "/api/settings/github-profiles",
        {"profile_id": second_id, "display_name": "Renamed", "token": ""},
    )
    assert status == 200
    assert edited["active_profile_id"] == second_id
    assert edited["profiles"][-1]["profile_id"] == second_id
    assert edited["profiles"][-1]["display_name"] == "Renamed"
    assert "ghp-http-second" not in json.dumps(edited)

    status, deleted = _request(
        service,
        server,
        "DELETE",
        f"/api/settings/github-profiles/{second_id}",
    )
    assert status == 200
    assert deleted["active_profile_id"] == first_id
    assert integrations.load_github_token(service.workspace) == "ghp-http-first"
