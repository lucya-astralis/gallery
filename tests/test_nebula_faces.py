"""gallery.cfg `faces` / NEBULA_FACES: the stock voice or Nebula's own.

The switch is gallery.cfg `faces` (the console's Site > Look); NEBULA_FACES
is its default while the file says nothing, and that is off. Off, every page
draws JetBrains Mono, Ethnocentric and the Font Awesome subset, exactly as
before the switch existed. On, both surfaces link nebula-faces.css and
nebula-icons.css instead. The markup is the same either
way -- the icon sheets name the same fa-* classes -- so what is tested is the
<head>, the files it points at, and that the two icon sheets cover one list.
"""

import re
from pathlib import Path

import pytest

from aperture import checks, config, runtime

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "aperture" / "gallery" / "static"


def _rules(sheet: str) -> set[str]:
    return set(re.findall(r"^\.fa-([a-z0-9-]+)\{", sheet, re.M))


@pytest.fixture
def nebula_on(monkeypatch):
    """Both surfaces' templates as `faces = nebula` would render them."""
    from aperture.console.app import templates as console_templates
    from aperture.gallery.context import templates as gallery_templates
    for t in (gallery_templates, console_templates):
        monkeypatch.setitem(t.env.globals, "nebula_faces", lambda: True)


@pytest.fixture
def gallery_cfg(photos_dir):
    """The fixture's gallery.cfg with lines added, put back afterwards."""
    path = photos_dir / ".gallery" / "gallery.cfg"
    original = path.read_bytes()
    yield lambda extra: path.write_bytes(original + extra.encode("utf-8"))
    path.write_bytes(original)


def test_the_file_decides_and_the_environment_is_its_default(monkeypatch):
    for env in (False, True):
        monkeypatch.setattr(config, "settings", config.settings.__class__(
            **{**config.settings.__dict__, "nebula_faces": env}))
        for cfg, expected in (({}, env), ({"faces": ["nebula"]}, True),
                              ({"faces": ["stock"]}, False), ({"faces": ["Nebula "]}, True),
                              ({"faces": ["purple"]}, env)):
            monkeypatch.setattr(config, "gallery_config", lambda cfg=cfg: cfg)
            assert config.site_faces() is expected, (env, cfg)


def test_the_console_switch_reaches_the_next_page(client, console, gallery_cfg):
    """What the console's Save writes is what the very next page wears, on
    both surfaces -- no restart, no cache to wait out."""
    assert "fa-icons.css" in client.get("/album/berlin").text
    gallery_cfg("faces = nebula\n")
    for page in (client.get("/album/berlin").text, console.get("/").text):
        head = page.split("</head>")[0]
        assert "nebula-faces.css" in head and "nebula-icons.css" in head
        assert "fa-icons.css" not in head
    assert 'data-faces="nebula"' in console.get("/").text
    gallery_cfg("faces = stock\n")
    assert "fa-icons.css" in client.get("/album/berlin").text
    assert 'data-faces="stock"' in console.get("/").text


def test_the_console_offers_the_switch(console):
    meta = console.get("/api/meta").json()
    assert "faces" in meta["gallery_keys"]
    assert meta["spec"]["faces"] == {"type": "choice", "choices": ["stock", "nebula"]}
    assert meta["faces_default"] == "stock" and meta["help"]["faces"]


def test_an_unknown_faces_value_is_an_error_with_a_fix(indexed, gallery_cfg):
    gallery_cfg("faces = comic-sans\n")
    [issue] = [i for i in checks.gallery() if i["key"] == "faces"]
    assert issue["level"] == "error" and issue["fix"]["key"] == "faces"
    gallery_cfg("faces = nebula\n")
    assert not [i for i in checks.gallery() if i["key"] == "faces"]


def test_the_switch_is_off_unless_it_is_set(monkeypatch):
    monkeypatch.delenv("NEBULA_FACES", raising=False)
    assert runtime.load().nebula_faces is False
    monkeypatch.setenv("NEBULA_FACES", "1")
    assert runtime.load().nebula_faces is True


def test_by_default_the_pages_wear_the_stock_set(client, console):
    for page in (client.get("/").text, client.get("/album/berlin").text):
        assert "fa-icons.css" in page and "fa-solid-subset.woff2" in page
        assert "JetBrainsMono-400.woff2" in page
        assert "nebula-faces.css" not in page and "nebula-icons.css" not in page
        assert "Nebula" not in page.split("</head>")[0]
    door = console.get("/").text
    assert 'href="/static/fa-icons.css"' in door
    assert "nebula-faces.css" not in door and "nebula-icons.css" not in door


def test_switched_on_both_surfaces_wear_nebula(client, console, nebula_on):
    head = client.get("/album/berlin").text.split("</head>")[0]
    assert "nebula-faces.css" in head and "nebula-icons.css" in head
    assert "NebulaMono-400.woff2" in head and "NebulaIcons.woff2" in head
    assert "fa-icons.css" not in head and "fa-solid-subset" not in head
    assert "JetBrainsMono-400.woff2" not in head
    # the font sheet comes before the site's own face, so gallery.cfg's
    # `font = …` still wins over the Nebula Wordmark
    assert head.index("nebula-faces.css") < head.index("nebula-icons.css")
    welcome = client.get("/").text.split("</head>")[0]
    assert "NebulaWordmark-400.woff2" in welcome
    assert "Ethnocentric-Regular.woff2" not in welcome

    door = console.get("/").text.split("</head>")[0]
    assert 'href="/static/nebula-faces.css"' in door
    assert 'href="/static/nebula-icons.css"' in door
    assert "fa-icons.css" not in door


def test_the_nebula_files_are_served_by_both_apps(client, console):
    for app in (client, console):
        for sheet in ("nebula-faces.css", "nebula-icons.css"):
            r = app.get(f"/static/{sheet}")
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
        for font in ("NebulaMono-400", "NebulaMono-700", "NebulaWordmark-400", "NebulaIcons"):
            assert app.get(f"/static/fonts/{font}.woff2").status_code == 200


def test_both_icon_sheets_name_the_same_icons():
    """The markup says fa-<name>; whichever sheet is linked has to know it.
    tools/build_nebula_icons.py refuses to write a sheet with a gap, and this
    catches the other way round: fa-icons.css rebuilt with a new icon and
    nebula-icons.css not rebuilt after it."""
    stock = _rules((STATIC / "fa-icons.css").read_text(encoding="utf-8"))
    nebula = _rules((STATIC / "nebula-icons.css").read_text(encoding="utf-8"))
    assert stock - {"fw"} == nebula - {"fw"}


def test_the_faces_sheet_only_moves_the_two_tokens():
    """Rule 6: a voice is swapped by redefining tokens, never rules."""
    sheet = (STATIC / "nebula-faces.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    root = re.search(r":root\s*\{([^}]*)\}", body).group(1)
    assert sorted(re.findall(r"(--[a-z-]+)\s*:", root)) == ["--display-font", "--mono"]
    # and nothing else is in it but the faces themselves
    blocks = {sel.strip() for sel in re.findall(r"([^{}]+)\{[^{}]*\}", body)}
    assert blocks == {"@font-face", ":root"}
