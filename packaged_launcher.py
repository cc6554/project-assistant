"""Native Windows launcher for the 校招雷达 workbench.

The browser workbench and the native workbench intentionally share the same
HTTP service.  This entry point starts that service on a loopback-only,
ephemeral port and puts the URL in a pywebview EdgeChromium window.  It never
calls :mod:`webbrowser`, so a double-clicked executable does not open the
user's default browser.

``--headless`` is useful for smoke tests and CI.  ``--no-browser`` is retained
as a compatibility alias for the old browser launcher; both flags run the
loopback service without opening any UI and without invoking a system browser.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import parse_qsl, urlsplit


# PyInstaller resolves imports from the bundle, while a source-checkout smoke
# test needs the package's ``src`` directory just like run-web.py does.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


APP_TITLE = "校招雷达"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 860
MIN_WIDTH = 960
MIN_HEIGHT = 640
MODEL_SOURCE_FILENAME = "model-source.json"


def _application_root() -> Path:
    """Return the portable root used for ``work`` and ``config``."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _has_option(argv: Sequence[str], name: str) -> bool:
    prefix = f"{name}="
    return any(item == name or item.startswith(prefix) for item in argv)


def _option_value(argv: Sequence[str], name: str) -> str | None:
    """Read a simple ``--name value``/``--name=value`` option for defaults."""

    prefix = f"{name}="
    for index, item in enumerate(argv):
        if item.startswith(prefix):
            return item[len(prefix) :]
        if item == name and index + 1 < len(argv):
            return str(argv[index + 1])
    return None


def _remembered_config(workspace: Path) -> Path | None:
    """Return a previously explicit YAML path without reading credentials."""

    source = workspace / ".agent-coach" / MODEL_SOURCE_FILENAME
    try:
        import json

        payload = json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return None
    candidate = payload.get("config_path") if isinstance(payload, dict) else None
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate.encode("utf-8")) > 4096:
        return None
    path = Path(candidate).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    return path if path.is_file() else None


def _default_arguments(argv: Sequence[str]) -> list[str]:
    """Add portable defaults without changing explicit caller arguments."""

    root = _application_root()
    supplied = list(argv)
    defaults: list[str] = []
    if not _has_option(supplied, "--workspace"):
        defaults.extend(("--workspace", str(root / "work")))

    if not _has_option(supplied, "--config") and "--demo" not in supplied:
        # GUI settings are workspace-scoped and take precedence over the
        # portable config template.  Adding ``--demo`` on restart would make
        # a saved model configuration unreachable, so only use the historical
        # config/demo defaults before the first settings save.
        workspace_value = _option_value(supplied, "--workspace")
        workspace = Path(workspace_value) if workspace_value else root / "work"
        settings_path = workspace / ".agent-coach" / "model-settings.json"
        remembered_config = _remembered_config(workspace)
        if remembered_config is not None:
            defaults.extend(("--config", str(remembered_config)))
        elif not settings_path.is_file():
            config = root / "config" / "providers.yaml"
            if config.is_file():
                defaults.extend(("--config", str(config)))
            else:
                # A newly unpacked directory can still be opened immediately.
                # In demo mode no provider is contacted and no network is
                # required; explicit --demo remains available for acceptance.
                defaults.append("--demo")

    if not _has_option(supplied, "--port"):
        # Let Windows choose a free loopback port.  The native window receives
        # the selected port in its URL after the socket has been bound.
        defaults.extend(("--port", "0"))
    return defaults + supplied


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the native 校招雷达 workbench")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--config", default=None)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-skills", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="compatibility alias: serve the loopback API without opening a UI",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="serve the loopback API without creating a native window",
    )
    return parser


def _load_web_runtime() -> tuple[Any, Callable[..., Any]]:
    """Load service constructors lazily so ``--headless`` stays minimal."""

    from agent_coach.web import build_service, make_server

    return build_service, make_server


