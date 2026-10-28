"""Live doubt handling: STT -> classify with lookahead -> spoken ack -> streamed answer."""

import asyncio
import json
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.api.sessions import callback_path, callback_spoken, get_session, touch, update_mastery
from app.api.video_doubts import video_answer_prompt
from app.audio import STT_RATE, speak, speak_cached, to_wav, transcribe
from app.config import settings
from app.db import CheckAttempt, CheckResult, Doubt, DoubtThread, Lesson, SessionLocal, TutorSession, get_db
from app.progress import doubt_mastery
from app.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, FIXED_ACKS, VIDEO_ANSWER_SYSTEM, clarify_ack, for_gender, line_for
from app.providers import get_stt
from app.routing import is_affirmative
from app.schemas import Classification, QuestionOut, Spoken, StepDraft, clean_draw
from app.services.threads import schedule_recap
from app.telemetry import log_event
from app.voices import get_voice

router = APIRouter(prefix="/api")

ANSWER_WHEN = {
    "covered_later": "target_scene",
    "not_covered": "scene_end",
    "prerequisite": "now",
    "confused": "now",
    "repeat": "replay",
    # The player moves on (or closes the check card); nothing to answer
    "continue": "none",
    "off_topic": "none",
    "unclear": "none",
    "clarify": "none",
    # Video-only intents; the scene router's prompt doesn't offer them
    "reply": "now",
    "overview": "now",
}
# Below this confidence, these intents are confirmed with the student before answering
CLARIFY_BELOW = 0.6
CLARIFIABLE = {"covered_later", "not_covered", "prerequisite", "confused"}
# These suggest the student hasn't got the current scene's concept yet
STRUGGLE_INTENTS = {"confused", "repeat", "prerequisite"}
RECENT_DOUBTS = 5
# Asked after a wrong check answer ("kahan confusion hua?"): anything on topic is re-explained right away
KEEP_DURING_CHECK = {"off_topic", "unclear", "continue"}
# Earlier doubts (with what was answered) shown to the answer, so a repeated doubt is noticed
ANSWER_HISTORY = 3
# A follow-up asked during an answer with one of these intents is answered right away, below that answer
FOLLOW_UP_NOW = {"covered_later", "not_covered", "prerequisite", "confused"}


def lesson_context(lesson: Lesson) -> str:
    """Every scene with its narration, so the LLM can look ahead in the lesson."""
    parts = []
    for s in lesson.scenes:
        says = " ".join(st.say for st in s.steps)
        parts.append(f"Scene {s.idx}: {s.title}\nGoal: {s.goal}\nKey terms: {', '.join(s.key_terms)}\nNarration: {says}")
    return "\n\n".join(parts)


def picks(db: Session, session_id: str, scene_idx: int) -> list[int]:
    """Options the student picked for a scene's check question: first answer, then retry."""
    first = db.scalars(select(CheckResult).filter_by(session_id=session_id, scene_idx=scene_idx)).first()
    if not first:
        return []
    retry = db.scalars(select(CheckAttempt).filter_by(session_id=session_id, scene_idx=scene_idx, kind="retry")).first()
    return [first.chosen_index] + ([retry.chosen_index] if retry else [])


def check_on_screen(db: Session, sess: TutorSession, scene_idx: int) -> str:
    """The check card the student is looking at, options lettered as on screen, for routing a follow-up."""
    scene = next((s for s in sess.lesson.scenes if s.idx == scene_idx), None)
    q = scene.check_question if scene else None
    if not q:
        return ""
    letters = "ABCD"
    picked = ", ".join(f"{letters[i]}" for i in picks(db, sess.id, scene_idx) if 0 <= i < len(q["options"]))
    return (
        f"Check question on screen: {q['question']}\n"
        + "".join(f"{letters[i]}. {o}\n" for i, o in enumerate(q["options"]))
        + f"Student picked (wrong): {picked or 'none'}; correct: {letters[q['correct_index']]}\n\n"
    )


def follow_up_chain(db: Session, doubt: Doubt) -> list[Doubt]:
    """The doubts this one follows up on, oldest first (the first asked during the lesson, then its follow-ups)."""
    chain: list[Doubt] = []
    parent_id = doubt.follow_up_of
    while parent_id and len(chain) < 5:
        parent = db.get(Doubt, parent_id)
        if not parent:
            break
        chain.append(parent)
        parent_id = parent.follow_up_of
    return chain[::-1]


