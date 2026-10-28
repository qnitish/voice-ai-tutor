from typing import Protocol

import httpx


class ProviderError(RuntimeError):
    """A speech API answered with an error status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class ProviderUnavailable(ProviderError):
    """429 or 5xx: the request may well succeed if repeated (app.telemetry.with_retries)."""


# What audio.speak / audio.transcribe retry: timeouts, connection errors and ProviderUnavailable.
# Providers make one attempt per call, so the total is bounded by the *_retries settings.
RETRYABLE: tuple[type[BaseException], ...] = (httpx.TransportError, ProviderUnavailable)


def raise_for_status(resp: httpx.Response, vendor: str) -> None:
    if resp.is_error:
        message = f"{vendor} {resp.url.path} failed: {resp.status_code} {resp.text[:300]}"
        cls = ProviderUnavailable if resp.status_code == 429 or resp.status_code >= 500 else ProviderError
        raise cls(message, resp.status_code)


class STTProvider(Protocol):
    # Longest clip one request accepts; longer audio must be split by the caller
    max_seconds: int

    async def transcribe(self, wav: bytes) -> str:
        """Transcribe 16 kHz mono WAV audio."""
        ...


class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return WAV audio for the text."""
        ...
