"""Spec section 9: remediation after a wrong check answer."""

import copy
import json

import pytest

from app.progress import ALPHA_RETRY, retry_mastery
from app.prompts import CHECK_FOLLOWUP, CHECK_LINES
from app.quiz import shuffle_checks
from app.schemas import RemedyDraft
from tests.conftest import (
    answer,
    ask,
    check,
    create_guest,
    create_session,
    lesson_detail,
    mastery_of,
    publish,
    scene,
    session_state,
    step,
    submit_check,
)

UNKNOWN = "0" * 32


def spoken(text: str) -> dict:
    return {"text": text, "audio_url": f"/media/test/{len(text)}.wav", "duration_ms": 900}


def remedy_check(concept: str, correct_index: int = 0, with_hints: bool = True, with_twin: bool = True) -> dict:
    """A check question with options opt0..opt3, a hint per wrong option and a twin whose answer is twin-opt0."""
    opts = [f"{concept}-opt{j}" for j in range(4)]
    cq = check(concept, options=opts, correct_index=correct_index)
    if with_hints:
        cq["why_wrong"] = [None if j == correct_index else spoken(f"hint for {opts[j]}") for j in range(4)]
    cq["reteach"] = [
        {"say": "Ek aur tarike se dekho.", "draw": [], "audio_url": "/media/test/re0.wav", "duration_ms": 1000},
        {"say": "Ab samjhe?", "draw": [], "audio_url": "/media/test/re1.wav", "duration_ms": 1000},
    ]
    cq["followup"] = spoken(CHECK_FOLLOWUP)
    if with_twin:
        cq["twin"] = {
            "question": f"Twin about {concept}?",
            "say": f"Twin about {concept}?",
            "options": [f"twin-opt{j}" for j in range(4)],
            "correct_index": 0,
            "explanation": f"Twin explanation about {concept}.",
            "audio_url": "/media/test/twin.wav",
        }
    return cq


def remedy_scenes() -> list[dict]:
    return [
        scene("Magnets", "What a magnet is", ["magnet"], [step("Scene zero step zero."), step("Scene zero step one.")],
              remedy_check("poles")),
        scene("Attraction", "Poles attract", ["pole"], [step("Scene one step zero.")],
              remedy_check("attraction", with_hints=False, with_twin=False)),
        scene("Compass", "Build a compass", ["compass"], [step("Scene two step zero.")], None),
    ]  # fmt: skip


@pytest.fixture
def lesson():
    return publish(scenes=remedy_scenes())


@pytest.fixture
def guest(client, lesson):
    return create_guest(client)


@pytest.fixture
def session(client, lesson, guest):
    return create_session(client, guest, lesson.slug)


def stored(client, scene_idx: int) -> dict:
    return lesson_detail(client)["scenes"][scene_idx]["check_question"]


def wrong_of(cq: dict, n: int = 0) -> int:
    return [i for i in range(len(cq["options"])) if i != cq["correct_index"]][n]


def retry(client, session_id: str, scene_idx: int, chosen: int):
    return client.post(f"/api/sessions/{session_id}/check/retry", json={"scene_idx": scene_idx, "chosen_index": chosen})


def twin(client, session_id: str, scene_idx: int, chosen: int):
    return client.post(f"/api/sessions/{session_id}/check/twin", json={"scene_idx": scene_idx, "chosen_index": chosen})


def progress_entry(client, guest_id: str) -> dict:
    r = client.get(f"/api/guests/{guest_id}/progress")
    assert r.status_code == 200, r.text
    return r.json()[0]


# --- shuffling ------------------------------------------------------------------------------


def test_shuffle_keeps_each_hint_with_its_option():
    checks = [remedy_check(f"c{i}", correct_index=i % 4) for i in range(8)]
    out = shuffle_checks(copy.deepcopy(checks), seed="hints")
    for before, after in zip(checks, out):
        hint_of = {o: h for o, h in zip(before["options"], before["why_wrong"])}
        assert len(after["why_wrong"]) == 4
        for o, h in zip(after["options"], after["why_wrong"]):
            assert h == hint_of[o]
        assert after["why_wrong"][after["correct_index"]] is None


