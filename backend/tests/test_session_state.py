"""Spec section 8 (backend): GET /api/sessions/{session_id}/state."""

import pytest

from tests.conftest import (
    answer,
    ask,
    correct_index,
    create_guest,
    create_session,
    default_scenes,
    doubt_id,
    finish,
    publish,
    save_position,
    session_state,
    submit_check,
)

UNKNOWN = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def session(client, lesson):
    guest = create_guest(client)
    s = create_session(client, guest, lesson.slug)
    s["guest_id"] = guest
    return s


def test_unknown_session_is_404(client, lesson):
    assert client.get(f"/api/sessions/{UNKNOWN}/state").status_code == 404


def test_fresh_session_state(client, session):
    assert session_state(client, session["id"]) == {
        "session_id": session["id"],
        "completed": False,
        "position": None,
        "answered_checks": [],
        "doubts": [],
    }


# --- position / completed ----------------------------------------------------------------


def test_position_reflects_saved_position(client, session):
    save_position(client, session["id"], 1, 2)
    assert session_state(client, session["id"])["position"] == {"scene_idx": 1, "step_idx": 2}


def test_position_reflects_last_write(client, session):
    save_position(client, session["id"], 1, 2)
    save_position(client, session["id"], 2, 0)
    assert session_state(client, session["id"])["position"] == {"scene_idx": 2, "step_idx": 0}


def test_completed_after_summary(client, session):
    finish(client, session["id"])
    assert session_state(client, session["id"])["completed"] is True


# --- answered_checks ---------------------------------------------------------------------


def test_answered_checks_lists_scenes_ascending(client, session, lesson):
    submit_check(client, session["id"], 1, correct_index(client, lesson.slug, 1))
    submit_check(client, session["id"], 0, correct_index(client, lesson.slug, 0))
    assert session_state(client, session["id"])["answered_checks"] == [0, 1]


def test_answered_checks_includes_wrong_answers(client, session, lesson):
    wrong = next(i for i in range(4) if i != correct_index(client, lesson.slug, 0))
    submit_check(client, session["id"], 0, wrong)
    assert session_state(client, session["id"])["answered_checks"] == [0]


def test_answered_checks_has_no_duplicates_after_rejected_repeat(client, session, lesson):
    ci = correct_index(client, lesson.slug, 0)
    submit_check(client, session["id"], 0, ci)
    assert submit_check(client, session["id"], 0, ci).status_code == 409
    assert session_state(client, session["id"])["answered_checks"] == [0]


def test_answered_checks_are_per_session(client, session, lesson):
    other = create_session(client, session["guest_id"], lesson.slug)
    submit_check(client, other["id"], 0, correct_index(client, lesson.slug, 0))
    assert session_state(client, session["id"])["answered_checks"] == []


# --- doubts -------------------------------------------------------------------------------


def test_doubts_in_asked_order_with_question_intent_and_position(client, llm, session):
    llm.classify(intent="not_covered", question="pehla sawal")
    d1 = ask(client, session["id"], scene_idx=0, step_idx=1)
    llm.classify(intent="confused", question="doosra sawal")
    d2 = ask(client, session["id"], scene_idx=1, step_idx=2)
    doubts = session_state(client, session["id"])["doubts"]
    assert [d["id"] for d in doubts] == [doubt_id(d1), doubt_id(d2)]
    assert [d["question"] for d in doubts] == ["pehla sawal", "doosra sawal"]
    assert [d["intent"] for d in doubts] == ["not_covered", "confused"]
    assert [(d["scene_idx"], d["step_idx"]) for d in doubts] == [(0, 1), (1, 2)]


