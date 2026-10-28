"""Claude via the Anthropic Messages API.

Claude models take no temperature (it is ignored here); depth is set with effort. JSON is asked for in the system
prompt and validated by the caller (app.llm.complete_json), as with the other providers.
"""

import re
from collections.abc import AsyncIterator

from anthropic import (
    APIConnectionError,
    AsyncAnthropic,
    DeadlineExceededError,
    InternalServerError,
    OverloadedError,
    RateLimitError,
    ServiceUnavailableError,
)

from app.config import settings
from app.providers.llm_base import Messages
from app.telemetry import log_event

JSON_ONLY = "\n\nReply with the JSON object only: no markdown code fence and no text before or after it."
# Models that accept the server-side refusal fallback (fallbacks="default")
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMRefused(RuntimeError):
    """The model declined the request (stop_reason "refusal")."""


def content_blocks(content: str | list[dict]) -> str | list[dict]:
    """llm_base message parts as Messages API content blocks; a part marked "cache" ends a cached prefix."""
    if isinstance(content, str):
        return content
    blocks = []
    for part in content:
        if part["type"] == "image":
            block = {"type": "image", "source": {"type": "base64", "media_type": part["media_type"], "data": part["data"]}}
        else:
            block = {"type": "text", "text": part["text"]}
        if part.get("cache"):
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def json_text(text: str) -> str:
    """The JSON object in a reply, without a code fence or stray text around it."""
    text = FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


class AnthropicLLM:
    retryable = (APIConnectionError, RateLimitError, InternalServerError, ServiceUnavailableError, OverloadedError, DeadlineExceededError)

    def __init__(self) -> None:
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=0)

    @staticmethod
    def _request(system: str, messages: Messages, model: str, effort: str | None) -> dict:
        req = {
            "model": model,
            "max_tokens": settings.llm_max_tokens,
            # The system prompt is the stable prefix (rules, DRAW_SPEC, lesson vocabulary); short ones just don't cache
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{**m, "content": content_blocks(m["content"])} for m in messages],
        }
        if effort:
            req["output_config"] = {"effort": effort}
        if settings.anthropic_fallbacks and model.startswith(FALLBACK_MODELS):
            req |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return req

    def _messages(self, req: dict):
        return self.client.beta.messages if "betas" in req else self.client.messages

    async def complete(
        self, system: str, messages: Messages, *, model: str, json: bool, temperature: float, effort: str | None
    ) -> str:
        req = self._request(system + JSON_ONLY if json else system, messages, model, effort)
        # Streamed so a long prep reply (thinking + JSON) never hits the SDK's non-streaming timeout
        async with self._messages(req).stream(**req) as stream:
            msg = await stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise LLMRefused(f"{model} declined: {getattr(msg.stop_details, 'category', None)}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens ({settings.llm_max_tokens})")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return json_text(text) if json else text

    async def open_stream(
        self, system: str, messages: Messages, *, model: str, temperature: float, effort: str | None
    ) -> AsyncIterator[str]:
        req = self._request(system, messages, model, effort)
        stream = await self._messages(req).create(**req, stream=True)

        async def deltas() -> AsyncIterator[str]:
            usage: dict = {}
            async for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
                elif event.type == "message_start":
                    u = event.message.usage
                    usage = dict(
                        input_tokens=u.input_tokens,
                        cache_read=u.cache_read_input_tokens or 0,
                        cache_write=u.cache_creation_input_tokens or 0,
                    )
                elif event.type == "message_delta":
                    if event.delta.stop_reason == "refusal":
                        raise LLMRefused(f"{model} declined mid-answer")
                    log_event("llm.usage", model=model, output_tokens=event.usage.output_tokens, **usage)

        return deltas()
