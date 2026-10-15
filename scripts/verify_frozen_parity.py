"""Headless acceptance smoke for a packaged Agent Coach build.

The check deliberately starts the supplied executable with a fresh, private
workspace.  It discovers the loopback URL from the headless marker (or the
child's stdout for a console build), then exercises only deterministic demo
routes.  No provider request, browser window, global ``taskkill`` or existing
workspace is touched.

Examples::

    python scripts/verify_frozen_parity.py \
        --exe dist/AgentCoachDesktop/AgentCoachDesktop.exe \
        --archive dist/AgentCoachDesktop-windows.zip

For a source launcher smoke, ``--exe`` may point at ``packaged_launcher.py``;
the script will invoke it with the current Python interpreter.  This is useful
while a new frozen executable is being prepared, but it does not replace the
final EXE check.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid
import zipfile


_URL_RE = re.compile(r"https?://127\.0\.0\.1:\d+/#token=[^\s'\"]+")
_QA_ROOT_PREFIX = "agent-coach-frozen-qa-"
_SECRET_BASENAMES = {
    "api.key",
    "github-token.key",
    "id_ed25519",
    "id_rsa",
    "model-settings.key",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_SAFE_CERTIFICATE_BASENAMES = {"cacert.pem"}


class CheckFailure(RuntimeError):
    """A user-actionable acceptance check failure."""


@dataclass(frozen=True)
class Response:
    status: int
    value: Any
    text: str
    content_type: str


class Checks:
    def __init__(self) -> None:
        self.count = 0
        self.skipped = 0

    def require(self, condition: bool, label: str, detail: str = "") -> None:
        if not condition:
            suffix = f": {detail}" if detail else ""
            raise CheckFailure(f"{label}{suffix}")
        self.count += 1
        print(f"[OK] {label}")

    def skip(self, label: str, detail: str = "") -> None:
        self.skipped += 1
        suffix = f": {detail}" if detail else ""
        print(f"[SKIP] {label}{suffix}")


class LocalAPI:
    """Small stdlib-only client for the authenticated loopback API."""

    def __init__(self, url: str, *, timeout: float) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "http" or host != "127.0.0.1" or parsed.port is None:
            raise CheckFailure("headless URL must use http://127.0.0.1:<port>")
        token = parse_qs(parsed.fragment, keep_blank_values=False).get("token", [""])[0]
        if not token:
            raise CheckFailure("headless URL did not contain a token")
        self.host = host
        self.port = parsed.port
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None) -> Response:
        encoded = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Connection": "close",
        }
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        client = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            client.request(method, path, body=encoded, headers=headers)
            response = client.getresponse()
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            text = raw.decode("utf-8", errors="replace")
            value: Any = text
            if "json" in content_type.casefold():
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    value = text
            return Response(response.status, value, text, content_type)
        except (OSError, http.client.HTTPException) as exc:
            raise CheckFailure(f"HTTP {method} {path} failed: {type(exc).__name__}") from exc
        finally:
            client.close()

    def get(self, path: str) -> Response:
        return self.request("GET", path)

    def post(self, path: str, body: Any) -> Response:
        return self.request("POST", path, body)


def _public_payload(value: Any, *, path: str = "payload") -> None:
    """Reject raw credential fields in public settings responses.

    ``has_api_key`` is an intentional boolean and is therefore allowed.  The
    check is structural so a model name or description containing a word such
    as ``token`` does not cause a false failure.
    """

    forbidden = {"api_key", "token", "secret", "password", "credential", "authorization"}
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered in forbidden:
                raise CheckFailure(f"public payload exposed credential field at {path}.{key}")
            _public_payload(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _public_payload(child, path=f"{path}[{index}]")


def _expect_json(
    checks: Checks,
    api: LocalAPI,
    method: str,
    path: str,
    body: Any = None,
    *,
    status: int,
) -> Any:
    response = api.request(method, path, body)
    checks.require(response.status == status, f"{method} {path} -> {status}", f"got {response.status}")
    checks.require(isinstance(response.value, dict), f"{method} {path} returned a JSON object")
    return response.value


def _extract_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,;)") if match else None


def _discover_url(
    process: subprocess.Popen[str],
    workspace: Path,
    output: list[str],
    *,
    timeout: float,
) -> str:
    marker = workspace / ".agent-coach" / "headless-url.txt"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if marker.is_file():
                url = _extract_url(marker.read_text(encoding="utf-8", errors="replace"))
                if url:
                    return url
        except OSError:
            pass
        url = _extract_url("".join(output))
        if url:
            return url
        if process.poll() is not None:
            tail = _redact_urls("".join(output))[-500:]
            raise CheckFailure(f"headless process exited before publishing its URL ({tail})")
        time.sleep(0.1)
    raise CheckFailure(f"headless URL not published within {timeout:g}s")


def _redact_urls(value: str) -> str:
    return _URL_RE.sub("<headless-url>", value)


def _start_process(executable: Path, workspace: Path) -> tuple[subprocess.Popen[str], list[str]]:
    arguments = ["--headless", "--demo", "--port", "0", "--workspace", str(workspace)]
    if executable.suffix.casefold() == ".py":
        command = [sys.executable, str(executable), *arguments]
    else:
        command = [str(executable), *arguments]
    try:
        process: subprocess.Popen[str] = subprocess.Popen(
            command,
            cwd=str(executable.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise CheckFailure(f"could not start {executable.name}: {type(exc).__name__}") from exc

    output: list[str] = []

    def drain() -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                output.append(line)
        except (OSError, ValueError):
            return

    threading.Thread(target=drain, name="frozen-smoke-output", daemon=True).start()
    return process, output


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    """Stop only the child process created by this script."""

    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Popen.kill targets this process handle; no system-wide process name
        # matching is used, so another Agent Coach instance is untouched.
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        return


def _jd_payload() -> dict[str, Any]:
    return {
        "job_title": "冻结版后端工程师",
        "company": "QA Labs",
        "hard_requirements": ["Python"],
        "skill_tags": ["Python"],
        "raw_summary": "用于发布验收的最小岗位卡片。",
    }


def _profile_payload() -> dict[str, Any]:
    return {
        "user_id": "qa",
        "summary": "完成过 Python HTTP 服务。",
        "target_direction": "后端开发",
        "skills": [
            {
                "name": "Python",
                "proficiency": "working",
                "confidence": 0.8,
                "sources": ["self_report"],
                "evidence": "独立实现过 HTTP 服务。",
            }
        ],
        "open_questions": [],
    }


def _check_http_surface(api: LocalAPI, checks: Checks, workspace: Path, source_root: Path) -> None:
    info = _expect_json(checks, api, "GET", "/api/info", status=200)
    checks.require(info.get("app") == "agent-coach", "info identifies Agent Coach")
    checks.require(info.get("mode") == "demo", "headless process is in demo mode")
    checks.require(info.get("workflow") == "job-radar", "job-radar workflow is enabled")

    settings = _expect_json(checks, api, "GET", "/api/settings/models", status=200)
    checks.require(isinstance(settings, dict), "model settings response is an object")
    _public_payload(settings)
    checks.require("has_api_key" in settings, "model settings expose status only")

    options = _expect_json(checks, api, "GET", "/api/settings/models/options", status=200)
    checks.require(isinstance(options, dict), "model options response is an object")
    _public_payload(options)
    checks.require(isinstance(options.get("options"), list), "declared model options are listed")
    checks.require(isinstance(options.get("saved_profiles"), list), "saved model profiles are listed")

    root = api.get("/")
    checks.require(root.status == 200, "static index is served", f"got {root.status}")
    checks.require("求职" in root.text and "lang=\"zh-CN\"" in root.text, "static index contains Chinese UI")
    css = api.get("/static/styles.css")
    checks.require(css.status == 200, "static stylesheet is served", f"got {css.status}")
    checks.require(
        "data-theme" in css.text and "light" in css.text and "dark" in css.text,
        "static stylesheet contains both theme variants",
    )

    source = _expect_json(checks, api, "POST", "/api/sessions", {}, status=201)
    source_id = source.get("id") if isinstance(source, dict) else None
    checks.require(isinstance(source_id, str) and bool(source_id), "session can be created")

    saved_jd = _expect_json(
        checks,
        api,
        "POST",
        f"/api/sessions/{source_id}/jd",
        {"jd": _jd_payload(), "confirmed": True},
        status=200,
    )
    checks.require(saved_jd.get("jd", {}).get("job_title") == "冻结版后端工程师", "JD can be saved")
    checks.require(saved_jd.get("confirmed") is True, "JD confirmation is recorded as a user action")
    checks.require(saved_jd.get("confirmation", {}).get("actor") == "user", "JD confirmation actor is user")

    saved_profile = _expect_json(
        checks,
        api,
        "POST",
        f"/api/sessions/{source_id}/profile",
        {"profile": _profile_payload()},
        status=200,
    )
    checks.require(saved_profile.get("profile", {}).get("summary") == "完成过 Python HTTP 服务。", "profile can be saved")
    checks.require(saved_profile.get("clarification", {}).get("status") == "completed", "empty clarification is completed")

    clone = _expect_json(
        checks,
        api,
        "POST",
        "/api/sessions",
        {"source_session_id": source_id},
        status=201,
    )
    clone_id = clone.get("id") if isinstance(clone, dict) else None
    checks.require(isinstance(clone_id, str) and clone_id != source_id, "profile copy creates a new session")
    clone_profile = _expect_json(checks, api, "GET", f"/api/sessions/{clone_id}/profile", status=200)
    clone_jd = _expect_json(checks, api, "GET", f"/api/sessions/{clone_id}/jd", status=200)
    clone_snapshot = _expect_json(checks, api, "GET", f"/api/sessions/{clone_id}", status=200)
    checks.require(
        clone_profile.get("profile", {}).get("summary") == "完成过 Python HTTP 服务。",
        "copied session keeps the profile",
    )
    checks.require(clone_jd.get("jd") is None and clone_jd.get("confirmed") is False, "copied session starts without JD context")
    checks.require(not clone_snapshot.get("messages"), "copied session does not copy chat history")

    import_source = source_root / "local-import-fixture.txt"
    import_source.write_text("frozen parity fixture\n", encoding="utf-8")
    imported = _expect_json(
        checks,
        api,
        "POST",
        "/api/imports/local",
        {"path": str(import_source)},
        status=201,
    )
    imported_paths = imported.get("imported") if isinstance(imported, dict) else None
    checks.require(isinstance(imported_paths, list) and len(imported_paths) == 1, "local file import returns one file")
    imported_row = imported_paths[0] if isinstance(imported_paths, list) and imported_paths else None
    # The public route intentionally returns the manifest's workspace-relative
    # strings.  Keep accepting a manifest row here as a compatibility aid for
    # older builds, but never treat the whole row's repr as a filesystem path.
    relative_value = (
        imported_row.get("path")
        if isinstance(imported_row, dict)
        else imported_row
        if isinstance(imported_row, str)
        else None
    )
    checks.require(isinstance(relative_value, str) and bool(relative_value), "local import returns a workspace-relative path")
    relative = Path(str(relative_value))
    checks.require(not relative.is_absolute() and ".." not in relative.parts, "local import path stays relative")
    target = (workspace / relative).resolve()
    checks.require(target.is_file() and target.read_text(encoding="utf-8") == "frozen parity fixture\n", "local import writes inside QA workspace")


def _archive_candidates(executable: Path) -> list[Path]:
    parent = executable.parent
    return [
        executable.with_suffix(".zip"),
        parent.parent / f"{parent.name}-windows.zip",
        parent.parent / f"{parent.name}.zip",
    ]


def _check_archive(archive: Path, checks: Checks) -> None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            bad = bundle.testzip()
            checks.require(bad is None, "release ZIP CRC is valid", str(bad) if bad else "")
            forbidden: list[str] = []
            for entry in bundle.infolist():
                normalized = entry.filename.replace("\\", "/")
                parts = [part.casefold() for part in normalized.split("/") if part]
                basename = parts[-1] if parts else ""
                if any(part in {"work", ".agent-coach"} for part in parts):
                    forbidden.append(entry.filename)
                    continue
                if basename == ".env" or basename.startswith(".env."):
                    forbidden.append(entry.filename)
                    continue
                if basename in _SECRET_BASENAMES or basename in {"providers.yaml", "providers.yml"}:
                    forbidden.append(entry.filename)
                    continue
                if (
                    Path(basename).suffix.casefold() in _SECRET_SUFFIXES
                    and basename not in _SAFE_CERTIFICATE_BASENAMES
                ):
                    forbidden.append(entry.filename)
            checks.require(not forbidden, "release ZIP excludes work and credential files", ", ".join(forbidden[:3]))
    except CheckFailure:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise CheckFailure(f"could not inspect release ZIP {archive.name}: {type(exc).__name__}") from exc


def _check_workspace_clean(workspace: Path, checks: Checks) -> None:
    forbidden: list[str] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        parts = [part.casefold() for part in relative.parts]
        basename = parts[-1]
        if basename == "headless-url.txt":
            continue
        if basename == ".env" or basename.startswith(".env.") or basename in _SECRET_BASENAMES:
            forbidden.append(relative.as_posix())
        elif Path(basename).suffix.casefold() in _SECRET_SUFFIXES:
            forbidden.append(relative.as_posix())
    checks.require(not forbidden, "QA workspace contains no credential files", ", ".join(forbidden[:3]))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic headless smoke against a packaged Agent Coach build")
    parser.add_argument("--exe", required=True, help="path to the EXE; a .py source launcher is also accepted for preparation")
    parser.add_argument("--archive", help="release ZIP to CRC-check; nearby -windows.zip is auto-detected when present")
    parser.add_argument("--timeout", type=float, default=30.0, help="startup/API timeout in seconds (default: 30)")
    parser.add_argument("--keep-workspace", action="store_true", help="retain the private temporary QA workspace after the run")
    return parser.parse_args(argv)


def _summary_json(*, status: str, checks: Checks, archive_checked: bool, workspace_retained: bool) -> str:
    """Return a count-only result; never include URL, token, paths, or errors."""

    return json.dumps(
        {
            "status": status,
            "checks": checks.count,
            "skipped": checks.skipped,
            "archive_checked": archive_checked,
            "workspace_retained": workspace_retained,
            "redacted": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _make_qa_tree() -> tuple[Path, Path, Path, Path]:
    """Create sibling source/workspace directories under one disposable root."""

    temp_root = Path(tempfile.gettempdir()).resolve()
    qa_root: Path | None = None
    for _ in range(8):
        candidate = temp_root / f"{_QA_ROOT_PREFIX}{uuid.uuid4().hex}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        qa_root = candidate.resolve()
        break
    if qa_root is None:
        raise CheckFailure("could not create a unique temporary QA root")
    if qa_root.parent != temp_root or not qa_root.name.startswith(_QA_ROOT_PREFIX):
        # This should be impossible for mkdtemp, but do not retain or remove a
        # path if the platform returns an unexpected result.
        raise CheckFailure("temporary QA root failed its containment check")
    workspace = qa_root / "workspace"
    source_root = qa_root / "source"
    workspace.mkdir()
    source_root.mkdir()
    return qa_root, temp_root, workspace, source_root


def _remove_qa_tree(qa_root: Path, temp_root: Path) -> None:
    """Recursively remove only the root created by ``_make_qa_tree``."""

    resolved_root = qa_root.resolve(strict=False)
    resolved_temp = temp_root.resolve(strict=False)
    if resolved_root.parent != resolved_temp or not resolved_root.name.startswith(_QA_ROOT_PREFIX):
        raise CheckFailure("refusing to remove an unexpected QA root")
    shutil.rmtree(resolved_root)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.timeout <= 0:
        print("FAIL: --timeout must be positive", file=sys.stderr)
        return 2
    executable = Path(args.exe).expanduser()
    try:
        executable = executable.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        print(f"FAIL: executable does not exist: {args.exe}", file=sys.stderr)
        return 2
    if not executable.is_file():
        print(f"FAIL: executable is not a file: {executable}", file=sys.stderr)
        return 2

    qa_root, temp_root, workspace, source_root = _make_qa_tree()
    process: subprocess.Popen[str] | None = None
    keep = bool(args.keep_workspace)
    checks = Checks()
    archive_checked = False
    status = "fail"
    return_code = 1
    try:
        print("QA workspace: <private-temporary>")
        process, output = _start_process(executable, workspace)
        url = _discover_url(process, workspace, output, timeout=args.timeout)
        api = LocalAPI(url, timeout=min(10.0, args.timeout))
        _check_http_surface(api, checks, workspace, source_root)
        _check_workspace_clean(workspace, checks)

        archive: Path | None = None
        if args.archive:
            archive = Path(args.archive).expanduser().resolve()
        else:
            archive = next((candidate for candidate in _archive_candidates(executable) if candidate.is_file()), None)
        if archive is None:
            checks.skip("release ZIP CRC/content checks", "no release ZIP supplied or found")
        else:
            _check_archive(archive, checks)
            archive_checked = True
        status = "pass"
        return_code = 0
    except CheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
    finally:
        _stop_process(process)
        if keep:
            print("Kept QA workspace: <private-temporary>")
        else:
            try:
                _remove_qa_tree(qa_root, temp_root)
            except CheckFailure as exc:
                print(f"FAIL: {exc}", file=sys.stderr)
                status = "fail"
                return_code = 1
        print(_summary_json(status=status, checks=checks, archive_checked=archive_checked, workspace_retained=keep))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