def test_doubt_entry_keys(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    entry = session_state(client, session["id"])["doubts"][0]
    assert set(entry.keys()) == {"id", "scene_idx", "step_idx", "question", "intent", "target_scene_idx", "answered", "callback"}


def test_doubts_include_clarify_and_unclear(client, llm, session):
    llm.classify(intent="confused", question="dhundhla sawal", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="unclear", question="kuch samajh nahi aaya")
    ask(client, session["id"], scene_idx=0, step_idx=1)
    intents = [d["intent"] for d in session_state(client, session["id"])["doubts"]]
    assert intents == ["clarify", "unclear"]


def test_confirmed_clarify_reports_the_restored_intent(client, llm, stt, session):
    llm.classify(intent="prerequisite", question="Magnet ka matlab kya hai?", confidence=0.4)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    stt.text = "haan"
    confirmed = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert confirmed["intent"] == "prerequisite"
    doubts = session_state(client, session["id"])["doubts"]
    last = doubts[-1]
    assert last["intent"] == "prerequisite"
    assert last["question"] == "Magnet ka matlab kya hai?"
    assert last["callback"] is None


def test_doubts_exclude_other_sessions(client, llm, session, lesson):
    other = create_session(client, session["guest_id"], lesson.slug)
    llm.classify(intent="not_covered", question="doosre session ka")
    ask(client, other["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="not_covered", question="mera")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert [d["question"] for d in session_state(client, session["id"])["doubts"]] == ["mera"]


def test_answered_false_before_stream_and_true_after(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    d = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert session_state(client, session["id"])["doubts"][0]["answered"] is False
    answer(client, d)
    assert session_state(client, session["id"])["doubts"][0]["answered"] is True


def test_answered_is_per_doubt(client, llm, session):
    llm.classify(intent="not_covered", question="ek")
    d1 = ask(client, session["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="confused", question="do")
    ask(client, session["id"], scene_idx=0, step_idx=1)
    answer(client, d1)
    assert [d["answered"] for d in session_state(client, session["id"])["doubts"]] == [True, False]


# --- callback / target --------------------------------------------------------------------


def test_covered_later_doubt_carries_target_and_callback(client, llm, session):
    llm.classify(intent="covered_later", question="compass kaise", target_scene_idx=2, callback="Tumne pucha tha compass ke baare mein.")
    routed = ask(client, session["id"], scene_idx=0, step_idx=1)
    entry = session_state(client, session["id"])["doubts"][0]
    assert entry["intent"] == "covered_later"
    assert entry["target_scene_idx"] == 2
    assert set(entry["callback"].keys()) == {"text", "audio_url", "duration_ms"}
    assert entry["callback"]["text"] == "Tumne pucha tha compass ke baare mein."
    assert entry["callback"]["audio_url"] == routed["callback"]["audio_url"]
    assert isinstance(entry["callback"]["duration_ms"], int)


@pytest.mark.parametrize("intent", ["not_covered", "prerequisite", "confused", "repeat", "unclear"])
def test_other_intents_have_null_callback_and_target(client, llm, session, intent):
    llm.classify(intent=intent, question="sawal", target_scene_idx=2, callback="Baad mein.")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    entry = session_state(client, session["id"])["doubts"][0]
    assert entry["intent"] == intent
    assert entry["callback"] is None
    assert entry["target_scene_idx"] is None


def test_downgraded_covered_later_has_null_callback(client, llm, session):
    llm.classify(intent="covered_later", question="sawal", target_scene_idx=0, callback="Baad mein.")
    ask(client, session["id"], scene_idx=1, step_idx=0)  # target is not ahead -> not_covered
    entry = session_state(client, session["id"])["doubts"][0]
    assert entry["intent"] == "not_covered"
    assert entry["callback"] is None
    assert entry["target_scene_idx"] is None


def test_state_survives_republish_of_the_lesson(client, llm, session, lesson):
    llm.classify(intent="not_covered", question="purana")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    save_position(client, session["id"], 1, 1)
    publish(lesson.slug, scenes=default_scenes())
    state = session_state(client, session["id"])
    assert state["position"] == {"scene_idx": 1, "step_idx": 1}
    assert [d["question"] for d in state["doubts"]] == ["purana"]
