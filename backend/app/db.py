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
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS language varchar(20) NOT NULL DEFAULT 'hinglish'"))
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS label varchar(60)"))
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS renderer varchar(10) NOT NULL DEFAULT 'svg'"))
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS kind varchar(10) NOT NULL DEFAULT 'generated'"))
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS video_url varchar(300)"))
        conn.execute(text("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS duration_ms integer"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS position_ms integer"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS at_ms integer"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS thread_id varchar(32)"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS turn integer NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS checkpoint_idx integer"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS question_idx integer"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS said varchar"))
        conn.execute(text("ALTER TABLE doubts ADD COLUMN IF NOT EXISTS follow_up_of varchar(32)"))


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
    # What the tutor speaks for this lesson (app.language): "hinglish" or "english"
    language: Mapped[str] = mapped_column(String(20), default="hinglish", server_default="hinglish")
    # Free-text build note shown as a tag in the app (e.g. which prompt rules made it); prep --label
    label: Mapped[str | None] = mapped_column(String(60))
    # Which whiteboard draws it in the app: "svg" or "skia" (CanvasKit); prep --renderer
    renderer: Mapped[str] = mapped_column(String(10), default="svg", server_default="svg")
    # "generated": scenes narrated by the tutor; "video": the recorded lecture plays (chapters + checkpoints)
    kind: Mapped[str] = mapped_column(String(10), default="generated", server_default="generated")
    # Video lessons only: the web-playable video under /media, and its length
    video_url: Mapped[str | None] = mapped_column(String(300))
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="lesson", order_by="Scene.idx", cascade="all, delete-orphan"
    )
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="lesson", order_by="Chapter.idx", cascade="all, delete-orphan"
    )
    checkpoints: Mapped[list["Checkpoint"]] = relationship(
        back_populates="lesson", order_by="Checkpoint.idx", cascade="all, delete-orphan"
    )


class Chapter(Base):
    """A subtopic of a video lesson with its time range; doubt routing and recaps work per chapter."""

    __tablename__ = "chapters"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    idx: Mapped[int]
    topic: Mapped[str]  # the larger topic this subtopic belongs to
    title: Mapped[str]
    summary: Mapped[str]
    key_terms: Mapped[list] = mapped_column(JSONB, default=list)
    misconceptions: Mapped[list] = mapped_column(JSONB, default=list)
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    lesson: Mapped[Lesson] = relationship(back_populates="chapters")


class Checkpoint(Base):
    """A pause in a video lesson where 1..N questions are asked (schemas.CheckpointQuestion each)."""

    __tablename__ = "checkpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    idx: Mapped[int]
    at_ms: Mapped[int]
    chapter_idx: Mapped[int]  # the chapter the questions are about (usually the one ending here)
    intro: Mapped[dict] = mapped_column(JSONB)  # Spoken: "Let's check what we just learned"
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    lesson: Mapped[Lesson] = relationship(back_populates="checkpoints")


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
    # Video lessons: playback position, for resuming
    position_ms: Mapped[int | None]
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
    # What speech-to-text heard; `question` is the router's rewrite of it (video lessons)
    said: Mapped[str | None]
    intent: Mapped[str]
    target_scene_idx: Mapped[int | None]
    # covered_later only: the "Tumne pucha tha…" line, audio at /media/doubts/<id>_callback.wav
    callback: Mapped[str | None]
    confidence: Mapped[float | None]
    # For a "clarify" doubt: the classification waiting for the student to confirm it
    pending: Mapped[dict | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(default=False)
    answer: Mapped[list | None] = mapped_column(JSONB)
    # Video lessons: where the video was paused, the doubt thread and this doubt's turn in it (0 = first)
    # (scene_idx holds the chapter index and step_idx is 0)
    at_ms: Mapped[int | None]
    thread_id: Mapped[str | None] = mapped_column(String(32))
    turn: Mapped[int] = mapped_column(default=0, server_default="0")
    # Asked from a checkpoint question's follow-up step (its question and wrong picks go to the answer prompt)
    checkpoint_idx: Mapped[int | None]
    question_idx: Mapped[int | None]
    # Lesson boards: asked while the tutor was answering this doubt; answered below it on the same board
    follow_up_of: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DoubtThread(Base):
    """A doubt and its follow-ups while the video is paused; ends resolved, capped or abandoned (app.services.threads)."""

    __tablename__ = "doubt_threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    at_ms: Mapped[int]
    chapter_idx: Mapped[int]
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | resolved | capped | abandoned
    turns: Mapped[int] = mapped_column(default=0)  # doubts answered in this thread
    audio_ms: Mapped[int] = mapped_column(default=0)  # spoken answer audio so far
    recap: Mapped[dict | None] = mapped_column(JSONB)  # Spoken, when the thread ended with a recap
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None]


class CheckpointAnswer(Base):
    """A student's answer to one question of a video checkpoint."""

    __tablename__ = "checkpoint_answers"
    __table_args__ = (UniqueConstraint("session_id", "checkpoint_idx", "question_idx", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    checkpoint_idx: Mapped[int]
    question_idx: Mapped[int]
    kind: Mapped[str] = mapped_column(String(10))  # first | retry | skip
    chosen_index: Mapped[int]  # -1 for skip
    correct: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SessionEvent(Base):
    """Structured log of what happened in a session (server and client), to trace where a flow failed."""

    __tablename__ = "session_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(32))
    doubt_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(60))  # e.g. "stt.failed", "thread.recap", "client.error"
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warn | error
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
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
