import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ---- Whiteboard draw actions (board is 1600 x 900, origin top-left) ----

Color = Literal["ink", "blue", "red", "green", "orange"]
Point = tuple[float, float]


class TextAction(BaseModel):
    type: Literal["text"]
    id: str
    x: float
    y: float
    text: str
    size: Literal["h1", "h2", "body", "small"] = "body"
    color: Color = "ink"


class LineAction(BaseModel):
    type: Literal["line"]
    id: str
    start: Point
    end: Point
    color: Color = "ink"
    dashed: bool = False


class ArrowAction(BaseModel):
    type: Literal["arrow"]
    id: str
    start: Point
    end: Point
    color: Color = "ink"
    label: str | None = None


class RectAction(BaseModel):
    type: Literal["rect"]
    id: str
    x: float
    y: float
    w: float
    h: float
    color: Color = "ink"
    label: str | None = None


class CircleAction(BaseModel):
    type: Literal["circle"]
    id: str
    cx: float
    cy: float
    r: float
    color: Color = "ink"
    filled: bool = False
    label: str | None = None


class AngleAction(BaseModel):
    type: Literal["angle"]
    id: str
    cx: float
    cy: float
    r: float
    start_deg: float
    end_deg: float
    color: Color = "ink"
    label: str | None = None


class FieldMarkAction(BaseModel):
    type: Literal["field_mark"]
    id: str
    x: float
    y: float
    dir: Literal["in", "out"]
    color: Color = "ink"


class HighlightAction(BaseModel):
    type: Literal["highlight"]
    id: str
    target: str
    color: Color = "orange"


DrawAction = Annotated[
    TextAction
    | LineAction
    | ArrowAction
    | RectAction
    | CircleAction
    | AngleAction
    | FieldMarkAction
    | HighlightAction,
    Field(discriminator="type"),
]
_draw_adapter = TypeAdapter(DrawAction)


def clean_draw(actions: list) -> list[dict]:
    """Keep only valid draw actions; the LLM occasionally emits a malformed one."""
    out = []
    for raw in actions:
        try:
            out.append(_draw_adapter.validate_python(raw).model_dump())
        except ValidationError:
            continue
    return out


# ---- LLM outputs ----


class OutlineScene(BaseModel):
    title: str
    goal: str
    transcript_focus: str
    key_terms: list[str] = []
    misconceptions: list[str] = []


class Outline(BaseModel):
    title: str
    scenes: list[OutlineScene]


class StepDraft(BaseModel):
    say: str
    draw: list[dict] = []


class CheckQuestion(BaseModel):
    question: str
    say: str
    options: list[str] = Field(min_length=2, max_length=4)
    correct_index: int
    concept: str
    explanation: str


class SceneDraft(BaseModel):
    steps: list[StepDraft] = Field(min_length=1)
    check_question: CheckQuestion

    @model_validator(mode="after")
    def roman_script(self):
        spoken = [s.say for s in self.steps] + [self.check_question.say]
        if any(DEVANAGARI.search(t) for t in spoken):
            raise ValueError('every "say" must be in Roman script, not Devanagari')
        return self


class TwinQuestion(BaseModel):
    question: str
    say: str
    options: list[str] = Field(min_length=2, max_length=4)
    correct_index: int
    explanation: str


class OptionHint(BaseModel):
    option: int
    hint: str


class RemedyDraft(BaseModel):
    """What the tutor says and draws when a check question is answered wrong."""

    # One per wrong option, labelled with its index (a plain list drifts when the LLM skips the correct one)
    why_wrong: list[OptionHint]
    reteach: list[StepDraft] = Field(min_length=2, max_length=4)
    twin: TwinQuestion

    @model_validator(mode="after")
    def roman_script(self):
        spoken = [h.hint for h in self.why_wrong] + [s.say for s in self.reteach] + [self.twin.say]
        if any(DEVANAGARI.search(t) for t in spoken):
            raise ValueError('every spoken line must be in Roman script, not Devanagari')
        return self


class CheckReview(BaseModel):
    """Reviewer verdict on one scene before narration; check_question is corrected when issues were found."""

    issues: list[str] = []
    narration_issues: list[str] = []
    check_question: CheckQuestion

    @model_validator(mode="after")
    def roman_script(self):
        if DEVANAGARI.search(self.check_question.say):
            raise ValueError('"say" must be in Roman script, not Devanagari')
        return self


