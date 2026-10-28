"""Review fixes on the video lesson backend: capped acks, no row lock across LLM/TTS, one recap per thread with
unique files, pre-generated fixed lines (and a text-only fallback), prep --redo order, and video resume."""

import asyncio
import json
import re

import pytest
from sqlalchemy import select

import app.services.threads as threads
from app.audio import cached_path
from app.config import settings
from app.db import DoubtThread, Lesson, SessionLocal, TutorSession
from app.prep.video import VIDEO_LINES, make_video_lines, reset_stages
from app.prompts import VIDEO_ACK_COVERED_NOW, VIDEO_ACK_NEUTRAL, VIDEO_ACKS, VIDEO_FOLLOWUP_LINE, VIDEO_RECAP_TEMPLATE
from app.schemas import Spoken
from app.services.threads import RecapDraft, close_thread, make_recap, schedule_recap
from tests.conftest import create_guest, create_session
from tests.test_video_api import ask_video, publish_video, resume, save_video_position
from tests.test_video_threads import CAPPED_LINE, abandon, open_thread_with, reply


@pytest.fixture
def video_lesson():
    return publish_video()


@pytest.fixture
def session(client, video_lesson):
    guest = create_guest(client)
    s = create_session(client, guest, video_lesson.slug)
    s["guest_id"] = guest
    return s


# --- 1. a capped doubt plays a neutral ack ---------------------------------------------------


