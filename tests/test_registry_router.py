"""注册表与 fallback 路由测试（不发真实网络请求）。"""

import os

import pytest

from coach.core.llm.base import ProviderError
from coach.core.llm.openai_compat import OpenAICompatibleClient
from coach.core.llm.registry import load_config
from coach.core.llm.router import ModelRouter
from coach.core.schema import LLMResponse, Message

YAML_TEMPLATE = """
providers:
  good:
    api_mode: chat_completions
    base_url: https://api.good.test/v1
    api_key_env: GOOD_KEY
  nokey:
    api_mode: chat_completions
    base_url: https://api.nokey.test/v1
    api_key_env: MISSING_KEY
  local:
    api_mode: chat_completions
    base_url: http://localhost:11434/v1
    api_key_env: null
tasks:
  default:
    - {provider: good, model: m-good}
  vision:
    - {provider: nokey, model: m-nokey}
    - {provider: good, model: m-good}
"""


def _write_config(tmp_path):
    os.environ["GOOD_KEY"] = "good-secret"
    os.environ.pop("MISSING_KEY", None)
    cfg_path = tmp_path / "providers.yaml"
    cfg_path.write_text(YAML_TEMPLATE, encoding="utf-8")
    return cfg_path


def test_load_config_resolves_keys(tmp_path):
    cfg = load_config(_write_config(tmp_path))
    assert cfg.providers["good"].api_key == "good-secret"
    assert cfg.providers["nokey"].api_key is None
    assert cfg.providers["nokey"].key_required is True
    assert cfg.providers["local"].key_required is False


def test_missing_provider_raises(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text(
        """
providers:
  a: {api_mode: chat_completions, base_url: https://x/v1, api_key_env: K}
tasks:
  default:
    - {provider: ghost, model: m}
""",
        encoding="utf-8",
    )
    os.environ["K"] = "k"
    with pytest.raises(ValueError, match="ghost"):
        load_config(cfg_path)


class _FakeClient(OpenAICompatibleClient):
    """跳过真实 SDK 初始化，用预置响应序列驱动。"""

    def __init__(self, responses):  # noqa: super-init-not-called
        self._responses = list(responses)

    def complete(self, messages, **kwargs):
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_clients(monkeypatch, router, clients):
    def fake_build(entry, model):
        return clients[entry.name]

    monkeypatch.setattr(router, "_build_client", fake_build)


def test_fallback_skips_missing_key_then_succeeds(tmp_path, monkeypatch):
    cfg = load_config(_write_config(tmp_path))
    router = ModelRouter(cfg)
    _patch_clients(
        monkeypatch,
        router,
        {
            "good": _FakeClient(
                [LLMResponse(text="ok", tool_calls=[])]
            ),
        },
    )
    resp = router.complete("vision", [Message(role="user", content="ping")])
    assert resp.text == "ok"
    assert resp.provider == "good"


def test_fallback_moves_past_retryable_error(tmp_path, monkeypatch):
    cfg = load_config(_write_config(tmp_path))
    cfg.tasks["default"] = [
        cfg.tasks["default"][0],
        type(cfg.tasks["default"][0])(provider="good", model="m-good-2"),
    ]
    router = ModelRouter(cfg)
    _patch_clients(
        monkeypatch,
        router,
        {
            "good": _FakeClient(
                [
                    ProviderError("good", "rate limited", status_code=429),
                    LLMResponse(text="second-ok"),
                ]
            ),
        },
    )
    resp = router.complete("default", [Message(role="user", content="ping")])
    assert resp.text == "second-ok"


def test_non_retryable_error_raises_immediately(tmp_path, monkeypatch):
    cfg = load_config(_write_config(tmp_path))
    router = ModelRouter(cfg)
    _patch_clients(
        monkeypatch,
        router,
        {
            "good": _FakeClient(
                [ProviderError("good", "bad request", status_code=400)]
            ),
        },
    )
    with pytest.raises(ProviderError, match="bad request"):
        router.complete("default", [Message(role="user", content="ping")])


class _RecordingFakeClient:
    """记录每次调用的 force_tool，用于验证 tool_choice 降级。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.force_values = []

    def complete(self, messages, **kwargs):
        self.force_values.append(kwargs.get("force_tool"))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_force_tool_choice_400_degrades_to_optional(tmp_path, monkeypatch):
    from coach.core.schema import ToolCall

    cfg = load_config(_write_config(tmp_path))
    router = ModelRouter(cfg)
    fake = _RecordingFakeClient(
        [
            ProviderError(
                "good", "Thinking mode does not support this tool_choice", status_code=400
            ),
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="emit_result", arguments={"city": "深圳"})
                ],
            ),
        ]
    )
    _patch_clients(monkeypatch, router, {"good": fake})

    result = router.complete_json(
        "default",
        [Message(role="user", content="q")],
        {"type": "object", "properties": {"city": {"type": "string"}}},
    )

    assert result == {"city": "深圳"}
    assert fake.force_values == ["emit_result", False]


def test_other_400_does_not_trigger_degrade(tmp_path, monkeypatch):
    from coach.core.schema import ToolSpec
    cfg = load_config(_write_config(tmp_path))
    router = ModelRouter(cfg)
    fake = _RecordingFakeClient(
        [ProviderError("good", "invalid image format", status_code=400)]
    )
    _patch_clients(monkeypatch, router, {"good": fake})

    with pytest.raises(ProviderError, match="invalid image"):
        router.complete(
            "default",
            [Message(role="user", content="ping")],
            tools=[ToolSpec(name="t", description="d", parameters={"type": "object"})],
            force_tool="t",
        )
    assert fake.force_values == ["t"]
