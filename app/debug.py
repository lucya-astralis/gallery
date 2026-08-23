"""Operator / debug CLI for the gallery backend.

    python -m app.debug <command> [options]
    docker compose exec gallery python -m app.debug <command>

Two kinds of command live in here:

  * Ones that talk to the RUNNING server — `status`, `scan`, `pause`,
    `resume`. They go through the flag-file channel in `DATA_DIR/control`
    (see app/control.py), because the HTTP surface is read-only by design and
    is going to stay that way.
  * Ones that just look at the index, the photo tree and the config the same
    way the app does — `doctor`, `thumbs`, `featured`, `cfg`, `photo`,
    `trip`, `i18n`. Those run standalone and need no server at all; they
    import app.main purely to reuse its resolution helpers, so what they
    report is what the pages actually render.

Every command takes `--json` for a machine-readable dump. `doctor` exits
non-zero when it found something, so it works as a cron / CI check.

Nothing in here writes to `photos/` — the originals stay untouched. The
commands that DO write are marked in their help: they touch the SQLite index
(`scan`, `featured --recompute`) or the generated thumbnail/preview files
(`thumbs --rebuild`, `thumbs --prune --apply`).
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

# Importing the app emits its own startup notes (a missing pillow-heif, say).
# Those would land above the masthead, so they are muted here and reported by
# the dashboard instead — anything a command logs while running still shows.
logging.disable(logging.CRITICAL)
from . import console as ui
from . import control, db, i18n, scanner
from . import main as gallery
logging.disable(logging.NOTSET)

# ----- output helpers ---------------------------------------------------
# The Windows console defaults to cp1252, which cannot print the JP strings
# or the box glyphs below — force UTF-8 rather than crash mid-report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# The report vocabulary lives in app/console.py (colour, rules, meters, the
# masthead); these are just the short names the command bodies use.
out = ui.out
kv = ui.kv
head = ui.head
hint = ui.hint


def dump(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False))


def fail(msg: str) -> int:
    ui.error(f"error: {msg}")
    return 2


def _stamp(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _dur(seconds) -> str:
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


def _ago(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "never"
    return f"{_dur(time.time() - ts)} ago"


def _bytes(n) -> str:
    if not isinstance(n, (int, float)):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ----- shared plumbing --------------------------------------------------
def _connect():
    """Open the index. Every non-control command needs this — the CLI runs
    outside the server process, so db.init() has not run here."""
    return db.init(gallery.DATA_DIR)


def _server_status() -> tuple[dict | None, bool]:
    st = control.read_status()
    return st, control.status_is_live(st)


def _norm_album(raw: str | None) -> str | None:
    """Normalize an album path off the command line and resolve its casing
    against the index, so `Japan_2026/kansai` finds the real folder."""
    if not raw:
        return None
    album = raw.replace("\\", "/").strip().strip("/")
    if not album:
        return None
    if (gallery.PHOTOS_DIR / album).is_dir():
        return album
    resolved = gallery._resolve_album_path(album)
    return resolved or album


def _photo_files(root: str | None = None):
    """Every indexable photo on disk, as rel_path — the same set full_scan
    walks (album folder required, `.album/` metadata skipped)."""
    base = gallery.PHOTOS_DIR / root if root else gallery.PHOTOS_DIR
    if not base.is_dir():
        return []
    found = []
    for file in sorted(base.rglob("*")):
        if not file.is_file() or not scanner.is_image(file):
            continue
        relp = file.relative_to(gallery.PHOTOS_DIR)
        if len(relp.parts) < 2 or scanner.is_meta_path(relp):
            continue
        found.append(relp.as_posix())
    return found


def _effective_mtime(rel: str) -> float | None:
    """The mtime the indexer stores: the photo's, or its `.tags` sidecar's
    when that is newer (see scanner.index_image)."""
    src = gallery.PHOTOS_DIR / rel
    try:
        mtime = src.stat().st_mtime
    except OSError:
        return None
    sidecar = src.with_suffix(src.suffix + ".tags")
    try:
        return max(mtime, sidecar.stat().st_mtime)
    except OSError:
        return mtime


def _derivatives(rel: str) -> dict[str, Path]:
    """Where the generated files for one photo live. `full` only applies to
    formats the browser cannot show (HEIC/HEIF), which are converted on
    demand — see scanner.ensure_full_jpeg."""
    paths = {
        "thumb": (gallery.THUMBS_DIR / rel).with_suffix(".jpg"),
        "preview": (gallery.PREVIEWS_DIR / rel).with_suffix(".jpg"),
    }
    if scanner.needs_jpeg_conversion(gallery.PHOTOS_DIR / rel):
        paths["full"] = (gallery.FULLS_DIR / rel).with_suffix(".jpg")
    return paths


def _derivative_state(rel: str) -> dict[str, str]:
    """ok / missing / stale per derivative, against the source mtime."""
    src_mtime = _effective_mtime(rel)
    state = {}
    for kind, path in _derivatives(rel).items():
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


def _scope_rows(c, album: str | None):
    """Indexed rows, optionally limited to one album subtree. substr() (not
    LIKE) keeps `_`/`%` in album names from acting as wildcards."""
    if album:
        prefix = album + "/"
        return c.execute(
            "SELECT * FROM images WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY rel_path",
            (album, len(prefix), prefix),
        ).fetchall()
    return c.execute("SELECT * FROM images ORDER BY rel_path").fetchall()


def _index_counts(c) -> dict:
    row = c.execute(
        "SELECT COUNT(*) AS images, COUNT(DISTINCT album) AS albums, "
        "SUM(is_showcase) AS featured, SUM(size) AS bytes FROM images"
    ).fetchone()
    tags = c.execute("SELECT COUNT(*) AS n FROM tags").fetchone()["n"]
    db_file = gallery.DATA_DIR / "gallery.db"
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
def _featured_map() -> tuple[dict[str, list[tuple[str, str]]], list[dict]]:
    """Which album.cfg entry featured which photo.

    Mirrors main._recompute_featured, but keeps the provenance instead of
    only the resulting set: returns (rel_path -> [(album, cfg entry)], list
    of entries that matched nothing). The unresolved list is the interesting
    half — a typo in `featured = …` is silent in the app.
    """
    c = db.conn()
    by_photo: dict[str, list[tuple[str, str]]] = {}
    unresolved: list[dict] = []
    for album in gallery._albums_with_ancestors():
        cfg = gallery._album_config(album)
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
            rels = gallery._resolve_photo_refs(album, [item])
            if not rels:
                unresolved.append({"album": album, "entry": item, "reason": "no photo matches"})
            for rel in rels:
                by_photo.setdefault(rel, []).append((album, item))
    return by_photo, unresolved


# ----- config validation ------------------------------------------------
ALBUM_CFG_KEYS = {
    "collection", "cover", "showcase", "featured", "reel", "order", "sort",
    "tags", "effect", "icon", "font", "font_scale",
}
GALLERY_CFG_KEYS = {"welcome", "welcome_desktop", "welcome_mobile", "album_order", "album_sort"}
REEL_VALUES = {"featured", "random", "shuffle", "off", "false", "0", "no", "none"}


def _check_album_cfg(album: str) -> list[dict]:
    """Everything wrong with one album.cfg, as {level, key, detail}. Empty
    list means the file is fine (or absent)."""
    cfg = gallery._album_config(album)
    if not cfg:
        return []
    issues: list[dict] = []

    def add(level, key, detail):
        issues.append({"album": album, "level": level, "key": key, "detail": detail})

    for key in cfg:
        if key not in ALBUM_CFG_KEYS:
            add("error", key, f"unknown key — ignored by the app (known: {', '.join(sorted(ALBUM_CFG_KEYS))})")

    if "cover" in cfg:
        raw = gallery._cfg_first(cfg, "cover")
        if not gallery._config_cover_rel(album, raw):
            add("error", "cover", f"{raw!r} does not resolve to an indexed photo")
    if "featured" in cfg:
        for item in cfg["featured"]:
            item = item.strip()
            if not item or item.lower() in ("*", "all"):
                continue
            if not gallery._resolve_photo_refs(album, [item]):
                add("error", "featured", f"{item!r} matches no photo")
    if "order" in cfg:
        for item in cfg["order"]:
            item = item.strip()
            if item and not gallery._resolve_photo_refs(album, [item]):
                add("warn", "order", f"{item!r} matches no photo")
    if "reel" in cfg:
        val = (gallery._cfg_first(cfg, "reel") or "").strip().lower()
        if val and val not in REEL_VALUES:
            add("error", "reel", f"{val!r} is not featured/random/off")
    if "sort" in cfg:
        val = (gallery._cfg_first(cfg, "sort") or "").strip().lower()
        allowed = set(gallery.SORT_IMAGE_SQL) | {gallery.SORT_CURATED, gallery.SORT_DAYS}
        if val and val not in allowed:
            add("error", "sort", f"{val!r} is not one of {', '.join(sorted(allowed))}")
        elif val == gallery.SORT_CURATED and "order" not in cfg:
            add("warn", "sort", "curated preset without an `order` list — falls back to date_desc")
    if "effect" in cfg:
        val = (gallery._cfg_first(cfg, "effect") or "").strip().lower()
        if val and val not in gallery.ALBUM_EFFECTS:
            add("error", "effect", f"{val!r} is not whitelisted ({', '.join(sorted(gallery.ALBUM_EFFECTS))})")
    if "icon" in cfg:
        raw = gallery._cfg_first(cfg, "icon")
        if gallery._album_icon_file(album) is None:
            add("error", "icon", f"{raw!r} not found in .album/ (or unsupported type)")
    if "font" in cfg:
        raw = gallery._cfg_first(cfg, "font")
        if gallery._album_font_file(album) is None:
            add("error", "font", f"{raw!r} not found in .album/ (or unsupported type)")
    if "font_scale" in cfg:
        if gallery._album_font_scale(album) is None:
            lo, hi = gallery.ALBUM_FONT_SCALE_RANGE
            add("warn", "font_scale", f"ignored — not a number in {lo}–{hi}, or no `font` set")
    return issues


def _check_gallery_cfg() -> list[dict]:
    cfg = gallery._gallery_config()
    if not cfg:
        return []
    issues: list[dict] = []

    def add(level, key, detail):
        issues.append({"album": "(gallery.cfg)", "level": level, "key": key, "detail": detail})

    for key in cfg:
        if key not in GALLERY_CFG_KEYS:
            add("error", key, f"unknown key — ignored (known: {', '.join(sorted(GALLERY_CFG_KEYS))})")
    for key in ("welcome", "welcome_desktop", "welcome_mobile"):
        spec = cfg.get(key, [])
        if len(spec) == 1 and spec[0].lower() in gallery._WELCOME_KEYWORDS:
            continue
        for raw in spec:
            if not gallery._lookup_welcome_image(raw):
                add("error", key, f"{raw!r} does not resolve to an indexed photo — entry is skipped")
    if "album_order" in cfg:
        known = {gallery._album_order_key(n) for n in gallery._all_album_nodes()}
        for item in cfg["album_order"]:
            if item.startswith("#"):
                continue
            if gallery._album_order_key(item) not in known:
                add("warn", "album_order", f"{item!r} matches no album")
    if "album_sort" in cfg:
        val = (gallery._cfg_first(cfg, "album_sort") or "").strip().lower()
        allowed = set(gallery.SORT_ALBUM_SQL) | {gallery.SORT_CURATED}
        if val and val not in allowed:
            add("error", "album_sort", f"{val!r} is not one of {', '.join(sorted(allowed))}")
    return issues


# ----- commands ---------------------------------------------------------
def _render_system(st: dict | None, live: bool, pause: dict | None) -> None:
    """The server / indexer / scan / watcher block, shared by `status` and
    the dashboard."""
    if live:
        # The heartbeat age is shown even when it is fresh: a server that was
        # hard-killed (SIGKILL, OOM) leaves its status file behind, and a
        # growing age here is the first sign of that.
        kv("server", f"{ui.state('running')} · pid {st.get('pid')} · "
                     f"up {_dur(time.time() - (st.get('started_at') or time.time()))} · "
                     f"heartbeat {_ago(st.get('heartbeat'))}")
    elif st:
        kv("server", f"{ui.state('NOT running', 'bad')} · last heartbeat "
                     f"{_ago(st.get('heartbeat'))} (stale status file)")
    else:
        kv("server", f"{ui.state('NOT running', 'idle')} "
                     f"(no status file — never started, or a clean shutdown)")

    if pause:
        since = f" since {_stamp(pause.get('since'))}" if pause.get("since") else ""
        reason = f" · {pause['reason']}" if pause.get("reason") else ""
        kv("indexer", f"{ui.state('PAUSED', 'warn')}{since}{reason}")
    else:
        kv("indexer", ui.state("running"))

    if live:
        if st.get("scanning"):
            kv("scan", f"{ui.state('RUNNING', 'warn')} ({st.get('scan_trigger')}) · "
                       f"started {_ago(st.get('scan_started_at'))}")
        else:
            last = st.get("last_scan") or {}
            if last:
                res = last.get("result") or {}
                bits = ", ".join(f"{k} {res.get(k, 0)}" for k in
                                 ("indexed", "thumbnails", "previews", "removed", "failed"))
                scope = f" [{last['album']}]" if last.get("album") else ""
                err = f" · {ui.state('ERROR ' + str(last['error']), 'bad')}" if last.get("error") else ""
                kv("scan", f"idle · last {last.get('trigger')}{scope} {_ago(last.get('finished_at'))} "
                           f"in {_dur(last.get('seconds'))} → {bits}{err}")
            else:
                kv("scan", "idle · no scan in this process yet")
        w = st.get("watcher") or {}
        queued = w.get("pending", 0)
        kv("watcher", f"{'on' if w.get('enabled') else 'off'} · "
                      f"{ui.state('running') if w.get('running') else ui.state('not running', 'bad')} · "
                      f"{ui.state(f'{queued} event(s) queued', 'warn' if queued else 'idle')}")
        pend = st.get("pending_request")
        if pend:
            kv("queued", f"scan request {pend.get('id')} waiting ({_ago(pend.get('requested_at'))})")



def cmd_status(args) -> int:
    st, live = _server_status()
    pause = control.pause_info()
    c = _connect()
    counts = _index_counts(c)

    if args.json:
        dump({"server": st, "live": live, "paused": pause is not None,
              "pause": pause, "index": counts,
              "control_dir": str(control.control_dir())})
        return 0

    _render_system(st, live, pause)
    kv("index", f"{counts['images']} photos · {counts['albums']} albums · "
                f"{counts['featured']} featured · {counts['tags']} tags · "
                f"{_bytes(counts['bytes'])} of originals · db {_bytes(counts['db_bytes'])}")
    kv("paths", f"photos={gallery.PHOTOS_DIR}")
    kv("", f"thumbs={gallery.THUMBS_DIR}")
    kv("", f"previews={gallery.PREVIEWS_DIR}")
    kv("", f"data={gallery.DATA_DIR}")
    cfg = (st or {}).get("config") or {}
    kv("config", f"scan_interval={cfg.get('scan_interval', gallery.SCAN_INTERVAL)}s · "
                 f"thumb={cfg.get('thumb_size', gallery.THUMB_SIZE)} · "
                 f"preview={cfg.get('preview_size', gallery.PREVIEW_SIZE)} · "
                 f"watcher={'on' if gallery.ENABLE_WATCHER else 'off'} · "
                 f"hide_gps={int(gallery.HIDE_GPS)} · strip_gps={int(gallery.STRIP_GPS)}")
    return 0


def _wait_for_scan(request_id: str, timeout: float) -> dict | None:
    """Poll status.json until the server reports our request as finished."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        st = control.read_status()
        last = (st or {}).get("last_scan") or {}
        if last.get("request_id") == request_id:
            return last
        if not control.status_is_live(st):
            return None
    return None


