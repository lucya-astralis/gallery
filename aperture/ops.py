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

from . import control, db, i18n, scanner
from . import main as gallery


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
    return db.init(gallery.DATA_DIR)


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
    if (gallery.PHOTOS_DIR / album).is_dir():
        return album
    resolved = gallery._resolve_album_path(album)
    if not resolved:
        raise UnknownAlbum(album)
    return resolved


def photo_files(root: str | None = None):
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


def effective_mtime(rel: str) -> float | None:
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
        "thumb": (gallery.THUMBS_DIR / rel).with_suffix(scanner.THUMB_EXT),
        "preview": (gallery.PREVIEWS_DIR / rel).with_suffix(scanner.PREVIEW_EXT),
    }
    if scanner.needs_jpeg_conversion(gallery.PHOTOS_DIR / rel):
        paths["full"] = (gallery.FULLS_DIR / rel).with_suffix(".jpg")
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
# What counts as a KNOWN key is the gallery's own business, so both sets come
# from there (main.ALBUM_CFG_KEYS / main.GALLERY_CFG_KEYS, declared next to the
# comment block that documents them). This file used to keep its own copy and
# it went stale — `name` was never added, so doctor reported every album that
# set a display name as an error.
ALBUM_CFG_KEYS = gallery.ALBUM_CFG_KEYS
GALLERY_CFG_KEYS = gallery.GALLERY_CFG_KEYS

# gallery.cfg keys naming a file in photos/.gallery/. Checked the same way
# and for the same reason as an album's `icon`: a typo here is silent at
# runtime — the slot simply falls back or disappears.
BRAND_ASSET_KEYS = ("logo", "favicon", "operator_pfp")
BRAND_URL_KEYS = ("operator_url", "privacy_url", "imprint_url")
# Both files carry the knobs with identical rules, so the range check that
# reads this lives in one place (check_wallpaper_knobs).
WALLPAPER_KNOBS = (("wallpaper_tint", (0.0, 1.0), "0–1 or off"),
                   ("wallpaper_dim", (0.25, 1.0), "0.25–1 or off"))
REEL_VALUES = {"featured", "random", "shuffle", "off", "false", "0", "no", "none"}


def wallpaper_line(album: str, variant: str) -> str:
    """The backdrop this album actually shows, named through the tiers it is
    resolved by: `file.jpg` for its own, `file.jpg (from japan_2026)` for an
    inherited one, `file.jpg (gallery.cfg)` when the site's own is what shows,
    and `— (shipped default)` when nothing anywhere configures one."""
    src = gallery._album_wallpaper_source(album, variant)
    if src is not None:
        owner, path = src
        return path.name if owner == album else f"{path.name} (from {owner})"
    site = gallery._site_wallpaper_file(variant)
    if site is not None:
        return f"{site.name} (gallery.cfg)"
    return "— (shipped default)"


# What each file key of the theme block will and will not accept, for the
# message a typo gets. Same two keys in both cfg files (check_theme).
_WALLPAPER_HINTS = {"wallpaper": "video or still", "wallpaper_mobile": "stills only"}


