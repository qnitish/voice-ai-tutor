"""Gnani Vachana APIs. Docs: https://docs.gnani.ai"""

import asyncio

import httpx

from app.config import settings
from app.voices import current_voice

BASE_URL = "https://api.vachana.ai"
RETRY_STATUSES = {429, 500, 503}


async def _post(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    for attempt in range(4):
        resp = await client.post(url, headers={"X-API-Key-ID": settings.gnani_api_key}, **kwargs)
        if resp.status_code not in RETRY_STATUSES:
            break
        await asyncio.sleep(1.5 * (attempt + 1))
    if resp.is_error:
        raise RuntimeError(f"Gnani {url} failed: {resp.status_code} {resp.text[:300]}")
    return resp


async def embed_voice(sample_wav: bytes) -> dict:
    """speaker_embedding for voice cloning, from 5-30 s of clean speech."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as client:
        resp = await _post(client, "/api/v1/tts/voice-clone/embeddings", files={"audio_file": ("sample.wav", sample_wav, "audio/wav")})
    return resp.json()["data"]["voice_clone_embedding"]


class GnaniSTT:
    max_seconds = 30

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=60)
        # Like TTS, the account allows only one STT request in flight at a time (429 otherwise)
        self.lock = asyncio.Semaphore(1)

    async def transcribe(self, wav: bytes) -> str:
        async with self.lock:
            return await self._transcribe(wav)

    async def _transcribe(self, wav: bytes) -> str:
        resp = await _post(
            self.client,
            "/stt/v3",
            files={"audio_file": ("audio.wav", wav, "audio/wav")},
            data={"language_code": settings.gnani_stt_language},
        )
        return resp.json().get("transcript", "").strip()


class GnaniTTS:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=60)
        # The account allows only one TTS request in flight at a time
        self.lock = asyncio.Semaphore(1)

    async def synthesize(self, text: str) -> bytes:
        async with self.lock:
            return await self._synthesize(text)

    async def _synthesize(self, text: str) -> bytes:
        voice = current_voice.get()
        if voice and voice.embedding:
            # Cloned voice
            who = {"speaker_embedding": voice.embedding, "model": "vachana-vc-v1"}
        else:
            who = {
                "voice": voice.stock if voice and voice.stock else settings.gnani_tts_voice,
                "model": settings.gnani_tts_model,
                "language": settings.gnani_tts_language,
            }
        resp = await _post(
            self.client,
            "/api/v1/tts/inference",
            json={
                "text": text,
                **who,
                "audio_config": {
                    "sample_rate": 24000,
                    "num_channels": 1,
                    "sample_width": 2,
                    "encoding": "linear_pcm",
                    "container": "wav",
                },
            },
        )
        return resp.content
