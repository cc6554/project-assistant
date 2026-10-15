"""Application assembly for the single-agent job-coaching product."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from .contracts import ToolRegistry
from .demo import DemoRouter
from .engine import AgentEngine, EventCallback
from .store import SessionStore, canonical_path


DEFAULT_TEMPLATE = """# Copy this file to providers.yaml and configure a provider.
providers:
  local:
    api_mode: chat_completions
    base_url: http://localhost:11434/v1
    # Local endpoints do not require an API key.
tasks:
  default:
    - provider: local
      model: REPLACE_WITH_TOOL_AND_VISION_MODEL
  agent:
    - provider: local
      model: REPLACE_WITH_TOOL_AND_VISION_MODEL
"""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def example_config_path() -> Path | None:
    packaged = Path(__file__).resolve().parent / "resources" / "providers.example.yaml"
    if packaged.is_file():
        return packaged
    candidate = _project_root() / "config" / "providers.example.yaml"
    return candidate if candidate.is_file() else None


def build_registry(*, no_skills: bool = False, demo: bool = False) -> ToolRegistry:
    """Register kernel tools and the built-in job-coaching workflow."""

    registry = ToolRegistry()
    from .tools import register_builtin_tools

    register_builtin_tools(registry, include_job_integrations=not no_skills)
    if not no_skills:
        # These imports are intentionally local. ``--no-skills`` remains useful
        # even when optional domain dependencies are not installed.
        from .job_radar import register_job_radar_tools

        register_job_radar_tools(registry)
    return registry


def build_skill_catalog(workspace: str | Path, *, no_skills: bool = False) -> Any | None:
    if no_skills:
        return None
    from .skills import SkillCatalog

    workspace = canonical_path(workspace)
    extra = workspace / ".agent-coach" / "skills"
    return SkillCatalog(extra_dirs=[extra], include_builtin=True)


def build_router(config_path: str | Path | None, *, demo: bool = False) -> Any | None:
    if demo:
        return DemoRouter()
    if config_path is None:
        return None
    # The legacy router is loaded only for an explicitly selected config path;
    # this avoids implicitly reading an old .env or a repository-local config.
    from .legacy.core.llm.router import ModelRouter

    return ModelRouter.from_yaml(canonical_path(config_path))


def build_engine(
    workspace: str | Path,
    *,
    config_path: str | Path | None = None,
    demo: bool = False,
    no_skills: bool = False,
    product_mode: bool = True,
    max_steps: int = 8,
    on_event: EventCallback | None = None,
) -> AgentEngine:
    workspace = canonical_path(workspace)
    store = SessionStore(workspace)
    registry = build_registry(no_skills=no_skills, demo=demo)
    catalog = build_skill_catalog(workspace, no_skills=no_skills)
    router = build_router(config_path, demo=demo)
    product = bool(product_mode and not no_skills)
    return AgentEngine(
        store,
        registry,
        router,
        skill_catalog=catalog,
        no_skills=no_skills,
        product_mode=product,
        default_active_skills=("job-radar",) if product else (),
        max_steps=max_steps,
        on_event=on_event,
    )


def init_config(destination: str | Path, *, force: bool = False) -> Path:
    """Create a key-free provider config template at an explicit path."""

    destination = canonical_path(destination)
    if destination.exists() and not force:
        raise FileExistsError(f"配置已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = example_config_path()
    if source is not None:
        shutil.copyfile(source, destination)
    else:
        destination.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return destination


def doctor_config(config_path: str | Path | None) -> dict[str, Any]:
    """Validate config shape without contacting providers or printing secrets."""

    if config_path is None:
        return {"ok": False, "message": "未指定 --config；真实模型未启用", "providers": [], "tasks": []}
    path = canonical_path(config_path)
    if not path.is_file():
        return {"ok": False, "message": "配置文件不存在", "providers": [], "tasks": []}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {
            "ok": False,
            "message": f"配置无法读取（{type(exc).__name__}）",
            "providers": [],
            "tasks": [],
        }
    if not isinstance(raw, dict):
        return {"ok": False, "message": "配置根节点必须是映射", "providers": [], "tasks": []}
    providers = raw.get("providers") or {}
    tasks = raw.get("tasks") or {}
    errors: list[str] = []
    if not isinstance(providers, dict) or not providers:
        errors.append("providers 为空")
        providers = {}
    if not isinstance(tasks, dict) or not tasks.get("default"):
        errors.append("tasks.default 为空")
        tasks = {} if not isinstance(tasks, dict) else tasks
    safe_providers: list[dict[str, Any]] = []
    for name, item in providers.items():
        if not isinstance(item, dict):
            errors.append(f"provider {name} 格式无效")
            continue
        mode = item.get("api_mode", "chat_completions")
        if mode not in {"chat_completions", "anthropic_messages"}:
            errors.append(f"provider {name} api_mode 无效")
        safe_providers.append(
            {
                "name": str(name),
                "api_mode": mode,
                "base_url_present": bool(item.get("base_url")),
                "api_key_configured": bool(item.get("api_key_env")),
            }
        )
    for task_name, chain in tasks.items():
        if not isinstance(chain, list):
            errors.append(f"task {task_name} 必须是列表")
            continue
        for target in chain:
            if not isinstance(target, dict) or target.get("provider") not in providers:
                errors.append(f"task {task_name} 引用了未知 provider")
            elif not isinstance(target.get("model"), str) or not target["model"].strip():
                errors.append(f"task {task_name} 缺少 model")
    return {
        "ok": not errors,
        "message": "配置结构有效" if not errors else "配置结构存在问题",
        "errors": errors,
        "providers": safe_providers,
        "tasks": [str(name) for name in tasks],
    }


__all__ = [
    "build_engine",
    "build_registry",
    "build_router",
    "build_skill_catalog",
    "doctor_config",
    "example_config_path",
    "init_config",
]
