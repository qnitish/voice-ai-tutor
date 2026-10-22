"""Spec section 4: mastery rule, check/doubt side effects, mastery/progress/history endpoints."""

from datetime import datetime

import pytest

from app.progress import ALPHA_CHECK, ALPHA_DOUBT, PRIOR, WEAK_BELOW, doubt_mastery, next_mastery
from tests.conftest import (
    answer,
    ask,
    correct_index,
    create_guest,
    create_session,
    default_scenes,
    finish,
    mastery,
    mastery_of,
    publish,
    save_position,
    scene,
    step,
    submit_check,
    wrong_index,
)

UNKNOWN = "00000000-0000-0000-0000-000000000000"


# --- Mastery rule (pure functions) -----------------------------------------------------


def test_constants():
    assert PRIOR == 0.5
    assert ALPHA_CHECK == 0.4
    assert ALPHA_DOUBT == 0.15
    assert WEAK_BELOW == 0.5


def test_first_correct_check_gives_0_7():
    assert next_mastery(None, True) == pytest.approx(0.7)


def test_first_wrong_check_gives_0_3():
    assert next_mastery(None, False) == pytest.approx(0.3)


def test_next_mastery_moves_toward_outcome():
    assert next_mastery(0.7, True) == pytest.approx(0.82)
    assert next_mastery(0.7, False) == pytest.approx(0.42)
    assert next_mastery(0.3, True) == pytest.approx(0.58)


def test_next_mastery_stays_within_unit_interval():
    assert 0.0 <= next_mastery(1.0, True) <= 1.0
    assert 0.0 <= next_mastery(0.0, False) <= 1.0
    assert next_mastery(1.0, True) == pytest.approx(1.0)
    assert next_mastery(0.0, False) == pytest.approx(0.0)


def test_first_doubt_gives_0_425():
    assert doubt_mastery(None) == pytest.approx(0.425)


def test_doubt_mastery_decays_score():
    assert doubt_mastery(0.8) == pytest.approx(0.68)
    assert doubt_mastery(0.0) == pytest.approx(0.0)
    assert 0.0 <= doubt_mastery(1.0) <= 1.0


# --- Check endpoint ----------------------------------------------------------------------


@pytest.fixture
def guest(client, lesson):
    return create_guest(client)


@pytest.fixture
def session(client, lesson, guest):
    return create_session(client, guest, lesson.slug)


def test_check_response_fields(client, session, lesson):
    ci = correct_index(client, lesson.slug, 0)
    r = submit_check(client, session["id"], 0, ci)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"correct", "correct_index", "explanation", "feedback"}
    assert body["correct"] is True
    assert body["correct_index"] == ci
    assert body["explanation"] == "Explanation about magnetic poles."


def test_wrong_answer_is_reported_with_correct_index(client, session, lesson):
    wi = wrong_index(client, lesson.slug, 0)
    body = submit_check(client, session["id"], 0, wi).json()
    assert body["correct"] is False
    assert body["correct_index"] == correct_index(client, lesson.slug, 0)


def test_correct_check_sets_mastery_0_7_and_one_attempt(client, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    assert mastery(client, guest) == [{"concept": "magnetic poles", "score": pytest.approx(0.7), "attempts": 1}]


def test_wrong_check_sets_mastery_0_3_and_one_attempt(client, session, guest, lesson):
    submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))
    assert mastery(client, guest) == [{"concept": "magnetic poles", "score": pytest.approx(0.3), "attempts": 1}]


def test_same_scene_twice_in_one_session_is_409_and_changes_nothing(client, session, guest, lesson):
    ci = correct_index(client, lesson.slug, 0)
    submit_check(client, session["id"], 0, ci)
    r = submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))
    assert r.status_code == 409
    assert mastery(client, guest) == [{"concept": "magnetic poles", "score": pytest.approx(0.7), "attempts": 1}]


def test_same_scene_in_another_session_updates_again(client, guest, lesson):
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    ci = correct_index(client, lesson.slug, 0)
    submit_check(client, s1["id"], 0, ci)
    r = submit_check(client, s2["id"], 0, ci)
    assert r.status_code == 200
    assert mastery_of(client, guest, "magnetic poles") == {
        "concept": "magnetic poles",
        "score": pytest.approx(0.82),
        "attempts": 2,
    }


