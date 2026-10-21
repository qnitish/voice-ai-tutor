"""Live doubt handling: STT -> classify with lookahead -> spoken ack -> streamed answer."""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.sessions import get_session
from app.audio import STT_RATE, speak, speak_cached, to_wav
from app.db import Doubt, Lesson, SessionLocal, TutorSession, get_db
from app.llm import complete_json, stream_lines
from app.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, FIXED_ACKS
from app.providers import get_stt
from app.schemas import Classification, QuestionOut, Spoken, StepDraft, clean_draw

router = APIRouter(prefix="/api")

ANSWER_WHEN = {
    "covered_later": "target_scene",
    "not_covered": "scene_end",
    "repeat": "step_end",
    "off_topic": "none",
    "unclear": "none",
}


def lesson_context(lesson: Lesson) -> str:
    """Every scene with its narration, so the LLM can look ahead in the lesson."""
    parts = []
    for s in lesson.scenes:
        says = " ".join(st.say for st in s.steps)
        parts.append(f"Scene {s.idx}: {s.title}\nGoal: {s.goal}\nKey terms: {', '.join(s.key_terms)}\nNarration: {says}")
    return "\n\n".join(parts)


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

    if transcript:
        user = (
            f"current_scene_idx: {scene_idx} (step {step_idx})\n\n"
            f"Lesson scenes:\n{lesson_context(lesson)}\n\n"
            f'Student said (speech-to-text): "{transcript}"'
        )
        cls = await complete_json(CLASSIFY_SYSTEM, user, Classification, temperature=0.2)
    else:
        cls = Classification(intent="unclear", question="")

    # Code, not the LLM, owns the lesson pointer: only accept a lookahead target that is really ahead
    if cls.intent == "covered_later" and not (
        cls.target_scene_idx is not None
        and scene_idx < cls.target_scene_idx < len(lesson.scenes)
        and cls.callback
    ):
        cls = cls.model_copy(update={"intent": "not_covered"})
    if cls.intent != "covered_later":
        cls = cls.model_copy(update={"target_scene_idx": None, "callback": None})

    doubt = Doubt(
        session_id=sess.id,
        scene_idx=scene_idx,
        step_idx=step_idx,
        question=cls.question or transcript,
        intent=cls.intent,
        target_scene_idx=cls.target_scene_idx,
    )
    db.add(doubt)
    db.commit()

    if cls.intent == "off_topic" and cls.ack:
        ack_spoken = await speak(cls.ack, f"doubts/{doubt.id}_ack.wav")
    else:
        ack_spoken = await speak_cached(FIXED_ACKS[cls.intent])

    callback = None
    if cls.callback:
        # Not needed until a later scene starts, so synthesize it after responding
        rel_path = f"doubts/{doubt.id}_callback.wav"
        background.add_task(speak, cls.callback, rel_path)
        callback = Spoken(text=cls.callback, audio_url=f"/media/{rel_path}", duration_ms=0)

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
                async for line in stream_lines(ANSWER_SYSTEM, prompt):
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
