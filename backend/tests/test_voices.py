"""Spec section 10: per-lesson tutor voice (stock or cloned) and voice gender."""

import json

import pytest

from app.audio import speak, speak_cached
from app.config import settings
from app.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, FEMALE_RULE, FIXED_ACKS, MALE_RULE, SCENE_SYSTEM, for_gender, line_for
from app.voices import DEFAULT, current_voice, get_voice, voice_path
from app.db import SessionLocal
from app.lesson_store import publish_lesson
from tests.conftest import answer, ask, create_guest, create_session, default_scenes, finish, lesson_detail

MALE = "test-male"


class VoiceTTS:
    """Records which voice each synthesize() call ran under."""

    def __init__(self, wav: bytes):
        self.wav = wav
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(self, text: str) -> bytes:
        v = current_voice.get()
        self.calls.append((text, v.name if v else None))
        return self.wav


@pytest.fixture(autouse=True)
def male_voice():
    path = voice_path(MALE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": MALE, "gender": "male", "embedding": {"embedding": "x", "shape": [1, 768], "dtype": "torch.bfloat16"}}))
    yield


@pytest.fixture
def vtts(client):
    import app.providers
    from tests.conftest import TTS_WAV

    fake = VoiceTTS(TTS_WAV)
    app.providers.set_tts(fake)
    yield fake
    app.providers.set_tts(None)


@pytest.fixture
def male_lesson(client):
    with SessionLocal() as db:
        lesson = publish_lesson(db, "voice-clone-magnetism", "Magnetism", "transcript text", default_scenes(), voice=MALE)
        db.commit()
        return lesson


@pytest.fixture
def male_session(client, male_lesson):
    return create_session(client, create_guest(client), male_lesson.slug)


# --- voices ---------------------------------------------------------------------------------


def test_default_voice_is_the_configured_stock_voice():
    v = get_voice(None)
    assert (v.name, v.stock, v.gender) == (DEFAULT, settings.gnani_tts_voice, settings.tutor_gender)
    assert get_voice(DEFAULT) == v


def test_saved_voice_loads():
    v = get_voice(MALE)
    assert v.gender == "male"
    assert v.embedding["shape"] == [1, 768]


@pytest.mark.parametrize("name", ["../etc", "A", "a/b", "", "x" * 61])
def test_voice_names_are_checked(name):
    with pytest.raises(ValueError):
        voice_path(name)


# --- prompts ----------------------------------------------------------------------------------


def test_prompts_are_written_for_the_female_default():
    for system in (SCENE_SYSTEM, CLASSIFY_SYSTEM, ANSWER_SYSTEM):
        assert FEMALE_RULE in system
        assert for_gender(system, "female") == system


def test_male_prompts_swap_the_verb_rule():
    for system in (SCENE_SYSTEM, CLASSIFY_SYSTEM, ANSWER_SYSTEM):
        male = for_gender(system, "male")
        assert MALE_RULE in male
        assert FEMALE_RULE not in male
    assert "what he draws" in for_gender(SCENE_SYSTEM, "male")


def test_fixed_lines_follow_gender():
    female = FIXED_ACKS["confused"]
    assert line_for(female, "female") == female
    assert line_for(female, "male").endswith("samjhata hoon.")
    assert line_for("Bilkul sahi! Shabaash.", "male") == "Bilkul sahi! Shabaash."


# --- speaking -----------------------------------------------------------------------------------


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_speak_synthesizes_in_the_given_voice(vtts):
    run(speak("Namaste.", "test/a.wav", MALE))
    run(speak("Namaste.", "test/b.wav"))
    assert vtts.calls == [("Namaste.", MALE), ("Namaste.", DEFAULT)]
    assert current_voice.get() is None


def test_speak_cached_keeps_one_cache_per_voice(vtts):
    a = run(speak_cached("Ek line.", MALE))
    b = run(speak_cached("Ek line."))
    assert a.audio_url.startswith(f"/media/cache/{MALE}/")
    assert b.audio_url.startswith("/media/cache/") and f"/{MALE}/" not in b.audio_url
    run(speak_cached("Ek line.", MALE))
    assert len(vtts.calls) == 2


# --- lessons ----------------------------------------------------------------------------------


def test_lesson_detail_reports_voice(client, lesson, male_lesson):
    default = lesson_detail(client, lesson.slug)
    male = lesson_detail(client, male_lesson.slug)
    assert (default["voice"], default["voice_gender"]) == (None, "female")
    assert (male["voice"], male["voice_gender"]) == (MALE, "male")


def write_manifest(folder, name: str, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps([{"text": text, "audio_url": "/media/x.wav", "duration_ms": 1}]))


def test_fillers_and_bridges_per_voice(client):
    base = settings.media_dir / "fillers"
    write_manifest(base, "fillers.json", "default filler")
    write_manifest(base / MALE, "fillers.json", "male filler")
    write_manifest(base / MALE, "bridges.json", "male bridge")
    assert client.get("/api/fillers").json()[0]["text"] == "default filler"
    assert client.get("/api/fillers", params={"voice": MALE}).json()[0]["text"] == "male filler"
    assert client.get("/api/bridges", params={"voice": MALE}).json()[0]["text"] == "male bridge"


@pytest.mark.parametrize("voice", ["no-such-voice", "../..", "BAD"])
def test_fillers_for_unknown_voice_are_empty(client, voice):
    assert client.get("/api/fillers", params={"voice": voice}).json() == []


# --- live speech uses the lesson's voice ---------------------------------------------------------


def test_doubt_ack_uses_lesson_voice_and_male_line(client, llm, vtts, male_session):
    llm.classify(intent="not_covered")
    res = ask(client, male_session["id"], 0, 0)
    assert res["ack"]["text"] == line_for(FIXED_ACKS["not_covered"], "male")
    assert res["ack"]["text"] != FIXED_ACKS["not_covered"]
    assert (res["ack"]["text"], MALE) in vtts.calls
    assert MALE_RULE in llm.routing_calls[-1]["system"]


def test_default_lesson_keeps_female_lines(client, llm, vtts, lesson):
    session = create_session(client, create_guest(client), lesson.slug)
    llm.classify(intent="not_covered")
    res = ask(client, session["id"], 0, 0)
    assert res["ack"]["text"] == FIXED_ACKS["not_covered"]
    assert FEMALE_RULE in llm.routing_calls[-1]["system"]


def test_answer_is_spoken_in_lesson_voice(client, llm, vtts, male_session):
    llm.classify(intent="confused")
    res = ask(client, male_session["id"], 0, 0)
    answer(client, res["doubt_id"])
    assert MALE_RULE in llm.stream_calls[-1]["system"]
    assert ("Yeh answer hai.", MALE) in vtts.calls


def test_check_feedback_and_summary_use_lesson_voice(client, llm, vtts, male_session):
    ci = lesson_detail(client, "voice-clone-magnetism")["scenes"][0]["check_question"]["correct_index"]
    fb = client.post(f"/api/sessions/{male_session['id']}/check", json={"scene_idx": 0, "chosen_index": ci}).json()
    assert fb["feedback"]["audio_url"].startswith(f"/media/cache/{MALE}/")
    finish(client, male_session["id"])
    summary = next(c for c in llm.calls if c["schema"].__name__ == "SummaryDraft")
    assert MALE_RULE in summary["system"]
