import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_serializer, model_validator

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


# Blocks: diagram parts the board draws with correct proportions, so the LLM only places them


class WireAction(BaseModel):
    type: Literal["wire"]
    id: str
    start: Point
    end: Point
    current: str | None = None  # label of the current arrow drawn on the wire, e.g. "I"
    color: Color = "ink"


class ChargeAction(BaseModel):
    type: Literal["charge"]
    id: str
    x: float
    y: float
    sign: Literal["+", "-"] = "+"
    label: str | None = None
    velocity_deg: float | None = None  # direction of the velocity arrow "v", degrees counter-clockwise from +x
    force_deg: float | None = None  # direction of the force arrow "F"
    color: Color = "blue"


class CompassAction(BaseModel):
    type: Literal["compass"]
    id: str
    x: float
    y: float
    needle_deg: float = 90  # direction the north end points
    label: str | None = None


class MagnetAction(BaseModel):
    type: Literal["magnet"]
    id: str
    x: float
    y: float
    w: float = 240
    h: float = 70
    north: Literal["left", "right", "top", "bottom"] = "left"


class AxesAction(BaseModel):
    type: Literal["axes"]
    id: str
    x: float  # origin
    y: float
    w: float = 400
    h: float = 300
    x_label: str | None = "x"
    y_label: str | None = "y"
    color: Color = "ink"


class RegionAction(BaseModel):
    type: Literal["region"]
    id: str
    cx: float
    cy: float
    w: float
    h: float
    shape: Literal["ellipse", "rect"] = "ellipse"
    label: str | None = None  # inside the region, e.g. "R"
    curve_label: str | None = None  # next to the boundary, e.g. "C"
    orientation: Literal["ccw", "cw"] | None = None
    color: Color = "ink"


class FlowAction(BaseModel):
    type: Literal["flow"]
    id: str
    x: float
    y: float
    items: list[str] = Field(min_length=1, max_length=6)
    dir: Literal["down", "right"] = "down"
    connector: str | None = None  # label on every arrow between items, e.g. "+CH₂"
    size: Literal["h2", "body", "small"] = "body"
    color: Color = "ink"


Bond = Literal["-", "=", "≡"]


class ChainBranch(BaseModel):
    at: int  # index of the main-chain group it hangs from
    groups: list[str] = Field(min_length=1, max_length=4)
    dir: Literal["up", "down"] = "up"
    bond: Bond = "-"


class ChainAction(BaseModel):
    type: Literal["chain"]
    id: str
    x: float  # baseline start of the first group
    y: float
    groups: list[str] = Field(min_length=1, max_length=8)  # e.g. ["CH₃", "CH₂", "CH₂", "OH"]
    bonds: list[Bond] = []  # bond after each group but the last; missing ones are single
    branches: list[ChainBranch] = []
    color: Color = "ink"


class CurveAction(BaseModel):
    type: Literal["curve"]
    id: str
    points: list[Point] = Field(min_length=2, max_length=40)  # a smooth curve is drawn through them, in order
    color: Color = "ink"
    dashed: bool = False
    label: str | None = None  # placed near the last point, e.g. "y = x²"


class Vector(BaseModel):
    deg: float  # direction, degrees counter-clockwise from +x (90 = up, 270 = down)
    label: str  # e.g. "v₀", "g", "a"
    color: Color = "blue"


class BodyAction(BaseModel):
    """Any object in a mechanics problem (ball, stone, packet, car, balloon) with its vectors."""

    type: Literal["body"]
    id: str
    x: float  # centre
    y: float
    shape: Literal["ball", "box"] = "ball"
    label: str | None = None
    vectors: list[Vector] = Field(default=[], max_length=3)
    color: Color = "ink"


class GroundAction(BaseModel):
    type: Literal["ground"]
    id: str
    x: float  # left end of the surface
    y: float
    w: float = 400
    label: str | None = None


class RingAction(BaseModel):
    type: Literal["ring"]
    id: str
    cx: float
    cy: float
    r: float = 60
    size: Literal[3, 4, 5, 6] = 6
    aromatic: bool = False  # benzene: a circle inside the hexagon
    label: str | None = None
    color: Color = "ink"


