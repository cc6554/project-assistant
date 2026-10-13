"""Assemble a fresh portable release from an explicit allowlist of build outputs."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def assemble(root: Path, destination: Path, *, windows: bool) -> Path:
    root, destination = root.resolve(), destination.resolve()
    executable = "job-radar-coach.exe" if windows else "job-radar-coach"
    sidecar_executable = "job-radar-sidecar.exe" if windows else "job-radar-sidecar"
    sidecar = root / "build" / "sidecar-dist" / "job-radar-sidecar"
    desktop = root / "src-tauri" / "target" / "release" / executable
    required = [desktop, sidecar / sidecar_executable, root / "dist" / "index.html",
                root / "config" / "providers.example.yaml", root / ".env.example"]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"Missing build input: {path}")
    for tree in (sidecar, root / "dist"):
        if destination.is_relative_to(tree):
            raise ValueError("Release destination cannot be inside an input directory")
        for candidate in tree.rglob("*"):
            if candidate.name in {".env", "github_token.json", "providers.yaml"} or (
                candidate.name.startswith(".env.") and candidate.name != ".env.example"
            ):
                raise ValueError(f"Local settings must not be bundled: {candidate}")
    if destination.exists():
        raise FileExistsError(f"Release destination must be new: {destination}")
    destination.mkdir(parents=True)
    shutil.copy2(desktop, destination / executable)
    shutil.copytree(sidecar, destination / "job-radar-sidecar")
    shutil.copytree(root / "dist", destination / "dist")
    (destination / "config").mkdir()
    shutil.copy2(root / "config" / "providers.example.yaml",
                 destination / "config" / "providers.example.yaml")
    shutil.copy2(root / ".env.example", destination / ".env.example")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    destination = args.destination or project / "build" / "releases" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    print(assemble(project, destination, windows=args.windows))
