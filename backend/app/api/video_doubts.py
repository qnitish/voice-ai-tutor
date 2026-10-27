"""Live doubts of a video lesson: STT -> classify against the chapters -> ack -> thread lifecycle.

The answer itself streams from GET /api/doubts/{id}/answer (questions.py), which builds the video prompt with
`video_answer_prompt` below. Thread policy (turns, cap, recap) lives in app.services.threads.
"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.api.sessions import callback_path, callback_spoken, get_session, touch, update_mastery
from app.audio import STT_RATE, speak, to_wav, transcribe
from app.config import settings
from app.context.base import ContextQuery
from app.context.service import get_context_service, render
from app.db import Checkpoint, CheckpointAnswer, Doubt, DoubtThread, Lesson, TutorSession, get_db
from app.progress import doubt_mastery
from app.prompts import (
    VIDEO_ACK_COVERED_NOW,
    VIDEO_ACK_NEUTRAL,
    VIDEO_ACKS,
    VIDEO_CAPPED_LINE,
    VIDEO_CLASSIFY_SYSTEM,
    VIDEO_FOLLOWUP_LINE,
    VIDEO_SORRY_LINE,
)
from app.providers import get_stt
from app.routing import is_affirmative
from app.schemas import Classification, Spoken, ThreadAbandonIn, ThreadOut, ThreadReplyIn, VideoQuestionOut
from app.services.threads import (
    ThreadPolicy,
    can_follow_up,
    chapter_of,
    close_thread,
    get_open_thread,
    open_thread,
    safe_line,
    thread_out,
    transcript_window,
)
from app.telemetry import Timer, log_event

router = APIRouter(prefix="/api")

ANSWER_WHEN = {
    "covered_later": "target_chapter",
    "not_covered": "now",
    "prerequisite": "now",
    "confused": "now",
    "repeat": "replay",
    "continue": "none",
    "off_topic": "none",
    "unclear": "none",
}
# These suggest the student hasn't got the current chapter's concept yet
STRUGGLE_INTENTS = {"confused", "repeat", "prerequisite"}
# Asked from a checkpoint's follow-up step: anything on topic is re-explained right away
KEEP_DURING_CHECKPOINT = {"off_topic", "unclear", "continue"}
# "repeat" seeks back this far (not before the chapter start)
REPLAY_MS = 30_000
ROUTING_WINDOW_MS = 60_000


def mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def chapter_at(lesson: Lesson, at_ms: int) -> int:
    """Index of the chapter playing at `at_ms` (the last one started; 0 before the first)."""
    idx = 0
    for c in lesson.chapters:
        if c.start_ms <= at_ms:
            idx = c.idx
    return idx


def checkpoint_question(lesson: Lesson, checkpoint_idx: int | None, question_idx: int | None) -> tuple[Checkpoint | None, dict | None]:
    cp = next((c for c in lesson.checkpoints if c.idx == checkpoint_idx), None) if checkpoint_idx is not None else None
    if cp is None or question_idx is None or not (0 <= question_idx < len(cp.questions)):
        return cp, None
    return cp, cp.questions[question_idx]


def checkpoint_picks(db: Session, session_id: str, checkpoint_idx: int, question_idx: int) -> list[int]:
    """Options the student picked for a checkpoint question: first answer, then retry."""
    rows = db.scalars(
        select(CheckpointAnswer)
        .filter_by(session_id=session_id, checkpoint_idx=checkpoint_idx, question_idx=question_idx)
        .filter(CheckpointAnswer.kind.in_(["first", "retry"]))
        .order_by(CheckpointAnswer.created_at)
    ).all()
    return [r.chosen_index for r in rows if r.chosen_index >= 0]


def thread_history(db: Session, thread_id: str | None, before: Doubt | None = None) -> str:
    """The thread's earlier doubts with what the tutor answered, oldest first."""
    if not thread_id:
        return ""
    q = select(Doubt).filter_by(thread_id=thread_id).order_by(Doubt.created_at)
    if before is not None:
        q = q.filter(Doubt.created_at < before.created_at)
    rows = db.scalars(q).all()
    if not rows:
        return ""
    lines = []
    for d in rows:
        said = " ".join(a["say"] for a in d.answer) if d.answer else "(not answered)"
        lines.append(f"- Doubt: {d.question} ({d.intent})\n  Tutor answered: {said}")
    return "Earlier doubts in this thread (oldest first):\n" + "\n".join(lines) + "\n\n"


def checkpoint_on_screen(db: Session, session_id: str, checkpoint_idx: int, question_idx: int, q: dict) -> str:
    """The checkpoint card the student is looking at, options lettered as on screen, for routing a follow-up."""
    letters = "ABCD"
    picked = ", ".join(letters[i] for i in checkpoint_picks(db, session_id, checkpoint_idx, question_idx) if i < len(q["options"]))
    return (
        f"Checkpoint question on screen: {q['question']}\n"
        + "".join(f"{letters[i]}. {o}\n" for i, o in enumerate(q["options"]))
        + f"Student picked (wrong): {picked or 'none'}; correct: {letters[q['correct_index']]}\n\n"
    )