class Track(BaseModel):
    target: str  # id of an item drawn earlier (usually a body)
    v: Point = (0, 0)  # initial velocity, board px/s, +y up
    a: Point = (0, 0)  # acceleration, board px/s², +y up (gravity is about (0, -300))


class MotionAction(BaseModel):
    """Moves earlier items by the equations of motion; the board computes every frame."""

    type: Literal["motion"]
    id: str
    tracks: list[Track] = Field(min_length=1, max_length=3)  # moved together, e.g. a balloon and its packet
    seconds: float = Field(default=2, gt=0, le=6)
    ground: str | None = None  # id of a ground block: a falling item stops when it touches it
    trace: bool = True  # dashed trail of each track's path
    ghosts: int = Field(default=0, ge=0, le=8)  # faint snapshots of the first track at equal time steps (strobe photo)
    show_velocity: bool = False  # live velocity arrow while moving
    color: Color = "blue"


DrawAction = Annotated[
    TextAction
    | LineAction
    | ArrowAction
    | RectAction
    | CircleAction
    | AngleAction
    | FieldMarkAction
    | HighlightAction
    | WireAction
    | ChargeAction
    | CompassAction
    | MagnetAction
    | AxesAction
    | RegionAction
    | FlowAction
    | ChainAction
    | RingAction
    | CurveAction
    | BodyAction
    | GroundAction
    | MotionAction,
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
    "covered_later", "not_covered", "prerequisite", "confused", "repeat", "continue", "off_topic", "unclear", "clarify",
    # Video lessons only: the student answers or reacts to the tutor; asks for the lecture so far / its topics
    "reply", "overview",
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
    language: str = "hinglish"
    # Shown on the lesson card to tell builds apart: language, voice, build number, prep --label
    tags: list[str] = []
    kind: Literal["generated", "video"] = "generated"


class LessonOut(BaseModel):
    slug: str
    title: str
    version: int
    # Tutor voice name (null = default) and its gender, for fillers/bridges and UI wording
    voice: str | None = None
    voice_gender: Literal["female", "male"] = "female"
    # What the tutor speaks ("hinglish" | "english"); the UI's own lines follow it
    language: str = "hinglish"
    tags: list[str] = []
    # Which whiteboard draws it: "svg" (default) or "skia" (CanvasKit)
    renderer: str = "svg"
    scenes: list[SceneOut]
    # Video lessons (kind "video"; scenes is then empty)
    kind: Literal["generated", "video"] = "generated"
    video: "VideoOut | None" = None
    chapters: list["ChapterOut"] = []
    checkpoints: list["CheckpointOut"] = []
    policy: "PlayerPolicy | None" = None


class GuestIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    class_level: int | None = Field(default=None, ge=6, le=12)
    language: Literal["hindi", "english", "hinglish"] = "hinglish"


class GuestUpdate(BaseModel):
    language: Literal["hindi", "english", "hinglish"]


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
    # Video lessons: where to resume playback (scene_idx is then the chapter, step_idx 0)
    position_ms: int | None = None

    @model_serializer(mode="wrap")
    def _omit_position_ms(self, handler):
        # Scene lessons keep their original shape
        data = handler(self)
        if data.get("position_ms") is None:
            data.pop("position_ms", None)
        return data


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
    # None for a follow-up answered on the board: its answer starts right away, without a canned line
    ack: Spoken | None
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


# ---- Video lessons (docs/plan/video-lesson-spec.md) ----

VideoAnswerWhen = Literal["now", "chapter_end", "target_chapter", "replay", "none"]
ThreadStatus = Literal["open", "resolved", "capped", "abandoned"]


class VideoOut(BaseModel):
    url: str
    duration_ms: int


class ChapterOut(BaseModel):
    idx: int
    topic: str
    title: str
    summary: str
    key_terms: list[str]
    start_ms: int
    end_ms: int


class ReplayRange(BaseModel):
    """A stretch of the lecture video, replayed as the teacher's own re-explanation."""

    start_ms: int
    end_ms: int


class ClipOut(BaseModel):
    """A narrated whiteboard step (same shape as an answer line or a re-teach step)."""

    say: str
    draw: list[dict] = []
    audio_url: str
    duration_ms: int


class CheckpointQuestion(BaseModel):
    """One question of a checkpoint, as stored in checkpoints.questions and sent to the player."""

    question: str
    say: str
    audio_url: str
    duration_ms: int
    options: list[str] = Field(min_length=2, max_length=4)
    correct_index: int
    concept: str
    explanation: str
    # After a wrong pick: a hint per option (null for the correct one), one retry, then the re-teach
    why_wrong: list[Spoken | None] = []
    replay: ReplayRange | None = None  # the teacher explaining this concept
    reteach: list[ClipOut] = []  # generated re-explanation, after the replay
    followup: Spoken | None = None  # "Which part confused you? Ask me, or let's continue."


class CheckpointOut(BaseModel):
    idx: int
    at_ms: int
    chapter_idx: int
    intro: Spoken
    questions: list[CheckpointQuestion]


class PlayerPolicy(BaseModel):
    """Limits the player follows; from Settings so they can be tuned without a release."""

    max_followups: int
    recap_after_turns: int
    recap_after_seconds: int
    rewind_ms: int
    idle_ms: int  # unanswered "Got it?" prompt: the thread is abandoned and the video resumes


class ThreadOut(BaseModel):
    id: str
    status: ThreadStatus
    turns: int
    audio_ms: int
    # Another follow-up is allowed (turns - 1 < max_followups)
    can_follow_up: bool
    # Played when the thread ends long enough to need it: "So, we were looking at …"
    recap: Spoken | None = None
    # A line to play now, e.g. "Which part is still unclear? Ask me." or the capped "Let's continue…"
    line: Spoken | None = None


class VideoQuestionOut(BaseModel):
    doubt_id: str
    thread_id: str
    turn: int
    transcript: str
    question: str
    intent: Intent
    answer_when: VideoAnswerWhen
    target_chapter_idx: int | None = None
    target_ms: int | None = None  # covered_later: when the callback plays (the target chapter's start)
    replay_from_ms: int | None = None  # repeat: where the video seeks back to
    ack: Spoken | None  # None for a follow-up answered now: its answer continues on the board
    callback: Spoken | None = None
    thread: ThreadOut


class ThreadReplyIn(BaseModel):
    understood: bool


class ThreadAbandonIn(BaseModel):
    # "error": answering failed, the reply's line is the sorry line
    reason: Literal["idle", "error"] = "idle"


class VideoPositionIn(BaseModel):
    at_ms: int = Field(ge=0)


class CheckpointAnswerIn(BaseModel):
    question_idx: int = Field(ge=0)
    chosen_index: int = Field(ge=0)
    kind: Literal["first", "retry"] = "first"


class CheckpointSkipIn(BaseModel):
    question_idx: int = Field(ge=0)


class AnsweredQuestion(BaseModel):
    checkpoint_idx: int
    question_idx: int
    kind: Literal["first", "retry", "skip"]
    correct: bool


class VideoStateDoubt(BaseModel):
    id: str
    at_ms: int
    thread_id: str | None
    turn: int
    question: str
    intent: str
    answered: bool


class VideoStateOut(BaseModel):
    session_id: str
    completed: bool
    position_ms: int | None
    answered: list[AnsweredQuestion]
    doubts: list[VideoStateDoubt]


class ClientEventIn(BaseModel):
    session_id: str | None = None
    thread_id: str | None = None
    doubt_id: str | None = None
    kind: str = Field(min_length=1, max_length=60)
    level: Literal["info", "warn", "error"] = "info"
    data: dict = {}


class SessionEventOut(BaseModel):
    id: int
    kind: str
    level: str
    thread_id: str | None
    doubt_id: str | None
    data: dict
    created_at: datetime


LessonOut.model_rebuild()