def check_theme(cfg: dict, files: dict, scale, where: str) -> list[tuple]:
    """(level, key, detail) for the theme block BOTH cfg files carry: the
    accent, the face (`font` / `font_scale`) and the backdrop (`wallpaper`,
    `wallpaper_mobile`, and the two knobs).

    The keys are spelled identically in album.cfg and gallery.cfg — gallery.cfg
    dresses the site, an album overrides its own pages — so the rules live
    here once. All the caller passes in is what genuinely differs: how the
    file keys resolve (`files`: key -> a no-arg resolver returning a Path or
    None), how the scale resolves, and the folder to name in a message."""
    out = []
    for key, resolve in files.items():
        if key not in cfg:
            continue
        raw = gallery._cfg_first(cfg, key)
        if resolve() is None:
            hint = _WALLPAPER_HINTS.get(key)
            detail = f"{raw!r} not found in {where} (or unsupported type"
            detail += f" — {hint})" if hint else ")"
            out.append(("error", key, detail))
    if "font_scale" in cfg and scale() is None:
        lo, hi = gallery.ALBUM_FONT_SCALE_RANGE
        out.append(("warn", "font_scale",
                    f"ignored — not a number in {lo}–{hi}, or no `font` set"))
    if "accent" in cfg:
        raw = (gallery._cfg_first(cfg, "accent") or "").strip()
        rgb = gallery._parse_hex_color(raw)
        if rgb is None:
            out.append(("error", "accent",
                        f"{raw!r} is not a hex colour (#abc or #aabbcc) — ignored"))
        elif gallery._accent_shades(rgb)["lifted"]:
            # not an error: the gallery lightens it rather than shipping an
            # unreadable page, but the colour on screen is then not the one
            # in the file, and that is worth saying out loud.
            out.append(("warn", "accent",
                        f"{raw!r} is too dark to read on the black page — the gallery "
                        f"lightens it to {gallery._accent_shades(rgb)['acc']}"))
    out += check_wallpaper_knobs(cfg)
    return out


def check_wallpaper_knobs(cfg: dict) -> list[tuple]:
    """(level, key, detail) for whatever is wrong with `wallpaper_tint` /
    `wallpaper_dim`. Both album.cfg and gallery.cfg carry them under the same
    rules — gallery.cfg sets the site default, an album overrides it — so the
    check is written once."""
    out = []
    for key, span, unit in WALLPAPER_KNOBS:
        if key not in cfg:
            continue
        raw = (gallery._cfg_first(cfg, key) or "").strip()
        if raw.lower() in gallery._TRUE | gallery._FALSE:
            continue
        try:
            num = float(raw.replace(",", "."))
        except ValueError:
            out.append(("error", key, f"{raw!r} is not a number ({unit}) — ignored"))
            continue
        if not span[0] <= num <= span[1]:
            out.append(("warn", key,
                        f"{raw!r} is outside {unit} — ignored, the gallery default stands"))
    return out


def check_album_cfg(album: str) -> list[dict]:
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
    for level, key, detail in check_theme(
            cfg,
            {"font": lambda: gallery._album_font_file(album),
             "wallpaper": lambda: gallery._album_wallpaper_file(album, "desktop"),
             "wallpaper_mobile": lambda: gallery._album_wallpaper_file(album, "mobile")},
            lambda: gallery._album_font_scale(album), ".album/"):
        add(level, key, detail)
    # A custom stat renders as KEY / VALUE, so it needs the colon to split on;
    # without one _album_stats drops the line silently.
    for item in cfg.get("stat", []):
        label, sep, val = item.partition(":")
        if not sep:
            add("warn", "stat", f"{item!r} has no `Label: Value` colon — the line is dropped")
        elif not val.strip():
            add("warn", "stat", f"{item!r} has an empty value — the line is dropped")
    if "stats" in cfg:
        val = (gallery._cfg_first(cfg, "stats") or "").strip().lower()
        if val and val not in gallery._FALSE:
            add("warn", "stats", f"{val!r} does nothing — only an off/false/no value hides the block")
    return issues


def check_gallery_cfg() -> list[dict]:
    cfg = gallery._gallery_config()
    issues: list[dict] = []

    def add(level, key, detail):
        issues.append({"album": "(gallery.cfg)", "level": level, "key": key, "detail": detail})

    if not cfg:
        return issues

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
    for level, key, detail in check_theme(
            cfg,
            {"font": gallery._site_font_file,
             "wallpaper": lambda: gallery._site_wallpaper_file("desktop"),
             "wallpaper_mobile": lambda: gallery._site_wallpaper_file("mobile")},
            gallery._site_font_scale, ".gallery/"):
        add(level, key, detail)
    for level, key, detail in check_brand(cfg):
        add(level, key, detail)
    return issues


