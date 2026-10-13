"""Release assembly and relocation regressions; no build tool or real settings used."""
import importlib.util
from pathlib import Path

import pytest

from coach.sidecar import paths

spec = importlib.util.spec_from_file_location(
    "assemble_release", Path(__file__).resolve().parents[1] / "scripts" / "assemble_release.py"
)
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def inputs(root, windows):
    suffix = ".exe" if windows else ""
    for relative, content in {
        f"src-tauri/target/release/job-radar-coach{suffix}": "desktop",
        f"build/sidecar-dist/job-radar-sidecar/job-radar-sidecar{suffix}": "sidecar",
        "dist/index.html": "<html>app</html>",
        "config/providers.example.yaml": "providers: {}\ntasks: {}\n",
        ".env.example": "API_KEY=\n",
        ".env": "PRIVATE_MARKER=not-for-release",
        "config/providers.yaml": "private configuration",
        "config/github_token.json": "private token marker",
    }.items():
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("windows", [False, True])
def test_release_allowlist_and_relocation(tmp_path, monkeypatch, windows):
    root, destination = tmp_path / "project", tmp_path / "relocated" / "release"
    inputs(root, windows)
    assembly.assemble(root, destination, windows=windows)
    assert not (destination / ".env").exists()
    assert not (destination / "config" / "providers.yaml").exists()
    assert not (destination / "config" / "github_token.json").exists()
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(destination / "job-radar-sidecar" / "job-radar-sidecar.exe"))
    monkeypatch.delenv("JOB_RADAR_RESOURCES", raising=False)
    monkeypatch.setenv("JOB_RADAR_HOME", str(tmp_path / "writable"))
    assert paths.dist_dir() == destination / "dist"
    assert paths.config_path().read_text() == "providers: {}\ntasks: {}\n"
    assert paths.sessions_dir().is_relative_to(tmp_path / "writable")
    assert paths.workspace_dir().is_relative_to(tmp_path / "writable")
    assert paths.env_path().is_relative_to(tmp_path / "writable")


def test_reject_existing_destination_and_stale_credentials(tmp_path):
    root = tmp_path / "project"
    inputs(root, True)
    destination = tmp_path / "release"
    destination.mkdir()
    with pytest.raises(FileExistsError):
        assembly.assemble(root, destination, windows=True)
    (root / "dist" / ".env").write_text("fake key", encoding="utf-8")
    with pytest.raises(ValueError, match="must not be bundled"):
        assembly.assemble(root, tmp_path / "new-release", windows=True)
    assert not (tmp_path / "new-release").exists()


def test_release_missing_input_fails_before_creating_destination(tmp_path):
    with pytest.raises(FileNotFoundError):
        assembly.assemble(tmp_path, tmp_path / "release", windows=False)
    assert not (tmp_path / "release").exists()
