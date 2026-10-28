"""Publishing and looking up lesson versions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Chapter, Checkpoint, Lesson, Scene, Step
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
    renderer: str = "svg",
) -> Lesson:
    """Add a new version of the lesson; earlier versions and their sessions are kept. Caller commits."""
    latest = db.scalar(select(func.max(Lesson.version)).filter_by(slug=slug)) or 0
    checks = shuffle_checks([s["check_question"] for s in scenes], seed=slug)
    twins = shuffle_checks([(c or {}).get("twin") for c in checks], seed=f"{slug}:twin")
    checks = [{**c, "twin": t} if c and t else c for c, t in zip(checks, twins)]
    lesson = Lesson(
        slug=slug,
        version=latest + 1,
        title=title,
        transcript=transcript,
        voice=voice,
        language=language,
        label=label,
        renderer=renderer,
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


def publish_video_lesson(
    db: Session,
    slug: str,
    title: str,
    transcript: str,
    video_url: str,
    duration_ms: int,
    chapters: list[dict],
    checkpoints: list[dict],
    voice: str | None = None,
    language: str = "english",
    label: str | None = None,
) -> Lesson:
    """Add a new version of a video lesson (schemas.ChapterOut / CheckpointOut shaped dicts, idx = list order).

    Caller commits.
    """
    latest = db.scalar(select(func.max(Lesson.version)).filter_by(slug=slug)) or 0
    lesson = Lesson(
        slug=slug,
        version=latest + 1,
        title=title,
        transcript=transcript,
        voice=voice,
        language=language,
        label=label,
        kind="video",
        video_url=video_url,
        duration_ms=duration_ms,
    )
    for i, c in enumerate(chapters):
        lesson.chapters.append(
            Chapter(
                idx=i,
                topic=c["topic"],
                title=c["title"],
                summary=c.get("summary", ""),
                key_terms=c.get("key_terms", []),
                misconceptions=c.get("misconceptions", []),
                start_ms=c["start_ms"],
                end_ms=c["end_ms"],
            )
        )
    for i, cp in enumerate(checkpoints):
        lesson.checkpoints.append(
            Checkpoint(idx=i, at_ms=cp["at_ms"], chapter_idx=cp["chapter_idx"], intro=cp["intro"], questions=cp["questions"])
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


def is_visible(slug: str) -> bool:
    """Whether students may see and start this lesson (settings.visible_lessons; empty: every lesson)."""
    allowed = {s.strip() for s in settings.visible_lessons.split(",") if s.strip()}
    return not allowed or slug in allowed


def visible_lesson(db: Session, slug: str) -> Lesson | None:
    """The latest version of a lesson students may open; None when it is hidden (prep scripts use latest_lesson)."""
    return latest_lesson(db, slug) if is_visible(slug) else None


def visible_lessons(db: Session) -> list[Lesson]:
    return [l for l in latest_lessons(db) if is_visible(l.slug)]
