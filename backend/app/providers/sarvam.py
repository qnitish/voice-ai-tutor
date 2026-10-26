"""Sarvam AI APIs (Saaras STT, Bulbul TTS, instant voice cloning). Docs: https://docs.sarvam.ai"""

import asyncio
import base64
import io
import re
import wave

import httpx

from app.config import settings
from app.language import STT_CODES, current_stt_language
from app.voices import current_voice

BASE_URL = "https://api.sarvam.ai"
RETRY_STATUSES = {429, 500, 502, 503, 504}
# /voices/clone rejects text over 1000 characters; longer lines are split at sentence ends
MAX_CHARS = 900
SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


async def _post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    for attempt in range(5):
        try:
            resp = await client.post(url, headers={"api-subscription-key": settings.sarvam_api_key}, **kwargs)
        except httpx.TransportError:
            # Generation occasionally hangs past the timeout; a retry usually goes through
            if attempt == 4:
                raise
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code not in RETRY_STATUSES:
            break
        await asyncio.sleep(2 * (attempt + 1))
    if resp.is_error:
        raise RuntimeError(f"Sarvam {url} failed: {resp.status_code} {resp.text[:300]}")
    return resp


async def create_voice(sample_wav: bytes, name: str, language: str) -> str:
    """Save a cloned voice from 10-15 s of clean speech; returns its voice_id (svc-...)."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as client:
        resp = await _post(
            client,
            "/voices/create",
            files={"file": ("sample.wav", sample_wav, "audio/wav")},
            data={"name": name, "language": language},
        )
    return resp.json()["data"]["voice_id"]


def _split(text: str) -> list[str]:
    parts: list[str] = []
    for sentence in SENTENCE_END.split(text.strip()):
        if parts and len(parts[-1]) + 1 + len(sentence) <= MAX_CHARS:
            parts[-1] += " " + sentence
        else:
            parts.extend(sentence[i : i + MAX_CHARS] for i in range(0, len(sentence), MAX_CHARS))
    return parts


def _join_wavs(wavs: list[bytes]) -> bytes:
    frames, params = [], None
    for data in wavs:
        with wave.open(io.BytesIO(data)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)
        out.writeframes(b"".join(frames))
    return buf.getvalue()


class SarvamSTT:
    max_seconds = 30

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=60)
        self.lock = asyncio.Semaphore(settings.sarvam_concurrency)

    async def transcribe(self, wav: bytes) -> str:
        code = STT_CODES.get(current_stt_language.get() or "", settings.sarvam_stt_language)
        async with self.lock:
            resp = await _post(
                self.client,
                "/speech-to-text",
                files={"file": ("audio.wav", wav, "audio/wav")},
                # codemix keeps English terms in Latin script inside Devanagari Hindi, as the routing prompt expects
                data={"model": settings.sarvam_stt_model, "language_code": code, "mode": "transcribe" if code == "en-IN" else "codemix"},
            )
        return resp.json().get("transcript", "").strip()


class SarvamTTS:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=60)
        self.lock = asyncio.Semaphore(settings.sarvam_concurrency)

    async def synthesize(self, text: str) -> bytes:
        voice = current_voice.get()
        voice_id = voice.sarvam_id if voice else None
        parts = await asyncio.gather(*(self._synthesize(p, voice_id) for p in _split(text)))
        return parts[0] if len(parts) == 1 else _join_wavs(parts)

    async def _synthesize(self, text: str, voice_id: str | None) -> bytes:
        # Narration is Roman-script (Hinglish or English); en-IN reads it more naturally than hi-IN
        async with self.lock:
            if voice_id:
                resp = await _post(
                    self.client,
                    "/voices/clone",
                    data={"voice_id": voice_id, "text": text, "language_code": settings.sarvam_tts_language, "speech_sample_rate": "24000"},
                )
                return base64.b64decode(resp.json()["audio_b64"])
            resp = await _post(
                self.client,
                "/text-to-speech",
                json={
                    "text": text,
                    "language_code": settings.sarvam_tts_language,
                    "speaker": settings.sarvam_tts_speaker,
                    "model": settings.sarvam_tts_model,
                    "speech_sample_rate": 24000,
                },
            )
            return base64.b64decode("".join(resp.json()["audios"]))
