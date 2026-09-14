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

    cli/            renders these payloads to a terminal
    console/opsapi.py  serves them over HTTP

The reports that only look (a cfg, a photo, the tags, the export…) live next
door in `aperture/reports.py`; this module keeps the live state, `doctor`, and
everything that ends in a write — the scan, the pause and the jobs.

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

import os
import shutil
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
    for file in scanner.walk_photo_tree(base):
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


# ----- disk -------------------------------------------------------------
def _tree_usage(root: Path, skip: Path | None = None) -> dict:
    """Files and bytes under one directory, split by extension. os.walk and
    DirEntry.stat rather than rglob: on a tree of tens of thousands of
    thumbnails the difference is the scandir cache."""
    files = total = 0
    formats: dict[str, dict] = {}
    if not root.is_dir():
        return {"files": 0, "bytes": 0, "formats": formats}
    skip_s = os.path.normcase(str(skip)) if skip else None
    for dirpath, dirnames, _ in os.walk(root):
        if skip_s:
            dirnames[:] = [d for d in dirnames
                           if os.path.normcase(os.path.join(dirpath, d)) != skip_s]
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            files += 1
            total += size
            ext = os.path.splitext(entry.name)[1].lower() or "(none)"
            slot = formats.setdefault(ext, {"files": 0, "bytes": 0})
            slot["files"] += 1
            slot["bytes"] += size
    return {"files": files, "bytes": total, "formats": formats}


def disk_usage() -> dict:
    """What the generated trees cost on disk, next to what they are made from.

    Walks the thumbnail, preview and HEIC-conversion trees (the last sits
    inside the previews by default and is counted once, as its own tier), and
    asks each volume involved how full it is. Formats are split out because a
    tier that changed format keeps its old files until `thumbs --prune --apply`
    — which is exactly the kind of space this is here to find.
    """
    started = time.monotonic()
    fulls_inside = settings.previews_dir in settings.fulls_dir.parents
    specs = (
        ("thumbnails", settings.thumbs_dir, None, scanner.THUMB_EXT),
        ("previews", settings.previews_dir,
         settings.fulls_dir if fulls_inside else None, scanner.PREVIEW_EXT),
        ("fulls", settings.fulls_dir, None, ".jpg"),
    )
    counts = index_counts(db.conn())
    photos = counts["images"]
    tiers = []
    for key, path, skip, current in specs:
        usage = _tree_usage(path, skip)
        tiers.append({
            "key": key, "path": str(path), "exists": path.is_dir(),
            "format": current, **usage,
            # files in a format this tier no longer writes: prune leftovers
            "stale_formats": sorted(ext for ext in usage["formats"]
                                    if ext != current and ext in scanner.DERIVATIVE_EXTS),
            "per_photo": round(usage["bytes"] / photos) if photos else None,
        })
    derived = sum(t["bytes"] for t in tiers)

    volumes: dict = {}
    for label, path in [("photos", settings.photos_dir), ("data", settings.data_dir)] + \
                       [(t["key"], Path(t["path"])) for t in tiers]:
        try:
            dev = path.stat().st_dev
            if dev not in volumes:
                total, used, free = shutil.disk_usage(path)
                volumes[dev] = {"path": str(path), "total": total, "used": used,
                                "free": free, "holds": []}
            volumes[dev]["holds"].append(label)
        except OSError:
            continue

    return {
        "tiers": tiers,
        "derivatives": {"files": sum(t["files"] for t in tiers), "bytes": derived},
        "originals": {"files": photos, "bytes": counts["bytes"]},
        "database": {"bytes": counts["db_bytes"]},
        # how much of the originals' size the generated trees add on top
        "ratio": round(derived / counts["bytes"], 4) if counts["bytes"] else None,
        "volumes": list(volumes.values()),
        "took_ms": int((time.monotonic() - started) * 1000),
    }


# ----- derivatives --------------------------------------------------------
def orphan_derivatives(disk: list[str] | None = None) -> list[Path]:
    """Generated files no photo maps to any more, over the whole tree. Only
    ever asked for the whole gallery: in an album scope every other album's
    files would look orphaned."""
    disk = photo_files() if disk is None else disk
    expected = {p for rel in disk for p in derivatives(rel).values()}
    orphans: list[Path] = []
    for d in (settings.thumbs_dir, settings.previews_dir, settings.fulls_dir):
        if not d.is_dir():
            continue
        for f in derivative_files(d):
            # FULLS_DIR sits inside PREVIEWS_DIR by default — don't report its
            # contents twice, or as orphans of the previews tree
            if d is settings.previews_dir and settings.fulls_dir in f.parents:
                continue
            if f not in expected:
                orphans.append(f)
    return orphans


