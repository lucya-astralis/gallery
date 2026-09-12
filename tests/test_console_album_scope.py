"""The console's album routes address ONE album, and the root is not one.

An empty `album` used to slip through: it strips to "", `safe("")` resolves to
the photos root, the root IS a directory, so the "no such album" guard passed
and the write landed in `photos/.album/album.cfg` — a file the gallery never
reads, in the folder an operator hands us as input. A request that names no
album is malformed, not a request about the root; the gallery-wide equivalents
have their own /api/gallery/* routes and the `gallery` asset scope.
"""

import pytest

# Every way an empty album can be spelled on the wire.
BLANKS = ["", "   ", "/", "//", "\\", " / "]


def _root_meta(photos_dir):
    return photos_dir / ".album"


@pytest.mark.parametrize("blank", BLANKS)
def test_writing_cfg_without_an_album_is_a_400(console, photos_dir, blank):
    r = console.put("/api/album/cfg",
                    json={"album": blank, "values": {"name": "Root"}})
    assert r.status_code == 400, r.text
    assert not _root_meta(photos_dir).exists()


def test_a_body_with_the_wrong_keys_writes_nothing(console, photos_dir):
    """The shape that found this: `path` / `updates` instead of `album` /
    `values`, so `album` defaulted to "" and the endpoint answered ok."""
    r = console.put("/api/album/cfg",
                    json={"path": "berlin", "updates": {"name": "Berlin"}})
    assert r.status_code == 400, r.text
    assert not _root_meta(photos_dir).exists()


def test_writing_raw_without_an_album_is_a_400(console, photos_dir):
    r = console.put("/api/album/raw", json={"album": "", "raw": "name = Root\n"})
    assert r.status_code == 400, r.text
    assert not _root_meta(photos_dir).exists()


def test_writing_a_description_without_an_album_is_a_400(console, photos_dir):
    r = console.put("/api/album/description",
                    json={"album": "", "lang": "en", "text": "hello"})
    assert r.status_code == 400, r.text
    assert not _root_meta(photos_dir).exists()


def test_deleting_a_cfg_without_an_album_is_a_400(console, photos_dir):
    r = console.delete("/api/album/cfg?path=")
    assert r.status_code == 400, r.text
    assert (photos_dir / ".gallery" / "gallery.cfg").is_file()


def test_reading_an_album_without_an_album_is_a_400(console):
    assert console.get("/api/album?path=").status_code == 400


def test_the_album_asset_scope_needs_an_album(console, photos_dir, tmp_path):
    """Both directions of the asset routes: an upload with no album must not
    create `photos/.album/`, and a read must not serve out of it."""
    png = tmp_path / "icon.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    r = console.post("/api/asset", data={"path": "", "scope": "album"},
                     files={"file": ("icon.png", png.read_bytes(), "image/png")})
    assert r.status_code == 400, r.text
    assert not _root_meta(photos_dir).exists()

    assert console.get("/api/asset?scope=album&path=&name=icon.png"
                       ).status_code == 400
    assert console.delete("/api/asset?scope=album&path=&name=icon.png"
                          ).status_code == 400


# ----- what must keep working ------------------------------------------
def test_a_real_album_still_reads_and_writes(console, photos_dir):
    assert console.get("/api/album?path=berlin").status_code == 200
    r = console.put("/api/album/cfg",
                    json={"album": "berlin", "values": {"name": "Berlin"}})
    assert r.status_code == 200, r.text
    # cfg values come back as lists — a key may legitimately repeat.
    assert r.json()["values"]["name"] == ["Berlin"]


def test_the_gallery_scope_still_addresses_the_root(console, photos_dir):
    """The root has its own routes — refusing an empty album must not take
    the gallery-wide surface with it."""
    assert console.get("/api/gallery").status_code == 200
    r = console.put("/api/gallery/cfg", json={"values": {"site_name": "Fixture Archive"}})
    assert r.status_code == 200, r.text
    assert (photos_dir / ".gallery" / "gallery.cfg").is_file()


@pytest.mark.parametrize("path", ["", "/"])
def test_browsing_the_photo_root_is_not_an_album_request(console, path):
    """/api/photos is the one route where the root is a legitimate scope: the
    picker starts there. It must not have been caught by the same guard."""
    r = console.get("/api/photos?path=" + path)
    assert r.status_code == 200, r.text
    assert r.json()["album"] == ""
    assert {f["path"] for f in r.json()["folders"]} >= {"berlin", "tech"}
