"""The operations surface, served over HTTP.

The same reports the CLI prints, asserted through the console's routes — and,
where it matters, asserted to be *the same object*: a console that computes
its own version of `doctor` is two implementations of a check, which is the
thing this whole merge exists to stop.

The actions are covered too, because they are the part with consequences: a
scan request and a pause are writes, so they need a session, a CSRF token, and
a line in the audit log like every other write.
"""

import io
import json
import tarfile

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from aperture import control, indexer, ops, reports, scanner
from aperture.console import security
from aperture.console.app import app as console_app
from aperture import runtime
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
    security.reset()
    client = TestClient(console_app)
    csrf = client.post("/api/session", json={"password": PASSWORD}).json()["csrf"]
    yield client, csrf
    control.resume()
    security.clear_password()
    security.reset()


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
    for path in ("/api/ops/status", "/api/ops/doctor", "/api/ops/disk",
                 "/api/ops/export", "/api/ops/photo?path=berlin/gate.jpg",
                 "/api/ops/jobs/abc-123"):
        assert client.get(path).status_code == 401, path
    for path in ("/api/ops/scan", "/api/ops/jobs", "/api/ops/password"):
        assert client.post(path, json={}).status_code == 401, path


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


def test_reading_is_allowed_read_only_but_acting_is_not(open_console):
    """READ_ONLY means the console cannot change anything — not merely that it
    cannot change photos. Pausing the indexer is a change, and so is writing a
    cfg file.

    Both guards ask the setting itself. The cfg routes used to ask a copy
    bound at import, which no test could reach and no operator could change
    without a restart: a console switched to read-only refused the operations
    panel and wrote the file anyway.
    """
    with runtime.override(console_read_only=True):
        assert open_console.get("/api/ops/status").json()["read_only"] is True
        assert open_console.get("/api/meta").json()["read_only"] is True
        for path in ("/api/ops/scan", "/api/ops/pause", "/api/ops/resume",
                     "/api/ops/jobs", "/api/ops/password"):
            res = open_console.post(path, json={})
            assert res.status_code == 403, path
            assert "read-only" in res.json()["detail"]
        res = open_console.put("/api/album/cfg",
                               json={"album": "tech", "values": {"name": "Nope"}})
        assert res.status_code == 403, res.text
        assert "read-only" in res.json()["detail"]


# ----- disk -------------------------------------------------------------
def test_disk_is_the_same_walk(open_console, indexed):
    served = open_console.get("/api/ops/disk").json()
    computed = jsonable_encoder(ops.disk_usage())
    assert served["volumes"] and all(v["total"] >= v["free"] for v in served["volumes"])
    # the walk's own time, and free space on a disk this very test run is
    # writing to, are the two things two calls cannot agree on
    for body in (served, computed):
        body.pop("took_ms")
        for vol in body["volumes"]:
            vol.pop("free"), vol.pop("used")
    assert served == computed
    tiers = {t["key"]: t for t in served["tiers"]}
    # every indexed photo got a thumbnail and a preview
    assert tiers["thumbnails"]["files"] >= indexed["result"]["indexed"]
    assert tiers["previews"]["bytes"] > 0


# ----- every other report ----------------------------------------------
REPORTS = [
    ("/api/ops/featured", ops.featured_report),
    ("/api/ops/derivatives?album=berlin", lambda: ops.derivatives_report("berlin")),
    ("/api/ops/cfg?album=berlin", lambda: reports.cfg_report("berlin")),
    ("/api/ops/cfg?gallery=true", lambda: reports.cfg_report(gallery=True)),
    ("/api/ops/photo?path=berlin/gate.jpg", lambda: reports.photo_report("berlin/gate.jpg")),
    ("/api/ops/albums", reports.albums_report),
    ("/api/ops/albums?album=berlin", lambda: reports.album_report("berlin")),
    ("/api/ops/trips", reports.trips_report),
    ("/api/ops/tags", reports.tags_report),
    ("/api/ops/tags?tag=workspace", lambda: reports.tag_report("workspace")),
    ("/api/ops/gps?album=tech", lambda: ops.gps_audit("tech")),
    ("/api/ops/search?q=gate", lambda: reports.search_report("gate", limit=200)),
    ("/api/ops/i18n", reports.i18n_report),
    ("/api/ops/archive", reports.archive_report),
    ("/api/ops/export/contents", reports.export_report),
]


@pytest.mark.parametrize("path, compute", REPORTS, ids=[p for p, _ in REPORTS])
def test_every_cli_report_is_served_as_the_same_report(open_console, path, compute):
    """One implementation per report: the route serializes the function the
    CLI renders, and nothing in between computes a version of its own."""
    res = open_console.get(path)
    assert res.status_code == 200, res.text
    assert res.json() == jsonable_encoder(compute())


def test_the_welcome_report_has_both_devices(open_console):
    # the feed itself is random in showcase mode, so only its shape compares
    body = open_console.get("/api/ops/welcome").json()
    assert [d["device"] for d in body["devices"]] == ["desktop", "mobile"]
    assert body["keywords"] == sorted(body["keywords"])


def test_a_photo_that_is_not_there_names_the_near_miss(open_console):
    res = open_console.get("/api/ops/photo?path=berlin/GATE.JPG")
    assert res.status_code == 404
    assert "berlin/gate.jpg" in res.json()["detail"]


def test_a_report_on_an_unknown_album_is_a_404(open_console):
    for path in ("/api/ops/cfg?album=atlantis", "/api/ops/tags?album=atlantis",
                 "/api/ops/derivatives?album=atlantis", "/api/ops/gps?album=atlantis"):
        assert open_console.get(path).status_code == 404, path


