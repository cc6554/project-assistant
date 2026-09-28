"""Provider 注册表：providers.yaml 的声明式配置 → 运行时对象。

新增一家厂商 = 在 yaml 里加一段，不需要改任何 Python 代码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_API_MODES = {"chat_completions", "anthropic_messages"}


@dataclass
class ProviderEntry:
    name: str
    api_mode: str
    base_url: str
    api_key: str | None
    key_required: bool = True
    timeout: float = 120.0
    extra_body: dict = field(default_factory=dict)


@dataclass
class TaskTarget:
    provider: str
    model: str


@dataclass
class ModelConfig:
    providers: dict[str, ProviderEntry]
    tasks: dict[str, list[TaskTarget]]

    def chain_for(self, task: str) -> list[TaskTarget]:
        if task not in self.tasks:
            if task != "default":
                return self.tasks.get("default", [])
            raise KeyError("配置中没有 default 任务链")
        return self.tasks[task]


def load_config(path: str | Path) -> ModelConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到模型配置 {path}，请先复制 config/providers.example.yaml "
            f"为 config/providers.yaml 并按你的 API key 修改"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    providers: dict[str, ProviderEntry] = {}
    for name, spec in (raw.get("providers") or {}).items():
        api_mode = spec.get("api_mode", "chat_completions")
        if api_mode not in VALID_API_MODES:
            raise ValueError(f"provider {name} 的 api_mode={api_mode} 不支持")

        key_env = spec.get("api_key_env")
        # 未声明 api_key_env 的端点（如本地 Ollama）视为免鉴权
        key_required = bool(key_env)
        api_key = os.environ.get(key_env) if key_env else None

        providers[name] = ProviderEntry(
            name=name,
            api_mode=api_mode,
            base_url=spec["base_url"],
            api_key=api_key,
            key_required=key_required,
            timeout=float(spec.get("timeout", 120)),
            extra_body=spec.get("extra_body") or {},
        )

    tasks: dict[str, list[TaskTarget]] = {}
    for task_name, chain in (raw.get("tasks") or {}).items():
        targets = [TaskTarget(provider=item["provider"], model=item["model"]) for item in chain]
        for t in targets:
            if t.provider not in providers:
                raise ValueError(
                    f"任务 {task_name} 引用了未注册的 provider: {t.provider}"
                )
        tasks[task_name] = targets

    if "default" not in tasks:
        raise ValueError("providers.yaml 必须配置 tasks.default 兜底链")

    return ModelConfig(providers=providers, tasks=tasks)
