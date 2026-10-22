"""Shared fixtures for the backend test-suite.

Environment variables are set *before* anything from ``app`` is imported so the
app binds to the dedicated test database and a throw-away media directory.
"""

import io
import os
import sys
import tempfile
import types
import typing
import wave

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_DATA_DIR = tempfile.mkdtemp(prefix="voice-tutor-tests-")
os.environ["DATABASE_URL"] = "postgresql+psycopg://tutor:tutor@localhost:5440/voice_tutor_test"
os.environ["DATA_DIR"] = _DATA_DIR

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import app.llm  # noqa: E402
import app.providers  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.lesson_store import publish_lesson  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

DATA_DIR = _DATA_DIR


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def make_wav(seconds: float, rate: int = 16000) -> bytes:
    """Mono 16-bit PCM silence of the given length, built with the wave module."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


QUESTION_WAV = make_wav(1.0, 16000)  # > 300 ms, as the spec requires
TTS_WAV = make_wav(0.2, 24000)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSTT:
    max_seconds = 30

    def __init__(self, text: str = "yeh kya hai"):
        self.text = text
        self.calls = 0

    async def transcribe(self, wav: bytes) -> str:
        self.calls += 1
        return self.text


class FakeTTS:
    def __init__(self):
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return TTS_WAV


def _default_for(annotation):
    """A valid-looking default value for a pydantic field annotation."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or (origin is not None and origin.__name__ == "UnionType"):
        if type(None) in args:
            return None
        return _default_for(args[0])
    if origin is typing.Literal:
        return args[0]
    if origin in (list, set, tuple):
        return origin()
    if origin is dict:
        return {}
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return blank_instance(annotation)
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, int):
            return 0
        if issubclass(annotation, float):
            return 0.0
        if issubclass(annotation, str):
            return ""
        if issubclass(annotation, (list, set, tuple, dict)):
            return annotation()
    return None


def blank_instance(schema):
    """Instantiate an arbitrary pydantic schema with neutral values.

    Used for LLM calls the tests do not care about (e.g. the session summary) so the
    app keeps working without the network.
    """
    try:
        return schema()
    except Exception:
        pass
    values = {}
    for name, field in schema.model_fields.items():
        if field.is_required():
            values[name] = _default_for(field.annotation)
    return schema(**values)


class FakeLLM:
    """Recording replacement for ``app.llm.complete_json`` / ``app.llm.stream_lines``.

    ``classify(**fields)`` sets the Classification the next routing call returns; several
    calls queue several results (consumed in order, the last one is reused afterwards).
    """

    def __init__(self):
        self.default = dict(intent="not_covered", question="yeh kya hai", confidence=1.0)
        self.queue: list[dict] = []
        self.calls: list[dict] = []  # every complete_json call
        self.stream_calls: list[dict] = []  # every stream_lines call
        self.answer_lines: list[str] = ['{"say": "Yeh answer hai.", "draw": []}']

    def classify(self, **fields) -> None:
        payload = dict(intent="not_covered", question="yeh kya hai", confidence=1.0)
        payload.update(fields)
        self.queue.append(payload)

    @property
    def routing_calls(self) -> list[dict]:
        return [c for c in self.calls if c["schema"].__name__ == "Classification"]

    async def complete_json(self, system: str, user: str, schema: type, temperature: float = 0.4, model=None):
        self.calls.append(dict(system=system, user=user, schema=schema, temperature=temperature, model=model))
        if schema.__name__ == "Classification":
            payload = self.queue.pop(0) if self.queue else self.default
            if len(self.queue) == 0:
                self.default = payload
            return schema(**payload)
        return blank_instance(schema)

    async def stream_lines(self, system: str, user: str, temperature: float = 0.5, model=None):
        self.stream_calls.append(dict(system=system, user=user, temperature=temperature, model=model))
        for line in self.answer_lines:
            yield line


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def stt():
    provider = FakeSTT()
    app.providers.set_stt(provider)
    yield provider
    app.providers.set_stt(None)


@pytest.fixture
def tts():
    provider = FakeTTS()
    app.providers.set_tts(provider)
    yield provider
    app.providers.set_tts(None)


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(app.llm, "complete_json", fake.complete_json)
    monkeypatch.setattr(app.llm, "stream_lines", fake.stream_lines)
    return fake


