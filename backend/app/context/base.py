"""Types every context source shares (docs/plan/video-lesson-spec.md 2.2)."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class Passage(BaseModel):
    source: str  # "transcript", "documents", …
    ref: str  # where it is from, e.g. "12:40–13:10" or "book p.4"
    text: str
    score: float = 0.0  # higher first; transcript window 3.0, its chapter 2.0, keyword matches <= 1.0


@dataclass(frozen=True)
class ContextQuery:
    lesson_id: int
    question: str
    at_ms: int | None = None
    chapter_idx: int | None = None


class ContextSource(Protocol):
    name: str

    async def retrieve(self, q: ContextQuery, k: int) -> list[Passage]: ...