def check_brand(cfg: dict) -> list[tuple]:
    """(level, key, detail) for the branding block. Every failure here is
    silent in the browser — a mistyped logo falls back to the built-in mark,
    a bad URL drops the link, a badge naming a missing file just vanishes —
    so this is the only place they surface."""
    out = []
    meta = gallery._gallery_meta_dir()
    for key in BRAND_ASSET_KEYS:
        name = (gallery._cfg_first(cfg, key) or "").strip()
        if not name:
            continue
        if meta is None:
            out.append(("error", key, f"{name!r} — there is no photos/{gallery.GALLERY_META_DIR}/ folder"))
            continue
        if Path(name).name != name:
            out.append(("error", key, f"{name!r} must be a bare filename inside {gallery.GALLERY_META_DIR}/"))
        elif gallery._brand_file(name) is None:
            types = ", ".join(sorted(gallery.BRAND_ASSET_TYPES))
            out.append(("error", key, f"{name!r} is not a readable file in {gallery.GALLERY_META_DIR}/ ({types})"))
    for key in BRAND_URL_KEYS:
        raw = gallery._cfg_text(cfg, key)
        if raw and gallery._brand_link(cfg, key) is None:
            out.append(("error", key, f"{raw!r} is not an http(s) or site-relative URL — the link is dropped"))
    for badge in cfg.get("badges", [])[:gallery.BRAND_BADGE_MAX]:
        name = badge.partition("|")[0].strip()
        if name and gallery._brand_file(name) is None:
            out.append(("error", "badges", f"{name!r} is not a readable image in {gallery.GALLERY_META_DIR}/ — the badge is skipped"))
    if len(cfg.get("badges", [])) > gallery.BRAND_BADGE_MAX:
        out.append(("warn", "badges", f"only the first {gallery.BRAND_BADGE_MAX} are shown"))
    return out


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
    return {"photos": str(gallery.PHOTOS_DIR), "thumbs": str(gallery.THUMBS_DIR),
            "previews": str(gallery.PREVIEWS_DIR), "data": str(gallery.DATA_DIR),
            "scan_interval": gallery.SCAN_INTERVAL,
            "thumb_size": gallery.THUMB_SIZE,
            "preview_size": gallery.PREVIEW_SIZE,
            "watcher": gallery.ENABLE_WATCHER,
            "hide_gps": gallery.HIDE_GPS, "strip_gps": gallery.STRIP_GPS}


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
    if album and not (gallery.PHOTOS_DIR / album).is_dir():
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

    derivative_dirs = [gallery.THUMBS_DIR, gallery.PREVIEWS_DIR, gallery.FULLS_DIR]
    _tick("looking for orphaned derivatives")
    if album is None:  # orphan sweep only makes sense over the whole tree
        for d in derivative_dirs:
            if not d.is_dir():
                continue
            for f in derivative_files(d):
                # FULLS_DIR sits inside PREVIEWS_DIR by default — don't report
                # its contents twice, or as orphans of the previews tree
                if d is gallery.PREVIEWS_DIR and gallery.FULLS_DIR in f.parents:
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
            with Image.open(gallery.PHOTOS_DIR / rel) as img:
                img.verify()
        except Exception as e:
            note("unreadable", {"rel_path": rel, "detail": f"{type(e).__name__}: {e}"})

    # --- config ---
    _tick("parsing album.cfg files")
    for a in (gallery._albums_with_ancestors() if album is None else [album]):
        for issue in check_album_cfg(a):
            note("config", issue)
    if album is None:
        for issue in check_gallery_cfg():
            note("config", issue)

    # --- featured drift ---
    by_photo, unresolved = featured_map()
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
    return {"scope": album, "photos_on_disk": len(disk), "rows": len(rows),
            "problems": problems, "total": total}
