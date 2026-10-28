"""Promised (covered_later) video doubts: answered on the board after the target chapter, in a thread of their own."""

import pytest

from app.db import Doubt, DoubtThread, SessionLocal
from tests.conftest import answer, create_guest, create_session
from tests.test_video_api import DURATION_MS, ask_video, checkpoint, publish_video, question


@pytest.fixture
def session(client):
    lesson = publish_video()
    return create_session(client, create_guest(client), lesson.slug)


def promise(client, llm, session_id: str, target: int = 2) -> dict:
    llm.classify(intent="covered_later", question="what is relative velocity", target_scene_idx=target,
                 callback="You asked earlier about relative velocity, we just covered this.")
    body = ask_video(client, session_id, at_ms=90_000)
    assert body["answer_when"] == "target_chapter"
    return body


def deliver(client, doubt_id: str) -> dict:
    r = client.post(f"/api/doubts/{doubt_id}/deliver")
    assert r.status_code == 200, r.text
    return r.json()


def test_delivered_at_the_end_of_the_target_chapter(client, llm, session):
    body = promise(client, llm, session["id"])
    assert body["target_ms"] == 400_000  # the chapter's start: the player prepares the answer from here
    assert body["deliver_ms"] == DURATION_MS - 1500  # the last chapter ends with the video


def test_delivered_before_the_target_chapters_checkpoint(client, llm):
    lesson = publish_video(slug="promised-cp", checkpoints=[checkpoint(380_000, 1, [question("range")])])
    s = create_session(client, create_guest(client), lesson.slug)
    assert promise(client, llm, s["id"], target=1)["deliver_ms"] == 380_000


def test_other_intents_have_no_delivery_point(client, llm, session):
    llm.classify(intent="confused", question="why")
    assert ask_video(client, session["id"], at_ms=90_000)["deliver_ms"] is None


def test_delivery_opens_a_thread_with_the_doubt_as_its_answered_turn(client, llm, session):
    body = promise(client, llm, session["id"])
    thread = deliver(client, body["doubt_id"])
    assert thread["status"] == "open" and thread["turns"] == 1 and thread["id"] != body["thread_id"]
    with SessionLocal() as db:
        assert db.get(Doubt, body["doubt_id"]).thread_id == thread["id"]
        assert db.get(DoubtThread, thread["id"]).at_ms == DURATION_MS - 1500


def test_only_promised_doubts_are_delivered(client, llm, session):
    llm.classify(intent="confused", question="why")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert client.post(f"/api/doubts/{body['doubt_id']}/deliver").status_code == 400
    assert client.post("/api/doubts/nope/deliver").status_code == 404


def test_answer_is_written_for_the_end_of_the_target_chapter(client, llm, session):
    body = promise(client, llm, session["id"])
    answer(client, body["doubt_id"])
    prompt = llm.stream_calls[-1]["user"]
    assert "Answered later: the student asked this at 01:30" in prompt
    assert "we just covered this" in prompt  # the callback line, so the answer doesn't restate the doubt
    assert "Current chapter 2: Relative velocity" in prompt
    assert "Video paused at 09:58" in prompt
    assert "Chapter 1 [03:20-06:40, watched]" in prompt


def test_answer_prepared_before_delivery_counts_toward_the_delivery_thread(client, llm, session):
    body = promise(client, llm, session["id"])
    answer(client, body["doubt_id"])  # prefetched while the target chapter plays
    thread = deliver(client, body["doubt_id"])
    assert thread["audio_ms"] > 0
    with SessionLocal() as db:
        assert db.get(DoubtThread, body["thread_id"]).audio_ms == 0


def test_answer_prepared_after_delivery_counts_toward_the_delivery_thread(client, llm, session):
    body = promise(client, llm, session["id"])
    thread = deliver(client, body["doubt_id"])
    answer(client, body["doubt_id"])
    with SessionLocal() as db:
        assert db.get(DoubtThread, thread["id"]).audio_ms > 0


def test_follow_up_after_delivery_continues_that_thread(client, llm, session):
    body = promise(client, llm, session["id"])
    thread = deliver(client, body["doubt_id"])
    answer(client, body["doubt_id"])
    llm.classify(intent="confused", question="and with numbers?")
    follow = ask_video(client, session["id"], at_ms=DURATION_MS - 1500, thread_id=thread["id"])
    assert follow["thread_id"] == thread["id"] and follow["turn"] == 1
    assert follow["answer_when"] == "now" and follow["ack"] is None