def test_capped_doubt_gets_a_neutral_ack_then_the_capped_line(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="confused", question="more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "capped"
    assert body["ack"]["text"] == VIDEO_ACK_NEUTRAL
    assert body["ack"]["audio_url"].startswith("/media/")
    assert body["thread"]["line"]["text"] == CAPPED_LINE


def test_covered_later_inside_an_open_thread_is_answered_now_with_the_paused_ack(client, llm, session):
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="covered_later", question="what about range", target_scene_idx=2, callback="Earlier you asked about range.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "now"
    assert body["ack"]["text"] == VIDEO_ACK_COVERED_NOW
    assert body["callback"] is None
    assert body["target_chapter_idx"] is None
    assert body["target_ms"] is None
    assert body["thread"]["status"] == "open"


def test_covered_later_inside_an_open_thread_over_the_cap_promises_nothing(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    llm.classify(intent="covered_later", question="what about range", target_scene_idx=2, callback="Earlier you asked about range.")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert body["answer_when"] == "none"
    assert body["thread"]["status"] == "capped"
    assert body["ack"]["text"] == VIDEO_ACK_NEUTRAL
    assert body["callback"] is None
    assert body["target_chapter_idx"] is None
    assert body["target_ms"] is None


# --- 2. no row lock is held while the recap is made ------------------------------------------


class LockProbe:
    """A make_recap stand-in that tries to lock the session and thread rows from another connection (NOWAIT):
    it fails if the request's transaction still holds either lock while the recap is awaited."""

    def __init__(self, session_id: str, thread_id: str):
        self.session_id, self.thread_id = session_id, thread_id
        self.calls = 0
        self.errors: list[str] = []

    async def __call__(self, thread, lesson) -> Spoken:
        self.calls += 1
        for model, row_id in ((TutorSession, self.session_id), (DoubtThread, self.thread_id)):
            with SessionLocal() as other:
                try:
                    other.execute(select(model).where(model.id == row_id).with_for_update(nowait=True)).all()
                except Exception as e:  # noqa: BLE001 - recorded for the assertion
                    self.errors.append(f"{model.__tablename__}: {type(e).__name__}")
                finally:
                    other.rollback()
        return Spoken(text="Probe recap.", audio_url="/media/test/probe.wav", duration_ms=500)


@pytest.fixture
def probe(monkeypatch):
    def install(session_id: str, thread_id: str) -> LockProbe:
        p = LockProbe(session_id, thread_id)
        monkeypatch.setattr(threads, "make_recap", p)
        return p

    return install


def test_got_it_holds_no_row_lock_while_the_recap_is_made(client, llm, session, monkeypatch, probe):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    first = open_thread_with(client, llm, session["id"])
    p = probe(session["id"], first["thread_id"])
    body = reply(client, first["thread_id"], understood=True).json()
    assert p.calls == 1 and p.errors == []
    assert body["status"] == "resolved"
    assert body["recap"]["text"] == "Probe recap."


def test_abandon_holds_no_row_lock_while_the_recap_is_made(client, llm, session, monkeypatch, probe):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    first = open_thread_with(client, llm, session["id"])
    p = probe(session["id"], first["thread_id"])
    body = abandon(client, first["thread_id"], reason="idle").json()
    assert p.calls == 1 and p.errors == []
    assert body["status"] == "abandoned"
    assert body["recap"]["text"] == "Probe recap."


def test_capping_question_holds_no_row_lock_while_the_recap_is_made(client, llm, session, monkeypatch, probe):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    monkeypatch.setattr(settings, "video_max_followups", 0)
    first = open_thread_with(client, llm, session["id"])
    p = probe(session["id"], first["thread_id"])
    llm.classify(intent="confused", question="more")
    body = ask_video(client, session["id"], at_ms=90_000, thread_id=first["thread_id"])
    assert p.calls == 1 and p.errors == []
    assert body["thread"]["status"] == "capped"
    assert body["thread"]["recap"]["text"] == "Probe recap."


# --- 3. one recap per thread, unique files ---------------------------------------------------


def load_thread_and_lesson(thread_id: str) -> tuple[DoubtThread, Lesson]:
    with SessionLocal() as db:
        thread = db.get(DoubtThread, thread_id)
        lesson = db.get(TutorSession, thread.session_id).lesson
        lesson.chapters
        return thread, lesson


def test_close_thread_waits_for_the_recap_in_flight_instead_of_making_another(client, llm, session, monkeypatch):
    monkeypatch.setattr(settings, "video_recap_after_turns", 1)
    first = open_thread_with(client, llm, session["id"])
    thread_id = first["thread_id"]
    stored = {"text": "Background recap.", "audio_url": "/media/test/bg.wav", "duration_ms": 700}

    async def never(thread, lesson):
        raise AssertionError("a second recap was generated")

    monkeypatch.setattr(threads, "make_recap", never)

    async def background():
        await asyncio.sleep(0.05)
        with SessionLocal.begin() as db:
            db.get(DoubtThread, thread_id).recap = stored

    async def main():
        task = asyncio.create_task(background())
        threads._recap_tasks[thread_id] = task
        try:
            thread, lesson = load_thread_and_lesson(thread_id)
            with SessionLocal() as db:
                thread = db.get(DoubtThread, thread_id)
                return await close_thread(db, thread, "resolved", lesson)
        finally:
            threads._recap_tasks.pop(thread_id, None)

    out = asyncio.run(main())
    assert out.status == "resolved"
    assert out.recap.text == "Background recap."
    with SessionLocal() as db:
        row = db.get(DoubtThread, thread_id)
        assert row.status == "resolved"
        assert row.recap == stored


def test_schedule_recap_keeps_one_task_per_thread(monkeypatch):
    started = []
    release = None

    async def slow(thread_id: str):
        started.append(thread_id)
        await release.wait()

    monkeypatch.setattr(threads, "prepare_recap", slow)

    async def main():
        nonlocal release
        release = asyncio.Event()
        schedule_recap("t1")
        schedule_recap("t1")
        schedule_recap("t2")
        await asyncio.sleep(0)
        assert started == ["t1", "t2"]
        assert set(threads._recap_tasks) == {"t1", "t2"}
        release.set()
        await asyncio.gather(*threads._recap_tasks.values())
        await asyncio.sleep(0)
        assert threads._recap_tasks == {}

    asyncio.run(main())


def test_llm_recap_files_are_unique_per_generation(client, llm, session, monkeypatch):
    first = open_thread_with(client, llm, session["id"])
    thread, lesson = load_thread_and_lesson(first["thread_id"])

    async def recap_text(system, user, schema, temperature=0.4, model=None):
        return RecapDraft(recap="So, we were looking at free fall.")

    monkeypatch.setattr(threads.llm, "complete_json", recap_text)

    async def main():
        return await make_recap(thread, lesson), await make_recap(thread, lesson)

    a, b = asyncio.run(main())
    pattern = re.compile(rf"^/media/threads/{thread.id}_recap_[0-9a-f]{{8}}\.wav$")
    assert pattern.match(a.audio_url) and pattern.match(b.audio_url)
    assert a.audio_url != b.audio_url
    assert (settings.media_dir / a.audio_url.removeprefix("/media/")).exists()
    assert (settings.media_dir / b.audio_url.removeprefix("/media/")).exists()


# --- 4. fixed lines: pre-generated by prep, text-only when TTS fails -------------------------


def test_make_video_lines_caches_every_fixed_line_the_routes_speak(tts):
    titles = ["Free fall", "Projectiles", "Lecture 5"]
    expected = [*VIDEO_LINES, *(VIDEO_RECAP_TEMPLATE.format(title=t) for t in titles)]
    for text in expected:
        cached_path(text, None).unlink(missing_ok=True)
    made = asyncio.run(make_video_lines(titles, None))
    assert made == len(expected)
    assert set(tts.texts) >= set(expected)
    for text in expected:
        assert cached_path(text, None).exists(), text
    # Every ack, the follow-up, capped and sorry lines, the neutral ack and the covered-now ack are among them
    assert set(VIDEO_ACKS.values()) <= set(expected)
    assert {VIDEO_ACK_COVERED_NOW, VIDEO_ACK_NEUTRAL, VIDEO_FOLLOWUP_LINE, CAPPED_LINE} <= set(expected)
    # A second run synthesizes nothing
    before = len(tts.texts)
    assert asyncio.run(make_video_lines(titles, None)) == 0
    assert len(tts.texts) == before


def test_ack_is_text_only_when_tts_fails(client, llm, session, monkeypatch):
    async def broken(text, voice=None):
        raise RuntimeError("tts down")

    monkeypatch.setattr(threads, "speak_cached", broken)
    llm.classify(intent="confused", question="why up")
    body = ask_video(client, session["id"], at_ms=90_000)
    assert body["answer_when"] == "now"
    assert body["ack"] == {"text": VIDEO_ACKS["confused"], "audio_url": "", "duration_ms": 0}
    # Thread lines fall back the same way
    line = reply(client, body["thread_id"], understood=False).json()["line"]
    assert line == {"text": VIDEO_FOLLOWUP_LINE, "audio_url": "", "duration_ms": 0}


# --- 6. prep: --redo transcript with --share-from copies the transcript again -----------------


def test_redo_transcript_with_share_from_copies_the_shared_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    src = settings.lessons_dir / "lecture-05-en"
    src.mkdir(parents=True)
    src.joinpath("transcript.json").write_text(json.dumps("shared transcript"))
    out = settings.lessons_dir / "lecture-05-video"
    out.mkdir(parents=True)
    files = {s: out / f"{s}.json" for s in ("video", "transcript", "chapters", "checkpoints", "review", "remedy", "audio")}
    files["video"].write_text(json.dumps({"url": "/media/videos/x.mp4", "duration_ms": 1000}))
    files["transcript"].write_text(json.dumps("stale transcript"))
    files["chapters"].write_text(json.dumps({"chapters": []}))

    reset_stages(files, "transcript", "lecture-05-en")

    assert json.loads(files["transcript"].read_text()) == "shared transcript"
    assert files["video"].exists()
    assert not files["chapters"].exists()


def test_share_from_without_redo_keeps_an_existing_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    src = settings.lessons_dir / "lecture-05-en"
    src.mkdir(parents=True)
    src.joinpath("transcript.json").write_text(json.dumps("shared transcript"))
    out = settings.lessons_dir / "lecture-05-video"
    out.mkdir(parents=True)
    files = {s: out / f"{s}.json" for s in ("video", "transcript", "chapters")}
    files["transcript"].write_text(json.dumps("own transcript"))
    reset_stages(files, None, "lecture-05-en")
    assert json.loads(files["transcript"].read_text()) == "own transcript"


# --- 7. resume: a new video session without a position hides older ones ----------------------


def test_resume_is_null_once_a_newer_video_session_has_no_position_yet(client, session, video_lesson):
    save_video_position(client, session["id"], 250_000)
    assert resume(client, session["guest_id"]).json()["session_id"] == session["id"]
    # "Start over": a new session that hasn't saved a position yet
    create_session(client, session["guest_id"], video_lesson.slug)
    assert resume(client, session["guest_id"]).json() is None


def test_resume_of_a_scene_lesson_still_falls_back_to_the_older_session(client, lesson):
    guest = create_guest(client)
    first = create_session(client, guest, lesson.slug)
    client.post(f"/api/sessions/{first['id']}/position", json={"scene_idx": 1, "step_idx": 1})
    create_session(client, guest, lesson.slug)
    assert resume(client, guest, lesson.slug).json() == {"session_id": first["id"], "scene_idx": 1, "step_idx": 1}
