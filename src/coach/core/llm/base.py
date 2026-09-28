"""LLM 客户端抽象基类与可重试异常判定。"""

from __future__ import annotations

import abc
from typing import ClassVar

from ..schema import LLMResponse, Message, ToolSpec

# 这些 HTTP 状态码值得换到 fallback 链上的下一家再试：
# 401/403 可能是这一家的 key 失效；408/409/429 是限流与冲突；5xx 是对方故障。
RETRYABLE_STATUS = {401, 403, 408, 409, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """调用 provider 失败的统一包装。"""

    def __init__(self, provider: str, message: str, *, status_code: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code is None or self.status_code in RETRYABLE_STATUS


class LLMClient(abc.ABC):
    """一个 LLMClient 只绑定「一个端点 + 一个模型」。切换模型由 router 新建实例。"""

    api_mode: ClassVar[str] = "base"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 120.0,
        extra_body: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        # OpenAI SDK 不接受空 api_key；本地端点传占位值即可
        self.api_key = api_key or "not-needed"
        self.model = model
        self.timeout = timeout
        self.extra_body = extra_body or {}

    @abc.abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        force_tool: bool | str = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """发起一次对话补全。

        force_tool:
            False  - 模型自行决定是否调工具；
            True   - 必须调用某个工具；
            str    - 必须调用指定名称的工具（结构化输出的跨厂商通用手段）。
        """
