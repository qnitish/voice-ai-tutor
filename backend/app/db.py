import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str]
    # Full source transcript, used to ground live answers
    transcript: Mapped[str]
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
    weak_concepts: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TutorSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"))
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
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
