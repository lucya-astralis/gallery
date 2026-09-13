"""Pretty links: the file, the redirect, the console screen and the check.

One claim per test, in the order a link lives: written by the console into
`photos/.gallery/links.cfg`, followed by a visitor on the public port, and
reported by "Check all" and `doctor` when its target is gone.

The public half matters most, because it adds a route that matches ANY
one-segment path. Two tests below exist only to keep that from ever eating a
real page: every gallery route's first segment is a reserved name, and the
link router is the last one the app consults.
"""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aperture import checks, links, ops, runtime
from aperture.console import security
from aperture.console.app import app as console_app
from aperture.gallery.app import app as public_app

PASSWORD = "links-test-password"


@pytest.fixture(autouse=True)
def no_links_file():
    """The fixture tree is shared by the whole session; a links.cfg left behind
    would turn up in every other test's `Check all`."""
    links.PATH.unlink(missing_ok=True)
    yield
    links.PATH.unlink(missing_ok=True)


def write(text: str) -> None:
    links.PATH.parent.mkdir(parents=True, exist_ok=True)
    links.PATH.write_text(text, encoding="utf-8")


# ============================================================
# the public side
# ============================================================
def test_a_link_to_an_album_redirects_to_it(client):
    write("winter = berlin\n")
    r = client.get("/winter", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/album/berlin"
    # never a cached, permanent redirect: the point is that it can be re-pointed
    assert r.headers["cache-control"] == "no-store"


def test_a_link_to_a_photo_redirects_to_its_page(client):
    write("dome = berlin/mitte/dome.jpg\n")
    r = client.get("/dome", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/image/berlin/mitte/dome.jpg"
    assert client.get("/dome").status_code == 200        # and it lands on a page


def test_a_link_is_found_whatever_the_casing(client):
    write("gate = berlin/gate.jpg\n")
    assert client.get("/GATE", follow_redirects=False).headers["location"] == "/image/berlin/gate.jpg"


@pytest.mark.parametrize("path", ["/no-such-link", "/robots.txt", "/gone"])
def test_what_is_not_a_live_link_is_the_ordinary_404(client, path):
    write("gone = berlin/not-there.jpg\n")
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")


def test_a_link_never_shadows_a_page_of_the_gallery(client):
    """Even when the file asks for it — the console refuses these names, but
    the file can be edited by hand."""
    write("albums = tech\nstats = tech\napi = tech\n")
    assert client.get("/albums", follow_redirects=False).status_code == 200
    assert client.get("/stats", follow_redirects=False).status_code == 200
    assert client.get("/api", follow_redirects=False).json()["product_version"]


def test_every_gallery_route_is_a_reserved_name():
    """A route added later without a line in links.RESERVED would let the
    console save a link that can never be reached."""
    firsts = set()
    for route in public_app.routes:
        segment = route.path.strip("/").split("/", 1)[0]
        if links.SLUG.fullmatch(segment):
            firsts.add(segment)
    assert firsts <= links.RESERVED, sorted(firsts - links.RESERVED)


def test_the_link_route_is_the_last_one_consulted():
    routes = [r for r in public_app.routes if isinstance(r, APIRoute)]
    assert routes[-1].path == "/{slug}"
    assert sum(1 for r in routes if r.path == "/{slug}") == 1


def test_a_destination_survives_a_non_latin_folder_name():
    """Location is Latin-1 on the wire; a folder called 東京 is not."""
    assert links._quoted("/album/japan/東京") == "/album/japan/%E6%9D%B1%E4%BA%AC"


# ============================================================
# the check
# ============================================================
BROKEN = """\
# hand-edited
Bad_Name = berlin
albums = berlin
lost = berlin/not-there.jpg
nowhere = atlantis
twice = berlin, tech
fine = tech
"""


def test_the_check_names_every_link_that_answers_404(indexed):
    write(BROKEN)
    issues = checks.pretty_links()
    assert {i["key"] for i in issues} == {"bad_name", "albums", "lost", "nowhere", "twice"}
    assert all(i["scope"] == "links" and i["level"] == "error" for i in issues)


def test_check_all_and_doctor_both_see_it(console):
    write(BROKEN)
    served = console.get("/api/validate").json()["issues"]
    assert {i["key"] for i in served if i["scope"] == "links"} >= {"lost", "nowhere"}
    config = ops.doctor()["problems"]["config"]
    assert any(i["scope"] == "links" and i["key"] == "lost" for i in config)


# ============================================================
# the console
# ============================================================
def test_the_console_creates_the_file_and_logs_the_write(console):
    r = console.put("/api/links", json={"slug": "Winter", "target": "/berlin/"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(e["slug"], e["target"], e["kind"]) for e in body["links"]] == [("winter", "berlin", "album")]
    assert body["links"][0]["destination"] == "/album/berlin"
    assert body["links"][0]["thumb"]                      # the album's cover
    text = links.PATH.read_text(encoding="utf-8")
    assert text.startswith("# links.cfg") and "winter = berlin" in text

    newest = console.get("/api/audit?limit=1").json()["entries"][0]
    assert newest["target"] == ".gallery/links.cfg"
    assert newest["action"] == "link added (/winter)"


def test_the_console_lists_what_a_photo_link_resolves_to(console):
    console.put("/api/links", json={"slug": "gate", "target": "berlin/gate.jpg"})
    (entry,) = console.get("/api/links").json()["links"]
    assert entry["kind"] == "photo"
    assert entry["thumb"] == "berlin/gate.jpg"
    assert entry["issues"] == []


@pytest.mark.parametrize("slug, target", [
    ("", "berlin"),
    ("has space", "berlin"),
    ("under_score", "berlin"),
    ("-edge", "berlin"),
    ("double--hyphen", "berlin"),
    ("x" * 65, "berlin"),
    ("albums", "berlin"),                 # a gallery route
    ("ok", ""),
    ("ok", "atlantis"),                   # no such album
    ("ok", "berlin/not-there.jpg"),       # no such photo
    ("ok", "gate.jpg"),                   # a photo outside any album
    ("ok", "../berlin"),
    ("ok", ".gallery/links.cfg"),
    ("ok", "berlin/.album"),
    ("ok", "berlin, tech"),               # the grammar would split it
])
def test_the_console_refuses_a_link_that_would_not_work(console, slug, target):
    r = console.put("/api/links", json={"slug": slug, "target": target})
    assert r.status_code == 400, r.text
    assert not links.PATH.exists()


def test_a_name_in_use_is_not_overwritten_by_a_new_link(console):
    console.put("/api/links", json={"slug": "trip", "target": "berlin"})
    r = console.put("/api/links", json={"slug": "trip", "target": "tech"})
    assert r.status_code == 409
    assert "berlin" in r.json()["detail"]


def test_editing_re_points_and_renames(console):
    console.put("/api/links", json={"slug": "trip", "target": "berlin"})
    r = console.put("/api/links", json={"slug": "trip", "target": "tech", "was": "trip"})
    assert r.status_code == 200, r.text
    r = console.put("/api/links", json={"slug": "desk", "target": "tech", "was": "trip"})
    assert [(e["slug"], e["target"]) for e in r.json()["links"]] == [("desk", "tech")]


def test_an_edit_leaves_the_rest_of_the_file_alone(console):
    write("# my links, hand-kept\n\nold = berlin   \n; parked = tech\nkeep = tech\n")
    console.put("/api/links", json={"slug": "old", "target": "berlin/mitte", "was": "old"})
    assert links.PATH.read_text(encoding="utf-8") == (
        "# my links, hand-kept\n\nold = berlin/mitte\n; parked = tech\nkeep = tech\n")


def test_delete(console):
    console.put("/api/links", json={"slug": "trip", "target": "berlin"})
    r = console.delete("/api/links?slug=trip")
    assert r.status_code == 200 and r.json()["links"] == []
    assert console.delete("/api/links?slug=trip").status_code == 404


def test_a_read_only_console_writes_no_link(console):
    with runtime.override(console_read_only=True):
        assert console.put("/api/links", json={"slug": "trip", "target": "berlin"}).status_code == 403
        assert console.delete("/api/links?slug=trip").status_code == 403
    assert not links.PATH.exists()


def test_the_link_routes_are_behind_the_door(indexed):
    security.set_password(PASSWORD)
    security.reset()
    try:
        stranger = TestClient(console_app)
        assert stranger.get("/api/links").status_code == 401
        assert stranger.put("/api/links", json={"slug": "a", "target": "berlin"}).status_code == 401

        operator = TestClient(console_app)
        csrf = operator.post("/api/session", json={"password": PASSWORD}).json()["csrf"]
        assert operator.put("/api/links", json={"slug": "a", "target": "berlin"}).status_code == 403
        r = operator.put("/api/links", json={"slug": "a", "target": "berlin"},
                         headers={security.CSRF_HEADER: csrf})
        assert r.status_code == 200, r.text
    finally:
        security.clear_password()
        security.reset()
