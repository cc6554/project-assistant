from __future__ import annotations

import base64
import http.client
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any
import wave

import pytest

from agent_coach.speech import (
    SPEECH_MAX_AUDIO_BYTES,
    SPEECH_MAX_AUDIO_SAMPLES,
    SpeechBadAudioError,
    SpeechService,
    _decode_audio_with_pyav,
    _simplify_chinese_text,
    _DecodedAudio,
)
from agent_coach.web import build_service, make_server


def _request(service: Any, server: Any, method: str, path: str, body: Any = None, **headers: str):
    port = int(server.server_address[1])
    request_headers = {
        "Host": f"127.0.0.1:{port}",
        "Authorization": f"Bearer {service.token}",
    }
    request_headers.update(headers)
    raw = None if body is None else json.dumps(body).encode("utf-8")
    if raw is not None:
        request_headers.setdefault("Content-Type", "application/json")
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, raw, request_headers)
    response = client.getresponse()
    data = response.read()
    client.close()
    return response.status, json.loads(data.decode("utf-8"))


class _FakeModel:
    def __init__(self, text: str = "你好") -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, samples: Any, **kwargs: Any):
        self.calls.append(kwargs)
        return iter([SimpleNamespace(text=self.text)]), SimpleNamespace()


def _fake_decoder(_raw: bytes, _mime_type: str) -> _DecodedAudio:
    return _DecodedAudio([0.1, 0.2], rms=0.1, peak=0.2)


@pytest.fixture
def speech_server(tmp_path: Path):
    service = build_service(tmp_path, demo=True)
    fake = _FakeModel()
    service.speech = SpeechService(
        model_path=tmp_path / "fake-model",
        model_loader=lambda: fake,
        decoder=_fake_decoder,
    )
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server, fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _audio_payload(raw: bytes = b"fake audio") -> dict[str, str]:
    return {
        "audio_base64": base64.b64encode(raw).decode("ascii"),
        "mime_type": "audio/wav",
    }


def test_status_is_authenticated_and_does_not_load_model(speech_server):
    service, server, fake = speech_server
    status, _ = _request(service, server, "GET", "/api/speech/status", Authorization="")
    assert status == 401

    status, payload = _request(service, server, "GET", "/api/speech/status")
    assert status == 200
    assert set(payload) == {"available", "mode", "message", "max_duration_seconds"}
    assert payload["mode"] == "offline"
    assert payload["max_duration_seconds"] == 120
    assert fake.calls == []


def test_transcribe_returns_text_and_safe_options(speech_server):
    service, server, fake = speech_server
    status, payload = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())
    assert status == 200
    assert payload == {"text": "你好", "mode": "offline"}
    assert fake.calls[0]["language"] == "zh"
    assert fake.calls[0]["task"] == "transcribe"
    assert "initial_prompt" not in fake.calls[0]
    assert fake.calls[0]["beam_size"] == 1
    assert fake.calls[0]["vad_filter"] is True


def test_transcribe_normalizes_script_without_touching_ascii():
    fake = _FakeModel(text="繁體中文 臺灣開發國際標準體驗廣東話 API v2.0 C++ 代码")
    service = SpeechService(
        model_loader=lambda: fake,
        decoder=_fake_decoder,
    )
    converted = _simplify_chinese_text(fake.text)
    if converted == fake.text and os.name != "nt":
        pytest.skip("需要 OpenCC 或 Windows NLS 简繁转换后端")
    result = service.transcribe_payload(_audio_payload())
    assert result == {"text": "繁体中文 台湾开发国际标准体验广东话 API v2.0 C++ 代码", "mode": "offline"}
    assert "initial_prompt" not in fake.calls[0]


def test_transcribe_preserves_legitimate_repeated_words():
    fake = _FakeModel(text="你好你好，API v2.0")
    service = SpeechService(model_loader=lambda: fake, decoder=_fake_decoder)

    assert service.transcribe_payload(_audio_payload()) == {
        "text": "你好你好，API v2.0",
        "mode": "offline",
    }


def test_transcribe_preserves_model_whitespace_between_mixed_language_segments():
    class SegmentedModel:
        def transcribe(self, samples: Any, **kwargs: Any):
            segments = ["请使用 ", "API ", "v2.0 ", "https://example.com"]
            return iter(SimpleNamespace(text=text) for text in segments), SimpleNamespace()

    service = SpeechService(model_loader=SegmentedModel, decoder=_fake_decoder)

    assert service.transcribe_payload(_audio_payload()) == {
        "text": "请使用 API v2.0 https://example.com",
        "mode": "offline",
    }


