import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, create_engine, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


def migrate() -> None:
    """Create tables, plus columns added after a table first existed (create_all doesn't add those)."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS voice varchar(60)"))


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Lesson(Base):
    """One published version of a lesson. Re-running prep adds a version; old ones keep their sessions."""

    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("slug", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str]
    # Full source transcript, used to ground live answers
    transcript: Mapped[str]
    # Tutor voice for everything spoken in this lesson (app.voices); null = the default voice
    voice: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="lesson", order_by="Scene.idx", cascade="all, delete-orphan"
    )


class Scene(Base):
    __tablename__ = "scenes"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    idx: Mapped[int]
    title: Mapped[str]
    goal: Mapped[str]
    key_terms: Mapped[list] = mapped_column(JSONB, default=list)
    check_question: Mapped[dict | None] = mapped_column(JSONB)
    lesson: Mapped[Lesson] = relationship(back_populates="scenes")
    steps: Mapped[list["Step"]] = relationship(
        back_populates="scene", order_by="Step.idx", cascade="all, delete-orphan"
    )


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    idx: Mapped[int]
    say: Mapped[str]
    draw: Mapped[list] = mapped_column(JSONB, default=list)
    audio_url: Mapped[str]
    duration_ms: Mapped[int]
    scene: Mapped[Scene] = relationship(back_populates="steps")


class Guest(Base):
    __tablename__ = "guests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str]
    class_level: Mapped[int | None]
    language: Mapped[str] = mapped_column(default="hinglish")
    # Concepts named weak by session summaries; GuestOut merges these with low mastery scores
    weak_concepts: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TutorSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"))
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    # Set once the session summary has been generated, i.e. the lesson was finished
    summary: Mapped[dict | None] = mapped_column(JSONB)
    # Step the student last started, for resuming
    position_scene: Mapped[int | None]
    position_step: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Last activity (position, doubt, check, summary)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    guest: Mapped[Guest] = relationship()
    lesson: Mapped[Lesson] = relationship()


class Doubt(Base):
    __tablename__ = "doubts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    scene_idx: Mapped[int]
    step_idx: Mapped[int]
    question: Mapped[str]
    intent: Mapped[str]
    target_scene_idx: Mapped[int | None]
    # covered_later only: the "Tumne pucha tha…" line, audio at /media/doubts/<id>_callback.wav
    callback: Mapped[str | None]
    confidence: Mapped[float | None]
    # For a "clarify" doubt: the classification waiting for the student to confirm it
    pending: Mapped[dict | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(default=False)
    answer: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CheckResult(Base):
    __tablename__ = "check_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    scene_idx: Mapped[int]
    concept: Mapped[str]
    chosen_index: Mapped[int]
    correct: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CheckAttempt(Base):
    """A later answer after a wrong first one: a retry of the same question, or its twin. Not a CheckResult."""

    __tablename__ = "check_attempts"
    __table_args__ = (UniqueConstraint("session_id", "scene_idx", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    scene_idx: Mapped[int]
    kind: Mapped[str] = mapped_column(String(10))  # "retry" | "twin" | "skip" | "twin_skip"
    chosen_index: Mapped[int]
    correct: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ConceptMastery(Base):
    """How well a guest knows a concept, 0..1; see app.progress for the update rule."""

    __tablename__ = "concept_mastery"
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), primary_key=True)
    concept: Mapped[str] = mapped_column(primary_key=True)
    score: Mapped[float]
    attempts: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
