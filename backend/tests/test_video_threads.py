"""Video lesson spec 1.1 and 2.3: doubt threads (policy rules, per-intent lifecycle, reply/abandon, caps, recaps)."""

import pytest

from app.config import settings
from app.db import DoubtThread
from app.progress import doubt_mastery
from app.schemas import Spoken, ThreadOut
from app.services.threads import ThreadPolicy, can_follow_up, needs_recap, thread_out
from tests.conftest import create_guest, create_session, mastery_of
from tests.test_video_api import UNKNOWN, ask_video, publish_video, video_state

CAPPED_LINE = "Let's continue with the lecture. Ask me again at the end and we'll go through it."
FOLLOWUP_LINE = "Sure, which part is still unclear? Ask me."
SORRY_LINE = "Sorry, something went wrong there. Let's continue with the lecture."
# Fallback recap for chapter 0 ("Free fall"); the fake LLM returns blank text for the recap schema
TEMPLATE_RECAP = "Okay, back to Free fall."
ANSWERABLE = ["prerequisite", "confused", "not_covered"]


@pytest.fixture
def video_lesson():
    return publish_video()


@pytest.fixture
def session(client, video_lesson):
    guest = create_guest(client)
    s = create_session(client, guest, video_lesson.slug)
    s["guest_id"] = guest
    return s


def reply(client, thread_id: str, understood: bool):
    return client.post(f"/api/threads/{thread_id}/reply", json={"understood": understood})


def abandon(client, thread_id: str, reason: str | None = "idle"):
    if reason is None:
        return client.post(f"/api/threads/{thread_id}/abandon")
    return client.post(f"/api/threads/{thread_id}/abandon", json={"reason": reason})


def answered(client, doubt: dict) -> str:
    """Consume the answer stream so the doubt counts as answered."""
    r = client.get(f"/api/doubts/{doubt['doubt_id']}/answer")
    assert r.status_code == 200, r.text
    return r.text


def open_thread_with(client, llm, session_id: str, at_ms: int = 90_000, question: str = "why does the packet go up") -> dict:
    llm.classify(intent="confused", question=question)
    body = ask_video(client, session_id, at_ms=at_ms)
    assert body["thread"]["status"] == "open"
    return body


# --- ThreadPolicy and pure rules ----------------------------------------------------------


def test_policy_from_settings():
    p = ThreadPolicy.from_settings()
    assert p.max_followups == settings.video_max_followups
    assert p.recap_after_turns == settings.video_recap_after_turns
    assert p.recap_after_seconds == settings.video_recap_after_seconds


def test_can_follow_up_while_turns_minus_one_is_below_max():
    p = ThreadPolicy(max_followups=2, recap_after_turns=2, recap_after_seconds=45)
    assert can_follow_up(1, p) is True
    assert can_follow_up(2, p) is True
    assert can_follow_up(3, p) is False


def test_can_follow_up_with_zero_followups_allowed():
    p = ThreadPolicy(max_followups=0, recap_after_turns=2, recap_after_seconds=45)
    assert can_follow_up(1, p) is False


def test_needs_recap_by_turns():
    p = ThreadPolicy(max_followups=3, recap_after_turns=2, recap_after_seconds=45)
    assert needs_recap(1, 0, p) is False
    assert needs_recap(2, 0, p) is True


def test_needs_recap_by_audio():
    p = ThreadPolicy(max_followups=3, recap_after_turns=2, recap_after_seconds=45)
    assert needs_recap(1, 44_999, p) is False
    assert needs_recap(1, 45_000, p) is True


def test_thread_out_reports_follow_up_and_stored_recap():
    p = ThreadPolicy(max_followups=1, recap_after_turns=2, recap_after_seconds=45)
    stored = {"text": "So, we were looking at free fall.", "audio_url": "/media/r.wav", "duration_ms": 700}
    thread = DoubtThread(id="t1", session_id="s1", at_ms=90_000, chapter_idx=0, status="resolved", turns=2, audio_ms=1200, recap=stored)
    out = thread_out(thread, p)
    assert isinstance(out, ThreadOut)
    assert out.id == "t1"
    assert out.status == "resolved"
    assert out.turns == 2
    assert out.audio_ms == 1200
    assert out.can_follow_up is False
    assert out.recap.text == "So, we were looking at free fall."
    assert out.line is None


