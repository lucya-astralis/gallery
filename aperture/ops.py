"""The operations surface, as data.

Everything the operator CLI reports — the live state of the indexer, what
`doctor` checked, which config keys resolve to what — used to be computed
inside `cli.py`, one function per command, immediately next to the code that
printed it. That was fine while a terminal was the only place it could be
asked from.

The console can ask now. It runs in the same package and, in the `all` role,
in the same process; and even where it does not (`APERTURE_ROLE=console`, its
own container) it can read the index and write the control channel, which is
all any of this needs. So the computation moved here and the two front ends
are exactly that:

    cli.py          renders these payloads to a terminal
    console/ops.py  serves them over HTTP

Nothing in this module prints, and nothing in it imports the terminal layer.
The only formatting that survived is the handful of strings that are part of a
payload's meaning — a finding's `detail`, a duration in a summary — because
those are the report, not its presentation.

**Actions go through the control channel, not through a function call.** Even
in the `all` role, where the console shares a process with the indexer, a scan
request is written to `data/control/` and picked up by the control loop, the
same way the CLI has always done it. One code path, and the split deployment
keeps working for free.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import albums, checks, config, control, db, scanner, schema, theme
from .runtime import settings


def stamp(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def dur(seconds) -> str:
    """Compact duration: 2d 3h / 4h 12m / 3m 07s / 12.4s."""
    if not isinstance(seconds, (int, float)):
        return "—"
    s = int(seconds)
    if s >= 86400:
        return f"{s // 86400}d {(s % 86400) // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{seconds:.1f}s"


def ago(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "never"
    return f"{dur(time.time() - ts)} ago"


def bytes_h(n) -> str:
    if not isinstance(n, (int, float)):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ----- shared plumbing --------------------------------------------------
def connect():
    """Open the index. Every non-control command needs this — the CLI runs
    outside the server process, so db.init() has not run here."""
    return db.init(settings.data_dir)


def server_status() -> tuple[dict | None, bool]:
    st = control.read_status()
    return st, control.status_is_live(st)


class UnknownAlbum(Exception):
    """An album was named on the command line but matches nothing.

    Raised rather than quietly scoping to a folder that is not there: a typo
    used to come back as "0 files · no problems found", which reads exactly
    like a clean bill of health for the album you meant.
    """


def norm_album(raw: str | None) -> str | None:
    """Normalize an album path off the command line and resolve its casing
    against the index, so `Japan_2026/kansai` finds the real folder. Raises
    UnknownAlbum when a name was given and nothing matches it."""
    if not raw:
        return None
    album = raw.replace("\\", "/").strip().strip("/")
    if not album:
        return None
    if (settings.photos_dir / album).is_dir():
        return album
    resolved = albums.resolve_album_path(album)
    if not resolved:
        raise UnknownAlbum(album)
    return resolved


def photo_files(root: str | None = None):
    """Every indexable photo on disk, as rel_path — the same set full_scan
    walks (album folder required, `.album/` metadata skipped)."""
    base = settings.photos_dir / root if root else settings.photos_dir
    if not base.is_dir():
        return []
    found = []
    for file in sorted(base.rglob("*")):
        if not file.is_file() or not schema.is_image(file):
            continue
        relp = file.relative_to(settings.photos_dir)
        if len(relp.parts) < 2 or scanner.is_meta_path(relp):
            continue
        found.append(relp.as_posix())
    return found


def effective_mtime(rel: str) -> float | None:
    """The mtime the indexer stores: the photo's, or its `.tags` sidecar's
    when that is newer (see scanner.index_image)."""
    src = settings.photos_dir / rel
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return None
    sidecar = src.with_suffix(src.suffix + ".tags")
    try:
        return max(mtime, sidecar.stat().st_mtime)
    except OSError:
        return mtime


def derivative_files(d: Path):
    """Every generated file under one derivative directory, in any format the
    gallery has ever written there (scanner.DERIVATIVE_EXTS). The sweeps that
    use this look for files no photo maps to any more — which after a tier
    changes format includes that tier's own leftovers, e.g. the JPEG thumbs a
    gallery built before the WebP switch. `thumbs --prune --apply` is what
    clears them."""
    for ext in scanner.DERIVATIVE_EXTS:
        yield from d.rglob("*" + ext)


def derivatives(rel: str) -> dict[str, Path]:
    """Where the generated files for one photo live. `full` only applies to
    formats the browser cannot show (HEIC/HEIF), which are converted on
    demand — see scanner.ensure_full_jpeg."""
    paths = {
        "thumb": (settings.thumbs_dir / rel).with_suffix(scanner.THUMB_EXT),
        "preview": (settings.previews_dir / rel).with_suffix(scanner.PREVIEW_EXT),
    }
    if scanner.needs_jpeg_conversion(settings.photos_dir / rel):
        paths["full"] = (settings.fulls_dir / rel).with_suffix(".jpg")
    return paths


def derivative_state(rel: str) -> dict[str, str]:
    """ok / missing / stale per derivative, against the source mtime."""
    src_mtime = effective_mtime(rel)
    state = {}
    for kind, path in derivatives(rel).items():
        try:
            dst_mtime = path.stat().st_mtime
        except OSError:
            state[kind] = "missing"
            continue
        if src_mtime is not None and dst_mtime < src_mtime:
            state[kind] = "stale"
        else:
            state[kind] = "ok"
    return state


def scope_rows(c, album: str | None):
    """Indexed rows, optionally limited to one album subtree. substr() (not
    LIKE) keeps `_`/`%` in album names from acting as wildcards."""
    if album:
        prefix = album + "/"
        return c.execute(
            "SELECT * FROM images WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY rel_path",
            (album, len(prefix), prefix),
        ).fetchall()
    return c.execute("SELECT * FROM images ORDER BY rel_path").fetchall()


def index_counts(c) -> dict:
    row = c.execute(
        "SELECT COUNT(*) AS images, COUNT(DISTINCT album) AS albums, "
        "SUM(is_showcase) AS featured, SUM(size) AS bytes FROM images"
    ).fetchone()
    tags = c.execute("SELECT COUNT(*) AS n FROM tags").fetchone()["n"]
    db_file = settings.data_dir / "gallery.db"
    db_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_file) + suffix)
        try:
            db_bytes += p.stat().st_size
        except OSError:
            pass
    return {
        "images": row["images"] or 0,
        "albums": row["albums"] or 0,
        "featured": row["featured"] or 0,
        "bytes": row["bytes"] or 0,
        "tags": tags,
        "db_bytes": db_bytes,
    }


# ----- featured provenance ---------------------------------------------
def featured_map() -> tuple[dict[str, list[tuple[str, str]]], list[dict]]:
    """Which album.cfg entry featured which photo.

    Mirrors main._recompute_featured, but keeps the provenance instead of
    only the resulting set: returns (rel_path -> [(album, cfg entry)], list
    of entries that matched nothing). The unresolved list is the interesting
    half — a typo in `featured = …` is silent in the app.
    """
    c = db.conn()
    by_photo: dict[str, list[tuple[str, str]]] = {}
    unresolved: list[dict] = []
    for album in albums.albums_with_ancestors():
        cfg = config.album_config(album)
        if "featured" not in cfg:
            continue
        items = cfg["featured"]
        if any(i.strip().lower() in ("*", "all") for i in items):
            rels = [r["rel_path"] for r in
                    c.execute("SELECT rel_path FROM images WHERE album = ?", (album,))]
            if not rels:
                unresolved.append({"album": album, "entry": "*", "reason": "album has no photos"})
            for rel in rels:
                by_photo.setdefault(rel, []).append((album, "*"))
            continue
        for item in items:
            item = item.strip()
            if not item:
                continue
            rels = albums.resolve_photo_refs(album, [item])
            if not rels:
                unresolved.append({"album": album, "entry": item, "reason": "no photo matches"})
            for rel in rels:
                by_photo.setdefault(rel, []).append((album, item))
    return by_photo, unresolved


def wallpaper_line(album: str, variant: str) -> str:
    """The backdrop this album actually shows, named through the tiers it is
    resolved by: `file.jpg` for its own, `file.jpg (from japan_2026)` for an
    inherited one, `file.jpg (gallery.cfg)` when the site's own is what shows,
    and `— (shipped default)` when nothing anywhere configures one."""
    src = theme.album_wallpaper_source(album, variant)
    if src is not None:
        owner, path = src
        return path.name if owner == album else f"{path.name} (from {owner})"
    site = theme.site_wallpaper_file(variant)
    if site is not None:
        return f"{site.name} (gallery.cfg)"
    return "— (shipped default)"


# ----- state ------------------------------------------------------------
def status() -> dict:
    """Everything both front ends open with: is the indexer alive, is it
    paused, what did the last scan do, and what does the index hold.

    Reads the control directory and the database and touches nothing, so it
    is safe to call from a console process that does not own the indexer.
    """
    st, live = server_status()
    pause = control.pause_info()
    counts = index_counts(connect())
    return {"server": st, "live": live, "paused": pause is not None,
            "pause": pause, "index": counts,
            "control_dir": str(control.control_dir())}


def paths() -> dict:
    """Where this process thinks everything is. Part of `status` in the CLI's
    rendering; separate here because the console shows it in its own panel."""
    return {"photos": str(settings.photos_dir), "thumbs": str(settings.thumbs_dir),
            "previews": str(settings.previews_dir), "data": str(settings.data_dir),
            "scan_interval": settings.scan_interval,
            "thumb_size": settings.thumb_size,
            "preview_size": settings.preview_size,
            "watcher": settings.enable_watcher,
            "hide_gps": settings.hide_gps, "strip_gps": settings.strip_gps}


# ----- actions ----------------------------------------------------------
# All three write a file into DATA_DIR/control and return. Nothing here calls
# the indexer, even where it happens to be in the same process: the control
# loop is the one thing that starts a scan, so there is one place a scan can
# begin and one place to look when one did not.
def request_scan(album: str | None = None, force: bool = False,
                 by: str = "cli") -> dict:
    """Ask the running indexer for a pass. Raises UnknownAlbum for a name
    that matches nothing, rather than quietly scanning everything.

    `by` rides along into status.json, so "who asked for this scan" is
    answerable after the fact — the CLI and the console are both operators
    and only one of them leaves a shell history."""
    album = norm_album(album)
    if album and not (settings.photos_dir / album).is_dir():
        raise UnknownAlbum(album)
    return control.request_scan(album=album, force=force, by=by)


def pause(reason: str = "", by: str = "cli") -> dict:
    """Suspend indexing. Deliberately PERSISTENT — it survives the restart it
    was very likely set for, and only `resume` lifts it."""
    return control.pause(reason.strip() or None, by=by)


def resume() -> bool:
    """Lift a pause. True when there was one to lift."""
    return control.resume()


def last_scan() -> dict | None:
    return ((control.read_status() or {}).get("last_scan")) or None


def scan_result(request_id: str) -> dict | None:
    """The summary of one requested scan, once the indexer has published it.
    None while it is still running — a poller's "not yet"."""
    last = last_scan()
    if last and last.get("request_id") == request_id:
        return last
    return None


