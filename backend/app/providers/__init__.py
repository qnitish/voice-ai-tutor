"""Speech provider registry.

To add a vendor (Sarvam, ElevenLabs, OpenAI, ...), write a class matching
STTProvider / TTSProvider in its own module, register it below, and set
STT_PROVIDER / TTS_PROVIDER in .env.
"""

from functools import cache

from app.config import settings
from app.providers.base import STTProvider, TTSProvider
from app.providers.gnani import GnaniSTT, GnaniTTS

STT_PROVIDERS: dict[str, type] = {"gnani": GnaniSTT}
TTS_PROVIDERS: dict[str, type] = {"gnani": GnaniTTS}


@cache
def get_stt() -> STTProvider:
    return STT_PROVIDERS[settings.stt_provider]()


@cache
def get_tts() -> TTSProvider:
    return TTS_PROVIDERS[settings.tts_provider]()
