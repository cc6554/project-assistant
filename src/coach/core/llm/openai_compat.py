"""OpenAI 兼容协议 adapter。

覆盖 OpenAI、DeepSeek、Kimi、智谱、豆包方舟、Mistral、Groq、Together，
以及本地 Ollama / vLLM / LM Studio 等所有实现 /v1/chat/completions 的端点。

消息格式转换函数（to_openai_messages / to_openai_tools）为纯函数，便于单测。
"""

from __future__ import annotations

import json

from openai import APIConnectionError, APITimeoutError, OpenAI

from ..schema import (
    ImageBlock,
    LLMResponse,
    Message,
    TextBlock,
    ToolCall,
    ToolSpec,
    Usage,
)
from .base import LLMClient, ProviderError


def _block_to_openai(block: TextBlock | ImageBlock) -> dict:
    if isinstance(block, TextBlock) or block.type == "text":
        return {"type": "text", "text": block.text}
    return {"type": "image_url", "image_url": {"url": block.data_url()}}


def to_openai_messages(messages: list[Message]) -> list[dict]:
    """把内部 Message 列表翻译成 OpenAI chat.completions 的 messages。"""
    out: list[dict] = []
    for m in messages:
        if isinstance(m.content, str):
            content: str | list[dict] = m.content
        else:
            content = [_block_to_openai(b) for b in m.content]

        msg: dict = {"role": m.role, "content": content}

        if m.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id is not None:
            msg["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            msg["name"] = m.name
        out.append(msg)
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _tool_choice(force_tool: bool | str):
    if force_tool is False:
        return None
    if force_tool is True:
        return "required"
    return {"type": "function", "function": {"name": force_tool}}


class OpenAICompatibleClient(LLMClient):
    api_mode = "chat_completions"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        force_tool: bool | str = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "messages": to_openai_messages(messages),
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            choice = _tool_choice(force_tool)
            if choice is not None:
                kwargs["tool_choice"] = choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except (APITimeoutError, APIConnectionError) as exc:
            raise ProviderError(self._label(), str(exc)) from exc
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            raise ProviderError(self._label(), str(exc), status_code=status) from exc

        choice0 = resp.choices[0]
        cm = choice0.message

        tool_calls: list[ToolCall] = []
        for raw in getattr(cm, "tool_calls", None) or []:
            args = _safe_json_loads(raw.function.arguments or "{}")
            tool_calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=args))

        usage = None
        if getattr(resp, "usage", None):
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens,
                output_tokens=resp.usage.completion_tokens,
            )

        return LLMResponse(
            text=cm.content or "",
            tool_calls=tool_calls,
            stop_reason=choice0.finish_reason,
            usage=usage,
        )

    def _label(self) -> str:
        return f"openai-compat:{self.base_url}:{self.model}"


def _safe_json_loads(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        # 个别模型会吐出不严格的 JSON，截取首个 {...} 兜底；仍失败则原样交给上层报错
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"_raw": raw}
