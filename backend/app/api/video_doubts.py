"""Live doubts of a video lesson: STT -> classify against the chapters -> ack -> thread lifecycle.

The answer itself streams from GET /api/doubts/{id}/answer (questions.py), which builds the video prompt with
`video_answer_prompt` below. Thread policy (turns, cap, recap) lives in app.services.threads.
"""

import asyncio
import re
import time

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
    VIDEO_GOT_IT_LINE,
    VIDEO_SOLUTION_LATER,
    VIDEO_SORRY_LINE,
)
from app.providers import get_stt
from app.routing import is_understood
from app.schemas import Classification, SolutionIn, Spoken, ThreadAbandonIn, ThreadOut, ThreadReplyIn, VideoQuestionOut
from app.services.threads import (
    ThreadPolicy,
    schedule_recap,
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
    "reply": "now",
    "overview": "now",
    "solution": "now",
    "continue": "none",
    "off_topic": "none",
    "unclear": "none",
}
# These suggest the student hasn't got the current chapter's concept yet
STRUGGLE_INTENTS = {"confused", "repeat", "prerequisite"}
# Asked from a checkpoint's follow-up step: anything on topic is re-explained right away
KEEP_DURING_CHECKPOINT = {"off_topic", "unclear", "continue", "solution"}
# Answered without using one of the thread's follow-up turns: a reply to the tutor's own question isn't a new doubt
NO_TURN_INTENTS = {"reply"}
# A rewritten "question" that describes the student instead of quoting them is replaced by what STT heard
META_QUESTION = re.compile(r"^\s*(the student|student's|the doubt|this doubt|unclear|not understandable)", re.I)
# What a press of the Solution button is stored as
SOLUTION_REQUEST = "Show me the step-by-step solution."
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


# A promised doubt is answered at least this long before the video ends (the player can't stop on its last frame)
END_MARGIN_MS = 1500


def deliver_ms(lesson: Lesson, chapter_idx: int) -> int:
    """When a promised (covered_later) doubt is answered: at the end of its target chapter, once the teacher has
    taught it, or at that chapter's checkpoint if it comes first (the doubt is answered before the quiz on it)."""
    chapter = chapter_of(lesson, chapter_idx)
    at = min([chapter.end_ms, *(cp.at_ms for cp in lesson.checkpoints if cp.chapter_idx == chapter_idx and cp.at_ms > chapter.start_ms)])
    if lesson.duration_ms:
        at = min(at, lesson.duration_ms - END_MARGIN_MS)
    return max(at, chapter.start_ms)


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
    return history_text(db.scalars(q).all())


def history_text(rows: list[Doubt]) -> str:
    """`thread_history` for these doubts (also used by scripts/book_context_eval.py for unsaved doubts)."""
    if not rows:
        return ""
    lines = []
    for d in rows:
        answer = " ".join(a["say"] for a in d.answer) if d.answer else "(not answered)"
        heard = f' (student said: "{d.said}")' if d.said and d.said != d.question else ""
        lines.append(f"- Doubt: {d.question}{heard} ({d.intent})\n  Tutor answered: {answer}")
    last = tutor_last_line(rows)
    return (
        "Earlier doubts in this thread (oldest first):\n" + "\n".join(lines) + "\n\n"
        + (f'Tutor\'s last line to the student: "{last}"\n\n' if last else "")
    )


def tutor_last_line(rows: list[Doubt]) -> str:
    """The last thing the tutor said in the thread (its closing question, usually)."""
    for d in reversed(rows):
        if d.answer:
            return d.answer[-1]["say"]
    return ""


def checkpoint_on_screen(
    db: Session, session_id: str, checkpoint_idx: int, question_idx: int, q: dict, decided: bool = True
) -> str:
    """The checkpoint question the student is looking at, options lettered as on screen, for routing a doubt.
    The correct option is left out while the student may still answer it."""
    letters = "ABCD"
    picked = ", ".join(letters[i] for i in checkpoint_picks(db, session_id, checkpoint_idx, question_idx) if i < len(q["options"]))
    answer = f"correct: {letters[q['correct_index']]}" if decided else "not answered yet"
    return (
        f"Checkpoint question on screen: {q['question']}\n"
        + "".join(f"{letters[i]}. {o}\n" for i, o in enumerate(q["options"]))
        + f"Student picked (wrong): {picked or 'none'}; {answer}\n\n"
    )


