from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import packaged_launcher


def test_defaults_keep_saved_gui_settings_and_skip_demo_or_template(tmp_path, monkeypatch):
    root = tmp_path / "JobCoachDesktop"
    root.mkdir()
    workspace = root / "work"
    monkeypatch.setattr(packaged_launcher, "_application_root", lambda: root)

    # A fresh portable folder can still use its key-free template/demo path.
    (root / "config").mkdir()
    (root / "config" / "providers.yaml").write_text("providers: {}\n", encoding="utf-8")
    fresh = packaged_launcher._default_arguments(["--workspace", str(workspace)])
    assert "--demo" not in fresh
    assert str(root / "config" / "providers.yaml") in fresh

    # Once the GUI has saved settings, a restart must let WebService load them
    # without a stale --config or --demo default winning.
    settings = workspace / ".agent-coach"
    settings.mkdir(parents=True)
    (settings / "model-settings.json").write_text("{}", encoding="utf-8")
    restored = packaged_launcher._default_arguments(["--workspace", str(workspace)])
    assert "--demo" not in restored
    assert "--config" not in restored

    # An explicit YAML source is remembered separately so a packaged restart
    # can keep its declared provider/fallback options while restoring GUI
    # profile settings.
    remembered = root / "selected-providers.yaml"
    remembered.write_text("providers: {}\n", encoding="utf-8")
    (settings / "model-source.json").write_text(
        '{"config_path": "' + str(remembered).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    restored_with_source = packaged_launcher._default_arguments(["--workspace", str(workspace)])
    config_index = restored_with_source.index("--config")
    assert restored_with_source[config_index + 1] == str(remembered.resolve())


def test_redact_diagnostic_hides_credentials():
    value = "api_key=sk-live-123456789 password=secret Bearer abc.def/ghi"
    cleaned = packaged_launcher._redact_diagnostic(value)
    assert "sk-live-123456789" not in cleaned
    assert "secret" not in cleaned
    assert "abc.def/ghi" not in cleaned
    assert "[已隐藏凭据]" in cleaned


class _FakeEvent:
    def __init__(self):
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self

    def fire(self):
        if self.callback is not None:
            self.callback()


class _FakeWindow:
    def __init__(self):
        self.events = type("Events", (), {"closing": _FakeEvent()})()
        self.destroyed = False
        self.restored = 0
        self.shown = 0

    def destroy(self):
        self.destroyed = True

    def restore(self):
        self.restored += 1

    def show(self):
        self.shown += 1


class _FakeWebview:
    def __init__(self):
        self.window = None
        self.create_args = None
        self.start_kwargs = None

    def create_window(self, *args, **kwargs):
        self.create_args = (args, kwargs)
        self.window = _FakeWindow()
        return self.window

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        self.window.events.closing.fire()


class _PracticeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, *args, **kwargs):
        window = _FakeWindow()
        self.windows.append((args, kwargs, window))
        return window


class _FakeServer:
    server_address = ("127.0.0.1", 43123)

    def __init__(self):
        self.stop = threading.Event()
        self.shutdown_called = False
        self.close_called = False

    def serve_forever(self):
        self.stop.wait(timeout=2)

    def shutdown(self):
        self.shutdown_called = True
        self.stop.set()

    def server_close(self):
        self.close_called = True


def test_stale_practice_close_does_not_remove_replacement_window():
    base = "http://127.0.0.1:43123/#token=abc"
    api = packaged_launcher._PracticeWindowApi(_PracticeWebview(), base)
    key = api._scope_key(("A", "owner/repo"))
    old_child = _FakeWindow()
    new_child = _FakeWindow()
    api._windows[key] = new_child
    api._window_urls[key] = "fresh-url"

    api._forget(key, old_child)
    assert api._windows[key] is new_child
    assert api._window_urls[key] == "fresh-url"

    api._forget(key, new_child)
    assert key not in api._windows
    assert key not in api._window_urls


def test_native_window_uses_edgechromium_and_stops_server(monkeypatch, tmp_path):
    fake_webview = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    server = _FakeServer()

    assert packaged_launcher._run_native(server, "http://127.0.0.1:43123/#token=x", tmp_path / "profile") == 0
    assert fake_webview.create_args[0][0] == packaged_launcher.APP_TITLE
    native_api = fake_webview.create_args[1]["js_api"]
    assert isinstance(native_api, packaged_launcher._NativeBridgeApi)
    assert native_api._main_window is fake_webview.window
    assert fake_webview.start_kwargs == {
        "gui": "edgechromium",
        "private_mode": False,
        "storage_path": str(tmp_path / "profile"),
    }
    # Give the asynchronous shutdown callback a scheduling opportunity.
    deadline = time.monotonic() + 2
    while not server.shutdown_called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.shutdown_called
    assert server.close_called


