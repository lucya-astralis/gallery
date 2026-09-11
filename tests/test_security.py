"""The console's door and the write path behind it.

This file is the executable form of the security section in the README: one
test per claim, so "the console can only write metadata" and "a write needs a
session" are things the suite proves rather than things a paragraph asserts.

Two halves:

  * `aperture/paths.py` — pure functions, no server. Every way a path can be
    hostile, tried against the one resolver that decides where a write lands.
  * the console app — authentication, CSRF, the origin check, throttling, and
    what a completed write leaves in the audit log.
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from aperture import paths, runtime
from aperture.console import security
from aperture.console.app import app as console_app
from aperture.runtime import settings

PHOTOS = settings.photos_dir
PASSWORD = "console-test-password"


# ============================================================
# the write-path resolver
# ============================================================
TRAVERSAL = [
    "../outside",
    "berlin/../../outside",
    "..",
    "berlin/..",
]

BAD_NAMES = [
    "../album.cfg",            # a path, not a name
    "sub/album.cfg",
    "album.cfg:stream",        # NTFS alternate data stream
    "album.cfg.",              # windows drops the trailing dot
    "album.cfg ",              # ... and the trailing space
    "con.cfg",                 # reserved device name
    "NUL",
    "",
]


@pytest.mark.parametrize("album", TRAVERSAL)
def test_traversal_never_produces_a_target(album):
    with pytest.raises(paths.PathRefused):
        paths.writable_target(PHOTOS, album, "album.cfg")


@pytest.mark.parametrize("album", ["/etc", "C:/Windows", "\\\\server\\share"])
def test_absolute_paths_are_refused(album):
    with pytest.raises(paths.PathRefused):
        paths.writable_target(PHOTOS, album, "album.cfg")


@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_file_names_are_refused(name):
    with pytest.raises(paths.PathRefused):
        paths.writable_target(PHOTOS, "berlin", name)


@pytest.mark.parametrize("album", [".album", ".gallery", "berlin/.album", ".ssh"])
def test_a_metadata_folder_is_not_an_album(album):
    """Otherwise `.album/.album/album.cfg` and worse become addressable."""
    with pytest.raises(paths.PathRefused):
        paths.writable_target(PHOTOS, album, "album.cfg")


def test_the_root_of_the_share_is_not_an_album():
    """An empty album resolved to photos/.album/album.cfg — inside the
    permitted scope, but a file nothing in the product ever reads."""
    for empty in ("", "   ", None):
        with pytest.raises(paths.PathRefused):
            paths.writable_target(PHOTOS, empty, "album.cfg")


def test_the_extension_allowlist_is_enforced():
    with pytest.raises(paths.PathRefused):
        paths.writable_target(PHOTOS, "berlin", "icon.php",
                              allowed_exts={".png", ".svg", ".webp"})
    ok = paths.writable_target(PHOTOS, "berlin", "icon.png",
                               allowed_exts={".png", ".svg", ".webp"})
    assert ok.name == "icon.png"


def test_a_legitimate_target_lands_in_the_metadata_folder():
    target = paths.writable_target(PHOTOS, "berlin/mitte", "album.cfg")
    assert target.parent.name == ".album"
    assert target.parent.parent.name == "mitte"
    assert target.is_relative_to(PHOTOS)


def test_the_gallery_scope_ignores_the_album():
    target = paths.writable_target(PHOTOS, "berlin", "gallery.cfg", scope="gallery")
    assert target.parent == PHOTOS / ".gallery"


def test_no_photo_is_ever_a_writable_target():
    """The guarantee the read-only mount used to give. Whatever a caller
    names, the resolver's answer is always inside a metadata folder."""
    for album in ("berlin", "berlin/mitte", "tech"):
        for name in ("gate.jpg", "album.cfg", "anything.png"):
            try:
                target = paths.writable_target(PHOTOS, album, name)
            except paths.PathRefused:
                continue
            assert target.parent.name in paths.META_DIRS


