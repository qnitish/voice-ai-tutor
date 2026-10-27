import asyncio
import hashlib
import io
import subprocess
import wave
from pathlib import Path

from app.config import settings
from app.language import current_stt_language
from app.providers import get_stt, get_tts
from app.schemas import Spoken
from app.voices import current_voice, get_voice, is_default

TTS_RATE = 24000
STT_RATE = 16000


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _decode_pcm(args: list[str], data: bytes | None, rate: int) -> bytes:
    cmd = ["ffmpeg", "-v", "error", *args, "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"]
    return subprocess.run(cmd, input=data, capture_output=True, check=True).stdout


def to_wav(data: bytes, rate: int) -> tuple[bytes, int]:
    """Re-encode any audio to mono 16-bit WAV with a correct header.

    Some providers stream WAV with a placeholder length in the header, so we
    always rewrite it. Returns (wav bytes, duration in ms).
    """
    pcm = _decode_pcm(["-i", "pipe:0"], data, rate)
    return _pcm_to_wav(pcm, rate), round(len(pcm) / 2 / rate * 1000)


def split_for_stt(path: Path, start_s: float, duration_s: float, chunk_s: int) -> list[bytes]:
    """Cut a slice of a long recording into STT-sized WAV chunks."""
    pcm = _decode_pcm(["-ss", str(start_s), "-t", str(duration_s), "-i", str(path)], None, STT_RATE)
    size = chunk_s * STT_RATE * 2
    return [_pcm_to_wav(pcm[i : i + size], STT_RATE) for i in range(0, len(pcm), size)]


def save_media(rel_path: str, data: bytes) -> str:
    path = settings.media_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/media/{rel_path}"


async def speak(text: str, rel_path: str, voice: str | None = None) -> Spoken:
    """TTS the text in a lesson's voice (None = default), store it under media/, and return its URL and duration."""
    token = current_voice.set(get_voice(voice))
    try:
        raw = await get_tts().synthesize(text)
    finally:
        current_voice.reset(token)
    wav, duration_ms = await asyncio.to_thread(to_wav, raw, TTS_RATE)
    return Spoken(text=text, audio_url=save_media(rel_path, wav), duration_ms=duration_ms)


async def transcribe(wav: bytes, language: str, **context) -> str:
    """STT in the lesson's language (`context` = session_id/doubt_id for logging).

    Contract for B4 (docs/plan/video-lesson-spec.md 2.5): add retries on network/429/5xx and the stt.done event.
    """
    token = current_stt_language.set(language)
    try:
        return await get_stt().transcribe(wav)
    finally:
        current_stt_language.reset(token)


async def speak_cached(text: str, voice: str | None = None) -> Spoken:
    """Like speak(), but reuses audio for fixed lines that recur (feedback, prompts), per voice."""
    folder = "cache" if is_default(voice) else f"cache/{voice}"
    rel_path = f"{folder}/{hashlib.sha1(text.encode()).hexdigest()[:16]}.wav"
    path = settings.media_dir / rel_path
    if not path.exists():
        return await speak(text, rel_path, voice)
    with wave.open(str(path)) as w:
        duration_ms = round(w.getnframes() / w.getframerate() * 1000)
    return Spoken(text=text, audio_url=f"/media/{rel_path}", duration_ms=duration_ms)