def test_shuffle_with_hints_moves_options_like_without_hints():
    with_hints = [remedy_check(f"c{i}") for i in range(6)]
    without = copy.deepcopy(with_hints)
    for q in without:
        del q["why_wrong"]
    a = shuffle_checks(with_hints, seed="same")
    b = shuffle_checks(without, seed="same")
    assert [q["options"] for q in a] == [q["options"] for q in b]
    assert [q["correct_index"] for q in a] == [q["correct_index"] for q in b]


def test_publish_shuffles_twins_with_their_own_seed(client):
    scenes = remedy_scenes()
    twins = [s["check_question"].get("twin") if s["check_question"] else None for s in scenes]
    expected = shuffle_checks(copy.deepcopy(twins), seed="magnetism-01:twin")
    publish(scenes=copy.deepcopy(scenes))
    got = [s["check_question"] and s["check_question"].get("twin") for s in lesson_detail(client)["scenes"]]
    assert got[0]["options"] == expected[0]["options"]
    assert got[0]["correct_index"] == expected[0]["correct_index"]
    assert got[0]["options"][got[0]["correct_index"]] == "twin-opt0"
    assert not got[1]
    assert got[2] is None


def test_published_check_keeps_remediation_fields(client, lesson):
    cq = stored(client, 0)
    assert len(cq["reteach"]) == 2
    assert cq["followup"]["text"] == CHECK_FOLLOWUP
    assert cq["why_wrong"][cq["correct_index"]] is None


# --- first answer feedback -------------------------------------------------------------------


def test_first_wrong_with_hints_does_not_reveal_answer(client, session):
    cq = stored(client, 0)
    body = submit_check(client, session["id"], 0, wrong_of(cq)).json()
    assert body["correct"] is False
    assert body["feedback"]["text"] == CHECK_LINES["wrong_try_again"]
    assert "Sahi jawab option" not in body["feedback"]["text"]


def test_first_wrong_without_hints_reveals_answer(client, session):
    cq = stored(client, 1)
    body = submit_check(client, session["id"], 1, wrong_of(cq)).json()
    letter = "ABCD"[cq["correct_index"]]
    assert body["feedback"]["text"].endswith(f"Sahi jawab option {letter} hai.")


def test_first_correct_feedback(client, session):
    cq = stored(client, 0)
    body = submit_check(client, session["id"], 0, cq["correct_index"]).json()
    assert body["feedback"]["text"] == CHECK_LINES["correct"]


# --- retry ------------------------------------------------------------------------------------


def test_retry_mastery_rule():
    assert ALPHA_RETRY == 0.2
    assert retry_mastery(None, True) == pytest.approx(0.6)
    assert retry_mastery(0.3, True) == pytest.approx(0.44)
    assert retry_mastery(0.3, False) == pytest.approx(0.24)
    assert retry_mastery(1.0, True) == pytest.approx(1.0)
    assert retry_mastery(0.0, False) == pytest.approx(0.0)


def test_retry_unknown_session_is_404(client, lesson):
    assert retry(client, UNKNOWN, 0, 0).status_code == 404


def test_retry_scene_without_check_is_404(client, session):
    assert retry(client, session["id"], 2, 0).status_code == 404


def test_retry_without_first_answer_is_409(client, session, guest):
    assert retry(client, session["id"], 0, 0).status_code == 409
    assert mastery_of(client, guest, "poles") is None


def test_retry_after_correct_first_answer_is_409(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, cq["correct_index"])
    assert retry(client, session["id"], 0, cq["correct_index"]).status_code == 409
    assert mastery_of(client, guest, "poles")["score"] == pytest.approx(0.7)


