"""The console's photo tiles are the gallery's thumbnails.

/api/thumb used to look for a JPEG the gallery never writes, found none, and
built its own copies under data/console/ on every first view. It now hands out
-- and when needed builds -- the file /thumb serves visitors. These pin that
there is one derivative tree, and that the console cannot grow a second one or
put a thumbnail of something that is not a photo into the first.
"""

import pytest
from PIL import Image

from aperture import scanner, templating
from aperture.console.app import BASE_DIR as CONSOLE_DIR
from aperture.runtime import settings


def tile(console, path):
    return console.get("/api/thumb", params={"path": path})


def test_a_tile_is_the_gallerys_thumbnail(console, client):
    thumb = (settings.thumbs_dir / "berlin" / "wall.jpg").with_suffix(scanner.THUMB_EXT)
    thumb.unlink(missing_ok=True)
    r = tile(console, "berlin/wall.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert thumb.is_file()                      # built into the gallery's own tree
    assert r.content == thumb.read_bytes()
    assert client.get("/thumb/berlin/wall.jpg").content == r.content


def test_the_console_keeps_no_thumbnails_of_its_own(console):
    assert tile(console, "tech/desk.jpg").status_code == 200
    assert not (settings.console_dir / "thumbcache").exists()


@pytest.mark.parametrize("path", ["berlin/.album/album.cfg", ".gallery/gallery.cfg",
                                  "berlin", "", "nope/missing.jpg"])
def test_only_a_photo_has_a_tile(console, path):
    assert tile(console, path).status_code == 404


def test_a_path_outside_the_share_is_refused(console):
    assert tile(console, "../outside.jpg").status_code == 400


def test_an_image_in_a_metadata_folder_gets_no_tile(console, photos_dir):
    """A mark or a backdrop is not a photo, and its thumbnail would sit in the
    gallery's tree as an orphan no photo maps to."""
    mark = photos_dir / "berlin" / ".album" / "mark.png"
    Image.new("RGB", (8, 8)).save(mark)
    try:
        assert tile(console, "berlin/.album/mark.png").status_code == 404
        assert not (settings.thumbs_dir / "berlin" / ".album").exists()
    finally:
        mark.unlink()


# ----- the chrome is the gallery's ----------------------------------------
def test_the_console_serves_the_gallerys_fonts(console):
    """Nine woff2 files, byte for byte the gallery's, used to sit in the
    console's own static/ because the two surfaces shipped as separate
    images. They ship as one package, so there is one copy of each face."""
    name = "SpaceGrotesk-400.woff2"
    r = console.get("/static/fonts/" + name)
    assert r.status_code == 200
    assert r.content == (templating.WEB_DIR / "static" / "fonts" / name).read_bytes()
    assert not (CONSOLE_DIR / "static" / "fonts").exists()


def test_the_console_keeps_its_own_sheet_and_mark(console):
    """The fonts mount is the more specific path; everything else under
    /static must still come from the console's own folder."""
    assert console.get("/static/style.css").status_code == 200
    assert console.get("/static/logo/lucya_logo.svg").content ==         (CONSOLE_DIR / "static" / "logo" / "lucya_logo.svg").read_bytes()