def test_symlinked_metadata_folder_is_refused(tmp_path):
    """A single link inside .album/ would turn every rule above into
    decoration. Skipped where the OS will not let the test create one."""
    root = tmp_path / "photos"
    (root / "album" / ".album").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "album" / ".album" / "escape.png"
    try:
        link.symlink_to(outside / "target.png")
    except (OSError, NotImplementedError):
        pytest.skip("this environment does not allow creating symlinks")
    with pytest.raises(paths.PathRefused):
        paths.writable_target(root, "album", "escape.png", allowed_exts={".png"})


# ----- tag sidecars -----------------------------------------------------
def test_a_sidecar_is_derived_from_a_photo_that_exists():
    target = paths.sidecar_target(PHOTOS, "berlin/gate.jpg")
    assert target.name == "gate.jpg.tags"
    assert target.parent == PHOTOS / "berlin"


@pytest.mark.parametrize("rel", [
    "berlin/nope.jpg",              # not there
    "berlin",                       # a folder
    "berlin/.album/album.cfg",      # metadata, not a photograph
    "../outside.jpg",
])
def test_a_sidecar_needs_a_real_photograph(rel):
    def is_image(name):
        return name.lower().endswith((".jpg", ".jpeg", ".png"))

    with pytest.raises(paths.PathRefused):
        paths.sidecar_target(PHOTOS, rel, is_image=is_image)


# ============================================================
# the startup rule
# ============================================================
@pytest.fixture
def no_password():
    security.clear_password()
    yield
    security.clear_password()


def _check(host, allow_open=False):
    """Run the startup rule against one address. The address is an argument
    because the settings are frozen; the escape hatch is one of them, so it is
    overridden rather than set in an environment the rule no longer reads."""
    with runtime.override(console_allow_open=allow_open):
        security.assert_safe_binding(host, 8090)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_open_on_loopback_is_allowed(no_password, host):
    _check(host)               # must not raise


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "192.168.1.20"])
def test_open_on_a_network_refuses_to_start(no_password, host):
    with pytest.raises(SystemExit) as caught:
        _check(host)
    message = str(caught.value)
    # The refusal has to teach, not just refuse.
    assert "passwd" in message and "CONSOLE_ALLOW_OPEN" in message


def test_the_escape_hatch_is_explicit(no_password):
    _check("0.0.0.0", allow_open=True)


def test_a_password_makes_the_bind_irrelevant():
    security.set_password(PASSWORD)
    try:
        _check("0.0.0.0")
    finally:
        security.clear_password()


# ============================================================
# the door
# ============================================================
@pytest.fixture
def locked():
    """The console with a password set and every session cleared."""
    security.set_password(PASSWORD)
    security.reset()
    yield TestClient(console_app)
    security.clear_password()
    security.reset()


@pytest.fixture
def signed_in(locked):
    res = locked.post("/api/session", json={"password": PASSWORD})
    assert res.status_code == 200, res.text
    return locked, res.json()["csrf"]


def test_a_stranger_gets_nothing(locked):
    for path in ("/api/meta", "/api/tree", "/api/album?path=berlin", "/api/validate"):
        assert locked.get(path).status_code == 401, path


def test_the_page_sends_a_stranger_to_the_door(locked):
    res = locked.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"


def test_health_is_the_only_open_route_and_says_little(locked):
    body = locked.get("/api/health").json()
    assert body["auth"] == "password"
    # The mount path is a fact about the deployment, not a liveness signal.
    assert "photos_dir" not in body


def test_the_login_page_is_reachable_and_carries_no_data(locked):
    body = locked.get("/login").text
    assert "Sign in" in body
    assert str(PHOTOS) not in body


def test_a_wrong_password_is_refused(locked):
    assert locked.post("/api/session", json={"password": "wrong"}).status_code == 401
    assert locked.get("/api/meta").status_code == 401


def test_a_right_password_opens_a_session(signed_in):
    client, csrf = signed_in
    assert csrf
    assert client.get("/api/meta").status_code == 200


