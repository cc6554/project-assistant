"""Source checkout entry point for the browser workbench.

The project can be used directly from a source checkout, so this wrapper keeps
the same ``src`` import setup as :mod:`run.py` and leaves argument parsing and
browser startup to ``agent_coach.web.main``.
"""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agent_coach.web import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