def _print_scan_result(summary: dict) -> None:
    res = summary.get("result") or {}
    if summary.get("error"):
        out(f"scan finished WITH ERRORS in {_dur(summary.get('seconds'))}: {summary['error']}")
    else:
        out(f"scan finished in {_dur(summary.get('seconds'))}")
    for key in ("indexed", "thumbnails", "previews", "removed", "failed", "total_seen"):
        if key in res:
            out(f"  {key:<12}{res[key]}")
    if res.get("failed"):
        out("  → unreadable files stay in the gallery without a thumbnail; "
            "see `python -m app.debug doctor`")


def cmd_scan(args) -> int:
    album = _norm_album(args.album)
    if album and not (gallery.PHOTOS_DIR / album).is_dir():
        return fail(f"no such album folder: {album}")
    st, live = _server_status()

    if live and not args.local:
        req = control.request_scan(album=album, force=args.force)
        scope = f" of {album}" if album else ""
        if not args.json:
            out(f"scan{scope} requested{' (force)' if args.force else ''} — "
                f"the server picks it up within {control.CONTROL_TICK:.0f}s (id {req['id']})")
        if args.no_wait:
            if args.json:
                dump({"queued": req, "waited": False})
            return 0
        summary = _wait_for_scan(req["id"], args.timeout)
        if summary is None:
            if args.json:
                dump({"queued": req, "waited": True, "result": None,
                      "error": "timeout or server gone"})
            else:
                out(f"gave up waiting after {_dur(args.timeout)} — the scan may still be running; "
                    f"check `python -m app.debug status`")
            return 1
        if args.json:
            dump(summary)
        else:
            _print_scan_result(summary)
        return 1 if summary.get("error") else 0

    if live and args.local:
        print("warning: the server is running — a local scan writes to the same SQLite file "
              "as its own indexer. Prefer plain `scan`, which routes the request to it.",
              file=sys.stderr)
    elif not live and not args.json:
        out("server not running — scanning in this process")

    _connect()
    started = time.time()
    result = scanner.full_scan(
        gallery.PHOTOS_DIR, gallery.THUMBS_DIR, gallery.THUMB_SIZE,
        previews_dir=gallery.PREVIEWS_DIR, preview_size=gallery.PREVIEW_SIZE,
        root=album, force=args.force,
    )
    gallery._recompute_featured()
    summary = {"result": result, "seconds": round(time.time() - started, 3), "error": None}
    if args.json:
        dump(summary)
        return 0
    _print_scan_result(summary)
    return 0


