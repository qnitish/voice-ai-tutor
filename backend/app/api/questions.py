"""Live doubt handling: STT -> classify with lookahead -> spoken ack -> streamed answer."""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.api.sessions import callback_path, callback_spoken, get_session, touch, update_mastery
from app.audio import STT_RATE, speak, speak_cached, to_wav
from app.config import settings
from app.db import Doubt, Lesson, SessionLocal, TutorSession, get_db
from app.progress import doubt_mastery
from app.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, FIXED_ACKS, clarify_ack
from app.providers import get_stt
from app.routing import is_affirmative
from app.schemas import Classification, QuestionOut, Spoken, StepDraft, clean_draw

router = APIRouter(prefix="/api")

ANSWER_WHEN = {
    "covered_later": "target_scene",
    "not_covered": "scene_end",
    "prerequisite": "now",
    "confused": "now",
    "repeat": "replay",
    "off_topic": "none",
    "unclear": "none",
    "clarify": "none",
}
# Below this confidence, these intents are confirmed with the student before answering
CLARIFY_BELOW = 0.6
CLARIFIABLE = {"covered_later", "not_covered", "prerequisite", "confused"}
# These suggest the student hasn't got the current scene's concept yet
STRUGGLE_INTENTS = {"confused", "repeat", "prerequisite"}
RECENT_DOUBTS = 5


def lesson_context(lesson: Lesson) -> str:
    """Every scene with its narration, so the LLM can look ahead in the lesson."""
    parts = []
    for s in lesson.scenes:
        says = " ".join(st.say for st in s.steps)
        parts.append(f"Scene {s.idx}: {s.title}\nGoal: {s.goal}\nKey terms: {', '.join(s.key_terms)}\nNarration: {says}")
    return "\n\n".join(parts)


def routing_prompt(lesson: Lesson, scene_idx: int, step_idx: int, earlier: list[Doubt], transcript: str) -> str:
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


async def classify(db: Session, sess: TutorSession, scene_idx: int, step_idx: int, transcript: str) -> tuple[Classification, bool]:
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
    user = routing_prompt(sess.lesson, scene_idx, step_idx, earlier, transcript)
    cls = await llm.complete_json(CLASSIFY_SYSTEM, user, Classification, temperature=0.2, model=settings.routing_deployment)
    return cls, cls.confidence < CLARIFY_BELOW and cls.intent in CLARIFIABLE


@router.post("/sessions/{session_id}/questions", response_model=QuestionOut)
async def ask_question(
    session_id: str,
    audio: UploadFile,
    background: BackgroundTasks,
    scene_idx: int = Form(),
    step_idx: int = Form(),
    db: Session = Depends(get_db),
):
    sess = get_session(db, session_id)
    lesson = sess.lesson
    stt = get_stt()
    wav, duration_ms = await asyncio.to_thread(to_wav, await audio.read(), STT_RATE)
    if duration_ms > stt.max_seconds * 1000:
        raise HTTPException(400, f"Recording longer than {stt.max_seconds}s")
    transcript = await stt.transcribe(wav) if duration_ms > 300 else ""

    cls, needs_clarify = await classify(db, sess, scene_idx, step_idx, transcript)
    pending = None
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
    )
    db.add(doubt)
    scene = next((s for s in lesson.scenes if s.idx == scene_idx), None)
    if cls.intent in STRUGGLE_INTENTS and scene and scene.check_question:
        update_mastery(db, sess.guest_id, scene.check_question["concept"], doubt_mastery, attempt=False)
    touch(sess)
    db.commit()

    if cls.intent == "clarify":
        ack_spoken = await speak(clarify_ack(doubt.question), f"doubts/{doubt.id}_ack.wav")
    elif cls.intent == "off_topic" and cls.ack:
        ack_spoken = await speak(cls.ack, f"doubts/{doubt.id}_ack.wav")
    else:
        ack_spoken = await speak_cached(FIXED_ACKS[cls.intent])

    callback = None
    if cls.callback:
        # Not needed until a later scene starts, so synthesize it after responding
        background.add_task(speak, cls.callback, callback_path(doubt.id))
        callback = callback_spoken(doubt)

    return QuestionOut(
        doubt_id=doubt.id,
        transcript=transcript,
        question=doubt.question,
        intent=cls.intent,
        target_scene_idx=cls.target_scene_idx,
        answer_when=ANSWER_WHEN[cls.intent],
        ack=ack_spoken,
        callback=callback,
    )


def answer_prompt(doubt: Doubt, sess: TutorSession) -> str:
    lesson = sess.lesson
    scene = next(s for s in lesson.scenes if s.idx == doubt.scene_idx)
    guest = sess.guest
    return (
        f"Student: {guest.name}, class {guest.class_level or 'unknown'}, "
        f"weak concepts so far: {', '.join(guest.weak_concepts) or 'none'}\n"
        f"Doubt intent: {doubt.intent}\nDoubt: {doubt.question}\n\n"
        f"Current scene {scene.idx}: {scene.title}\nGoal: {scene.goal}\n"
        f"Narration so far: {' '.join(st.say for st in scene.steps[: doubt.step_idx + 1])}\n\n"
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
        prompt = None if cached else answer_prompt(doubt, db.get(TutorSession, doubt.session_id))

    async def generate():
        if cached:
            for row in cached:
                yield json.dumps(row, ensure_ascii=False) + "\n"
            return

        queue: asyncio.Queue = asyncio.Queue()

        async def produce():
            n = 0
            try:
                async for line in llm.stream_lines(ANSWER_SYSTEM, prompt):
                    try:
                        step = StepDraft.model_validate_json(line)
                    except ValidationError:
                        continue  # code fences or stray text
                    task = asyncio.create_task(speak(step.say, f"doubts/{doubt_id}_{n}.wav"))
                    await queue.put((step, task))
                    n += 1
            finally:
                await queue.put(None)

        rows: list[dict] = []
        producer = asyncio.create_task(produce())
        while (item := await queue.get()) is not None:
            step, task = item
            spoken = await task
            row = {"say": step.say, "draw": clean_draw(step.draw), "audio_url": spoken.audio_url, "duration_ms": spoken.duration_ms}
            rows.append(row)
            yield json.dumps(row, ensure_ascii=False) + "\n"
        await producer

        with SessionLocal.begin() as db:
            db.get(Doubt, doubt_id).answer = rows

    return StreamingResponse(generate(), media_type="application/x-ndjson")
