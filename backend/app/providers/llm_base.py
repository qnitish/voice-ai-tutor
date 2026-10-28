from collections.abc import AsyncIterator
from typing import Any, Protocol

# A chat history as {"role": "user" | "assistant", "content": str | parts}; the system prompt is passed separately.
# Parts are {"type": "text", "text": ...} or {"type": "image", "media_type": ..., "data": base64}; a part with
# "cache": True ends a prefix the provider may cache (with the system prompt), where it supports that.
Messages = list[dict[str, Any]]


class LLMProvider(Protocol):
    # Transient failures worth another attempt (app.telemetry.with_retries); 4xx (bad request, auth) are not.
    # Providers make one attempt per call, so the total is bounded by settings.llm_retries.
    retryable: tuple[type[BaseException], ...]

    async def complete(
        self, system: str, messages: Messages, *, model: str, json: bool, temperature: float, effort: str | None
    ) -> str:
        """The reply's text. json=True asks for a single JSON object (validated by the caller).

        effort is the reasoning effort (low|medium|high...) or None; a provider whose model takes no temperature
        ignores temperature.
        """
        ...

    async def open_stream(
        self, system: str, messages: Messages, *, model: str, temperature: float, effort: str | None
    ) -> AsyncIterator[str]:
        """Open a streamed reply (this await is what gets retried) and return an iterator over its text deltas."""
        ...
