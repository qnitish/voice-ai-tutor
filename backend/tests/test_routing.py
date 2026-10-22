"""Spec section 5: intents/delivery, routing prompt, routing deployment, clarify, answers."""

import json

import pytest

from app.config import Settings, settings
from app.routing import is_affirmative
from app.schemas import Classification
from tests.conftest import answer, ask, create_guest, create_session, default_scenes, publish, scene, step


@pytest.fixture
def session(client, lesson):
    guest = create_guest(client)
    s = create_session(client, guest, lesson.slug)
    s["guest_id"] = guest
    return s


def last_routing_call(llm) -> dict:
    assert llm.routing_calls, "no routing LLM call was made"
    return llm.routing_calls[-1]


# --- Intents and delivery --------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,answer_when",
    [("not_covered", "scene_end"), ("prerequisite", "now"), ("confused", "now"), ("repeat", "replay"), ("unclear", "none")],
)
def test_intent_maps_to_answer_when(client, llm, session, intent, answer_when):
    llm.classify(intent=intent, question="sawal")
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert body["intent"] == intent
    assert body["answer_when"] == answer_when
    assert body["question"] == "sawal"


def test_covered_later_with_valid_target_gives_target_scene(client, llm, session):
    llm.classify(intent="covered_later", question="compass kaise banta hai", target_scene_idx=2, callback="Tumne pucha tha compass ke baare mein.")
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == "covered_later"
    assert body["answer_when"] == "target_scene"
    assert body["target_scene_idx"] == 2
    assert body["callback"]["text"] == "Tumne pucha tha compass ke baare mein."
    assert set(body["callback"].keys()) == {"text", "audio_url", "duration_ms"}


@pytest.mark.parametrize("target", [0, 1, 3, 99, None])
def test_covered_later_with_target_not_strictly_ahead_or_outside_lesson_becomes_not_covered(client, llm, session, target):
    llm.classify(intent="covered_later", question="sawal", target_scene_idx=target, callback="Baad mein.")
    body = ask(client, session["id"], scene_idx=1, step_idx=0)
    assert body["intent"] == "not_covered"
    assert body["answer_when"] == "scene_end"
    assert body["target_scene_idx"] is None
    assert body["callback"] is None


def test_covered_later_without_callback_becomes_not_covered(client, llm, session):
    llm.classify(intent="covered_later", question="sawal", target_scene_idx=2, callback=None)
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == "not_covered"
    assert body["answer_when"] == "scene_end"


@pytest.mark.parametrize("intent", ["not_covered", "prerequisite", "confused", "repeat", "unclear"])
def test_target_and_callback_are_null_for_other_intents(client, llm, session, intent):
    llm.classify(intent=intent, question="sawal", target_scene_idx=2, callback="Baad mein.")
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["target_scene_idx"] is None
    assert body["callback"] is None


@pytest.mark.parametrize("intent", ["not_covered", "prerequisite", "confused", "repeat", "unclear"])
def test_fixed_ack_is_non_empty(client, llm, session, intent):
    llm.classify(intent=intent, question="sawal")
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["ack"]["text"].strip() != ""


def test_fixed_ack_for_covered_later(client, llm, session):
    llm.classify(intent="covered_later", question="sawal", target_scene_idx=2, callback="Baad mein.")
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["ack"]["text"].strip() != ""


