"""环节级模型路由。

- 按任务名（vision / parsing / interview / planning / default）取 fallback 链；
- 链上任一 provider 报可重试错误（限流、鉴权、5xx、超时、断连）自动切下一家；
- ``complete_json`` 用「强制单工具调用」这一各家兼容性最好的方式拿结构化 JSON。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schema import LLMResponse, Message, ToolSpec
from .anthropic_adapter import AnthropicClient
from .base import ProviderError
from .openai_compat import OpenAICompatibleClient
from .registry import ModelConfig, ProviderEntry, load_config


class AllProvidersFailedError(RuntimeError):
    def __init__(self, task: str, errors: list[ProviderError]):
        detail = "; ".join(str(e) for e in errors) or "链上没有可用 provider"
        super().__init__(f"任务 {task!r} 的整条 fallback 链均失败：{detail}")
        self.task = task
        self.errors = errors


class ModelRouter:
    def __init__(self, config: ModelConfig):
        self.config = config
        self._client_cache: dict[tuple[str, str], object] = {}

    @classmethod
    def from_yaml(cls, path: str | Path = "config/providers.yaml") -> "ModelRouter":
        return cls(load_config(path))

    def _build_client(self, entry: ProviderEntry, model: str):
        cache_key = (entry.name, model)
        if cache_key in self._client_cache:
            return self._client_cache[cache_key]

        common = dict(
            base_url=entry.base_url,
            api_key=entry.api_key,
            model=model,
            timeout=entry.timeout,
            extra_body=entry.extra_body,
        )
        if entry.api_mode == "chat_completions":
            client = OpenAICompatibleClient(**common)
        elif entry.api_mode == "anthropic_messages":
            client = AnthropicClient(**common)
        else:
            raise ValueError(f"未知 api_mode: {entry.api_mode}")

        self._client_cache[cache_key] = client
        return client

    def complete(
        self,
        task: str,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        force_tool: bool | str = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # 全局当前模型（对话界面选择）优先于任务默认链；未选则按任务路由
        chain = [self.config.current] if self.config.current else self.config.chain_for(task)
        errors: list[ProviderError] = []

        for target in chain:
            entry = self.config.providers[target.provider]
            if entry.key_required and entry.api_key is None:
                errors.append(
                    ProviderError(entry.name, "未配置 API key（环境变量为空），跳过")
                )
                continue

            client = self._build_client(entry, target.model)
            try:
                resp = client.complete(
                    messages,
                    tools=tools,
                    force_tool=force_tool,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except ProviderError as exc:
                # 兼容降级：部分厂商（如 DeepSeek thinking 模式）拒绝强制
                # tool_choice，但不强制时工具调用仍可用 → 同 provider 降级重试一次。
                if (
                    exc.status_code == 400
                    and tools
                    and force_tool
                    and "tool_choice" in str(exc).lower()
                ):
                    try:
                        resp = client.complete(
                            messages,
                            tools=tools,
                            force_tool=False,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                    except ProviderError as exc2:
                        errors.append(exc2)
                        if exc2.retryable:
                            continue
                        raise
                else:
                    errors.append(exc)
                    if exc.retryable:
                        continue
                    # 400/422 这类请求本身的错误，换下一家也是同样的错，直接抛
                    raise
            resp.provider = target.provider
            resp.model = target.model
            return resp

        raise AllProvidersFailedError(task, errors)

    def complete_json(
        self,
        task: str,
        messages: list[Message],
        schema: dict,
        *,
        tool_name: str = "emit_result",
        tool_description: str = "按 schema 输出最终结构化结果",
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> dict:
        """优先用强制单工具调用拿结构化 JSON；厂商不支持强制 tool_choice 时
        自动降级为「提供工具 + prompt 约束」，文本 JSON 再做一次解析兜底。
        """
        clean_schema = _strip_schema_meta(schema)
        spec = ToolSpec(
            name=tool_name,
            description=tool_description,
            parameters=clean_schema,
        )
        resp = self.complete(
            task,
            messages,
            tools=[spec],
            force_tool=tool_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if resp.tool_calls:
            return resp.first_tool_arguments()

        return _parse_loose_json(resp.text)


def _strip_schema_meta(schema: dict) -> dict:
    """递归去掉部分厂商 JSON 校验器拒收的 schema 元字段。"""
    drop_keys = {"$schema", "title"}

    def walk(node: object):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in drop_keys}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _parse_loose_json(text: str) -> dict:
    """宽容解析模型输出的 JSON：剥代码块围栏 → 整段解析 → 括号配平截取。"""
    raw = text.strip()
    # 1. 剥掉 markdown 代码块围栏（```json ... ```）
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        pass
    # 2. 从第一个 { 开始做括号配平截取（比 find/rfind 更稳，不会截到半个对象）
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw)):
            c = raw[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        return parsed if isinstance(parsed, dict) else {"value": parsed}
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"模型既没调工具，也没返回可解析的 JSON：{text[:200]}")
