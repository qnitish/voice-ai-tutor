"""Per-lesson language: an English lesson's prompts, fixed lines, STT language, fillers and the student's choice."""

import json

import pytest

from app.config import settings
from app.db import SessionLocal
from app.language import current_stt_language
from app.lesson_store import publish_lesson
from app.prompts import (
    ANSWER_SYSTEM,
    BRIDGES,
    CHECK_FOLLOWUP,
    CHECK_LINES,
    CLASSIFY_SYSTEM,
    ENGLISH_LINES,
    ENGLISH_NOTE,
    FILLERS,
    FIXED_ACKS,
    SCENE_SYSTEM,
    SPEECH_RULES,
    SPEECH_RULES_EN,
    clarify_ack,
    for_gender,
    line_for,
    reveal_line,
)
from tests.conftest import answer, ask, create_guest, create_session, default_scenes, lesson_detail

SLUG = "english-lesson"


class LangSTT:
    """Records the language each transcribe() call ran under."""

    max_seconds = 30

    def __init__(self):
        self.languages: list[str | None] = []

    async def transcribe(self, wav: bytes) -> str:
        self.languages.append(current_stt_language.get())
        return "what is this"


@pytest.fixture
def lang_stt(client):
    import app.providers

    fake = LangSTT()
    app.providers.set_stt(fake)
    yield fake
    app.providers.set_stt(None)


@pytest.fixture
def english_session(client):
    with SessionLocal() as db:
        publish_lesson(db, SLUG, "Motion", "transcript text", default_scenes(), language="english")
        db.commit()
    return create_session(client, create_guest(client), SLUG)


# --- prompts and fixed lines ------------------------------------------------------------------------


@pytest.mark.parametrize("system", [SCENE_SYSTEM, CLASSIFY_SYSTEM, ANSWER_SYSTEM])
def test_english_prompts_swap_the_speech_rules(system):
    english = for_gender(system, "male", "english")
    assert SPEECH_RULES not in english and SPEECH_RULES_EN in english
    assert english.endswith(ENGLISH_NOTE)
    assert for_gender(system, "female", "hinglish") == for_gender(system, "female") == system


def test_every_fixed_line_has_an_english_form():
    for text in [*FIXED_ACKS.values(), *CHECK_LINES.values(), CHECK_FOLLOWUP, *BRIDGES, *FILLERS]:
        assert text in ENGLISH_LINES, text
        assert line_for(text, "male", "english") == ENGLISH_LINES[text]
        assert line_for(text, "female", "english") == ENGLISH_LINES[text]


def test_english_reveal_and_clarify_lines():
    assert reveal_line(1, "english") == "Hmm, that's not right. The correct answer is option B."
    assert clarify_ack("What is g", "english").startswith("Are you asking: What is g?")
    assert reveal_line(1) == "Hmm, yeh sahi nahi hai. Sahi jawab option B hai."


# --- live API ---------------------------------------------------------------------------------------


def test_lessons_report_their_language(client, lesson, english_session):
    assert lesson_detail(client, SLUG)["language"] == "english"
    assert lesson_detail(client)["language"] == "hinglish"
    langs = {l["slug"]: l["language"] for l in client.get("/api/lessons").json()}
    assert langs[SLUG] == "english" and langs[lesson.slug] == "hinglish"


def test_english_lesson_routes_and_acks_in_english(client, llm, lang_stt, english_session):
    llm.classify(intent="not_covered")
    res = ask(client, english_session["id"], 0, 0)
    assert lang_stt.languages == ["english"]
    assert res["ack"]["text"] == ENGLISH_LINES[FIXED_ACKS["not_covered"]]
    assert ENGLISH_NOTE in llm.routing_calls[-1]["system"]


def test_hinglish_lesson_keeps_default_stt_language(client, llm, lang_stt, lesson):
    session = create_session(client, create_guest(client), lesson.slug)
    llm.classify(intent="not_covered")
    ask(client, session["id"], 0, 0)
    assert lang_stt.languages == ["hinglish"]


def test_english_answer_prompt(client, llm, english_session):
    llm.classify(intent="confused")
    answer(client, ask(client, english_session["id"], 0, 0))
    assert ENGLISH_NOTE in llm.stream_calls[-1]["system"]


def test_english_check_feedback(client, english_session):
    detail = lesson_detail(client, SLUG)
    right = detail["scenes"][0]["check_question"]["correct_index"]
    r = client.post(f"/api/sessions/{english_session['id']}/check", json={"scene_idx": 0, "chosen_index": right})
    assert r.json()["feedback"]["text"] == ENGLISH_LINES[CHECK_LINES["correct"]]


def test_fillers_per_language(client):
    base = settings.media_dir / "fillers"
    (base / "english").mkdir(parents=True, exist_ok=True)
    (base / "english" / "fillers.json").write_text(json.dumps([{"text": "One second", "audio_url": "/x.wav", "duration_ms": 1}]))
    assert client.get("/api/fillers", params={"language": "english"}).json()[0]["text"] == "One second"


def test_student_language_can_be_changed(client):
    guest = create_guest(client)
    r = client.patch(f"/api/guests/{guest}", json={"language": "english"})
    assert r.status_code == 200 and r.json()["language"] == "english"
    assert client.get(f"/api/guests/{guest}").json()["language"] == "english"
    assert client.patch(f"/api/guests/{guest}", json={"language": "french"}).status_code == 422

