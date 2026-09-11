"""Every public route, once, against the fixture tree.

This is the characterization net the refactor runs under: it does not say the
pages are *good*, it says they are the same as they were before something was
moved. Three things are pinned per route — the status, the content type, and
the header set the security middleware is supposed to add — plus, for the
routes that render, a handful of strings that only appear when the resolution
chain behind them actually ran.
"""

import pytest
from fastapi.testclient import TestClient

# Routes that render a page or hand out a file, with the content type they
# promise. `None` means "don't care beyond the status".
PAGES = [
    ("/", "text/html"),
    ("/albums", "text/html"),
    ("/albums?sort=name_asc", "text/html"),
    ("/stats", "text/html"),
    ("/search?q=berlin", "text/html"),
    ("/album/berlin", "text/html"),
    ("/album/berlin?subtree=1", "text/html"),
    ("/album/berlin/mitte", "text/html"),
    ("/album/tech?tag=workspace", "text/html"),
    ("/image/berlin/gate.jpg", "text/html"),
    ("/humans.txt", "text/plain"),
    ("/site-theme.css", "text/css"),
    ("/album-theme.css/berlin", "text/css"),
]

MEDIA = [
    ("/thumb/berlin/gate.jpg", ("image/webp", "image/jpeg")),
    ("/preview/berlin/gate.jpg", ("image/webp", "image/jpeg")),
    ("/full/berlin/gate.jpg", ("image/jpeg",)),
]

API = [
    "/api",
    "/api/stats",
    "/api/albums",
    "/api/albums?parent=berlin",
    "/api/album/berlin",
    "/api/album/berlin?images=true",
    "/api/photos",
    "/api/photos?album=berlin&subtree=true",
    "/api/photo/berlin/gate.jpg",
    "/api/tags",
    "/api/showcase",
]

# The one endpoint that answers with a bare array rather than an object.
API_LIST = ["/api/shuffle"]

# Set by the security middleware on every single response.
ALWAYS = {
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "x-powered-by",
}


@pytest.mark.parametrize("path,ctype", PAGES)
def test_pages_render(client, path, ctype):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.headers["content-type"].startswith(ctype), path
    assert not ALWAYS - set(r.headers), path


@pytest.mark.parametrize("path,ctypes", MEDIA)
def test_media_served(client, path, ctypes):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.headers["content-type"] in ctypes, r.headers["content-type"]
    assert r.headers["cache-control"] == "public, max-age=31536000"
    assert len(r.content) > 0


@pytest.mark.parametrize("path", API)
def test_api_responds(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.headers["content-type"].startswith("application/json"), path
    assert r.headers["access-control-allow-origin"] == "*", path
    assert isinstance(r.json(), dict), path


@pytest.mark.parametrize("path", API_LIST)
def test_api_list_endpoints_respond(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.headers["access-control-allow-origin"] == "*", path
    assert isinstance(r.json(), list), path


# ----- what the pages must actually contain -----------------------------
def test_album_page_resolves_its_config(client):
    """Title, description and photo list all come from different resolution
    chains; a page that renders with none of them still returns 200."""
    body = client.get("/album/berlin").text
    assert "Berlin" in body
    assert "mostly in the cold" in body       # album_en.md, markdown-rendered
    assert "gate.jpg" in body
    assert "Mitte" in body                    # the sub-album card


def test_album_page_is_german_under_the_lang_cookie(client):
    # A client of its own: cookies set per request are deprecated in httpx,
    # and setting one on the shared session client would leak into every
    # other test.
    german = TestClient(client.app, cookies={"lang": "de"})
    body = german.get("/album/berlin").text
    assert "überwiegend in der Kälte" in body


def test_albums_index_lists_the_top_level_only(client):
    body = client.get("/albums").text
    assert "Berlin" in body and "Tech" in body
    assert "/album/berlin/mitte" not in body


def test_stats_page_counts_every_photo(client, indexed):
    body = client.get("/stats").text
    assert str(indexed["result"]["indexed"]) in body
    assert "Fixture Cam" in body               # the camera chart read the EXIF


def test_search_finds_by_album_and_tag(client):
    assert "gate.jpg" in client.get("/search?q=berlin").text
    assert "desk.jpg" in client.get("/search?q=workspace").text


def test_image_page_shows_exif(client):
    body = client.get("/image/berlin/gate.jpg").text
    assert "Fixture Cam" in body
    assert "400" in body                       # ISO


# ----- redirects, errors, negotiation -----------------------------------
def test_lang_switch_sets_the_cookie_and_returns(client):
    r = client.get("/lang/de?next=/albums", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/albums"
    assert "lang=de" in r.headers["set-cookie"]


def test_lang_switch_refuses_an_absolute_next(client):
    r = client.get("/lang/de?next=https://evil.example/", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_missing_page_renders_the_404_template(client):
    r = client.get("/album/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")


def test_missing_api_route_answers_json(client):
    r = client.get("/api/album/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "error" in r.json()


def test_html_is_never_cached(client):
    r = client.get("/albums")
    assert r.headers["cache-control"] == "no-store"
    assert "Cookie" in r.headers["vary"]
