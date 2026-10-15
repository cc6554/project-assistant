"""Verify the operating-system session lock with a separate interpreter."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_coach.store import SessionBusy, SessionStore


def test_separate_process_cannot_acquire_busy_session(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create()
    code = (
        "import sys; from agent_coach.store import SessionStore; "
        "s=SessionStore(sys.argv[1]); "
        "ctx=s.lock(sys.argv[2]); ctx.__enter__(); "
        "print('LOCKED',flush=True); sys.stdin.readline(); ctx.__exit__(None,None,None)"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONUTF8="1")
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), session.id],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", env=env)
    try:
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(SessionBusy):
            with store.lock(session.id):
                pass
        _, errors = child.communicate("release\n", timeout=10)
        assert child.returncode == 0, errors
        with store.lock(session.id):
            assert store.get(session.id).id == session.id
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