def _create_server(args: argparse.Namespace) -> tuple[Any, Any, str]:
    build_service, make_server = _load_web_runtime()
    service = build_service(
        args.workspace,
        config_path=args.config,
        demo=args.demo,
        no_skills=args.no_skills,
    )
    server = make_server(service, host="127.0.0.1", port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/#token={service.token}"
    return service, server, url


def _start_server_thread(server: Any) -> threading.Thread:
    def serve() -> None:
        server.serve_forever()

    thread = threading.Thread(target=serve, name="agent-coach-http", daemon=True)
    thread.start()
    return thread


def _stop_server(server: Any, thread: threading.Thread | None = None) -> None:
    """Stop an HTTP server once, without blocking a GUI event callback."""

    state = getattr(server, "_agent_coach_shutdown_state", None)
    if state is None:
        state = threading.Event()
        try:
            setattr(server, "_agent_coach_shutdown_state", state)
        except Exception:
            # A test double may use slots.  Calling shutdown more than once is
            # harmless for the real ThreadingHTTPServer, so continue below.
            state = None
    if state is not None and state.is_set():
        return
    if state is not None:
        state.set()

    def shutdown() -> None:
        try:
            server.shutdown()
        except Exception:
            # Cleanup should not mask the original GUI or engine exception.
            return
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # ``closing`` is allowed to run synchronously on some pywebview backends;
    # never wait for ``serve_forever`` from the GUI thread itself.
    threading.Thread(target=shutdown, name="agent-coach-http-shutdown", daemon=True).start()


def _serve_headless(server: Any, url: str, workspace: Path | None = None) -> int:
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            print(url, file=stream, flush=True)
        except (AttributeError, OSError):
            stream = None
    if stream is None and workspace is not None:
        # ``--headless`` is also accepted by the windowed exe, where stdout is
        # commonly None.  Leave a local-only discovery file for smoke scripts.
        try:
            marker = workspace / ".agent-coach" / "headless-url.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(url + "\n", encoding="utf-8")
        except OSError:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            server.server_close()
        except Exception:
            pass
    return 0


def _practice_scope_from_url(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(str(value or ""))
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        values: dict[str, list[str]] = {}
        for key, item in pairs:
            values.setdefault(str(key).strip().lower(), []).append(str(item).strip())
        if values.get("view") != ["practice"] or len(values.get("session_id", [])) != 1 or len(values.get("project_key", [])) != 1:
            return None
        session_id = values["session_id"][0]
        project_key = values["project_key"][0]
        if not session_id or not project_key or len(session_id) > 256 or len(project_key) > 1024:
            return None
        if any(ord(char) < 0x20 for char in f"{session_id}{project_key}"):
            return None
        return session_id, project_key
    except ValueError:
        return None


def _is_safe_practice_url(value: str, base_url: str | None = None) -> bool:
    """Allow child windows to load only the exact loopback workbench route.

    The practice child receives its session/project scope in the query and the
    short lived API credential in the URL fragment.  Rejecting credential-like
    query keys protects native window diagnostics and referrer metadata if a
    stale or hand-written browser call reaches the bridge.
    """

    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        if parsed.username or parsed.password:
            return False
        if base_url:
            base = urlsplit(str(base_url))
            if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port):
                return False
            if (parsed.path or "/") != (base.path or "/"):
                return False
        blocked = {"token", "api_key", "apikey", "access_token", "authorization", "secret", "password"}
        if any(str(key).strip().lower() in blocked for key, _value in parse_qsl(parsed.query, keep_blank_values=True)):
            return False
        return _practice_scope_from_url(value) is not None
    except ValueError:
        return False


class _PracticeWindowApi:
    """Small pywebview bridge for independent practice windows.

    No model or API credential is stored here.  The child URL is validated and
    then loaded in a sibling WebView, so closing/minimizing that window cannot
    stop the shared HTTP worker or a running job.
    """

    def __init__(self, webview: Any, base_url: str):
        self._webview = webview
        self._base_url = str(base_url or "")
        self._windows: dict[str, Any] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _scope_key(scope: tuple[str, str]) -> str:
        return f"{scope[0]}\x00{scope[1]}"

    def _forget(self, key: str, child: Any) -> None:
        with self._lock:
            if self._windows.get(key) is child:
                self._windows.pop(key, None)

    def _close(self, key: str) -> dict[str, bool]:
        with self._lock:
            child = self._windows.pop(key, None)
        if child is None:
            return {"closed": False}
        try:
            destroy = getattr(child, "destroy", None)
            if callable(destroy):
                destroy()
            else:
                hide = getattr(child, "hide", None)
                if callable(hide):
                    hide()
        except Exception:
            return {"closed": False}
        return {"closed": True}

    def open_practice_window(self, url: str) -> dict[str, bool]:
        scope = _practice_scope_from_url(url)
        if scope is None or not _is_safe_practice_url(url, self._base_url):
            return {"opened": False}
        key = self._scope_key(scope)
        with self._lock:
            existing = self._windows.get(key)
        if existing is not None:
            try:
                restore = getattr(existing, "restore", None)
                if callable(restore):
                    restore()
                show = getattr(existing, "show", None)
                if callable(show):
                    show()
                return {"opened": True, "reused": True}
            except Exception:
                self._forget(key, existing)
        try:
            child = self._webview.create_window(
                "校招雷达 · Agent 实操",
                url=str(url),
                width=1160,
                height=860,
                min_size=(960, 640),
                text_select=True,
                confirm_close=False,
                background_color="#F7F8FA",
                js_api=_PracticeWindowChildApi(self, key),
            )
        except Exception:
            return {"opened": False}
        with self._lock:
            self._windows[key] = child
        events = getattr(child, "events", None)
        closing = getattr(events, "closing", None)
        if closing is not None:
            closing += lambda *_args, _key=key, _child=child: self._forget(_key, _child)
        return {"opened": True}

    def close_practice_window(self, session_id: str = "", project_key: str = "") -> dict[str, bool]:
        session = str(session_id or "").strip()
        project = str(project_key or "").strip()
        return self._close(self._scope_key((session, project))) if session and project else {"closed": False}


class _PracticeWindowChildApi:
    """Per-child bridge so close requests cannot destroy another scope."""

    def __init__(self, parent: _PracticeWindowApi, key: str):
        self._parent = parent
        self._key = key

    def close_practice_window(self, *_args: Any, **_kwargs: Any) -> dict[str, bool]:
        return self._parent._close(self._key)


def _run_native(server: Any, url: str, storage_path: Path | None = None) -> int:
    """Run the native EdgeChromium window and close the API server with it."""

    try:
        import webview  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "原生窗口依赖未安装。请在构建环境执行 "
            "build_windows_exe.ps1 -InstallDesktopDeps（需要网络），"
            "并确认 Windows 已安装 WebView2 Runtime。"
        ) from exc

    server_thread = _start_server_thread(server)
    stopped = False
    stop_lock = threading.Lock()

    def request_close(*_args: Any, **_kwargs: Any) -> None:
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        _stop_server(server, server_thread)

    api = _PracticeWindowApi(webview, url)
    try:
        window = webview.create_window(
            APP_TITLE,
            url=url,
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            text_select=True,
            confirm_close=False,
            background_color="#F7F8FA",
            js_api=api,
        )
        # pywebview exposes an event container on all supported Windows
        # backends.  Keep this defensive branch for headless test doubles.
        events = getattr(window, "events", None)
        closing = getattr(events, "closing", None)
        if closing is not None:
            closing += request_close

        # ``edgechromium`` is the mature WebView2 backend on Windows.  The
        # runtime is supplied by Windows/Edge, so it is not embedded in the
        # application directory.
        start_kwargs: dict[str, Any] = {"gui": "edgechromium"}
        if storage_path is not None:
            # Keep WebView2's profile/cache beside the portable workspace.  A
            # default profile can otherwise land under the small C: volume.
            storage_path.mkdir(parents=True, exist_ok=True)
            start_kwargs.update(private_mode=False, storage_path=str(storage_path))
        webview.start(**start_kwargs)
        return 0
    finally:
        request_close()
        try:
            server.server_close()
        except Exception:
            pass


