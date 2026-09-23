"""The update notice: what every gallery says about itself, and what the
console makes of the maker's answer.

Nothing here reaches the network. The fixture environment sets UPDATE_CHECK=0,
and every test that turns it on replaces the one function that would ask.
"""

import pytest
from fastapi.testclient import TestClient

from aperture import brand, update_check
from aperture.console import security
from aperture.console.app import app as console_app
from aperture.runtime import override


def _bump(version, part):
    major, minor, patch = update_check.version_tuple(version)
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0",
            "patch": f"{major}.{minor}.{patch + 1}"}[part]


@pytest.fixture
def asks(monkeypatch):
    """Turns the check on and answers it with whatever the test puts in
    `answer`; `calls` counts how often the server was asked."""
    box = {"answer": {"version": brand.VERSION}, "calls": 0}

    def fake(url):
        box["calls"] += 1
        if isinstance(box["answer"], Exception):
            raise box["answer"]
        return box["answer"]

    monkeypatch.setattr(update_check, "_fetch", fake)
    update_check.reset()
    with override(update_check=True):
        yield box
    update_check.reset()


def test_the_versions_compare_as_numbers_not_as_text():
    assert update_check.version_tuple("1.10.0") > update_check.version_tuple("1.9.9")
    for bad in ("1.8", "v1.8.0", "1.8.0-beta", "", None, 180):
        assert update_check.version_tuple(bad) is None


def test_the_manifest_is_read_defensively():
    got = update_check.read_manifest({
        "version": " 2.0.0 ", "date": "2030-01-01",
        "url": "javascript:alert(1)", "notes": "  two\n\nlines  " + "x" * 400})
    assert got["version"] == "2.0.0" and got["date"] == "2030-01-01"
    assert got["url"] is None                          # https or nothing
    assert got["notes"].startswith("two lines ") and len(got["notes"]) == update_check.MAX_NOTES
    assert update_check.read_manifest({"version": "2.0.0", "date": "soon"})["date"] is None
    for bad in ([], "2.0.0", {"version": "2.0"}, {}):
        with pytest.raises(ValueError):
            update_check.read_manifest(bad)


def test_off_asks_nobody(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch", lambda url: pytest.fail("asked the network"))
    update_check.reset()
    assert update_check.status()["state"] == "off"


@pytest.mark.parametrize("part", ["major", "minor", "patch"])
def test_a_newer_release_is_available(asks, part):
    asks["answer"] = {"version": _bump(brand.VERSION, part),
                      "url": "https://lucya.sh/aperture", "notes": "New things."}
    got = update_check.status(now=1000)
    assert got["state"] == "available" and got["current"] == brand.VERSION
    assert got["latest"]["version"] == asks["answer"]["version"]
    assert got["latest"]["url"] == "https://lucya.sh/aperture"


def test_the_same_or_an_older_release_is_current(asks):
    assert update_check.status(now=1000)["state"] == "current"
    update_check.reset()
    asks["answer"] = {"version": "0.9.0"}
    assert update_check.status(now=1000)["state"] == "current"


def test_an_answer_is_kept_and_a_check_again_is_rate_limited(asks):
    update_check.status(now=1000)
    update_check.status(now=1000 + update_check.FRESH_FOR - 1)
    update_check.status(now=1030, fresh=True)               # inside the minute: not asked
    assert asks["calls"] == 1
    update_check.status(now=1000 + update_check.MIN_GAP, fresh=True)
    assert asks["calls"] == 2
    update_check.status(now=1000 + update_check.MIN_GAP + update_check.FRESH_FOR)
    assert asks["calls"] == 3


def test_a_failure_is_a_state_and_is_retried_sooner(asks):
    asks["answer"] = OSError("name resolution failed")
    got = update_check.status(now=1000)
    assert got["state"] == "unreachable" and "name resolution" in got["error"]
    update_check.status(now=1000 + update_check.RETRY_AFTER - 1)
    assert asks["calls"] == 1
    asks["answer"] = {"version": brand.VERSION}
    assert update_check.status(now=1000 + update_check.RETRY_AFTER)["state"] == "current"


def test_the_console_route_answers(console, asks):
    asks["answer"] = {"version": _bump(brand.VERSION, "minor")}
    body = console.get("/api/updates").json()
    assert body["state"] == "available"
    assert body["source"] == brand.UPDATE_URL


def test_the_route_stays_behind_the_door(indexed):
    security.set_password("a long enough password for the door test")
    security.reset()
    try:
        assert TestClient(console_app).get("/api/updates").status_code == 401
    finally:
        security.clear_password()
        security.reset()


def test_every_gallery_says_which_aperture_it_runs(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
    body = r.json()
    assert body["version"] == brand.VERSION and body["product"] == brand.PRODUCT
    # the answer one gallery gives is exactly what another one's check reads
    got = update_check.read_manifest(body)
    assert got["version"] == brand.VERSION and got["date"] and got["notes"]
    assert got["url"].startswith(brand.REPO_URL + "/blob/main/CHANGELOG.md#")
    assert any(e["path"] == "/api/version" for e in client.get("/api").json()["endpoints"])


def test_the_maker_gallery_is_the_default_source():
    assert brand.UPDATE_URL == "https://images.lucya.sh/api/version"
