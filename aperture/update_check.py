"""Is there a newer aperture than this one?

Every gallery says which aperture it runs, publicly, at /api/version:

    {"version": "1.8.0", "date": "2026-09-15",
     "url": "https://github.com/lucya-astralis/gallery/blob/main/CHANGELOG.md#...",
     "notes": "One sentence on what the release is."}

`manifest()` builds it from VERSION and the newest CHANGELOG.md entry. The
maker's own gallery (brand.UPDATE_URL, images.lucya.sh) always runs the newest
release, so its answer IS the latest one -- there is no file to publish and
nothing to forget after a push. Only `version` is required of an answer; the
rest is shown when it is there.

The SERVER asks, never the browser. The console's CSP connects nowhere but
itself and stays that way, one answer serves every open tab, and the request
carries nothing but the product's User-Agent -- not the version, not an
address, not an id. What comes back is treated as untrusted: a version that is
not three numbers is refused, a link that is not https is dropped, the notes
are plain text of bounded length.

It is a notice and nothing more. Nothing is downloaded or installed, and a
check that fails is a line on the Changelog place, never an error anywhere
else. UPDATE_CHECK=0 turns it off; UPDATE_URL points it somewhere else.
"""

from __future__ import annotations

import functools
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

from . import brand
from .runtime import settings

# An answer is good for twelve hours; a failed ask is retried after one. A
# "check again" from the console is honoured at most once a minute, so a
# button cannot be used to hammer somebody else's server.
FRESH_FOR = 12 * 3600
RETRY_AFTER = 3600
MIN_GAP = 60
TIMEOUT = 5
MAX_BYTES = 64 * 1024
MAX_NOTES = 280

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The release notes this build ships with; the Dockerfile copies them next to
# the package. The console's Changelog place renders the same file.
CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})\s*$", re.M)

_lock = threading.Lock()
_last: dict | None = None
_at = 0.0


def version_tuple(text: str) -> tuple[int, int, int] | None:
    """`1.8.0` -> (1, 8, 0); anything that is not exactly three numbers -> None."""
    text = (text or "").strip() if isinstance(text, str) else ""
    if not _VERSION.fullmatch(text):
        return None
    major, minor, patch = (int(n) for n in text.split("."))
    return major, minor, patch


def read_manifest(payload) -> dict:
    """The published JSON -> {version, date, url, notes}, or ValueError. The
    optional fields come back as None when they are missing or not usable."""
    if not isinstance(payload, dict):
        raise ValueError("not a JSON object")
    version = payload.get("version")
    if version_tuple(version) is None:
        raise ValueError("no MAJOR.MINOR.PATCH version in it")
    date = payload.get("date")
    url = payload.get("url")
    notes = payload.get("notes")
    if isinstance(notes, str):
        notes = " ".join(notes.split())[:MAX_NOTES] or None
    else:
        notes = None
    return {
        "version": version.strip(),
        "date": date if isinstance(date, str) and _DATE.fullmatch(date) else None,
        # it becomes an href in the console: https or nothing
        "url": url if isinstance(url, str) and url.startswith("https://") else None,
        "notes": notes,
    }


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation out, spaces to
    hyphens -- `1.8.0 — 2026-09-15` -> `180--2026-09-15`."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


@functools.cache
def manifest() -> dict:
    """What this copy runs, in the shape read_manifest() reads: VERSION, and
    the date, link and one-line summary of its CHANGELOG.md entry (the test
    net holds the two to each other). Without the file -- or with an entry for
    another version -- it is the version alone."""
    out = {"version": brand.VERSION, "date": None, "url": None, "notes": None}
    try:
        text = CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return out
    match = _HEADING.search(text)
    if not match or match.group(1) != brand.VERSION:
        return out
    summary = text[match.end():].lstrip("\n").split("\n\n", 1)[0]
    heading = match.group(0)[3:].strip()
    out.update(
        date=match.group(2),
        url="%s/blob/main/CHANGELOG.md#%s" % (brand.REPO_URL, _anchor(heading)),
        notes=" ".join(summary.split())[:MAX_NOTES] or None,
    )
    return out


def _fetch(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": brand.USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("larger than %d KB" % (MAX_BYTES // 1024))
    return json.loads(body.decode("utf-8"))


def _ask() -> dict:
    try:
        latest = read_manifest(_fetch(settings.update_url))
    except Exception as exc:  # any failure is the same answer: not now
        reason = str(exc).strip() or type(exc).__name__
        return {"state": "unreachable", "latest": None, "error": reason[:160]}
    newer = version_tuple(latest["version"]) > version_tuple(brand.VERSION)
    return {"state": "available" if newer else "current", "latest": latest, "error": None}


def status(*, fresh: bool = False, now: float | None = None) -> dict:
    """What the console shows: `state` is one of off / current / available /
    unreachable. Asks the server only when the cached answer has run out (or
    when `fresh` and the last ask is more than MIN_GAP old)."""
    global _last, _at
    base = {"current": brand.VERSION, "source": settings.update_url}
    if not settings.update_check:
        return {**base, "state": "off", "latest": None, "error": None, "checked_at": None}
    now = time.time() if now is None else now
    # One ask at a time: a second tab arriving mid-ask waits for its answer
    # (bounded by TIMEOUT) rather than sending its own.
    with _lock:
        age = now - _at
        if _last is None:
            due = True
        elif fresh and age >= MIN_GAP:
            due = True
        else:
            due = age >= (RETRY_AFTER if _last["state"] == "unreachable" else FRESH_FOR)
        if due:
            _last, _at = _ask(), now
        return {**base, **_last, "checked_at": int(_at)}


def reset() -> None:
    """Forget the cached answer. FOR TESTS."""
    global _last, _at
    with _lock:
        _last, _at = None, 0.0