def test_fixed_acks_do_not_use_the_llm(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert len(llm.calls) == 1  # the routing call only
    assert len(llm.stream_calls) == 0


# --- Classification output / routing call ---------------------------------------------


def test_classification_confidence_defaults_to_1():
    c = Classification(intent="not_covered", question="sawal")
    assert c.confidence == 1.0


def test_classification_accepts_confidence():
    assert Classification(intent="confused", question="sawal", confidence=0.4).confidence == pytest.approx(0.4)


def test_routing_call_uses_classification_schema(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema"] is Classification


def test_routing_call_passes_routing_deployment(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert last_routing_call(llm)["model"] == settings.routing_deployment


def test_routing_call_uses_dedicated_routing_deployment_when_configured(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "azure_openai_routing_deployment", "fast-router")
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert last_routing_call(llm)["model"] == "fast-router"


# --- Routing deployment settings ------------------------------------------------------


def test_routing_deployment_falls_back_to_main_deployment():
    s = Settings(azure_openai_deployment="main-model", azure_openai_routing_deployment=None)
    assert s.routing_deployment == "main-model"


def test_routing_deployment_uses_dedicated_setting():
    s = Settings(azure_openai_deployment="main-model", azure_openai_routing_deployment="fast-model")
    assert s.routing_deployment == "fast-model"


def test_routing_deployment_read_from_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ROUTING_DEPLOYMENT", "env-router")
    assert Settings().azure_openai_routing_deployment == "env-router"
    assert Settings().routing_deployment == "env-router"


def test_routing_deployment_default_is_none(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ROUTING_DEPLOYMENT", raising=False)
    assert Settings(azure_openai_routing_deployment=None).azure_openai_routing_deployment is None


# --- Routing prompt --------------------------------------------------------------------


def test_prompt_contains_lesson_vocabulary_line(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    user = last_routing_call(llm)["user"]
    # key terms: ["magnet", "pole"], ["pole", "attraction"], ["compass"] -> distinct, scene order, first kept
    assert "Lesson vocabulary: magnet, pole, attraction, compass" in user


def test_prompt_contains_every_scene_title_and_goal(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=1, step_idx=0)
    user = last_routing_call(llm)["user"]
    for s in default_scenes():
        assert s["title"] in user
        assert s["goal"] in user


def test_prompt_contains_current_scene_narration_up_to_current_step(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=1, step_idx=1)
    user = last_routing_call(llm)["user"]
    assert "Scene one step zero: north aur south." in user
    assert "Scene one step one: like poles repel." in user


def test_prompt_contains_only_first_step_when_at_step_zero(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=1, step_idx=0)
    user = last_routing_call(llm)["user"]
    assert "Scene one step zero: north aur south." in user


def test_prompt_excludes_other_scenes_narration(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    ask(client, session["id"], scene_idx=1, step_idx=2)
    user = last_routing_call(llm)["user"]
    assert "Scene zero step zero" not in user
    assert "Scene zero step one" not in user
    assert "Scene two step zero" not in user
    assert "Scene two step one" not in user


def test_prompt_contains_student_transcript(client, llm, stt, session):
    stt.text = "yeh needle kyun ghoomti hai"
    llm.classify(intent="not_covered", question="Needle kyun ghoomti hai?")
    ask(client, session["id"], scene_idx=2, step_idx=0)
    assert "yeh needle kyun ghoomti hai" in last_routing_call(llm)["user"]


def test_prompt_contains_up_to_five_most_recent_earlier_doubts_oldest_first(client, llm, session):
    questions = [f"earlier-question-{i}" for i in range(7)]
    for q in questions:
        llm.classify(intent="not_covered", question=q)
        ask(client, session["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="not_covered", question="final-question")
    ask(client, session["id"], scene_idx=0, step_idx=1)
    user = last_routing_call(llm)["user"]
    recent = questions[2:]
    for q in recent:
        assert q in user
    assert "earlier-question-0" not in user
    assert "earlier-question-1" not in user
    positions = [user.index(q) for q in recent]
    assert positions == sorted(positions)


def test_prompt_excludes_doubts_of_other_sessions(client, llm, lesson, session):
    other_guest = create_guest(client, "Other")
    other = create_session(client, other_guest, lesson.slug)
    llm.classify(intent="not_covered", question="other-session-question")
    ask(client, other["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="not_covered", question="mine")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert "other-session-question" not in last_routing_call(llm)["user"]


def test_prompt_excludes_doubts_of_same_guests_other_session(client, llm, lesson, session):
    other = create_session(client, session["guest_id"], lesson.slug)
    llm.classify(intent="not_covered", question="sibling-session-question")
    ask(client, other["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="not_covered", question="mine")
    ask(client, session["id"], scene_idx=0, step_idx=0)
    assert "sibling-session-question" not in last_routing_call(llm)["user"]


def test_prompt_vocabulary_for_custom_lesson(client, llm):
    publish(
        "order-01",
        scenes=[
            scene("A", "goal a", ["zeta", "alpha"], [step("A.")], None),
            scene("B", "goal b", ["alpha", "beta", "zeta"], [step("B.")], None),
        ],
    )
    guest = create_guest(client)
    s = create_session(client, guest, "order-01")
    llm.classify(intent="not_covered", question="sawal")
    ask(client, s["id"], scene_idx=0, step_idx=0)
    assert "Lesson vocabulary: zeta, alpha, beta" in last_routing_call(llm)["user"]


# --- Ask back when unsure (clarify) ---------------------------------------------------


@pytest.mark.parametrize("intent", ["covered_later", "not_covered", "prerequisite", "confused"])
def test_low_confidence_becomes_clarify(client, llm, session, intent):
    llm.classify(intent=intent, question="Kya compass needle north dikhati hai?", target_scene_idx=2, callback="Baad mein.", confidence=0.59)
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == "clarify"
    assert body["answer_when"] == "none"
    assert body["target_scene_idx"] is None
    assert body["callback"] is None
    assert "Kya compass needle north dikhati hai?" in body["ack"]["text"]


def test_clarify_ack_is_longer_than_the_question_alone(client, llm, session):
    llm.classify(intent="not_covered", question="Compass needle north dikhati hai", confidence=0.2)
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == "clarify"
    assert len(body["ack"]["text"]) > len("Compass needle north dikhati hai")


def test_confidence_at_threshold_is_not_clarify(client, llm, session):
    llm.classify(intent="not_covered", question="sawal", confidence=0.6)
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == "not_covered"
    assert body["answer_when"] == "scene_end"


@pytest.mark.parametrize("intent,answer_when", [("repeat", "replay"), ("unclear", "none")])
def test_low_confidence_other_intents_are_not_clarify(client, llm, session, intent, answer_when):
    llm.classify(intent=intent, question="sawal", confidence=0.1)
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert body["intent"] == intent
    assert body["answer_when"] == answer_when


def test_clarify_doubt_is_stored_in_history(client, llm, session):
    llm.classify(intent="confused", question="sawal", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    entry = client.get(f"/api/guests/{session['guest_id']}/history").json()[0]
    assert [d["intent"] for d in entry["doubts"]] == ["clarify"]


def test_affirmative_after_clarify_skips_llm_and_uses_original_classification(client, llm, stt, session):
    llm.classify(intent="prerequisite", question="Magnet ka matlab kya hai?", confidence=0.4)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    calls_before = len(llm.calls)
    stt.text = "haan"
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert len(llm.calls) == calls_before
    assert body["intent"] == "prerequisite"
    assert body["answer_when"] == "now"
    assert body["question"] == "Magnet ka matlab kya hai?"
    assert body["ack"]["text"].strip() != ""


def test_affirmative_after_clarify_restores_covered_later_target_and_callback(client, llm, stt, session):
    llm.classify(intent="covered_later", question="Compass kaise banta hai?", target_scene_idx=2, callback="Tumne pucha tha compass.", confidence=0.5)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    stt.text = "haan ji"
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert body["intent"] == "covered_later"
    assert body["answer_when"] == "target_scene"
    assert body["target_scene_idx"] == 2
    assert body["callback"]["text"] == "Tumne pucha tha compass."
    assert body["question"] == "Compass kaise banta hai?"


def test_affirmative_after_clarify_still_validates_covered_later_target(client, llm, stt, session):
    llm.classify(intent="covered_later", question="Compass kaise banta hai?", target_scene_idx=2, callback="Baad mein.", confidence=0.5)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    stt.text = "haan"
    body = ask(client, session["id"], scene_idx=2, step_idx=0)  # the target is no longer ahead
    assert body["intent"] == "not_covered"
    assert body["answer_when"] == "scene_end"
    assert body["target_scene_idx"] is None
    assert body["callback"] is None


def test_clarify_is_resolved_after_affirmative_follow_up(client, llm, stt, session):
    llm.classify(intent="confused", question="sawal", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    stt.text = "haan"
    ask(client, session["id"], scene_idx=0, step_idx=0)
    calls_before = len(llm.calls)
    llm.classify(intent="not_covered", question="naya sawal")
    stt.text = "haan"
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert len(llm.calls) == calls_before + 1
    assert body["intent"] == "not_covered"
    assert body["question"] == "naya sawal"


def test_non_affirmative_after_clarify_is_classified_normally(client, llm, stt, session):
    llm.classify(intent="confused", question="pehla", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    calls_before = len(llm.calls)
    llm.classify(intent="not_covered", question="bilkul alag sawal")
    stt.text = "nahi, main pole ke baare mein pooch raha tha"
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert len(llm.calls) == calls_before + 1
    assert body["intent"] == "not_covered"
    assert body["question"] == "bilkul alag sawal"


def test_clarify_is_resolved_after_non_affirmative_follow_up(client, llm, stt, session):
    llm.classify(intent="confused", question="pehla", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    llm.classify(intent="not_covered", question="doosra")
    stt.text = "nahi kuch aur"
    ask(client, session["id"], scene_idx=0, step_idx=1)
    calls_before = len(llm.calls)
    llm.classify(intent="repeat", question="teesra")
    stt.text = "haan"
    body = ask(client, session["id"], scene_idx=0, step_idx=1)
    assert len(llm.calls) == calls_before + 1
    assert body["intent"] == "repeat"


def test_clarify_does_not_leak_into_another_session(client, llm, stt, lesson, session):
    llm.classify(intent="confused", question="pehla", confidence=0.3)
    ask(client, session["id"], scene_idx=0, step_idx=0)
    other = create_session(client, session["guest_id"], lesson.slug)
    calls_before = len(llm.calls)
    llm.classify(intent="not_covered", question="doosre session ka")
    stt.text = "haan"
    body = ask(client, other["id"], scene_idx=0, step_idx=0)
    assert len(llm.calls) == calls_before + 1
    assert body["intent"] == "not_covered"


def test_affirmative_without_pending_clarify_is_classified_normally(client, llm, stt, session):
    llm.classify(intent="unclear", question="haan")
    stt.text = "haan"
    body = ask(client, session["id"], scene_idx=0, step_idx=0)
    assert len(llm.calls) == 1
    assert body["intent"] == "unclear"


# --- is_affirmative --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["haan", "haa", "ha", "han", "haanji", "hanji", "ji", "yes", "yeah", "yep", "sahi", "bilkul", "हाँ", "हां", "हा", "जी"],
)
def test_is_affirmative_single_words(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["Haan", "YES", "Haan.", "haan!", "haan?", "haan।", '"haan"', "'ji'", "  haan  "])
def test_is_affirmative_ignores_case_punctuation_and_whitespace(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["haan ji", "haan yahi wala", "yes that is it", "bilkul sahi wahi wala"])
def test_is_affirmative_up_to_four_words(text):
    assert is_affirmative(text) is True


def test_is_affirmative_false_for_five_or_more_words():
    assert is_affirmative("haan lekin mujhe kuch aur poochna") is False
    assert is_affirmative("haan yes ji bilkul sahi") is False


@pytest.mark.parametrize("text", ["nahi", "nahi haan", "no", "kya", "magnet kya hai", "haanyes"])
def test_is_affirmative_false_when_first_word_is_not_affirmative(text):
    assert is_affirmative(text) is False


@pytest.mark.parametrize("text", ["", "   ", "...", "?!"])
def test_is_affirmative_false_for_empty_text(text):
    assert is_affirmative(text) is False


# --- Answers ---------------------------------------------------------------------------


def parsed_lines(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def test_answer_streams_ndjson_lines_from_the_llm(client, llm, session):
    llm.answer_lines = ['{"say": "Pehli line.", "draw": []}', '{"say": "Doosri line.", "draw": []}']
    llm.classify(intent="not_covered", question="sawal")
    doubt = ask(client, session["id"], scene_idx=0, step_idx=0)
    lines = parsed_lines(answer(client, doubt))
    says = [line["say"] for line in lines if "say" in line]
    assert says[:2] == ["Pehli line.", "Doosri line."]


@pytest.mark.parametrize("intent", ["prerequisite", "confused", "not_covered"])
def test_answer_prompt_includes_intent_name(client, llm, session, intent):
    llm.classify(intent=intent, question="sawal")
    doubt = ask(client, session["id"], scene_idx=0, step_idx=0)
    answer(client, doubt)
    assert len(llm.stream_calls) == 1
    prompt = llm.stream_calls[0]["system"] + "\n" + llm.stream_calls[0]["user"]
    assert intent in prompt


def test_doubt_is_unanswered_before_stream_and_answered_after(client, llm, session):
    llm.classify(intent="not_covered", question="sawal")
    doubt = ask(client, session["id"], scene_idx=0, step_idx=0)
    history_url = f"/api/guests/{session['guest_id']}/history"
    assert client.get(history_url).json()[0]["doubts"][0]["answered"] is False
    answer(client, doubt)
    assert client.get(history_url).json()[0]["doubts"][0]["answered"] is True