class _PickerWindow:
    def __init__(self, selection):
        self.selection = selection
        self.dialog_calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.dialog_calls.append((args, kwargs))
        return self.selection


class _PickerWebview:
    FOLDER_DIALOG = 77
    OPEN_DIALOG = 88


def test_native_workspace_picker_only_opens_after_explicit_bridge_call(tmp_path):
    target = tmp_path / "selected-workspace"
    target.mkdir()
    webview = _PickerWebview()
    window = _PickerWindow([str(target)])
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")

    # Binding the main window must not inspect it or open a dialog.  The only
    # filesystem operation happens after the caller invokes the bridge method.
    api.bind_main_window(window)
    assert window.dialog_calls == []

    result = api.pick_workspace_folder(str(target))

    assert result == {
        "ok": True,
        "available": True,
        "selected": True,
        "cancelled": False,
        "path": str(target.resolve()),
    }
    assert window.dialog_calls == [
        ((webview.FOLDER_DIALOG,), {"directory": str(target), "allow_multiple": False})
    ]


def test_native_workspace_picker_reports_cancel_and_browser_fallback(tmp_path):
    webview = _PickerWebview()
    window = _PickerWindow([])
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    api.bind_main_window(window)

    assert api.select_workspace_folder() == {
        "ok": True,
        "available": True,
        "selected": False,
        "cancelled": True,
        "path": "",
    }
    assert api.workspace_picker() == {
        "ok": True,
        "available": True,
        "selected": False,
        "cancelled": True,
        "path": "",
    }

    unavailable = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    result = unavailable.pick_workspace_folder()
    assert result["ok"] is False
    assert result["available"] is False
    assert result["selected"] is False


def test_native_workspace_picker_accepts_string_tuple_and_rejects_invalid_path(tmp_path):
    target = tmp_path / "tuple-workspace"
    target.mkdir()
    webview = _PickerWebview()
    window = _PickerWindow((str(target),))
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    api.bind_main_window(window)

    selected = api.pick_workspace_folder()
    assert selected["selected"] is True
    assert selected["path"] == str(target.resolve())

    invalid = _PickerWindow([str(tmp_path / "does-not-exist")])
    api.bind_main_window(invalid)
    rejected = api.pick_workspace_folder()
    assert rejected["ok"] is False
    assert rejected["available"] is True
    assert rejected["selected"] is False
    assert "文件夹" in rejected["error"]


def test_native_local_path_picker_returns_selected_file_without_importing_or_validating(tmp_path):
    selected_file = tmp_path / "notes" / "career.log"
    selected_file.parent.mkdir()
    webview = _PickerWebview()
    window = _PickerWindow([str(selected_file)])
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")

    api.bind_main_window(window)
    assert window.dialog_calls == []

    result = api.pick_local_file(str(selected_file.parent))

    assert result == {
        "ok": True,
        "available": True,
        "selected": True,
        "cancelled": False,
        "path": str(selected_file),
    }
    assert window.dialog_calls == [
        ((webview.OPEN_DIALOG,), {"directory": str(selected_file.parent), "allow_multiple": False})
    ]


def test_native_local_path_picker_supports_folder_alias_and_keeps_http_validation_boundary(tmp_path):
    selected_folder = tmp_path / "Obsidian Vault"
    webview = _PickerWebview()
    window = _PickerWindow([str(selected_folder)])
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    api.bind_main_window(window)

    # The bridge returns the user's explicit selection as-is.  Existence,
    # symlink, sensitive-path, and import-size checks remain in import_local_path.
    result = api.pick_local_path({"kind": "folder"})

    assert result == {
        "ok": True,
        "available": True,
        "selected": True,
        "cancelled": False,
        "path": str(selected_folder),
    }
    assert window.dialog_calls == [
        ((webview.FOLDER_DIALOG,), {"directory": "", "allow_multiple": False})
    ]
    window.selection = []
    assert api.pick_local_folder() == {
        "ok": True,
        "available": True,
        "selected": False,
        "cancelled": True,
        "path": "",
    }