def routing_prompt(
    db: Session, sess: TutorSession, chapter_idx: int, at_ms: int, thread: DoubtThread, transcript: str, on_screen: str = ""
) -> str:
    lesson = sess.lesson
    chapters = "\n".join(
        f"Chapter {c.idx} [{mmss(c.start_ms)}-{mmss(c.end_ms)}]: {c.title}. Key terms: {', '.join(c.key_terms)}"
        for c in lesson.chapters
    )
    current = chapter_of(lesson, chapter_idx)
    return (
        f"current_chapter_idx: {chapter_idx} ({current.title if current else 'unknown'}), paused at {mmss(at_ms)}\n\n"
        f"Lecture chapters:\n{chapters}\n\n"
        f"Transcript of the last minute before the pause: {transcript_window(lesson.transcript, at_ms, ROUTING_WINDOW_MS)}\n\n"
        f"{thread_history(db, thread.id)}"
        f"{on_screen}"
        f'Student said (speech-to-text): "{transcript}"'
    )


def validate(cls: Classification, chapter_idx: int, lesson: Lesson) -> Classification:
    """Code owns the pointer: a lookahead target must really be a later chapter. No clarify in video routing."""
    if cls.intent == "clarify":
        cls = cls.model_copy(update={"intent": "not_covered"})
    if cls.intent == "covered_later" and not (
        cls.target_scene_idx is not None
        and chapter_idx < cls.target_scene_idx < len(lesson.chapters)
        and cls.callback
    ):
        cls = cls.model_copy(update={"intent": "not_covered"})
    if cls.intent != "covered_later":
        cls = cls.model_copy(update={"target_scene_idx": None, "callback": None})
    return cls


def last_answered(db: Session, thread: DoubtThread) -> bool:
    last = db.scalars(select(Doubt).filter_by(thread_id=thread.id).order_by(Doubt.created_at.desc()).limit(1)).first()
    return last is not None and last.answer is not None


async def classify(
    db: Session,
    sess: TutorSession,
    chapter_idx: int,
    at_ms: int,
    thread: DoubtThread,
    was_open: bool,
    transcript: str,
    on_screen: str = "",
) -> Classification:
    if not transcript:
        return Classification(intent="unclear", question="")
    # "yes / got it" after an answer in this pause: no LLM call
    if was_open and is_affirmative(transcript) and last_answered(db, thread):
        return Classification(intent="continue", question=transcript)
    user = routing_prompt(db, sess, chapter_idx, at_ms, thread, transcript, on_screen)
    cls = await llm.complete_json(VIDEO_CLASSIFY_SYSTEM, user, Classification, temperature=0.2, model=settings.routing_deployment)
    if on_screen and cls.intent not in KEEP_DURING_CHECKPOINT:
        # On topic after a wrong checkpoint answer: re-explain now
        cls = cls.model_copy(update={"intent": "confused"})
    return validate(cls, chapter_idx, sess.lesson)


def mastery_concept(lesson: Lesson, chapter_idx: int) -> str | None:
    """The concept of the first question of the checkpoint on this chapter, if any."""
    cp = next((c for c in lesson.checkpoints if c.chapter_idx == chapter_idx and c.questions), None)
    return cp.questions[0].get("concept") if cp else None


async def ack_for(cls: Classification, answer_when: str, doubt_id: str, voice: str | None, capped: bool = False) -> Spoken:
    """The fixed ack for a routed doubt (cached audio; text only if TTS fails, see services.threads.safe_line).

    A capped doubt gets a neutral ack: the thread's capped line follows it, so the intent's ack ("let me explain
    it another way", "I'll come back to it") would promise something that isn't going to happen.
    """
    if capped:
        return await safe_line(VIDEO_ACK_NEUTRAL, voice, doubt_id=doubt_id)
    if cls.intent == "off_topic" and cls.ack:
        try:
            return await speak(cls.ack, f"doubts/{doubt_id}_ack.wav", voice)
        except Exception as e:  # noqa: BLE001 - the fixed line is always there
            log_event("ack.failed", level="warn", doubt_id=doubt_id, error=f"{type(e).__name__}: {e}"[:300])
    if cls.intent == "covered_later" and answer_when == "now":
        return await safe_line(VIDEO_ACK_COVERED_NOW, voice, doubt_id=doubt_id)
    return await safe_line(VIDEO_ACKS[cls.intent], voice, doubt_id=doubt_id)