def _derivative_todo(disk: list[str], rebuild_all: bool) -> list[dict]:
    todo = []
    for rel in disk:
        for kind, state in derivative_state(rel).items():
            if kind == "full":
                continue  # built on demand, never eagerly
            if rebuild_all or state in ("missing", "stale"):
                todo.append({"rel_path": rel, "kind": kind, "state": state})
    return todo


def derivatives_report(album: str | None = None, rebuild_all: bool = False) -> dict:
    """`thumbs` without flags: what is missing or stale, and what is left
    over. Reads only."""
    album = norm_album(album)
    disk = photo_files(album)
    todo = _derivative_todo(disk, rebuild_all)
    orphans = orphan_derivatives(disk) if album is None else []
    by_state: dict[str, int] = {}
    for item in todo:
        if item["state"] != "ok":
            by_state[f"{item['state']}_{item['kind']}"] = by_state.get(f"{item['state']}_{item['kind']}", 0) + 1
    orphan_bytes = 0
    for f in orphans:
        try:
            orphan_bytes += f.stat().st_size
        except OSError:
            pass
    return {"scope": album, "photos": len(disk), "to_build": len(todo),
            "all": bool(rebuild_all), "by_state": by_state,
            "pending": [i for i in todo if i["state"] != "ok"],
            "orphans": [str(p) for p in orphans], "orphan_bytes": orphan_bytes,
            "orphans_checked": album is None}


def rebuild_derivatives(album: str | None = None, rebuild_all: bool = False,
                        progress=None) -> dict:
    """`thumbs --rebuild`: build every missing or stale thumbnail and preview
    in scope (with `rebuild_all`, every one)."""
    album = norm_album(album)
    disk = photo_files(album)
    todo = _derivative_todo(disk, rebuild_all)
    built = failed = 0
    broken: list[str] = []
    for done, item in enumerate(todo, 1):
        rel, kind = item["rel_path"], item["kind"]
        size = settings.thumb_size if kind == "thumb" else settings.preview_size
        if scanner.make_thumbnail(settings.photos_dir / rel, derivatives(rel)[kind], size):
            built += 1
        else:
            failed += 1
            broken.append(f"{rel} ({kind})")
        if progress is not None:
            # the name trails the meter, so a slow share shows where it is
            progress(Path(rel).name, done, len(todo))
    return {"scope": album, "photos": len(disk), "to_build": len(todo),
            "all": bool(rebuild_all), "built": built, "failed": failed, "broken": broken}


def prune_derivatives(progress=None) -> dict:
    """`thumbs --prune --apply`: delete the generated files no photo maps to."""
    orphans = orphan_derivatives()
    pruned = freed = 0
    errors: list[str] = []
    for done, f in enumerate(orphans, 1):
        try:
            size = f.stat().st_size
            f.unlink()
            pruned += 1
            freed += size
        except OSError as e:
            errors.append(f"{f}: {e}")
        if progress is not None and (done % 25 == 0 or done == len(orphans)):
            progress("deleting orphans", done, len(orphans))
    return {"orphans": len(orphans), "pruned": pruned, "freed_bytes": freed,
            "errors": errors}


# ----- gps --------------------------------------------------------------
def gps_audit(album: str | None = None, strip: bool = False, progress=None) -> dict:
    """Which originals still carry coordinates — and, with `strip`, remove
    them in place. The only thing in the operations surface that rewrites a
    photograph, which is why it is a job the indexer runs and never a route."""
    album = norm_album(album)
    base = (settings.photos_dir / album) if album else settings.photos_dir
    files = [p for p in scanner.walk_photo_tree(base)
             if p.is_file() and schema.is_image(p)
             and not scanner.is_meta_path(p.relative_to(settings.photos_dir))] \
        if base.is_dir() else []
    carrying: list[str] = []
    stripped: list[str] = []
    unreadable: list[str] = []
    for seen, path in enumerate(files, 1):
        if progress is not None and (seen % 25 == 0 or seen == len(files)):
            progress("reading EXIF", seen, len(files))
        rel = path.relative_to(settings.photos_dir).as_posix()
        try:
            with Image.open(path) as img:
                has = scanner._has_gps(img.getexif())
        except Exception as exc:
            unreadable.append(f"{rel} — {exc}")
            continue
        if not has:
            continue
        carrying.append(rel)
        if strip and scanner.strip_gps_inplace(path):
            stripped.append(rel)
    return {"album": album, "checked": len(files),
            "with_gps": carrying, "stripped": stripped,
            "unreadable": unreadable,
            "settings": {"hide_gps": bool(settings.hide_gps),
                         "strip_gps": bool(settings.strip_gps)}}


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


