"""The console's Changelog place: the release notes, the repository, the maker.

The notes come from CHANGELOG.md, rendered on the server at /api/about, so
what the place shows is always the file this build was made from.
"""

import re

from fastapi.testclient import TestClient

from aperture import brand
from aperture.console import security
from aperture.console.app import _release_notes, app as console_app

VERSION = re.compile(r"\d+\.\d+\.\d+")


def test_about_names_the_build_the_repository_and_the_maker(console):
    body = console.get("/api/about").json()
    assert body["product"] == brand.PRODUCT
    assert body["version"] == brand.VERSION
    assert body["repo"] == "https://github.com/lucya-astralis/gallery"
    assert body["maker"] == {"name": "lucya.sh", "url": "https://lucya.sh",
                             "pfp": "/static/maker.webp"}
    assert body["vendor"] == {"name": "lucya.systems", "url": "https://lucya.systems"}
    assert body["design"]["name"] == "Nebula" and body["design"]["url"].endswith("/nebula")


def test_the_notes_are_every_release_newest_first(console):
    releases = console.get("/api/about").json()["releases"]
    versions = [r["version"] for r in releases]
    assert versions[0] == brand.VERSION            # this build's own entry leads
    assert all(VERSION.fullmatch(v) for v in versions)
    assert versions == sorted(versions, key=lambda v: tuple(map(int, v.split("."))),
                              reverse=True)
    assert "1.0.0" in versions
    newest = releases[0]["html"]
    assert "<strong>" in newest and "<li>" in newest
    assert "<hr" not in newest                      # the rule between entries is not one
    assert all('href="README.md' not in r["html"] for r in releases)


def test_a_relative_link_points_at_the_repository():
    [release] = _release_notes(
        "# Changelog\n\n## 9.9.9 — 2030-01-01\n\nSee [the rules](README.md#versions).\n\n"
        "---\n\n## Before 1.0\n\nNot a release.\n")
    assert release["version"] == "9.9.9" and release["date"] == "2030-01-01"
    assert f'href="{brand.REPO_URL}/blob/main/README.md#versions"' in release["html"]
    assert "<hr" not in release["html"] and "Not a release" not in release["html"]


def test_the_maker_picture_is_served(console):
    res = console.get("/static/maker.webp")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"


def test_the_notes_stay_behind_the_door(indexed):
    security.set_password("a long enough password for the door test")
    security.reset()
    try:
        assert TestClient(console_app).get("/api/about").status_code == 401
    finally:
        security.clear_password()
        security.reset()
