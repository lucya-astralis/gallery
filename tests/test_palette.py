"""The identity palettes follow the accent -- on the gallery, in the console
and on the console's backdrop -- and resolve to the sheets' own values for
the built-in accent."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aperture import palette, theme
from aperture.console import security
from aperture.console.app import app as console_app

PACKAGE = Path(__file__).resolve().parent.parent / "aperture"
NEBULA_CSS = PACKAGE.parent / "nebula" / "nebula.css"


@pytest.fixture
def gallery_cfg(photos_dir):
    path = photos_dir / ".gallery" / "gallery.cfg"
    original = path.read_bytes()
    yield lambda text: path.write_text(text, encoding="utf-8")
    path.write_bytes(original)


def _decls(css: str, selector: str) -> dict:
    """Every `--token: #hex` inside the blocks that start with `selector {`."""
    out = {}
    for block in re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css):
        for name, value in re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", block):
            out[name] = value.upper()
    return out


@pytest.mark.parametrize("sheet", [PACKAGE / "console" / "static" / "style.css", NEBULA_CSS])
def test_the_built_in_accent_derives_the_sheets_own_values(sheet):
    """The hex values in the sheets are what the derivation gives for the
    built-in accent -- so a page with no accent set and a page with the
    built-in accent set look the same."""
    if not sheet.exists():
        pytest.skip("no nebula checkout")
    css = sheet.read_text(encoding="utf-8")
    mist = palette.mist_tokens(palette.BASE_ACCENT)
    root = {}
    for block in re.findall(r"(?m)^:root\s*\{([^}]*)\}", css):
        root.update({n: v.upper() for n, v in re.findall(r"(--mist[\w-]*):\s*(#[0-9A-Fa-f]{6})", block)})
    assert root == mist
    stardust = _decls(css, ':root[data-palette="stardust"], [data-palette-preview="stardust"]') \
        or _decls(css, ':root[data-palette="stardust"]')
    assert stardust == palette.stardust_tokens(palette.BASE_ACCENT)


def test_another_accent_turns_the_palettes_with_it():
    green = (0x3F, 0xC2, 0x7A)
    mist = palette.mist_tokens(green)
    assert mist != palette.mist_tokens(palette.BASE_ACCENT)
    r, g, b = (int(mist["--mist"][i:i + 2], 16) for i in (1, 3, 5))
    assert g > r and g > b                       # a green silver, not a lilac one
    # lightness stays the silver's, whatever the hue
    assert abs(palette.rgb_to_oklch((r, g, b))[0] - 0.80) < 0.01


def test_a_grey_accent_leaves_plain_silver():
    for value in palette.mist_tokens((0x88, 0x88, 0x88)).values():
        r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        assert max(r, g, b) - min(r, g, b) <= 2


def test_the_gallery_theme_sheet_carries_the_palettes(client, gallery_cfg):
    gallery_cfg("accent = #3fc27a\n")
    css = client.get(theme.theme_css_url(None)).text
    green = theme.parse_hex_color("#3fc27a")
    shades = theme.accent_shades(green)
    for name, value in palette.mist_tokens(shades["_rgb"]).items():
        assert "%s:%s" % (name, value) in css
    assert ':root[data-palette="stardust"],[data-palette-preview="stardust"]{' in css
    assert "--acc-soft-rgb:" in css


def test_the_console_wears_the_site_accent_before_sign_in(gallery_cfg):
    """The look sheet and the backdrop are open at the door, and turn with
    gallery.cfg's accent."""
    security.set_password("console-test-password")
    security.reset()
    try:
        door = TestClient(console_app)
        gallery_cfg("accent = #3fc27a\n")
        css = door.get("/theme.css")
        assert css.status_code == 200
        shades = theme.accent_shades(theme.parse_hex_color("#3fc27a"))
        assert "--acc:%s" % shades["acc"] in css.text
        svg = door.get("/bg/nova-wide-16-9.svg")
        assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
        assert "#4034c0" not in svg.text          # the built-in violet is gone
        assert "#05050a" in svg.text              # the black is untouched
        assert door.get("/bg/other.svg").status_code == 401   # only the two cuts are open
        assert 'id="look-sheet"' in door.get("/login").text
    finally:
        security.clear_password()
        security.reset()


def test_an_accent_preview_writes_nothing_and_is_not_cached(console, gallery_cfg):
    gallery_cfg("accent = #616ef3\n")
    res = console.get("/theme.css", params={"accent": "#e0625f"})
    assert res.headers["cache-control"] == "no-store"
    assert "--acc:" in res.text
    assert console.get("/theme.css", params={"accent": "red; }"}).text.startswith(":root{}")
    assert "#4034c0" in console.get("/bg/nova-square.svg", params={"accent": "none"}).text
    assert console.get("/bg/other.svg").status_code == 404
