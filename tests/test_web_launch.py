from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_web_entrypoint_forwards_arguments(monkeypatch):
    received: list[list[str] | None] = []

    def fake_main(argv=None):
        received.append(argv)
        return 19

    fake_web = types.ModuleType("agent_coach.web")
    fake_web.main = fake_main
    monkeypatch.setitem(sys.modules, "agent_coach.web", fake_web)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(PROJECT_ROOT / "run-web.py"), "--workspace", "work", "--no-browser"],
    )

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(PROJECT_ROOT / "run-web.py"), run_name="__main__")

    assert raised.value.code == 19
    assert received == [["--workspace", "work", "--no-browser"]]
