"""Structured logging, session events and retries for external calls.

Every event is one JSON log line (logger "tutor.events"). Events that name a session are also stored in
`session_events`, so a single session can be traced end to end (GET /api/sessions/{id}/events).
Logging never raises: a failure to record an event must not break the lesson.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.config import settings

T = TypeVar("T")
log = logging.getLogger("tutor.events")


def log_event(
    kind: str,
    *,
    level: str = "info",
    session_id: str | None = None,
    thread_id: str | None = None,
    doubt_id: str | None = None,
    **data: Any,
) -> None:
    """Log `kind` (e.g. "thread.recap", "tts.failed") with its fields; stored too when it names a session."""
    record = {"event": kind, "session_id": session_id, "thread_id": thread_id, "doubt_id": doubt_id, **data}
    log.log(logging.getLevelName(level.upper()), json.dumps(record, ensure_ascii=False, default=str))
    if not session_id:
        return
    try:
        from app.db import SessionEvent, SessionLocal

        with SessionLocal.begin() as db:
            db.add(
                SessionEvent(
                    session_id=session_id,
                    thread_id=thread_id,
                    doubt_id=doubt_id,
                    kind=kind[:60],
                    level=level,
                    data=json.loads(json.dumps(data, default=str)),
                )
            )
    except Exception:  # noqa: BLE001 - recording an event must never break the caller
        log.exception("could not store event %s", kind)


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    attempts: int,
    backoff_s: float | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    **context: Any,
) -> T:
    """Await `fn()`, retrying up to `attempts` more times on `retry_on` with doubling backoff.

    Each failure is logged as "<what>.retry"; the last one (or one not in `retry_on`, such as a 4xx) as
    "<what>.failed" and re-raised. `context` (session_id=…, doubt_id=…) goes into the logged events.
    """
    delay = settings.retry_backoff_s if backoff_s is None else backoff_s
    for attempt in range(attempts + 1):
        try:
            return await fn()
        except Exception as e:
            last = attempt == attempts or not isinstance(e, retry_on)
            log_event(
                f"{what}.{'failed' if last else 'retry'}",
                level="error" if last else "warn",
                attempt=attempt + 1,
                error=f"{type(e).__name__}: {e}"[:500],
                **context,
            )
            if last:
                raise
            await asyncio.sleep(delay * 2**attempt)
    raise AssertionError("unreachable")


class Timer:
    """`with Timer() as t: ...` then `t.ms` is the elapsed time in milliseconds."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._start) * 1000)