def test_thread_out_takes_an_explicit_line_and_recap():
    p =ThreadPolicy(max_followups=3, recap_after_turns=2, recap_after_seconds=45)
    thread = DoubtThread(id="t2", session_id="s1", at_ms=0, chapter_idx=0, status="open", turns=1, audio_ms=0, recap=None)
    line = Spoken(text=FOLLOWUP_LINE, audio_url="/media/l.wav", duration_ms=500)
    recap = Spoken(text="Recap.", audio_url="/media/r.wav", duration_ms=500)
    out = thread_out(thread, p, recap=recap, line=line)
    assert out.can_follow_up is True
    assert out.line.text == FOLLOWUP_LINE
    assert out.recap.text == "Recap."


# --- per-intent lifecycle (1.1.2) ----------------------------------------------------------


@pytest.mark.parametrize("intent", ANSWERABLE)
def test_answerable_intent_opens_a_thread_and_is_answered_now(client, llm, session, intent):
    llm.classify(intent=intent, question="sawal")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["intent"] == intent
    assert body["answer_when"] == "now"
    assert body["question"] == "sawal"
    assert body["turn"] == 0
    assert body["thread_id"] == body["thread"]["id"]
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 1
    assert body["ack"]["text"].strip() != ""
    assert body["target_chapter_idx"] is None
    assert body["callback"] is None


def test_covered_later_with_a_chapter_ahead_resolves_and_schedules_the_callback(client, llm, session):
    llm.classify(intent="covered_later", question="what is relative velocity", target_scene_idx=2, callback="You asked about relative velocity.")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["intent"] == "covered_later"
    assert body["answer_when"] == "target_chapter"
    assert body["target_chapter_idx"] == 2
    assert body["target_ms"] == 400_000
    assert body["callback"]["text"] == "You asked about relative velocity."
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["turns"] == 0


@pytest.mark.parametrize("target", [0, 1, 3, None])
def test_covered_later_target_not_ahead_becomes_not_covered(client, llm, session, target):
    llm.classify(intent="covered_later", question="sawal", target_scene_idx=target, callback="Later.")
    body = ask_video(client, session["id"], at_ms=250_000)  # chapter 1
    assert body["intent"] == "not_covered"
    assert body["answer_when"] == "now"
    assert body["target_chapter_idx"] is None
    assert body["callback"] is None
    assert body["thread"]["status"] == "open"


def test_covered_later_inside_an_open_thread_is_answered_now(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="covered_later", question="and the range", target_scene_idx=2, callback="You asked about range.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "now"
    assert body["thread_id"] == first["thread_id"]
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 2
    assert body["turn"] == 1


def test_repeat_replays_from_thirty_seconds_back(client, llm, session):
    llm.classify(intent="repeat", question="say that again")
    body = ask_video(client, session["id"], at_ms=250_000)
    assert body["answer_when"] == "replay"
    assert body["replay_from_ms"] == 220_000
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["turns"] == 0


def test_repeat_never_replays_from_before_the_chapter_start(client, llm, session):
    llm.classify(intent="repeat", question="say that again")
    body = ask_video(client, session["id"], at_ms=210_000)  # chapter 1 starts at 200 s
    assert body["replay_from_ms"] == 200_000


def test_continue_without_a_thread_is_resolved(client, llm, session):
    llm.classify(intent="continue", question="let's go on")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "resolved"


def test_continue_closes_the_open_thread(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="continue", question="ok let's continue")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread_id"] == first["thread_id"]
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["turns"] == 1


@pytest.mark.parametrize("intent", ["off_topic", "unclear"])
def test_off_topic_or_unclear_keeps_an_open_thread_open_without_a_turn(client, llm, session, intent):
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent=intent, question="hmm")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["intent"] == intent
    assert body["answer_when"] == "none"
    assert body["thread_id"] == first["thread_id"]
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 1
    assert body["turn"] == 1


