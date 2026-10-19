"""LLM 层内部统一消息模型。

所有 provider adapter 只认这一套 schema，对外界 API 的格式差异负责翻译。
新增厂商时不改这里，只新增/复用一个 adapter。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    """图片块。``data`` 为不带 ``data:`` 前缀的纯 base64；或直接给 ``url``。"""

    type: Literal["image"] = "image"
    media_type: str = "image/png"
    data: str | None = None
    url: str | None = None

    def data_url(self) -> str:
        if self.url:
            return self.url
        if self.data:
            return f"data:{self.media_type};base64,{self.data}"
        raise ValueError("ImageBlock 既没有 data 也没有 url")


Block = TextBlock | ImageBlock


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    # 纯文本可直接传字符串；含图片时传 block 列表
    content: str | list[Block] = ""
    # Some OpenAI-compatible providers (notably DeepSeek thinking mode) return
    # an opaque reasoning trace alongside an assistant turn.  It is part of
    # the provider conversation protocol, so keep it on the durable internal
    # message even though it is never user-facing text.  ``None`` preserves
    # compatibility with sessions written before this field existed.
    reasoning_content: str | None = None
    # The adapter that produced ``reasoning_content``.  ``None`` is kept for
    # old sessions, whose DeepSeek turns are allowed on the official
    # DeepSeek endpoint but must be omitted by other OpenAI-compatible
    # endpoints.
    reasoning_provider: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # role == "tool" 时对应调用 id
    name: str | None = None
    # Browser-only provenance for transcript scoping.  Providers deliberately
    # ignore this field when converting messages to their wire protocol; it is
    # persisted so a practice window can show only its own tagged turns while
    # the model still receives the complete session transcript.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict  # JSON Schema


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMResponse(BaseModel):
    text: str = ""
    # Provider-specific assistant metadata.  DeepSeek requires this exact
    # value to be echoed on the next request when thinking + tools are used.
    reasoning_content: str | None = None
    # Internal protocol provenance; never render this as transcript content.
    reasoning_provider: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str | None = None
    usage: Usage | None = None
    provider: str | None = None
    model: str | None = None

    def first_tool_arguments(self) -> dict:
        if not self.tool_calls:
            raise ValueError("响应中没有 tool_call")
        return self.tool_calls[0].arguments
