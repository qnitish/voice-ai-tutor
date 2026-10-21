import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio import speak, speak_cached
from app.db import CheckResult, Doubt, Guest, Lesson, TutorSession, get_db
from app.llm import complete_json
from app.prompts import SUMMARY_SYSTEM
from app.schemas import (
    CheckIn,
    CheckOut,
    GuestIn,
    GuestOut,
    SessionIn,
    SessionOut,
    SummaryDraft,
    SummaryOut,
)

router = APIRouter(prefix="/api")


def guest_out(g: Guest) -> GuestOut:
    return GuestOut(id=g.id, name=g.name, class_level=g.class_level, language=g.language, weak_concepts=g.weak_concepts)


def get_session(db: Session, session_id: str) -> TutorSession:
    sess = db.get(TutorSession, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    return sess


@router.post("/guests", response_model=GuestOut)
def create_guest(body: GuestIn, db: Session = Depends(get_db)):
    guest = Guest(**body.model_dump())
    db.add(guest)
    db.commit()
    return guest_out(guest)


@router.get("/guests/{guest_id}", response_model=GuestOut)
def get_guest(guest_id: str, db: Session = Depends(get_db)):
    guest = db.get(Guest, guest_id)
    if not guest:
        raise HTTPException(404, "Guest not found")
    return guest_out(guest)


@router.post("/sessions", response_model=SessionOut)
def create_session(body: SessionIn, db: Session = Depends(get_db)):
    guest = db.get(Guest, body.guest_id)
    lesson = db.scalars(select(Lesson).filter_by(slug=body.lesson_slug)).one_or_none()
    if not guest or not lesson:
        raise HTTPException(404, "Guest or lesson not found")
    sess = TutorSession(guest_id=guest.id, lesson_id=lesson.id)
    db.add(sess)
    db.commit()
    return SessionOut(id=sess.id, guest=guest_out(guest), lesson_slug=lesson.slug)


@router.post("/sessions/{session_id}/check", response_model=CheckOut)
async def record_answer(session_id: str, body: CheckIn, db: Session = Depends(get_db)):
    sess = get_session(db, session_id)
    scene = next((s for s in sess.lesson.scenes if s.idx == body.scene_idx), None)
    if not scene or not scene.check_question:
        raise HTTPException(404, "No check question for this scene")
    q = scene.check_question
    correct = body.chosen_index == q["correct_index"]
    db.add(
        CheckResult(
            session_id=sess.id,
            scene_idx=body.scene_idx,
            concept=q["concept"],
            chosen_index=body.chosen_index,
            correct=correct,
        )
    )
    db.commit()
    letter = "ABCD"[q["correct_index"]]
    text = "Bilkul sahi! Shabaash." if correct else f"Hmm, yeh sahi nahi hai. Sahi jawab option {letter} hai."
    return CheckOut(
        correct=correct,
        correct_index=q["correct_index"],
        explanation=q["explanation"],
        feedback=await speak_cached(text),
    )


@router.post("/sessions/{session_id}/summary", response_model=SummaryOut)
async def end_session_summary(session_id: str, db: Session = Depends(get_db)):
    sess = get_session(db, session_id)
    if sess.summary:
        return sess.summary
    doubts = db.scalars(select(Doubt).filter_by(session_id=sess.id).order_by(Doubt.created_at)).all()
    checks = db.scalars(select(CheckResult).filter_by(session_id=sess.id).order_by(CheckResult.created_at)).all()
    user = json.dumps(
        {
            "student": {"name": sess.guest.name, "class": sess.guest.class_level},
            "lesson": sess.lesson.title,
            "scenes": [{"title": s.title, "goal": s.goal} for s in sess.lesson.scenes],
            "doubts": [{"question": d.question, "intent": d.intent} for d in doubts],
            "check_results": [{"concept": c.concept, "correct": c.correct} for c in checks],
        },
        ensure_ascii=False,
    )
    draft = await complete_json(SUMMARY_SYSTEM, user, SummaryDraft)
    speech = await speak(" ".join(draft.lines), f"sessions/{sess.id}_summary.wav")
    out = SummaryOut(**draft.model_dump(), speech=speech)
    sess.summary = out.model_dump()
    guest = sess.guest
    guest.weak_concepts = list(dict.fromkeys([*guest.weak_concepts, *draft.weak_concepts]))
    db.commit()
    return out
