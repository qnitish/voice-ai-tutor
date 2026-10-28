"""Follow-ups asked while a lesson doubt is being answered: answered right away, below that answer."""

import pytest

from tests.conftest import QUESTION_WAV, answer, ask, create_guest, create_session


@pytest.fixture
def session(client, lesson):
    return create_session(client, create_guest(client), lesson.slug)


def ask_follow_up(client, session_id: str, parent_id: str, scene_idx: int = 0, step_idx: int = 0) -> dict:
    r = client.post(
        f"/api/sessions/{session_id}/questions",
        files={"audio": ("question.wav", QUESTION_WAV, "audio/wav")},
        data={"scene_idx": str(scene_idx), "step_idx": str(step_idx), "follow_up_of": parent_id},
    )
    assert r.status_code == 200, r.text
    return r.json()


def first_doubt(client, llm, session) -> dict:
    llm.classify(intent="confused", question="magnetic field kya hai")
    llm.answer_lines = ['{"say": "Field ek invisible area hai.", "draw": []}']
    doubt = ask(client, session["id"], scene_idx=1, step_idx=1)
    answer(client, doubt)
    return doubt


@pytest.mark.parametrize("intent", ["confused", "prerequisite", "not_covered", "covered_later"])
def test_answerable_follow_up_is_answered_now_without_ack(client, llm, session, intent):
    parent = first_doubt(client, llm, session)
    llm.classify(intent=intent, question="field invisible kyun hai", target_scene_idx=2, callback="Baad mein.")
    body = ask_follow_up(client, session["id"], parent["doubt_id"], scene_idx=2, step_idx=0)
    assert body["answer_when"] == "now"
    assert body["ack"] is None
    assert body["target_scene_idx"] is None
    assert body["callback"] is None


@pytest.mark.parametrize("intent,answer_when", [("off_topic", "none"), ("unclear", "none"), ("repeat", "replay")])
def test_other_follow_ups_keep_their_delivery_and_ack(client, llm, session, intent, answer_when):
    parent = first_doubt(client, llm, session)
    llm.classify(intent=intent, question="sawal", ack="Chalo, padhai pe dhyan dein.")
    body = ask_follow_up(client, session["id"], parent["doubt_id"])
    assert body["answer_when"] == answer_when
    assert body["ack"]["text"].strip()


def test_routing_and_answer_prompts_see_the_answer_on_the_board(client, llm, session):
    parent = first_doubt(client, llm, session)
    llm.classify(intent="confused", question="invisible matlab?")
    body = ask_follow_up(client, session["id"], parent["doubt_id"])
    routing = llm.routing_calls[-1]["user"]
    assert "FOLLOW-UP" in routing and "Field ek invisible area hai." in routing
    answer(client, body)
    prompt = llm.stream_calls[-1]["user"]
    assert "FOLLOW-UP to your answer" in prompt and "Field ek invisible area hai." in prompt
    # Answered where the first doubt was asked, not where the player said
    assert "Current scene 1:" in prompt


def test_follow_up_of_another_session_is_ignored(client, llm, lesson, session):
    other = create_session(client, create_guest(client, "Ravi"), lesson.slug)
    parent = first_doubt(client, llm, other)
    llm.classify(intent="not_covered", question="sawal")
    body = ask_follow_up(client, session["id"], parent["doubt_id"])
    assert body["answer_when"] == "scene_end"
    assert body["ack"] is not None


# --- Video lessons: follow-ups inside an open thread ----------------------------------


@pytest.fixture
def video_session(client):
    from tests.test_video_api import publish_video

    lesson = publish_video()
    return create_session(client, create_guest(client), lesson.slug)


@pytest.mark.parametrize("intent", ["confused", "not_covered", "prerequisite"])
def test_video_follow_up_answered_now_has_no_ack(client, llm, video_session, intent):
    from tests.test_video_api import ask_video
    from tests.test_video_threads import open_thread_with

    first = open_thread_with(client, llm, video_session["id"])
    assert first["ack"] is not None  # the first doubt of a pause keeps its ack
    llm.classify(intent=intent, question="with numbers?")
    body = ask_video(client, video_session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "now" and body["turn"] == 1
    assert body["ack"] is None


def test_video_follow_up_off_topic_keeps_its_ack(client, llm, video_session):
    from tests.test_video_api import ask_video
    from tests.test_video_threads import open_thread_with

    first = open_thread_with(client, llm, video_session["id"])
    llm.classify(intent="off_topic", question="cricket?", ack="Let's stay with the lecture.")
    body = ask_video(client, video_session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["ack"]["text"].strip()
