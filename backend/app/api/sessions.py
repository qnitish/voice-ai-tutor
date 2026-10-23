import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import llm
from app.audio import speak, speak_cached
from app.db import CheckAttempt, CheckResult, ConceptMastery, Doubt, Guest, Lesson, TutorSession, get_db
from app.lesson_store import latest_lesson
from app.voices import get_voice
from app.progress import WEAK_BELOW, next_mastery, retry_mastery
from app.prompts import CHECK_LINES, SUMMARY_SYSTEM, for_gender, line_for, reveal_line
from app.schemas import (
    CheckIn,
    CheckOut,
    GuestIn,
    GuestOut,
    GuestUpdate,
    HistoryDoubt,
    HistoryOut,
    MasteryOut,
    Position,
    ProgressOut,
    ResumeOut,
    SessionIn,
    SessionOut,
    SessionStateOut,
    SkipIn,
    Spoken,
    StateDoubt,
    SummaryDraft,
    SummaryOut,
)

router = APIRouter(prefix="/api")


def mastery_rows(db: Session, guest_id: str) -> list[ConceptMastery]:
    """Weakest first."""
    q = select(ConceptMastery).filter_by(guest_id=guest_id).order_by(ConceptMastery.score, ConceptMastery.concept)
    return list(db.scalars(q).all())


def guest_out(db: Session, g: Guest) -> GuestOut:
    weak = [m.concept for m in mastery_rows(db, g.id) if m.score < WEAK_BELOW]
    weak += [c for c in g.weak_concepts if c not in weak]
    return GuestOut(id=g.id, name=g.name, class_level=g.class_level, language=g.language, weak_concepts=weak)


def get_guest_or_404(db: Session, guest_id: str) -> Guest:
    guest = db.get(Guest, guest_id)
    if not guest:
        raise HTTPException(404, "Guest not found")
    return guest


