import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Lesson, get_db
from app.language import lesson_language
from app.lesson_store import latest_lesson, latest_lessons
from app.schemas import (
    ChapterOut,
    CheckpointOut,
    LessonOut,
    LessonSummaryOut,
    PlayerPolicy,
    SceneOut,
    Spoken,
    StepOut,
    VideoOut,
)
from app.voices import describe, exists, fillers_dir, get_voice

router = APIRouter(prefix="/api")


def lesson_tags(lesson: Lesson) -> list[str]:
    language = "English" if lesson_language(lesson.language) == "english" else "Hinglish"
    renderer = ["Skia"] if lesson.renderer == "skia" else []
    return [language, describe(lesson.voice), f"v{lesson.version}", *([lesson.label] if lesson.label else []), *renderer]


def player_policy() -> PlayerPolicy:
    """Limits the video player follows (doubt threads, resume rewind), from settings."""
    return PlayerPolicy(
        max_followups=settings.video_max_followups,
        recap_after_turns=settings.video_recap_after_turns,
        recap_after_seconds=settings.video_recap_after_seconds,
        rewind_ms=settings.video_rewind_ms,
        idle_ms=settings.video_thread_idle_s * 1000,
    )


def video_fields(lesson: Lesson) -> dict:
    """The video-lesson part of LessonOut: the video, its chapters, checkpoints and the player policy."""
    if lesson.kind != "video":
        return {}
    return dict(
        kind="video",
        video=VideoOut(url=lesson.video_url, duration_ms=lesson.duration_ms),
        chapters=[
            ChapterOut(
                idx=c.idx,
                topic=c.topic,
                title=c.title,
                summary=c.summary,
                key_terms=c.key_terms,
                start_ms=c.start_ms,
                end_ms=c.end_ms,
            )
            for c in lesson.chapters
        ],
        checkpoints=[
            CheckpointOut(idx=cp.idx, at_ms=cp.at_ms, chapter_idx=cp.chapter_idx, intro=cp.intro, questions=cp.questions)
            for cp in lesson.checkpoints
        ],
        policy=player_policy(),
    )


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
        **video_fields(lesson),
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
            slug=l.slug,
            title=l.title,
            # A video lesson has no scenes; its chapters are the units the card counts
            scene_count=len(l.chapters) if l.kind == "video" else len(l.scenes),
            version=l.version,
            language=l.language,
            tags=lesson_tags(l),
            kind=l.kind,
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
