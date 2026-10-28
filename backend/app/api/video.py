"""Video lessons (docs/plan/video-lesson-spec.md 2.4): playback position, checkpoint answers and the resume state.

Doubt threads are in video_doubts.py; resume/progress/history/summary branch on the lesson kind in sessions.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sessions import checkpoint_answers, fixed_line, get_session, touch, update_mastery
from app.db import CheckpointAnswer, Doubt, TutorSession, get_db
from app.progress import next_mastery, retry_mastery
from app.prompts import CHECK_LINES, reveal_line
from app.quiz import spoken_explanation
from app.schemas import (
    AnsweredQuestion,
    VideoCheckOut,
    CheckpointAnswerIn,
    CheckpointSkipIn,
    VideoPositionIn,
    VideoStateDoubt,
    Spoken,
    VideoStateOut,
)
from app.services.threads import safe_line

router = APIRouter(prefix="/api")


@router.post("/sessions/{session_id}/video/position")
def save_video_position(session_id: str, body: VideoPositionIn, db: Session = Depends(get_db)):
    """Where playback is, every few seconds and at every pause; the resume card continues from here."""
    sess = get_session(db, session_id)
    duration = sess.lesson.duration_ms
    sess.position_ms = min(body.at_ms, duration) if duration is not None else body.at_ms
    touch(sess)
    db.commit()
    return {"ok": True}


def checkpoint_question_or_404(sess: TutorSession, checkpoint_idx: int, question_idx: int) -> dict:
    checkpoint = next((cp for cp in sess.lesson.checkpoints if cp.idx == checkpoint_idx), None)
    if not checkpoint:
        raise HTTPException(404, "No such checkpoint in this lesson")
    if not 0 <= question_idx < len(checkpoint.questions):
        raise HTTPException(404, "No such question in this checkpoint")
    return checkpoint.questions[question_idx]


def checkpoint_answer(
    db: Session, session_id: str, checkpoint_idx: int, question_idx: int, kind: str
) -> CheckpointAnswer | None:
    q = select(CheckpointAnswer).filter_by(
        session_id=session_id, checkpoint_idx=checkpoint_idx, question_idx=question_idx, kind=kind
    )
    return db.scalars(q).first()


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_idx}/answer", response_model=VideoCheckOut)
async def answer_checkpoint_question(
    session_id: str, checkpoint_idx: int, body: CheckpointAnswerIn, db: Session = Depends(get_db)
):
    """A first answer (counts like a scene check) or the one retry after a wrong first pick."""
    sess = get_session(db, session_id)
    q = checkpoint_question_or_404(sess, checkpoint_idx, body.question_idx)
    if body.chosen_index >= len(q["options"]):
        raise HTTPException(422, "chosen_index is not one of the options")
    first = checkpoint_answer(db, sess.id, checkpoint_idx, body.question_idx, "first")
    correct = body.chosen_index == q["correct_index"]
    language = sess.lesson.language
    if body.kind == "first":
        if first:
            raise HTTPException(409, "Already answered in this session")
        update_mastery(db, sess.guest_id, q["concept"], lambda s: next_mastery(s, correct), attempt=True)
        shown = True
        if correct:
            text = CHECK_LINES["correct"]
        elif any(q.get("why_wrong") or []):
            # The player plays the hint for the picked option and lets the student try again
            text = CHECK_LINES["wrong_try_again"]
            shown = False
        else:
            text = reveal_line(q["correct_index"], language)
    else:
        if not first or first.correct:
            raise HTTPException(409, "Only after a wrong first answer")
        if checkpoint_answer(db, sess.id, checkpoint_idx, body.question_idx, "retry"):
            raise HTTPException(409, "retry already answered in this session")
        update_mastery(db, sess.guest_id, q["concept"], lambda s: retry_mastery(s, correct), attempt=False)
        text = CHECK_LINES["retry_correct"] if correct else reveal_line(q["correct_index"], language)
        shown = True
    db.add(
        CheckpointAnswer(
            session_id=sess.id,
            checkpoint_idx=checkpoint_idx,
            question_idx=body.question_idx,
            kind=body.kind,
            chosen_index=body.chosen_index,
            correct=correct,
        )
    )
    touch(sess)
    db.commit()
    return VideoCheckOut(
        correct=correct,
        correct_index=q["correct_index"],
        explanation=q["explanation"],
        feedback=await fixed_line(sess, text),
        explanation_spoken=await explanation_spoken(q, sess.lesson.voice) if shown else None,
    )


async def explanation_spoken(q: dict, voice: str | None) -> Spoken | None:
    """The explanation's first sentences in the lesson's voice: prepared by app.prep.solutions, else made (and
    cached) now; None if TTS fails, so the answer is still shown."""
    if q.get("explanation_audio"):
        return Spoken(**q["explanation_audio"])
    text = spoken_explanation(q["explanation"])
    spoken = await safe_line(text, voice)
    return spoken if spoken.audio_url else None


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_idx}/skip")
def skip_checkpoint_question(session_id: str, checkpoint_idx: int, body: CheckpointSkipIn, db: Session = Depends(get_db)):
    """The student closed a checkpoint question without answering; recorded once so reviews can tell. No mastery change."""
    sess = get_session(db, session_id)
    checkpoint_question_or_404(sess, checkpoint_idx, body.question_idx)
    if not checkpoint_answer(db, sess.id, checkpoint_idx, body.question_idx, "skip"):
        db.add(
            CheckpointAnswer(
                session_id=sess.id,
                checkpoint_idx=checkpoint_idx,
                question_idx=body.question_idx,
                kind="skip",
                chosen_index=-1,
                correct=False,
            )
        )
        touch(sess)
        db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/video/state", response_model=VideoStateOut)
def get_video_state(session_id: str, db: Session = Depends(get_db)):
    """Everything a resumed video player needs: the position, answered checkpoint questions and earlier doubts."""
    sess = get_session(db, session_id)
    doubts = db.scalars(select(Doubt).filter_by(session_id=sess.id).order_by(Doubt.created_at)).all()
    return VideoStateOut(
        session_id=sess.id,
        completed=sess.summary is not None,
        position_ms=sess.position_ms,
        answered=[
            AnsweredQuestion(checkpoint_idx=a.checkpoint_idx, question_idx=a.question_idx, kind=a.kind, correct=a.correct)
            for a in checkpoint_answers(db, sess.id)
        ],
        doubts=[
            VideoStateDoubt(
                id=d.id,
                at_ms=d.at_ms or 0,
                thread_id=d.thread_id,
                turn=d.turn,
                question=d.question,
                intent=d.intent,
                answered=d.answer is not None,
            )
            for d in doubts
        ],
    )
