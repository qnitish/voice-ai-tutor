"""Spec section 1: shuffled check options (`app.quiz.shuffle_checks`)."""

import copy
import math

import pytest

from app.quiz import shuffle_checks
from tests.conftest import check, create_guest, create_session, lesson_detail, publish, scene, step, submit_check


def four_option_check(i: int, correct_index: int = 0) -> dict:
    opts = [f"q{i}-opt{j}" for j in range(4)]
    return check(f"concept-{i}", options=opts, correct_index=correct_index, question=f"Q{i}?")


def correct_text(cq: dict) -> str:
    return cq["options"][cq["correct_index"]]


def test_returns_new_list_of_same_length():
    checks = [four_option_check(0), four_option_check(1)]
    out = shuffle_checks(checks, seed="s")
    assert out is not checks
    assert len(out) == 2


def test_none_entries_stay_none_at_same_position():
    checks = [four_option_check(0), None, four_option_check(2), None]
    out = shuffle_checks(checks, seed="s")
    assert out[1] is None
    assert out[3] is None
    assert out[0] is not None
    assert out[2] is not None


def test_does_not_mutate_input():
    checks = [four_option_check(i) for i in range(6)]
    snapshot = copy.deepcopy(checks)
    shuffle_checks(checks, seed="mutation")
    assert checks == snapshot


def test_options_are_a_permutation_of_the_input_options():
    checks = [four_option_check(i, correct_index=i % 4) for i in range(8)]
    out = shuffle_checks(checks, seed="perm")
    for before, after in zip(checks, out):
        assert sorted(after["options"]) == sorted(before["options"])
        assert len(after["options"]) == 4


def test_correct_index_points_at_the_originally_correct_option_text():
    checks = [four_option_check(i, correct_index=(i * 3) % 4) for i in range(8)]
    out = shuffle_checks(checks, seed="correct")
    for before, after in zip(checks, out):
        assert correct_text(after) == correct_text(before)


def test_other_keys_and_values_are_preserved():
    checks = [four_option_check(i) for i in range(3)]
    out = shuffle_checks(checks, seed="keys")
    for before, after in zip(checks, out):
        assert set(after.keys()) == set(before.keys())
        for key in before:
            if key not in ("options", "correct_index"):
                assert after[key] == before[key]


def test_same_seed_gives_same_result():
    checks = [four_option_check(i) for i in range(10)]
    assert shuffle_checks(checks, seed="abc") == shuffle_checks(copy.deepcopy(checks), seed="abc")


def test_shuffle_actually_moves_the_answer_away_from_position_zero():
    # 12 questions whose correct answer is always option A: a balanced shuffle cannot keep
    # them all at position 0 (at most ceil(12/4) = 3 may stay there).
    checks = [four_option_check(i, correct_index=0) for i in range(12)]
    out = shuffle_checks(checks, seed="moves")
    assert sum(1 for cq in out if cq["correct_index"] == 0) <= 3


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 13])
@pytest.mark.parametrize("seed", ["magnetism-01", "light-02", "x"])
def test_balanced_no_position_used_more_than_ceil_n_over_4(n, seed):
    checks = [four_option_check(i, correct_index=0) for i in range(n)]
    out = shuffle_checks(checks, seed=seed)
    counts = [0, 0, 0, 0]
    for cq in out:
        counts[cq["correct_index"]] += 1
    assert max(counts) <= math.ceil(n / 4)


def test_balance_counts_only_four_option_questions_and_ignores_none():
    checks = [four_option_check(0), None, check("two", options=["Yes", "No"], correct_index=1)]
    checks += [four_option_check(i) for i in range(3, 7)]  # 5 four-option questions in total
    out = shuffle_checks(checks, seed="mixed")
    counts = [0, 0, 0, 0]
    for cq in out:
        if cq is not None and len(cq["options"]) == 4:
            counts[cq["correct_index"]] += 1
    assert max(counts) <= math.ceil(5 / 4)


def test_question_with_fewer_options_gets_a_valid_shuffle():
    checks = [
        check("two", options=["Haan", "Nahi"], correct_index=1),
        check("three", options=["One", "Two", "Three"], correct_index=2),
    ]
    out = shuffle_checks(checks, seed="small")
    for before, after in zip(checks, out):
        assert sorted(after["options"]) == sorted(before["options"])
        assert 0 <= after["correct_index"] < len(after["options"])
        assert correct_text(after) == correct_text(before)


def test_empty_list_gives_empty_list():
    assert shuffle_checks([], seed="empty") == []


# --- publish_lesson applies shuffle_checks(seed=slug) ---------------------------------


def _scenes_with_checks(n: int) -> list[dict]:
    return [
        scene(f"Scene {i}", f"Goal {i}", [f"term{i}"], [step(f"Say {i}.")], four_option_check(i, correct_index=0))
        for i in range(n)
    ]


def test_publish_lesson_stores_options_shuffled_with_slug_as_seed(client):
    scenes = _scenes_with_checks(8)
    expected = shuffle_checks([s["check_question"] for s in scenes], seed="magnetism-01")
    publish("magnetism-01", copy.deepcopy(scenes))
    stored = [s["check_question"] for s in lesson_detail(client, "magnetism-01")["scenes"]]
    for exp, got in zip(expected, stored):
        assert got["options"] == exp["options"]
        assert got["correct_index"] == exp["correct_index"]


def test_published_correct_index_still_points_at_the_right_answer_text(client):
    scenes = _scenes_with_checks(8)
    publish("magnetism-01", copy.deepcopy(scenes))
    stored = [s["check_question"] for s in lesson_detail(client, "magnetism-01")["scenes"]]
    for original, got in zip(scenes, stored):
        assert correct_text(got) == correct_text(original["check_question"])


def test_check_endpoint_judges_against_the_shuffled_correct_index(client):
    scenes = _scenes_with_checks(8)
    publish("magnetism-01", copy.deepcopy(scenes))
    stored = [s["check_question"] for s in lesson_detail(client, "magnetism-01")["scenes"]]
    guest = create_guest(client)
    session = create_session(client, guest, "magnetism-01")
    for i, cq in enumerate(stored):
        r = submit_check(client, session["id"], i, cq["correct_index"])
        assert r.status_code == 200, r.text
        assert r.json()["correct"] is True
        assert r.json()["correct_index"] == cq["correct_index"]