@router.post("/sessions/{session_id}/video/questions", response_model=VideoQuestionOut)
async def ask_video_question(
    session_id: str,
    audio: UploadFile,
    background: BackgroundTasks,
    at_ms: int = Form(ge=0),
    thread_id: str | None = Form(None),
    checkpoint_idx: int | None = Form(None),
    question_idx: int | None = Form(None),
    db: Session = Depends(get_db),
):
    sess = get_session(db, session_id)
    lesson = sess.lesson
    if lesson.kind != "video":
        raise HTTPException(400, "Not a video lesson")
    policy = ThreadPolicy.from_settings()
    voice = lesson.voice

    with Timer() as timer:
        stt = get_stt()
        wav, duration_ms = await asyncio.to_thread(to_wav, await audio.read(), STT_RATE)
        if duration_ms > stt.max_seconds * 1000:
            raise HTTPException(400, f"Recording longer than {stt.max_seconds}s")
        transcript = await transcribe(wav, lesson.language, session_id=sess.id) if duration_ms > 300 else ""

        cp, q = checkpoint_question(lesson, checkpoint_idx, question_idx)
        on_screen = checkpoint_on_screen(db, sess.id, cp.idx, question_idx, q) if q else ""
        chapter_idx = cp.chapter_idx if cp else chapter_at(lesson, at_ms)
        thread = get_open_thread(db, sess, thread_id)
        was_open = thread is not None
        if thread is None:
            thread = open_thread(db, sess, at_ms, chapter_idx)

        cls = await classify(db, sess, chapter_idx, at_ms, thread, was_open, transcript, on_screen)
        answer_when = ANSWER_WHEN[cls.intent]
        if cls.intent == "covered_later" and was_open:
            # The student is already paused: answer it now instead of promising a callback
            answer_when = "now"
            cls = cls.model_copy(update={"target_scene_idx": None, "callback": None})
        # The cap applies only to answerable doubts; a capped one is still stored, with no turn used
        capped = answer_when == "now" and not can_follow_up(thread.turns, policy)
        if capped:
            answer_when = "none"
        turn = thread.turns
        if answer_when == "now":
            thread.turns += 1

        doubt = Doubt(
            session_id=sess.id,
            scene_idx=chapter_idx,
            step_idx=0,
            question=cls.question or transcript,
            intent=cls.intent,
            target_scene_idx=cls.target_scene_idx,
            callback=cls.callback,
            confidence=cls.confidence,
            at_ms=at_ms,
            thread_id=thread.id,
            turn=turn,
            checkpoint_idx=cp.idx if cp else None,
            question_idx=question_idx if q else None,
        )
        db.add(doubt)
        concept = mastery_concept(lesson, chapter_idx)
        if cls.intent in STRUGGLE_INTENTS and concept:
            update_mastery(db, sess.guest_id, concept, doubt_mastery, attempt=False)
        touch(sess)
        db.commit()

        # Everything is committed: no row stays locked while the lines are synthesized or the recap is made
        ack_spoken = await ack_for(cls, answer_when, doubt.id, voice, capped=capped)

        callback = None
        target_ms = None
        replay_from_ms = None
        if answer_when == "now":
            out = thread_out(thread, policy)
        elif capped:
            line = await safe_line(VIDEO_CAPPED_LINE, voice, session_id=sess.id, thread_id=thread.id)
            out = await close_thread(db, thread, "capped", lesson, line=line)
        elif cls.intent == "covered_later":
            # Not needed until the target chapter starts, so synthesize it after responding
            background.add_task(speak, cls.callback, callback_path(doubt.id), voice)
            callback = callback_spoken(doubt)
            target_ms = chapter_of(lesson, cls.target_scene_idx).start_ms
            out = await close_thread(db, thread, "resolved", lesson)
        elif cls.intent == "repeat":
            chapter = chapter_of(lesson, chapter_idx)
            replay_from_ms = max(chapter.start_ms if chapter else 0, at_ms - REPLAY_MS)
            out = await close_thread(db, thread, "resolved", lesson)
        elif cls.intent == "unclear" or (cls.intent == "off_topic" and was_open):
            out = thread_out(thread, policy)  # still open, no turn used: the player shows "Got it?" again
        else:
            out = await close_thread(db, thread, "resolved", lesson)

    log_event(
        "route.done",
        session_id=sess.id,
        thread_id=thread.id,
        doubt_id=doubt.id,
        ms=timer.ms,
        intent=cls.intent,
        confidence=cls.confidence,
        answer_when=answer_when,
        turn=turn,
    )
    return VideoQuestionOut(
        doubt_id=doubt.id,
        thread_id=thread.id,
        turn=turn,
        transcript=transcript,
        question=doubt.question,
        intent=cls.intent,
        answer_when=answer_when,
        target_chapter_idx=cls.target_scene_idx,
        target_ms=target_ms,
        replay_from_ms=replay_from_ms,
        ack=ack_spoken,
        callback=callback,
        thread=out,
    )


