"""Spec section 11: content quality fixes found in the nomenclature-01 session review."""

import asyncio
import json

import pytest

import app.llm
from app.config import settings
from app.prompts import FIXED_ACKS
from app.schemas import Outline, SceneDraft, leaks_answer
from tests.conftest import (
    QUESTION_WAV,
    answer,
    ask,
    create_guest,
    create_session,
    finish,
    lesson_detail,
    mastery,
    submit_check,
    wrong_index,
)
from tests.test_remediation import remedy_draft, remedy_scenes

UNKNOWN = "0" * 32


@pytest.fixture
def lesson():
    from tests.conftest import publish

    return publish(scenes=remedy_scenes())


@pytest.fixture
def guest(client, lesson):
    return create_guest(client)


@pytest.fixture
def session(client, lesson, guest):
    return create_session(client, guest, lesson.slug)


def ask_during_check(client, session_id: str, scene_idx: int = 0) -> dict:
    r = client.post(
        f"/api/sessions/{session_id}/questions",
        files={"audio": ("question.wav", QUESTION_WAV, "audio/wav")},
        data={"scene_idx": str(scene_idx), "step_idx": "1", "during_check": "true"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def skip(client, session_id: str, scene_idx: int, twin: bool = False):
    return client.post(f"/api/sessions/{session_id}/check/skip", json={"scene_idx": scene_idx, "twin": twin})


# --- continue ---------------------------------------------------------------------------------


def test_continue_intent_needs_no_answer(client, llm, session):
    llm.classify(intent="continue", question="sab clear hai, aage chalo")
    res = ask(client, session["id"], 0, 1)
    assert (res["intent"], res["answer_when"]) == ("continue", "none")
    assert res["ack"]["text"] == FIXED_ACKS["continue"]


def test_continue_during_check_stays_continue(client, llm, session):
    llm.classify(intent="continue", confidence=0.3)
    assert ask_during_check(client, session["id"])["intent"] == "continue"


# --- routing sees the check card ----------------------------------------------------------------


def test_routing_during_check_sees_lettered_options_and_picks(client, llm, session):
    cq = lesson_detail(client)["scenes"][0]["check_question"]
    first = wrong_index(client, "magnetism-01", 0)
    submit_check(client, session["id"], 0, first)
    llm.classify(intent="confused")
    ask_during_check(client, session["id"])
    user = llm.routing_calls[-1]["user"]
    assert "Check question on screen:" in user
    for letter, option in zip("ABCD", cq["options"]):
        assert f"{letter}. {option}" in user
    assert f"Student picked (wrong): {'ABCD'[first]}" in user


def test_routing_outside_a_check_has_no_check_card(client, llm, session):
    submit_check(client, session["id"], 0, wrong_index(client, "magnetism-01", 0))
    llm.classify(intent="confused")
    ask(client, session["id"], 1, 0)
    assert "Check question on screen:" not in llm.routing_calls[-1]["user"]


# --- answers ----------------------------------------------------------------------------------


def test_answer_sees_earlier_doubts_and_their_answers(client, llm, session):
    llm.answer_lines = ['{"say": "Pehla jawab.", "draw": []}']
    llm.classify(intent="confused", question="Kya saare atoms count hote hain?")
    first = ask(client, session["id"], 0, 1)
    answer(client, first["doubt_id"])
    llm.classify(intent="confused", question="Maine pehle pucha tha, kya saare atoms count hote hain?")
    second = ask(client, session["id"], 1, 0)
    answer(client, second["doubt_id"])
    prompt = llm.stream_calls[-1]["user"]
    assert "Earlier doubts in this session" in prompt
    tail = prompt.split("Earlier doubts in this session", 1)[1]
    assert "Kya saare atoms count hote hain?" in tail
    assert "Pehla jawab." in tail


def test_first_answer_has_no_earlier_doubts(client, llm, session):
    llm.classify(intent="confused")
    res = ask(client, session["id"], 0, 1)
    answer(client, res["doubt_id"])
    assert "Earlier doubts in this session" not in llm.stream_calls[-1]["user"]


def test_source_line_is_not_spoken_or_stored(client, llm, tts, session):
    llm.answer_lines = [
        '{"source": "Teacher: DU = C + 1 - (H + X - N) / 2"}',
        '{"say": "Haan, H ke number pe depend karta hai.", "draw": []}',
    ]
    llm.classify(intent="confused")
    res = ask(client, session["id"], 0, 1)
    rows = [json.loads(line) for line in answer(client, res["doubt_id"]).splitlines() if line.strip()]
    assert [r["say"] for r in rows] == ["Haan, H ke number pe depend karta hai."]
    assert not any("Teacher: DU" in t for t in tts.texts)


# --- skips ------------------------------------------------------------------------------------


def test_skip_is_recorded_once_without_mastery(client, llm, session, guest):
    assert skip(client, session["id"], 0).status_code == 200
    assert skip(client, session["id"], 0).status_code == 200
    assert mastery(client, guest) == []
    finish(client, session["id"])
    call = next(c for c in llm.calls if c["schema"].__name__ == "SummaryDraft")
    assert json.loads(call["user"])["skipped_checks"] == [0]


def test_twin_skip_shows_in_summary(client, llm, session):
    submit_check(client, session["id"], 0, wrong_index(client, "magnetism-01", 0))
    assert skip(client, session["id"], 0, twin=True).status_code == 200
    finish(client, session["id"])
    call = next(c for c in llm.calls if c["schema"].__name__ == "SummaryDraft")
    result = json.loads(call["user"])["check_results"][0]
    assert result["twin_skipped"] is True
    assert result["twin_correct"] is None


def test_skip_404s(client, session):
    assert skip(client, UNKNOWN, 0).status_code == 404
    assert skip(client, session["id"], 2).status_code == 404  # no check question
    assert skip(client, session["id"], 1, twin=True).status_code == 404  # no twin


# --- hint leaks -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hint,option,leaks",
    [
        ("Molecular mass badhta hai. Functional group consistent hota hai. Ek baar phir socho.", "Functional group", True),
        ("FUNCTIONAL-GROUP hi same rehta hai", "Functional group", True),
        ("Triple bond mein do pi bonds hote hain.", "Two", False),  # too short to judge by text
        ("Lagta hai tumne socha ki Faraday ka law hai.", "Biot-Savart Law", False),
        ("Yeh functional groups ki baat hai", "Functional group", False),  # whole words only
    ],
)
def test_leaks_answer(hint, option, leaks):
    assert leaks_answer(hint, option) is leaks


# --- prep: review and model ---------------------------------------------------------------------


def scene_draft() -> SceneDraft:
    return SceneDraft.model_validate(
        {
            "steps": [{"say": "Ek step.", "draw": []}],
            "check_question": {"question": "Q?", "say": "Q?", "options": ["OH se", "CHO se", "Kahin se", "ABC"],
                               "correct_index": 0, "concept": "c", "explanation": "OH ki priority."},
        }
    )  # fmt: skip


def outline() -> Outline:
    return Outline(title="T", scenes=[{"title": "S", "goal": "G", "transcript_focus": "F"}])


def test_review_scene_returns_the_corrected_check(monkeypatch):
    from app.prep.__main__ import review_scene

    seen = {}

    async def fake(system, user, schema, temperature=0.4, model=None):
        seen["user"], seen["schema"] = user, schema.__name__
        fixed = scene_draft().check_question.model_copy(update={"correct_index": 1, "explanation": "CHO ki priority."})
        return schema.model_validate({"issues": ["CHO outranks OH"], "narration_issues": [], "check_question": fixed.model_dump()})

    monkeypatch.setattr(app.llm, "complete_json", fake)
    out = asyncio.run(review_scene(outline().scenes[0], scene_draft(), "LECTURE TEXT"))
    assert seen["schema"] == "CheckReview"
    assert "LECTURE TEXT" in seen["user"] and "Option 1: CHO se" in seen["user"]
    assert out["check_question"]["correct_index"] == 1
    assert out["issues"] == ["CHO outranks OH"]


def test_remedy_is_reviewed_and_leaking_hints_dropped(monkeypatch, tts):
    from app.prep.__main__ import make_remedy

    schemas = []
    leaky = remedy_draft(why_wrong=[{"option": 1, "hint": "Socho, numbering OH se hi hoti hai."}, {"option": 2, "hint": "Kahin se nahi hota."}])
    leaky["twin"]["say"] = "Chalo, ek aur sawal."

    async def fake(system, user, schema, temperature=0.4, model=None):
        schemas.append(schema.__name__)
        if schema.__name__ == "RemedyReview":
            return schema.model_validate({"issues": [], "remedy": leaky})
        return schema.model_validate(remedy_draft())

    monkeypatch.setattr(app.llm, "complete_json", fake)
    d = scene_draft()
    d.check_question.options[0] = "Numbering OH se"  # long enough to be judged by text
    out = asyncio.run(make_remedy(outline().scenes[0], d, None, "LECTURE"))
    assert schemas == ["RemedyDraft", "RemedyReview"]
    assert [h.option for h in out.why_wrong] == [2]
    assert out.twin.say == "Chalo, ek aur sawal."


def test_prep_uses_prep_deployment_and_reasoning_effort(monkeypatch):
    calls = []

    async def fake(system, user, schema, temperature=0.4, model=None, reasoning_effort=None):
        calls.append((model, reasoning_effort, temperature))
        return schema(title="T", scenes=[])

    monkeypatch.setattr(app.llm, "complete_json", fake)
    monkeypatch.setattr(settings, "azure_openai_prep_deployment", "o4-mini")
    monkeypatch.setattr(settings, "azure_openai_prep_reasoning_effort", "high")
    asyncio.run(app.llm.prep_json("s", "u", Outline))
    monkeypatch.setattr(settings, "azure_openai_prep_reasoning_effort", None)
    asyncio.run(app.llm.prep_json("s", "u", Outline))
    monkeypatch.setattr(settings, "azure_openai_prep_deployment", None)
    asyncio.run(app.llm.prep_json("s", "u", Outline))
    assert calls[0][:2] == ("o4-mini", "high")
    assert calls[1][:2] == ("o4-mini", None)
    assert calls[2][0] == settings.azure_openai_deployment


def test_prep_json_retries_a_reply_that_never_validates(monkeypatch):
    from pydantic import ValidationError

    from app.schemas import RemedyDraft

    calls = []

    async def fake(system, user, schema, temperature=0.4, model=None):
        calls.append(1)
        if len(calls) == 1:
            schema.model_validate({"why_wrong": [], "reteach": [], "twin": {}})  # raises
        return schema.model_validate(remedy_draft())

    monkeypatch.setattr(app.llm, "complete_json", fake)
    assert asyncio.run(app.llm.prep_json("s", "u", RemedyDraft)).twin.correct_index == 0
    assert len(calls) == 2
    with pytest.raises(ValidationError):
        RemedyDraft.model_validate({})


def test_review_meta_commentary_is_not_spoken(monkeypatch, tts):
    from app.prep.__main__ import make_remedy

    original = remedy_draft()
    reviewed = remedy_draft(why_wrong=[{"option": 1, "hint": "Lecture mein yeh nahi bataya gaya."}, {"option": 2, "hint": "Behtar hint."}])
    reviewed["twin"]["explanation"] = "Not in the transcript."

    async def fake(system, user, schema, temperature=0.4, model=None):
        if schema.__name__ == "RemedyReview":
            return schema.model_validate({"issues": ["x"], "remedy": reviewed})
        return schema.model_validate(original)

    monkeypatch.setattr(app.llm, "complete_json", fake)
    out = asyncio.run(make_remedy(outline().scenes[0], scene_draft(), None, "LECTURE"))
    hints = {h.option: h.hint for h in out.why_wrong}
    assert hints[1] == original["why_wrong"][0]["hint"]
    assert hints[2] == "Behtar hint."
    assert out.twin.explanation == original["twin"]["explanation"]
