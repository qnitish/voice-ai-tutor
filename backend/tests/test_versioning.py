"""Spec section 2: lesson versioning."""

from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import (
    answer,
    ask,
    correct_index,
    create_guest,
    create_session,
    default_scenes,
    lesson_detail,
    publish,
    scene,
    step,
    submit_check,
)


def lesson_rows(slug: str) -> list[int]:
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT version FROM lessons WHERE slug = :slug ORDER BY version"), {"slug": slug})
        return [r[0] for r in rows]
    finally:
        db.close()


def test_first_publish_has_version_1():
    assert publish("magnetism-01").version == 1


def test_republish_increments_version():
    publish("magnetism-01")
    assert publish("magnetism-01").version == 2
    assert publish("magnetism-01").version == 3


def test_republish_keeps_older_rows():
    first = publish("magnetism-01")
    second = publish("magnetism-01")
    assert first.id != second.id
    assert lesson_rows("magnetism-01") == [1, 2]


def test_versions_are_per_slug():
    publish("magnetism-01")
    publish("magnetism-01")
    assert publish("light-02").version == 1


def test_list_lessons_has_one_entry_per_slug_with_latest_version(client):
    publish("magnetism-01", title="Old title")
    publish("magnetism-01", title="New title")
    publish("light-02", title="Light")
    r = client.get("/api/lessons")
    assert r.status_code == 200
    entries = {e["slug"]: e for e in r.json()}
    assert len(r.json()) == 2
    assert entries["magnetism-01"]["version"] == 2
    assert entries["magnetism-01"]["title"] == "New title"
    assert entries["light-02"]["version"] == 1


def test_list_lessons_entry_fields(client):
    publish("magnetism-01")
    entry = client.get("/api/lessons").json()[0]
    assert entry["slug"] == "magnetism-01"
    assert entry["title"] == "Magnetism"
    assert entry["scene_count"] == 3
    assert entry["version"] == 1


def test_get_lesson_returns_latest_version(client):
    publish("magnetism-01", title="Old title")
    publish("magnetism-01", title="New title", scenes=[scene("Only", "goal", ["x"], [step("Hi.")], None)])
    body = lesson_detail(client, "magnetism-01")
    assert body["version"] == 2
    assert body["title"] == "New title"
    assert len(body["scenes"]) == 1


def test_create_session_uses_latest_version_and_reports_it(client):
    publish("magnetism-01")
    publish("magnetism-01")
    guest = create_guest(client)
    session = create_session(client, guest, "magnetism-01")
    assert session["lesson_slug"] == "magnetism-01"
    assert session["lesson_version"] == 2
    assert session["guest"]
    assert session["id"]


def test_create_session_on_first_version(client):
    publish("magnetism-01")
    guest = create_guest(client)
    assert create_session(client, guest, "magnetism-01")["lesson_version"] == 1


def test_old_version_session_data_survives_republish(client, llm):
    publish("magnetism-01")
    guest = create_guest(client)
    session = create_session(client, guest, "magnetism-01")
    ci = correct_index(client, "magnetism-01", 0)
    assert submit_check(client, session["id"], 0, ci).status_code == 200
    llm.classify(intent="not_covered", question="purana sawal")
    doubt = ask(client, session["id"], scene_idx=1, step_idx=0)
    answer(client, doubt)

    publish("magnetism-01", scenes=default_scenes()[:1])  # version 2

    history = client.get(f"/api/guests/{guest}/history").json()
    entry = next(h for h in history if h["session_id"] == session["id"])
    assert entry["version"] == 1
    assert entry["checks_correct"] == 1
    assert entry["checks_total"] == 1
    assert [d["question"] for d in entry["doubts"]] == ["purana sawal"]
    assert entry["doubts"][0]["answered"] is True

