"""One set of config checks, and every front end asking it.

The console's "Check all" and the doctor used to be two implementations of
the same checks, and they disagreed. What this file pins is less that each
check is right than that there is only one of them: doctor, the console and
the CLI are asserted to hand back the SAME issues for the same file.
"""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aperture import checks, cli, ops, theme
from aperture.console import security
from aperture.console.app import app as console_app

PACKAGE = Path(checks.__file__).parent


@pytest.fixture
def album_cfg(photos_dir):
    """tech's album.cfg, rewritten per test and restored byte for byte."""
    path = photos_dir / "tech" / ".album" / "album.cfg"
    original = path.read_bytes()
    yield lambda text: path.write_text(text, encoding="utf-8")
    path.write_bytes(original)


@pytest.fixture
def gallery_cfg(photos_dir):
    path = photos_dir / ".gallery" / "gallery.cfg"
    original = path.read_bytes()
    yield lambda text: path.write_text(text, encoding="utf-8")
    path.write_bytes(original)


def keyed(issues, key):
    return [i for i in issues if i["key"] == key]


# ----- one implementation ----------------------------------------------
def test_there_is_one_implementation():
    assert not (PACKAGE / "console" / "validate.py").exists()
    old = {"check_album_cfg", "check_gallery_cfg", "check_album", "check_gallery",
           "check_all", "check_brand", "check_theme", "check_wallpaper_knobs"}
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef):
                assert node.name not in old, f"{path.relative_to(PACKAGE)} defines {node.name}"


def test_every_issue_says_where_it_is(indexed, album_cfg):
    album_cfg("name = Tech\ncolour = red\n")
    issues = checks.everything()
    assert issues
    for issue in issues:
        assert set(issue) == {"scope", "album", "level", "key", "detail"}
        assert issue["level"] in ("error", "warn")
        assert (issue["scope"] == "gallery") == (issue["album"] is None)


def test_the_fixture_tree_is_clean(indexed):
    assert checks.everything() == []


# ----- the same issues, whoever asks ------------------------------------
def test_doctor_reports_exactly_these(indexed, album_cfg):
    album_cfg("name = Tech\ncolour = red\n")
    assert ops.doctor("tech")["problems"]["config"] == checks.album("tech")


def test_the_console_reports_exactly_these(indexed, album_cfg):
    security.clear_password()
    album_cfg("name = Tech\ncolour = red\n")
    client = TestClient(console_app)
    assert client.get("/api/album?path=tech").json()["issues"] == checks.album("tech")
    everything = client.get("/api/validate").json()["issues"]
    assert [i for i in everything if i["album"] == "tech"] == checks.album("tech")


def test_the_cli_reports_exactly_these(indexed, album_cfg):
    album_cfg("name = Tech\ncolour = red\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--no-color", "--logo", "off", "cfg", "tech", "--json"])
    assert code == 1                       # an error makes it a failed check
    assert json.loads(buf.getvalue())["issues"] == checks.album("tech")


# ----- what the checks say ----------------------------------------------
def test_an_unknown_key_is_an_error(indexed, album_cfg):
    album_cfg("name = Tech\ncolour = red\n")
    [issue] = keyed(checks.album("tech"), "colour")
    assert issue["level"] == "error" and "unknown key" in issue["detail"]


@pytest.mark.parametrize("value,expect", [
    ("sub/mark.png", "bare file name"),
    ("mark.php", "is not one of"),
    ("mark.png", "is not in .album/"),
])
def test_a_file_key_says_what_is_wrong_with_it(indexed, album_cfg, value, expect):
    album_cfg(f"name = Tech\nicon = {value}\n")
    [issue] = keyed(checks.album("tech"), "icon")
    assert issue["level"] == "error" and expect in issue["detail"], issue


def test_a_file_the_gallery_serves_is_not_an_issue(indexed, album_cfg, photos_dir):
    """The checks and the gallery's resolver must agree in both directions."""
    mark = photos_dir / "tech" / ".album" / "mark.png"
    mark.write_bytes(b"png")
    try:
        album_cfg("name = Tech\nicon = mark.png\n")
        assert not keyed(checks.album("tech"), "icon")
        assert theme.album_icon_file("tech") == mark
    finally:
        mark.unlink()


def test_a_phone_wallpaper_explains_its_whitelist(indexed, album_cfg):
    album_cfg("name = Tech\nwallpaper_mobile = loop.mp4\n")
    [issue] = keyed(checks.album("tech"), "wallpaper_mobile")
    assert "stills only" in issue["detail"]


def test_a_font_scale_without_a_font_is_a_warning(indexed, album_cfg):
    album_cfg("name = Tech\nfont_scale = 1.2\n")
    [issue] = keyed(checks.album("tech"), "font_scale")
    assert issue["level"] == "warn" and "no `font`" in issue["detail"]


def test_an_empty_accent_is_unset_not_wrong(indexed, album_cfg):
    album_cfg("name = Tech\naccent =\n")
    assert not keyed(checks.album("tech"), "accent")


def test_the_values_handed_in_are_the_ones_checked(indexed):
    """The console checks what it has just written, not the cache's idea of
    the file, which a same-size edit inside one mtime tick would not change."""
    [issue] = checks.album("tech", {"colour": ["red"]})
    assert issue["key"] == "colour"
    assert checks.album("tech") == []


def test_a_curated_album_sort_without_its_list_is_a_warning(indexed, gallery_cfg):
    gallery_cfg("album_sort = curated\n")
    [issue] = keyed(checks.gallery(), "album_sort")
    assert issue["level"] == "warn"
    assert issue["scope"] == "gallery" and issue["album"] is None


# ----- the index opens itself ----------------------------------------------
def test_the_database_opens_on_first_use(tmp_path):
    """A console-only process never runs the indexer's startup, which is what
    used to call db.init(); every check against the index then failed with
    "DB not initialised". Nothing has to remember to open it now."""
    env = dict(os.environ, DATA_DIR=str(tmp_path / "data"), PHOTOS_DIR=str(tmp_path / "photos"))
    probe = "from aperture import db; print(db.conn().execute('SELECT COUNT(*) FROM images').fetchone()[0])"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            env=env, cwd=str(PACKAGE.parent))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"