def test_correct_retry(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, wrong_of(cq))
    r = retry(client, session["id"], 0, cq["correct_index"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"correct", "correct_index", "explanation", "feedback"}
    assert body["correct"] is True
    assert body["correct_index"] == cq["correct_index"]
    assert body["explanation"] == "Explanation about poles."
    assert body["feedback"]["text"] == CHECK_LINES["retry_correct"]
    assert mastery_of(client, guest, "poles") == {"concept": "poles", "score": pytest.approx(0.44), "attempts": 1}


def test_wrong_retry_reveals_answer(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, wrong_of(cq, 0))
    body = retry(client, session["id"], 0, wrong_of(cq, 1)).json()
    assert body["correct"] is False
    assert body["feedback"]["text"].endswith(f"Sahi jawab option {'ABCD'[cq['correct_index']]} hai.")
    assert mastery_of(client, guest, "poles") == {"concept": "poles", "score": pytest.approx(0.24), "attempts": 1}


def test_second_retry_is_409_and_changes_nothing(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, wrong_of(cq))
    retry(client, session["id"], 0, cq["correct_index"])
    assert retry(client, session["id"], 0, cq["correct_index"]).status_code == 409
    assert mastery_of(client, guest, "poles")["score"] == pytest.approx(0.44)


def test_retry_is_not_counted_as_a_check(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, wrong_of(cq))
    retry(client, session["id"], 0, cq["correct_index"])
    twin(client, session["id"], 0, 0)
    entry = progress_entry(client, guest)
    assert (entry["checks_correct"], entry["checks_total"]) == (0, 1)
    assert session_state(client, session["id"])["answered_checks"] == [0]


# --- twin -------------------------------------------------------------------------------------


def stored_twin(client) -> dict:
    return stored(client, 0)["twin"]


def test_twin_scene_without_twin_is_404(client, session):
    cq = stored(client, 1)
    submit_check(client, session["id"], 1, wrong_of(cq))
    assert twin(client, session["id"], 1, 0).status_code == 404
    assert twin(client, session["id"], 2, 0).status_code == 404


def test_twin_unknown_session_is_404(client, lesson):
    assert twin(client, UNKNOWN, 0, 0).status_code == 404


def test_twin_without_wrong_first_answer_is_409(client, session):
    assert twin(client, session["id"], 0, 0).status_code == 409
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, cq["correct_index"])
    assert twin(client, session["id"], 0, 0).status_code == 409


