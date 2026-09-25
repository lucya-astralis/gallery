"""Colour search: read at index time, searched by name in three languages,
offered as a facet and drawn as a strip on the photo page."""

import json

from PIL import Image

from aperture import colors, db, i18n, search


def _picture(parts):
    """An image made of vertical bands: [(rgb, share of the width), ...]."""
    img = Image.new("RGB", (100, 60))
    x = 0
    for rgb, share in parts:
        w = round(100 * share)
        img.paste(rgb, (x, 0, min(100, x + w), 60))
        x += w
    return img


def test_a_picture_is_named_by_what_covers_it():
    found, strip = colors.read(_picture([((40, 90, 210), 0.6), ((245, 245, 245), 0.4)]))
    assert found == ",blue,white,"
    top = json.loads(strip)[0]
    assert colors.name_of(tuple(int(top[0][i:i + 2], 16) for i in (1, 3, 5))) == "blue"
    assert 55 <= top[1] <= 65


def test_a_speck_of_colour_is_not_its_colour():
    found, _ = colors.read(_picture([((200, 30, 30), 0.03), ((20, 20, 20), 0.97)]))
    assert found == ",black,"


def test_names_are_read_in_every_language_and_as_hex():
    assert colors.parse_name("Blau") == "blue"
    assert colors.parse_name("青") == "blue"
    assert colors.parse_name("grey") == colors.parse_name("gray") == "grey"
    assert colors.parse_name("#e0301e") == "red"
    assert colors.parse_name("plaid") is None
    for name in colors.NAMES:            # every name has a label in each language
        for lang in i18n.LANGS:
            assert i18n.t(lang, "color." + name) != "color." + name


def test_the_grammar_knows_the_key():
    f = search.parse("farbe:blau").filters[0]
    assert f.fact == "color" and f.ok and f.params == [",blue,"]
    assert not search.parse("color:plaid").filters[0].ok


def test_the_scan_reads_every_photo(indexed):
    rows = db.conn().execute("SELECT colors, palette FROM images").fetchall()
    assert rows and all(r["colors"] is not None and r["palette"] for r in rows)


def _a_colour_the_fixture_has():
    for row in db.conn().execute("SELECT rel_path, colors FROM images").fetchall():
        names = colors.names_of(row["colors"])
        if names:
            return row["rel_path"], names[0]
    raise AssertionError("no fixture photo carries a named colour")


def test_search_by_colour_finds_the_photo(indexed, client):
    rel, name = _a_colour_the_fixture_has()
    body = client.get("/search", params={"q": "color:" + name}).text
    assert rel.rsplit("/", 1)[-1] in body
    # the German key and name find the same thing
    de = i18n.t("de", "color." + name).lower()
    assert rel.rsplit("/", 1)[-1] in client.get("/search", params={"q": "farbe:" + de}).text


def test_the_colour_facet_is_offered_and_toggles(indexed, client):
    _, name = _a_colour_the_fixture_has()
    browse = client.get("/search").text
    assert "swatch--" + name in browse
    on = client.get("/search", params={"q": "color:" + name}).text
    assert 'swatch swatch--%s is-on' % name in on


def test_the_photo_page_draws_its_palette(indexed, client):
    rel, _ = _a_colour_the_fixture_has()
    body = client.get("/image/" + rel).text
    assert 'class="palette-strip"' in body
    assert '<rect x="0' in body and 'fill="#' in body
    assert "/search?q=color%3A" in body


def test_both_sheets_paint_the_swatches_the_names_promise():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "aperture"
    for sheet in (root / "gallery" / "static" / "style.css", root / "console" / "static" / "style.css"):
        css = sheet.read_text(encoding="utf-8")
        found = {m.group(1): m.group(2).lower()
                 for m in re.finditer(r"\.swatch--(\w+)\s*\{\s*background:\s*(#[0-9a-fA-F]{6})", css)}
        assert found == colors.SWATCH, sheet


def test_the_library_hands_the_console_each_photos_colours(indexed, console):
    photos = console.get("/api/library").json()["photos"]
    assert all(isinstance(p["colors"], list) for p in photos)
    assert any(p["palette"] and p["palette"][0]["hex"].startswith("#") for p in photos)
    assert console.get("/api/meta").json()["color_names"] == colors.NAMES