def test_checks_on_different_scenes_track_different_concepts(client, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    submit_check(client, session["id"], 1, wrong_index(client, lesson.slug, 1))
    assert mastery_of(client, guest, "magnetic poles")["score"] == pytest.approx(0.7)
    assert mastery_of(client, guest, "attraction")["score"] == pytest.approx(0.3)


# --- Doubt side effects ------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["confused", "repeat", "prerequisite"])
def test_basic_gap_doubt_applies_doubt_mastery_without_attempt(client, llm, session, guest, intent):
    llm.classify(intent=intent, question="samajh nahi aaya")
    ask(client, session["id"], scene_idx=1, step_idx=1)
    assert mastery(client, guest) == [{"concept": "attraction", "score": pytest.approx(0.425), "attempts": 0}]


def test_doubt_mastery_applies_on_top_of_check_score(client, llm, session, guest, lesson):
    submit_check(client, session["id"], 1, correct_index(client, lesson.slug, 1))
    llm.classify(intent="confused", question="phir se samjhao")
    ask(client, session["id"], scene_idx=1, step_idx=0)
    assert mastery_of(client, guest, "attraction") == {
        "concept": "attraction",
        "score": pytest.approx(0.595),
        "attempts": 1,
    }


@pytest.mark.parametrize("intent", ["not_covered", "unclear"])
def test_other_intents_do_not_change_mastery(client, llm, session, guest, intent):
    llm.classify(intent=intent, question="kuch aur")
    ask(client, session["id"], scene_idx=1, step_idx=0)
    for m in mastery(client, guest):
        assert m["score"] == pytest.approx(PRIOR)
        assert m["attempts"] == 0


def test_covered_later_does_not_change_mastery(client, llm, session, guest):
    llm.classify(intent="covered_later", question="compass kaise", target_scene_idx=2, callback="Compass baad mein.")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    for m in mastery(client, guest):
        assert m["score"] == pytest.approx(PRIOR)
        assert m["attempts"] == 0


def test_doubt_in_scene_without_check_question_changes_nothing(client, llm, session, guest):
    llm.classify(intent="confused", question="compass samajh nahi aaya")
    ask(client, session["id"], scene_idx=2, step_idx=0)
    assert mastery(client, guest) == []


# --- GET /api/guests/{id}/mastery --------------------------------------------------------


def test_mastery_unknown_guest_is_404(client):
    assert client.get(f"/api/guests/{UNKNOWN}/mastery").status_code == 404


def test_mastery_empty_for_new_guest(client, guest):
    assert mastery(client, guest) == []


def test_mastery_sorted_by_score_then_concept(client, session, guest, lesson):
    submit_check(client, session["id"], 1, correct_index(client, lesson.slug, 1))  # attraction 0.7
    submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))  # magnetic poles 0.3
    assert [m["concept"] for m in mastery(client, guest)] == ["magnetic poles", "attraction"]


def test_mastery_ties_sorted_by_concept(client, session, guest, lesson):
    submit_check(client, session["id"], 1, wrong_index(client, lesson.slug, 1))
    submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))
    assert [m["concept"] for m in mastery(client, guest)] == ["attraction", "magnetic poles"]


# --- weak_concepts on the guest ------------------------------------------------------------


def test_guest_weak_concepts_lists_scores_below_threshold_in_mastery_order(client, session, guest, lesson):
    submit_check(client, session["id"], 1, correct_index(client, lesson.slug, 1))  # attraction 0.7
    submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))  # magnetic poles 0.3
    r = client.get(f"/api/guests/{guest}")
    assert r.status_code == 200
    assert r.json()["weak_concepts"] == ["magnetic poles"]


def test_guest_weak_concepts_orders_like_mastery(client, session, guest, lesson):
    submit_check(client, session["id"], 1, wrong_index(client, lesson.slug, 1))  # attraction 0.3
    submit_check(client, session["id"], 0, wrong_index(client, lesson.slug, 0))  # magnetic poles 0.3
    assert client.get(f"/api/guests/{guest}").json()["weak_concepts"] == ["attraction", "magnetic poles"]


