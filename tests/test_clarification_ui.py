from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_clarification_modal_dom_contract() -> None:
    """Check the modal contract and session/job guards without a live model."""

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the clarification DOM regression")
    script = Path(__file__).with_name("clarification_dom_regression.js")
    result = subprocess.run(
        [node, str(script)],
        cwd=script.parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr).strip()