def cmd_pause(args) -> int:
    reason = " ".join(args.reason).strip() if args.reason else ""
    info = control.pause(reason or None)
    _, live = _server_status()
    if args.json:
        dump({"paused": True, "info": info, "server_live": live})
        return 0
    out("indexer paused" + ("" if live else " (server is not running — takes effect at its next start)"))
    out("  periodic scan  off")
    out("  watcher        keeps queueing events, processes them on resume")
    out("  manual scan    still works: `python -m app.debug scan`")
    out("  the pause survives a restart — lift it with `python -m app.debug resume`")
    return 0


def cmd_resume(args) -> int:
    was_paused = control.resume()
    _, live = _server_status()
    if args.scan and live:
        control.request_scan()
    if args.json:
        dump({"was_paused": was_paused, "server_live": live, "scan_requested": bool(args.scan and live)})
        return 0
    out("indexer resumed" if was_paused else "indexer was not paused")
    if live:
        out(f"  queued watcher events are drained within ~{control.CONTROL_TICK:.0f}s")
        if args.scan:
            out("  scan requested")
    else:
        out("  server is not running — indexing starts with it")
    return 0


def cmd_doctor(args) -> int:
    c = _connect()
    album = _norm_album(args.album)
    rows = _scope_rows(c, album)
    disk = _photo_files(album)
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
            disk_mtime = _effective_mtime(rel)
            stored = row_by_rel[rel]["mtime"]
            if disk_mtime is not None and abs(stored - disk_mtime) >= 1.0:
                note("stale_index", {"rel_path": rel,
                                     "detail": f"index mtime {_stamp(stored)} vs file {_stamp(disk_mtime)}"})

    # --- derivatives ---
    expected: set[Path] = set()
    for rel in disk:
        for kind, path in _derivatives(rel).items():
            expected.add(path)
        for kind, state in _derivative_state(rel).items():
            if state != "ok" and not (kind == "full" and state == "missing"):
                # a missing `full` is normal: HEIC conversions are built on
                # first request, not up front
                note(f"{state}_{kind}", {"rel_path": rel, "detail": f"{kind} is {state}"})

    derivative_dirs = [gallery.THUMBS_DIR, gallery.PREVIEWS_DIR, gallery.FULLS_DIR]
    if album is None:  # orphan sweep only makes sense over the whole tree
        for d in derivative_dirs:
            if not d.is_dir():
                continue
            for f in d.rglob("*.jpg"):
                # FULLS_DIR sits inside PREVIEWS_DIR by default — don't report
                # its contents twice, or as orphans of the previews tree
                if d is gallery.PREVIEWS_DIR and gallery.FULLS_DIR in f.parents:
                    continue
                if f not in expected:
                    note("orphan_derivative", {"rel_path": str(f), "detail": "no photo maps to this file"})

    # --- unreadable sources (bounded: only where a thumb never built) ---
    checked = 0
    for rel in disk:
        if checked >= args.limit_slow:
            break
        if _derivative_state(rel).get("thumb") != "missing":
            continue
        checked += 1
        try:
            with Image.open(gallery.PHOTOS_DIR / rel) as img:
                img.verify()
        except Exception as e:
            note("unreadable", {"rel_path": rel, "detail": f"{type(e).__name__}: {e}"})

    # --- config ---
    for a in (gallery._albums_with_ancestors() if album is None else [album]):
        for issue in _check_album_cfg(a):
            note("config", issue)
    if album is None:
        for issue in _check_gallery_cfg():
            note("config", issue)

    # --- featured drift ---
    by_photo, unresolved = _featured_map()
    for item in unresolved:
        note("config", {"album": item["album"], "level": "error", "key": "featured",
                        "detail": f"{item['entry']!r} — {item['reason']}"})
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
    if args.json:
        dump({"scope": album, "photos_on_disk": len(disk), "rows": len(rows),
              "problems": problems, "total": total})
        return 1 if total else 0

    kv("scope", album or "whole gallery")
    kv("on disk", f"{len(disk)} photo(s)")
    kv("indexed", f"{len(rows)} row(s)")
    if not total:
        out()
        out("no problems found")
        return 0
    for check in sorted(problems):
        items = problems[check]
        head(f"{check}  ({len(items)})")
        for item in items[:args.limit]:
            if check == "config":
                out(f"  [{item['level']}] {item['album']} · {item['key']}: {item['detail']}")
            else:
                out(f"  {item['rel_path']}")
                out(f"      {item['detail']}")
        if len(items) > args.limit:
            out(f"  … {len(items) - args.limit} more (--limit {len(items)} to see all, or --json)")
    out()
    out(f"{total} problem(s) found")
    out("hints: stale/missing derivatives → `thumbs --rebuild`, orphans → `thumbs --prune`,")
    out("       unindexed / stale index   → `scan` (add --force to ignore mtimes),")
    out("       featured_drift            → `featured --recompute`")
    return 1


