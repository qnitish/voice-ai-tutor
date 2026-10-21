import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Lesson, get_db
from app.schemas import LessonOut, LessonSummaryOut, SceneOut, Spoken, StepOut

router = APIRouter(prefix="/api")


def lesson_out(lesson: Lesson) -> LessonOut:
    return LessonOut(
        slug=lesson.slug,
        title=lesson.title,
        scenes=[
            SceneOut(
                idx=s.idx,
                title=s.title,
                goal=s.goal,
                key_terms=s.key_terms,
                check_question=s.check_question,
                steps=[
                    StepOut(idx=st.idx, say=st.say, draw=st.draw, audio_url=st.audio_url, duration_ms=st.duration_ms)
                    for st in s.steps
                ],
            )
            for s in lesson.scenes
        ],
    )


@router.get("/lessons", response_model=list[LessonSummaryOut])
def list_lessons(db: Session = Depends(get_db)):
    lessons = db.scalars(select(Lesson).order_by(Lesson.created_at.desc())).all()
    return [LessonSummaryOut(slug=l.slug, title=l.title, scene_count=len(l.scenes)) for l in lessons]


@router.get("/lessons/{slug}", response_model=LessonOut)
def get_lesson(slug: str, db: Session = Depends(get_db)):
    lesson = db.scalars(select(Lesson).filter_by(slug=slug)).one_or_none()
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    return lesson_out(lesson)


@router.get("/fillers", response_model=list[Spoken])
def fillers():
    manifest = settings.media_dir / "fillers" / "fillers.json"
    return json.loads(manifest.read_text()) if manifest.exists() else []
