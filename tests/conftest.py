"""Use ordinary workspace directories for temporary files on restricted Windows."""
from pathlib import Path
import shutil
import uuid
import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[1] / "work" / "tests" / uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
