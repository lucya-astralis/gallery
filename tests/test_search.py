"""The search grammar (search.py) and the capture facts it filters on.

Every fixture photo carries the same EXIF (see conftest._jpeg): Fixture Optics
/ Fixture Cam, f/2.8, ISO 400, 35 mm. So the filters are asserted as "all" or
"none", and the dates -- which do differ -- carry the finer cases.
"""

import json
import sqlite3

import pytest

from aperture import capture, db, search


def _found(client, q) -> set[str]:
    body = client.get("/api/photos", params={"q": q, "limit": 200}).json()
    return {item["rel_path"] for item in body["items"]}


@pytest.fixture(scope="module")
def everything(indexed):
    return {r["rel_path"] for r in db.conn().execute("SELECT rel_path FROM images")}


# ----- the facts --------------------------------------------------------
def test_facts_read_like_the_pages_print_them():
    assert capture.facts({"Make": "Fixture Optics", "Model": "Fixture Cam",
                          "FNumber": 2.8, "ISOSpeedRatings": 400, "FocalLength": 35.0}) == {
        "camera": "Fixture Cam", "lens": None, "focal": 35, "aperture": 2.8, "iso": 400}
    fact = capture.facts({"Make": "Apple", "Model": "iPhone 17", "ISOSpeedRatings": [800],
                          "FocalLength": 6.9, "FocalLengthIn35mmFilm": 26,
                          "LensModel": "iPhone 17 back camera 6.9mm f/1.8"})
    assert fact["camera"] == "iPhone 17"          # Apple by model alone, as on /stats
    assert fact["iso"] == 800                     # a list of one
    assert fact["focal"] == 26                    # the 35 mm equivalent wins
    assert fact["lens"].startswith("iPhone 17 back")
    assert capture.facts(None) == {"camera": None, "lens": None, "focal": None,
                                   "aperture": None, "iso": None}


def test_the_index_stores_the_facts(indexed):
    row = db.conn().execute(
        "SELECT camera, focal, aperture, iso FROM images WHERE rel_path = 'berlin/gate.jpg'"
    ).fetchone()
    assert dict(row) == {"camera": "Fixture Cam", "focal": 35, "aperture": 2.8, "iso": 400}


def test_migrate_fills_the_facts_for_rows_indexed_before_them():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO images (album, filename, rel_path, mtime, exif_json) VALUES (?, ?, ?, ?, ?)",
        ("old", "a.jpg", "old/a.jpg", 1.0, json.dumps({"Model": "X100V", "Make": "FUJIFILM",
                                                       "ISOSpeedRatings": 3200})))
    db.migrate(conn)
    db.migrate(conn)                              # a second start changes nothing
    row = conn.execute("SELECT camera, iso FROM images").fetchone()
    assert dict(row) == {"camera": "FUJIFILM X100V", "iso": 3200}


# ----- the grammar ------------------------------------------------------
def test_parse_splits_words_from_filters():
    q = search.parse('tv tower iso:400-800 lens:"35mm f/2" 10:30')
    assert q.text == "tv tower 10:30"            # 10:30 is no key, so a word
    assert [(f.fact, f.value, f.ok) for f in q.filters] == [
        ("iso", "400-800", True), ("lens", "35mm f/2", True)]
    assert q.without(q.filters[0]) == 'tv tower lens:"35mm f/2" 10:30'
    # an aperture as the EXIF panel prints it is a filter without a key
    assert [(f.fact, f.value) for f in search.parse("f/2.8 ƒ1.4-2 f/abc").filters] == [
        ("aperture", "2.8"), ("aperture", "1.4-2")]
    assert search.parse("f/abc").text == "f/abc"
    assert search.term("camera", "Fixture Cam") == 'camera:"Fixture Cam"'
    assert search.term("iso", 400) == "iso:400"


@pytest.mark.parametrize("q", ["iso:400", "iso:100-400", "iso:-400", "iso:400-",
                               "f:2.8", "f/2.8", "aperture:ƒ2.8", "f:2-4",
                               "mm:35", "mm:35mm", "focal:24-50",
                               'camera:"fixture cam"', "cam:FIXTURE", "fixture"])
def test_filters_that_describe_every_photo_find_every_photo(client, everything, q):
    assert _found(client, q) == everything


@pytest.mark.parametrize("q", ["iso:800", "iso:401-", "f:4", "mm:50", "camera:nikon",
                               "lens:summicron", "date:2024", "iso:abc", "%"])
def test_filters_that_describe_none_find_none(client, indexed, q):
    assert _found(client, q) == set()


def test_dates_narrow_by_prefix_and_by_range(client, indexed):
    assert _found(client, "date:2025-12") == {
        "berlin/gate.jpg", "berlin/wall.jpg", "berlin/tv-tower.jpg",
        "berlin/mitte/dome.jpg", "berlin/mitte/river.jpg"}
    assert _found(client, "date:2026-02-11") == {"tech/desk.jpg"}
    assert _found(client, "date:2026-01..2026-02") == {"tech/desk.jpg", "tech/rack.jpg"}
    assert _found(client, "2026-03") == {"phone/portrait.jpg"}   # a bare date is a date


def test_words_and_filters_combine(client, indexed):
    assert _found(client, "berlin date:2025-12-05") == {
        "berlin/tv-tower.jpg", "berlin/mitte/dome.jpg", "berlin/mitte/river.jpg"}
    # a filter that cannot be read is ignored, not a reason to find nothing
    assert _found(client, "tech iso:abc") == _found(client, "tech")


# ----- the pages --------------------------------------------------------
def test_the_search_page_shows_each_filter_as_a_chip(client, indexed):
    body = client.get("/search", params={"q": "berlin iso:400 f:abc"}).text
    assert "gate.jpg" in body
    assert 'class="tag search-filter"' in body
    assert 'class="tag search-filter is-bad"' in body
    assert 'href="/search?q=berlin%20f%3Aabc"' in body        # the chip drops iso:400
    only = client.get("/search", params={"q": "iso:400"}).text
    assert 'href="/albums"' in only and "tv-tower.jpg" in only


def test_exif_values_link_to_their_search(client, indexed):
    body = client.get("/image/berlin/gate.jpg").text
    assert 'href="/search?q=iso%3A400"' in body
    assert 'href="/search?q=f%3A2.8"' in body
    assert 'href="/search?q=camera%3A%22Fixture%20Cam%22"' in body
    assert 'href="/search?q=date%3A2025-12-04"' in body


def test_stats_bars_link_to_their_search(client, indexed):
    body = client.get("/stats").text
    assert 'href="/search?q=camera%3A%22Fixture%20Cam%22"' in body
    assert 'href="/search?q=iso%3A201-400"' in body