def on_board(chain: list[Doubt]) -> str:
    """What the tutor said for each doubt in a follow-up chain; it is all still on the board."""
    lines = []
    for d in chain:
        said = " ".join(a["say"] for a in d.answer) if d.answer else "(still being answered)"
        lines.append(f"- Doubt: {d.question}\n  Tutor answered: {said}")
    return "\n".join(lines)


def routing_prompt(
    lesson: Lesson, scene_idx: int, step_idx: int, earlier: list[Doubt], transcript: str, on_screen: str = ""
) -> str:
    """Compact: every scene's title/goal/terms, but narration only for the current scene so far."""
    vocab = dict.fromkeys(t for s in lesson.scenes for t in s.key_terms)
    scenes = "\n".join(f"Scene {s.idx}: {s.title}. Goal: {s.goal}. Key terms: {', '.join(s.key_terms)}" for s in lesson.scenes)
    current = next((s for s in lesson.scenes if s.idx == scene_idx), None)
    narration = " ".join(st.say for st in current.steps[: step_idx + 1]) if current else ""
    doubts = "\n".join(f"- {d.question} ({d.intent})" for d in earlier) or "(none)"
    return (
        f"current_scene_idx: {scene_idx} (step {step_idx})\n"
        f"Lesson vocabulary: {', '.join(vocab)}\n\n"
        f"Lesson scenes:\n{scenes}\n\n"
        f"Current scene narration so far: {narration}\n\n"
        f"Earlier doubts in this session (oldest first):\n{doubts}\n\n"
        f"{on_screen}"
        f'Student said (speech-to-text): "{transcript}"'
    )


def validate(cls: Classification, scene_idx: int, lesson: Lesson) -> Classification:
    # Code, not the LLM, owns the lesson pointer: only accept a lookahead target that is really ahead
    if cls.intent == "covered_later" and not (
        cls.target_scene_idx is not None
        and scene_idx < cls.target_scene_idx < len(lesson.scenes)
        and cls.callback
    ):
        cls = cls.model_copy(update={"intent": "not_covered"})
    if cls.intent != "covered_later":
        cls = cls.model_copy(update={"target_scene_idx": None, "callback": None})
    return cls


async def classify(
    db: Session,
    sess: TutorSession,
    scene_idx: int,
    step_idx: int,
    transcript: str,
    during_check: bool = False,
    chain: list[Doubt] | None = None,
) -> tuple[Classification, bool]:
    """Route the doubt. Returns (classification, needs_clarify)."""
    earlier = db.scalars(
        select(Doubt).filter_by(session_id=sess.id).order_by(Doubt.created_at.desc()).limit(RECENT_DOUBTS)
    ).all()[::-1]
    # A pending "Kya tum yeh pooch rahe ho…?" is answered (or dropped) by this very next question
    pending = earlier[-1] if earlier and earlier[-1].intent == "clarify" and not earlier[-1].resolved else None
    if pending:
        pending.resolved = True
        if is_affirmative(transcript):
            return Classification.model_validate(pending.pending), False
    if not transcript:
        return Classification(intent="unclear", question=""), False
    on_screen = check_on_screen(db, sess, scene_idx) if during_check else ""
    user = routing_prompt(sess.lesson, scene_idx, step_idx, earlier, transcript, on_screen)
    if chain:
        user = (
            "The student is asking a FOLLOW-UP while the tutor is answering their doubt (the answer is on the board). "
            "\"Samajh nahi aaya\", \"kyun?\", \"matlab?\" etc. refer to that answer, so classify them as confused.\n"
            f"{on_board(chain)}\n\n{user}"
        )
    system = for_gender(CLASSIFY_SYSTEM, get_voice(sess.lesson.voice).gender, sess.lesson.language)
    cls = await llm.complete_json(system, user, Classification, temperature=0.2, model=settings.routing_deployment)
    return cls, cls.confidence < CLARIFY_BELOW and cls.intent in CLARIFIABLE