def featured_report(album: str | None = None) -> dict:
    """`featured`: which entry features which photo, per album.cfg, and where
    the DB flags have drifted from that."""
    c = db.conn()
    by_photo, unresolved = featured_map()
    album = norm_album(album)
    by_album: dict[str, dict[str, list[str]]] = {}
    for rel, sources in by_photo.items():
        for src_album, entry in sources:
            if album and src_album != album and not src_album.startswith(album + "/"):
                continue
            by_album.setdefault(src_album, {}).setdefault(entry, []).append(rel)
    flagged = {r["rel_path"] for r in c.execute("SELECT rel_path FROM images WHERE is_showcase = 1")}
    known = {r["rel_path"] for r in c.execute("SELECT rel_path FROM images")}
    expected = {rel for rel in by_photo if rel in known}
    return {"albums": {a: {e: sorted(v) for e, v in entries.items()}
                       for a, entries in by_album.items()},
            "showcase_albums": [a for a in albums.albums_with_ancestors()
                                if albums.album_is_showcase(a)],
            "unresolved": unresolved,
            "flagged": sorted(flagged),
            "db_flagged": len(flagged), "expected": len(expected),
            "drift": {"not_flagged": sorted(expected - flagged),
                      "flagged_without_rule": sorted(flagged - expected)}}


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
    counts = index_counts(db.conn())
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


# ----- jobs -------------------------------------------------------------
# The writes that are not a scan, by name. The console and the CLI both queue
# them on the control channel and the indexer runs them through run_job below,
# so the process that owns the index is still the only one that writes it —
# and the derivative trees, and (for gps_strip) the originals.
JOBS = {
    "rebuild": "build missing and stale thumbnails and previews (`all`: every one)",
    "prune": "delete generated files no photo maps to any more",
    "featured": "rewrite the featured flags from the album.cfg files",
    "gps_strip": "remove the GPS block from every original that has one — rewrites the photos",
}


def request_job(kind: str, album: str | None = None, rebuild_all: bool = False,
                by: str = "cli") -> dict:
    """Queue one job. Raises ValueError for a kind that does not exist and
    UnknownAlbum for a scope that matches nothing."""
    if kind not in JOBS:
        raise ValueError(f"no such job: {kind!r}")
    params: dict = {}
    if kind in ("rebuild", "gps_strip"):
        album = norm_album(album)
        if album and not (settings.photos_dir / album).is_dir():
            raise UnknownAlbum(album)
        params["album"] = album
    if kind == "rebuild":
        params["all"] = bool(rebuild_all)
    return control.request_job(kind, params, by=by)


def run_job(kind: str, params: dict, progress=None) -> dict:
    """What a job does. Called by the indexer (indexer.run_job) with the scan
    lock held; never by a front end."""
    if kind == "rebuild":
        return rebuild_derivatives(params.get("album"), bool(params.get("all")), progress)
    if kind == "prune":
        return prune_derivatives(progress)
    if kind == "featured":
        if progress is not None:
            progress("recomputing featured flags")
        albums.recompute_featured()
        report = featured_report()
        return {"db_flagged": report["db_flagged"], "expected": report["expected"],
                "unresolved": len(report["unresolved"])}
    if kind == "gps_strip":
        result = gps_audit(params.get("album"), strip=True, progress=progress)
        # the rewritten files have a new mtime, so a plain scan re-reads them
        result["scan_requested"] = bool(result["stripped"])
        if result["stripped"]:
            control.request_scan(album=result["album"], by="job")
        return result
    raise ValueError(f"no such job: {kind!r}")


def job(job_id: str) -> dict:
    """Where one job is: queued, running (with its progress), or done."""
    st = control.read_status() or {}
    running = st.get("job") if (st.get("job") or {}).get("id") == job_id else None
    return {"id": job_id, "result": control.job_result(job_id),
            "pending": control.job_is_pending(job_id), "running": running}


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

    c = db.conn()
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
    for seen, rel in enumerate(disk, 1):
        if seen % 25 == 0 or seen == len(disk):
            _tick_progress(seen, len(disk), "derivatives")
        for kind, state in derivative_state(rel).items():
            if state != "ok" and not (kind == "full" and state == "missing"):
                # a missing `full` is normal: HEIC conversions are built on
                # first request, not up front
                note(f"{state}_{kind}", {"rel_path": rel, "detail": f"{kind} is {state}"})

    _tick("looking for orphaned derivatives")
    if album is None:  # orphan sweep only makes sense over the whole tree
        for f in orphan_derivatives(disk):
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
        for issue in checks.gallery() + checks.pretty_links():
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