def cmd_thumbs(args) -> int:
    _connect()
    album = _norm_album(args.album)
    disk = _photo_files(album)
    todo: list[tuple[str, str]] = []      # (rel, kind)
    for rel in disk:
        for kind, state in _derivative_state(rel).items():
            if kind == "full":
                continue  # built on demand, never eagerly
            if args.all or state in ("missing", "stale"):
                todo.append((rel, kind))

    expected = {p for rel in disk for p in _derivatives(rel).values()}
    orphans: list[Path] = []
    if album is None:
        for d in (gallery.THUMBS_DIR, gallery.PREVIEWS_DIR, gallery.FULLS_DIR):
            if not d.is_dir():
                continue
            for f in d.rglob("*.jpg"):
                if d is gallery.PREVIEWS_DIR and gallery.FULLS_DIR in f.parents:
                    continue
                if f not in expected:
                    orphans.append(f)

    built = failed = pruned = 0
    if args.rebuild:
        for rel, kind in todo:
            src = gallery.PHOTOS_DIR / rel
            size = gallery.THUMB_SIZE if kind == "thumb" else gallery.PREVIEW_SIZE
            dst = _derivatives(rel)[kind]
            if scanner.make_thumbnail(src, dst, size):
                built += 1
            else:
                failed += 1
                out(f"  failed: {rel} ({kind})")
    if args.prune and args.apply:
        for f in orphans:
            try:
                f.unlink()
                pruned += 1
            except OSError as e:
                out(f"  could not delete {f}: {e}")

    if args.json:
        dump({"scope": album, "photos": len(disk), "to_build": len(todo),
              "built": built, "failed": failed,
              "orphans": [str(p) for p in orphans], "pruned": pruned,
              "applied": bool(args.apply)})
        return 0

    kv("scope", album or "whole gallery")
    kv("photos", str(len(disk)))
    kv("to build", f"{len(todo)} derivative(s)" + (" (--all: rebuilding everything)" if args.all else ""))
    kv("orphans", f"{len(orphans)} generated file(s) without a source photo"
                  if album is None else "not checked (album scope)")
    if args.rebuild:
        kv("built", f"{built} ok, {failed} failed")
    elif todo:
        out()
        out("dry run — add --rebuild to actually generate them")
    if args.prune:
        if args.apply:
            kv("pruned", f"{pruned} file(s) deleted")
        else:
            for f in orphans[:args.limit]:
                out(f"  {f}")
            if len(orphans) > args.limit:
                out(f"  … {len(orphans) - args.limit} more")
            out()
            out("dry run — add --apply to actually delete these")
    return 0


def cmd_featured(args) -> int:
    c = _connect()
    if args.recompute:
        gallery._recompute_featured()
    by_photo, unresolved = _featured_map()
    album = _norm_album(args.album)

    by_album: dict[str, dict[str, list[str]]] = {}
    for rel, sources in by_photo.items():
        for src_album, entry in sources:
            if album and src_album != album and not src_album.startswith(album + "/"):
                continue
            by_album.setdefault(src_album, {}).setdefault(entry, []).append(rel)

    flagged = {r["rel_path"] for r in c.execute("SELECT rel_path FROM images WHERE is_showcase = 1")}
    known = set(r["rel_path"] for r in c.execute("SELECT rel_path FROM images"))
    expected = {rel for rel in by_photo if rel in known}
    drift_missing = sorted(expected - flagged)
    drift_extra = sorted(flagged - expected)

    showcase_albums = [a for a in gallery._albums_with_ancestors() if gallery._album_is_showcase(a)]

    if args.json:
        dump({"albums": {a: {e: sorted(v) for e, v in entries.items()} for a, entries in by_album.items()},
              "showcase_albums": showcase_albums,
              "unresolved": unresolved,
              "db_flagged": len(flagged), "expected": len(expected),
              "drift": {"not_flagged": drift_missing, "flagged_without_rule": drift_extra},
              "recomputed": bool(args.recompute)})
        return 0

    kv("featured", f"{len(expected)} photo(s) from {len(by_album)} album.cfg file(s)")
    kv("db flag", f"{len(flagged)} row(s) with is_showcase = 1")
    kv("showcase", f"{len(showcase_albums)} album(s): {', '.join(showcase_albums) or '—'}")
    if args.recompute:
        out("(flags recomputed before this report)")

    for a in sorted(by_album):
        head(a)
        for entry, rels in sorted(by_album[a].items()):
            out(f"  featured = {entry}")
            for rel in sorted(rels)[:args.limit]:
                mark = " " if rel in flagged else "!"
                out(f"    {mark} {rel}")
            if len(rels) > args.limit:
                out(f"      … {len(rels) - args.limit} more")
    if unresolved:
        head(f"entries matching nothing  ({len(unresolved)})")
        for item in unresolved:
            out(f"  {item['album']} · featured = {item['entry']} — {item['reason']}")
    if drift_missing or drift_extra:
        head("db flag drift")
        for rel in drift_missing[:args.limit]:
            out(f"  ! {rel} — configured, but flag is 0")
        for rel in drift_extra[:args.limit]:
            out(f"  ! {rel} — flag is 1, but nothing features it")
        out()
        out("run `python -m app.debug featured --recompute` (or any scan) to fix the flags")
    return 1 if (unresolved or drift_missing or drift_extra) else 0


