"""The welcome screen's hero feed, as gallery.cfg resolves it."""

from __future__ import annotations

import logging
from datetime import datetime

from . import albums, config, db, photos, schema

log = logging.getLogger("aperture.welcome")
WELCOME_FEED_MAX = 24
_warned_welcome: set[str] = set()


def lookup_welcome_image(raw: str):
    """Resolve one gallery.cfg welcome entry to an indexed image row.
    Backslashes are tolerated."""
    rel = raw.replace("\\", "/").strip().strip("/")
    if not rel or "/" not in rel:
        return None
    c = db.conn()
    return c.execute(
        "SELECT album, filename, rel_path FROM images WHERE rel_path = ?", (rel,)
    ).fetchone()


def welcome_feed(mobile: bool = False) -> tuple[list[dict], str, str]:
    """Hero feed for the welcome screen honoring gallery.cfg. The device
    keys (welcome_mobile / welcome_desktop) win over the shared `welcome`
    key for their device class; each accepts the same syntax.
    Returns (feed, label, mode) with mode one of manual/showcase/random."""
    cfg = config.gallery_config()
    spec = cfg.get("welcome_mobile" if mobile else "welcome_desktop") or cfg.get("welcome", [])
    mode = "showcase"
    if len(spec) == 1 and spec[0].lower() in schema.WELCOME_KEYWORDS:
        mode = schema.WELCOME_KEYWORDS[spec[0].lower()]
    elif spec:
        feed: list[dict] = []
        seen: set[str] = set()
        for raw in spec[:WELCOME_FEED_MAX]:
            row = lookup_welcome_image(raw)
            if row is None:
                if raw not in _warned_welcome:
                    _warned_welcome.add(raw)
                    log.warning("gallery.cfg: welcome image not indexed, skipping: %r", raw)
                continue
            if row["rel_path"] in seen:
                continue
            seen.add(row["rel_path"])
            feed.append({"album": row["album"], "filename": row["filename"],
                         "rel_path": row["rel_path"]})
        if feed:
            return feed, "CURATED", "manual"
        # nothing resolved -> behave as if the key were absent
    if mode != "random":
        showcase_feed = photos.showcase_rows(limit=12, random_order=True)
        if showcase_feed:
            feed = [
                {"album": r["album"], "filename": r["filename"], "rel_path": r["rel_path"]}
                for r in showcase_feed
            ]
            return feed, "FEATURED", "showcase"
    listed, listed_params = albums.unlisted_clause()
    feed = [
        dict(r)
        for r in db.conn().execute(
            f"SELECT album, filename, rel_path FROM images WHERE {listed} "
            "ORDER BY RANDOM() LIMIT 8",
            listed_params,
        ).fetchall()
    ]
    return feed, "RANDOM", "random"


def archive_updated_at() -> str | None:
    """ISO timestamp of the most recent photo file in the archive, for the
    readout band's LAST UPDATE cell.

    Deliberately MAX(mtime) — the file's own date on disk — and not
    MAX(indexed_at): indexed_at is stamped when the scanner inserts a row, so
    a from-scratch DB rebuild would reset every photo to "today" and the
    archive would claim it was updated when in fact nothing new arrived.
    mtime survives rebuilds and still moves when photos are dropped in."""
    row = db.conn().execute("SELECT MAX(mtime) AS m FROM images").fetchone()
    m = row["m"] if row else None
    if not m:
        return None
    try:
        return datetime.fromtimestamp(float(m)).isoformat()
    except (ValueError, OSError, OverflowError):
        return None