def test_off_topic_without_a_thread_is_resolved(client, llm, session):
    llm.classify(intent="off_topic", question="hmm")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["turns"] == 0


def test_unclear_without_a_thread_stays_open(client, llm, session):
    # "Could you say that again?" waits for the student instead of resuming the video
    llm.classify(intent="unclear", question="hmm")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 0


def test_empty_speech_is_unclear_and_uses_no_turn(client, llm, stt, session):
    stt.text = ""
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["intent"] == "unclear"
    assert body["answer_when"] == "none"
    assert body["thread"]["turns"] == 0


def test_low_confidence_is_answered_now_not_clarified(client, llm, session):
    llm.classify(intent="confused", question="why up", confidence=0.3)
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["intent"] == "confused"
    assert body["answer_when"] == "now"
    assert body["thread"]["status"] == "open"


def test_affirmative_after_an_answer_is_continue_without_an_llm_call(client, llm, stt, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    routing_calls = len(llm.routing_calls)
    stt.text = "yes got it"
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["intent"] == "continue"
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "resolved"
    assert len(llm.routing_calls) == routing_calls


def test_unknown_thread_id_starts_a_new_thread(client, llm, session):
    llm.classify(intent="confused", question="sawal")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=UNKNOWN)
    assert body["thread_id"] != UNKNOWN
    assert body["thread"]["status"] == "open"
    assert body["thread"]["turns"] == 1


