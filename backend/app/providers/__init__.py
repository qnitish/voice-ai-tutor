"""Speech provider registry.

To add a vendor (Sarvam, ElevenLabs, OpenAI, ...), write a class matching
STTProvider / TTSProvider in its own module, register it below, and set
STT_PROVIDER / TTS_PROVIDER in .env. Tests swap in fakes with set_stt / set_tts.
"""

from functools import cache

from app.config import settings
from app.providers.base import STTProvider, TTSProvider
from app.providers.gnani import GnaniSTT, GnaniTTS

STT_PROVIDERS: dict[str, type] = {"gnani": GnaniSTT}
TTS_PROVIDERS: dict[str, type] = {"gnani": GnaniTTS}


_overrides: dict[str, object] = {}


@cache
def _configured_stt() -> STTProvider:
    return STT_PROVIDERS[settings.stt_provider]()


@cache
def _configured_tts() -> TTSProvider:
    return TTS_PROVIDERS[settings.tts_provider]()


def get_stt() -> STTProvider:
    return _overrides.get("stt") or _configured_stt()


def get_tts() -> TTSProvider:
    return _overrides.get("tts") or _configured_tts()


def _override(kind: str, provider: object | None) -> None:
    if provider is None:
        _overrides.pop(kind, None)
    else:
        _overrides[kind] = provider


def set_stt(provider: STTProvider | None) -> None:
    """Use this provider instead of the configured one; None restores the default."""
    _override("stt", provider)


def set_tts(provider: TTSProvider | None) -> None:
    _override("tts", provider)
