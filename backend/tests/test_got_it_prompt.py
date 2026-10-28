"""The spoken "Did you get it?" prompt of a video doubt thread, voice replies to it, and recaps of threads asked on a
checkpoint card."""

import pytest

from app.config import settings
from app.prep.video import VIDEO_LINES
from app.prompts import VIDEO_GOT_IT_LINE
from app.routing import is_understood
from tests.test_video_api import ask_video, publish_video
from tests.test_video_threads import TEMPLATE_RECAP, answered, open_thread_with, reply
from tests.conftest import create_guest, create_session


@pytest.fixture
def session(client):
    lesson = publish_video()
    return create_session(client, create_guest(client), lesson.slug)


# --- is_understood ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Yeah, I got it",
        "got it",
        "continue",
        "Okay, let's continue.",
        "next",
        "haan",
        "yes",
        "samajh gaya",
        "haan samajh aa gaya",
        "clear hai",
        "aage chalo",
        "theek hai",
        "makes sense",
        "ok",
    ],
)
def test_is_understood_moves_on(text):
    assert is_understood(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "samajh nahi aaya",
        "no",
        "not really",
        "I didn't get it",
        "ok but why does it go up",
        "got it, what about the second ball",
        "haan lekin velocity zero kyun hai",
        "why is the acceleration constant",
        "can you explain the graph once more please",
        "phir se batao",
    ],
)
def test_is_understood_leaves_follow_ups_to_routing(text):
    assert is_understood(text) is False


# --- the spoken prompt --------------------------------------------------------------------


def test_answer_now_carries_the_spoken_got_it_prompt(client, llm, session):
    body = open_thread_with(client, llm, session["id"])
    assert body["thread"]["prompt"]["text"] == VIDEO_GOT_IT_LINE


def test_closed_thread_has_no_prompt(client, llm, session):
    llm.classify(intent="continue", question="let's go on")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["prompt"] is None


def test_got_it_line_is_pre_generated_in_prep():
    assert VIDEO_GOT_IT_LINE in VIDEO_LINES


def test_prompt_waits_ten_seconds():
    assert settings.video_thread_idle_s == 10


# --- voice replies to the prompt ----------------------------------------------------------


def test_spoken_got_it_moves_on_without_llm_or_ack(client, llm, stt, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    calls = len(llm.routing_calls)
    stt.text = "Yeah, I got it"
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["intent"] == "continue"
    assert body["thread"]["status"] == "resolved"
    assert body["ack"] is None  # straight to the recap or the video, no "Okay, let's continue."
    assert len(llm.routing_calls) == calls


def test_anything_else_after_the_prompt_is_a_follow_up(client, llm, stt, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    calls = len(llm.routing_calls)
    stt.text = "samajh nahi aaya, velocity zero kyun hai"
    llm.classify(intent="confused", question="velocity zero kyun hai")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert len(llm.routing_calls) == calls + 1
    assert body["thread_id"] == first["thread_id"]
    assert body["answer_when"] == "now" and body["turn"] == 1
    assert body["thread"]["status"] == "open"
    assert body["thread"]["prompt"]["text"] == VIDEO_GOT_IT_LINE


def test_continue_without_a_thread_keeps_its_ack(client, llm, session):
    llm.classify(intent="continue", question="let's go on")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["ack"] is not None


# --- recap of a thread asked on a checkpoint card ----------------------------------------


def card_thread(client, llm, session_id: str) -> dict:
    llm.classify(intent="confused", question="why B")
    first = ask_video(client, session_id, at_ms=200_000, checkpoint_idx=0, question_idx=0)
    assert first["thread"]["status"] == "open"
    return first


def test_long_card_thread_gets_a_recap(client, llm, session):
    first = card_thread(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="confused", question="and C?")
    second = ask_video(client, session["id"], at_ms=200_000, thread_id=first["thread_id"], checkpoint_idx=0, question_idx=0)
    answered(client, second)
    body = reply(client, first["thread_id"], True).json()
    assert body["status"] == "resolved"
    assert body["recap"]["text"]


def test_short_card_thread_gets_no_recap(client, llm, session):
    first = card_thread(client, llm, session["id"])
    answered(client, first)
    body = reply(client, first["thread_id"], True).json()
    assert body["recap"] is None


def test_card_recap_names_the_checkpoint_chapter(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    first = card_thread(client, llm, session["id"])
    answered(client, first)
    body = reply(client, first["thread_id"], True).json()
    # The fake LLM writes no recap text, so the template names the chapter the checkpoint is in
    assert body["recap"]["text"] in {TEMPLATE_RECAP, "Okay, back to Projectiles."}