def test_closed_thread_id_starts_a_new_thread(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    assert reply(client, first["thread_id"], True).json()["status"] == "resolved"
    llm.classify(intent="confused", question="one more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread_id"] != first["thread_id"]
    assert body["thread"]["status"] == "open"
    assert body["turn"] == 0


def test_routing_prompt_sees_chapters_and_the_threads_earlier_doubts(client, llm, session):
    first = open_thread_with(client, llm, session["id"], question="why does the packet go up")
    llm.classify(intent="confused", question="and then")
    ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    prompt = llm.routing_calls[-1]["user"]
    assert "Free fall" in prompt
    assert "Relative velocity" in prompt
    assert "why does the packet go up" in prompt


def test_struggle_intent_lowers_mastery_of_the_chapters_checkpoint_concept(client, llm, session):
    llm.classify(intent="confused", question="why up")
    ask_video(client, session["id"], at_ms=90_000)  # chapter 0 -> checkpoint 0, first question "free fall"
    m = mastery_of(client, session["guest_id"], "free fall")
    assert m is not None
    assert m["score"] == pytest.approx(doubt_mastery(None))


def test_struggle_intent_in_a_chapter_without_a_checkpoint_skips_mastery(client, llm, session):
    llm.classify(intent="confused", question="what frame")
    ask_video(client, session["id"], at_ms=450_000)  # chapter 2 has no checkpoint
    assert mastery_of(client, session["guest_id"], "free fall") is None
    assert mastery_of(client, session["guest_id"], "projectile range") is None


# --- checkpoint follow-up doubts -----------------------------------------------------------


def test_checkpoint_doubt_is_forced_to_confused(client, llm, session):
    llm.classify(intent="not_covered", question="why not alpha")
    body = ask_video(client, session["id"], at_ms=200_000, checkpoint_idx=0, question_idx=0)
    assert body["intent"] == "confused"
    assert body["answer_when"] == "now"
    assert body["thread"]["status"] == "open"


@pytest.mark.parametrize("intent", ["off_topic", "unclear", "continue"])
def test_checkpoint_doubt_keeps_non_answerable_intents(client, llm, session, intent):
    llm.classify(intent=intent, question="hmm")
    body = ask_video(client, session["id"], at_ms=200_000, checkpoint_idx=0, question_idx=0)
    assert body["intent"] == intent
    assert body["answer_when"] == "none"


# --- answer stream -------------------------------------------------------------------------


def test_answer_streams_the_lines_and_stores_them(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    text = answered(client, first)
    assert "Yeh answer hai." in text
    assert len(llm.stream_calls) == 1
    doubts = video_state(client, session["id"])["doubts"]
    assert doubts[0]["answered"] is True


def test_answer_replays_from_storage(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    answered(client, first)
    assert len(llm.stream_calls) == 1


def test_answer_prompt_names_the_doubt_and_the_current_chapter(client, llm, session):
    first = open_thread_with(client, llm, session["id"], question="why does the packet go up")
    answered(client, first)
    prompt = llm.stream_calls[-1]["user"]
    assert "why does the packet go up" in prompt
    assert "Free fall" in prompt


def test_answer_prompt_sees_the_threads_earlier_doubt(client, llm, session):
    first = open_thread_with(client, llm, session["id"], question="why does the packet go up")
    answered(client, first)
    llm.classify(intent="confused", question="and what about the balloon")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, second)
    assert "why does the packet go up" in llm.stream_calls[-1]["user"]


def test_checkpoint_doubt_answer_prompt_sees_the_question_and_picks(client, llm, session):
    client.post(f"/api/sessions/{session['id']}/checkpoints/0/answer", json={"question_idx": 0, "chosen_index": 3, "kind": "first"})
    llm.classify(intent="confused", question="why not delta")
    body = ask_video(client, session["id"], at_ms=200_000, checkpoint_idx=0, question_idx=0)
    answered(client, body)
    assert "Question about free fall?" in llm.stream_calls[-1]["user"]


def test_answer_adds_its_audio_to_the_thread_once(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    assert first["thread"]["audio_ms"] == 0
    answered(client, first)
    after_first = reply(client, first["thread_id"], False).json()["audio_ms"]
    assert after_first > 0
    answered(client, first)  # cached replay
    assert reply(client, first["thread_id"], False).json()["audio_ms"] == after_first


# --- reply / abandon -----------------------------------------------------------------------


def test_got_it_resolves_the_thread(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    r = reply(client, first["thread_id"], True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == first["thread_id"]
    assert body["status"] == "resolved"
    assert body["turns"] == 1


def test_ask_another_keeps_the_thread_open_with_the_follow_up_line(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    body = reply(client, first["thread_id"], False).json()
    assert body["status"] == "open"
    assert body["can_follow_up"] is True
    assert body["line"]["text"] == FOLLOWUP_LINE
    assert set(body["line"].keys()) == {"text", "audio_url", "duration_ms"}


def test_ask_another_when_no_follow_up_is_left_caps_the_thread(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    assert first["thread"]["can_follow_up"] is False
    body = reply(client, first["thread_id"], False).json()
    assert body["status"] == "capped"
    assert body["line"]["text"] == CAPPED_LINE


def test_follow_up_after_ask_another_continues_the_thread(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    reply(client, first["thread_id"], False)
    llm.classify(intent="confused", question="the balloon part")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread_id"] == first["thread_id"]
    assert body["turn"] == 1
    assert body["thread"]["turns"] == 2


def test_abandon_idle(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    r = abandon(client, first["thread_id"], "idle")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "abandoned"
    assert r.json()["line"] is None


def test_abandon_error_gives_the_sorry_line(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    body = abandon(client, first["thread_id"], "error").json()
    assert body["status"] == "abandoned"
    assert body["line"]["text"] == SORRY_LINE


def test_abandon_body_is_optional(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    r = abandon(client, first["thread_id"], None)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "abandoned"


def test_reply_unknown_thread_is_404(client, video_lesson):
    assert reply(client, UNKNOWN, True).status_code == 404


def test_abandon_unknown_thread_is_404(client, video_lesson):
    assert abandon(client, UNKNOWN, "idle").status_code == 404


def test_reply_on_a_closed_thread_is_idempotent(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    once = reply(client, first["thread_id"], True).json()
    again = reply(client, first["thread_id"], True)
    assert again.status_code == 200
    assert again.json()["status"] == "resolved"
    assert again.json()["turns"] == once["turns"]
    assert reply(client, first["thread_id"], False).json()["status"] == "resolved"


def test_abandon_on_a_closed_thread_leaves_it_unchanged(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    reply(client, first["thread_id"], True)
    body = abandon(client, first["thread_id"], "error").json()
    assert body["status"] == "resolved"
    assert body["line"] is None


# --- caps (1.1.5) --------------------------------------------------------------------------


def test_can_follow_up_in_the_response_follows_the_turns(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 1)
    first = open_thread_with(client, llm, session["id"])
    assert first["thread"]["can_follow_up"] is True
    llm.classify(intent="confused", question="more")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert second["thread"]["turns"] == 2
    assert second["thread"]["can_follow_up"] is False


def test_answerable_doubt_over_the_cap_is_not_answered_and_caps_the_thread(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 1)
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="confused", question="more")
    ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    llm.classify(intent="prerequisite", question="even more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["intent"] == "prerequisite"
    assert body["answer_when"] == "none"
    assert body["turn"] == 2
    assert body["thread"]["status"] == "capped"
    assert body["thread"]["turns"] == 2
    assert body["thread"]["line"]["text"] == CAPPED_LINE


def test_capped_doubt_is_stored_without_an_answer(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="confused", question="more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread"]["status"] == "capped"
    doubts = video_state(client, session["id"])["doubts"]
    assert [d["question"] for d in doubts] == ["why does the packet go up", "more"]
    assert doubts[1]["intent"] == "confused"
    assert doubts[1]["answered"] is False


# --- recap (1.1.6) -------------------------------------------------------------------------


def test_single_short_doubt_gets_no_recap(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    body = reply(client, first["thread_id"], True).json()
    assert body["status"] == "resolved"
    assert body["recap"] is None


def test_thread_with_enough_turns_gets_a_recap_on_got_it(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 2)
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="confused", question="more")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, second)
    body = reply(client, first["thread_id"], True).json()
    assert body["status"] == "resolved"
    assert body["recap"] is not None
    assert set(body["recap"].keys()) == {"text", "audio_url", "duration_ms"}


def test_blank_recap_text_falls_back_to_the_template(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 2)
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="confused", question="more")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, second)
    body = reply(client, first["thread_id"], True).json()
    assert body["recap"]["text"] == TEMPLATE_RECAP


def test_long_thread_abandoned_gets_a_recap(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 2)
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="confused", question="more")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, second)
    body = abandon(client, first["thread_id"], "idle").json()
    assert body["status"] == "abandoned"
    assert body["recap"]["text"] == TEMPLATE_RECAP


def test_long_thread_capped_by_a_question_gets_a_recap(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 2)
    monkeypatch.setattr(settings, "video_max_followups", 1)
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="confused", question="more")
    second = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    answered(client, second)
    llm.classify(intent="confused", question="even more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread"]["status"] == "capped"
    assert body["thread"]["line"]["text"] == CAPPED_LINE
    assert body["thread"]["recap"]["text"] == TEMPLATE_RECAP


def test_short_thread_closed_by_continue_gets_no_recap(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    answered(client, first)
    llm.classify(intent="continue", question="ok")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["thread"]["status"] == "resolved"
    assert body["thread"]["recap"] is None


def test_recap_template_names_the_threads_chapter(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    first = open_thread_with(client, llm, session["id"], at_ms=250_000)  # chapter 1
    answered(client, first)
    body = reply(client, first["thread_id"], True).json()
    assert body["recap"]["text"] == "Okay, back to Projectiles."


def test_video_doubt_rows_carry_the_thread_and_turn(client, llm, session):
    first = open_thread_with(client, llm, session["id"], at_ms=90_000)
    llm.classify(intent="confused", question="more")
    ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    doubts = video_state(client, session["id"])["doubts"]
    assert [(d["thread_id"], d["turn"], d["at_ms"]) for d in doubts] == [
        (first["thread_id"], 0, 90_000),
        (first["thread_id"], 1, 90_000),
    ]
