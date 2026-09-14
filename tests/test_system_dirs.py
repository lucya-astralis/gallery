"""The folders a NAS keeps for itself are not albums, at any depth.

A Synology writes `@eaDir/<photo>/SYNOPHOTO_THUMB_*.jpg` beside every photo it
has indexed. Those are JPEGs, so the scan used to take each one for a photo of
an album named `<album>/@eaDir/<photo>` — thumbnails of thumbnails, in the
index, in the album list, in the search. schema.SYSTEM_DIRS is the one list
of such folders, and these tests hold every surface to it.
"""

import shutil
from pathlib import Path

import pytest

from aperture import db, ops, scanner, schema
from aperture.console.library import Library
from aperture.paths import PathRefused, writable_target
from aperture.runtime import settings

# One per shape: Synology's thumbnail folder two levels down (and in another
# case than it is listed in), its recycle bin at the root of the share, and a
# Windows one inside an album.
JUNK = ("berlin/@eaDir/gate.jpg/SYNOPHOTO_THUMB_M.jpg",
        "berlin/mitte/@EADIR/dome.jpg/SYNOPHOTO_THUMB_XL.jpg",
        "#recycle/old-album/deleted.jpg",
        "tech/$RECYCLE.BIN/S-1-5-21/rack.jpg")


def _rows(where: str = "", params=()) -> list[str]:
    return [r["rel_path"] for r in db.conn().execute(
        "SELECT rel_path FROM images " + where, params)]


@pytest.fixture
def junk(indexed, photos_dir):
    source = photos_dir / "berlin" / "gate.jpg"
    made = []
    for rel in JUNK:
        target = photos_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        made.append(target)
    yield made
    for rel in JUNK:
        top = photos_dir / Path(rel).parts[0]
        # the whole system folder goes, wherever it was planted
        for part_index, part in enumerate(Path(rel).parts):
            if schema.is_system_dir(part):
                shutil.rmtree(photos_dir.joinpath(*Path(rel).parts[:part_index + 1]),
                              ignore_errors=True)
                break
        if top.name.startswith("#") and top.exists():
            shutil.rmtree(top, ignore_errors=True)
    scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                      previews_dir=settings.previews_dir, preview_size=settings.preview_size)


def test_the_list_matches_without_regard_to_case():
    for name in ("@eaDir", "@EADIR", "#recycle", "$RECYCLE.BIN", "System Volume Information"):
        assert schema.is_system_dir(name), name
    for name in ("berlin", "eaDir", "recycle", ".album"):
        assert not schema.is_system_dir(name), name


@pytest.mark.parametrize("rel", JUNK)
def test_nothing_inside_one_is_metadata_the_gallery_reads(rel):
    assert scanner.is_meta_path(Path(rel))


def test_a_scan_indexes_none_of_it(junk):
    before = len(_rows())
    result = scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                               previews_dir=settings.previews_dir,
                               preview_size=settings.preview_size)
    assert result["total_seen"] == before
    assert len(_rows()) == before
    for rel in JUNK:
        assert rel not in _rows()
        # and it built nothing for them either
        assert not ops.derivatives(rel)["thumb"].exists()
    assert not any(rel in scanner.walk_photo_tree(settings.photos_dir) for rel in JUNK)


def test_a_row_that_got_in_before_is_dropped_by_the_next_scan(junk):
    """An index built before this fix holds those photos. The next scan must
    take them out rather than keep them because they are still on disk."""
    c = db.conn()
    cols = [r["name"] for r in c.execute("PRAGMA table_info(images)") if r["name"] != "id"]
    row = dict(c.execute("SELECT * FROM images WHERE rel_path = 'berlin/gate.jpg'").fetchone())
    row.update(rel_path=JUNK[0], album=str(Path(JUNK[0]).parent.as_posix()),
               filename=Path(JUNK[0]).name)
    with db.lock():
        c.execute("INSERT INTO images (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                  [row[col] for col in cols])
        c.commit()
    assert JUNK[0] in _rows()

    result = scanner.full_scan(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                               previews_dir=settings.previews_dir,
                               preview_size=settings.preview_size)
    assert result["removed"] >= 1
    assert JUNK[0] not in _rows()
    assert not _rows("WHERE album LIKE ?", ("%@eaDir%",))


def test_the_console_does_not_list_one(junk):
    lib = Library(settings.photos_dir)
    paths = lib.album_paths()
    assert not any(schema.is_system_dir(part) for path in paths for part in path.split("/"))
    assert all("@" not in p["rel"] and "#" not in p["rel"] and "$" not in p["rel"]
               for p in lib.photos("", recursive=True))
    assert [f["name"] for f in lib.folders("berlin")] == ["mitte"]


def test_the_doctor_does_not_count_one(junk):
    assert not any(schema.is_system_dir(part)
                   for rel in ops.photo_files() for part in rel.split("/"))


def test_the_public_media_routes_refuse_one(junk, client):
    assert client.get("/thumb/" + JUNK[0]).status_code == 404
    assert client.get("/image/" + JUNK[0]).status_code == 404


@pytest.mark.parametrize("album", ["berlin/@eaDir", "#recycle/old-album", "tech/$RECYCLE.BIN"])
def test_no_console_write_lands_in_one(album):
    with pytest.raises(PathRefused):
        writable_target(settings.photos_dir, album, "album.cfg")
