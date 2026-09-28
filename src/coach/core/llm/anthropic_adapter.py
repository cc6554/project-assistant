"""Anthropic 原生 Messages 协议 adapter（也兼容任何 /anthropic 风格代理）。

与 OpenAI 协议的主要差异在这里屏蔽：
1. system 不是消息，而是顶层参数；
2. user/assistant 必须严格交替，工具结果（OpenAI 的 role=tool）要放进
   user 消息的 tool_result block；
3. 图片用 image block（base64 / url 两种 source）；
4. 工具调用是 assistant 输出中的 tool_use block；
5. max_tokens 为必传参数。
"""

from __future__ import annotations

from anthropic import Anthropic, APIConnectionError, APITimeoutError

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

_DEFAULT_MAX_TOKENS = 4096


def _content_blocks(content: str | list) -> list[dict]:
    if isinstance(content, str):
        # assistant 携带 tool_calls 时文本可能为空串，避免产生空 text block
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict] = []
    for b in content:
        if isinstance(b, TextBlock) or b.type == "text":
            if b.text:
                blocks.append({"type": "text", "text": b.text})
        else:
            assert isinstance(b, ImageBlock)
            if b.data:
                source = {
                    "type": "base64",
                    "media_type": b.media_type,
                    "data": b.data,
                }
            else:
                source = {"type": "url", "url": b.url}
            blocks.append({"type": "image", "source": source})
    return blocks


def to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict]]:
    """返回 (system_prompt, anthropic messages)，并把相邻同角色消息合并。"""
    system_parts: list[str] = []
    wire: list[dict] = []

    def push(role: str, blocks: list[dict]):
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": blocks})

    for m in messages:
        if m.role == "system":
            if isinstance(m.content, str):
                system_parts.append(m.content)
            else:
                system_parts.extend(
                    b.text for b in m.content if isinstance(b, TextBlock)
                )
            continue

        if m.role == "tool":
            # 工具结果统一挂在 user 侧
            result_content = m.content if isinstance(m.content, str) else _result_text(m.content)
            push(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": result_content,
                    }
                ],
            )
            continue

        blocks = _content_blocks(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
        push("assistant" if m.role == "assistant" else "user", blocks)

    return "\n\n".join(system_parts), wire


def _result_text(blocks: list) -> str:
    return "\n".join(
        b.text for b in blocks if isinstance(b, TextBlock)
    )


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]


def _tool_choice(force_tool: bool | str):
    if force_tool is False:
        return None
    if force_tool is True:
        return {"type": "any"}
    return {"type": "tool", "name": force_tool}


class AnthropicClient(LLMClient):
    api_mode = "anthropic_messages"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = Anthropic(
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
        system_prompt, wire_messages = to_anthropic_messages(messages)

        kwargs: dict = {
            "model": self.model,
            "messages": wire_messages,
            "max_tokens": max_tokens or _DEFAULT_MAX_TOKENS,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)
            choice = _tool_choice(force_tool)
            if choice is not None:
                kwargs["tool_choice"] = choice

        try:
            resp = self._client.messages.create(**kwargs)
        except (APITimeoutError, APIConnectionError) as exc:
            raise ProviderError(self._label(), str(exc)) from exc
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            raise ProviderError(self._label(), str(exc), status_code=status) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {"value": block.input},
                    )
                )

        usage = None
        if getattr(resp, "usage", None):
            usage = Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
            )

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            usage=usage,
        )

    def _label(self) -> str:
        return f"anthropic:{self.base_url}:{self.model}"
