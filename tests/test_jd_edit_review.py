"""Acceptance checks for the Doubao-aligned JD card/editor contract.

These checks intentionally stay at the public HTTP/static-resource boundary.  They
do not reach a model and do not couple the test to a browser implementation.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_web import create_session, live_server, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "src" / "agent_coach" / "web_static" / "app.js"
STYLES_CSS = PROJECT_ROOT / "src" / "agent_coach" / "web_static" / "styles.css"


def test_jd_http_round_trip_preserves_every_display_and_editor_field(live_server):
    """The editor must be able to save the complete JDCard without field loss."""

    service, server = live_server
    session_id = create_session(service, server)
    jd = {
        "job_title": "多模态算法工程师",
        "company": "Example Labs",
        "salary": "25-45K·15薪",
        "location": "北京·海淀",
        "team": "智能交互团队",
        "hard_requirements": ["本科及以上学历", "熟悉 Python 与深度学习"],
        "skill_tags": ["Python", "PyTorch", "RAG"],
        "responsibilities": ["负责模型训练与评测", "建设线上推理服务"],
        "nice_to_haves": ["有开源项目经验", "有竞赛获奖经历"],
        "raw_summary": "负责多模态模型训练、评测及推理服务建设。",
        "uncertain_fields": ["salary"],
    }

    status, saved = request(
        service,
        server,
        "POST",
        f"/api/sessions/{session_id}/jd",
        {"jd": jd, "confirmed": True},
    )
    assert status == 200
    assert saved["jd"] == jd
    assert saved["confirmed"] is True

    status, loaded = request(service, server, "GET", f"/api/sessions/{session_id}/jd")
    assert status == 200
    assert loaded["jd"] == jd


def test_jd_static_editor_uses_independent_rows_and_renderer_options():
    """List fields must be row-editable and renderer options must be effective."""

    app = APP_JS.read_text(encoding="utf-8")
    styles = STYLES_CSS.read_text(encoding="utf-8")

    assert "function editableListEditor" in app
    for field in (
        "hard_requirements",
        "skill_tags",
        "responsibilities",
        "nice_to_haves",
        "uncertain_fields",
    ):
        assert re.search(
            rf'editableListEditor\("[^"]*",\s*"{re.escape(field)}"',
            app,
        )
    assert "data-job-edit-list-row" in app
    assert "data-job-edit-list-input" in app
    assert "readEditableList" in app

    # The JD renderer recently grew listTag/listClass/itemClass options.  The
    # helper must consume them; passing them at the call site alone is a no-op.
    helper = re.search(
        r"function appendJobList\([\s\S]*?(?=\n\s*function parentPath\b)",
        app,
    )
    assert helper, "appendJobList helper is missing"
    helper_text = helper.group(0)
    assert "listTag" in helper_text
    assert "listClass" in helper_text
    assert "itemClass" in helper_text

    # A JD card must call a function that is actually defined.  This catches a
    # rename typo that only appears when the JD workflow is rendered.
    jd_call = re.search(r"const jdCard = ([A-Za-z_$][\w$]*)\(stateData\.jd\)", app)
    assert jd_call, "JD card construction call is missing"
    assert re.search(rf"function\s+{re.escape(jd_call.group(1))}\s*\(", app), (
        f"JD card helper {jd_call.group(1)} is called but not defined"
    )
    # Blue skill pills need a pill radius and the shared blue theme token.  The
    # exact shade remains theme-controlled through --accent-2.
    css_rules = re.findall(r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*)\}", styles)
    chip_style = next(
        (
            body
            for selectors, body in css_rules
            if ".jd-card" in selectors
            and re.search(r"\.job-chip(?=[\s,{])", selectors)
        ),
        None,
    )
    assert chip_style is not None, "JD skill chip style is missing"
    assert re.search(r"border-radius\s*:\s*999px", chip_style)
    assert (
        "--accent-2" in chip_style
        or re.search(r"#4d8dff|rgba\(\s*77\s*,\s*141\s*,\s*255", chip_style)
    )
    chip_list_style = next(
        (
            body
            for selectors, body in css_rules
            if ".jd-card" in selectors and ".job-chip-list" in selectors
        ),
        None,
    )
    if chip_list_style is None:
        chip_list_style = next(
            (
                body
                for selectors, body in css_rules
                if ".job-chip-list" in selectors
            ),
            None,
        )
    assert chip_list_style is not None, "JD skill chip list selector is missing"
    assert re.search(r"flex-direction\s*:\s*row", chip_list_style)

    # The JD selector is deliberately more specific than the generic theme
    # .job-chip rules below.  Requiring a second theme-specific selector here
    # would reject the valid compound selector while checking the wrong thing.
    assert re.search(
        r"\.jd-card[^{}]*\.job-list\.job-chip-list[^{}]*\{",
        styles,
    )