def get_session(db: Session, session_id: str) -> TutorSession:
    sess = db.get(TutorSession, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    return sess


def callback_path(doubt_id: str) -> str:
    return f"doubts/{doubt_id}_callback.wav"


def callback_spoken(doubt: Doubt) -> Spoken | None:
    if not doubt.callback:
        return None
    return Spoken(text=doubt.callback, audio_url=f"/media/{callback_path(doubt.id)}", duration_ms=0)


def fixed_line(sess: TutorSession, text: str):
    """A fixed spoken line (cached audio) in the lesson's voice and language."""
    lesson = sess.lesson
    return speak_cached(line_for(text, get_voice(lesson.voice).gender, lesson.language), lesson.voice)


def touch(sess: TutorSession) -> None:
    sess.updated_at = func.clock_timestamp()


def update_mastery(db: Session, guest_id: str, concept: str, rule, attempt: bool) -> None:
    """Apply a mastery rule from app.progress to one concept, creating the row on first evidence."""
    row = db.get(ConceptMastery, (guest_id, concept))
    if row is None:
        row = ConceptMastery(guest_id=guest_id, concept=concept, score=rule(None), attempts=0)
        db.add(row)
    else:
        row.score = rule(row.score)
    if attempt:
        row.attempts += 1


@router.post("/guests", response_model=GuestOut)
def create_guest(body: GuestIn, db: Session = Depends(get_db)):
    guest = Guest(**body.model_dump())
    db.add(guest)
    db.commit()
    return guest_out(db, guest)


@router.get("/guests/{guest_id}", response_model=GuestOut)
def get_guest(guest_id: str, db: Session = Depends(get_db)):
    return guest_out(db, get_guest_or_404(db, guest_id))


@router.patch("/guests/{guest_id}", response_model=GuestOut)
def update_guest(guest_id: str, body: GuestUpdate, db: Session = Depends(get_db)):
    """The student's language choice; the home page lists the lessons in it."""
    guest = get_guest_or_404(db, guest_id)
    guest.language = body.language
    db.commit()
    return guest_out(db, guest)


@router.post("/sessions", response_model=SessionOut)
def create_session(body: SessionIn, db: Session = Depends(get_db)):
    guest = db.get(Guest, body.guest_id)
    lesson = latest_lesson(db, body.lesson_slug)
    if not guest or not lesson:
        raise HTTPException(404, "Guest or lesson not found")
    sess = TutorSession(guest_id=guest.id, lesson_id=lesson.id)
    db.add(sess)
    db.commit()
    return SessionOut(id=sess.id, guest=guest_out(db, guest), lesson_slug=lesson.slug, lesson_version=lesson.version)


@router.post("/sessions/{session_id}/position", response_model=Position)
def save_position(session_id: str, body: Position, db: Session = Depends(get_db)):
    sess = get_session(db, session_id)
    scene = next((s for s in sess.lesson.scenes if s.idx == body.scene_idx), None)
    if not scene or not 0 <= body.step_idx < len(scene.steps):
        raise HTTPException(400, "No such scene/step in this lesson")
    sess.position_scene, sess.position_step = body.scene_idx, body.step_idx
    touch(sess)
    db.commit()
    return body


def check_question_or_404(sess: TutorSession, scene_idx: int) -> dict:
    scene = next((s for s in sess.lesson.scenes if s.idx == scene_idx), None)
    if not scene or not scene.check_question:
        raise HTTPException(404, "No check question for this scene")
    return scene.check_question


def first_answer(db: Session, session_id: str, scene_idx: int) -> CheckResult | None:
    return db.scalars(select(CheckResult).filter_by(session_id=session_id, scene_idx=scene_idx)).first()


@router.post("/sessions/{session_id}/check", response_model=CheckOut)
async def record_answer(session_id: str, body: CheckIn, db: Session = Depends(get_db)):
    sess = get_session(db, session_id)
    q = check_question_or_404(sess, body.scene_idx)
    if first_answer(db, sess.id, body.scene_idx):
        raise HTTPException(409, "Already answered in this session")
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
    update_mastery(db, sess.guest_id, q["concept"], lambda s: next_mastery(s, correct), attempt=True)
    touch(sess)
    db.commit()
    if correct:
        text = CHECK_LINES["correct"]
    elif any(q.get("why_wrong") or []):
        # The player plays the hint for the picked option and lets the student try again
        text = CHECK_LINES["wrong_try_again"]
    else:
        text = reveal_line(q["correct_index"], sess.lesson.language)
    return CheckOut(
        correct=correct,
        correct_index=q["correct_index"],
        explanation=q["explanation"],
        feedback=await fixed_line(sess, text),
    )


def later_attempt(db: Session, sess: TutorSession, body: CheckIn, kind: str, q: dict) -> bool:
    """Record a retry or twin answer to q; only allowed once, after a wrong first answer. Caller commits."""
    first = first_answer(db, sess.id, body.scene_idx)
    if not first or first.correct:
        raise HTTPException(409, "Only after a wrong first answer")
    if db.scalar(select(CheckAttempt.id).filter_by(session_id=sess.id, scene_idx=body.scene_idx, kind=kind)):
        raise HTTPException(409, f"{kind} already answered in this session")
    correct = body.chosen_index == q["correct_index"]
    db.add(CheckAttempt(session_id=sess.id, scene_idx=body.scene_idx, kind=kind, chosen_index=body.chosen_index, correct=correct))
    touch(sess)
    return correct


@router.post("/sessions/{session_id}/check/retry", response_model=CheckOut)
async def retry_answer(session_id: str, body: CheckIn, db: Session = Depends(get_db)):
    """Second try at the same question, after the hint for the first (wrong) pick."""
    sess = get_session(db, session_id)
    q = check_question_or_404(sess, body.scene_idx)
    correct = later_attempt(db, sess, body, "retry", q)
    update_mastery(db, sess.guest_id, q["concept"], lambda s: retry_mastery(s, correct), attempt=False)
    db.commit()
    text = CHECK_LINES["retry_correct"] if correct else reveal_line(q["correct_index"], sess.lesson.language)
    return CheckOut(
        correct=correct,
        correct_index=q["correct_index"],
        explanation=q["explanation"],
        feedback=await fixed_line(sess, text),
    )


@router.post("/sessions/{session_id}/check/twin", response_model=CheckOut)
async def twin_answer(session_id: str, body: CheckIn, db: Session = Depends(get_db)):
    """A different question on the same concept, asked a scene later to see if the re-teach worked."""
    sess = get_session(db, session_id)
    q = check_question_or_404(sess, body.scene_idx)
    twin = q.get("twin")
    if not twin:
        raise HTTPException(404, "No twin question for this scene")
    correct = later_attempt(db, sess, body, "twin", twin)
    update_mastery(db, sess.guest_id, q["concept"], lambda s: next_mastery(s, correct), attempt=True)
    db.commit()
    text = CHECK_LINES["twin_correct"] if correct else reveal_line(twin["correct_index"], sess.lesson.language)
    return CheckOut(
        correct=correct,
        correct_index=twin["correct_index"],
        explanation=twin["explanation"],
        feedback=await fixed_line(sess, text),
    )


@router.post("/sessions/{session_id}/check/skip")
def skip_check(session_id: str, body: SkipIn, db: Session = Depends(get_db)):
    """The student closed a check (or twin) card without answering; recorded so reviews can tell. No mastery change."""
    sess = get_session(db, session_id)
    q = check_question_or_404(sess, body.scene_idx)
    if body.twin and not q.get("twin"):
        raise HTTPException(404, "No twin question for this scene")
    kind = "twin_skip" if body.twin else "skip"
    if not db.scalar(select(CheckAttempt.id).filter_by(session_id=sess.id, scene_idx=body.scene_idx, kind=kind)):
        db.add(CheckAttempt(session_id=sess.id, scene_idx=body.scene_idx, kind=kind, chosen_index=-1, correct=False))
        touch(sess)
        db.commit()
    return {"ok": True}


@router.post("/sessions/{session_id}/summary", response_model=SummaryOut)
async def end_session_summary(session_id: str, db: Session = Depends(get_db)):
    sess = get_session(db, session_id)
    if sess.summary:
        return sess.summary
    doubts = db.scalars(select(Doubt).filter_by(session_id=sess.id).order_by(Doubt.created_at)).all()
    checks = db.scalars(select(CheckResult).filter_by(session_id=sess.id).order_by(CheckResult.created_at)).all()
    later = {(a.scene_idx, a.kind): a.correct for a in db.scalars(select(CheckAttempt).filter_by(session_id=sess.id))}
    user = json.dumps(
        {
            "student": {"name": sess.guest.name, "class": sess.guest.class_level},
            "lesson": sess.lesson.title,
            "scenes": [{"title": s.title, "goal": s.goal} for s in sess.lesson.scenes],
            "doubts": [{"question": d.question, "intent": d.intent} for d in doubts],
            "check_results": [
                {
                    "concept": c.concept,
                    "correct": c.correct,
                    "retry_correct": later.get((c.scene_idx, "retry")),
                    "twin_correct": later.get((c.scene_idx, "twin")),
                    "twin_skipped": (c.scene_idx, "twin_skip") in later,
                }
                for c in checks
            ],
            "skipped_checks": sorted(i for i, kind in later if kind == "skip"),
        },
        ensure_ascii=False,
    )
    voice = sess.lesson.voice
    system = for_gender(SUMMARY_SYSTEM, get_voice(voice).gender, sess.lesson.language)
    draft = await llm.complete_json(system, user, SummaryDraft)
    speech = await speak(" ".join(draft.lines), f"sessions/{sess.id}_summary.wav", voice)
    out = SummaryOut(**draft.model_dump(), speech=speech)
    sess.summary = out.model_dump()
    touch(sess)
    guest = sess.guest
    guest.weak_concepts = list(dict.fromkeys([*guest.weak_concepts, *draft.weak_concepts]))
    db.commit()
    return out


@router.get("/sessions/{session_id}/state", response_model=SessionStateOut)
def get_session_state(session_id: str, db: Session = Depends(get_db)):
    """Everything a resumed player needs: position, answered checks and the earlier doubts."""
    sess = get_session(db, session_id)
    checks = db.scalars(select(CheckResult.scene_idx).filter_by(session_id=sess.id).distinct()).all()
    doubts = db.scalars(select(Doubt).filter_by(session_id=sess.id).order_by(Doubt.created_at)).all()
    position = None
    if sess.position_scene is not None:
        position = Position(scene_idx=sess.position_scene, step_idx=sess.position_step)
    return SessionStateOut(
        session_id=sess.id,
        completed=sess.summary is not None,
        position=position,
        answered_checks=sorted(checks),
        doubts=[
            StateDoubt(
                id=d.id,
                scene_idx=d.scene_idx,
                step_idx=d.step_idx,
                question=d.question,
                intent=d.intent,
                target_scene_idx=d.target_scene_idx,
                answered=d.answer is not None,
                callback=callback_spoken(d),
            )
            for d in doubts
        ],
    )


# ---- guest progress ----


def guest_sessions(db: Session, guest_id: str, lesson_ids: list[int] | None = None) -> list[TutorSession]:
    """Most recently active first."""
    q = select(TutorSession).filter_by(guest_id=guest_id)
    if lesson_ids is not None:
        q = q.where(TutorSession.lesson_id.in_(lesson_ids))
    q = q.order_by(TutorSession.updated_at.desc(), TutorSession.created_at.desc())
    return list(db.scalars(q).all())


def resume_point(sessions: list[TutorSession]) -> ResumeOut | None:
    """Latest unfinished session with a saved position (sessions must be most recent first)."""
    for s in sessions:
        if s.summary is None and s.position_scene is not None:
            return ResumeOut(session_id=s.id, scene_idx=s.position_scene, step_idx=s.position_step)
    return None


def percent_done(sess: TutorSession) -> int:
    if sess.summary is not None:
        return 100
    if sess.position_scene is None:
        return 0
    scenes = sess.lesson.scenes
    total = sum(len(s.steps) for s in scenes)
    before = sum(len(s.steps) for s in scenes if s.idx < sess.position_scene) + sess.position_step
    return round(100 * before / total) if total else 0


@router.get("/guests/{guest_id}/lessons/{slug}/resume", response_model=ResumeOut | None)
def get_resume(guest_id: str, slug: str, db: Session = Depends(get_db)):
    get_guest_or_404(db, guest_id)
    lesson = latest_lesson(db, slug)
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    return resume_point(guest_sessions(db, guest_id, [lesson.id]))


@router.get("/guests/{guest_id}/mastery", response_model=list[MasteryOut])
def get_mastery(guest_id: str, db: Session = Depends(get_db)):
    get_guest_or_404(db, guest_id)
    return [MasteryOut(concept=m.concept, score=m.score, attempts=m.attempts) for m in mastery_rows(db, guest_id)]


def check_counts(db: Session, session_ids: list[str]) -> tuple[int, int]:
    rows = db.scalars(select(CheckResult.correct).where(CheckResult.session_id.in_(session_ids))).all()
    return sum(rows), len(rows)


@router.get("/guests/{guest_id}/progress", response_model=list[ProgressOut])
def get_progress(guest_id: str, db: Session = Depends(get_db)):
    """Per lesson, counting only sessions on its latest version (older versions had different scenes)."""
    get_guest_or_404(db, guest_id)
    out = []
    slugs = dict.fromkeys(s.lesson.slug for s in guest_sessions(db, guest_id))  # most recent activity first
    for slug in slugs:
        lesson = latest_lesson(db, slug)
        sessions = guest_sessions(db, guest_id, [lesson.id])
        ids = [s.id for s in sessions]
        correct, total = check_counts(db, ids)
        doubts = db.scalar(select(func.count(Doubt.id)).where(Doubt.session_id.in_(ids))) if ids else 0
        out.append(
            ProgressOut(
                slug=slug,
                title=lesson.title,
                version=lesson.version,
                percent=max((percent_done(s) for s in sessions), default=0),
                completed=any(s.summary is not None for s in sessions),
                checks_correct=correct,
                checks_total=total,
                doubts=doubts,
                sessions=len(sessions),
                resume=resume_point(sessions),
            )
        )
    return out


@router.get("/guests/{guest_id}/history", response_model=list[HistoryOut])
def get_history(guest_id: str, db: Session = Depends(get_db)):
    get_guest_or_404(db, guest_id)
    q = select(TutorSession).filter_by(guest_id=guest_id).order_by(TutorSession.created_at.desc())
    out = []
    for s in db.scalars(q).all():
        lesson: Lesson = s.lesson
        correct, total = check_counts(db, [s.id])
        doubts = db.scalars(select(Doubt).filter_by(session_id=s.id).order_by(Doubt.created_at)).all()
        out.append(
            HistoryOut(
                session_id=s.id,
                slug=lesson.slug,
                title=lesson.title,
                version=lesson.version,
                created_at=s.created_at,
                completed=s.summary is not None,
                checks_correct=correct,
                checks_total=total,
                doubts=[HistoryDoubt(question=d.question, intent=d.intent, answered=d.answer is not None) for d in doubts],
            )
        )
    return out
