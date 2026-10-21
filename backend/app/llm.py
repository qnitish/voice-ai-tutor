from collections.abc import AsyncIterator
from typing import TypeVar

from openai import AsyncAzureOpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)

client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
)


async def complete_json(system: str, user: str, schema: type[T], temperature: float = 0.4) -> T:
    """Ask for a JSON object and validate it, giving the model one chance to fix errors."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        resp = await client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        content = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(content)
        except ValidationError as e:
            if attempt == 1:
                raise
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That JSON did not match the format: {e}. Return the corrected JSON only."},
            ]
    raise AssertionError("unreachable")


async def stream_lines(system: str, user: str, temperature: float = 0.5) -> AsyncIterator[str]:
    """Stream the completion and yield it one complete line at a time."""
    stream = await client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        stream=True,
    )
    buf = ""
    async for chunk in stream:
        if not chunk.choices:
            continue
        buf += chunk.choices[0].delta.content or ""
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                yield line.strip()
    if buf.strip():
        yield buf.strip()
