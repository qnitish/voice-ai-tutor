from collections.abc import AsyncIterator
from typing import TypeVar

from openai import APIConnectionError, APITimeoutError, AsyncAzureOpenAI, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.telemetry import Timer, log_event, with_retries

T = TypeVar("T", bound=BaseModel)

client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
)

# Transient failures worth another attempt; 4xx (bad request, auth, content filter) are not
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


async def _create(**kwargs):
    """One chat completion request, retried on transient errors (settings.llm_retries).

    Logs `llm.done` with the time to the response (for a stream: until it is open).
    """
    model = kwargs["model"]
    with Timer() as t:
        resp = await with_retries(
            lambda: client.chat.completions.create(**kwargs), what="llm", attempts=settings.llm_retries, retry_on=RETRYABLE, model=model
        )
    log_event("llm.done", ms=t.ms, model=model, stream=kwargs.get("stream", False))
    return resp


async def complete_json(
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.4,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> T:
    """Ask for a JSON object and validate it, giving the model one chance to fix errors.

    reasoning_effort marks a reasoning model, which takes it instead of a temperature.
    """
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    sampling = {"reasoning_effort": reasoning_effort} if reasoning_effort else {"temperature": temperature}
    for attempt in range(2):
        resp = await _create(
            model=model or settings.azure_openai_deployment,
            messages=messages,
            response_format={"type": "json_object"},
            **sampling,
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


async def prep_json(system: str, user: str, schema: type[T]) -> T:
    """complete_json on the prep deployment (see Settings.azure_openai_prep_deployment).

    Offline, so a reply that still fails validation after its fix-up turn gets one fresh try
    instead of failing a long prep run.
    """
    effort = settings.azure_openai_prep_reasoning_effort
    kwargs = {"model": settings.prep_deployment, **({"reasoning_effort": effort} if effort else {})}
    try:
        return await complete_json(system, user, schema, **kwargs)
    except ValidationError:
        return await complete_json(system, user, schema, **kwargs)


async def stream_lines(
    system: str, user: str, temperature: float = 0.5, model: str | None = None
) -> AsyncIterator[str]:
    """Stream the completion and yield it one complete line at a time.

    Only opening the stream is retried. Once a line has been yielded, a failure is logged
    (`llm.stream_failed`) and ends the stream, so the caller keeps what it has.
    """
    stream = await _create(
        model=model or settings.azure_openai_deployment,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        stream=True,
    )
    buf, lines = "", 0
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            buf += chunk.choices[0].delta.content or ""
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.strip():
                    yield line.strip()
                    lines += 1
        if buf.strip():
            yield buf.strip()
    except Exception as e:
        if lines == 0:
            raise
        log_event("llm.stream_failed", level="error", lines=lines, error=f"{type(e).__name__}: {e}"[:500])