def test_transcribe_auth_and_bad_audio_are_bounded(speech_server):
    service, server, _fake = speech_server
    status, _ = _request(
        service,
        server,
        "POST",
        "/api/speech/transcribe",
        _audio_payload(),
        Authorization="Bearer wrong",
    )
    assert status == 401

    status, _ = _request(
        service,
        server,
        "POST",
        "/api/speech/transcribe",
        {"audio_base64": "not-base64", "mime_type": "audio/wav"},
    )
    assert status == 400
    status, _ = _request(
        service,
        server,
        "POST",
        "/api/speech/transcribe",
        {"audio_base64": base64.b64encode(b"x").decode("ascii"), "mime_type": "text/plain"},
    )
    assert status == 400

    oversized = base64.b64encode(b"x" * (SPEECH_MAX_AUDIO_BYTES + 1)).decode("ascii")
    with pytest.raises(SpeechBadAudioError):
        SpeechService(model_loader=lambda: _FakeModel(), decoder=_fake_decoder).transcribe_payload(
            {"audio_base64": oversized, "mime_type": "audio/wav"}
        )


def test_transcribe_rejects_oversized_decoded_duration():
    class OversizedSamples:
        size = SPEECH_MAX_AUDIO_SAMPLES + 1

    service = SpeechService(
        model_loader=lambda: _FakeModel(),
        decoder=lambda _raw, _mime: _DecodedAudio(OversizedSamples(), rms=0.1, peak=0.2),
    )
    with pytest.raises(SpeechBadAudioError):
        service.transcribe_payload(_audio_payload())


def test_silence_does_not_call_model():
    fake = _FakeModel(text="不应产生幻觉")
    service = SpeechService(
        model_loader=lambda: fake,
        decoder=lambda _raw, _mime: _DecodedAudio([0.0], rms=0.0, peak=0.0),
    )
    assert service.transcribe_payload(_audio_payload()) == {"text": "", "mode": "offline"}
    assert fake.calls == []


def test_model_load_failure_can_be_retried_through_status_and_transcribe(speech_server, monkeypatch):
    service, server, _fake = speech_server
    model_path = service.speech.model_path
    model_path.mkdir(parents=True)
    for filename in ("config.json", "tokenizer.json", "model.bin"):
        (model_path / filename).write_text("test fixture", encoding="utf-8")
    monkeypatch.setattr("agent_coach.speech._optional_dependencies_available", lambda: True)

    attempts = 0

    def flaky_loader():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary load failure")
        return _FakeModel()

    service.speech = SpeechService(
        model_path=model_path,
        model_loader=flaky_loader,
        decoder=_fake_decoder,
    )
    status, status_payload = _request(service, server, "GET", "/api/speech/status")
    assert status == 200
    assert status_payload["available"] is True

    status, error_payload = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())
    assert status == 503
    assert error_payload == {"error": "本地语音模型未就绪"}

    status, status_payload = _request(service, server, "GET", "/api/speech/status")
    assert status == 200
    assert status_payload["available"] is True
    assert status_payload["message"] == "本地离线语音模型上次加载失败，可重新录音重试。"

    status, payload = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())
    assert status == 200
    assert payload == {"text": "你好", "mode": "offline"}
    assert service.speech._load_error is False

    status, status_payload = _request(service, server, "GET", "/api/speech/status")
    assert status == 200
    assert status_payload["available"] is True
    assert status_payload["message"] == "本地离线语音模型可用。"


def test_inference_error_has_retryable_message_and_does_not_mark_model_unavailable(speech_server):
    service, server, _fake = speech_server

    class BrokenModel(_FakeModel):
        def transcribe(self, samples: Any, **kwargs: Any):
            raise RuntimeError("private decoder details")

    service.speech = SpeechService(model_loader=BrokenModel, decoder=_fake_decoder)
    status, payload = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())
    assert status == 500
    assert payload == {"error": "本地语音识别失败，请重新录音后重试"}
    assert service.speech._load_error is False


def test_pyav_decode_flushes_short_pcm_tail_without_disk_or_mic():
    pytest.importorskip("av")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(bytes(2 * 80))
    decoded = _decode_audio_with_pyav(buffer.getvalue(), "audio/wav")
    assert getattr(decoded.samples, "size", 0) == 160
    assert decoded.rms == 0.0
    assert decoded.peak == 0.0


def test_busy_transcription_returns_409(speech_server):
    service, server, _fake = speech_server
    started = threading.Event()
    release = threading.Event()

    class BlockingModel(_FakeModel):
        def transcribe(self, samples: Any, **kwargs: Any):
            self.calls.append(kwargs)
            started.set()
            assert release.wait(timeout=3)
            return iter([SimpleNamespace(text="完成")]), SimpleNamespace()

    blocking = BlockingModel()
    service.speech = SpeechService(
        model_loader=lambda: blocking,
        decoder=_fake_decoder,
    )
    first: dict[str, Any] = {}

    def first_request() -> None:
        first["result"] = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())

    worker = threading.Thread(target=first_request, daemon=True)
    worker.start()
    assert started.wait(timeout=3)
    status, payload = _request(service, server, "POST", "/api/speech/transcribe", _audio_payload())
    assert status == 409
    assert "语音识别" in payload["error"]
    release.set()
    worker.join(timeout=3)
    assert first["result"] == (200, {"text": "完成", "mode": "offline"})