def test_guest_weak_concepts_includes_doubt_decayed_concepts(client, llm, session, guest):
    llm.classify(intent="confused", question="samajh nahi aaya")
    ask(client, session["id"], scene_idx=1, step_idx=0)  # attraction -> 0.425
    assert client.get(f"/api/guests/{guest}").json()["weak_concepts"] == ["attraction"]


def test_guest_weak_concepts_empty_when_all_strong(client, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    assert client.get(f"/api/guests/{guest}").json()["weak_concepts"] == []


# --- GET /api/guests/{id}/progress -------------------------------------------------------


def progress(client, guest_id: str) -> list[dict]:
    r = client.get(f"/api/guests/{guest_id}/progress")
    assert r.status_code == 200, r.text
    return r.json()


def test_progress_unknown_guest_is_404(client):
    assert client.get(f"/api/guests/{UNKNOWN}/progress").status_code == 404


def test_progress_empty_without_sessions(client, guest):
    assert progress(client, guest) == []


def test_progress_fresh_session_entry(client, session, guest, lesson):
    assert progress(client, guest) == [
        {
            "slug": "magnetism-01",
            "title": "Magnetism",
            "version": 1,
            "percent": 0,
            "completed": False,
            "checks_correct": 0,
            "checks_total": 0,
            "doubts": 0,
            "sessions": 1,
            "resume": None,
        }
    ]


def test_progress_percent_from_saved_position(client, session, guest):
    # steps per scene: 2, 3, 2 -> position (1, 1) has 3 steps strictly before it -> 43 %
    save_position(client, session["id"], 1, 1)
    entry = progress(client, guest)[0]
    assert entry["percent"] == round(100 * 3 / 7)
    assert entry["resume"] == {"session_id": session["id"], "scene_idx": 1, "step_idx": 1}


def test_progress_percent_is_max_over_sessions(client, guest, lesson):
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    save_position(client, s1["id"], 2, 1)  # 6 of 7 -> 86
    save_position(client, s2["id"], 0, 1)  # 1 of 7 -> 14
    entry = progress(client, guest)[0]
    assert entry["percent"] == round(100 * 6 / 7)
    assert entry["sessions"] == 2
    assert entry["resume"]["session_id"] == s2["id"]  # most recently updated session


def test_progress_completed_session_is_100_percent(client, session, guest):
    save_position(client, session["id"], 0, 1)
    finish(client, session["id"])
    entry = progress(client, guest)[0]
    assert entry["percent"] == 100
    assert entry["completed"] is True
    assert entry["resume"] is None


def test_progress_counts_checks_and_doubts(client, llm, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    submit_check(client, session["id"], 1, wrong_index(client, lesson.slug, 1))
    llm.classify(intent="not_covered", question="ek")
    ask(client, session["id"], 0, 0)
    llm.classify(intent="not_covered", question="do")
    ask(client, session["id"], 0, 1)
    llm.classify(intent="confused", question="teen")
    ask(client, session["id"], 1, 0)
    entry = progress(client, guest)[0]
    assert entry["checks_correct"] == 1
    assert entry["checks_total"] == 2
    assert entry["doubts"] == 3


def test_progress_only_counts_latest_version_sessions(client, llm, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    save_position(client, session["id"], 1, 1)
    llm.classify(intent="not_covered", question="ek")
    ask(client, session["id"], 0, 0)
    publish("magnetism-01", title="Magnetism v2", scenes=default_scenes())  # version 2
    entry = progress(client, guest)[0]
    assert entry["slug"] == "magnetism-01"
    assert entry["title"] == "Magnetism v2"
    assert entry["version"] == 2
    assert entry["sessions"] == 0
    assert entry["percent"] == 0
    assert entry["completed"] is False
    assert entry["checks_total"] == 0
    assert entry["checks_correct"] == 0
    assert entry["doubts"] == 0
    assert entry["resume"] is None


def test_progress_new_version_session_counts_after_republish(client, guest, lesson):
    old = create_session(client, guest, lesson.slug)
    save_position(client, old["id"], 2, 1)
    publish("magnetism-01", scenes=default_scenes())
    new = create_session(client, guest, lesson.slug)
    save_position(client, new["id"], 0, 1)
    entry = progress(client, guest)[0]
    assert entry["sessions"] == 1
    assert entry["percent"] == round(100 * 1 / 7)
    assert entry["resume"]["session_id"] == new["id"]


def test_progress_one_entry_per_slug_sorted_by_most_recent_activity(client, guest, lesson):
    publish("light-02", title="Light")
    create_session(client, guest, "magnetism-01")
    create_session(client, guest, "magnetism-01")
    create_session(client, guest, "light-02")
    entries = progress(client, guest)
    assert [e["slug"] for e in entries] == ["light-02", "magnetism-01"]
    assert entries[1]["sessions"] == 2


# --- GET /api/guests/{id}/history ---------------------------------------------------------


def history(client, guest_id: str) -> list[dict]:
    r = client.get(f"/api/guests/{guest_id}/history")
    assert r.status_code == 200, r.text
    return r.json()


def test_history_unknown_guest_is_404(client):
    assert client.get(f"/api/guests/{UNKNOWN}/history").status_code == 404


def test_history_empty_without_sessions(client, guest):
    assert history(client, guest) == []


def test_history_entry_fields(client, session, guest, lesson):
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    submit_check(client, session["id"], 1, wrong_index(client, lesson.slug, 1))
    entries = history(client, guest)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == session["id"]
    assert entry["slug"] == "magnetism-01"
    assert entry["title"] == "Magnetism"
    assert entry["version"] == 1
    assert entry["completed"] is False
    assert entry["checks_correct"] == 1
    assert entry["checks_total"] == 2
    assert entry["doubts"] == []
    datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))


def test_history_marks_completed_after_summary(client, session, guest):
    finish(client, session["id"])
    assert history(client, guest)[0]["completed"] is True


def test_history_newest_session_first(client, guest, lesson):
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    assert [e["session_id"] for e in history(client, guest)] == [s2["id"], s1["id"]]


def test_history_includes_all_versions(client, guest, lesson):
    s1 = create_session(client, guest, lesson.slug)
    publish("magnetism-01", scenes=default_scenes())
    s2 = create_session(client, guest, lesson.slug)
    entries = {e["session_id"]: e for e in history(client, guest)}
    assert entries[s1["id"]]["version"] == 1
    assert entries[s2["id"]]["version"] == 2


def test_history_doubts_in_asked_order_with_intent_and_answered(client, llm, session, guest):
    llm.classify(intent="not_covered", question="pehla sawal")
    d1 = ask(client, session["id"], 0, 0)
    llm.classify(intent="confused", question="doosra sawal")
    ask(client, session["id"], 1, 0)
    llm.classify(intent="prerequisite", question="teesra sawal")
    ask(client, session["id"], 1, 1)
    answer(client, d1)
    doubts = history(client, guest)[0]["doubts"]
    assert doubts == [
        {"question": "pehla sawal", "intent": "not_covered", "answered": True},
        {"question": "doosra sawal", "intent": "confused", "answered": False},
        {"question": "teesra sawal", "intent": "prerequisite", "answered": False},
    ]


def test_history_doubts_belong_to_their_own_session(client, llm, guest, lesson):
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    llm.classify(intent="not_covered", question="s1 ka sawal")
    ask(client, s1["id"], 0, 0)
    llm.classify(intent="not_covered", question="s2 ka sawal")
    ask(client, s2["id"], 0, 0)
    entries = {e["session_id"]: e for e in history(client, guest)}
    assert [d["question"] for d in entries[s1["id"]]["doubts"]] == ["s1 ka sawal"]
    assert [d["question"] for d in entries[s2["id"]]["doubts"]] == ["s2 ka sawal"]


def test_history_is_per_guest(client, lesson):
    g1 = create_guest(client, "One")
    g2 = create_guest(client, "Two")
    create_session(client, g1, lesson.slug)
    assert history(client, g2) == []


def test_history_scene_without_check_does_not_count_toward_checks_total(client, guest):
    publish("light-02", scenes=[scene("Only", "goal", ["ray"], [step("Light."), step("Ray.")], None)])
    create_session(client, guest, "light-02")
    entry = history(client, guest)[0]
    assert entry["checks_total"] == 0
    assert entry["checks_correct"] == 0