def cmd_cfg(args) -> int:
    _connect()
    if args.gallery:
        cfg = gallery._gallery_config()
        path = gallery.PHOTOS_DIR / gallery.GALLERY_CFG_NAME
        issues = _check_gallery_cfg()
        title = "gallery.cfg"
    else:
        if not args.album:
            return fail("give an album path, or --gallery for the gallery-wide config")
        album = _norm_album(args.album)
        cfg = gallery._album_config(album)
        meta = gallery._album_meta_dir(album)
        path = (meta / "album.cfg") if meta else (gallery.PHOTOS_DIR / album / ".album" / "album.cfg")
        issues = _check_album_cfg(album)
        title = f"{album}/.album/album.cfg"

    if args.json:
        dump({"file": str(path), "exists": path.is_file(), "parsed": cfg, "issues": issues})
        return 1 if any(i["level"] == "error" for i in issues) else 0

    kv("file", str(path))
    kv("exists", "yes" if path.is_file() else "NO — the app falls back to defaults")
    if not cfg:
        out()
        out("nothing parsed (no file, or an empty one)")
        return 0
    head("parsed")
    for key in sorted(cfg):
        values = cfg[key]
        shown = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)
        out(f"  {key:<12}{shown}")
    if not args.gallery:
        album = _norm_album(args.album)
        head("resolved")
        out(f"  showcase album  {gallery._album_is_showcase(album)}")
        out(f"  collection      {gallery._album_collection(album)}")
        cover = gallery._album_cover_rel(album)
        out(f"  cover           {cover or '— (no photo found)'}")
        mode, reel = gallery._album_reel(album, cfg)
        out(f"  reel            {mode} ({len(reel)} photo(s))")
        out(f"  tags            {', '.join(gallery._album_tags(album, cfg)) or '—'}")
        for lang in i18n.LANGS:
            desc = gallery._album_description(album, lang)
            out(f"  album_{lang}.md   {'present' if desc else '—'}")
    if issues:
        head(f"issues  ({len(issues)})")
        for item in issues:
            out(f"  [{item['level']}] {item['key']}: {item['detail']}")
        return 1 if any(i["level"] == "error" for i in issues) else 0
    head("issues")
    out("  none")
    return 0


def cmd_photo(args) -> int:
    c = _connect()
    rel = args.rel_path.replace("\\", "/").strip().strip("/")
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if row is None:
        like = c.execute(
            "SELECT rel_path FROM images WHERE lower(rel_path) = lower(?) "
            "OR lower(filename) = lower(?) ORDER BY rel_path LIMIT 10",
            (rel, Path(rel).name),
        ).fetchall()
        if not like:
            return fail(f"not indexed: {rel}")
        out(f"no exact match for {rel!r} — did you mean:")
        for r in like:
            out(f"  {r['rel_path']}")
        return 1
    row = dict(row)
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    tags = [r["name"] for r in c.execute(
        "SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id = t.id "
        "WHERE it.image_id = ? ORDER BY t.name", (row["id"],))]
    by_photo, _ = _featured_map()
    sources = by_photo.get(rel, [])
    src = gallery.PHOTOS_DIR / rel
    disk_mtime = _effective_mtime(rel)
    derivatives = {k: {"path": str(p), "state": _derivative_state(rel).get(k)}
                   for k, p in _derivatives(rel).items()}

    if args.json:
        dump({**{k: v for k, v in row.items() if k != "exif_json"},
              "tags": tags, "exif": exif,
              "featured_by": [{"album": a, "entry": e} for a, e in sources],
              "file_exists": src.exists(), "file_mtime": disk_mtime,
              "derivatives": derivatives})
        return 0

    kv("rel_path", rel)
    kv("album", row["album"])
    kv("file", f"{row['filename']} · {_bytes(row['size'])} · "
               f"{row['width']}×{row['height']}" if row["width"] else row["filename"])
    kv("on disk", "yes" if src.exists() else "NO — the row is stale, run `scan`")
    kv("taken", row["taken_at"] or "— (no EXIF date; sorted by mtime)")
    drift = ""
    if disk_mtime is not None and abs(disk_mtime - row["mtime"]) >= 1.0:
        drift = f"  ← file says {_stamp(disk_mtime)} (stale index)"
    kv("mtime", f"{_stamp(row['mtime'])}{drift}")
    kv("indexed", row["indexed_at"])
    kv("featured", ("yes" if row["is_showcase"] else "no") +
                   (" · " + ", ".join(f"{a} → featured = {e}" for a, e in sources) if sources
                    else " · no album.cfg entry features it"))
    kv("tags", ", ".join(tags) or "—")
    head("derivatives")
    for kind, info in derivatives.items():
        out(f"  {kind:<9}{info['state']:<8}{info['path']}")
    head("urls")
    for name in ("thumb", "preview", "full", "image", "api/photo"):
        out(f"  /{name}/{rel}")
    head(f"exif  ({len(exif)} raw key(s))")
    for label, value in gallery._prettify_exif(exif, i18n.DEFAULT_LANG):
        out(f"  {label:<18}{value}")
    if args.exif:
        head("exif (raw)")
        dump(exif)
    return 0


def cmd_trip(args) -> int:
    _connect()
    if not args.album:
        kv("trips", f"{len(gallery.TRIPS)} configured in app/main.py")
        for key, cfg in gallery.TRIPS.items():
            exists = gallery._album_exists(key)
            out(f"  {key:<16}{cfg.get('title')} · {len(cfg.get('stops', []))} stop(s)"
                f"{'' if exists else '  ← no album with this path!'}")
        out()
        out("a trip attaches to the album whose lower-cased path equals its key")
        return 0
    album = _norm_album(args.album)
    trip = gallery._trip_for_album(album, args.lang)
    if trip is None:
        return fail(f"no trip configured for {album!r} "
                    f"(keys: {', '.join(gallery.TRIPS) or 'none'})")
    if args.json:
        dump(trip)
        return 0
    kv("album", album)
    kv("trip", f"{trip.get('title')} ({trip.get('key')})")
    kv("depart", str(trip.get("depart")))
    head("stops")
    for stop in trip.get("stops", []):
        # `href` is the resolved sub-album link — None when that folder holds
        # no photos, which is exactly what you want to see here
        out(f"  {stop.get('city'):<12}{stop.get('start')} -> {stop.get('end')}")
        out(f"      {stop.get('count', 0)} photo(s) · link {stop.get('href') or 'none (empty folder)'} "
            f"· icon {stop.get('icon') or 'none'}")
    head("raw")
    dump(trip)
    return 0


