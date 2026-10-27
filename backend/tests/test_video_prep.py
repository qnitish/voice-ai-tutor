"""Video lesson spec 2.1: the pure prep helpers fix_chapters, plan_checkpoints and snap_to_silence."""

import pytest

from app.prep.video import fix_chapters, plan_checkpoints, snap_to_silence

# --- fix_chapters ----------------------------------------------------------------------


def test_fix_chapters_sorts_and_chains_the_ends():
    out = fix_chapters(
        [{"start_s": 100, "end_s": 150, "title": "B"}, {"start_s": 5, "end_s": 999, "title": "A"}],
        duration_s=300,
    )
    assert [(c["title"], c["start_s"], c["end_s"]) for c in out] == [("A", 0, 100), ("B", 100, 300)]


def test_fix_chapters_first_starts_at_zero():
    out = fix_chapters([{"start_s": 42, "end_s": 60}], duration_s=120)
    assert out[0]["start_s"] == 0


def test_fix_chapters_last_ends_at_the_duration():
    out = fix_chapters([{"start_s": 0, "end_s": 50}, {"start_s": 50, "end_s": 70}], duration_s=200)
    assert out[-1]["end_s"] == 200


def test_fix_chapters_ignores_the_llms_end_except_the_last():
    out = fix_chapters([{"start_s": 0, "end_s": 999}, {"start_s": 80, "end_s": 1}], duration_s=200)
    assert out[0]["end_s"] == 80
    assert out[1]["end_s"] == 200


def test_fix_chapters_drops_chapters_with_no_length():
    out = fix_chapters([{"start_s": 0, "end_s": 10}, {"start_s": 50, "end_s": 60}, {"start_s": 50, "end_s": 70}], duration_s=300)
    assert [(c["start_s"], c["end_s"]) for c in out] == [(0, 50), (50, 300)]


def test_fix_chapters_drops_a_chapter_starting_after_the_duration():
    out = fix_chapters([{"start_s": 0, "end_s": 10}, {"start_s": 300, "end_s": 310}], duration_s=300)
    assert [(c["start_s"], c["end_s"]) for c in out] == [(0, 300)]


def test_fix_chapters_keeps_other_keys():
    out = fix_chapters([{"start_s": 0, "end_s": 10, "topic": "Motion", "title": "Free fall", "key_terms": ["g"]}], duration_s=60)
    assert out[0]["topic"] == "Motion"
    assert out[0]["title"] == "Free fall"
    assert out[0]["key_terms"] == ["g"]


def test_fix_chapters_raises_when_none_remain():
    with pytest.raises(ValueError):
        fix_chapters([], duration_s=300)
    with pytest.raises(ValueError):
        fix_chapters([{"start_s": 0, "end_s": 10}], duration_s=0)


# --- plan_checkpoints ------------------------------------------------------------------

CHAPTERS = [
    {"start_s": 0, "end_s": 50},
    {"start_s": 50, "end_s": 250},
    {"start_s": 250, "end_s": 400},
    {"start_s": 400, "end_s": 600},
]
DURATION = 600


def plan(items, min_gap_s=100, max_n=8, max_q=3):
    return plan_checkpoints(items, CHAPTERS, DURATION, min_gap_s, max_n, max_q)


def test_plan_checkpoints_uses_the_chapter_end():
    out = plan([{"after_chapter": 1, "questions": 2, "reason": "dense"}])
    assert out == [{"chapter_idx": 1, "t_s": 250, "questions": 2, "reason": "dense"}]


def test_plan_checkpoints_sorts_by_time():
    out = plan([{"after_chapter": 3, "questions": 1, "reason": "end"}, {"after_chapter": 1, "questions": 1, "reason": "mid"}])
    assert [c["t_s"] for c in out] == [250, 600]


def test_plan_checkpoints_drops_invalid_chapter_indexes():
    out = plan([{"after_chapter": -1, "questions": 1, "reason": "x"}, {"after_chapter": 4, "questions": 1, "reason": "x"}])
    assert out == []


def test_plan_checkpoints_drops_duplicate_chapters():
    out = plan([{"after_chapter": 1, "questions": 1, "reason": "a"}, {"after_chapter": 1, "questions": 2, "reason": "b"}])
    assert len(out) == 1
    assert out[0]["chapter_idx"] == 1


def test_plan_checkpoints_drops_times_under_a_minute():
    out = plan([{"after_chapter": 0, "questions": 1, "reason": "too early"}])
    assert out == []


def test_plan_checkpoints_clamps_questions():
    out = plan([{"after_chapter": 1, "questions": 0, "reason": "x"}, {"after_chapter": 3, "questions": 9, "reason": "y"}])
    assert [c["questions"] for c in out] == [1, 3]


def test_plan_checkpoints_drops_items_too_close_to_the_previous_kept_one():
    items = [{"after_chapter": i, "questions": 1, "reason": "x"} for i in (1, 2, 3)]  # 250, 400, 600
    out = plan(items, min_gap_s=200)
    assert [c["t_s"] for c in out] == [250, 600]


def test_plan_checkpoints_keeps_items_exactly_min_gap_apart():
    items = [{"after_chapter": i, "questions": 1, "reason": "x"} for i in (1, 2)]  # 250, 400
    out = plan(items, min_gap_s=150)
    assert [c["t_s"] for c in out] == [250, 400]


def test_plan_checkpoints_truncates_to_max_n():
    items = [{"after_chapter": i, "questions": 1, "reason": "x"} for i in (1, 2, 3)]
    out = plan(items, min_gap_s=10, max_n=2)
    assert [c["t_s"] for c in out] == [250, 400]


def test_plan_checkpoints_empty_plan():
    assert plan([]) == []


# --- snap_to_silence -------------------------------------------------------------------


def test_snap_returns_the_nearest_silence_midpoint():
    assert snap_to_silence(12.0, [(10.0, 12.0), (30.0, 31.0)], 3.0) == pytest.approx(11.0)


def test_snap_keeps_t_when_no_silence_is_near():
    assert snap_to_silence(20.0, [(10.0, 12.0), (30.0, 31.0)], 3.0) == pytest.approx(20.0)


def test_snap_accepts_a_midpoint_exactly_window_away():
    assert snap_to_silence(14.0, [(10.0, 12.0)], 3.0) == pytest.approx(11.0)


def test_snap_picks_the_nearest_of_several():
    assert snap_to_silence(29.0, [(10.0, 12.0), (30.0, 31.0)], 5.0) == pytest.approx(30.5)


def test_snap_prefers_the_earlier_silence_on_a_tie():
    assert snap_to_silence(11.0, [(8.0, 10.0), (12.0, 14.0)], 5.0) == pytest.approx(9.0)


def test_snap_with_no_silences():
    assert snap_to_silence(42.0, [], 8.0) == pytest.approx(42.0)
