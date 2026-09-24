"""The console's editors: the dry run behind the diff, and the fixes the
checks hand over.

A save is reviewed before it is written, so the preview has to be the very
text the save would produce -- same parser, same apply() -- and it must not
touch the file.
"""


def test_the_preview_is_the_save_without_the_write(console, photos_dir):
    path = photos_dir / "tech" / ".album" / "album.cfg"
    original = path.read_bytes()
    res = console.post("/api/album/cfg/preview",
                       json={"album": "tech", "values": {"showcase": "true"}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "showcase = true" in body["after"]
    assert "showcase" not in body["before"]
    assert path.read_bytes() == original          # nothing was written

    # and the save itself lands exactly that text
    saved = console.put("/api/album/cfg", json={"album": "tech", "values": {"showcase": "true"}})
    try:
        assert saved.json()["raw"] == body["after"]
    finally:
        path.write_bytes(original)


def test_the_preview_keeps_the_comments(console, photos_dir):
    path = photos_dir / "tech" / ".album" / "album.cfg"
    original = path.read_bytes()
    path.write_text("# why this album exists\nname = Tech\n", encoding="utf-8")
    try:
        after = console.post("/api/album/cfg/preview",
                             json={"album": "tech", "values": {"name": "Desk"}}).json()["after"]
        assert after.startswith("# why this album exists\n")
        assert "name = Desk" in after
    finally:
        path.write_bytes(original)


def test_the_gallery_has_a_preview_too(console, photos_dir):
    path = photos_dir / ".gallery" / "gallery.cfg"
    original = path.read_bytes()
    body = console.post("/api/gallery/cfg/preview",
                        json={"values": {"site_sub": "a test line"}}).json()
    assert "site_sub = a test line" in body["after"]
    assert path.read_bytes() == original


def test_an_album_carries_its_fixes_to_the_console(console, photos_dir):
    path = photos_dir / "tech" / ".album" / "album.cfg"
    original = path.read_bytes()
    path.write_text("name = Tech\ncolour = red\n", encoding="utf-8")
    try:
        issues = console.get("/api/album", params={"path": "tech"}).json()["issues"]
        fix = next(i["fix"] for i in issues if i["key"] == "colour")
        assert fix == {"label": "Remove the line", "key": "colour", "value": None}
    finally:
        path.write_bytes(original)