_JS_LANG_RE = re.compile(r"^  (\w+): \{$")
_JS_KEY_RE = re.compile(r"^    (\w+):")
# `t('album.count')` in a template, `i18n.t(lang, "sort.curated")` in Python
_T_CALL_RE = re.compile(r"""\bt\(\s*['"]([a-z0-9_]+(?:\.[a-z0-9_]+)+)['"]""", re.I)
_PY_T_CALL_RE = re.compile(r"""i18n\.t\(\s*[^,]+,\s*['"]([a-z0-9_]+(?:\.[a-z0-9_]+)+)['"]""", re.I)
# a key built at runtime: t(lang, f"exif.{tag}") — the literal prefix is what
# we can see, so every key in that family counts as used
_KEY_FAMILY_RE = re.compile(r"""['"]([a-z0-9_]+(?:\.[a-z0-9_]+)*\.)\{""", re.I)


def _i18n_sources() -> str:
    """Everything that can reference a translation key: the templates and the
    app modules (minus i18n.py itself, whose table would match everything,
    and this file)."""
    parts = []
    for path in sorted((gallery.BASE_DIR / "templates").glob("*.html")):
        parts.append(path.read_text(encoding="utf-8"))
    for path in sorted(gallery.BASE_DIR.glob("*.py")):
        if path.name in ("i18n.py", "debug.py"):
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _js_ui_strings() -> dict[str, set[str]]:
    """Key sets of the UI_STRINGS blocks in app.js, per language. Parsed by
    indentation rather than by evaluating JS — the block is hand-formatted
    and stays that way."""
    text = (gallery.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("const UI_STRINGS"))
    except StopIteration:
        return {}
    blocks: dict[str, set[str]] = {}
    current = None
    for line in lines[start + 1:]:
        if line.startswith("};"):
            break
        m = _JS_LANG_RE.match(line)
        if m:
            current = m.group(1)
            blocks[current] = set()
            continue
        m = _JS_KEY_RE.match(line)
        if m and current:
            blocks[current].add(m.group(1))
    return blocks


def cmd_i18n(args) -> int:
    problems: dict[str, list[str]] = {}

    def note(kind, detail):
        problems.setdefault(kind, []).append(detail)

    # --- python STRINGS table ---
    for key, value in i18n.STRINGS.items():
        if not isinstance(value, tuple) or len(value) != len(i18n.LANGS):
            note("shape", f"{key}: expected {len(i18n.LANGS)} values, got {len(value)}")
            continue
        for lang, text in zip(i18n.LANGS, value):
            if str(text).strip():
                continue
            if lang == i18n.DEFAULT_LANG:
                note("empty", f"{key} [{lang}] is empty — there is nothing left to fall back to")
            elif key.endswith(("_prefix", "_suffix")):
                # deliberate: a sentence split around a value puts all of the
                # text in one half for a language whose word order differs
                continue
            else:
                note("blank", f"{key} [{lang}] is empty — renders the English text")
        if value[1] == value[0] and value[2] == value[0]:
            note("untranslated", f"{key}: DE and JP are identical to EN")

    # --- referenced vs. defined ---
    blob = _i18n_sources()
    referenced = set(_T_CALL_RE.findall(blob)) | set(_PY_T_CALL_RE.findall(blob))
    for key in sorted(referenced - set(i18n.STRINGS)):
        note("missing", f"{key} is used but not defined in i18n.STRINGS (renders as the key itself)")
    # A key counts as used when it appears as a literal anywhere (a t() call,
    # or a constant table like SORT_IMAGE_OPTIONS), or when its family prefix
    # is built into an f-string.
    used = {key for key in i18n.STRINGS if f'"{key}"' in blob or f"'{key}'" in blob}
    for prefix in set(_KEY_FAMILY_RE.findall(blob)):
        used |= {key for key in i18n.STRINGS if key.startswith(prefix)}
    for key in sorted(set(i18n.STRINGS) - used):
        note("unused", f"{key} is defined but never referenced")

    # --- app.js UI_STRINGS ---
    js = _js_ui_strings()
    if not js:
        note("js", "could not find the UI_STRINGS block in app.js")
    else:
        base = js.get("en", set())
        for lang, keys in js.items():
            for key in sorted(base - keys):
                note("js", f"UI_STRINGS.{lang} is missing {key!r}")
            for key in sorted(keys - base):
                note("js", f"UI_STRINGS.{lang} has {key!r}, which en does not")

    total = sum(len(v) for v in problems.values())
    # What actually breaks a page vs. what is only worth knowing.
    hard = sum(len(problems.get(k, [])) for k in ("shape", "empty", "missing", "js"))
    if args.json:
        dump({"languages": list(i18n.LANGS), "keys": len(i18n.STRINGS),
              "js_languages": {k: len(v) for k, v in js.items()},
              "problems": problems, "total": total})
        return 1 if hard else 0

    kv("table", f"{len(i18n.STRINGS)} key(s) × {len(i18n.LANGS)} language(s) in app/i18n.py")
    kv("app.js", " · ".join(f"{k}:{len(v)}" for k, v in js.items()) or "—")
    kv("used", f"{len(used)} of them referenced in templates / app code")
    for kind in ("shape", "empty", "missing", "js", "blank", "untranslated", "unused"):
        items = problems.get(kind)
        if not items:
            continue
        head(f"{kind}  ({len(items)})")
        for detail in items[:args.limit]:
            out(f"  {detail}")
        if len(items) > args.limit:
            out(f"  … {len(items) - args.limit} more")
    out()
    if not total:
        out("no problems found")
    elif not hard:
        out(f"{total} note(s), nothing broken (blank/untranslated/unused are informational)")
    else:
        out(f"{hard} problem(s) that affect rendering")
        out("reminder: new Japanese glyphs need `python tools/build_jp_subset.py`")
    return 1 if hard else 0


# ----- dashboard / menu / help -----------------------------------------
# `python -m app.debug` with no arguments lands here: the masthead, what the
# server is doing, and what the archive currently holds. On a terminal it
# then drops into the menu; piped or redirected it just prints and exits.
SUBTITLE_FMT = "{title}  ·  OPS CONSOLE  ·  API v{api}"
# Above this many rows the dashboard skips the two directory walks (health +
# cache size) instead of making you wait for them.
QUICK_CHECK_MAX_ROWS = 20000


def _top_albums(c, limit: int = 6):
    return c.execute(
        "SELECT album, COUNT(*) AS n, SUM(size) AS bytes FROM images "
        "GROUP BY album ORDER BY n DESC, album ASC LIMIT ?", (limit,)).fetchall()


def _months(c, limit: int = 12):
    """Shots per capture month, oldest first — the archive's pulse."""
    rows = c.execute(
        "SELECT substr(taken_at, 1, 7) AS ym, COUNT(*) AS n FROM images "
        "WHERE taken_at IS NOT NULL GROUP BY ym ORDER BY ym DESC LIMIT ?",
        (limit,)).fetchall()
    return list(reversed(rows))


