"""Video lesson spec 2.4: lesson out, list, position, checkpoint answers/skips, video state, resume.

Also holds the video-lesson factory and HTTP helpers the other video test files import.
"""

import types

import pytest

from app.config import settings
from app.db import SessionLocal
from app.lesson_store import publish_video_lesson
from app.progress import next_mastery
from tests.conftest import QUESTION_WAV, create_guest, create_session, finish, mastery_of

UNKNOWN = "00000000-0000-0000-0000-000000000000"
VIDEO_SLUG = "lecture-05-video"
DURATION_MS = 600_000


# ---------------------------------------------------------------------------
# Video lesson factory
# ---------------------------------------------------------------------------


def spoken(text: str) -> dict:
    return {"text": text, "audio_url": f"/media/test/{len(text)}.wav", "duration_ms": 900}


def chapter(topic: str, title: str, key_terms: list[str], start_ms: int, end_ms: int, summary: str = "") -> dict:
    return {
        "topic": topic,
        "title": title,
        "summary": summary or f"About {title.lower()}.",
        "key_terms": key_terms,
        "misconceptions": [],
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def question(concept: str, correct_index: int = 1, replay: dict | None = None) -> dict:
    options = ["Alpha", "Beta", "Gamma", "Delta"]
    return {
        "question": f"Question about {concept}?",
        "say": f"Question about {concept}?",
        "audio_url": "/media/test/q.wav",
        "duration_ms": 1500,
        "options": options,
        "correct_index": correct_index,
        "concept": concept,
        "explanation": f"Explanation about {concept}.",
        "why_wrong": [None if i == correct_index else spoken(f"Hint for option {i} of {concept}.") for i in range(4)],
        "replay": replay or {"start_ms": 100_000, "end_ms": 150_000},
        "reteach": [],
        "followup": spoken("Which part confused you? Ask me, or let's continue."),
    }


def checkpoint(at_ms: int, chapter_idx: int, questions: list[dict]) -> dict:
    return {
        "at_ms": at_ms,
        "chapter_idx": chapter_idx,
        "intro": spoken("Let's check what we just covered."),
        "questions": questions,
    }


def default_chapters() -> list[dict]:
    """Three chapters of 200 s each over a 10 minute video."""
    return [
        chapter("Motion", "Free fall", ["gravity", "free fall", "packet"], 0, 200_000),
        chapter("Motion", "Projectiles", ["projectile", "range"], 200_000, 400_000),
        chapter("Motion", "Relative velocity", ["relative velocity", "frame"], 400_000, 600_000),
    ]


def default_checkpoints() -> list[dict]:
    """Checkpoint 0 (end of chapter 0) has two questions, checkpoint 1 (end of chapter 1) has one; chapter 2 has none."""
    return [
        checkpoint(200_000, 0, [question("free fall"), question("gravity", correct_index=2)]),
        checkpoint(400_000, 1, [question("projectile range", correct_index=0)]),
    ]


def default_transcript() -> str:
    """A `[mm:ss]` transcript line every 30 s over the whole video; each line names its chapter's words."""
    lines = []
    for i in range(20):
        t = i * 30
        if t < 200:
            text = f"Line {i}: the packet dropped from the balloon is in free fall under gravity."
        elif t < 400:
            text = f"Line {i}: a projectile follows a parabola and its range depends on the angle."
        else:
            text = f"Line {i}: relative velocity means velocity measured in another frame."
        lines.append(f"[{t // 60:02d}:{t % 60:02d}] {text}")
    return "\n".join(lines)


def publish_video(
    slug: str = VIDEO_SLUG,
    chapters: list[dict] | None = None,
    checkpoints: list[dict] | None = None,
    transcript: str | None = None,
    duration_ms: int = DURATION_MS,
    title: str = "Lecture 5",
) -> types.SimpleNamespace:
    """Publish a video lesson and commit; returns a detached snapshot of the Lesson row."""
    db = SessionLocal()
    try:
        lesson = publish_video_lesson(
            db,
            slug,
            title,
            transcript if transcript is not None else default_transcript(),
            f"/media/videos/{slug}.mp4",
            duration_ms,
            chapters if chapters is not None else default_chapters(),
            checkpoints if checkpoints is not None else default_checkpoints(),
            voice=None,
            language="english",
        )
        db.commit()
        db.refresh(lesson)
        return types.SimpleNamespace(id=lesson.id, slug=lesson.slug, title=lesson.title, version=lesson.version)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def ask_video(
    client,
    session_id: str,
    at_ms: int,
    thread_id: str | None = None,
    checkpoint_idx: int | None = None,
    question_idx: int | None = None,
) -> dict:
    """Upload a doubt while the video is paused at `at_ms` (the STT fake decides the transcript)."""
    data = {"at_ms": str(at_ms)}
    if thread_id is not None:
        data["thread_id"] = thread_id
    if checkpoint_idx is not None:
        data["checkpoint_idx"] = str(checkpoint_idx)
        data["question_idx"] = str(question_idx)
    r = client.post(
        f"/api/sessions/{session_id}/video/questions",
        files={"audio": ("question.wav", QUESTION_WAV, "audio/wav")},
        data=data,
    )
    assert r.status_code == 200, r.text
    return r.json()


def save_video_position(client, session_id: str, at_ms: int):
    return client.post(f"/api/sessions/{session_id}/video/position", json={"at_ms": at_ms})


def answer_checkpoint(client, session_id: str, idx: int, question_idx: int, chosen_index: int, kind: str = "first"):
    return client.post(
        f"/api/sessions/{session_id}/checkpoints/{idx}/answer",
        json={"question_idx": question_idx, "chosen_index": chosen_index, "kind": kind},
    )


def skip_checkpoint(client, session_id: str, idx: int, question_idx: int):
    return client.post(f"/api/sessions/{session_id}/checkpoints/{idx}/skip", json={"question_idx": question_idx})


def video_state(client, session_id: str) -> dict:
    r = client.get(f"/api/sessions/{session_id}/video/state")
    assert r.status_code == 200, r.text
    return r.json()


def resume(client, guest_id: str, slug: str = VIDEO_SLUG):
    return client.get(f"/api/guests/{guest_id}/lessons/{slug}/resume")


@pytest.fixture
def video_lesson():
    return publish_video()


@pytest.fixture
def session(client, video_lesson):
    guest = create_guest(client)
    s = create_session(client, guest, video_lesson.slug)
    s["guest_id"] = guest
    return s


# --- lesson out -------------------------------------------------------------------------


def test_video_lesson_out_has_kind_video_and_the_video(client, video_lesson):
    body = client.get(f"/api/lessons/{VIDEO_SLUG}").json()
    assert body["kind"] == "video"
    assert body["video"] == {"url": f"/media/videos/{VIDEO_SLUG}.mp4", "duration_ms": DURATION_MS}
    assert body["scenes"] == []


def test_video_lesson_out_lists_chapters_in_order(client, video_lesson):
    chapters = client.get(f"/api/lessons/{VIDEO_SLUG}").json()["chapters"]
    assert [c["idx"] for c in chapters] == [0, 1, 2]
    assert chapters[0]["title"] == "Free fall"
    assert chapters[0]["topic"] == "Motion"
    assert chapters[0]["key_terms"] == ["gravity", "free fall", "packet"]
    assert (chapters[1]["start_ms"], chapters[1]["end_ms"]) == (200_000, 400_000)


def test_video_lesson_out_lists_checkpoints_with_their_questions(client, video_lesson):
    checkpoints = client.get(f"/api/lessons/{VIDEO_SLUG}").json()["checkpoints"]
    assert [cp["idx"] for cp in checkpoints] == [0, 1]
    first = checkpoints[0]
    assert first["at_ms"] == 200_000
    assert first["chapter_idx"] == 0
    assert first["intro"]["text"] == "Let's check what we just covered."
    assert len(first["questions"]) == 2
    q = first["questions"][0]
    assert q["options"] == ["Alpha", "Beta", "Gamma", "Delta"]
    assert q["correct_index"] == 1
    assert q["concept"] == "free fall"
    assert q["why_wrong"][1] is None
    assert q["why_wrong"][0]["text"].startswith("Hint for option 0")
    assert q["replay"] == {"start_ms": 100_000, "end_ms": 150_000}
    assert q["reteach"] == []
    assert q["followup"]["text"] == "Which part confused you? Ask me, or let's continue."


def test_video_lesson_out_policy_comes_from_settings(client, video_lesson):
    policy = client.get(f"/api/lessons/{VIDEO_SLUG}").json()["policy"]
    assert policy == {
        "max_followups": settings.video_max_followups,
        "recap_after_turns": settings.video_recap_after_turns,
        "recap_after_seconds": settings.video_recap_after_seconds,
        "rewind_ms": settings.video_rewind_ms,
        "idle_ms": settings.video_thread_idle_s * 1000,
    }


def test_generated_lesson_out_keeps_kind_generated(client, lesson):
    body = client.get(f"/api/lessons/{lesson.slug}").json()
    assert body["kind"] == "generated"
    assert body["video"] is None
    assert body["chapters"] == []
    assert body["checkpoints"] == []
    assert len(body["scenes"]) == 3


def test_list_lessons_fills_kind_and_chapter_count(client, lesson, video_lesson):
    by_slug = {l["slug"]: l for l in client.get("/api/lessons").json()}
    assert by_slug[VIDEO_SLUG]["kind"] == "video"
    assert by_slug[VIDEO_SLUG]["scene_count"] == 3  # chapters
    assert by_slug[lesson.slug]["kind"] == "generated"
    assert by_slug[lesson.slug]["scene_count"] == 3  # scenes


# --- POST /api/sessions/{id}/video/position ----------------------------------------------


def test_save_video_position_returns_ok(client, session):
    r = save_video_position(client, session["id"], 123_456)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_save_video_position_is_readable_in_the_state(client, session):
    save_video_position(client, session["id"], 123_456)
    assert video_state(client, session["id"])["position_ms"] == 123_456


def test_save_video_position_last_write_wins(client, session):
    save_video_position(client, session["id"], 300_000)
    save_video_position(client, session["id"], 150_000)
    assert video_state(client, session["id"])["position_ms"] == 150_000


def test_save_video_position_is_clamped_to_the_duration(client, session):
    assert save_video_position(client, session["id"], DURATION_MS + 50_000).status_code == 200
    assert video_state(client, session["id"])["position_ms"] == DURATION_MS


def test_save_video_position_unknown_session_is_404(client, video_lesson):
    assert save_video_position(client, UNKNOWN, 1000).status_code == 404


def test_save_video_position_rejects_negative(client, session):
    assert save_video_position(client, session["id"], -1).status_code == 422


# --- POST /api/sessions/{id}/checkpoints/{idx}/answer ------------------------------------


def test_correct_first_answer(client, session):
    r = answer_checkpoint(client, session["id"], 0, 0, 1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["correct"] is True
    assert body["correct_index"] == 1
    assert body["explanation"] == "Explanation about free fall."
    assert body["feedback"]["text"].strip() != ""
    assert set(body["feedback"].keys()) == {"text", "audio_url", "duration_ms"}


def test_wrong_first_answer(client, session):
    body = answer_checkpoint(client, session["id"], 0, 0, 3).json()
    assert body["correct"] is False
    assert body["correct_index"] == 1
    assert body["feedback"]["text"].strip() != ""


def test_each_question_of_a_checkpoint_is_answered_separately(client, session):
    assert answer_checkpoint(client, session["id"], 0, 0, 1).status_code == 200
    assert answer_checkpoint(client, session["id"], 0, 1, 2).status_code == 200
    assert answer_checkpoint(client, session["id"], 1, 0, 0).status_code == 200


def test_second_first_answer_is_409(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    assert answer_checkpoint(client, session["id"], 0, 0, 1).status_code == 409


def test_retry_after_wrong_first_answer(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    r = answer_checkpoint(client, session["id"], 0, 0, 1, kind="retry")
    assert r.status_code == 200, r.text
    assert r.json()["correct"] is True
    assert r.json()["correct_index"] == 1


def test_retry_without_a_first_answer_is_409(client, session):
    assert answer_checkpoint(client, session["id"], 0, 0, 1, kind="retry").status_code == 409


def test_retry_after_a_correct_first_answer_is_409(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 1)
    assert answer_checkpoint(client, session["id"], 0, 0, 2, kind="retry").status_code == 409


def test_second_retry_is_409(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    answer_checkpoint(client, session["id"], 0, 0, 2, kind="retry")
    assert answer_checkpoint(client, session["id"], 0, 0, 1, kind="retry").status_code == 409


def test_answer_unknown_session_is_404(client, video_lesson):
    assert answer_checkpoint(client, UNKNOWN, 0, 0, 1).status_code == 404


def test_answer_unknown_checkpoint_is_404(client, session):
    assert answer_checkpoint(client, session["id"], 5, 0, 1).status_code == 404


def test_answer_unknown_question_is_404(client, session):
    assert answer_checkpoint(client, session["id"], 1, 1, 1).status_code == 404


def test_answer_chosen_index_beyond_options_is_422(client, session):
    assert answer_checkpoint(client, session["id"], 0, 0, 4).status_code == 422


def test_first_answer_updates_mastery_of_the_question_concept(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 1)
    m = mastery_of(client, session["guest_id"], "free fall")
    assert m is not None
    assert m["attempts"] == 1
    assert m["score"] == pytest.approx(next_mastery(None, True))


def test_wrong_first_answer_lowers_mastery(client, session):
    answer_checkpoint(client, session["id"], 0, 1, 0)  # correct is 2
    m = mastery_of(client, session["guest_id"], "gravity")
    assert m is not None
    assert m["score"] == pytest.approx(next_mastery(None, False))


# --- POST /api/sessions/{id}/checkpoints/{idx}/skip --------------------------------------


def test_skip_returns_ok(client, session):
    r = skip_checkpoint(client, session["id"], 0, 0)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_skip_is_idempotent(client, session):
    skip_checkpoint(client, session["id"], 0, 0)
    assert skip_checkpoint(client, session["id"], 0, 0).status_code == 200
    skips = [a for a in video_state(client, session["id"])["answered"] if a["kind"] == "skip"]
    assert len(skips) == 1


def test_skip_is_allowed_after_an_answer(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    assert skip_checkpoint(client, session["id"], 0, 0).status_code == 200


def test_skip_unknown_session_is_404(client, video_lesson):
    assert skip_checkpoint(client, UNKNOWN, 0, 0).status_code == 404


# --- GET /api/sessions/{id}/video/state --------------------------------------------------


def test_fresh_video_state(client, session):
    assert video_state(client, session["id"]) == {
        "session_id": session["id"],
        "completed": False,
        "position_ms": None,
        "answered": [],
        "doubts": [],
    }


def test_video_state_unknown_session_is_404(client, video_lesson):
    assert client.get(f"/api/sessions/{UNKNOWN}/video/state").status_code == 404


def test_video_state_lists_checkpoint_answers(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    answer_checkpoint(client, session["id"], 0, 0, 1, kind="retry")
    answer_checkpoint(client, session["id"], 1, 0, 0)
    answered = video_state(client, session["id"])["answered"]
    assert {"checkpoint_idx": 0, "question_idx": 0, "kind": "first", "correct": False} in answered
    assert {"checkpoint_idx": 0, "question_idx": 0, "kind": "retry", "correct": True} in answered
    assert {"checkpoint_idx": 1, "question_idx": 0, "kind": "first", "correct": True} in answered
    assert len(answered) == 3


def test_video_state_lists_skips(client, session):
    skip_checkpoint(client, session["id"], 0, 1)
    answered = video_state(client, session["id"])["answered"]
    assert len(answered) == 1
    assert answered[0]["checkpoint_idx"] == 0
    assert answered[0]["question_idx"] == 1
    assert answered[0]["kind"] == "skip"


def test_video_state_lists_doubts_oldest_first_with_answered_flag(client, llm, session):
    llm.classify(intent="confused", question="why does the packet go up")
    first = ask_video(client, session["id"], at_ms=90_000)
    llm.classify(intent="confused", question="what is a frame")
    second = ask_video(client, session["id"], at_ms=450_000)
    assert client.get(f"/api/doubts/{first['doubt_id']}/answer").status_code == 200

    doubts = video_state(client, session["id"])["doubts"]
    assert [d["id"] for d in doubts] == [first["doubt_id"], second["doubt_id"]]
    assert doubts[0] == {
        "id": first["doubt_id"],
        "at_ms": 90_000,
        "thread_id": first["thread_id"],
        "turn": 0,
        "question": "why does the packet go up",
        "intent": "confused",
        "answered": True,
    }
    assert doubts[1]["answered"] is False
    assert doubts[1]["at_ms"] == 450_000


def test_video_state_completed_after_summary(client, session):
    finish(client, session["id"])
    assert video_state(client, session["id"])["completed"] is True


# --- resume / progress -------------------------------------------------------------------


def test_resume_gives_position_ms_and_the_chapter_at_that_time(client, session):
    save_video_position(client, session["id"], 250_000)
    r = resume(client, session["guest_id"])
    assert r.status_code == 200
    assert r.json() == {"session_id": session["id"], "scene_idx": 1, "step_idx": 0, "position_ms": 250_000}


def test_resume_chapter_is_the_one_containing_the_position(client, session):
    save_video_position(client, session["id"], 450_000)
    assert resume(client, session["guest_id"]).json()["scene_idx"] == 2


def test_resume_without_a_saved_video_position_has_no_position_ms(client, session):
    body = resume(client, session["guest_id"]).json()
    assert body is None or "position_ms" not in body


def test_resume_of_a_scene_lesson_has_no_position_ms_key(client, lesson):
    guest = create_guest(client)
    s = create_session(client, guest, lesson.slug)
    client.post(f"/api/sessions/{s['id']}/position", json={"scene_idx": 1, "step_idx": 1})
    assert resume(client, guest, lesson.slug).json() == {"session_id": s["id"], "scene_idx": 1, "step_idx": 1}


def test_resume_is_null_for_a_completed_video_session(client, session):
    save_video_position(client, session["id"], 250_000)
    finish(client, session["id"])
    assert resume(client, session["guest_id"]).json() is None


def test_progress_percent_is_position_over_duration(client, session):
    save_video_position(client, session["id"], 300_000)
    rows = {p["slug"]: p for p in client.get(f"/api/guests/{session['guest_id']}/progress").json()}
    assert rows[VIDEO_SLUG]["percent"] == 50
    assert rows[VIDEO_SLUG]["resume"]["position_ms"] == 300_000


def test_progress_counts_checkpoint_first_answers(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 1)  # correct
    answer_checkpoint(client, session["id"], 0, 1, 0)  # wrong
    answer_checkpoint(client, session["id"], 0, 1, 2, kind="retry")  # retries don't count
    skip_checkpoint(client, session["id"], 1, 0)
    rows = {p["slug"]: p for p in client.get(f"/api/guests/{session['guest_id']}/progress").json()}
    assert rows[VIDEO_SLUG]["checks_correct"] == 1
    assert rows[VIDEO_SLUG]["checks_total"] == 2


def test_history_counts_checkpoint_first_answers(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 1)
    answer_checkpoint(client, session["id"], 1, 0, 3)
    rows = {h["session_id"]: h for h in client.get(f"/api/guests/{session['guest_id']}/history").json()}
    assert rows[session["id"]]["checks_correct"] == 1
    assert rows[session["id"]]["checks_total"] == 2


def test_summary_marks_a_video_session_completed(client, session):
    answer_checkpoint(client, session["id"], 0, 0, 3)
    body = finish(client, session["id"])
    assert set(body.keys()) == {"lines", "weak_concepts", "revise", "speech"}
    assert video_state(client, session["id"])["completed"] is True