def question_decided(db: Session, session_id: str, checkpoint_idx: int, question_idx: int, q: dict) -> bool:
    """Whether the student is done answering a checkpoint question: right, out of tries, revealed or skipped."""
    rows = db.scalars(
        select(CheckpointAnswer).filter_by(session_id=session_id, checkpoint_idx=checkpoint_idx, question_idx=question_idx)
    ).all()
    if any(r.correct or r.kind in ("retry", "skip") for r in rows):
        return True
    # A wrong first pick on a question without hints shows the answer at once
    return any(r.kind == "first" for r in rows) and not any(q.get("why_wrong") or [])


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
    # "yes / got it / continue" after an answer in this pause: no LLM call
    if was_open and is_understood(transcript) and last_answered(db, thread):
        return Classification(intent="continue", question=transcript)
    user = routing_prompt(db, sess, chapter_idx, at_ms, thread, transcript, on_screen)
    cls = await llm.complete_json(VIDEO_CLASSIFY_SYSTEM, user, Classification, temperature=0.2, model=settings.routing_deployment)
    if cls.intent == "unclear" or not cls.question.strip() or META_QUESTION.match(cls.question):
        cls = cls.model_copy(update={"question": transcript})
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
    if cls.intent == "solution" and answer_when == "none":
        return await safe_line(VIDEO_SOLUTION_LATER, voice, doubt_id=doubt_id)
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
    sess = video_session(db, session_id)
    started = time.perf_counter()
    stt = get_stt()
    wav, duration_ms = await asyncio.to_thread(to_wav, await audio.read(), STT_RATE)
    if duration_ms > stt.max_seconds * 1000:
        raise HTTPException(400, f"Recording longer than {stt.max_seconds}s")
    transcript = await transcribe(wav, sess.lesson.language, session_id=sess.id) if duration_ms > 300 else ""
    return await route_question(db, sess, background, started, at_ms, thread_id, checkpoint_idx, question_idx, transcript)


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_idx}/solution", response_model=VideoQuestionOut)
async def ask_for_solution(
    session_id: str, checkpoint_idx: int, body: SolutionIn, background: BackgroundTasks, db: Session = Depends(get_db)
):
    """The Solution button at a checkpoint question: routed like a spoken "show me the solution"."""
    sess = video_session(db, session_id)
    cp, q = checkpoint_question(sess.lesson, checkpoint_idx, body.question_idx)
    if q is None:
        raise HTTPException(404, "Checkpoint question not found")
    request = Classification(intent="solution", question=SOLUTION_REQUEST, confidence=1.0)
    return await route_question(
        db, sess, background, time.perf_counter(), cp.at_ms, body.thread_id, checkpoint_idx, body.question_idx, "", request
    )


def video_session(db: Session, session_id: str) -> TutorSession:
    sess = get_session(db, session_id)
    if sess.lesson.kind != "video":
        raise HTTPException(400, "Not a video lesson")
    return sess


