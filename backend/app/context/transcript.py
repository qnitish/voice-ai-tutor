"""The lecture transcript as an answer-context source (docs/plan/video-lesson-spec.md 2.2).

A transcript is a string of `[mm:ss] text` lines (prep's `transcript.json`). It is split into timed segments; a
question gets the window of speech just before the pause point, the rest of the current chapter, and the segments
that share the most words with the question.
"""

import re
from dataclasses import dataclass

from app.config import settings
from app.context.base import ContextQuery, Passage

STOP_WORDS = frozenset(
    """
    a an the is are was were be been being of to in on at and or it its this that these those what why how
    we you i me my our your do does did done for with so then as by from if not no can could will would
    should have has had there here just like also very
    """.split()
)
DEFAULT_SEGMENT_MS = 30_000  # length of the last segment when the total length is unknown

_MARK = re.compile(r"^\[(\d+(?::\d{1,2}){1,2})\]\s*")  # [mm:ss] or [h:mm:ss]; minutes may exceed 59


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


def _mark_ms(mark: str) -> int:
    parts = [int(p) for p in mark.split(":")]
    seconds = parts[-1] + 60 * parts[-2] + (3600 * parts[0] if len(parts) == 3 else 0)
    return seconds * 1000


def mmss(ms: int) -> str:
    """`mm:ss` (minutes may exceed 59), as the transcript writes its marks."""
    seconds = ms // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def parse_transcript(text: str, total_ms: int | None = None) -> list[Segment]:
    """Split `[mm:ss] text` lines into segments.

    A segment ends where the next starts; the last at `total_ms` or 30 s after its start. Lines without a mark join
    the previous segment; text before the first mark starts at 0. Empty segments are dropped.
    """
    starts: list[int] = []
    texts: list[list[str]] = []
    for line in text.splitlines():
        m = _MARK.match(line)
        if m:
            starts.append(_mark_ms(m.group(1)))
            texts.append([line[m.end() :]])
        elif texts:
            texts[-1].append(line)
        else:
            starts.append(0)
            texts.append([line])

    segments: list[Segment] = []
    for i, (start, lines) in enumerate(zip(starts, texts)):
        if i + 1 < len(starts):
            end = starts[i + 1]
        else:
            end = total_ms if total_ms is not None else start + DEFAULT_SEGMENT_MS
        joined = " ".join(part.strip() for part in lines if part.strip())
        if joined:
            segments.append(Segment(start, end, joined))
    return segments


def tokens(text: str) -> set[str]:
    """Lower-case word tokens minus stop words."""
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP_WORDS


def window(segments: list[Segment], at_ms: int, window_ms: int) -> list[Segment]:
    """The segments spoken in the `window_ms` before `at_ms` (those that start by `at_ms` and end after the window opens)."""
    return [s for s in segments if s.start_ms <= at_ms and s.end_ms > at_ms - window_ms]


def keyword_matches(segments: list[Segment], question: str, k: int) -> list[tuple[Segment, float]]:
    """Top `k` segments by the share of the question's tokens they contain; ties in time order."""
    wanted = tokens(question)
    if not wanted:
        return []
    scored = [(s, len(wanted & tokens(s.text)) / len(wanted)) for s in segments]
    hits = [(s, score) for s, score in scored if score > 0]
    hits.sort(key=lambda hit: (-hit[1], hit[0].start_ms))
    return hits[:k]


def _ref(start_ms: int, end_ms: int) -> str:
    return f"{mmss(start_ms)}–{mmss(end_ms)}"


class TranscriptSource:
    """Passages from the lesson's own transcript: the recent window (3.0), its chapter (2.0), keyword matches (<= 1.0)."""

    name = "transcript"

    def __init__(self, window_ms: int | None = None):
        self.window_ms = settings.context_window_s * 1000 if window_ms is None else window_ms

    async def retrieve(self, q: ContextQuery, k: int) -> list[Passage]:
        from app.db import Lesson, SessionLocal

        with SessionLocal() as db:
            lesson = db.get(Lesson, q.lesson_id)
            if lesson is None:
                return []
            segments = parse_transcript(lesson.transcript, lesson.duration_ms)
            chapters = [(c.idx, c.start_ms, c.end_ms) for c in lesson.chapters]
        return self.passages(segments, chapters, q, k)

    def passages(
        self, segments: list[Segment], chapters: list[tuple[int, int, int]], q: ContextQuery, k: int
    ) -> list[Passage]:
        """Pure part of `retrieve`; `chapters` are `(idx, start_ms, end_ms)`."""
        out: list[Passage] = []
        included: set[Segment] = set()

        if q.at_ms is not None:
            recent = window(segments, q.at_ms, self.window_ms)
            if recent:
                text = " ".join(s.text for s in recent)
                out.append(Passage(source=self.name, ref=_ref(recent[0].start_ms, recent[-1].end_ms), text=text, score=3.0))
                included.update(recent)

        span = _chapter_span(chapters, q)
        if span is not None:
            start_ms, end_ms = span
            for s in segments:
                if start_ms <= s.start_ms < end_ms and s not in included:
                    out.append(Passage(source=self.name, ref=_ref(s.start_ms, s.end_ms), text=s.text, score=2.0))
                    included.add(s)

        for s, score in keyword_matches(segments, q.question, k):
            if s not in included:
                out.append(Passage(source=self.name, ref=_ref(s.start_ms, s.end_ms), text=s.text, score=score))
                included.add(s)
        return out


def _chapter_span(chapters: list[tuple[int, int, int]], q: ContextQuery) -> tuple[int, int] | None:
    """The time range of `q.chapter_idx`, or of the chapter playing at `q.at_ms`."""
    for idx, start_ms, end_ms in chapters:
        if q.chapter_idx is not None:
            if idx == q.chapter_idx:
                return start_ms, end_ms
        elif q.at_ms is not None and start_ms <= q.at_ms < end_ms:
            return start_ms, end_ms
    return None
