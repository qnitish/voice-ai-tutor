"""settings.visible_lessons: only the listed lessons are shown and can be started; the others stay in the DB."""

import pytest

from app.config import settings
from tests.conftest import create_guest, create_session
from tests.test_video_api import publish_video


@pytest.fixture
def two_lessons(lesson):
    return lesson, publish_video()


def slugs(client) -> set[str]:
    return {l["slug"] for l in client.get("/api/lessons").json()}


def test_empty_setting_shows_every_lesson(client, two_lessons):
    board, video = two_lessons
    assert {board.slug, video.slug} <= slugs(client)


def test_only_listed_lessons_are_shown(client, two_lessons, monkeypatch):
    board, video = two_lessons
    monkeypatch.setattr(settings, "visible_lessons", f" {video.slug} ,")
    assert slugs(client) == {video.slug}
    assert client.get(f"/api/lessons/{video.slug}").status_code == 200
    assert client.get(f"/api/lessons/{board.slug}").status_code == 404


def test_hidden_lesson_cannot_be_started_or_resumed(client, two_lessons, monkeypatch):
    board, video = two_lessons
    guest = create_guest(client)
    create_session(client, guest, board.slug)  # before it was hidden
    monkeypatch.setattr(settings, "visible_lessons", video.slug)
    r = client.post("/api/sessions", json={"guest_id": guest, "lesson_slug": board.slug})
    assert r.status_code == 404
    assert client.get(f"/api/guests/{guest}/lessons/{board.slug}/resume").status_code == 404
    assert all(p["slug"] != board.slug for p in client.get(f"/api/guests/{guest}/progress").json())
