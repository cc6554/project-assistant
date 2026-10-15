from agent_coach.legacy.core.llm import openai_compat


def test_keyless_provider_does_not_inherit_unrelated_openai_key(monkeypatch):
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-test-key")

    def fake_sdk(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(openai_compat, "OpenAI", fake_sdk)
    openai_compat.OpenAICompatibleClient(
        base_url="http://localhost:11434/v1", api_key=None, model="local-model"
    )
    assert captured["api_key"] == "not-needed"


def test_explicit_provider_key_is_preserved(monkeypatch):
    captured = {}
    monkeypatch.setattr(openai_compat, "OpenAI", lambda **kw: captured.update(kw))
    openai_compat.OpenAICompatibleClient(
        base_url="https://example.test/v1", api_key="explicit-test-key", model="model"
    )
    assert captured["api_key"] == "explicit-test-key"
