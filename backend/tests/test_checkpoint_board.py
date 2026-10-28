"""Checkpoint questions on the board: spoken explanations, doubts before answering, step-by-step solutions."""

import asyncio

import pytest

import app.llm
from app.prep import solutions
from app.prompts import VIDEO_ACKS, VIDEO_SOLUTION_LATER
from app.schemas import StepDraft
from tests.conftest import answer, create_guest, create_session
from tests.test_video_api import ask_video, checkpoint, publish_video, question

STEPS = [
    {"say": "We know the initial velocity.", "draw": [], "audio_url": "/media/test/s0.wav", "duration_ms": 900},
    {"say": "So the answer is Beta.", "draw": [], "audio_url": "/media/test/s1.wav", "duration_ms": 900},
]


def lesson_with(q: dict):
    return publish_video(slug="board-cp", checkpoints=[checkpoint(200_000, 0, [q])])


@pytest.fixture
def session(client):
    lesson_with(question("free fall"))  # correct option: 1 (Beta)
    return create_session(client, create_guest(client), "board-cp")


def pick(client, session_id: str, chosen: int, kind: str = "first") -> dict:
    r = client.post(f"/api/sessions/{session_id}/checkpoints/0/answer", json={"question_idx": 0, "chosen_index": chosen, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def ask_at_question(client, session_id: str, thread_id: str | None = None) -> dict:
    return ask_video(client, session_id, at_ms=200_000, thread_id=thread_id, checkpoint_idx=0, question_idx=0)


def solution_button(client, session_id: str) -> dict:
    r = client.post(f"/api/sessions/{session_id}/checkpoints/0/solution", json={"question_idx": 0})
    assert r.status_code == 200, r.text
    return r.json()


# --- spoken explanation ------------------------------------------------------------------


def test_right_answer_speaks_the_explanation(client, session):
    body = pick(client, session["id"], 1)
    assert body["correct"] and body["explanation_spoken"]["text"] == "Explanation about free fall."


def test_wrong_first_pick_with_a_hint_keeps_the_explanation_for_later(client, session):
    assert pick(client, session["id"], 0)["explanation_spoken"] is None
    assert pick(client, session["id"], 2, kind="retry")["explanation_spoken"]["text"] == "Explanation about free fall."


def test_prepared_explanation_audio_is_used(client):
    q = question("free fall") | {"explanation_audio": {"text": "Prepared.", "audio_url": "/media/test/e.wav", "duration_ms": 700}}
    lesson_with(q)
    s = create_session(client, create_guest(client), "board-cp")
    assert pick(client, s["id"], 1)["explanation_spoken"]["audio_url"] == "/media/test/e.wav"


# --- doubts before answering: hints, not the answer ------------------------------------------


def test_doubt_before_answering_hides_the_correct_option(client, llm, session):
    llm.classify(intent="confused", question="what does initial velocity mean here")
    body = ask_at_question(client, session["id"])
    routing = llm.routing_calls[-1]["user"]
    assert "not answered yet" in routing and "correct:" not in routing
    answer(client, body)
    prompt = llm.stream_calls[-1]["user"]
    assert "not answered yet" in prompt and "B. Beta" in prompt
    assert "Correct option" not in prompt and "Explanation about free fall." not in prompt


def test_doubt_after_answering_sees_the_correct_option(client, llm, session):
    pick(client, session["id"], 1)
    llm.classify(intent="confused", question="why beta")
    answer(client, ask_at_question(client, session["id"]))
    assert "Correct option: Beta" in llm.stream_calls[-1]["user"]


# --- solution ------------------------------------------------------------------------------


def test_solution_before_answering_is_not_given(client, llm, session):
    llm.classify(intent="solution", question="solve it")
    body = ask_at_question(client, session["id"])
    assert body["intent"] == "solution" and body["answer_when"] == "none"
    assert body["ack"]["text"] == VIDEO_SOLUTION_LATER


def test_prepared_solution_is_played_without_an_llm_call(client, llm):
    lesson_with(question("free fall") | {"solution": STEPS})
    s = create_session(client, create_guest(client), "board-cp")
    pick(client, s["id"], 1)
    llm.classify(intent="solution", question="solve it step by step")
    body = ask_at_question(client, s["id"])
    assert body["answer_when"] == "now" and body["ack"]["text"] == VIDEO_ACKS["solution"]
    text = answer(client, body)
    assert "So the answer is Beta." in text and llm.stream_calls == []


def test_solution_without_a_prepared_one_is_written_live(client, llm, session):
    pick(client, session["id"], 1)
    llm.classify(intent="solution", question="solve it")
    answer(client, ask_at_question(client, session["id"]))
    assert len(llm.stream_calls) == 1 and "Doubt intent: solution" in llm.stream_calls[-1]["user"]


def test_solution_button_routes_like_a_spoken_request(client, llm):
    lesson_with(question("free fall") | {"solution": STEPS})
    s = create_session(client, create_guest(client), "board-cp")
    assert solution_button(client, s["id"])["answer_when"] == "none"  # not answered yet
    pick(client, s["id"], 1)
    body = solution_button(client, s["id"])
    assert body["intent"] == "solution" and body["answer_when"] == "now" and body["thread"]["status"] == "open"
    assert llm.routing_calls == []  # no STT, no routing call
    assert "So the answer is Beta." in answer(client, body)


def test_solution_away_from_a_checkpoint_is_an_ordinary_doubt(client, llm, session):
    llm.classify(intent="solution", question="solve that problem")
    assert ask_video(client, session["id"], at_ms=90_000)["intent"] == "not_covered"


def test_solution_button_for_an_unknown_question_is_404(client, session):
    r = client.post(f"/api/sessions/{session['id']}/checkpoints/0/solution", json={"question_idx": 5})
    assert r.status_code == 404


# --- prep: solutions are checked against the answer key ---------------------------------------------


def test_a_solution_ending_on_another_option_is_written_again(monkeypatch):
    q = question("free fall")
    replies = [
        solutions.SolutionDraft(final_option="C", steps=[StepDraft(say="a"), StepDraft(say="b")]),
        solutions.SolutionDraft(final_option="B", steps=[StepDraft(say="c"), StepDraft(say="d")]),
        solutions.SolutionCheck(option="B", correct=True),
    ]
    prompts: list[str] = []

    async def prep_json(system, user, schema):
        prompts.append(user)
        return replies.pop(0)

    monkeypatch.setattr(app.llm, "prep_json", prep_json)
    steps = asyncio.run(solutions.write_solution(q, "[00:00] lecture"))
    assert [s.say for s in steps] == ["c", "d"]
    assert "ended on option C, not B" in prompts[1]


def test_no_solution_when_the_checker_never_agrees(monkeypatch):
    draft = solutions.SolutionDraft(final_option="B", steps=[StepDraft(say="a"), StepDraft(say="b")])
    wrong = solutions.SolutionCheck(option="B", correct=False, problems="step 2 is wrong")

    async def prep_json(system, user, schema):
        return draft if schema is solutions.SolutionDraft else wrong

    monkeypatch.setattr(app.llm, "prep_json", prep_json)
    assert asyncio.run(solutions.write_solution(question("free fall"), "")) is None


def test_question_image_reaches_the_player(client):
    image = {"url": "/media/lessons/board-cp/questions/q1.png", "w": 1000, "h": 667}
    lesson_with(question("free fall") | {"image": image})
    q = client.get("/api/lessons/board-cp").json()["checkpoints"][0]["questions"][0]
    assert q["image"] == image

