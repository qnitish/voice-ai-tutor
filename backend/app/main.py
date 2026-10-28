import json
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import events, lessons, questions, sessions, video, video_doubts
from app.config import settings
from app.db import migrate


class EventFormatter(logging.Formatter):
    """One JSON object per line: time and level around the fields app.telemetry.log_event already serialised."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            fields = json.loads(message)
        except ValueError:
            fields = {"message": message}
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname.lower(), **fields}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_event_logging() -> None:
    """Send "tutor.events" to stderr as JSON lines (other loggers keep uvicorn's format)."""
    log = logging.getLogger("tutor.events")
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(EventFormatter())
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


configure_event_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    yield


app = FastAPI(title="Voice Tutor", lifespan=lifespan)
app.include_router(lessons.router)
app.include_router(sessions.router)
app.include_router(questions.router)
app.include_router(video.router)
app.include_router(video_doubts.router)
app.include_router(events.router)

settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")


@app.get("/api/health")
def health():
    return {"ok": True}