def test_correct_twin(client, session, guest):
    cq = stored(client, 0)
    t = stored_twin(client)
    submit_check(client, session["id"], 0, wrong_of(cq))  # 0.3
    r = twin(client, session["id"], 0, t["correct_index"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["correct"] is True
    assert body["correct_index"] == t["correct_index"]
    assert body["explanation"] == "Twin explanation about poles."
    assert body["feedback"]["text"] == CHECK_LINES["twin_correct"]
    assert mastery_of(client, guest, "poles") == {"concept": "poles", "score": pytest.approx(0.58), "attempts": 2}


def test_wrong_twin_reveals_twin_answer(client, session, guest):
    cq = stored(client, 0)
    t = stored_twin(client)
    submit_check(client, session["id"], 0, wrong_of(cq))
    body = twin(client, session["id"], 0, wrong_of(t)).json()
    assert body["correct"] is False
    assert body["feedback"]["text"].endswith(f"Sahi jawab option {'ABCD'[t['correct_index']]} hai.")
    assert mastery_of(client, guest, "poles") == {"concept": "poles", "score": pytest.approx(0.18), "attempts": 2}


def test_twin_after_retry_still_allowed(client, session, guest):
    cq = stored(client, 0)
    submit_check(client, session["id"], 0, wrong_of(cq))  # 0.3
    retry(client, session["id"], 0, cq["correct_index"])  # 0.44
    assert twin(client, session["id"], 0, stored_twin(client)["correct_index"]).status_code == 200
    assert mastery_of(client, guest, "poles")["score"] == pytest.approx(0.44 + 0.4 * 0.56)


def test_second_twin_is_409(client, session, guest):
    cq = stored(client, 0)
    t = stored_twin(client)
    submit_check(client, session["id"], 0, wrong_of(cq))
    twin(client, session["id"], 0, t["correct_index"])
    assert twin(client, session["id"], 0, t["correct_index"]).status_code == 409
    assert mastery_of(client, guest, "poles")["attempts"] == 2


# --- summary ----------------------------------------------------------------------------------


def test_summary_lists_retry_and_twin_results(client, llm, session):
    cq0, cq1 = stored(client, 0), stored(client, 1)
    submit_check(client, session["id"], 0, wrong_of(cq0))
    retry(client, session["id"], 0, cq0["correct_index"])
    twin(client, session["id"], 0, wrong_of(stored_twin(client)))
    submit_check(client, session["id"], 1, cq1["correct_index"])
    client.post(f"/api/sessions/{session['id']}/summary", json={})
    call = next(c for c in llm.calls if c["schema"].__name__ == "SummaryDraft")
    results = json.loads(call["user"])["check_results"]
    assert results[0]["concept"] == "poles"
    assert results[0]["correct"] is False
    assert results[0]["retry_correct"] is True
    assert results[0]["twin_correct"] is False
    assert results[1]["retry_correct"] is None
    assert results[1]["twin_correct"] is None


# --- doubts during a check -----------------------------------------------------------------


def ask_during_check(client, session_id: str, scene_idx: int = 0, step_idx: int = 1) -> dict:
    from tests.conftest import QUESTION_WAV

    r = client.post(
        f"/api/sessions/{session_id}/questions",
        files={"audio": ("question.wav", QUESTION_WAV, "audio/wav")},
        data={"scene_idx": str(scene_idx), "step_idx": str(step_idx), "during_check": "true"},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("intent", ["not_covered", "prerequisite", "repeat", "confused"])
def test_during_check_doubts_become_confused_now(client, llm, session, intent):
    llm.classify(intent=intent, question="option B kyun galat hai?")
    res = ask_during_check(client, session["id"])
    assert res["intent"] == "confused"
    assert res["answer_when"] == "now"
    assert res["target_scene_idx"] is None
    assert res["callback"] is None


def test_during_check_covered_later_becomes_confused(client, llm, session):
    llm.classify(intent="covered_later", target_scene_idx=2, callback="Tumne pucha tha ki x, chalo ab yahi dekhte hain.")
    res = ask_during_check(client, session["id"])
    assert (res["intent"], res["answer_when"], res["target_scene_idx"], res["callback"]) == ("confused", "now", None, None)


def test_during_check_low_confidence_asks_back(client, llm, session):
    llm.classify(intent="not_covered", question="option B kyun galat hai?", confidence=0.2)
    res = ask_during_check(client, session["id"])
    assert res["intent"] == "clarify"
    assert res["answer_when"] == "none"
    assert "option B kyun galat hai" in res["ack"]["text"]


def test_during_check_confirmed_clarify_is_answered_now(client, llm, session):
    llm.classify(intent="not_covered", question="option B kyun galat hai?", confidence=0.2)
    ask_during_check(client, session["id"])
    import app.providers

    app.providers.get_stt().text = "haan"
    res = ask_during_check(client, session["id"])
    assert (res["intent"], res["answer_when"]) == ("confused", "now")
    assert len(llm.routing_calls) == 1


@pytest.mark.parametrize("intent", ["off_topic", "unclear"])
def test_during_check_off_topic_and_unclear_stay(client, llm, session, intent):
    llm.classify(intent=intent, ack="Chalo lesson pe aate hain." if intent == "off_topic" else None)
    assert ask_during_check(client, session["id"])["intent"] == intent


def test_without_flag_routing_is_unchanged(client, llm, session):
    llm.classify(intent="not_covered", confidence=0.2)
    assert ask(client, session["id"], 0, 1)["intent"] == "clarify"


def test_answer_prompt_has_check_answers_for_doubt_after_check(client, llm, session):
    cq = stored(client, 0)
    first, second = wrong_of(cq, 0), wrong_of(cq, 1)
    submit_check(client, session["id"], 0, first)
    retry(client, session["id"], 0, second)
    llm.classify(intent="confused", question="mujhe samajh nahi aaya")
    res = ask_during_check(client, session["id"])
    answer(client, res["doubt_id"])
    prompt = llm.stream_calls[-1]["user"]
    assert "Student's check answers:" in prompt
    tail = prompt.split("Student's check answers:", 1)[1]
    assert cq["question"] in tail
    assert cq["options"][first] in tail
    assert cq["options"][second] in tail
    assert cq["options"][cq["correct_index"]] in tail
    assert cq["explanation"] in tail


def test_answer_prompt_without_check_answer_has_no_check_section(client, llm, session):
    llm.classify(intent="confused")
    res = ask(client, session["id"], 0, 1)
    answer(client, res["doubt_id"])
    assert "Student's check answers:" not in llm.stream_calls[-1]["user"]


def test_doubt_asked_before_the_check_has_no_check_section(client, llm, session):
    llm.classify(intent="not_covered")
    res = ask(client, session["id"], 0, 1)
    submit_check(client, session["id"], 0, wrong_of(stored(client, 0)))
    answer(client, res["doubt_id"])
    assert "Student's check answers:" not in llm.stream_calls[-1]["user"]


# --- prep schema -------------------------------------------------------------------------------


def remedy_draft(**over) -> dict:
    d = {
        "why_wrong": [
            {"option": 1, "hint": "Lagta hai tumne socha ki magnet sab kuch kheenchta hai."},
            {"option": 2, "hint": "Yeh ulta ho gaya."},
        ],
        "reteach": [{"say": "Chalo ek aur tarike se dekhte hain.", "draw": []}, {"say": "Ab yeh saaf hai.", "draw": []}],
        "twin": {
            "question": "Which pole?",
            "say": "Batao, kaunsa pole?",
            "options": ["N", "S"],
            "correct_index": 0,
            "explanation": "North pole.",
        },
    }
    d.update(over)
    return d


def test_remedy_draft_accepts_roman_script():
    draft = RemedyDraft.model_validate(remedy_draft())
    assert len(draft.reteach) == 2
    assert draft.twin.correct_index == 0


@pytest.mark.parametrize(
    "over",
    [
        {"why_wrong": [{"option": 1, "hint": "यह गलत है"}]},
        {"reteach": [{"say": "चलो देखते हैं", "draw": []}, {"say": "Ab yeh saaf hai.", "draw": []}]},
    ],
)
def test_remedy_draft_rejects_devanagari(over):
    with pytest.raises(ValueError):
        RemedyDraft.model_validate(remedy_draft(**over))


def test_remedy_draft_rejects_devanagari_twin_say():
    d = remedy_draft()
    d["twin"]["say"] = "बताओ"
    with pytest.raises(ValueError):
        RemedyDraft.model_validate(d)


@pytest.mark.parametrize("n", [1, 5])
def test_remedy_draft_reteach_has_2_to_4_steps(n):
    with pytest.raises(ValueError):
        RemedyDraft.model_validate(remedy_draft(reteach=[{"say": "Ek line.", "draw": []}] * n))


def test_remediate_puts_each_hint_on_its_labelled_option(monkeypatch, tts):
    """The LLM labels hints by option index; skipping the correct option must not shift them."""
    import asyncio

    import app.llm
    from app.prep.__main__ import remediate
    from app.schemas import Outline, SceneDraft

    draft = remedy_draft(why_wrong=[{"option": 3, "hint": "Hint for three."}, {"option": 1, "hint": "Hint for one."}])

    async def fake_complete_json(system, user, schema, temperature=0.4, model=None):
        if schema.__name__ == "RemedyReview":
            return schema.model_validate({"issues": [], "remedy": draft})
        return schema.model_validate(draft)

    monkeypatch.setattr(app.llm, "complete_json", fake_complete_json)
    outline = Outline(title="T", scenes=[{"title": "S", "goal": "G", "transcript_focus": "F"}])
    scene_draft = SceneDraft.model_validate(
        {
            "steps": [{"say": "Ek step.", "draw": []}],
            "check_question": {"question": "Q?", "say": "Q?", "options": ["a", "b", "c", "d"], "correct_index": 2,
                               "concept": "c", "explanation": "E."},
        }
    )  # fmt: skip
    [out] = asyncio.run(remediate("remedy-test", outline, [scene_draft]))
    texts = [h and h["text"] for h in out["why_wrong"]]
    assert texts == [None, "Hint for one.", None, "Hint for three."]
    assert out["followup"]["text"] == CHECK_FOLLOWUP
    assert len(out["reteach"]) == 2 and out["reteach"][0]["audio_url"].endswith(".wav")
