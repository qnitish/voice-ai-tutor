"""Video lesson spec 2.2: transcript parsing/search, TranscriptSource, ContextService and render."""

import asyncio

import pytest

import app.context.service
import app.telemetry
from app.config import settings
from app.context.base import ContextQuery, Passage
from app.context.documents import DocumentSource
from app.context.service import ContextService, get_context_service, render
from app.context.transcript import (
    STOP_WORDS,
    Segment,
    TranscriptSource,
    keyword_matches,
    parse_transcript,
    tokens,
    window,
)
from tests.test_video_api import publish_video


def run(coro):
    return asyncio.run(coro)


# --- parse_transcript ------------------------------------------------------------------


def test_parse_marks_and_ends_each_segment_where_the_next_starts():
    segs = parse_transcript("[00:00] Hello there.\n[00:30] Second part.\n[01:00] Third.")
    assert segs[0] == Segment(start_ms=0, end_ms=30_000, text="Hello there.")
    assert segs[1] == Segment(start_ms=30_000, end_ms=60_000, text="Second part.")
    assert len(segs) == 3


def test_parse_last_segment_ends_at_total_ms():
    segs = parse_transcript("[00:00] a\n[00:30] b", total_ms=100_000)
    assert segs[-1].end_ms == 100_000


def test_parse_last_segment_ends_thirty_seconds_after_its_start_without_total():
    segs = parse_transcript("[00:00] a\n[01:10] b")
    assert segs[-1] == Segment(start_ms=70_000, end_ms=100_000, text="b")


def test_parse_hours_mark():
    segs = parse_transcript("[1:02:03] late")
    assert segs[0].start_ms == 3_723_000


def test_parse_minutes_beyond_59():
    segs = parse_transcript("[75:10] late")
    assert segs[0].start_ms == 4_510_000


def test_parse_joins_unmarked_lines_to_the_previous_segment():
    segs = parse_transcript("[00:00] first line\ncontinues here\n[00:30] second")
    assert len(segs) == 2
    assert segs[0].text.split() == ["first", "line", "continues", "here"]
    assert segs[1].text == "second"


def test_parse_text_before_the_first_mark_starts_at_zero():
    segs = parse_transcript("intro words\n[00:30] second")
    assert segs[0] == Segment(start_ms=0, end_ms=30_000, text="intro words")
    assert segs[1].start_ms == 30_000


def test_parse_strips_text_and_drops_empty_segments():
    segs = parse_transcript("[00:00]   \n[00:30]   padded text   \n")
    assert segs == [Segment(start_ms=30_000, end_ms=60_000, text="padded text")]


def test_parse_empty_text():
    assert parse_transcript("") == []


# --- tokens ----------------------------------------------------------------------------


def test_tokens_lowercases_and_drops_stop_words():
    assert tokens("Why does the Packet accelerate?") == {"packet", "accelerate"}


def test_tokens_keeps_digits():
    assert "9" in tokens("g is 9.8") and "8" in tokens("g is 9.8")


def test_stop_words_contain_common_function_words():
    for w in ["a", "an", "the", "is", "are", "of", "to", "in", "and", "what", "why", "how", "do", "does"]:
        assert w in STOP_WORDS


def test_tokens_of_only_stop_words_is_empty():
    assert tokens("what is it?") == set()


# --- window ----------------------------------------------------------------------------


SEGS = [
    Segment(0, 30_000, "zero"),
    Segment(30_000, 60_000, "one"),
    Segment(60_000, 90_000, "two"),
    Segment(90_000, 120_000, "three"),
]


def test_window_keeps_segments_started_by_at_ms_that_end_inside_the_window():
    assert window(SEGS, 75_000, 30_000) == [SEGS[1], SEGS[2]]


def test_window_includes_a_segment_starting_exactly_at_at_ms():
    assert SEGS[2] in window(SEGS, 60_000, 30_000)


def test_window_excludes_a_segment_ending_exactly_at_the_window_start():
    assert window(SEGS, 60_000, 30_000) == [SEGS[1], SEGS[2]]


def test_window_excludes_later_segments():
    assert SEGS[3] not in window(SEGS, 75_000, 200_000)


def test_window_covers_everything_before_with_a_large_window():
    assert window(SEGS, 100_000, 10**9) == SEGS


# --- keyword_matches -------------------------------------------------------------------


KSEGS = [
    Segment(0, 30_000, "The balloon rises slowly"),
    Segment(30_000, 60_000, "free fall of the packet"),
    Segment(60_000, 90_000, "the packet drops"),
    Segment(90_000, 120_000, "free fall again here"),
]


