"""Doubt handling after the 2026-10-27 session review: replies to the tutor, the student's own words, the lecture
outline and passages after the pause in the answer prompt."""

import pytest

from app.api.video_doubts import render_marked
from app.config import settings
from app.context.base import Passage
from app.db import Doubt, SessionLocal
from tests.conftest import create_guest, create_session
from tests.test_video_api import ask_video, publish_video
from tests.test_video_threads import answered, open_thread_with


@pytest.fixture
def session(client):
    lesson = publish_video()
    return create_session(client, create_guest(client), lesson.slug)


def stored(doubt_id: str) -> Doubt:
    with SessionLocal() as db:
        return db.get(Doubt, doubt_id)


def test_a_reply_to_the_tutor_is_answered_without_using_a_turn(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="reply", question="One-dimensional is simpler.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "now"
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 1


def test_a_reply_is_answered_even_at_the_follow_up_cap(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="reply", question="I already know that.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "now"
    assert body["thread"]["status"] == "open"


def test_overview_is_answered_now(client, llm, session):
    llm.classify(intent="overview", question="What topics are in this video?")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["answer_when"] == "now"


def test_what_stt_heard_is_stored_next_to_the_rewrite(client, llm, stt, session):
    stt.text = "1d is easy for me"
    llm.classify(intent="confused", question="Why is one-dimensional motion easier?")
    body = ask_video(client, session["id"], at_ms=90_000)
    d = stored(body["doubt_id"])
    assert d.said == "1d is easy for me"
    assert d.question == "Why is one-dimensional motion easier?"


@pytest.mark.parametrize("meta", ["The student's doubt is unclear or not understandable.", "", "   "])
def test_a_question_describing_the_student_is_replaced_by_what_was_heard(client, llm, stt, session, meta):
    stt.text = "what is this formula"
    llm.classify(intent="confused", question=meta)
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["question"] == "what is this formula"


def test_answer_prompt_has_the_raw_words_the_turn_the_outline_and_the_tutors_last_line(client, llm, stt, session):
    first = open_thread_with(client, llm, session["id"], question="why does the packet go up")
    llm.answer_lines = ['{"say": "Which one is simpler, one or two dimensions?", "draw": []}']
    answered(client, first)
    stt.text = "one d is simpler"
    llm.classify(intent="reply", question="One-dimensional motion is simpler.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, body)
    prompt = llm.stream_calls[-1]["user"]
    assert 'Student said (speech-to-text): "one d is simpler"' in prompt
    assert "Turn in this pause: 1" in prompt
    assert "Lecture chapters:" in prompt and "Relative velocity" in prompt and "not watched yet" in prompt
    assert "Tutor's last line to the student: \"Which one is simpler, one or two dimensions?\"" in prompt


def test_passages_after_the_pause_are_marked():
    out = render_marked(
        [
            Passage(source="transcript", ref="01:00–01:30", text="before", score=3.0),
            Passage(source="transcript", ref="02:00–02:30", text="after", score=1.0),
            Passage(source="documents", ref="book p.4", text="book", score=1.0),
        ],
        at_ms=90_000,
    )
    before, after, book = out.split("\n\n")
    assert before == "[transcript 01:00–01:30]\nbefore"
    assert after == "[transcript 02:00–02:30]\n(after the pause: the student has not watched this yet)\nafter"
    assert book == "[documents book p.4]\nbook"