def test_native_local_path_picker_reports_browser_fallback_and_invalid_kind():
    webview = _PickerWebview()
    unavailable = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    unavailable_result = unavailable.pick_local_path(kind="folder")
    assert unavailable_result["ok"] is False
    assert unavailable_result["available"] is False
    assert unavailable_result["selected"] is False

    window = _PickerWindow([])
    api = packaged_launcher._NativeBridgeApi(webview, "http://127.0.0.1:43123/#token=x")
    api.bind_main_window(window)
    invalid = api.pick_local_path(kind="anything")
    assert invalid == {
        "ok": False,
        "available": True,
        "selected": False,
        "cancelled": False,
        "path": "",
        "error": "本地选择类型无效，请选择文件或文件夹。",
    }
    assert window.dialog_calls == []


def test_practice_bridge_is_exact_origin_scoped_and_reuses_windows():
    base = "http://127.0.0.1:43123/#token=abc"
    first = "http://127.0.0.1:43123/?view=practice&session_id=A&project_key=owner%2Frepo#token=abc"
    refreshed = first.replace("#token=abc", "#token=rotated")
    second = "http://127.0.0.1:43123/?view=practice&session_id=A&project_key=other%2Frepo#token=abc"
    webview = _PracticeWebview()
    api = packaged_launcher._PracticeWindowApi(webview, base)

    assert api.open_practice_window(first) == {"opened": True}
    assert api.open_practice_window(first) == {"opened": True, "reused": True}
    assert len(webview.windows) == 1
    assert api.open_practice_window(refreshed) == {"opened": True, "replaced": True}
    assert len(webview.windows) == 2
    assert webview.windows[0][2].destroyed
    assert api.open_practice_window(second) == {"opened": True}
    assert len(webview.windows) == 3
    assert api.close_practice_window("A", "owner/repo") == {"closed": True}
    assert webview.windows[1][2].destroyed
    assert api.close_practice_window("A", "other/repo") == {"closed": True}
    assert webview.windows[2][2].destroyed
    assert not packaged_launcher._is_safe_practice_url(first.replace("43123", "43124"), base)
    assert not packaged_launcher._is_safe_practice_url(first.replace("view=practice", "view=main"), base)
    assert not packaged_launcher._is_safe_practice_url(first.replace("project_key=owner%2Frepo", "token=secret&project_key=owner%2Frepo"), base)


def test_interview_bridge_is_exact_origin_scoped_and_reuses_windows():
    base = "http://127.0.0.1:43123/#token=abc"
    first = "http://127.0.0.1:43123/?view=interview&session_id=A#token=abc"
    refreshed = first.replace("#token=abc", "#token=rotated")
    second = first.replace("session_id=A", "session_id=B")
    webview = _PracticeWebview()
    api = packaged_launcher._InterviewWindowApi(webview, base)

    assert packaged_launcher._interview_scope_from_url(first) == "A"
    assert api.open_interview_window(first) == {"opened": True}
    assert api.open_interview_window(first) == {"opened": True, "reused": True}
    assert len(webview.windows) == 1
    assert api.open_interview_window(refreshed) == {"opened": True, "replaced": True}
    assert len(webview.windows) == 2
    assert webview.windows[0][2].destroyed
    assert api.open_interview_window(second) == {"opened": True}
    assert len(webview.windows) == 3
    assert api.close_interview_window("A") == {"closed": True}
    assert webview.windows[1][2].destroyed
    assert api.close_interview_window("B") == {"closed": True}
    assert webview.windows[2][2].destroyed
    assert not packaged_launcher._is_safe_interview_url(first.replace("43123", "43124"), base)
    assert not packaged_launcher._is_safe_interview_url(first.replace("view=interview", "view=main"), base)
    assert not packaged_launcher._is_safe_interview_url(first.replace("session_id=A", "token=secret&session_id=A"), base)


def test_headless_windowed_mode_leaves_local_discovery_marker(tmp_path, monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(sys, "stdout", None)
    assert packaged_launcher._serve_headless(server, "http://127.0.0.1:43123/#token=x", tmp_path) == 0
    marker = tmp_path / ".agent-coach" / "headless-url.txt"
    assert marker.read_text(encoding="utf-8").strip().endswith("#token=x")
    assert server.close_called
