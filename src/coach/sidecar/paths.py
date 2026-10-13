"""Separate release resources from writable per-user application data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_root() -> Path:
    override = os.environ.get("JOB_RADAR_RESOURCES")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        return root.parent if root.name == "job-radar-sidecar" else root
    return Path(__file__).resolve().parents[3]


def app_root() -> Path:
    """Writable data root; JOB_RADAR_HOME can keep runtime data on F:."""
    override = os.environ.get("JOB_RADAR_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[3]
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "job-radar-coach"


def data_dir() -> Path:
    d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_path() -> Path:
    app_root().mkdir(parents=True, exist_ok=True)
    return app_root() / ".env"


def config_path() -> Path:
    c = app_root() / "config"
    c.mkdir(parents=True, exist_ok=True)
    target = c / "providers.yaml"
    if not target.exists():
        template = resource_root() / "config" / "providers.example.yaml"
        if template.is_file():
            try:
                with target.open("x", encoding="utf-8") as output:
                    output.write(template.read_text(encoding="utf-8"))
            except FileExistsError:
                pass
    return target


def github_token_path() -> Path:
    """用户显式输入的 GitHub Token（私有仓库读取用）；只读本文件，绝不自动读系统凭据。"""
    c = app_root() / "config"
    c.mkdir(parents=True, exist_ok=True)
    return c / "github_token.json"


def dist_dir() -> Path:
    """前端构建产物（Vite dist）。窗口直接加载 http://127.0.0.1:17689/。"""
    return resource_root() / "dist"


def workspace_dir() -> Path:
    """实操 Agent 的工作区：项目复现都在这里进行。"""
    w = app_root() / "workspace"
    w.mkdir(parents=True, exist_ok=True)
    return w