# ----- jobs -------------------------------------------------------------
def _run_queued(job_id):
    """The indexer is not running under test: do what its control loop does
    with the oldest queued job."""
    job = control.take_job()
    assert job is not None and job["id"] == job_id
    return indexer.run_job(job)


def test_a_rebuild_is_a_job_the_indexer_runs(open_console):
    thumb = ops.derivatives("berlin/gate.jpg")["thumb"]
    thumb.unlink()
    body = open_console.post("/api/ops/jobs", json={"kind": "rebuild", "album": "berlin"}).json()
    job = body["job"]
    assert job["by"] == "console" and job["params"] == {"album": "berlin", "all": False}
    state = open_console.get("/api/ops/jobs/" + job["id"]).json()
    assert state["pending"] is True and state["result"] is None

    _run_queued(job["id"])
    state = open_console.get("/api/ops/jobs/" + job["id"]).json()
    assert state["pending"] is False
    assert state["result"]["error"] is None
    assert state["result"]["result"]["built"] >= 1
    assert thumb.is_file()


def test_a_leftover_is_counted_reported_and_pruned(open_console):
    if scanner.THUMB_EXT == ".jpg":
        pytest.skip("the thumbnail tier writes JPEG, so a .jpg is no leftover")
    stray = settings.thumbs_dir / "berlin" / "left-over.jpg"
    stray.write_bytes(b"x" * 2048)
    try:
        thumbs = next(t for t in open_console.get("/api/ops/disk").json()["tiers"]
                      if t["key"] == "thumbnails")
        assert ".jpg" in thumbs["stale_formats"]
        assert str(stray) in open_console.get("/api/ops/derivatives").json()["orphans"]

        job = open_console.post("/api/ops/jobs", json={"kind": "prune"}).json()["job"]
        summary = _run_queued(job["id"])
        assert summary["result"]["pruned"] == 1
        assert summary["result"]["freed_bytes"] == 2048
        assert not stray.exists()
    finally:
        stray.unlink(missing_ok=True)


def test_featured_and_gps_jobs_run(open_console):
    job = open_console.post("/api/ops/jobs", json={"kind": "featured"}).json()["job"]
    assert _run_queued(job["id"])["result"]["db_flagged"] == ops.featured_report()["db_flagged"]
    # the fixture EXIF has no coordinates, so a strip rewrites nothing and
    # asks for no scan
    job = open_console.post("/api/ops/jobs", json={"kind": "gps_strip", "album": "tech"}).json()["job"]
    result = _run_queued(job["id"])["result"]
    assert result["stripped"] == [] and result["scan_requested"] is False


def test_a_job_that_does_not_exist_is_refused(open_console):
    assert open_console.post("/api/ops/jobs", json={"kind": "rm -rf"}).status_code == 400
    assert open_console.post("/api/ops/jobs",
                             json={"kind": "rebuild", "album": "atlantis"}).status_code == 404
    assert control.pending_jobs() == []


def test_a_job_id_never_reaches_the_filesystem(open_console):
    for bad in ("not-an-id!", "..%2F..%2Fstatus", "abc"):
        assert open_console.get("/api/ops/jobs/" + bad).status_code == 404, bad
    assert control.job_result("../status") is None


def test_resume_can_ask_for_a_scan(open_console):
    open_console.post("/api/ops/pause", json={})
    body = open_console.post("/api/ops/resume", json={"scan": True}).json()
    assert body["was_paused"] is True
    assert control.take_scan_request()["id"] == body["request"]["id"]


# ----- export -----------------------------------------------------------
def test_the_export_downloads_every_hand_written_file(open_console):
    res = open_console.get("/api/ops/export")
    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith("attachment;")
    with tarfile.open(fileobj=io.BytesIO(res.content), mode="r:gz") as tar:
        names = tar.getnames()
    assert names == reports.export_report()["files"]
    assert ".gallery/gallery.cfg" in names
    # the hand-written files, never a photograph
    assert all("/.album/" in "/" + n or n.startswith(".gallery/") for n in names)


# ----- the password -----------------------------------------------------
def test_changing_the_password_asks_for_the_old_one_and_ends_every_session(signed_in):
    client, csrf = signed_in
    token = {"X-Aperture-CSRF": csrf}
    wrong = client.post("/api/ops/password", headers=token,
                        json={"current": "not it", "password": "a-new-password"})
    # 403, not 401: a 401 would send the operator to the door for a typo
    assert wrong.status_code == 403
    assert security.verify_password(PASSWORD)
    short = client.post("/api/ops/password", headers=token,
                        json={"current": PASSWORD, "password": "short"})
    assert short.status_code == 400

    ok = client.post("/api/ops/password", headers=token,
                     json={"current": PASSWORD, "password": "a-new-password"})
    assert ok.status_code == 200, ok.text
    assert security.verify_password("a-new-password")
    assert client.get("/api/ops/status").status_code == 401


def test_the_password_is_only_cleared_where_the_console_may_run_open(signed_in):
    client, csrf = signed_in
    token = {"X-Aperture-CSRF": csrf}
    with runtime.override(console_bind="0.0.0.0", console_allow_open=False):
        assert client.get("/api/ops/status").json()["auth"]["may_run_open"] is False
        res = client.request("DELETE", "/api/ops/password", headers=token,
                             json={"current": PASSWORD})
        assert res.status_code == 409
        assert security.password_is_set()
    with runtime.override(console_bind="127.0.0.1"):
        res = client.request("DELETE", "/api/ops/password", headers=token,
                             json={"current": PASSWORD})
        assert res.status_code == 200, res.text
    assert not security.password_is_set()
