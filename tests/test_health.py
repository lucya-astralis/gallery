"""The self-watch and the failsafe (aperture/health.py).

The case they are for: the NAS the photos live on switches itself off at
night while the app keeps running. What must hold then:

  * the gallery answers 503 with a page that says what is going on -- never a
    bare 500 -- and /healthz says so to a monitor;
  * it takes two failed rounds (or one plus a request that tripped over it) to
    go down, and three good ones to come back;
  * a directory that hangs instead of failing does not stop the watch;
  * nothing is removed from the index on the way: not by a scan whose walk
    lost folders, not by the watcher, not by a scan while down.
"""

import errno
import threading
import time

import pytest

from aperture import db, health, indexer, scanner
from aperture.gallery import pages
from aperture.runtime import override, settings


@pytest.fixture(autouse=True)
def fresh():
    health._reset_for_tests()
    yield
    health._reset_for_tests()


@pytest.fixture
def unmounted(tmp_path):
    """The photo tree as a `nofail` share that did not come up leaves it:
    an empty directory where the mount point is."""
    empty = tmp_path / "mnt"
    empty.mkdir()
    with override(photos_dir=empty):
        yield empty


def _down(n=health.DOWN_AFTER):
    for _ in range(n):
        health.check_now()


def _rows() -> int:
    return db.conn().execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]


# ----- the verdict -----------------------------------------------------------
def test_a_healthy_tree_checks_out(indexed):
    snap = health.check_now()
    assert snap["state"] in ("ok", "warn")
    storage = {c["key"]: c["level"] for c in snap["checks"] if c["critical"]}
    assert storage == {"photos": "ok", "data": "ok", "thumbs": "ok", "previews": "ok"}
    assert not health.is_down()


def test_an_empty_mount_point_is_down_after_two_rounds(indexed, unmounted):
    snap = health.check_now()
    assert snap["state"] != "down", "one bad round is not enough"
    photos = next(c for c in snap["checks"] if c["key"] == "photos")
    assert photos["level"] == "error" and "not mounted" in photos["detail"]

    snap = health.check_now()
    assert snap["state"] == "down" and snap["failsafe"]
    assert "photos" in snap["reason"]
    assert snap["events"][-1]["to"] == "down"


def test_a_vanished_photo_dir_is_down_too(indexed, tmp_path):
    with override(photos_dir=tmp_path / "gone"):
        _down()
        assert health.is_down()


def test_a_fresh_install_without_photos_is_not_down(tmp_path, indexed, monkeypatch):
    monkeypatch.setattr(health, "_index_has_rows", lambda: False)
    with override(photos_dir=tmp_path / "not-yet"):
        _down()
        assert not health.is_down()


def test_it_comes_back_only_after_it_stayed_back(indexed, unmounted, photos_dir):
    _down()
    with override(photos_dir=photos_dir):
        for _ in range(health.UP_AFTER - 1):
            assert health.check_now()["state"] == "down"
        assert health.check_now()["state"] != "down"
    assert health.snapshot()["events"][-1]["from"] == "down"


def test_a_request_that_tripped_over_it_makes_one_round_enough(indexed, unmounted):
    health.suspect(OSError(errno.EHOSTDOWN, "Host is down"))
    assert health.snapshot()["state"] == "down"


