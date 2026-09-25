"""Live status: the running scan says how far it is, the console streams
every change of the status, and the stream ends with the session without
ever keeping it alive."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from aperture import control, indexer, scanner
from aperture.console import opsapi, security
from aperture.console.app import app as console_app
from aperture.runtime import settings

PASSWORD = "console-live-password"


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(opsapi, "LIVE_TICK", 0.05)
    monkeypatch.setattr(opsapi, "LIVE_LIFETIME", 0.6)


def _first_status(client, limit=200):
    """Read the stream until its first `status` event, and return it."""
    with client.stream("GET", "/api/ops/live") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        event = None
        for n, line in enumerate(res.iter_lines()):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: ") and event == "status":
                return json.loads(line[len("data: "):])
            assert n < limit
    raise AssertionError("no status event")


def test_a_scan_reports_its_progress(indexed):
    seen = []
    scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                      progress=lambda done, total, file: seen.append((done, total)))
    assert seen and seen[-1][0] == seen[-1][1] == len(seen)


def test_progress_reaches_the_published_status(indexed, monkeypatch):
    monkeypatch.setattr(indexer, "_last_progress", 0.0)
    with indexer._scan_state_lock:
        indexer._scan_state.update(scanning=True)
    try:
        indexer._progress("photos", 3, 10, "berlin/gate.jpg")
        published = control.read_status()
        assert published["progress"] == {"phase": "photos", "done": 3, "total": 10,
                                         "current": "berlin/gate.jpg"}
    finally:
        with indexer._scan_state_lock:
            indexer._scan_state.update(scanning=False, progress=None)
        indexer._publish_status()


def test_the_stream_carries_the_status(indexed, console, fast):
    st = _first_status(console)
    assert "server" in st and "index" in st and st["index"]["images"] > 0


def test_the_stream_is_behind_the_door(indexed, fast):
    security.set_password(PASSWORD)
    security.reset()
    try:
        door = TestClient(console_app)
        assert door.get("/api/ops/live").status_code == 401
        assert door.post("/api/session", json={"password": PASSWORD}).status_code == 200
        assert "server" in _first_status(door)
    finally:
        security.clear_password()
        security.reset()


def test_the_stream_ends_when_the_session_does(indexed, console, fast, monkeypatch):
    """Signed out, timed out or a new password: the next look says `auth`
    and the stream stops -- the page goes to the door."""
    answers = iter([True, True])
    monkeypatch.setattr(security, "still_signed_in", lambda request: next(answers, False))
    with console.stream("GET", "/api/ops/live") as res:
        events = [line for line in res.iter_lines() if line.startswith("event: ")]
    assert events[-1] == "event: auth"


def test_asking_whether_a_session_stands_does_not_keep_it_alive():
    security.set_password(PASSWORD)
    security.reset()
    try:
        door = TestClient(console_app)
        door.post("/api/session", json={"password": PASSWORD})
        sid = door.cookies.get(security.COOKIE)
        sess = security._sessions[sid]
        sess.seen = time.time() - 100
        before = sess.seen

        class Req:
            cookies = {security.COOKIE: sid}

        assert security.still_signed_in(Req()) is True
        assert security._sessions[sid].seen == before
    finally:
        security.clear_password()
        security.reset()
