from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import packaged_launcher


def test_defaults_keep_saved_gui_settings_and_skip_demo_or_template(tmp_path, monkeypatch):
    root = tmp_path / "AgentCoachDesktop"
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


def test_native_window_uses_edgechromium_and_stops_server(monkeypatch, tmp_path):
    fake_webview = _FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    server = _FakeServer()

    assert packaged_launcher._run_native(server, "http://127.0.0.1:43123/#token=x", tmp_path / "profile") == 0
    assert fake_webview.create_args[0][0] == packaged_launcher.APP_TITLE
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
def test_practice_bridge_is_exact_origin_scoped_and_reuses_windows():
    base = "http://127.0.0.1:43123/#token=abc"
    first = "http://127.0.0.1:43123/?view=practice&session_id=A&project_key=owner%2Frepo#token=abc"
    second = "http://127.0.0.1:43123/?view=practice&session_id=A&project_key=other%2Frepo#token=abc"
    webview = _PracticeWebview()
    api = packaged_launcher._PracticeWindowApi(webview, base)

    assert api.open_practice_window(first) == {"opened": True}
    assert api.open_practice_window(first) == {"opened": True, "reused": True}
    assert len(webview.windows) == 1
    assert api.open_practice_window(second) == {"opened": True}
    assert len(webview.windows) == 2
    assert api.close_practice_window("A", "owner/repo") == {"closed": True}
    assert webview.windows[0][2].destroyed
    assert api.close_practice_window("A", "other/repo") == {"closed": True}
    assert not packaged_launcher._is_safe_practice_url(first.replace("43123", "43124"), base)
    assert not packaged_launcher._is_safe_practice_url(first.replace("view=practice", "view=main"), base)
    assert not packaged_launcher._is_safe_practice_url(first.replace("project_key=owner%2Frepo", "token=secret&project_key=owner%2Frepo"), base)


def test_headless_windowed_mode_leaves_local_discovery_marker(tmp_path, monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(sys, "stdout", None)
    assert packaged_launcher._serve_headless(server, "http://127.0.0.1:43123/#token=x", tmp_path) == 0
    marker = tmp_path / ".agent-coach" / "headless-url.txt"
    assert marker.read_text(encoding="utf-8").strip().endswith("#token=x")
    assert server.close_called
