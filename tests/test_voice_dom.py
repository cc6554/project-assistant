from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_voice_dom_regression() -> None:
    """Exercise speech lifecycle and stale-input guards without a microphone."""

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the voice DOM regression")
    script = Path(__file__).with_name("voice_dom_regression.js")
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
