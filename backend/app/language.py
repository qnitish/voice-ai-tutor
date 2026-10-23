"""Lesson languages. A lesson is prepared in one language (`lessons.language`); everything the tutor says for it
(narration, answers, fixed lines) is in that language, and the student's speech is recognised in it."""

from contextvars import ContextVar
from typing import Literal

Language = Literal["hinglish", "english"]
DEFAULT: Language = "hinglish"

# Gnani STT language codes; Hinglish speech is recognised as Hindi (Devanagari), which the routing prompt expects
STT_CODES: dict[str, str] = {"hinglish": "hi-IN", "hindi": "hi-IN", "english": "en-IN"}

# Language of the current STT call, set by the live API around transcribe(); None = the configured default
current_stt_language: ContextVar[str | None] = ContextVar("current_stt_language", default=None)


def lesson_language(value: str | None) -> Language:
    """A lesson's (or student's) language; anything other than English falls back to Hinglish."""
    return "english" if value == "english" else "hinglish"
