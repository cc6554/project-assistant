"""Local, offline speech-to-text service.

The desktop voice path deliberately stays independent from the configured LLM
provider.  ``faster-whisper`` and PyAV are optional and imported only when a
transcription is requested, so opening the web service does not allocate the
model or require a network connection.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterable
import importlib.util
import io
import os
from pathlib import Path
import sys
import threading
from typing import Any


SPEECH_MODE = "offline"
SPEECH_MAX_DURATION_SECONDS = 120
SPEECH_SAMPLE_RATE = 16_000
SPEECH_MAX_AUDIO_BYTES = 8 * 1024 * 1024
SPEECH_MAX_BODY_BYTES = 12 * 1024 * 1024
SPEECH_MAX_AUDIO_SAMPLES = SPEECH_MAX_DURATION_SECONDS * SPEECH_SAMPLE_RATE
# A normal decoder frame is a few thousand samples.  Refuse an implausibly
# large single frame before converting it to a NumPy array.
SPEECH_MAX_FRAME_SAMPLES = 1_000_000
SPEECH_MAX_MIME_BYTES = 128
_LCMAP_SIMPLIFIED_CHINESE = 0x02000000


class SpeechError(Exception):
    """Base class for fixed, user-safe speech errors."""


class SpeechBadAudioError(ValueError, SpeechError):
    """The request or decoded audio is invalid or exceeds a safety limit."""


class SpeechBusyError(RuntimeError, SpeechError):
    """Another transcription is already using the single local model."""


class SpeechNotReadyError(RuntimeError, SpeechError):
    """The optional runtime/model is not available in this installation."""


class SpeechRecognitionError(SpeechError):
    """The runtime was ready, but this audio could not be transcribed."""


class _DecodedAudio:
    """Small internal wrapper that keeps decoder details out of the model call."""

    def __init__(self, samples: Any, *, rms: float, peak: float):
        self.samples = samples
        self.rms = float(rms)
        self.peak = float(peak)


def speech_model_path() -> Path:
    """Return the only permitted local model location.

    A frozen executable must use its bundled ``_MEIPASS/models`` directory.
    Source runs may override the path for development; no model name is ever
    passed to faster-whisper, which prevents an implicit download.
    """

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "models" / "faster-whisper-base"
    configured = os.environ.get("AGENT_COACH_SPEECH_MODEL", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    # speech.py -> agent_coach -> src -> package root
    return Path(__file__).resolve().parents[2] / "models" / "faster-whisper-base"


def _optional_dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("av", "numpy", "faster_whisper"))


def _status_message(*, model_path: Path, load_error: bool = False) -> tuple[bool, str]:
    if not model_path.is_dir():
        return False, "本地离线语音模型未就绪。"
    if not (model_path / "config.json").is_file() or not (model_path / "tokenizer.json").is_file():
        return False, "本地离线语音模型文件不完整。"
    if not any((model_path / name).is_file() for name in ("model.bin", "model.safetensors")):
        return False, "本地离线语音模型文件不完整。"
    if not _optional_dependencies_available():
        return False, "本地离线语音运行依赖未就绪。"
    if load_error:
        return True, "本地离线语音模型上次加载失败，可重新录音重试。"
    return True, "本地离线语音模型可用。"


def _fixed_text(value: Any, *, name: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpeechBadAudioError(f"{name} 无效")
    value = value.strip()
    if len(value.encode("utf-8")) > max_bytes:
        raise SpeechBadAudioError(f"{name} 无效")
    return value


def _decode_audio_base64(payload: dict[str, Any]) -> tuple[bytes, str]:
    encoded = payload.get("audio_base64")
    if not isinstance(encoded, str) or not encoded:
        raise SpeechBadAudioError("音频数据无效")
    # 8 MiB binary audio expands to about 10.7 MiB in base64; keep the JSON
    # body limit separate so a malformed request cannot allocate unbounded data.
    if len(encoded.encode("ascii", errors="ignore")) > 11 * 1024 * 1024:
        raise SpeechBadAudioError("音频数据过大")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        raise SpeechBadAudioError("音频数据无效") from exc
    if not raw or len(raw) > SPEECH_MAX_AUDIO_BYTES:
        raise SpeechBadAudioError("音频数据过大或为空")
    mime_type = _fixed_text(payload.get("mime_type"), name="音频类型", max_bytes=SPEECH_MAX_MIME_BYTES)
    if not mime_type.casefold().startswith("audio/"):
        raise SpeechBadAudioError("音频类型无效")
    return raw, mime_type


def _as_frames(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    return (value,)


def _decode_audio_with_pyav(raw: bytes, _mime_type: str) -> _DecodedAudio:
    """Decode in bounded chunks, resampling to mono 16 kHz in memory."""

    try:
        import av  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SpeechNotReadyError("本地离线语音运行依赖未就绪") from exc

    chunks: list[Any] = []
    sample_count = 0

    def append_output(output: Any, np: Any) -> None:
        nonlocal sample_count
        output_samples = int(getattr(output, "samples", 0) or 0)
        if output_samples <= 0:
            return
        if output_samples > SPEECH_MAX_FRAME_SAMPLES or sample_count + output_samples > SPEECH_MAX_AUDIO_SAMPLES:
            raise SpeechBadAudioError("音频时长超过 120 秒")
        values = np.asarray(output.to_ndarray(), dtype=np.float32).reshape(-1)
        if values.size > SPEECH_MAX_AUDIO_SAMPLES - sample_count:
            raise SpeechBadAudioError("音频时长超过 120 秒")
        if values.size:
            chunks.append(values)
            sample_count += int(values.size)

    try:
        with av.open(io.BytesIO(raw), mode="r") as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise SpeechBadAudioError("音频中没有音轨")
            resampler = av.audio.resampler.AudioResampler(
                format="flt",
                layout="mono",
                rate=SPEECH_SAMPLE_RATE,
            )
            for frame in container.decode(stream):
                source_samples = int(getattr(frame, "samples", 0) or 0)
                if source_samples <= 0 or source_samples > SPEECH_MAX_FRAME_SAMPLES:
                    raise SpeechBadAudioError("音频帧无效或过大")
                for output in _as_frames(resampler.resample(frame)):
                    append_output(output, np)
            # Resampling can retain a short tail until it is explicitly
            # flushed.  Keep it in memory and apply the same sample bound.
            for output in _as_frames(resampler.resample(None)):
                append_output(output, np)
    except SpeechError:
        raise
    except Exception as exc:
        # PyAV/FFmpeg errors may contain file paths, codec details, or input
        # data.  Keep them out of the API and logs.
        raise SpeechBadAudioError("音频格式无法识别") from exc

    if not chunks:
        raise SpeechBadAudioError("音频没有可识别样本")
    samples = np.concatenate(chunks).astype(np.float32, copy=False)
    if not np.isfinite(samples).all():
        raise SpeechBadAudioError("音频样本无效")
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    return _DecodedAudio(samples, rms=rms, peak=peak)


def _opencc_convert(text: str) -> str | None:
    """Convert Traditional Chinese with OpenCC when it is installed.

    OpenCC is an optional pure-Python runtime dependency for source and
    non-Windows builds.  Importing it lazily keeps the regular web service and
    the legacy non-speech build independent from the converter.
    """

    try:
        from opencc import OpenCC  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        converted = OpenCC("t2s").convert(text)
    except Exception:
        # A broken optional install must not make local transcription fail.
        return None
    return converted if isinstance(converted, str) else None


def _windows_native_convert(text: str) -> str | None:
    """Use Windows NLS for complete Traditional-to-Simplified conversion.

    ``LCMapStringEx`` ships with Windows and handles the full Unicode mapping;
    no hand-maintained character table or network service is involved.  The
    ASCII portions of a transcript (identifiers, versions, URLs and numbers)
    are preserved by the NLS script mapping.
    """

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        mapper = ctypes.windll.kernel32.LCMapStringEx
        mapper.restype = ctypes.c_int
        mapper.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_uint32,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_wchar),
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.LPVOID,
            ctypes.c_void_p,
        ]
        # Passing -1 includes the terminating NUL and lets the API size the
        # destination correctly for a UTF-16 Windows string.
        required = mapper(
            "zh-CN",
            _LCMAP_SIMPLIFIED_CHINESE,
            text,
            -1,
            None,
            0,
            None,
            None,
            0,
        )
        if required <= 0:
            return None
        destination = ctypes.create_unicode_buffer(required)
        result = mapper(
            "zh-CN",
            _LCMAP_SIMPLIFIED_CHINESE,
            text,
            -1,
            destination,
            required,
            None,
            None,
            0,
        )
        if result <= 0:
            return None
        return destination.value
    except Exception:
        return None


def _simplify_chinese_text(value: str) -> str:
    """Return transcript text in Simplified Chinese without touching ASCII."""

    text = value.strip()
    if not text:
        return ""
    converted = _opencc_convert(text)
    if converted is None:
        converted = _windows_native_convert(text)
    return converted.strip() if isinstance(converted, str) else text


class SpeechService:
    """Lazy local Whisper service with one in-flight transcription."""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        model_loader: Callable[[], Any] | None = None,
        decoder: Callable[[bytes, str], _DecodedAudio] | None = None,
    ) -> None:
        self.model_path = Path(model_path) if model_path is not None else speech_model_path()
        self._model_loader = model_loader
        self._decoder = decoder or _decode_audio_with_pyav
        self._model: Any | None = None
        self._load_error = False
        self._model_lock = threading.Lock()
        self._busy_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        available, message = _status_message(model_path=self.model_path, load_error=self._load_error)
        return {
            "available": bool(available),
            "mode": SPEECH_MODE,
            "message": message,
            "max_duration_seconds": SPEECH_MAX_DURATION_SECONDS,
        }

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            if self._model_loader is not None:
                try:
                    self._model = self._model_loader()
                except SpeechError:
                    raise
                except Exception as exc:
                    self._load_error = True
                    raise SpeechNotReadyError("本地离线语音模型加载失败") from exc
                self._load_error = False
                return self._model
            if not self.model_path.is_dir():
                self._load_error = True
                raise SpeechNotReadyError("本地离线语音模型未就绪")
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]

                self._model = WhisperModel(
                    str(self.model_path),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=2,
                    num_workers=1,
                    local_files_only=True,
                )
            except SpeechError:
                raise
            except Exception as exc:
                self._load_error = True
                raise SpeechNotReadyError("本地离线语音模型加载失败") from exc
            self._load_error = False
            return self._model

    @staticmethod
    def _transcribe(model: Any, decoded: _DecodedAudio) -> str:
        if decoded.rms < 0.003 and decoded.peak < 0.02:
            return ""
        try:
            segments, _info = model.transcribe(
                decoded.samples,
                language="zh",
                task="transcribe",
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            pieces: list[str] = []
            for segment in segments:
                text = getattr(segment, "text", "")
                if isinstance(text, str) and text.strip():
                    pieces.append(text)
            return _simplify_chinese_text("".join(pieces))
        except Exception as exc:
            raise SpeechRecognitionError("本地离线语音识别失败") from exc

    @staticmethod
    def _validate_decoded(decoded: _DecodedAudio) -> None:
        try:
            count_value = getattr(decoded.samples, "size", None)
            count = int(count_value if count_value is not None else len(decoded.samples))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SpeechBadAudioError("音频解码失败") from exc
        if count <= 0:
            raise SpeechBadAudioError("音频没有可识别样本")
        if count > SPEECH_MAX_AUDIO_SAMPLES:
            raise SpeechBadAudioError("音频时长超过 120 秒")

    def transcribe_payload(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise SpeechBadAudioError("请求体无效")
        if not self._busy_lock.acquire(blocking=False):
            raise SpeechBusyError("语音识别正在进行")
        try:
            raw, mime_type = _decode_audio_base64(payload)
            try:
                decoded = self._decoder(raw, mime_type)
            except SpeechError:
                raise
            except Exception as exc:
                raise SpeechBadAudioError("音频解码失败") from exc
            if not isinstance(decoded, _DecodedAudio):
                raise SpeechBadAudioError("音频解码失败")
            self._validate_decoded(decoded)
            if decoded.rms < 0.003 and decoded.peak < 0.02:
                return {"text": "", "mode": SPEECH_MODE}
            model = self._load_model()
            text = self._transcribe(model, decoded)
            return {"text": text, "mode": SPEECH_MODE}
        finally:
            self._busy_lock.release()


__all__ = [
    "SPEECH_MAX_AUDIO_BYTES",
    "SPEECH_MAX_BODY_BYTES",
    "SPEECH_MAX_DURATION_SECONDS",
    "SPEECH_MODE",
    "SpeechBadAudioError",
    "SpeechBusyError",
    "SpeechNotReadyError",
    "SpeechRecognitionError",
    "SpeechService",
    "_DecodedAudio",
    "_simplify_chinese_text",
    "speech_model_path",
]
