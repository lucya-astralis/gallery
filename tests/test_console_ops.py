"""The operations surface, served over HTTP.

The same reports the CLI prints, asserted through the console's routes — and,
where it matters, asserted to be *the same object*: a console that computes
its own version of `doctor` is two implementations of a check, which is the
thing this whole merge exists to stop.

The actions are covered too, because they are the part with consequences: a
scan request and a pause are writes, so they need a session, a CSRF token, and
a line in the audit log like every other write.
"""

import json

import pytest
from fastapi.testclient import TestClient

from aperture import control, ops
from aperture.console import security
from aperture.console.app import app as console_app
from aperture.runtime import settings

PASSWORD = "ops-test-password"


@pytest.fixture
def open_console(indexed):
    """No password: the console runs open on a loopback bind, which is what
    the tests that are not about the door want."""
    security.clear_password()
    yield TestClient(console_app)
    control.resume()


@pytest.fixture
def signed_in(indexed):
    security.set_password(PASSWORD)
    security._sessions.clear()
    security._failures.clear()
    client = TestClient(console_app)
    csrf = client.post("/api/session", json={"password": PASSWORD}).json()["csrf"]
    yield client, csrf
    control.resume()
    security.clear_password()
    security._sessions.clear()


# ----- state ------------------------------------------------------------
def test_status_answers_what_the_cli_answers(open_console, indexed):
    body = open_console.get("/api/ops/status").json()
    assert body["index"] == ops.status()["index"]
    assert body["index"]["images"] == indexed["result"]["indexed"]
    # The console adds what the CLI prints from its own process state.
    assert body["paths"]["photos"] == str(settings.photos_dir)
    assert body["role"] == settings.role
    assert body["read_only"] is False


def test_doctor_is_the_same_report(open_console, indexed):
    served = open_console.get("/api/ops/doctor").json()
    computed = ops.doctor()
    assert served["total"] == computed["total"]
    assert served["photos_on_disk"] == computed["photos_on_disk"]
    assert served["problems"] == computed["problems"]


def test_doctor_can_be_scoped(open_console):
    body = open_console.get("/api/ops/doctor?album=berlin").json()
    assert body["scope"] == "berlin"
    assert body["photos_on_disk"] == 5


def test_doctor_refuses_an_album_that_does_not_exist(open_console):
    res = open_console.get("/api/ops/doctor?album=atlantis")
    assert res.status_code == 404
    assert "atlantis" in res.json()["detail"]


# ----- actions ----------------------------------------------------------
def test_a_scan_request_lands_in_the_control_channel(open_console):
    body = open_console.post("/api/ops/scan", json={"album": "berlin"}).json()
    assert body["ok"] and body["request"]["album"] == "berlin"
    # The request is a file, and the console is not what reads it back.
    queued = control.take_scan_request()
    assert queued["id"] == body["request"]["id"]
    assert queued["by"] == "console"


def test_a_scan_for_an_unknown_album_is_refused(open_console):
    assert open_console.post("/api/ops/scan", json={"album": "atlantis"}).status_code == 404


def test_pause_and_resume_round_trip(open_console):
    assert open_console.post("/api/ops/pause", json={"reason": "rewiring"}).status_code == 200
    assert ops.status()["paused"] is True
    assert control.pause_info()["by"] == "console"
    assert control.pause_info()["reason"] == "rewiring"

    body = open_console.post("/api/ops/resume").json()
    assert body["was_paused"] is True
    assert ops.status()["paused"] is False


def test_scan_polling_says_not_yet_rather_than_not_found(open_console):
    body = open_console.get("/api/ops/scan/never-requested").json()
    assert body["result"] is None
    assert body["request_id"] == "never-requested"


# ----- the door applies here too ---------------------------------------
def test_operations_need_a_session(signed_in):
    client, _ = signed_in
    client.cookies.clear()
    for path in ("/api/ops/status", "/api/ops/doctor"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/ops/scan", json={}).status_code == 401


def test_an_action_needs_the_csrf_token(signed_in):
    client, csrf = signed_in
    assert client.post("/api/ops/pause", json={"reason": "no token"}).status_code == 403
    assert client.post("/api/ops/pause", json={"reason": "with token"},
                       headers={"X-Aperture-CSRF": csrf}).status_code == 200


def test_an_action_is_recorded(signed_in):
    client, csrf = signed_in
    log = settings.console_dir / "audit.log"
    before = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []

    client.post("/api/ops/scan", json={"album": "tech"},
                headers={"X-Aperture-CSRF": csrf})
    control.take_scan_request()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(before) + 1
    record = json.loads(lines[-1])
    assert record["action"] == "scan requested"
    assert record["target"] == "tech"
    assert "session" in record


def test_reading_is_allowed_read_only_but_acting_is_not(open_console, request):
    """READ_ONLY means the console cannot change anything — not merely that it
    cannot change photos. Pausing the indexer is a change.

    Settings are frozen on purpose (configuration does not change while the
    process runs) and the app binds the global at import, so the only way to
    exercise the other value is to reach past the freeze deliberately.
    """
    object.__setattr__(settings, "console_read_only", True)
    request.addfinalizer(
        lambda: object.__setattr__(settings, "console_read_only", False))
    assert open_console.get("/api/ops/status").json()["read_only"] is True
    for path in ("/api/ops/scan", "/api/ops/pause", "/api/ops/resume"):
        res = open_console.post(path, json={})
        assert res.status_code == 403, path
        assert "read-only" in res.json()["detail"]
