"""路径定位：开发态用项目根，PyInstaller 打包后用 exe 所在目录。"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        exe_dir = Path(sys.executable).resolve().parent
        # onedir 打包：exe 位于 job-radar-sidecar/ 子目录，运行时数据仍放在分发根
        if exe_dir.name == "job-radar-sidecar":
            return exe_dir.parent
        return exe_dir
    # src/coach/sidecar/paths.py → 项目根
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_path() -> Path:
    return app_root() / ".env"


def config_path() -> Path:
    c = app_root() / "config"
    c.mkdir(parents=True, exist_ok=True)
    return c / "providers.yaml"


def dist_dir() -> Path:
    """前端构建产物（Vite dist）。窗口直接加载 http://127.0.0.1:17689/。"""
    return app_root() / "dist"


def workspace_dir() -> Path:
    """实操 Agent 的工作区：项目复现都在这里进行。"""
    w = app_root() / "workspace"
    w.mkdir(parents=True, exist_ok=True)
    return w
