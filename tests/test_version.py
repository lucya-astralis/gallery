"""The version is one constant, and the changelog is what it means.

A release number that nobody can check drifts in the obvious way: the constant
moves and the notes do not, or the notes are written and the footer keeps
saying last week. Both halves are one edit apart, which is exactly the kind of
pair that is forgotten under a deploy.

So this file holds them to each other:

  * `brand.VERSION` is MAJOR.MINOR.PATCH and nothing else;
  * the newest heading in CHANGELOG.md IS that version, dated;
  * the entries descend, and no version is written twice;
  * every surface that shows a version shows THAT one -- checked through the
    running app, because a constant every module derives from is only worth
    it while nothing has quietly hard-coded the old string.
"""

import datetime
import re
from pathlib import Path

from aperture import brand
from aperture.gallery import api

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.M)


def _entries():
    return HEADING.findall(CHANGELOG.read_text(encoding="utf-8"))


def test_version_is_three_numbers():
    assert re.fullmatch(r"\d+\.\d+\.\d+", brand.VERSION), brand.VERSION


def test_newest_entry_is_this_version():
    entries = _entries()
    assert entries, "CHANGELOG.md has no '## X.Y.Z — YYYY-MM-DD' entry"
    assert entries[0][0] == brand.VERSION, (
        f"brand.VERSION is {brand.VERSION}, the newest changelog entry is "
        f"{entries[0][0]} -- bump both in the same commit")


def test_entries_descend_and_are_unique():
    versions = [tuple(int(n) for n in v.split(".")) for v, _ in _entries()]
    assert versions == sorted(set(versions), reverse=True), versions
    dates = [datetime.date.fromisoformat(d) for _, d in _entries()]
    assert dates == sorted(dates, reverse=True), dates


def test_every_surface_shows_the_one_version(client):
    assert brand.GENERATOR.endswith(brand.VERSION)

    r = client.get("/humans.txt")
    assert f"Version   {brand.VERSION}" in r.text

    r = client.get("/api")
    assert r.json()["product_version"] == brand.VERSION
    # the JSON contract keeps its own number and does not follow the product's
    assert r.json()["version"] == api.API_VERSION

    r = client.get("/")
    assert r.headers["X-Powered-By"].endswith(f"/{brand.VERSION}")
    assert brand.VERSION in r.text
