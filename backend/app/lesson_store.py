"""Publishing and looking up lesson versions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Lesson, Scene, Step
from app.quiz import shuffle_checks


def publish_lesson(
    db: Session,
    slug: str,
    title: str,
    transcript: str,
    scenes: list[dict],
    voice: str | None = None,
    language: str = "hinglish",
    label: str | None = None,
) -> Lesson:
    """Add a new version of the lesson; earlier versions and their sessions are kept. Caller commits."""
    latest = db.scalar(select(func.max(Lesson.version)).filter_by(slug=slug)) or 0
    checks = shuffle_checks([s["check_question"] for s in scenes], seed=slug)
    twins = shuffle_checks([(c or {}).get("twin") for c in checks], seed=f"{slug}:twin")
    checks = [{**c, "twin": t} if c and t else c for c, t in zip(checks, twins)]
    lesson = Lesson(
        slug=slug, version=latest + 1, title=title, transcript=transcript, voice=voice, language=language, label=label
    )
    for si, (scene, check) in enumerate(zip(scenes, checks)):
        lesson.scenes.append(
            Scene(
                idx=si,
                title=scene["title"],
                goal=scene["goal"],
                key_terms=scene["key_terms"],
                check_question=check,
                steps=[
                    Step(idx=i, say=s["say"], draw=s["draw"], audio_url=s["audio_url"], duration_ms=s["duration_ms"])
                    for i, s in enumerate(scene["steps"])
                ],
            )
        )
    db.add(lesson)
    db.flush()
    return lesson


def latest_lesson(db: Session, slug: str) -> Lesson | None:
    return db.scalars(select(Lesson).filter_by(slug=slug).order_by(Lesson.version.desc()).limit(1)).first()


def latest_lessons(db: Session) -> list[Lesson]:
    """The newest version of every slug, most recently published first."""
    rows = db.scalars(select(Lesson).order_by(Lesson.slug, Lesson.version.desc())).all()
    latest: dict[str, Lesson] = {}
    for lesson in rows:
        latest.setdefault(lesson.slug, lesson)
    return sorted(latest.values(), key=lambda l: (l.created_at, l.id), reverse=True)
