"""Spec section 6: GET /api/bridges."""

import json
import os

import pytest

from tests.conftest import DATA_DIR

BRIDGES_PATH = os.path.join(DATA_DIR, "media", "fillers", "bridges.json")


@pytest.fixture(autouse=True)
def no_bridges_file():
    if os.path.exists(BRIDGES_PATH):
        os.remove(BRIDGES_PATH)
    yield
    if os.path.exists(BRIDGES_PATH):
        os.remove(BRIDGES_PATH)


def write_bridges(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(BRIDGES_PATH), exist_ok=True)
    with open(BRIDGES_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f)


def test_bridges_empty_when_file_missing(client):
    r = client.get("/api/bridges")
    assert r.status_code == 200
    assert r.json() == []


def test_bridges_returns_file_entries(client):
    entries = [
        {"text": "Chalo, wapas lesson pe aate hain.", "audio_url": "/media/fillers/bridge-0.wav", "duration_ms": 1800},
        {"text": "Theek hai, aage badhte hain.", "audio_url": "/media/fillers/bridge-1.wav", "duration_ms": 1500},
    ]
    write_bridges(entries)
    r = client.get("/api/bridges")
    assert r.status_code == 200
    assert r.json() == entries


def test_bridges_entry_shape(client):
    write_bridges([{"text": "Wapas chalte hain.", "audio_url": "/media/fillers/b.wav", "duration_ms": 900}])
    entry = client.get("/api/bridges").json()[0]
    assert set(entry.keys()) == {"text", "audio_url", "duration_ms"}
    assert isinstance(entry["duration_ms"], int)
