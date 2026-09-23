"""A photo is known by its bytes, not only by its mtime.

Edited photos come back under the same name, and plenty of copy tools keep
the old timestamp. The index used to compare mtimes only and the derivatives
were rebuilt only when their source was NEWER than them, so the gallery kept
serving the thumbnail and preview of the picture that was replaced. Every row
now carries the SHA-256 of its file (scanner.content_hash): a different hash
drops the derivatives, the same hash under a new mtime keeps them, and the
photo URLs carry the hash as `?v=` so the year-long cache cannot hold on to
the old picture either.
"""

import hashlib
import os
import shutil
import time

import pytest
from PIL import Image

from aperture import db, scanner
from aperture.runtime import settings

ALBUM = "hashcheck"
REL = ALBUM + "/swap.jpg"
LONG_AGO = time.time() - 10_000


def _scan() -> dict:
    return scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                             previews_dir=settings.previews_dir,
                             preview_size=settings.preview_size)


def _row(rel: str = REL):
    return db.conn().execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paint(path, colour, size=(600, 400)) -> None:
    Image.new("RGB", size, colour).save(path, "JPEG", quality=90)


def _thumb_colour(rel: str = REL):
    thumb = (settings.thumbs_dir / rel).with_suffix(scanner.THUMB_EXT)
    with Image.open(thumb) as img:
        return img.convert("RGB").getpixel((img.width // 2, img.height // 2))


@pytest.fixture
def swap(indexed, photos_dir):
    """One red photo in an album of its own, indexed, its mtime long past."""
    folder = photos_dir / ALBUM
    folder.mkdir()
    photo = folder / "swap.jpg"
    _paint(photo, (220, 20, 20))
    os.utime(photo, (LONG_AGO, LONG_AGO))
    _scan()
    yield photo
    shutil.rmtree(folder, ignore_errors=True)
    _scan()
    scanner.drop_derivatives(REL)


def test_every_row_carries_the_hash_of_its_file(indexed, photos_dir):
    rows = db.conn().execute("SELECT rel_path, content_hash FROM images").fetchall()
    assert rows
    for r in rows:
        assert r["content_hash"] == _sha(photos_dir / r["rel_path"]), r["rel_path"]


def test_a_replaced_photo_with_its_old_mtime_is_rebuilt(swap):
    before = _row()["content_hash"]
    assert _thumb_colour()[0] > 150  # red

    # the edited export, uploaded under the same name with the timestamp kept
    _paint(swap, (20, 20, 220), size=(640, 420))
    os.utime(swap, (LONG_AGO, LONG_AGO))
    result = _scan()

    assert result["indexed"] == 1
    assert _row()["content_hash"] == _sha(swap) != before
    assert _row()["width"] == 640
    assert _thumb_colour()[2] > 150  # blue — the old tile is gone
    preview = (settings.previews_dir / REL).with_suffix(scanner.PREVIEW_EXT)
    with Image.open(preview) as img:
        assert img.convert("RGB").getpixel((10, 10))[2] > 150


def test_the_same_bytes_under_a_new_mtime_are_not_rebuilt(swap):
    thumb = (settings.thumbs_dir / REL).with_suffix(scanner.THUMB_EXT)
    # derivatives built long ago, then the very same file copied over again
    os.utime(thumb, (LONG_AGO, LONG_AGO))
    now = time.time() - 5
    os.utime(swap, (now, now))

    result = _scan()

    assert result["indexed"] == 1          # the row follows the new mtime
    assert result["thumbnails"] == 0       # ...and nothing is re-encoded
    assert result["previews"] == 0
    assert abs(_row()["mtime"] - now) < 1.0
    assert not scanner.needs_rebuild(thumb, swap)


def test_a_row_indexed_before_hashing_is_hashed_by_the_next_scan(swap):
    c = db.conn()
    with db.lock():
        c.execute("UPDATE images SET content_hash = NULL WHERE rel_path = ?", (REL,))
        c.commit()

    result = _scan()

    assert result["indexed"] == 0          # hashed, not re-indexed
    assert _row()["content_hash"] == _sha(swap)


def test_the_urls_follow_the_bytes(swap, client):
    first = client.get(f"/api/photo/{REL}").json()["urls"]["thumb"]
    assert first == f"/thumb/{REL}?v={_sha(swap)[:scanner.STAMP_LEN]}"

    _paint(swap, (20, 200, 20), size=(620, 410))
    os.utime(swap, (LONG_AGO, LONG_AGO))
    _scan()

    second = client.get(f"/api/photo/{REL}").json()["urls"]["thumb"]
    assert second != first
    assert f'src="{second}"' in client.get(f"/album/{ALBUM}").text


def test_only_a_stamped_url_is_cached_for_a_year(swap, client):
    stamped = scanner.media_url("preview", REL)
    assert "?v=" in stamped
    assert client.get(stamped).headers["cache-control"] == "public, max-age=31536000"
    plain = client.get(f"/preview/{REL}")
    assert plain.status_code == 200
    assert plain.headers["cache-control"] == "public, max-age=3600"


def test_a_photo_page_hands_the_lightbox_its_stamps(client):
    html = client.get("/image/berlin/gate.jpg").text
    stamp = scanner.photo_stamp("berlin/gate.jpg")
    assert stamp
    assert f'src="/preview/berlin/gate.jpg?v={stamp}"' in html
    assert f'"berlin/gate.jpg": "{stamp}"' in html
