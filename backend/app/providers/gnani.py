"""Gnani Vachana APIs. Docs: https://docs.gnani.ai"""

import asyncio

import httpx

from app.config import settings

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


class GnaniSTT:
    max_seconds = 30

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=BASE_URL, timeout=60)

    async def transcribe(self, wav: bytes) -> str:
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
        resp = await _post(
            self.client,
            "/api/v1/tts/inference",
            json={
                "text": text,
                "voice": settings.gnani_tts_voice,
                "model": settings.gnani_tts_model,
                "language": settings.gnani_tts_language,
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
