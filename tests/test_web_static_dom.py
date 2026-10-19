from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_queue_recovery_dom_states() -> None:
    """Exercise the real app.js render paths with a deterministic DOM shim."""

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the web static DOM regression")
    script = Path(__file__).with_name("web_static_dom_regression.js")
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


def test_plan_project_and_profile_table_dom_contract() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the web static DOM regression")
    script = Path(__file__).with_name("plan_project_dom_regression.js")
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


def test_plan_project_behavior_dom_regression() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the web static DOM regression")
    script = Path(__file__).with_name("plan_project_behavior_dom_regression.js")
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


def test_practice_scoped_history_and_filter_dom_regression() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the practice scoped DOM regression")
    script = Path(__file__).with_name("practice_scoped_dom_regression.js")
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
