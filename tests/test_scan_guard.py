"""The empty-walk guard: a scan that finds nothing must not empty the index.

An SMB share that did not mount leaves an empty directory behind, and a
whole-tree walk over it used to read as "every photo was deleted".
"""

from aperture import db, scanner
from aperture.runtime import settings


def _rows() -> int:
    return db.conn().execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]


def test_an_empty_walk_keeps_the_index(indexed, tmp_path):
    before = _rows()
    assert before > 0
    empty = tmp_path / "unmounted"
    empty.mkdir()

    result = scanner.full_scan(empty, settings.thumbs_dir, settings.thumb_size)

    assert result["held"] is True
    assert result["removed"] == 0
    assert result["total_seen"] == 0
    assert _rows() == before


def test_a_normal_scan_is_not_held(indexed):
    assert indexed["result"]["held"] is False
    again = scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size)
    assert again["held"] is False
    assert again["total_seen"] == _rows()


def test_the_page_still_answers_after_a_held_scan(client, tmp_path):
    empty = tmp_path / "unmounted"
    empty.mkdir()
    scanner.full_scan(empty, settings.thumbs_dir, settings.thumb_size)
    assert client.get("/album/berlin").status_code == 200