@pytest.fixture
def client(stt, tts, llm):
    with TestClient(fastapi_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Lesson factory
# ---------------------------------------------------------------------------


def step(say: str, draw: list | None = None) -> dict:
    return {"say": say, "draw": draw or [], "audio_url": "/media/test/step.wav", "duration_ms": 1000}


def check(concept: str, options: list[str] | None = None, correct_index: int = 0, question: str | None = None) -> dict:
    options = options or ["Alpha", "Beta", "Gamma", "Delta"]
    return {
        "question": question or f"Question about {concept}?",
        "say": f"Question about {concept}?",
        "options": options,
        "correct_index": correct_index,
        "concept": concept,
        "explanation": f"Explanation about {concept}.",
        "audio_url": "/media/test/check.wav",
    }


def scene(title: str, goal: str, key_terms: list[str], steps: list[dict], check_question: dict | None = None) -> dict:
    return {"title": title, "goal": goal, "key_terms": key_terms, "check_question": check_question, "steps": steps}


def default_scenes() -> list[dict]:
    """Three scenes with 2 + 3 + 2 = 7 steps; scenes 0 and 1 carry a check question."""
    return [
        scene(
            "Magnets",
            "Understand what a magnet is",
            ["magnet", "pole"],
            [step("Scene zero step zero: magnet dekho."), step("Scene zero step one: do pole hote hain.")],
            check("magnetic poles"),
        ),
        scene(
            "Attraction",
            "See how poles attract and repel",
            ["pole", "attraction"],
            [
                step("Scene one step zero: north aur south."),
                step("Scene one step one: like poles repel."),
                step("Scene one step two: unlike poles attract."),
            ],
            check("attraction"),
        ),
        scene(
            "Compass",
            "Build a compass",
            ["compass"],
            [step("Scene two step zero: needle lo."), step("Scene two step one: needle ghoomti hai.")],
            None,
        ),
    ]


def publish(slug: str = "magnetism-01", scenes: list[dict] | None = None, title: str = "Magnetism") -> types.SimpleNamespace:
    """Publish a lesson and commit; returns a detached snapshot of the Lesson row."""
    db = SessionLocal()
    try:
        lesson = publish_lesson(db, slug, title, "transcript text", scenes if scenes is not None else default_scenes())
        db.commit()
        db.refresh(lesson)
        return types.SimpleNamespace(id=lesson.id, slug=lesson.slug, title=lesson.title, version=lesson.version)
    finally:
        db.close()


@pytest.fixture
def lesson():
    return publish()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def create_guest(client: TestClient, name: str = "Asha") -> str:
    r = client.post("/api/guests", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def create_session(client: TestClient, guest_id: str, slug: str = "magnetism-01") -> dict:
    r = client.post("/api/sessions", json={"guest_id": guest_id, "lesson_slug": slug})
    assert r.status_code in (200, 201), r.text
    return r.json()


def ask(client: TestClient, session_id: str, scene_idx: int = 0, step_idx: int = 0) -> dict:
    """Upload a question (1 s of silence; the STT fake decides the transcript)."""
    r = client.post(
        f"/api/sessions/{session_id}/questions",
        files={"audio": ("question.wav", QUESTION_WAV, "audio/wav")},
        data={"scene_idx": str(scene_idx), "step_idx": str(step_idx)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def doubt_id(doubt: dict) -> str:
    return doubt.get("id") or doubt.get("doubt_id")


def answer(client: TestClient, doubt: dict | str) -> str:
    """Consume the NDJSON answer stream of a doubt and return the raw body."""
    did = doubt if isinstance(doubt, str) else doubt_id(doubt)
    r = client.get(f"/api/doubts/{did}/answer")
    assert r.status_code == 200, r.text
    return r.text


def save_position(client: TestClient, session_id: str, scene_idx: int, step_idx: int):
    return client.post(f"/api/sessions/{session_id}/position", json={"scene_idx": scene_idx, "step_idx": step_idx})


def submit_check(client: TestClient, session_id: str, scene_idx: int, chosen_index: int):
    return client.post(f"/api/sessions/{session_id}/check", json={"scene_idx": scene_idx, "chosen_index": chosen_index})


def finish(client: TestClient, session_id: str) -> dict:
    r = client.post(f"/api/sessions/{session_id}/summary", json={})
    assert r.status_code == 200, r.text
    return r.json()


def lesson_detail(client: TestClient, slug: str = "magnetism-01") -> dict:
    r = client.get(f"/api/lessons/{slug}")
    assert r.status_code == 200, r.text
    return r.json()


def correct_index(client: TestClient, slug: str, scene_idx: int) -> int:
    """The stored (post-shuffle) correct option index of a scene's check question."""
    return lesson_detail(client, slug)["scenes"][scene_idx]["check_question"]["correct_index"]


def wrong_index(client: TestClient, slug: str, scene_idx: int) -> int:
    cq = lesson_detail(client, slug)["scenes"][scene_idx]["check_question"]
    return next(i for i in range(len(cq["options"])) if i != cq["correct_index"])


def mastery(client: TestClient, guest_id: str) -> list[dict]:
    r = client.get(f"/api/guests/{guest_id}/mastery")
    assert r.status_code == 200, r.text
    return r.json()


def mastery_of(client: TestClient, guest_id: str, concept: str) -> dict | None:
    return next((m for m in mastery(client, guest_id) if m["concept"] == concept), None)


def session_state(client: TestClient, session_id: str) -> dict:
    r = client.get(f"/api/sessions/{session_id}/state")
    assert r.status_code == 200, r.text
    return r.json()
