"""Gathers passages from the configured sources for an answer. Skeleton: implemented per the spec (2.2)."""

from collections.abc import Callable

from app.context.base import ContextQuery, ContextSource, Passage

# name -> factory; settings.context_sources picks from these
SOURCES: dict[str, Callable[[], ContextSource]] = {}


class ContextService:
    def __init__(self, sources: list[ContextSource], max_chars: int):
        self.sources = sources
        self.max_chars = max_chars

    async def gather(self, q: ContextQuery, k_per_source: int = 6) -> list[Passage]:
        raise NotImplementedError


def render(passages: list[Passage]) -> str:
    raise NotImplementedError


def get_context_service() -> ContextService:
    raise NotImplementedError
