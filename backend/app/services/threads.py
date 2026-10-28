"""Doubt threads of a video lesson: follow-up limits, the recap before the video resumes, and closing a thread.

A thread is one pause of the video: the first doubt and its follow-ups (docs/plan/video-lesson-spec.md 1.1).
`turns` counts the doubts answered in it; the cap and the recap rules are pure functions of the policy.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.audio import speak, speak_cached
from app.config import settings
from app.db import Chapter, DoubtThread, Lesson, SessionLocal, TutorSession
from app.prompts import RECAP_SYSTEM, VIDEO_RECAP_TEMPLATE
from app.schemas import Spoken, ThreadOut
from app.telemetry import Timer, log_event

# Transcript of the last minute goes into the recap (and the routing prompt)
RECAP_WINDOW_MS = 60_000
_MARK = re.compile(r"\[(\d+):(\d{2})(?::(\d{2}))?\]")
# The background recap task per thread: kept so it is not garbage-collected before it finishes, and so a thread
# that closes while its recap is still being made waits for it instead of making a second one
_recap_tasks: dict[str, asyncio.Task] = {}


def recap_path(thread_id: str) -> str:
    """A fresh media path per recap, so two generations for one thread can never overwrite a file being served."""
    return f"threads/{thread_id}_recap_{uuid4().hex[:8]}.wav"


async def safe_line(text: str, voice: str | None, **ids) -> Spoken:
    """A fixed line's cached audio; if TTS fails, the text alone (empty audio_url, 0 ms) so the request still
    answers. Prep pre-generates these lines (app.prep.video.make_video_lines), so this is the exception."""
    try:
        return await speak_cached(text, voice)
    except Exception as e:  # noqa: BLE001 - the routing result matters more than the audio of a fixed line
        log_event("line.failed", level="warn", text=text[:80], error=_err(e), **ids)
        return Spoken(text=text, audio_url="", duration_ms=0)


@dataclass(frozen=True)
class ThreadPolicy:
    max_followups: int
    recap_after_turns: int
    recap_after_seconds: int

    @classmethod
    def from_settings(cls) -> "ThreadPolicy":
        return cls(
            max_followups=settings.video_max_followups,
            recap_after_turns=settings.video_recap_after_turns,
            recap_after_seconds=settings.video_recap_after_seconds,
        )


class RecapDraft(BaseModel):
    recap: str


def can_follow_up(turns: int, policy: ThreadPolicy) -> bool:
    """Another answerable doubt is allowed on a thread that has answered `turns` doubts."""
    return turns - 1 < policy.max_followups


def needs_recap(turns: int, audio_ms: int, policy: ThreadPolicy) -> bool:
    """A thread this long gets a recap before the video resumes; a single short doubt does not."""
    return turns >= policy.recap_after_turns or audio_ms >= policy.recap_after_seconds * 1000


def thread_out(
    thread: DoubtThread,
    policy: ThreadPolicy,
    recap: Spoken | None = None,
    line: Spoken | None = None,
    prompt: Spoken | None = None,
) -> ThreadOut:
    """The thread as the player sees it. The recap defaults to the stored one, once the thread has ended
    (it may be prepared in the background while the thread is still open)."""
    if recap is None and thread.recap and thread.status != "open":
        recap = Spoken(**thread.recap)
    return ThreadOut(
        id=thread.id,
        status=thread.status,
        turns=thread.turns,
        audio_ms=thread.audio_ms,
        can_follow_up=can_follow_up(thread.turns, policy),
        recap=recap,
        line=line,
        prompt=prompt,
    )


def open_thread(db: Session, sess: TutorSession, at_ms: int, chapter_idx: int) -> DoubtThread:
    thread = DoubtThread(session_id=sess.id, at_ms=at_ms, chapter_idx=chapter_idx)
    db.add(thread)
    db.flush()
    log_event("thread.opened", session_id=sess.id, thread_id=thread.id, at_ms=at_ms, chapter_idx=chapter_idx)
    return thread


def get_open_thread(db: Session, sess: TutorSession, thread_id: str | None) -> DoubtThread | None:
    """The session's thread with this id if it is still open; None lets the caller start a new one."""
    if not thread_id:
        return None
    thread = db.get(DoubtThread, thread_id)
    if thread is None or thread.session_id != sess.id or thread.status != "open":
        return None
    return thread


def chapter_of(lesson: Lesson, idx: int) -> Chapter | None:
    return next((c for c in lesson.chapters if c.idx == idx), None)


def transcript_window(text: str, at_ms: int | None, window_ms: int = RECAP_WINDOW_MS) -> str:
    """The transcript spoken in the `window_ms` before `at_ms`, from its [mm:ss] marks.

    A transcript without marks (or no `at_ms`) gives its tail instead, so the prompt still has something.
    """
    marks = list(_MARK.finditer(text))
    if not marks or at_ms is None:
        return text[-1500:].strip()
    segments = []  # (start_ms, text)
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        segments.append((_mark_ms(m), text[m.end() : end].strip()))
    inside = [s for s in segments if at_ms - window_ms <= s[0] <= at_ms]
    if not inside:
        # The window starts mid-segment: the one being spoken at the pause (or the first, if paused before any mark)
        before = [s for s in segments if s[0] <= at_ms]
        inside = [before[-1] if before else segments[0]]
    return " ".join(t for _, t in inside if t)


