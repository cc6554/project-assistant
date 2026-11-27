"""Native Windows launcher for the 简历项目助手 workbench.

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
import json
import os
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

from agent_coach.branding import PRODUCT_NAME

APP_TITLE = PRODUCT_NAME
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 860
MIN_WIDTH = 960
MIN_HEIGHT = 640
MODEL_SOURCE_FILENAME = "model-source.json"
WORKSPACE_SELECTION_FILENAME = "workspace-selection.json"


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


def _workspace_selection_marker(root: Path | None = None) -> Path:
    """Return the portable, non-sensitive workspace selection marker."""

    return (root or _application_root()) / ".agent-coach" / WORKSPACE_SELECTION_FILENAME


def _remembered_workspace(root: Path | None = None) -> Path | None:
    """Read the last explicit workspace chosen from the native UI."""

    try:
        from agent_coach.workspace import read_selection_marker

        return read_selection_marker(_workspace_selection_marker(root))
    except Exception:
        # Startup must remain usable if an older portable directory has a
        # malformed marker or the source package is being diagnosed.
        return None


def _default_arguments(argv: Sequence[str]) -> list[str]:
    """Add portable defaults without changing explicit caller arguments."""

    root = _application_root()
    supplied = list(argv)
    defaults: list[str] = []
    if not _has_option(supplied, "--workspace"):
        remembered_workspace = _remembered_workspace(root)
        defaults.extend(("--workspace", str(remembered_workspace or (root / "work"))))

    if not _has_option(supplied, "--config") and "--demo" not in supplied:
        # GUI settings are workspace-scoped and take precedence over the
        # portable config template.  Adding ``--demo`` on restart would make
        # a saved model configuration unreachable, so only use the historical
        # config/demo defaults before the first settings save.
        workspace_value = _option_value(supplied, "--workspace")
        workspace = Path(workspace_value) if workspace_value else (_remembered_workspace(root) or root / "work")
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
    parser = argparse.ArgumentParser(description=f"Run the native {PRODUCT_NAME} workbench")
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


def _create_server(
    args: argparse.Namespace,
    *,
    demo_explicit: bool | None = None,
) -> tuple[Any, Any, str]:
    build_service, make_server = _load_web_runtime()
    service = build_service(
        args.workspace,
        config_path=args.config,
        demo=args.demo,
        demo_explicit=demo_explicit,
        no_skills=args.no_skills,
        workspace_marker=_workspace_selection_marker(_application_root()),
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


def _interview_scope_from_url(value: str) -> str | None:
    """Return the fixed session scope for a native interview child URL."""

    try:
        parsed = urlsplit(str(value or ""))
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        values: dict[str, list[str]] = {}
        for key, item in pairs:
            values.setdefault(str(key).strip().lower(), []).append(str(item).strip())
        if values.get("view") != ["interview"] or len(values.get("session_id", [])) != 1:
            return None
        session_id = values["session_id"][0]
        if not session_id or len(session_id) > 256 or any(ord(char) < 0x20 for char in session_id):
            return None
        return session_id
    except ValueError:
        return None


def _is_safe_interview_url(value: str, base_url: str | None = None) -> bool:
    """Allow native child windows to load only a same-origin interview view."""

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
        return _interview_scope_from_url(value) is not None
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
        self._window_urls: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _scope_key(scope: tuple[str, str]) -> str:
        return f"{scope[0]}\x00{scope[1]}"

    def _forget(self, key: str, child: Any) -> None:
        with self._lock:
            if self._windows.get(key) is child:
                self._windows.pop(key, None)
                self._window_urls.pop(key, None)

    def _close(self, key: str) -> dict[str, bool]:
        with self._lock:
            child = self._windows.pop(key, None)
            self._window_urls.pop(key, None)
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
        replaced = False
        with self._lock:
            existing = self._windows.get(key)
            existing_url = self._window_urls.get(key)
        if existing is not None:
            if existing_url != str(url):
                # A workspace switch rotates the bearer token while keeping
                # the practice scope stable.  Reusing this child would leave
                # it permanently unauthorized, so close it before creating a
                # child with the fresh URL.
                self._close(key)
                replaced = True
                existing = None
            else:
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
                f"{PRODUCT_NAME} · Agent 实操",
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
            self._window_urls[key] = str(url)
        events = getattr(child, "events", None)
        closing = getattr(events, "closing", None)
        if closing is not None:
            closing += lambda *_args, _key=key, _child=child: self._forget(_key, _child)
        return {"opened": True, **({"replaced": True} if replaced else {})}

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


class _InterviewWindowApi:
    """Small pywebview bridge for independent skill-interview windows."""

    def __init__(self, webview: Any, base_url: str):
        self._webview = webview
        self._base_url = str(base_url or "")
        self._windows: dict[str, Any] = {}
        self._window_urls: dict[str, str] = {}
        self._lock = threading.Lock()

    def _forget(self, session_id: str, child: Any) -> None:
        with self._lock:
            if self._windows.get(session_id) is child:
                self._windows.pop(session_id, None)
                self._window_urls.pop(session_id, None)

    def _close(self, session_id: str) -> dict[str, bool]:
        with self._lock:
            child = self._windows.pop(session_id, None)
            self._window_urls.pop(session_id, None)
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

    def open_interview_window(self, url: str) -> dict[str, bool]:
        session_id = _interview_scope_from_url(url)
        if session_id is None or not _is_safe_interview_url(url, self._base_url):
            return {"opened": False}
        replaced = False
        with self._lock:
            existing = self._windows.get(session_id)
            existing_url = self._window_urls.get(session_id)
        if existing is not None:
            if existing_url != str(url):
                self._close(session_id)
                replaced = True
                existing = None
            else:
                try:
                    restore = getattr(existing, "restore", None)
                    if callable(restore):
                        restore()
                    show = getattr(existing, "show", None)
                    if callable(show):
                        show()
                    return {"opened": True, "reused": True}
                except Exception:
                    self._forget(session_id, existing)
        try:
            child = self._webview.create_window(
                f"{PRODUCT_NAME} · 技能访谈",
                url=str(url),
                width=1160,
                height=860,
                min_size=(960, 640),
                text_select=True,
                confirm_close=False,
                background_color="#F7F8FA",
                js_api=_InterviewWindowChildApi(self, session_id),
            )
        except Exception:
            return {"opened": False}
        with self._lock:
            self._windows[session_id] = child
            self._window_urls[session_id] = str(url)
        events = getattr(child, "events", None)
        closing = getattr(events, "closing", None)
        if closing is not None:
            closing += lambda *_args, _session_id=session_id, _child=child: self._forget(_session_id, _child)
        return {"opened": True, **({"replaced": True} if replaced else {})}

    def close_interview_window(self, session_id: str = "") -> dict[str, bool]:
        key = str(session_id or "").strip()
        return self._close(key) if key else {"closed": False}


class _InterviewWindowChildApi:
    """Per-child bridge so close requests stay bound to one session."""

    def __init__(self, parent: _InterviewWindowApi, session_id: str):
        self._parent = parent
        self._session_id = session_id

    def close_interview_window(self, *_args: Any, **_kwargs: Any) -> dict[str, bool]:
        return self._parent._close(self._session_id)


class _NativeBridgeApi(_PracticeWindowApi):
    """Native-only bridge methods shared by the main WebView.

    The folder dialog is deliberately exposed as an action, rather than being
    called during startup or while the service is loading.  A browser run has
    no ``pywebview`` object and therefore keeps its manual-path fallback.
    """

    def __init__(self, webview: Any, base_url: str):
        super().__init__(webview, base_url)
        self._interview_windows = _InterviewWindowApi(webview, base_url)
        self._main_window: Any | None = None
        self._main_window_lock = threading.Lock()

    def open_interview_window(self, url: str) -> dict[str, bool]:
        return self._interview_windows.open_interview_window(url)

    def close_interview_window(self, session_id: str = "") -> dict[str, bool]:
        return self._interview_windows.close_interview_window(session_id)

    def bind_main_window(self, window: Any) -> None:
        """Attach the already-created main window to this bridge."""

        with self._main_window_lock:
            self._main_window = window

    @staticmethod
    def _dialog_directory_hint(value: Any) -> str:
        """Return a bounded absolute hint without probing the filesystem."""

        text = str(value or "").strip()
        if not text or len(text.encode("utf-8")) > 4096:
            return ""
        try:
            candidate = Path(text).expanduser()
        except (OSError, RuntimeError, ValueError):
            return ""
        return str(candidate) if candidate.is_absolute() else ""

    @staticmethod
    def _dialog_selection(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        return value.strip() if isinstance(value, str) else ""

    def pick_workspace_folder(self, initial_path: str = "") -> dict[str, Any]:
        """Open the system folder picker after an explicit user action.

        The result is intentionally small and JSON-friendly so the frontend
        can use the same call in a packaged WebView and detect browser
        fallback by checking for ``window.pywebview.api``.  The selected path
        is canonicalized with the same symlink/absolute-path checks as the
        HTTP workspace switch endpoint; no directory enumeration is done.
        """

        with self._main_window_lock:
            window = self._main_window
        dialog = getattr(window, "create_file_dialog", None) if window is not None else None
        if not callable(dialog):
            return {
                "ok": False,
                "available": False,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": "系统文件夹选择器不可用，请使用桌面版选择文件夹或手动输入绝对路径。",
            }

        directory = self._dialog_directory_hint(initial_path)
        dialog_type = getattr(self._webview, "FOLDER_DIALOG", 2)
        try:
            selection = dialog(dialog_type, directory=directory, allow_multiple=False)
        except Exception:
            return {
                "ok": False,
                "available": True,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": "无法打开系统文件夹选择器，请手动输入绝对路径。",
            }

        selected = self._dialog_selection(selection)
        if not selected:
            return {
                "ok": True,
                "available": True,
                "selected": False,
                "cancelled": True,
                "path": "",
            }

        try:
            from agent_coach.workspace import WorkspaceError, canonical_workspace
        except Exception:
            return {
                "ok": False,
                "available": True,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": "工作区校验不可用，请手动输入绝对路径。",
            }
        try:
            workspace = canonical_workspace(selected, must_exist=True)
        except (WorkspaceError, OSError, RuntimeError, ValueError):
            return {
                "ok": False,
                "available": True,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": "所选位置不是可用的本地文件夹，请选择其他目录。",
            }
        return {
            "ok": True,
            "available": True,
            "selected": True,
            "cancelled": False,
            "path": str(workspace),
        }

    # Keep the contract discoverable for clients that naturally use the
    # ``workspace_picker`` or ``select`` verb; all names intentionally have
    # identical semantics for the native frontend contract and integrations.
    workspace_picker = pick_workspace_folder
    select_workspace_folder = pick_workspace_folder

    def _pick_local_path(self, initial_path: Any = "", kind: Any = "file") -> dict[str, Any]:
        """Return one path chosen through an explicit native file dialog.

        This bridge intentionally does not resolve, stat, enumerate, copy, or
        parse the selected path.  The browser submits the returned absolute
        path to ``POST /api/imports/local`` where the existing import policy
        validates links, sensitive locations, file types, and size limits.
        """

        # Accept a small object payload as well as the two positional values;
        # this keeps the contract convenient for JavaScript callers while
        # retaining the simple ``pick_local_path(path, kind)`` form.
        if isinstance(initial_path, dict):
            request = initial_path
            initial_path = request.get("initial_path", request.get("path", ""))
            kind = request.get("kind", request.get("type", kind))
        elif isinstance(initial_path, str) and str(kind or "file").strip().lower() == "file":
            # ``pick_local_path("folder")`` is a useful shorthand when no
            # initial directory is needed.  A real initial path is otherwise
            # passed through unchanged as a dialog hint.
            shorthand = initial_path.strip().lower()
            if shorthand in {"file", "files", "open", "folder", "directory", "dir"}:
                kind, initial_path = shorthand, ""

        normalized_kind = str(kind or "file").strip().lower()
        if normalized_kind in {"folder", "directory", "dir"}:
            dialog_name = "FOLDER_DIALOG"
            fallback_dialog_type = 2
            label = "文件夹"
        elif normalized_kind in {"file", "files", "open"}:
            dialog_name = "OPEN_DIALOG"
            fallback_dialog_type = 1
            label = "文件"
        else:
            return {
                "ok": False,
                "available": True,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": "本地选择类型无效，请选择文件或文件夹。",
            }

        with self._main_window_lock:
            window = self._main_window
        dialog = getattr(window, "create_file_dialog", None) if window is not None else None
        if not callable(dialog):
            return {
                "ok": False,
                "available": False,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": f"系统{label}选择器不可用，请使用页面文件选择入口或重试。",
            }

        directory = self._dialog_directory_hint(initial_path)
        dialog_type = getattr(self._webview, dialog_name, fallback_dialog_type)
        try:
            selection = dialog(dialog_type, directory=directory, allow_multiple=False)
        except Exception:
            return {
                "ok": False,
                "available": True,
                "selected": False,
                "cancelled": False,
                "path": "",
                "error": f"无法打开系统{label}选择器，请使用页面文件选择入口或重试。",
            }

        selected = self._dialog_selection(selection)
        if not selected:
            return {
                "ok": True,
                "available": True,
                "selected": False,
                "cancelled": True,
                "path": "",
            }
        return {
            "ok": True,
            "available": True,
            "selected": True,
            "cancelled": False,
            "path": selected,
        }

    def pick_local_path(self, initial_path: Any = "", kind: Any = "file") -> dict[str, Any]:
        """Open a native file or folder picker after an explicit user action."""

        return self._pick_local_path(initial_path, kind)

    def pick_local_file(self, initial_path: Any = "") -> dict[str, Any]:
        """Open the native file picker used by local log/note import."""

        return self._pick_local_path(initial_path, "file")

    def pick_local_folder(self, initial_path: Any = "") -> dict[str, Any]:
        """Open the native folder picker used by local log/note import."""

        return self._pick_local_path(initial_path, "folder")

    # Keep the generic contract easy to discover for the profile UI and older
    # desktop integrations without making them depend on implementation names.
    pick_local_input_path = pick_local_path
    select_local_path = pick_local_path
    select_local_file = pick_local_file
    select_local_folder = pick_local_folder


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

    api = _NativeBridgeApi(webview, url)
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
        api.bind_main_window(window)
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
        fallback_root = Path(tempfile.gettempdir()) / "JobCoachDesktop"
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
    supplied = list(sys.argv[1:] if argv is None else argv)
    demo_explicit = _has_option(supplied, "--demo")
    effective = _default_arguments(supplied)
    args = _build_parser().parse_args(effective)
    service, server, url = _create_server(args, demo_explicit=demo_explicit)
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