def test_a_hanging_directory_does_not_hang_the_watch(indexed, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(health, "PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr(health, "_probe_photos", lambda: release.wait(10))
    try:
        started = time.monotonic()
        snap = health.check_now()
        assert time.monotonic() - started < 3
        photos = next(c for c in snap["checks"] if c["key"] == "photos")
        assert "not answering" in photos["detail"]
        stuck = [t for t in threading.enumerate() if t.name == "health-probe-photos"]
        health.check_now()                          # a second round ...
        again = [t for t in threading.enumerate() if t.name == "health-probe-photos"]
        assert len(again) == len(stuck) == 1        # ... starts no second probe
        assert health.snapshot()["state"] == "down"
    finally:
        release.set()


def test_failsafe_off_still_holds_the_writers(indexed, unmounted):
    with override(failsafe=False):
        _down()
        assert not health.is_down()           # visitors: no failsafe page
        assert health.storage_down()          # writers: still hold still


# ----- what the world sees ---------------------------------------------------
def test_the_gallery_answers_503_with_a_page_not_a_500(client, unmounted):
    _down()
    page = client.get("/", headers={"accept-language": "de"})
    assert page.status_code == 503
    assert page.headers["retry-after"] == str(health.RETRY_AFTER)
    assert "text/html" in page.headers["content-type"]
    assert "Das Archiv schläft gerade." in page.text
    assert "Fixture Archive" in page.text or "Gallery" in page.text
    assert 'href="/_failsafe.css"' in page.text
    assert "content-security-policy" in page.headers     # still leaves with the CSP

    css = client.get("/_failsafe.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")

    api = client.get("/api/stats")
    assert api.status_code == 503 and api.json()["failsafe"] is True

    assert client.get("/thumb/berlin/gate.jpg").status_code == 503


def test_the_page_names_the_archive_it_last_saw(client, indexed):
    health.check_now()                   # healthy: the name is remembered
    with override(photos_dir=settings.thumbs_dir / "nowhere"):
        _down()
        assert "Fixture Archive" in client.get("/").text


def test_healthz(client, indexed, unmounted):
    ok = client.get("/healthz")
    assert ok.status_code == 200 and ok.json()["failsafe"] is False
    _down()
    down = client.get("/healthz")
    assert down.status_code == 503
    body = down.json()
    assert body["status"] == "down" and body["checks"]["photos"] == "error"
    assert str(settings.photos_dir) not in down.text        # never says where


def test_a_storage_error_mid_request_gets_the_failsafe_page(client, indexed, monkeypatch):
    def boom(*a, **k):
        raise OSError(errno.EHOSTDOWN, "Host is down")
    monkeypatch.setattr(pages.stats, "collect", boom)
    resp = client.get("/stats")
    assert resp.status_code == 503
    assert "archive is asleep" in resp.text


def test_any_other_error_is_still_the_500_it_was(indexed, monkeypatch):
    from fastapi.testclient import TestClient
    from aperture.gallery.app import app

    def boom(*a, **k):
        raise ValueError("a bug, not a NAS")
    monkeypatch.setattr(pages.stats, "collect", boom)
    resp = TestClient(app, raise_server_exceptions=False).get("/stats")
    assert resp.status_code == 500


# ----- the index is not touched ----------------------------------------------
def test_a_walk_that_lost_folders_removes_nothing(indexed, monkeypatch):
    before = _rows()
    real = scanner.walk_photo_tree

    def partial(base, errors=None):
        files = [f for f in real(base) if "/berlin/" not in f.as_posix()]
        if errors is not None:
            errors.append(OSError(errno.EHOSTDOWN, "Host is down", str(base / "berlin")))
        return files

    monkeypatch.setattr(scanner, "walk_photo_tree", partial)
    result = scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size)
    assert result["removed"] == 0 and result["held"] is True
    assert result["walk_errors"] == 1
    assert _rows() == before


def test_no_scan_runs_while_down(indexed, unmounted):
    before = _rows()
    _down()
    summary = indexer.run_scan(trigger="test")
    assert summary["result"] is None and "storage unavailable" in summary["error"]
    assert _rows() == before


def test_the_watcher_does_not_forget_what_it_cannot_see(indexed, unmounted):
    _down()
    assert health.storage_ok() is False


# ----- the environment -------------------------------------------------------
def test_a_network_share_without_a_rescan_is_worth_a_warning(indexed, monkeypatch):
    monkeypatch.setattr(health, "_mount_of", lambda p: ("/photos", "cifs"))
    with override(scan_interval=0):
        snap = health.check_now(force_env=True)
    share = next(c for c in snap["checks"] if c["key"] == "network_share")
    assert share["level"] == "warn" and "SCAN_INTERVAL" in share["hint"]
    assert snap["state"] == "warn"


def test_a_full_volume_is_worth_a_warning(indexed, monkeypatch):
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: (100 << 30, 97 << 30, 3 << 30))
    snap = health.check_now(force_env=True)
    disk = [c for c in snap["checks"] if c["key"].startswith("disk_")]
    assert disk and all(c["level"] == "warn" for c in disk)


# ----- the console and the CLI -----------------------------------------------
def test_the_console_status_carries_the_watch(console, unmounted):
    _down()
    st = console.get("/api/ops/status").json()
    assert st["health"]["state"] == "down"
    assert "checked_at" not in st["health"]      # the live stream stays quiet
    fresh = console.get("/api/ops/health").json()
    assert "checked_at" in fresh


def test_the_cli_exits_by_the_verdict(indexed, unmounted):
    from test_cli import run
    body = run("health", "--json", expect=2)
    assert body["exit"] == 2
    assert any(c["key"] == "photos" and c["level"] == "error" for c in body["here"]["checks"])