_REDACTION_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|credential|authorization)"
        r"(\s*[:=]\s*)[^\s,;]+"
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)sk[-_][A-Za-z0-9_-]{8,}"),
)


def _redact_diagnostic(value: str) -> str:
    text = str(value or "").replace("\x00", "")
    for index, pattern in enumerate(_REDACTION_PATTERNS):
        if index == 0:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[已隐藏凭据]", text)
        else:
            text = pattern.sub("[已隐藏凭据]", text)
    return text[:20000]


def _failure_log_path() -> Path:
    root = _application_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root / "agent-coach-launcher.log"
    except OSError:
        fallback_root = Path(tempfile.gettempdir()) / "AgentCoachDesktop"
        fallback_root.mkdir(parents=True, exist_ok=True)
        return fallback_root / "agent-coach-launcher.log"


def _write_failure(exc: BaseException) -> Path:
    path = _failure_log_path()
    try:
        diagnostic = _redact_diagnostic(traceback.format_exc())
        path.write_text(diagnostic, encoding="utf-8")
    except OSError:
        # The dialog still reports the attempted path; no secret is printed.
        pass
    return path


def _show_error_dialog(message: str, log_path: Path) -> None:
    """Show a native Windows error dialog without requiring a console."""

    text = _redact_diagnostic(message).strip() or "未知启动错误"
    body = f"{text}\n\n详细错误已写入：{log_path}"
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, body, f"{APP_TITLE} 启动失败", 0x10)
    except Exception:
        # Source-checkout diagnostics still remain visible in a terminal.  A
        # frozen windowed build normally reaches the Win32 dialog above.
        if not getattr(sys, "frozen", False):
            print(body, file=sys.stderr)


def run(argv: Sequence[str] | None = None) -> int:
    effective = _default_arguments(list(sys.argv[1:] if argv is None else argv))
    args = _build_parser().parse_args(effective)
    service, server, url = _create_server(args)
    if args.headless or args.no_browser:
        return _serve_headless(server, url, Path(service.workspace))
    storage_path = Path(service.workspace) / ".agent-coach" / "webview"
    return _run_native(server, url, storage_path)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except BaseException as exc:
        log_path = _write_failure(exc)
        _show_error_dialog(str(exc), log_path)
        if not getattr(sys, "frozen", False):
            traceback.print_exc()
        raise SystemExit(1)
