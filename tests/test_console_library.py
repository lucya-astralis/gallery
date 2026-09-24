"""The console's Library and its tag manager.

The Library lists every photo with the index's facts and the sidecar's tags
as they are on disk right now; its search is the gallery's own grammar. The
tag manager renames, merges and deletes a tag across every sidecar -- each
file through the same gate, backup and audit line a single tag write takes.
"""

from aperture import runtime
from aperture.console import security


def _tags(console, rel):
    return console.get("/api/image", params={"path": rel}).json()["tags"]


def _set(console, rels, tags):
    res = console.put("/api/tags", json={"photos": rels, "set": tags})
    assert res.status_code == 200, res.text


def test_the_library_lists_every_photo_with_its_facts(console):
    body = console.get("/api/library").json()
    rels = {p["rel"] for p in body["photos"]}
    assert {"berlin/gate.jpg", "berlin/mitte/dome.jpg", "tech/desk.jpg"} <= rels
    desk = next(p for p in body["photos"] if p["rel"] == "tech/desk.jpg")
    assert desk["album"] == "tech"
    assert desk["indexed"] is True
    assert desk["taken"].startswith("2026-02-11")
    assert set(desk["tags"]) == {"workspace", "keyboard"}


def test_the_library_sees_a_tag_before_any_scan_does(console):
    """The tags come off disk, not out of the index, so a write shows on the
    very next read."""
    _set(console, ["berlin/gate.jpg"], ["fresh"])
    try:
        gate = next(p for p in console.get("/api/library").json()["photos"]
                    if p["rel"] == "berlin/gate.jpg")
        assert gate["tags"] == ["fresh"]
    finally:
        _set(console, ["berlin/gate.jpg"], [])


def test_the_library_search_is_the_gallery_grammar(console):
    found = console.get("/api/library/search", params={"q": "date:2026-02"}).json()
    assert set(found["matches"]) == {"tech/desk.jpg", "tech/rack.jpg"}
    assert found["filters"] == [{"label": "date:2026-02", "ok": True}]
    words = console.get("/api/library/search", params={"q": "mitte"}).json()
    assert set(words["matches"]) == {"berlin/mitte/dome.jpg", "berlin/mitte/river.jpg"}
    # nothing typed finds nothing, never everything
    assert console.get("/api/library/search").json()["matches"] == []


def test_the_loupe_gets_a_preview_never_the_original(console):
    res = console.get("/api/preview", params={"path": "berlin/gate.jpg"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert console.get("/api/preview", params={"path": "berlin/nope.jpg"}).status_code == 404
    assert console.get("/api/preview", params={"path": "../etc/passwd"}).status_code in (400, 404)


def test_renaming_onto_an_existing_tag_merges_them(console):
    _set(console, ["berlin/gate.jpg"], ["tokio", "night"])
    _set(console, ["berlin/wall.jpg"], ["tokio", "tokyo"])
    try:
        res = console.post("/api/tags/rename", json={"from": "Tokio", "to": "tokyo"})
        assert res.status_code == 200, res.text
        assert res.json()["changed"] == 2
        assert _tags(console, "berlin/gate.jpg") == ["tokyo", "night"]
        assert _tags(console, "berlin/wall.jpg") == ["tokyo"]       # one, not two
        assert set(_tags(console, "tech/desk.jpg")) == {"workspace", "keyboard"}
        # every file it touched is an ordinary audited tag write
        logged = [e["target"] for e in security.recent(10) if e.get("action") == "tags"]
        assert "berlin/gate.jpg.tags" in logged and "berlin/wall.jpg.tags" in logged
    finally:
        _set(console, ["berlin/gate.jpg", "berlin/wall.jpg"], [])


def test_deleting_a_tag_takes_it_off_every_photo(console, photos_dir):
    _set(console, ["berlin/gate.jpg", "tech/rack.jpg"], ["gone"])
    try:
        res = console.post("/api/tags/delete", json={"tag": "GONE"})
        assert res.status_code == 200, res.text
        assert res.json()["changed"] == 2
        # a sidecar left empty is removed, not left behind as an empty file
        assert not (photos_dir / "berlin" / "gate.jpg.tags").exists()
        assert not (photos_dir / "tech" / "rack.jpg.tags").exists()
        assert set(_tags(console, "tech/desk.jpg")) == {"workspace", "keyboard"}
    finally:
        _set(console, ["berlin/gate.jpg", "tech/rack.jpg"], [])


def test_a_tag_name_cannot_smuggle_a_second_tag(console):
    for body in ({"from": "a", "to": "b,c"}, {"from": "a", "to": "b\nc"},
                 {"from": "", "to": "b"}, {"from": "a"}):
        assert console.post("/api/tags/rename", json=body).status_code == 400
    assert console.post("/api/tags/delete", json={"tag": "  "}).status_code == 400


def test_the_tag_manager_honours_read_only(console):
    with runtime.override(console_read_only=True):
        assert console.post("/api/tags/rename", json={"from": "a", "to": "b"}).status_code == 403
        assert console.post("/api/tags/delete", json={"tag": "a"}).status_code == 403