class RemedyReview(BaseModel):
    issues: list[str] = []
    remedy: RemedyDraft


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def leaks_answer(hint: str, correct_option: str) -> bool:
    """A hint that contains the correct option's text gives the answer away.

    Very short options ("Two", "OH") also occur in honest hints, so only options of 2+ words or 6+ letters count.
    """
    option = _norm(correct_option)
    if len(option.split()) < 2 and len(option) < 6:
        return False
    return f" {option} " in f" {_norm(hint)} "


Intent = Literal[
    "covered_later", "not_covered", "prerequisite", "confused", "repeat", "continue", "off_topic", "unclear", "clarify"
]
AnswerWhen = Literal["none", "now", "replay", "step_end", "scene_end", "target_scene"]


class Classification(BaseModel):
    intent: Intent
    question: str
    target_scene_idx: int | None = None
    ack: str | None = None
    callback: str | None = None
    # Below 0.6 the tutor asks the student to confirm the rewritten question first
    confidence: float = Field(default=1.0, ge=0, le=1)


class SummaryDraft(BaseModel):
    lines: list[str]
    weak_concepts: list[str] = []
    revise: list[str] = []


# ---- API ----


class StepOut(BaseModel):
    idx: int
    say: str
    draw: list[dict]
    audio_url: str
    duration_ms: int


class SceneOut(BaseModel):
    idx: int
    title: str
    goal: str
    key_terms: list[str]
    check_question: dict | None
    steps: list[StepOut]


class LessonSummaryOut(BaseModel):
    slug: str
    title: str
    scene_count: int
    version: int


class LessonOut(BaseModel):
    slug: str
    title: str
    version: int
    # Tutor voice name (null = default) and its gender, for fillers/bridges and UI wording
    voice: str | None = None
    voice_gender: Literal["female", "male"] = "female"
    scenes: list[SceneOut]


class GuestIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    class_level: int | None = Field(default=None, ge=6, le=12)
    language: Literal["hindi", "english", "hinglish"] = "hinglish"


class GuestOut(BaseModel):
    id: str
    name: str
    class_level: int | None
    language: str
    weak_concepts: list[str]


class SessionIn(BaseModel):
    guest_id: str
    lesson_slug: str


class SessionOut(BaseModel):
    id: str
    guest: GuestOut
    lesson_slug: str
    lesson_version: int


class Position(BaseModel):
    scene_idx: int
    step_idx: int


class ResumeOut(Position):
    session_id: str


class MasteryOut(BaseModel):
    concept: str
    score: float
    attempts: int


class ProgressOut(BaseModel):
    slug: str
    title: str
    version: int
    percent: int
    completed: bool
    checks_correct: int
    checks_total: int
    doubts: int
    sessions: int
    resume: ResumeOut | None


class HistoryDoubt(BaseModel):
    question: str
    intent: str
    answered: bool


class HistoryOut(BaseModel):
    session_id: str
    slug: str
    title: str
    version: int
    created_at: datetime
    completed: bool
    checks_correct: int
    checks_total: int
    doubts: list[HistoryDoubt]


class Spoken(BaseModel):
    text: str
    audio_url: str
    duration_ms: int


class QuestionOut(BaseModel):
    doubt_id: str
    transcript: str
    question: str
    intent: Intent
    target_scene_idx: int | None
    # When the player should deliver the answer ("replay" restarts the current step, no answer)
    answer_when: AnswerWhen
    ack: Spoken
    callback: Spoken | None = None


class CheckIn(BaseModel):
    scene_idx: int
    chosen_index: int


class SkipIn(BaseModel):
    scene_idx: int
    # Skipped the scene's twin question instead of its own check
    twin: bool = False


class CheckOut(BaseModel):
    correct: bool
    correct_index: int
    explanation: str
    feedback: Spoken


class SummaryOut(BaseModel):
    lines: list[str]
    weak_concepts: list[str]
    revise: list[str]
    speech: Spoken


# ---- resuming a session ----


class StateDoubt(BaseModel):
    id: str
    scene_idx: int
    step_idx: int
    question: str
    intent: str
    target_scene_idx: int | None
    answered: bool
    callback: Spoken | None


class SessionStateOut(BaseModel):
    session_id: str
    completed: bool
    position: Position | None
    answered_checks: list[int]
    doubts: list[StateDoubt]
