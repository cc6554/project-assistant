from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import re
import zipfile

import pytest

from agent_coach import integrations, job_radar, tools
from agent_coach.contracts import AgentSession, ToolContext, ToolRegistry
from agent_coach.legacy.tools.documents import UnsupportedDocumentError
from agent_coach.web import build_service


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session=AgentSession(workspace=str(tmp_path)),
        router=None,
    )


def _zip_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README.md", "this must not be extracted by read_document")
    return buffer.getvalue()


def test_upload_accepts_zip_as_opaque_attachment_but_reader_does_not_extract(tmp_path: Path):
    service = build_service(tmp_path / "upload-workspace", demo=True)
    uploaded = service.upload("candidate.zip", base64.b64encode(_zip_bytes()).decode("ascii"))
    uploaded_path = service.workspace / uploaded["path"]
    assert uploaded_path.is_file()
    assert uploaded_path.suffix == ".zip"
    assert not (uploaded_path.parent / "README.md").exists()

    registry = tools.register_builtin_tools(ToolRegistry())
    context = _context(service.workspace)
    with pytest.raises(UnsupportedDocumentError, match=r"\.zip"):
        registry.get("read_document").handler({"path": uploaded["path"]}, context)


def test_local_import_skips_zip_with_explicit_reason(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "candidate.zip").write_bytes(_zip_bytes())
    workspace = tmp_path / "workspace"

    result = integrations.import_local_path(workspace, source)

    assert result["imported"] == []
    assert result["skipped"] == [
        {"source_path": "candidate.zip", "reason": "unsupported_binary_or_extension"}
    ]
    assert not list(workspace.rglob("candidate.zip"))


def test_project_source_ui_does_not_promise_archive_extraction():
    static_root = Path(__file__).resolve().parents[1] / "src" / "agent_coach" / "web_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    script = (static_root / "app.js").read_text(encoding="utf-8")

    for identifier in ("planProjectUrl", "planProjectFileInput", "planProjectUpload", "planProjectLocalImport"):
        assert identifier in html
    assert "planPaperProjects" in html
    assert re.search(r'headerLines\s*:\s*\{\s*["\']熟练程度["\']\s*:\s*\[["\']熟练["\']\s*,\s*["\']程度["\']', script)
    assert not re.search(r"(?:自动)?(?:解压|展开压缩|提取压缩)|unzip|extract\s+archive", html + script, re.IGNORECASE)


def test_paper_impact_claim_requires_identity_and_evidence(tmp_path: Path):
    context = _context(tmp_path)
    registry = job_radar.register_job_radar_tools(ToolRegistry())
    register = registry.get("register_project", active_skills=["job-radar"]).handler

    with pytest.raises(ValueError, match=r"paper\.title、paper\.url 和 paper\.evidence"):
        register(
            {
                "project": {
                    "name": "unverified paper implementation",
                    "source": {
                        "kind": "paper_implementation",
                        "url": "https://example.org/repo",
                        "evidence": ["主体声称已阅读 README"],
                    },
                    "paper": {
                        "venue": "Unknown Venue",
                        "citation_count": 999999,
                        "evidence": ["README mentions a paper"],
                    },
                }
            },
            context,
        )


def test_project_source_kind_cannot_disagree_with_source_provenance(tmp_path: Path):
    context = _context(tmp_path)
    registry = job_radar.register_job_radar_tools(ToolRegistry())
    register = registry.get("register_project", active_skills=["job-radar"]).handler

    with pytest.raises(ValueError, match="source_kind"):
        register(
            {
                "project": {
                    "name": "mismatched source",
                    "source_kind": "upload",
                    "source": {
                        "kind": "user_url",
                        "url": "https://example.org/repo",
                        "evidence": ["URL"],
                    },
                }
            },
            context,
        )


def test_generic_public_url_requires_verifiable_source(tmp_path: Path):
    context = _context(tmp_path)
    registry = job_radar.register_job_radar_tools(ToolRegistry())
    register = registry.get("register_project", active_skills=["job-radar"]).handler

    with pytest.raises(ValueError, match="无法核验项目源码"):
        register(
            {
                "project": {
                    "name": "public repository",
                    "source": {
                        "kind": "user_url",
                        "url": "https://EXAMPLE.org/repo/",
                        "evidence": ["user URL"],
                    },
                }
            },
            context,
        )


def test_persisted_user_project_is_selectable_if_compatibility_candidates_are_empty(tmp_path: Path):
    context = _context(tmp_path)
    registry = job_radar.register_job_radar_tools(ToolRegistry())
    active = ["job-radar"]
    register = registry.get("register_project", active_skills=active).handler
    source_file = tmp_path / "imported" / "main.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("print('local project')", encoding="utf-8")
    result = register(
        {
            "project": {
                "name": "local project",
                "source": {
                    "kind": "local_import",
                    "paths": ["imported/main.py"],
                    "evidence": ["user supplied local implementation"],
                },
            }
        },
        context,
    )
    key = result.data["project"]["project_key"]
    state = context.session.skill_state["job-radar"]
    state["repos"] = []

    selected = registry.get("select_project", active_skills=active).handler({"project_key": key}, context)

    assert selected.data["project"]["project_key"] == key
