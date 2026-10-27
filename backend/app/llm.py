"""Every LLM call goes through here, to the provider selected by LLM_PROVIDER (app.providers.get_llm)."""

from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.providers import get_llm
from app.providers.llm_base import Messages
from app.telemetry import Timer, log_event, with_retries

T = TypeVar("T", bound=BaseModel)


async def _call(fn, model: str, stream: bool = False):
    """One provider request, retried on transient errors (settings.llm_retries).

    Logs `llm.done` with the time to the response (for a stream: until it is open).
    """
    llm = get_llm()
    with Timer() as t:
        resp = await with_retries(fn, what="llm", attempts=settings.llm_retries, retry_on=llm.retryable, model=model)
    log_event("llm.done", ms=t.ms, model=model, provider=settings.llm_provider, stream=stream)
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

    reasoning_effort (default settings.llm_effort) marks a reasoning model, which takes it instead of a temperature.
    """
    messages: Messages = [{"role": "user", "content": user}]
    model = model or settings.azure_openai_deployment
    effort = reasoning_effort or settings.llm_effort
    for attempt in range(2):
        content = await _call(
            lambda: get_llm().complete(system, messages, model=model, json=True, temperature=temperature, effort=effort),
            model,
        )
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
    model = model or settings.azure_openai_deployment
    messages: Messages = [{"role": "user", "content": user}]
    stream = await _call(
        lambda: get_llm().open_stream(system, messages, model=model, temperature=temperature, effort=settings.llm_effort),
        model,
        stream=True,
    )
    buf, lines = "", 0
    try:
        async for delta in stream:
            buf += delta
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
