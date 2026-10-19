from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_jd_editor_dom_scenarios() -> None:
    """Exercise JD row editing and save/session guards through the DOM shim."""

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for JD editor DOM scenarios")
    script = Path(__file__).with_name("jd_editor_dom_regression.js")
    result = subprocess.run(
        [node, str(script)],
        cwd=script.parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr).strip()
