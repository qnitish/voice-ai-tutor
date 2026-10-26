"""Speech provider registry.

To add a vendor (ElevenLabs, OpenAI, ...), write a class matching
STTProvider / TTSProvider in its own module, register it below, and set
STT_PROVIDER / TTS_PROVIDER in .env. A cloned voice only exists at the vendor
that cloned it, so TTS for such a voice goes to that vendor whatever
TTS_PROVIDER says. Tests swap in fakes with set_stt / set_tts.
"""

from functools import cache

from app.config import settings
from app.providers.base import STTProvider, TTSProvider
from app.providers.gnani import GnaniSTT, GnaniTTS
from app.providers.sarvam import SarvamSTT, SarvamTTS
from app.voices import current_voice

STT_PROVIDERS: dict[str, type] = {"gnani": GnaniSTT, "sarvam": SarvamSTT}
TTS_PROVIDERS: dict[str, type] = {"gnani": GnaniTTS, "sarvam": SarvamTTS}


_overrides: dict[str, object] = {}


@cache
def _configured_stt() -> STTProvider:
    return STT_PROVIDERS[settings.stt_provider]()


@cache
def _tts(name: str) -> TTSProvider:
    return TTS_PROVIDERS[name]()


def get_stt() -> STTProvider:
    return _overrides.get("stt") or _configured_stt()


def get_tts() -> TTSProvider:
    """The TTS provider for the current voice (audio.speak sets it)."""
    if "tts" in _overrides:
        return _overrides["tts"]
    voice = current_voice.get()
    if voice and voice.sarvam_id:
        return _tts("sarvam")
    if voice and voice.embedding:
        return _tts("gnani")
    return _tts(settings.tts_provider)


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
