"""Spec section 3 (backend): saving and resuming the position in a lesson."""

import pytest

from tests.conftest import create_guest, create_session, default_scenes, finish, publish, save_position


def resume(client, guest_id: str, slug: str = "magnetism-01"):
    return client.get(f"/api/guests/{guest_id}/lessons/{slug}/resume")


@pytest.fixture
def session(client, lesson):
    guest = create_guest(client)
    s = create_session(client, guest, lesson.slug)
    s["guest_id"] = guest
    return s


# --- POST /api/sessions/{id}/position ------------------------------------------------


def test_save_position_returns_the_position(client, session):
    r = save_position(client, session["id"], 1, 2)
    assert r.status_code == 200
    assert r.json() == {"scene_idx": 1, "step_idx": 2}


def test_save_position_last_step_of_last_scene_is_valid(client, session):
    assert save_position(client, session["id"], 2, 1).status_code == 200


def test_save_position_first_step_is_valid(client, session):
    assert save_position(client, session["id"], 0, 0).status_code == 200


@pytest.mark.parametrize("scene_idx,step_idx", [(3, 0), (-1, 0), (0, 2), (1, 3), (2, 2), (0, -1)])
def test_save_position_rejects_invalid_indexes(client, session, scene_idx, step_idx):
    assert save_position(client, session["id"], scene_idx, step_idx).status_code == 400


def test_save_position_unknown_session_is_404(client, lesson):
    assert save_position(client, "00000000-0000-0000-0000-000000000000", 0, 0).status_code == 404


def test_save_position_last_write_wins(client, session):
    save_position(client, session["id"], 1, 2)
    save_position(client, session["id"], 0, 1)
    body = resume(client, session["guest_id"]).json()
    assert body["scene_idx"] == 0
    assert body["step_idx"] == 1


# --- GET /api/guests/{guest_id}/lessons/{slug}/resume --------------------------------


def test_resume_returns_saved_position(client, session):
    save_position(client, session["id"], 1, 2)
    r = resume(client, session["guest_id"])
    assert r.status_code == 200
    assert r.json() == {"session_id": session["id"], "scene_idx": 1, "step_idx": 2}


def test_resume_is_null_when_guest_has_no_session(client, lesson):
    guest = create_guest(client)
    r = resume(client, guest)
    assert r.status_code == 200
    assert r.json() is None


def test_resume_is_null_when_no_position_saved(client, session):
    r = resume(client, session["guest_id"])
    assert r.status_code == 200
    assert r.json() is None


def test_resume_is_null_for_completed_session(client, session):
    save_position(client, session["id"], 1, 1)
    finish(client, session["id"])
    assert resume(client, session["guest_id"]).json() is None


def test_resume_ignores_sessions_on_older_lesson_version(client, session):
    save_position(client, session["id"], 1, 1)
    publish("magnetism-01", scenes=default_scenes())  # version 2
    assert resume(client, session["guest_id"]).json() is None


def test_resume_picks_most_recently_updated_session(client, lesson):
    guest = create_guest(client)
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    save_position(client, s1["id"], 0, 1)
    save_position(client, s2["id"], 1, 0)
    assert resume(client, guest).json()["session_id"] == s2["id"]
    save_position(client, s1["id"], 2, 0)
    assert resume(client, guest).json() == {"session_id": s1["id"], "scene_idx": 2, "step_idx": 0}


def test_resume_falls_back_to_other_session_when_latest_is_completed(client, lesson):
    guest = create_guest(client)
    s1 = create_session(client, guest, lesson.slug)
    s2 = create_session(client, guest, lesson.slug)
    save_position(client, s1["id"], 0, 1)
    save_position(client, s2["id"], 1, 0)
    finish(client, s2["id"])
    assert resume(client, guest).json()["session_id"] == s1["id"]


def test_resume_unknown_guest_is_404(client, lesson):
    assert resume(client, "00000000-0000-0000-0000-000000000000").status_code == 404


def test_resume_unknown_slug_is_404(client, session):
    assert resume(client, session["guest_id"], "no-such-lesson").status_code == 404


def test_resume_is_per_lesson(client, lesson):
    publish("light-02")
    guest = create_guest(client)
    s_mag = create_session(client, guest, "magnetism-01")
    save_position(client, s_mag["id"], 1, 1)
    assert resume(client, guest, "light-02").json() is None
    assert resume(client, guest, "magnetism-01").json()["session_id"] == s_mag["id"]


def test_resume_is_per_guest(client, lesson):
    g1 = create_guest(client, "One")
    g2 = create_guest(client, "Two")
    s1 = create_session(client, g1, lesson.slug)
    save_position(client, s1["id"], 1, 1)
    assert resume(client, g2).json() is None
