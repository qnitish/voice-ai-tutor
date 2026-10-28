"""Gathers passages from the configured sources for an answer (docs/plan/video-lesson-spec.md 2.2)."""

import asyncio
from collections.abc import Callable

from app.config import settings
from app.context.base import ContextQuery, ContextSource, Passage
from app.context.documents import DocumentSource
from app.context.transcript import TranscriptSource
from app.telemetry import log_event

# name -> factory; settings.context_sources picks from these
SOURCES: dict[str, Callable[[], ContextSource]] = {"transcript": TranscriptSource, "documents": DocumentSource}


class ContextService:
    def __init__(self, sources: list[ContextSource], max_chars: int):
        self.sources = sources
        self.max_chars = max_chars

    async def gather(self, q: ContextQuery, k_per_source: int = 6) -> list[Passage]:
        """Passages from every source, best first, within `max_chars` of text. A failing source is logged and skipped."""
        results = await asyncio.gather(
            *(source.retrieve(q, k_per_source) for source in self.sources), return_exceptions=True
        )
        passages: list[Passage] = []
        seen: set[tuple[str, str]] = set()
        for source, result in zip(self.sources, results):
            if isinstance(result, BaseException):
                log_event(
                    "context.source_failed",
                    level="warn",
                    source=source.name,
                    error=f"{type(result).__name__}: {result}"[:500],
                )
                continue
            for p in result:
                if (p.source, p.ref) not in seen:
                    seen.add((p.source, p.ref))
                    passages.append(p)
        passages.sort(key=lambda p: -p.score)  # stable: sources' own order within a score
        return self._within_budget(passages)

    def _within_budget(self, passages: list[Passage]) -> list[Passage]:
        kept: list[Passage] = []
        total = 0
        for p in passages:
            if total + len(p.text) > self.max_chars:
                if not kept:
                    kept.append(p.model_copy(update={"text": p.text[: self.max_chars]}))
                break
            kept.append(p)
            total += len(p.text)
        return kept


def render(passages: list[Passage]) -> str:
    """`[source ref]` header and text per passage, separated by blank lines; "" for none."""
    return "\n\n".join(f"[{p.source} {p.ref}]\n{p.text}" for p in passages)


def get_context_service() -> ContextService:
    """The service for `settings.context_sources` ("transcript,documents"); an unknown name is a `ValueError`."""
    sources: list[ContextSource] = []
    for name in (n.strip() for n in settings.context_sources.split(",")):
        if not name:
            continue
        if name not in SOURCES:
            raise ValueError(f"unknown context source {name!r}; known: {', '.join(SOURCES)}")
        sources.append(SOURCES[name]())
    return ContextService(sources, settings.context_max_chars)