def _formats(c):
    counts: dict[str, int] = {}
    for r in c.execute("SELECT filename FROM images"):
        ext = r["filename"].rsplit(".", 1)[-1].lower() if "." in r["filename"] else "?"
        counts[ext] = counts.get(ext, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _dir_stats(path):
    files = total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                files += 1
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return files, total


def cmd_dash(args) -> int:
    c = _connect()
    st, live = _server_status()
    pause = control.pause_info()
    counts = _index_counts(c)
    span = c.execute(
        "SELECT MIN(taken_at) AS a, MAX(taken_at) AS b FROM images "
        "WHERE taken_at IS NOT NULL").fetchone()

    if args.json:
        dump({"index": counts, "span": {"from": span["a"], "to": span["b"]},
              "albums": [dict(r) for r in _top_albums(c, 12)],
              "months": [dict(r) for r in _months(c)],
              "formats": _formats(c),
              "server": st, "live": live, "paused": pause is not None})
        return 0

    ui.logo(SUBTITLE_FMT.format(title=gallery.app.title.upper(), api=gallery.API_VERSION))

    ui.rule("system")
    _render_system(st, live, pause)

    ui.head("archive")
    kv("photos", f"{counts['images']:,}".replace(",", " "))
    kv("albums", f"{counts['albums']} with photos · "
                 f"{len(gallery._all_album_nodes())} incl. parents")
    kv("featured", f"{counts['featured']} photo(s) · "
                   f"{sum(1 for a in gallery._albums_with_ancestors() if gallery._album_is_showcase(a))} showcase album(s)")
    kv("tags", str(counts["tags"]))
    kv("originals", _bytes(counts["bytes"]))
    kv("span", f"{(span['a'] or '—')[:10]} → {(span['b'] or '—')[:10]}")
    kv("database", _bytes(counts["db_bytes"]))

    albums = _top_albums(c)
    if albums:
        ui.head("largest albums")
        peak = albums[0]["n"]
        name_w = min(28, max(len(r["album"]) for r in albums))
        for r in albums:
            name = r["album"]
            if len(name) > name_w:
                name = "…" + name[-(name_w - 1):]
            print(f"  {name:<{name_w}}  {ui.bar(r['n'], peak, 22)} "
                  f"{ui.C.bold}{r['n']:>5}{ui.C.off} {ui.C.gy}{_bytes(r['bytes'])}{ui.C.off}")

    months = _months(c)
    if months:
        ui.head("activity (by capture month)")
        peak = max(r["n"] for r in months)
        for r in months:
            print(f"  {r['ym']}   {ui.bar(r['n'], peak, 30, ui.C.mg)} "
                  f"{ui.C.bold}{r['n']:>5}{ui.C.off}")

    formats = _formats(c)
    if formats:
        ui.head("formats")
        kv("types", " · ".join(f"{ext} {n}" for ext, n in formats[:6]))
    kv("heic/heif", ui.state("supported") if scanner.HEIF_SUPPORTED
       else ui.state("NOT supported — pillow-heif is missing", "warn"))

    ui.head("health")
    if counts["images"] <= QUICK_CHECK_MAX_ROWS:
        on_disk = len(_photo_files())
        thumbs, thumb_bytes = _dir_stats(gallery.THUMBS_DIR)
        previews, preview_bytes = _dir_stats(gallery.PREVIEWS_DIR)
        drift = on_disk - counts["images"]
        if drift == 0:
            kv("index", f"{ui.state('in sync')} · {on_disk} file(s) on disk = {counts['images']} row(s)")
        else:
            what = "not indexed" if drift > 0 else "indexed but gone"
            kv("index", f"{ui.state(f'{abs(drift)} file(s) {what}', 'warn')}"
                        f" · {on_disk} on disk / {counts['images']} indexed")
        kv("cache", f"{thumbs} thumb(s) {_bytes(thumb_bytes)} · "
                    f"{previews} preview(s) {_bytes(preview_bytes)}")
        hint("  a full check (config, derivatives, drift) is `doctor`")
    else:
        hint(f"  skipped — over {QUICK_CHECK_MAX_ROWS} rows; run `doctor` for the full check")

    print()
    ui.rule("commands")
    _command_columns()
    print()
    hint("  python -m app.debug <command> --help   ·   `menu` for the interactive console")
    return 0


def _command_columns() -> None:
    ui.columns([(name, desc) for name, desc, _ in MENU_ITEMS if name])


# (key, command, prompts) — the prompts describe how the menu collects the
# arguments a command takes: ("arg", …) optional positional, ("arg!", …)
# required, ("opt", "--flag", …) optional value, ("flag", "--flag", …) yes/no.
MENU_ITEMS = [
    ("status", "server, indexer, last scan, watcher queue", []),
    ("scan", "index now (optionally one album, --force)",
     [("arg", "album", "album (blank = whole gallery)"),
      ("flag", "--force", "ignore mtimes and re-index everything? [y/N]")]),
    ("pause", "suspend indexing", [("arg", "reason", "reason (blank = none)")]),
    ("resume", "lift the pause", [("flag", "--scan", "scan right away? [y/N]")]),
    ("doctor", "integrity check: index, files, derivatives, config",
     [("opt", "--album", "album (blank = whole gallery)")]),
    ("thumbs", "inspect / rebuild / prune derivatives",
     [("opt", "--album", "album (blank = whole gallery)"),
      ("flag", "--rebuild", "rebuild missing and stale ones? [y/N]"),
      ("flag", "--prune", "list generated files with no source photo? [y/N]")]),
    ("featured", "which album.cfg entry features which photo",
     [("arg", "album", "album (blank = all)")]),
    ("cfg", "album.cfg / gallery.cfg as the app parses it",
     [("arg", "album", "album (blank = gallery.cfg)")]),
    ("photo", "one photo in full", [("arg!", "rel_path", "rel_path")]),
    ("trip", "trip dashboard", [("arg", "album", "album (blank = list trips)")]),
    ("i18n", "EN/DE/JP completeness + app.js mirror", []),
    ("dash", "redraw this dashboard", []),
]


def _ask(prompt: str) -> str:
    try:
        return input(f"{ui.C.cy}  {prompt}: {ui.C.off}").strip()
    except EOFError:
        return ""


def _menu_argv(command: str, prompts) -> list[str] | None:
    """Collect one command's arguments interactively. None = user backed out."""
    argv = [command]
    for kind, name, prompt in prompts:
        if kind == "flag":
            if _ask(prompt).lower().startswith("y"):
                argv.append(name)
            continue
        value = _ask(prompt)
        if not value:
            if kind == "arg!":
                ui.warn("  needs a value — cancelled")
                return None
            if command == "cfg" and kind == "arg":
                argv.append("--gallery")  # blank album = the gallery-wide file
            continue
        if kind == "opt":
            argv += [name, value]
        else:
            argv.append(value)
    return argv


def cmd_menu(args) -> int:
    """Interactive console. Refuses to run without a terminal — a prompt that
    nobody can answer would just hang a pipe or a cron job."""
    if not ui.is_tty():
        ui.warn("no terminal attached — printing the command overview instead")
        _command_columns()
        return 1
    while True:
        print()
        ui.rule("menu")
        ui.columns([(str(i), f"{ui.C.bold}{name:<9}{ui.C.off}{ui.C.gy}{desc}{ui.C.off}")
                    for i, (name, desc, _) in enumerate(MENU_ITEMS, 1)])
        ui.columns([("q", "quit")])
        choice = _ask("select").lower()
        if choice in ("q", "quit", "exit"):
            return 0
        item = None
        if choice.isdigit() and 1 <= int(choice) <= len(MENU_ITEMS):
            item = MENU_ITEMS[int(choice) - 1]
        else:
            item = next((m for m in MENU_ITEMS if m[0] == choice), None)
        if item is None:
            ui.warn("  no such entry")
            continue
        argv = _menu_argv(item[0], item[2])
        if argv is None:
            continue
        print()
        try:
            main(argv)
        except SystemExit:
            pass  # argparse complained; it already said why
        except Exception as e:  # a broken command must not kill the console
            ui.error(f"  {type(e).__name__}: {e}")
        print()
        hint("  ── enter to return to the menu ──")
        try:
            input()
        except EOFError:
            return 0


def cmd_help(args) -> int:
    ui.logo(SUBTITLE_FMT.format(title=gallery.app.title.upper(), api=gallery.API_VERSION))
    ui.rule("usage")
    ui.columns([
        ("python -m app.debug", "dashboard, then the interactive menu"),
        ("python -m app.debug <cmd>", "run one command"),
        ("… <cmd> --help", "options of that command"),
        ("… <cmd> --json", "machine-readable output"),
    ])
    ui.head("commands")
    _command_columns()
    ui.head("control channel")
    hint(f"  the server is driven through flag files in {control.control_dir()}")
    hint("  status.json (server) · paused.json + scan.request.json (this CLI)")
    hint("  there is no debug HTTP endpoint — the web surface stays read-only")
    print()
    return 0


def cmd_home() -> int:
    """No arguments: dashboard, and the menu when someone is watching."""
    args = argparse.Namespace(json=False, no_color=False)
    cmd_dash(args)
    if ui.is_tty():
        return cmd_menu(args)
    return 0


# ----- argument parsing -------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.debug",
        description="Operator / debug CLI for the gallery backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run without arguments for the dashboard and the interactive menu.\n"
               "Server control (status/scan/pause/resume) goes through the flag files in\n"
               "DATA_DIR/control — there is no debug HTTP endpoint, by design.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_text, **kwargs):
        sp = sub.add_parser(name, help=help_text, description=help_text, **kwargs)
        sp.set_defaults(func=func)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--no-color", action="store_true", help="plain output, no ANSI")
        return sp

    add("dash", cmd_dash, "Masthead, live state and archive statistics on one screen.")
    add("menu", cmd_menu, "Interactive console (needs a terminal).")
    add("help", cmd_help, "Command overview with the usage cheat sheet.")

    add("status", cmd_status, "Live state: server, pause, last scan, watcher queue, index counters.")

    sp = add("scan", cmd_scan, "Run an indexing pass now (writes the index and builds derivatives).")
    sp.add_argument("album", nargs="?", help="limit the scan to one album subtree")
    sp.add_argument("--force", action="store_true",
                    help="re-index and re-derive even when mtimes say nothing changed")
    sp.add_argument("--local", action="store_true",
                    help="scan in this process instead of asking the server")
    sp.add_argument("--no-wait", action="store_true", help="queue the request and return")
    sp.add_argument("--timeout", type=float, default=900.0,
                    help="seconds to wait for the server to finish (default: 900)")

    sp = add("pause", cmd_pause, "Suspend indexing: no periodic scan, watcher events queue up.")
    sp.add_argument("reason", nargs="*", help="free text, shown in `status`")

    sp = add("resume", cmd_resume, "Lift the pause.")
    sp.add_argument("--scan", action="store_true", help="request a scan right away")

    sp = add("doctor", cmd_doctor, "Check index, files, derivatives and config for drift. "
                                   "Exits 1 when something was found.")
    sp.add_argument("--album", help="limit the check to one album subtree")
    sp.add_argument("--limit", type=int, default=10, help="examples per finding (default: 10)")
    sp.add_argument("--limit-slow", type=int, default=50,
                    help="how many thumb-less files to open for a readability test (default: 50)")

    sp = add("thumbs", cmd_thumbs, "Inspect, rebuild or prune generated thumbnails and previews.")
    sp.add_argument("--album", help="limit to one album subtree")
    sp.add_argument("--rebuild", action="store_true", help="build missing / stale derivatives")
    sp.add_argument("--all", action="store_true", help="with --rebuild: rebuild every derivative")
    sp.add_argument("--prune", action="store_true", help="list generated files with no source photo")
    sp.add_argument("--apply", action="store_true", help="with --prune: actually delete them")
    sp.add_argument("--limit", type=int, default=20, help="examples to print (default: 20)")

    sp = add("featured", cmd_featured, "Which album.cfg entry featured which photo, plus DB drift.")
    sp.add_argument("album", nargs="?", help="limit to one album subtree")
    sp.add_argument("--recompute", action="store_true", help="rewrite the is_showcase flags first")
    sp.add_argument("--limit", type=int, default=10, help="photos per entry (default: 10)")

    sp = add("cfg", cmd_cfg, "Show an album.cfg / gallery.cfg exactly as the app parses it.")
    sp.add_argument("album", nargs="?", help="album path")
    sp.add_argument("--gallery", action="store_true", help="the gallery-wide gallery.cfg instead")

    sp = add("photo", cmd_photo, "Everything the app knows about one photo.")
    sp.add_argument("rel_path", help="path relative to photos/, e.g. japan_2026/kansai/IMG.png")
    sp.add_argument("--exif", action="store_true", help="also dump the raw EXIF block")

    sp = add("trip", cmd_trip, "Resolved trip dashboard (stops, dates, albums) for an album.")
    sp.add_argument("album", nargs="?", help="album path; omit to list configured trips")
    sp.add_argument("--lang", default=i18n.DEFAULT_LANG, choices=list(i18n.LANGS),
                    help="language for the human-readable date labels")

    sp = add("i18n", cmd_i18n, "Check EN/DE/JP completeness and the app.js UI_STRINGS mirror.")
    sp.add_argument("--limit", type=int, default=20, help="findings per group (default: 20)")

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        ui.init_color(None)
        try:
            return cmd_home()
        except KeyboardInterrupt:
            print()
            return 130
    args = build_parser().parse_args(argv)
    # JSON never carries escape codes, and --no-color is the manual override.
    ui.init_color(False if (args.json or args.no_color) else None)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print()
        return 130
    except BrokenPipeError:  # `| head`
        return 0


if __name__ == "__main__":
    sys.exit(main())