# ----- reports ----------------------------------------------------------
def doctor(album: str | None = None, limit_slow: int = 50,
           progress=None) -> dict:
    """Index, files, derivatives and config, checked against each other.

    The one report that walks everything, so it is the one that needs to say
    where it has got to: `progress` is called with a short label (and, during
    the derivative sweep, a done/total pair). The CLI hands it a spinner; the
    console ignores it and waits for the payload.

    `limit_slow` bounds the only expensive check — opening files that never
    produced a thumbnail, to find out whether they are readable at all.
    """
    def _tick(label, *rest):
        if progress is not None:
            progress(label, *rest)

    def _tick_progress(done, total, label):
        if progress is not None:
            progress(label, done, total)

    c = connect()
    album = norm_album(album)
    _tick("reading the index")
    rows = scope_rows(c, album)
    _tick("walking the photo tree")
    disk = photo_files(album)
    disk_set = set(disk)
    row_by_rel = {r["rel_path"]: r for r in rows}
    problems: dict[str, list] = {}

    def note(check: str, item):
        problems.setdefault(check, []).append(item)

    # --- index vs. filesystem ---
    for rel in row_by_rel:
        if rel not in disk_set:
            note("missing_file", {"rel_path": rel, "detail": "indexed, but the file is gone"})
    for rel in disk:
        if rel not in row_by_rel:
            note("unindexed", {"rel_path": rel, "detail": "on disk, but not in the index"})
        else:
            disk_mtime = effective_mtime(rel)
            stored = row_by_rel[rel]["mtime"]
            if disk_mtime is not None and abs(stored - disk_mtime) >= 1.0:
                note("stale_index", {"rel_path": rel,
                                     "detail": f"index mtime {stamp(stored)} vs file {stamp(disk_mtime)}"})

    # --- derivatives ---
    expected: set[Path] = set()
    for seen, rel in enumerate(disk, 1):
        if seen % 25 == 0 or seen == len(disk):
            _tick_progress(seen, len(disk), "derivatives")
        for kind, path in derivatives(rel).items():
            expected.add(path)
        for kind, state in derivative_state(rel).items():
            if state != "ok" and not (kind == "full" and state == "missing"):
                # a missing `full` is normal: HEIC conversions are built on
                # first request, not up front
                note(f"{state}_{kind}", {"rel_path": rel, "detail": f"{kind} is {state}"})

    derivative_dirs = [settings.thumbs_dir, settings.previews_dir, settings.fulls_dir]
    _tick("looking for orphaned derivatives")
    if album is None:  # orphan sweep only makes sense over the whole tree
        for d in derivative_dirs:
            if not d.is_dir():
                continue
            for f in derivative_files(d):
                # FULLS_DIR sits inside PREVIEWS_DIR by default — don't report
                # its contents twice, or as orphans of the previews tree
                if d is settings.previews_dir and settings.fulls_dir in f.parents:
                    continue
                if f not in expected:
                    note("orphan_derivative", {"rel_path": str(f), "detail": "no photo maps to this file"})

    # --- unreadable sources (bounded: only where a thumb never built) ---
    checked = 0
    for rel in disk:
        if checked >= limit_slow:
            break
        if derivative_state(rel).get("thumb") != "missing":
            continue
        checked += 1
        try:
            with Image.open(settings.photos_dir / rel) as img:
                img.verify()
        except Exception as e:
            note("unreadable", {"rel_path": rel, "detail": f"{type(e).__name__}: {e}"})

    # --- config ---
    _tick("parsing album.cfg files")
    for a in (albums.albums_with_ancestors() if album is None else [album]):
        for issue in checks.album(a):
            note("config", issue)
    if album is None:
        for issue in checks.gallery():
            note("config", issue)

    # --- featured drift ---
    by_photo, _unresolved = featured_map()   # the unresolved half is a config issue above
    flagged = {r["rel_path"] for r in rows if r["is_showcase"]}
    expected_featured = {rel for rel in by_photo if rel in row_by_rel}
    for rel in sorted(expected_featured - flagged):
        note("featured_drift", {"rel_path": rel, "detail": "album.cfg features it, DB flag is 0"})
    for rel in sorted(flagged - expected_featured):
        note("featured_drift", {"rel_path": rel, "detail": "DB flag is 1, no album.cfg entry features it"})

    # --- database ---
    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        note("database", {"rel_path": "-", "detail": f"integrity_check: {integrity}"})
    orphan_tags = c.execute(
        "SELECT COUNT(*) AS n FROM image_tags WHERE image_id NOT IN (SELECT id FROM images)"
    ).fetchone()["n"]
    if orphan_tags:
        note("database", {"rel_path": "-", "detail": f"{orphan_tags} image_tags row(s) without an image"})
    unused_tags = c.execute(
        "SELECT COUNT(*) AS n FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)"
    ).fetchone()["n"]
    if unused_tags:
        note("database", {"rel_path": "-", "detail": f"{unused_tags} tag(s) no longer used by any photo"})

    total = sum(len(v) for v in problems.values())
    return {"scope": album, "photos_on_disk": len(disk), "rows": len(rows),
            "problems": problems, "total": total}