@router.post("/sessions/{session_id}/questions", response_model=QuestionOut)
async def ask_question(
    session_id: str,
    audio: UploadFile,
    background: BackgroundTasks,
    scene_idx: int = Form(),
    step_idx: int = Form(),
    during_check: bool = Form(False),
    follow_up_of: str | None = Form(None),
    db: Session = Depends(get_db),
):
    sess = get_session(db, session_id)
    lesson = sess.lesson
    parent = db.get(Doubt, follow_up_of) if follow_up_of else None
    if parent and parent.session_id != sess.id:
        parent = None
    if parent:
        # Answered where the doubt it follows was asked
        scene_idx, step_idx = parent.scene_idx, parent.step_idx
    stt = get_stt()
    wav, duration_ms = await asyncio.to_thread(to_wav, await audio.read(), STT_RATE)
    if duration_ms > stt.max_seconds * 1000:
        raise HTTPException(400, f"Recording longer than {stt.max_seconds}s")
    # The student is expected to speak the lesson's language
    transcript = await transcribe(wav, lesson.language, session_id=sess.id) if duration_ms > 300 else ""

    chain = follow_up_chain(db, Doubt(follow_up_of=parent.id)) if parent else []
    cls, needs_clarify = await classify(db, sess, scene_idx, step_idx, transcript, during_check, chain)
    pending = None
    if parent and cls.intent == "covered_later":
        # The student is already in the answer: no promise for later, answer it there
        cls = cls.model_copy(update={"intent": "not_covered"})
    if during_check and not parent and cls.intent not in KEEP_DURING_CHECK:
        # On topic after a wrong answer: re-explain now (still asking back first when unsure what they meant)
        cls = cls.model_copy(update={"intent": "confused"})
    if needs_clarify:
        pending = cls.model_dump()
        cls = Classification(intent="clarify", question=cls.question, confidence=cls.confidence)
    cls = validate(cls, scene_idx, lesson)

    doubt = Doubt(
        session_id=sess.id,
        scene_idx=scene_idx,
        step_idx=step_idx,
        question=cls.question or transcript,
        intent=cls.intent,
        target_scene_idx=cls.target_scene_idx,
        callback=cls.callback,
        confidence=cls.confidence,
        pending=pending,
        follow_up_of=parent.id if parent else None,
    )
    db.add(doubt)
    scene = next((s for s in lesson.scenes if s.idx == scene_idx), None)
    if cls.intent in STRUGGLE_INTENTS and scene and scene.check_question:
        update_mastery(db, sess.guest_id, scene.check_question["concept"], doubt_mastery, attempt=False)
    touch(sess)
    db.commit()

    voice = lesson.voice
    answer_when = ANSWER_WHEN[cls.intent]
    if parent and cls.intent in FOLLOW_UP_NOW:
        answer_when = "now"
    if parent and answer_when == "now":
        # Continues the answer on the board: no canned ack in between
        ack_spoken = None
    elif cls.intent == "clarify":
        ack_spoken = await speak(clarify_ack(doubt.question, lesson.language), f"doubts/{doubt.id}_ack.wav", voice)
    elif cls.intent == "off_topic" and cls.ack:
        ack_spoken = await speak(cls.ack, f"doubts/{doubt.id}_ack.wav", voice)
    else:
        ack_spoken = await speak_cached(line_for(FIXED_ACKS[cls.intent], get_voice(voice).gender, lesson.language), voice)

    callback = None
    if cls.callback:
        # Not needed until a later scene starts, so synthesize it after responding
        background.add_task(speak, cls.callback, callback_path(doubt.id), voice)
        callback = callback_spoken(doubt)

    return QuestionOut(
        doubt_id=doubt.id,
        transcript=transcript,
        question=doubt.question,
        intent=cls.intent,
        target_scene_idx=cls.target_scene_idx,
        answer_when=answer_when,
        ack=ack_spoken,
        callback=callback,
    )


def check_answers(db: Session, doubt: Doubt, question: dict | None) -> str:
    """The student's wrong check answers, when the doubt was asked after them (i.e. during the check)."""
    first = db.scalars(select(CheckResult).filter_by(session_id=doubt.session_id, scene_idx=doubt.scene_idx)).first()
    if not question or not first or first.created_at >= doubt.created_at:
        return ""
    options = question["options"]
    picked = picks(db, doubt.session_id, doubt.scene_idx)
    return (
        "Student's check answers:\n"
        f"Question: {question['question']}\n"
        f"Student picked: {'; then '.join(options[i] for i in picked if 0 <= i < len(options))}\n"
        f"Correct option: {options[question['correct_index']]}\n"
        f"Why: {question['explanation']}\n\n"
    )


def earlier_answers(db: Session, doubt: Doubt) -> str:
    """The session's last few doubts before this one, with what the tutor answered (a follow-up's chain is shown apart)."""
    chain = {d.id for d in follow_up_chain(db, doubt)}
    rows = db.scalars(
        select(Doubt)
        .filter(Doubt.session_id == doubt.session_id, Doubt.created_at < doubt.created_at, Doubt.intent != "clarify")
        .filter(Doubt.id.notin_(chain))
        .order_by(Doubt.created_at.desc())
        .limit(ANSWER_HISTORY)
    ).all()[::-1]
    if not rows:
        return ""
    lines = []
    for d in rows:
        said = " ".join(a["say"] for a in d.answer) if d.answer else "(not answered)"
        lines.append(f"- Doubt: {d.question}\n  Tutor answered: {said}")
    return "Earlier doubts in this session (oldest first):\n" + "\n".join(lines) + "\n\n"