def _mark_ms(m: re.Match) -> int:
    h, mm, ss = m.groups()
    return (int(h) * 3600 + int(mm) * 60 + int(ss)) * 1000 if ss else (int(h) * 60 + int(mm)) * 1000


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


async def make_recap(thread: DoubtThread, lesson: Lesson) -> Spoken | None:
    """The recap for a thread: LLM text in the lesson's voice, else the template; None only if TTS fails too."""
    chapter = chapter_of(lesson, thread.chapter_idx)
    title = chapter.title if chapter else lesson.title
    text = ""
    with Timer() as t:
        try:
            user = (
                f"Current chapter: {title}\nSummary: {chapter.summary if chapter else ''}\n\n"
                f"Transcript of the last minute before the pause:\n{transcript_window(lesson.transcript, thread.at_ms)}"
            )
            draft = await llm.complete_json(RECAP_SYSTEM, user, RecapDraft, temperature=0.4, model=settings.routing_deployment)
            text = draft.recap.strip()
        except Exception as e:  # noqa: BLE001 - the template is always available
            log_event("thread.recap_failed", level="warn", session_id=thread.session_id, thread_id=thread.id, error=_err(e))
    try:
        if text:
            spoken = await speak(text, recap_path(thread.id), lesson.voice)
            source = "llm"
        else:
            spoken = await speak_cached(VIDEO_RECAP_TEMPLATE.format(title=title), lesson.voice)
            source = "template"
    except Exception as e:  # noqa: BLE001 - a missing recap must not block the video
        log_event("thread.recap_failed", level="error", session_id=thread.session_id, thread_id=thread.id, error=_err(e))
        return None
    log_event("thread.recap", session_id=thread.session_id, thread_id=thread.id, ms=t.ms, source=source)
    return spoken


async def thread_recap(db: Session, thread: DoubtThread, lesson: Lesson) -> Spoken | None:
    """The thread's recap: the stored one, the one a background task is still making, else a fresh one.

    Called with no transaction open (the caller committed), so nothing is locked while the recap is awaited;
    the row is re-read after the background task because that task writes with its own session.
    """
    task = _recap_tasks.get(thread.id)
    if task is not None and not task.done():
        try:
            await asyncio.shield(task)
        except Exception as e:  # noqa: BLE001 - logged by the task; make one here instead
            log_event("thread.recap_failed", level="warn", session_id=thread.session_id, thread_id=thread.id, error=_err(e))
    stored = db.scalar(select(DoubtThread.recap).where(DoubtThread.id == thread.id))
    db.commit()  # ends the read transaction before any await below
    if stored:
        thread.recap = stored
        return Spoken(**stored)
    return await make_recap(thread, lesson)


async def close_thread(
    db: Session, thread: DoubtThread, status: str, lesson: Lesson, line: Spoken | None = None
) -> ThreadOut:
    """End a thread (resolved | capped | abandoned) with its recap when it was long enough. Commits.

    The caller's pending changes are committed before the recap is awaited: an UPDATE flushed on a row (e.g. the
    session's `updated_at`) would otherwise hold its lock across the LLM/TTS calls, and a concurrent request on
    the same row would block the event loop. The thread row itself changes only after the recap exists.
    """
    policy = ThreadPolicy.from_settings()
    recap = None
    if needs_recap(thread.turns, thread.audio_ms, policy):
        db.commit()
        recap = await thread_recap(db, thread, lesson)
    thread.status = status
    thread.ended_at = datetime.now(timezone.utc)
    if recap:
        thread.recap = recap.model_dump()
    db.commit()
    log_event(
        "thread.closed",
        session_id=thread.session_id,
        thread_id=thread.id,
        status=status,
        turns=thread.turns,
        audio_ms=thread.audio_ms,
        recap=recap is not None,
    )
    return thread_out(thread, policy, recap=recap, line=line)


async def prepare_recap(thread_id: str) -> None:
    """Background: generate and store the recap as soon as the thread is long enough, so "Got it" doesn't wait."""
    policy = ThreadPolicy.from_settings()
    # No session stays open across the LLM/TTS awaits: an idle transaction would hold locks (and a background task
    # can be suspended mid-way when its event loop stops)
    with SessionLocal() as db:
        thread = db.get(DoubtThread, thread_id)
        if not thread or thread.recap or thread.status != "open":
            return
        if not needs_recap(thread.turns, thread.audio_ms, policy):
            return
        lesson = db.get(TutorSession, thread.session_id).lesson
        lesson.chapters  # loaded now; make_recap reads them after the session is closed
    recap = await make_recap(thread, lesson)
    if not recap:
        return
    with SessionLocal.begin() as db:
        thread = db.get(DoubtThread, thread_id)
        if thread and not thread.recap:  # close_thread may have stored one meanwhile
            thread.recap = recap.model_dump()


def schedule_recap(thread_id: str) -> None:
    """Run prepare_recap after the current request, one task per thread (a running one is left to finish)."""
    running = _recap_tasks.get(thread_id)
    if running is not None and not running.done():
        return
    task = asyncio.create_task(prepare_recap(thread_id))
    _recap_tasks[thread_id] = task

    def forget(t: asyncio.Task) -> None:
        if _recap_tasks.get(thread_id) is t:
            del _recap_tasks[thread_id]

    task.add_done_callback(forget)
