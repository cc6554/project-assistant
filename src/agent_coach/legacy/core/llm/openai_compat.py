"""OpenAI 兼容协议 adapter。

覆盖 OpenAI、DeepSeek、Kimi、智谱、豆包方舟、Mistral、Groq、Together，
以及本地 Ollama / vLLM / LM Studio 等所有实现 /v1/chat/completions 的端点。

消息格式转换函数（to_openai_messages / to_openai_tools）为纯函数，便于单测。
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

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


def to_openai_messages(
    messages: list[Message],
    *,
    include_reasoning_content: bool = True,
    reasoning_provider: str | None = None,
    allow_unmarked_reasoning: bool | None = None,
) -> list[dict]:
    """把内部 Message 列表翻译成 OpenAI chat.completions 的 messages。

    ``reasoning_content`` is an optional extension used by DeepSeek thinking
    mode.  It must be returned verbatim on the assistant turn that triggered a
    tool call.  The adapter supplies ``reasoning_provider`` to identify the
    current endpoint and model.  A
    marked turn is echoed only when that identity matches.  Unmarked legacy
    turns can be enabled explicitly for the official DeepSeek endpoint; the
    pure-function default keeps historical tests and callers compatible.
    Anthropic has its own adapter and never receives this field.
    """

    if allow_unmarked_reasoning is None:
        # Preserve the historical pure-function contract when no endpoint
        # identity was supplied.  Real adapters always pass an explicit bool.
        allow_unmarked_reasoning = reasoning_provider is None
    out: list[dict] = []
    for m in messages:
        if isinstance(m.content, str):
            content: str | list[dict] = m.content
        else:
            content = [_block_to_openai(b) for b in m.content]

        msg: dict = {"role": m.role, "content": content}

        # Do not expose provider metadata on user/tool/system messages even if
        # a malformed legacy payload happens to contain it.  ``is not None``
        # intentionally preserves an empty provider value byte-for-byte.
        if (
            include_reasoning_content
            and m.role == "assistant"
            and m.reasoning_content is not None
            and (
                reasoning_provider is None
                or m.reasoning_provider == reasoning_provider
                or (m.reasoning_provider is None and allow_unmarked_reasoning)
            )
        ):
            msg["reasoning_content"] = m.reasoning_content

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


def _endpoint_identity(base_url: str, model: str) -> str:
    """Build a stable, non-secret origin marker for one API endpoint/model."""

    try:
        parsed = urlsplit(base_url)
        scheme = (parsed.scheme or "").casefold()
        host = (parsed.hostname or "").casefold()
        port = parsed.port
        if port is not None and not (
            (scheme == "https" and port == 443)
            or (scheme == "http" and port == 80)
        ):
            host = f"{host}:{port}"
        path = parsed.path.rstrip("/")
        endpoint = f"{scheme}://{host}{path}"
    except ValueError:
        # A malformed URL will be rejected by the SDK; keep the marker free
        # of credentials and deterministic in the meantime.
        endpoint = ""
    return f"openai-compat:{endpoint}|model:{model}"


def _allows_unmarked_reasoning(
    base_url: str,
    model: str,
    provider_name: str | None,
) -> bool:
    """Allow only known DeepSeek origins to consume legacy unmarked traces.

    A model name alone is insufficient: a DeepSeek model can be selected on a
    strict OpenAI-compatible gateway that rejects ``reasoning_content``.  The
    endpoint host is authoritative.  The loopback/model fallback preserves
    direct local adapter users and the historical protocol test; once a
    provider identity is supplied, even a local strict gateway must opt out.
    """

    try:
        hostname = (urlsplit(base_url).hostname or "").casefold()
    except ValueError:
        hostname = ""
    if hostname == "api.deepseek.com" and model.casefold().startswith("deepseek"):
        return True
    if (
        provider_name is None
        and hostname in {"localhost", "127.0.0.1", "::1"}
        and model.casefold().startswith("deepseek")
    ):
        return True
    return False


class OpenAICompatibleClient(LLMClient):
    api_mode = "chat_completions"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._reasoning_provider = _endpoint_identity(
            self.base_url,
            self.model,
        )
        self._allow_unmarked_reasoning = _allows_unmarked_reasoning(
            self.base_url,
            self.model,
            self.provider_name,
        )
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
            "messages": to_openai_messages(
                messages,
                include_reasoning_content=True,
                reasoning_provider=self._reasoning_provider,
                allow_unmarked_reasoning=self._allow_unmarked_reasoning,
            ),
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

        raw_reasoning = getattr(cm, "reasoning_content", None)
        return LLMResponse(
            text=cm.content or "",
            # If an endpoint emits its own extension, bind it to this exact
            # endpoint/model.  A later switch cannot replay it elsewhere, but
            # the same endpoint can continue the protocol when required.
            reasoning_content=raw_reasoning,
            reasoning_provider=(
                self._reasoning_provider
                if raw_reasoning is not None
                else None
            ),
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
