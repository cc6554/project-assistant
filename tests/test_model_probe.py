from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_coach.legacy.core.schema import LLMResponse, ToolCall
from agent_coach.web import MODEL_TEST_MAX_TOKENS, MODEL_TEST_TIMEOUT, _message_payload, build_service


def _draft(**overrides):
    value = {
        "api_mode": "chat_completions",
        "base_url": "http://localhost:11434/v1",
        "model": "tool-model",
    }
    value.update(overrides)
    return value


class _Provider:
    def __init__(self):
        self.timeout = 120.0
        self.extra_body = {"keep": "me"}


class _ProbeRouter:
    def __init__(self, response=None, error=None):
        self.provider = _Provider()
        self.config = SimpleNamespace(providers={"gui": self.provider})
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def _service(tmp_path, router):
    service = build_service(tmp_path, demo=True)
    service._build_model_router = lambda settings: router
    return service


def test_probe_uses_a_reasonable_budget_and_never_executes_tool(tmp_path):
    router = _ProbeRouter(
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="probe", name="connection_test", arguments={"ok": True})],
        )
    )
    service = _service(tmp_path, router)

    result = service.test_model_settings(_draft(api_key="probe-key"))

    assert result["ok"] is True
    assert result["tool_call"] is True
    assert router.calls[0][1]["max_tokens"] >= 512
    assert router.calls[0][1]["max_tokens"] == MODEL_TEST_MAX_TOKENS
    assert router.provider.timeout == MODEL_TEST_TIMEOUT
    assert "tools" in router.calls[0][1]
    assert "force_tool" in router.calls[0][1]
    assert not hasattr(router, "execute")


@pytest.mark.parametrize(
    ("base_url", "api_mode", "should_disable"),
    [
        ("https://api.deepseek.com/v1", "chat_completions", True),
        ("https://api.deepseek.com/v1", "anthropic_messages", False),
        ("https://proxy.example.test/v1", "chat_completions", False),
    ],
)
def test_probe_thinking_override_is_scoped_to_official_deepseek_chat(
    tmp_path, base_url, api_mode, should_disable
):
    router = _ProbeRouter(
        LLMResponse(
            text="",
            tool_calls=[ToolCall(id="probe", name="connection_test", arguments={"ok": True})],
        )
    )
    service = _service(tmp_path, router)

    result = service.test_model_settings(
        _draft(base_url=base_url, api_mode=api_mode, api_key="probe-key")
    )

    assert result["ok"] is True
    if should_disable:
        assert router.provider.extra_body["thinking"] == {"type": "disabled"}
        assert router.provider.extra_body["keep"] == "me"
    else:
        assert router.provider.extra_body == {"keep": "me"}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (402, "balance_insufficient"),
        (400, "invalid_request"),
        (422, "invalid_request"),
        (404, "endpoint_failed"),
        (429, "rate_limited"),
        (500, "service_unavailable"),
        (503, "service_unavailable"),
    ],
)
def test_probe_http_error_categories_are_stable(tmp_path, status, expected):
    error = RuntimeError("provider raw response with Authorization: Bearer secret")
    error.status_code = status
    service = _service(tmp_path, _ProbeRouter(error=error))

    result = service.test_model_settings(_draft(api_key="secret"))

    assert result["ok"] is False
    assert result["error"] == expected
    assert result["code"] == expected
    assert "secret" not in str(result)
    assert "provider raw response" not in str(result)


def test_probe_distinguishes_timeout_length_and_tool_response(tmp_path):
    timeout = _service(tmp_path, _ProbeRouter(error=TimeoutError("SDK timeout body")))
    assert timeout.test_model_settings(_draft(api_key="key"))["error"] == "timeout"

    truncated = _service(
        tmp_path / "length",
        _ProbeRouter(LLMResponse(text="partial", stop_reason="length")),
    )
    assert truncated.test_model_settings(_draft(api_key="key"))["error"] == "length_truncated"

    unsupported = _service(
        tmp_path / "unsupported",
        _ProbeRouter(LLMResponse(text="I cannot call tools", tool_calls=[])),
    )
    assert unsupported.test_model_settings(_draft(api_key="key"))["error"] == "tool_unsupported"

    no_response = _service(
        tmp_path / "empty",
        _ProbeRouter(LLMResponse(text="", tool_calls=[])),
    )
    assert no_response.test_model_settings(_draft(api_key="key"))["error"] == "no_response"


def test_invalid_deepseek_model_gets_a_hint_without_being_blocked(tmp_path):
    error = RuntimeError("invalid_request_error: model not found")
    error.status_code = 400
    service = _service(
        tmp_path,
        _ProbeRouter(error=error),
    )

    result = service.test_model_settings(
        _draft(
            base_url="https://api.deepseek.com/v1",
            model="deepseek",
            api_key="key",
        )
    )

    assert result["error"] == "invalid_model"
    assert "deepseek-flash" in result["message"]


def test_reasoning_content_is_not_exposed_in_message_payload():
    class MessageWithReasoning:
        def model_dump(self, mode="json"):
            return {
                "role": "assistant",
                "content": "visible answer",
                "reasoning_content": "private trace",
            }

    payload = _message_payload(MessageWithReasoning())
    assert payload == {"role": "assistant", "content": "visible answer"}
