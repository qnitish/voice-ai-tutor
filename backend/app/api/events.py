"""Session events: the client reports its failures here, and a session's trace can be read back."""

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sessions import get_session
from app.db import SessionEvent, get_db
from app.schemas import ClientEventIn, SessionEventOut
from app.telemetry import log_event

router = APIRouter(prefix="/api")

# log_event's own parameters; a client payload can't override them
RESERVED = {"event", "kind", "level", "session_id", "thread_id", "doubt_id"}


@router.post("/events")
def post_event(body: ClientEventIn):
    """Record a client-side event (kind like "video.answer_stream") with the session it belongs to."""
    data = {k: v for k, v in body.data.items() if k not in RESERVED}
    log_event(
        body.kind, level=body.level, session_id=body.session_id, thread_id=body.thread_id, doubt_id=body.doubt_id, source="client", **data
    )
    return {"ok": True}


@router.get("/sessions/{session_id}/events", response_model=list[SessionEventOut])
def list_events(session_id: str, level: Literal["info", "warn", "error"] | None = None, db: Session = Depends(get_db)):
    """Everything logged for the session, oldest first; `level` keeps only events of that level."""
    get_session(db, session_id)
    q = select(SessionEvent).where(SessionEvent.session_id == session_id).order_by(SessionEvent.id)
    if level:
        q = q.where(SessionEvent.level == level)
    return db.scalars(q).all()
