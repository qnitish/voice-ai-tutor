"""Chat Completions vendors: Azure OpenAI, OpenAI, and the OpenAI-compatible APIs of DeepSeek and Gemini."""

from collections.abc import AsyncIterator

from openai import APIConnectionError, APITimeoutError, AsyncAzureOpenAI, AsyncOpenAI, InternalServerError, RateLimitError

from app.config import settings
from app.providers.llm_base import Messages

BASE_URLS = {
    "openai": None,
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def content_parts(content: str | list[dict]) -> str | list[dict]:
    """llm_base message parts as Chat Completions content (images as data URLs; "cache" marks are ignored)."""
    if isinstance(content, str):
        return content
    return [
        {"type": "image_url", "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}}
        if p["type"] == "image"
        else {"type": "text", "text": p["text"]}
        for p in content
    ]


class OpenAICompatLLM:
    retryable = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)

    def __init__(self, vendor: str = "azure") -> None:
        if vendor == "azure":
            self.client = AsyncAzureOpenAI(
                api_key=settings.azure_openai_api_key,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                max_retries=0,
            )
        else:
            key = {"openai": settings.openai_api_key, "deepseek": settings.deepseek_api_key, "gemini": settings.gemini_api_key}
            self.client = AsyncOpenAI(api_key=key[vendor], base_url=BASE_URLS[vendor], max_retries=0)

    @staticmethod
    def _request(system: str, messages: Messages, model: str, temperature: float, effort: str | None) -> dict:
        # A reasoning model takes reasoning_effort instead of a temperature
        sampling = {"reasoning_effort": effort} if effort else {"temperature": temperature}
        chat = [{**m, "content": content_parts(m["content"])} for m in messages]
        return {"model": model, "messages": [{"role": "system", "content": system}, *chat], **sampling}

    async def complete(
        self, system: str, messages: Messages, *, model: str, json: bool, temperature: float, effort: str | None
    ) -> str:
        fmt = {"response_format": {"type": "json_object"}} if json else {}
        resp = await self.client.chat.completions.create(**self._request(system, messages, model, temperature, effort), **fmt)
        return resp.choices[0].message.content or ""

    async def open_stream(
        self, system: str, messages: Messages, *, model: str, temperature: float, effort: str | None
    ) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            **self._request(system, messages, model, temperature, effort), stream=True
        )

        async def deltas() -> AsyncIterator[str]:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        return deltas()
