import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Lesson, get_db
from app.language import lesson_language
from app.lesson_store import latest_lesson, latest_lessons
from app.schemas import LessonOut, LessonSummaryOut, SceneOut, Spoken, StepOut
from app.voices import describe, exists, fillers_dir, get_voice

router = APIRouter(prefix="/api")


def lesson_tags(lesson: Lesson) -> list[str]:
    language = "English" if lesson_language(lesson.language) == "english" else "Hinglish"
    renderer = ["Skia"] if lesson.renderer == "skia" else []
    return [language, describe(lesson.voice), f"v{lesson.version}", *([lesson.label] if lesson.label else []), *renderer]


def lesson_out(lesson: Lesson) -> LessonOut:
    return LessonOut(
        slug=lesson.slug,
        title=lesson.title,
        version=lesson.version,
        voice=lesson.voice,
        voice_gender=get_voice(lesson.voice).gender,
        language=lesson.language,
        tags=lesson_tags(lesson),
        renderer=lesson.renderer,
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
    return [
        LessonSummaryOut(
            slug=l.slug, title=l.title, scene_count=len(l.scenes), version=l.version, language=l.language, tags=lesson_tags(l)
        )
        for l in latest_lessons(db)
    ]


@router.get("/lessons/{slug}", response_model=LessonOut)
def get_lesson(slug: str, db: Session = Depends(get_db)):
    lesson = latest_lesson(db, slug)
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    return lesson_out(lesson)


def _manifest(name: str, voice: str | None, language: str | None) -> list:
    if not exists(voice):
        return []
    manifest = fillers_dir(voice, lesson_language(language)) / name
    return json.loads(manifest.read_text()) if manifest.exists() else []


@router.get("/fillers", response_model=list[Spoken])
def fillers(voice: str | None = None, language: str | None = None):
    return _manifest("fillers.json", voice, language)


@router.get("/bridges", response_model=list[Spoken])
def bridges(voice: str | None = None, language: str | None = None):
    """Lines played after an answer, before the lesson continues."""
    return _manifest("bridges.json", voice, language)