def thread_or_404(db: Session, thread_id: str) -> DoubtThread:
    thread = db.get(DoubtThread, thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    return thread


@router.post("/threads/{thread_id}/reply", response_model=ThreadOut)
async def reply_thread(thread_id: str, body: ThreadReplyIn, db: Session = Depends(get_db)):
    """"Got it" closes the thread; "Ask another" keeps it open while follow-ups are allowed, else caps it."""
    thread = thread_or_404(db, thread_id)
    policy = ThreadPolicy.from_settings()
    if thread.status != "open":
        return thread_out(thread, policy)
    sess = db.get(TutorSession, thread.session_id)
    lesson = sess.lesson
    touch(sess)
    # Committed before anything is awaited: the session row's UPDATE must not hold its lock across TTS/LLM calls
    db.commit()
    ids = dict(session_id=sess.id, thread_id=thread.id)
    if body.understood:
        return await close_thread(db, thread, "resolved", lesson)
    if can_follow_up(thread.turns, policy):
        return thread_out(thread, policy, line=await safe_line(VIDEO_FOLLOWUP_LINE, lesson.voice, **ids))
    line = await safe_line(VIDEO_CAPPED_LINE, lesson.voice, **ids)
    return await close_thread(db, thread, "capped", lesson, line=line)


@router.post("/threads/{thread_id}/abandon", response_model=ThreadOut)
async def abandon_thread(thread_id: str, body: ThreadAbandonIn | None = None, db: Session = Depends(get_db)):
    """The "Got it?" prompt timed out, or answering failed (reason "error": the sorry line is returned)."""
    thread = thread_or_404(db, thread_id)
    policy = ThreadPolicy.from_settings()
    if thread.status != "open":
        return thread_out(thread, policy)
    sess = db.get(TutorSession, thread.session_id)
    lesson = sess.lesson
    touch(sess)
    db.commit()  # see reply_thread
    reason = body.reason if body else "idle"
    line = None
    if reason == "error":
        line = await safe_line(VIDEO_SORRY_LINE, lesson.voice, session_id=sess.id, thread_id=thread.id)
    log_event("thread.abandoned", session_id=sess.id, thread_id=thread.id, reason=reason)
    return await close_thread(db, thread, "abandoned", lesson, line=line)


# ---- answer prompt (used by questions.stream_answer for video lessons) ----


def checkpoint_context(db: Session, doubt: Doubt, lesson: Lesson) -> str:
    """The checkpoint question the doubt was asked from, with the student's wrong picks."""
    _, q = checkpoint_question(lesson, doubt.checkpoint_idx, doubt.question_idx)
    if not q:
        return ""
    options = q["options"]
    picked = checkpoint_picks(db, doubt.session_id, doubt.checkpoint_idx, doubt.question_idx)
    return (
        "Checkpoint question the student just got wrong:\n"
        f"Question: {q['question']}\n"
        f"Student picked: {'; then '.join(options[i] for i in picked if 0 <= i < len(options)) or 'nothing yet'}\n"
        f"Correct option: {options[q['correct_index']]}\n"
        f"Why: {q['explanation']}\n\n"
    )


async def answer_context(lesson: Lesson, doubt: Doubt) -> str:
    """Passages from the configured sources; the raw transcript if the context service fails."""
    try:
        passages = await get_context_service().gather(
            ContextQuery(lesson.id, doubt.question, doubt.at_ms, doubt.scene_idx)
        )
        text = render(passages)
    except Exception as e:  # noqa: BLE001 - answer from the transcript rather than not at all
        log_event(
            "context.failed", level="warn", session_id=doubt.session_id, doubt_id=doubt.id, error=f"{type(e).__name__}: {e}"[:300]
        )
        text = ""
    return text or lesson.transcript[: settings.context_max_chars]


async def video_answer_prompt(db: Session, doubt: Doubt, sess: TutorSession) -> str:
    lesson = sess.lesson
    guest = sess.guest
    chapter = chapter_of(lesson, doubt.scene_idx)
    return (
        f"Student: {guest.name}, class {guest.class_level or 'unknown'}, "
        f"weak concepts so far: {', '.join(guest.weak_concepts) or 'none'}\n"
        f"Doubt intent: {doubt.intent}\nDoubt: {doubt.question}\n\n"
        f"Current chapter {doubt.scene_idx}: {chapter.title if chapter else lesson.title}\n"
        f"Summary: {chapter.summary if chapter else ''}\n"
        f"Video paused at {mmss(doubt.at_ms or 0)}\n\n"
        f"{thread_history(db, doubt.thread_id, before=doubt)}"
        f"{checkpoint_context(db, doubt, lesson)}"
        f"Lecture context:\n{await answer_context(lesson, doubt)}"
    )
