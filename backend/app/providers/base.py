from typing import Protocol


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