def test_keyword_matches_scores_the_overlap_fraction():
    matches = keyword_matches(KSEGS, "free fall packet", 10)
    scores = {seg.start_ms: score for seg, score in matches}
    assert scores[30_000] == pytest.approx(1.0)
    assert scores[90_000] == pytest.approx(2 / 3)
    assert scores[60_000] == pytest.approx(1 / 3)


def test_keyword_matches_drops_zero_scores():
    matches = keyword_matches(KSEGS, "free fall packet", 10)
    assert 0 not in {seg.start_ms for seg, _ in matches}


def test_keyword_matches_sorts_by_score_then_time():
    assert [seg.start_ms for seg, _ in keyword_matches(KSEGS, "free fall packet", 10)] == [30_000, 90_000, 60_000]


def test_keyword_matches_ties_keep_time_order():
    assert [seg.start_ms for seg, _ in keyword_matches(KSEGS, "packet", 10)] == [30_000, 60_000]


def test_keyword_matches_limits_to_k():
    assert len(keyword_matches(KSEGS, "free fall packet", 2)) == 2


def test_keyword_matches_with_no_keywords_is_empty():
    assert keyword_matches(KSEGS, "what is it", 10) == []


# --- TranscriptSource ------------------------------------------------------------------


@pytest.fixture
def video_lesson():
    return publish_video()


def retrieve(lesson_id: int, question: str, at_ms: int, chapter_idx: int, k: int = 6) -> list[Passage]:
    return run(TranscriptSource().retrieve(ContextQuery(lesson_id, question, at_ms, chapter_idx), k))


def test_transcript_source_name():
    assert TranscriptSource().name == "transcript"


def test_transcript_source_starts_with_the_merged_window(video_lesson):
    # Default window of 180 s: every segment started by 150 s, i.e. lines 0..5 (the last ends at 180 s)
    passages = retrieve(video_lesson.id, "why does the packet go up", 150_000, 0)
    first = passages[0]
    assert first.source == "transcript"
    assert first.score == pytest.approx(3.0)
    assert first.ref == "00:00–03:00"
    assert "Line 0:" in first.text
    assert "Line 5:" in first.text
    assert "Line 6:" not in first.text


def test_transcript_source_then_the_rest_of_the_chapter(video_lesson):
    # Paused at 30 s: the window holds lines 0..1; the rest of chapter 0 (lines 2..6) follows at 2.0 each
    passages = retrieve(video_lesson.id, "why does the packet go up", 30_000, 0)
    assert passages[0].score == pytest.approx(3.0)
    chapter_passages = [p for p in passages if p.score == pytest.approx(2.0)]
    assert chapter_passages, "no chapter passages"
    assert all("free fall" in p.text for p in chapter_passages)
    assert any("Line 6:" in p.text for p in chapter_passages)
    assert not any("Line 7:" in p.text for p in chapter_passages)  # chapter 1
    assert not any("Line 0:" in p.text for p in chapter_passages)  # already in the window


def test_transcript_source_then_keyword_matches(video_lesson):
    passages = retrieve(video_lesson.id, "what does relative velocity mean", 150_000, 0)
    matches = [p for p in passages if p.score <= 1.0]
    assert matches, "no keyword matches"
    assert all("relative velocity" in p.text for p in matches)


def test_transcript_source_orders_by_score(video_lesson):
    passages = retrieve(video_lesson.id, "what does relative velocity mean", 150_000, 0)
    scores = [p.score for p in passages]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(3.0)


def test_transcript_source_limits_keyword_matches_to_k(video_lesson):
    passages = retrieve(video_lesson.id, "what does relative velocity mean", 150_000, 0, k=1)
    assert len([p for p in passages if p.score <= 1.0]) == 1


def test_transcript_source_has_no_duplicate_refs(video_lesson):
    passages = retrieve(video_lesson.id, "packet in free fall under gravity", 150_000, 0)
    refs = [p.ref for p in passages]
    assert len(refs) == len(set(refs))


def test_transcript_source_all_passages_are_from_the_transcript(video_lesson):
    passages = retrieve(video_lesson.id, "what does relative velocity mean", 150_000, 0)
    assert passages
    assert {p.source for p in passages} == {"transcript"}


# --- ContextService --------------------------------------------------------------------


class StaticSource:
    def __init__(self, name: str, passages: list[Passage], fail: bool = False):
        self.name = name
        self.passages = passages
        self.fail = fail
        self.k: int | None = None

    async def retrieve(self, q: ContextQuery, k: int) -> list[Passage]:
        self.k = k
        if self.fail:
            raise RuntimeError("boom")
        return list(self.passages)


def p(source: str, ref: str, text: str, score: float) -> Passage:
    return Passage(source=source, ref=ref, text=text, score=score)


Q = ContextQuery(lesson_id=1, question="why", at_ms=1000, chapter_idx=0)