def test_the_session_cookie_is_not_readable_by_script(locked):
    res = locked.post("/api/session", json={"password": PASSWORD})
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_signing_out_ends_it(signed_in):
    client, csrf = signed_in
    assert client.delete("/api/session",
                         headers={"X-Aperture-CSRF": csrf}).status_code == 200
    assert client.get("/api/meta").status_code == 401


# ----- CSRF and origin --------------------------------------------------
def _write(client, csrf=None, origin=None):
    headers = {}
    if csrf is not None:
        headers["X-Aperture-CSRF"] = csrf
    if origin is not None:
        headers["Origin"] = origin
    return client.put("/api/album/cfg", headers=headers,
                      json={"album": "berlin", "values": {"name": "Berlin"}})


def test_a_write_without_the_token_is_refused(signed_in):
    client, _ = signed_in
    res = _write(client)
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_a_write_with_a_stale_token_is_refused(signed_in):
    client, _ = signed_in
    assert _write(client, csrf="not-the-token").status_code == 403


def test_a_write_with_the_token_goes_through(signed_in):
    client, csrf = signed_in
    assert _write(client, csrf=csrf).status_code == 200


def test_a_cross_site_write_is_refused_even_with_the_token(signed_in):
    """SameSite=Strict is the browser's half; this is ours."""
    client, csrf = signed_in
    res = _write(client, csrf=csrf, origin="https://evil.example")
    assert res.status_code == 403
    assert "cross-site" in res.json()["detail"].lower()


def test_an_open_console_still_refuses_a_cross_site_write(no_password):
    """No password does not mean no CSRF: a browser that happens to reach a
    loopback console must not be steerable from another page."""
    client = TestClient(console_app)
    res = _write(client, origin="https://evil.example")
    assert res.status_code == 403


# ----- throttling -------------------------------------------------------
def test_repeated_failures_start_costing_time(locked):
    for _ in range(security.FREE_TRIES):
        assert locked.post("/api/session", json={"password": "no"}).status_code == 401
    # One past the free tries, the answer changes from "wrong" to "wait".
    locked.post("/api/session", json={"password": "no"})
    res = locked.post("/api/session", json={"password": PASSWORD})
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) >= 1


# ----- what a write leaves behind ---------------------------------------
def test_a_write_is_recorded(signed_in):
    client, csrf = signed_in
    log = settings.console_dir / "audit.log"
    before_lines = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []

    assert _write(client, csrf=csrf).status_code == 200

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(before_lines) + 1
    record = json.loads(lines[-1])
    assert record["action"] == "album.cfg"
    assert record["target"] == "berlin/.album/album.cfg"
    assert record["after"] and len(record["after"]) == 64
    assert "session" in record
    # The record says WHAT changed, never the content that changed.
    assert "Berlin" not in lines[-1]


# ----- uploads ----------------------------------------------------------
def test_an_upload_must_be_what_it_claims(signed_in):
    client, csrf = signed_in
    res = client.post(
        "/api/asset",
        headers={"X-Aperture-CSRF": csrf},
        data={"path": "berlin", "scope": "album"},
        files={"file": ("icon.png", b"<html>not a picture</html>", "image/png")},
    )
    assert res.status_code == 415


def test_an_upload_with_a_path_for_a_name_is_refused(signed_in):
    client, csrf = signed_in
    res = client.post(
        "/api/asset",
        headers={"X-Aperture-CSRF": csrf},
        data={"path": "berlin", "scope": "album"},
        files={"file": ("../../escape.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32,
                        "image/png")},
    )
    # Either the name is stripped to `escape.png` and lands in .album/, or it
    # is refused outright — what must NOT happen is a write outside the tree.
    assert res.status_code in (200, 400)
    assert not (PHOTOS.parent / "escape.png").exists()


# ----- headers ----------------------------------------------------------
def test_the_console_is_never_cached_and_never_framed(locked):
    res = locked.get("/login")
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert "script-src 'self'" in res.headers["content-security-policy"]
