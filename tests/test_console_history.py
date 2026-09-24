"""History: the backups every save keeps, readable and restorable.

A restore is itself an ordinary write -- the file it replaces is backed up
first -- so putting an old version back can be undone the same way.
"""

from aperture import runtime


def _save(console, **values):
    res = console.put("/api/album/cfg", json={"album": "tech", "values": values})
    assert res.status_code == 200, res.text


def test_a_save_leaves_a_version_that_can_be_read_and_restored(console, photos_dir):
    path = photos_dir / "tech" / ".album" / "album.cfg"
    original = path.read_bytes()
    try:
        _save(console, name="First")
        first = path.read_text(encoding="utf-8")
        _save(console, name="Second")

        hist = console.get("/api/history", params={"file": "album", "album": "tech"}).json()
        assert hist["file"] == "tech/.album/album.cfg"
        assert "name = Second" in hist["current"]
        assert hist["versions"], hist
        newest = hist["versions"][0]["id"]
        text = console.get("/api/history/version",
                           params={"file": "album", "album": "tech", "version": newest}).json()["text"]
        assert text == first

        res = console.post("/api/history/restore",
                           json={"file": "album", "album": "tech", "version": newest})
        assert res.status_code == 200, res.text
        assert path.read_text(encoding="utf-8") == first
        # and what the restore replaced is now a version of its own
        again = console.get("/api/history", params={"file": "album", "album": "tech"}).json()
        texts = [console.get("/api/history/version", params={
            "file": "album", "album": "tech", "version": v["id"]}).json()["text"]
            for v in again["versions"][:2]]
        assert any("name = Second" in t for t in texts)
    finally:
        path.write_bytes(original)


def test_a_version_id_cannot_leave_its_folder(console):
    for bad in ("../../gallery.db", "20260101T000000Z-../x", "nope", ""):
        res = console.get("/api/history/version",
                          params={"file": "album", "album": "tech", "version": bad})
        assert res.status_code in (400, 404), bad
    assert console.get("/api/history", params={"file": "passwd"}).status_code == 400
    assert console.get("/api/history", params={"file": "album", "album": "../etc"}).status_code == 400


def test_restore_honours_read_only(console):
    with runtime.override(console_read_only=True):
        res = console.post("/api/history/restore",
                           json={"file": "album", "album": "tech", "version": "20260101T000000Z-album.cfg"})
        assert res.status_code == 403