def gather(service: ContextService, **kw) -> list[Passage]:
    return run(service.gather(Q, **kw))


def test_gather_sorts_by_score_descending_across_sources():
    a = StaticSource("a", [p("a", "1", "low", 1.0), p("a", "2", "mid", 2.0)])
    b = StaticSource("b", [p("b", "1", "top", 3.0)])
    assert [x.text for x in gather(ContextService([a, b], 1000))] == ["top", "mid", "low"]


def test_gather_keeps_input_order_for_equal_scores():
    a = StaticSource("a", [p("a", "1", "first", 1.0), p("a", "2", "second", 1.0), p("a", "3", "third", 1.0)])
    assert [x.text for x in gather(ContextService([a], 1000))] == ["first", "second", "third"]


def test_gather_dedupes_by_source_and_ref():
    a = StaticSource("a", [p("a", "same", "one", 2.0), p("a", "same", "two", 1.0)])
    assert [x.text for x in gather(ContextService([a], 1000))] == ["one"]


def test_gather_keeps_the_same_ref_from_different_sources():
    a = StaticSource("a", [p("a", "r", "from a", 2.0)])
    b = StaticSource("b", [p("b", "r", "from b", 1.0)])
    assert len(gather(ContextService([a, b], 1000))) == 2


def test_gather_stops_before_a_passage_that_would_exceed_the_budget():
    a = StaticSource("a", [p("a", "1", "abcdef", 3.0), p("a", "2", "ghijkl", 2.0), p("a", "3", "mnopqr", 1.0)])
    assert [x.ref for x in gather(ContextService([a], 13))] == ["1", "2"]


def test_gather_budget_counts_text_in_score_order():
    a = StaticSource("a", [p("a", "low", "x" * 6, 1.0), p("a", "high", "y" * 6, 2.0)])
    assert [x.ref for x in gather(ContextService([a], 8))] == ["high"]


def test_gather_truncates_the_first_passage_alone_to_the_budget():
    a = StaticSource("a", [p("a", "1", "abcdefgh", 3.0), p("a", "2", "ij", 2.0)])
    out = gather(ContextService([a], 4))
    assert len(out) == 1
    assert out[0].text == "abcd"


def test_gather_skips_a_failing_source():
    good = StaticSource("good", [p("good", "1", "fine", 1.0)])
    bad = StaticSource("bad", [], fail=True)
    assert [x.text for x in gather(ContextService([bad, good], 1000))] == ["fine"]


def test_gather_logs_a_failing_source(monkeypatch):
    events: list[tuple[str, dict]] = []

    def spy(kind, **fields):
        events.append((kind, fields))

    monkeypatch.setattr(app.telemetry, "log_event", spy)
    monkeypatch.setattr(app.context.service, "log_event", spy, raising=False)
    gather(ContextService([StaticSource("bad", [], fail=True)], 1000))
    failed = [fields for kind, fields in events if kind == "context.source_failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "warn"
    assert failed[0]["source"] == "bad"
    assert "boom" in str(failed[0]["error"])


def test_gather_passes_k_per_source():
    a = StaticSource("a", [])
    gather(ContextService([a], 1000), k_per_source=4)
    assert a.k == 4


def test_gather_default_k_per_source_is_six():
    a = StaticSource("a", [])
    gather(ContextService([a], 1000))
    assert a.k == 6


def test_gather_with_no_sources_is_empty():
    assert gather(ContextService([], 1000)) == []


# --- render ----------------------------------------------------------------------------


def test_render_blocks_with_source_and_ref():
    out = render([p("transcript", "00:00–00:30", "Hello there.", 3.0), p("documents", "book p.4", "Figure 2.", 1.0)])
    assert out == "[transcript 00:00–00:30]\nHello there.\n\n[documents book p.4]\nFigure 2."


def test_render_nothing_is_empty_string():
    assert render([]) == ""


# --- get_context_service ---------------------------------------------------------------


def test_get_context_service_builds_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "context_sources", "transcript")
    monkeypatch.setattr(settings, "context_max_chars", 777)
    service = get_context_service()
    assert [s.name for s in service.sources] == ["transcript"]
    assert service.max_chars == 777


def test_get_context_service_accepts_several_sources(monkeypatch):
    monkeypatch.setattr(settings, "context_sources", "transcript,documents")
    assert [s.name for s in get_context_service().sources] == ["transcript", "documents"]


def test_get_context_service_rejects_an_unknown_source(monkeypatch):
    monkeypatch.setattr(settings, "context_sources", "transcript,nope")
    with pytest.raises(ValueError):
        get_context_service()


def test_document_source_returns_nothing_yet():
    source = DocumentSource()
    assert source.name == "documents"
    assert run(source.retrieve(Q, 6)) == []