def follow_up_note(db: Session, doubt: Doubt) -> str:
    chain = follow_up_chain(db, doubt)
    if not chain:
        return ""
    return f"FOLLOW-UP to your answer, which is still on the board above (oldest first):\n{on_board(chain)}\n\n"


def answer_prompt(db: Session, doubt: Doubt, sess: TutorSession) -> str:
    lesson = sess.lesson
    scene = next(s for s in lesson.scenes if s.idx == doubt.scene_idx)
    guest = sess.guest
    return (
        f"Student: {guest.name}, class {guest.class_level or 'unknown'}, "
        f"weak concepts so far: {', '.join(guest.weak_concepts) or 'none'}\n"
        f"Doubt intent: {doubt.intent}\nDoubt: {doubt.question}\n\n"
        f"Current scene {scene.idx}: {scene.title}\nGoal: {scene.goal}\n"
        f"Narration so far: {' '.join(st.say for st in scene.steps[: doubt.step_idx + 1])}\n\n"
        f"{check_answers(db, doubt, scene.check_question)}"
        f"{follow_up_note(db, doubt)}"
        f"{earlier_answers(db, doubt)}"
        f"Lesson scenes:\n{lesson_context(lesson)}\n\n"
        f"Lecture transcript:\n{lesson.transcript}"
    )


@router.get("/doubts/{doubt_id}/answer")
async def stream_answer(doubt_id: str):
    """NDJSON stream: one {say, draw, audio_url, duration_ms} line per answer sentence.

    TTS for each line starts as soon as the LLM finishes writing it, so the first
    line can play while later ones are still being generated.
    """
    with SessionLocal() as db:
        doubt = db.get(Doubt, doubt_id)
        if not doubt:
            raise HTTPException(404, "Doubt not found")
        cached = doubt.answer
        sess = db.get(TutorSession, doubt.session_id)
        session_id, thread_id = sess.id, doubt.thread_id
        voice = sess.lesson.voice
        video = sess.lesson.kind == "video"
        if video:
            # Grounded in the chapters and the context service, not in scenes
            system = VIDEO_ANSWER_SYSTEM
            prompt = None if cached else await video_answer_prompt(db, doubt, sess)
        else:
            system = for_gender(ANSWER_SYSTEM, get_voice(voice).gender, sess.lesson.language)
            prompt = None if cached else answer_prompt(db, doubt, sess)

    async def generate():
        if cached:
            for row in cached:
                yield json.dumps(row, ensure_ascii=False) + "\n"
            return

        queue: asyncio.Queue = asyncio.Queue()

        async def produce():
            n = 0
            try:
                async for line in llm.stream_lines(system, prompt):
                    try:
                        step = StepDraft.model_validate_json(line)
                    except ValidationError:
                        continue  # code fences or stray text
                    task = asyncio.create_task(speak(step.say, f"doubts/{doubt_id}_{n}.wav", voice))
                    await queue.put((step, task))
                    n += 1
            finally:
                await queue.put(None)

        rows: list[dict] = []
        started = time.perf_counter()
        ids = dict(session_id=session_id, thread_id=thread_id, doubt_id=doubt_id)
        producer = asyncio.create_task(produce())
        while (item := await queue.get()) is not None:
            step, task = item
            spoken = await task
            row = {"say": step.say, "draw": clean_draw(step.draw), "audio_url": spoken.audio_url, "duration_ms": spoken.duration_ms}
            rows.append(row)
            if len(rows) == 1:
                log_event("answer.first_line", ms=int((time.perf_counter() - started) * 1000), **ids)
            yield json.dumps(row, ensure_ascii=False) + "\n"
        try:
            await producer
        except Exception as e:  # noqa: BLE001 - logged, then the stream ends as before
            log_event("answer.failed", level="error", error=f"{type(e).__name__}: {e}"[:300], **ids)
            raise
        log_event("answer.done", ms=int((time.perf_counter() - started) * 1000), lines=len(rows), **ids)

        if video and not rows:
            return  # nothing to replay; the player abandons the thread with reason "error"
        with SessionLocal.begin() as db:
            db.get(Doubt, doubt_id).answer = rows
            if video and thread_id:
                # First store only (cached replays return above): the thread now has this much more audio
                thread = db.get(DoubtThread, thread_id)
                if thread:
                    thread.audio_ms += sum(r["duration_ms"] for r in rows)
        if video and thread_id:
            schedule_recap(thread_id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