async def route_question(
    db: Session,
    sess: TutorSession,
    background: BackgroundTasks,
    started: float,
    at_ms: int,
    thread_id: str | None,
    checkpoint_idx: int | None,
    question_idx: int | None,
    transcript: str,
    request: Classification | None = None,
) -> VideoQuestionOut:
    """Route a doubt (heard as `transcript`, or a `request` from a button) and run its thread's lifecycle."""
    lesson = sess.lesson
    policy = ThreadPolicy.from_settings()
    voice = lesson.voice

    cp, q = checkpoint_question(lesson, checkpoint_idx, question_idx)
    decided = question_decided(db, sess.id, cp.idx, question_idx, q) if q else True
    on_screen = checkpoint_on_screen(db, sess.id, cp.idx, question_idx, q, decided) if q else ""
    chapter_idx = cp.chapter_idx if cp else chapter_at(lesson, at_ms)
    thread = get_open_thread(db, sess, thread_id)
    was_open = thread is not None
    if thread is None:
        thread = open_thread(db, sess, at_ms, chapter_idx)

    cls = request or await classify(db, sess, chapter_idx, at_ms, thread, was_open, transcript, on_screen)
    if cls.intent == "solution" and not q:
        cls = cls.model_copy(update={"intent": "not_covered"})  # no checkpoint question to solve: answer the doubt
    answer_when = ANSWER_WHEN[cls.intent]
    if cls.intent == "solution" and not decided:
        answer_when = "none"  # not before the student has answered it (VIDEO_SOLUTION_LATER)
    if cls.intent == "covered_later" and was_open:
        # The student is already paused: answer it now instead of promising a callback
        answer_when = "now"
        cls = cls.model_copy(update={"target_scene_idx": None, "callback": None})
    # The cap applies only to answerable doubts; a capped one is still stored, with no turn used
    uses_turn = cls.intent not in NO_TURN_INTENTS
    capped = answer_when == "now" and uses_turn and not can_follow_up(thread.turns, policy)
    if capped:
        answer_when = "none"
    turn = thread.turns
    if answer_when == "now" and uses_turn:
        thread.turns += 1

    doubt = Doubt(
        session_id=sess.id,
        scene_idx=chapter_idx,
        step_idx=0,
        question=cls.question or transcript,
        said=transcript,
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
    if cls.intent == "solution" and answer_when == "now" and q.get("solution"):
        doubt.answer = q["solution"]  # prepared and checked in prep (app.prep.solutions): streamed from storage
    db.add(doubt)
    concept = mastery_concept(lesson, chapter_idx)
    if cls.intent in STRUGGLE_INTENTS and concept:
        update_mastery(db, sess.guest_id, concept, doubt_mastery, attempt=False)
    touch(sess)
    db.commit()

    # Everything is committed: no row stays locked while the lines are synthesized or the recap is made.
    # A follow-up answered now continues the answer on the board, with no canned line in between
    # (except that a later chapter's topic is answered early, which the student should hear).
    follow_up = answer_when == "now" and turn > 0 and cls.intent != "covered_later"
    # "Got it" in a pause goes straight to the recap or the video: no "Okay, let's continue." first
    moving_on = cls.intent == "continue" and was_open
    ack_spoken = None if follow_up or moving_on else await ack_for(cls, answer_when, doubt.id, voice, capped=capped)

    callback = None
    target_ms = None
    deliver_at = None
    replay_from_ms = None
    if answer_when == "now":
        prompt = await safe_line(VIDEO_GOT_IT_LINE, voice, session_id=sess.id, thread_id=thread.id)
        out = thread_out(thread, policy, prompt=prompt)
    elif capped:
        line = await safe_line(VIDEO_CAPPED_LINE, voice, session_id=sess.id, thread_id=thread.id)
        out = await close_thread(db, thread, "capped", lesson, line=line)
    elif cls.intent == "covered_later":
        # Not needed until the target chapter starts, so synthesize it after responding
        background.add_task(speak, cls.callback, callback_path(doubt.id), voice)
        callback = callback_spoken(doubt)
        target_ms = chapter_of(lesson, cls.target_scene_idx).start_ms
        deliver_at = deliver_ms(lesson, cls.target_scene_idx)
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
        ms=int((time.perf_counter() - started) * 1000),
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
        deliver_ms=deliver_at,
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


def promised(doubt: Doubt) -> bool:
    """A covered_later doubt waiting for (or answered after) its target chapter, not one answered right away."""
    return doubt.intent == "covered_later" and doubt.target_scene_idx is not None


@router.post("/doubts/{doubt_id}/deliver", response_model=ThreadOut)
async def deliver_doubt(doubt_id: str, db: Session = Depends(get_db)):
    """A promised doubt is due: the video is paused after its target chapter. The doubt moves to a new open thread
    there, as its first answered turn, so its answer, "Got it?" and follow-ups work as for a doubt answered now."""
    doubt = db.get(Doubt, doubt_id)
    if not doubt:
        raise HTTPException(404, "Doubt not found")
    if not promised(doubt):
        raise HTTPException(400, "Not a promised doubt")
    sess = db.get(TutorSession, doubt.session_id)
    lesson = sess.lesson
    thread = open_thread(db, sess, deliver_ms(lesson, doubt.target_scene_idx), doubt.target_scene_idx)
    thread.turns = 1
    if doubt.answer:  # prefetched while the chapter played: its audio moves to this thread (for its recap)
        audio = sum(r["duration_ms"] for r in doubt.answer)
        thread.audio_ms = audio
        earlier = db.get(DoubtThread, doubt.thread_id) if doubt.thread_id else None
        if earlier:
            earlier.audio_ms = max(0, earlier.audio_ms - audio)
    doubt.thread_id = thread.id
    touch(sess)
    db.commit()
    log_event("doubt.delivered", session_id=sess.id, thread_id=thread.id, doubt_id=doubt.id, at_ms=thread.at_ms)
    if doubt.answer:
        schedule_recap(thread.id)
    prompt = await safe_line(VIDEO_GOT_IT_LINE, lesson.voice, session_id=sess.id, thread_id=thread.id)
    return thread_out(thread, ThreadPolicy.from_settings(), prompt=prompt)


# ---- answer prompt (used by questions.stream_answer for video lessons) ----


def checkpoint_context(db: Session, doubt: Doubt, lesson: Lesson) -> str:
    """The checkpoint question the doubt was asked from, with the student's wrong picks."""
    _, q = checkpoint_question(lesson, doubt.checkpoint_idx, doubt.question_idx)
    if not q:
        return ""
    options = q["options"]
    picked = checkpoint_picks(db, doubt.session_id, doubt.checkpoint_idx, doubt.question_idx)
    if not question_decided(db, doubt.session_id, doubt.checkpoint_idx, doubt.question_idx, q):
        return (
            "Checkpoint question on the board, not answered yet (the student may still pick an option):\n"
            f"Question: {q['question']}\n"
            + "".join(f"{'ABCD'[i]}. {o}\n" for i, o in enumerate(options))
            + f"Wrong picks so far: {'; '.join(options[i] for i in picked if 0 <= i < len(options)) or 'none'}\n\n"
        )
    return (
        "Checkpoint question the student just got wrong:\n"
        f"Question: {q['question']}\n"
        f"Student picked: {'; then '.join(options[i] for i in picked if 0 <= i < len(options)) or 'nothing yet'}\n"
        f"Correct option: {options[q['correct_index']]}\n"
        f"Why: {q['explanation']}\n\n"
    )


PASSAGE_START = re.compile(r"^(\d+):(\d\d)")


def render_marked(passages: list, at_ms: int | None) -> str:
    """`render`, with transcript passages that start after the pause marked as not watched yet."""
    out = []
    for p in passages:
        block = render([p])
        m = PASSAGE_START.match(p.ref) if p.source == "transcript" and at_ms is not None else None
        if m and (int(m.group(1)) * 60 + int(m.group(2))) * 1000 >= at_ms:
            head, _, body = block.partition("\n")
            block = f"{head}\n(after the pause: the student has not watched this yet)\n{body}"
        out.append(block)
    return "\n\n".join(out)


def chapter_list(lesson: Lesson, at_ms: int) -> str:
    """Every chapter with its summary, marked watched / playing / not watched yet."""
    lines = []
    for c in lesson.chapters:
        state = "watched" if c.end_ms <= at_ms else "playing at the pause" if c.start_ms <= at_ms else "not watched yet"
        lines.append(f"- Chapter {c.idx} [{mmss(c.start_ms)}-{mmss(c.end_ms)}, {state}]: {c.title}. {c.summary}")
    return "Lecture chapters:\n" + "\n".join(lines) + "\n\n"


async def answer_context(lesson: Lesson, doubt: Doubt, at_ms: int | None, chapter_idx: int) -> str:
    """Passages from the configured sources around `at_ms`; the raw transcript if the context service fails."""
    try:
        passages = await get_context_service().gather(ContextQuery(lesson.id, doubt.question, at_ms, chapter_idx))
        text = render_marked(passages, at_ms)
    except Exception as e:  # noqa: BLE001 - answer from the transcript rather than not at all
        log_event(
            "context.failed", level="warn", session_id=doubt.session_id, doubt_id=doubt.id, error=f"{type(e).__name__}: {e}"[:300]
        )
        text = ""
    return text or lesson.transcript[: settings.context_max_chars]


async def video_answer_prompt(db: Session, doubt: Doubt, sess: TutorSession) -> str:
    lesson = sess.lesson
    guest = sess.guest
    # A promised doubt is answered after its target chapter: the context is that chapter, now watched
    later = promised(doubt)
    chapter_idx = doubt.target_scene_idx if later else doubt.scene_idx
    at_ms = deliver_ms(lesson, chapter_idx) if later else doubt.at_ms or 0
    chapter = chapter_of(lesson, chapter_idx)
    return (
        f"Student: {guest.name}, class {guest.class_level or 'unknown'}, "
        f"weak concepts so far: {', '.join(guest.weak_concepts) or 'none'}\n"
        f"Doubt intent: {doubt.intent}\nDoubt: {doubt.question}\n"
        + (f'Student said (speech-to-text): "{doubt.said}"\n' if doubt.said and doubt.said != doubt.question else "")
        + f"Turn in this pause: {doubt.turn}\n"
        + (
            f"Answered later: the student asked this at {mmss(doubt.at_ms or 0)} and I promised to come back to it. "
            f"They have just watched chapter {chapter_idx}, which covers it, and the video is paused at its end. "
            f'The callback line ("{doubt.callback}") has just been said.\n'
            if later
            else ""
        )
        + f"\nCurrent chapter {chapter_idx}: {chapter.title if chapter else lesson.title}\n"
        f"Summary: {chapter.summary if chapter else ''}\n"
        f"Video paused at {mmss(at_ms)}\n\n"
        f"{chapter_list(lesson, at_ms)}"
        f"{thread_history(db, doubt.thread_id, before=doubt)}"
        f"{checkpoint_context(db, doubt, lesson)}"
        f"Lecture context:\n{await answer_context(lesson, doubt, at_ms, chapter_idx)}"
    )
