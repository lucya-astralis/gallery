"""The commands that LOOK: what the app would resolve, printed.

`cfg`, `photo`, `album`, `search`, `tags`, `gps`, `welcome`, `trip`, `i18n`
and `export` read the index, the photo tree and the cfg files through the
gallery's own modules, so what they report is what the pages render rather
than a second opinion about it. None of them writes anything.
"""

import json
import re

from .. import albums, cfgio, checks, config, db, i18n, ops, photos, scanner, schema, templating, termui as ui, theme, trips, welcome
from ..runtime import settings
from PIL import Image
from datetime import datetime
from pathlib import Path

from .render import dump, fail, head, hint, kv, out


def cmd_cfg(args) -> int:
    db.conn()
    if args.gallery:
        cfg = config.gallery_config()
        path = config.GALLERY_CFG_PATH
        issues = checks.gallery()
        title = "gallery.cfg"
    else:
        if not args.album:
            return fail("give an album path, or --gallery for the gallery-wide config")
        album = ops.norm_album(args.album)
        cfg = config.album_config(album)
        meta = config.album_meta_dir(album)
        path = (meta / "album.cfg") if meta else (settings.photos_dir / album / ".album" / "album.cfg")
        issues = checks.album(album)
        title = f"{album}/.album/album.cfg"

    if args.json:
        dump({"file": str(path), "exists": path.is_file(), "parsed": cfg, "issues": issues})
        return 1 if any(i["level"] == "error" for i in issues) else 0

    kv("file", str(path))
    kv("exists", "yes" if path.is_file() else "NO — the app falls back to defaults")
    if not cfg:
        kv("parsed", "nothing (no file, or an empty one)")
        return 0
    head("parsed")
    # the key column sizes itself: a fixed 12 ran `wallpaper_mobile` straight
    # into its value with no gap
    key_w = max((len(k) for k in cfg), default=0) + 2
    for key in sorted(cfg):
        values = cfg[key]
        shown = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)
        out(f"  {key:<{key_w}}{shown}")
    if not args.gallery:
        album = ops.norm_album(args.album)
        head("resolved")
        mode, reel = albums.album_reel(album, cfg)
        cover = albums.album_cover_rel(album)
        langs = [lang for lang in i18n.LANGS if albums.album_description(album, lang)]
        ui.columns([
            ("showcase album", str(albums.album_is_showcase(album))),
            ("collection", str(config.album_collection(album))),
            ("cover", cover or "— (no photo found)"),
            ("reel", f"{mode} ({len(reel)} photo(s))"),
            ("tags", ", ".join(config.album_tags(album, cfg)) or "—"),
            ("descriptions", ", ".join(f"album_{l}.md" for l in langs) or "—"),
            # resolved, so an inherited backdrop names the album it came from
            ("wallpaper", ops.wallpaper_line(album, "desktop")),
            ("wallpaper mobile", ops.wallpaper_line(album, "mobile")),
        ], key_tint=ui.C.gy)
    if issues:
        head(f"issues  ({len(issues)})")
        for item in issues:
            out(f"  [{item['level']}] {item['key']}: {item['detail']}")
        return 1 if any(i["level"] == "error" for i in issues) else 0
    head("issues")
    out("  none")
    return 0


def cmd_photo(args) -> int:
    c = db.conn()
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
    by_photo, _ = ops.featured_map()
    sources = by_photo.get(rel, [])
    src = settings.photos_dir / rel
    disk_mtime = ops.effective_mtime(rel)
    derivatives = {k: {"path": str(p), "state": ops.derivative_state(rel).get(k)}
                   for k, p in ops.derivatives(rel).items()}

    if args.json:
        dump({**{k: v for k, v in row.items() if k != "exif_json"},
              "tags": tags, "exif": exif,
              "featured_by": [{"album": a, "entry": e} for a, e in sources],
              "file_exists": src.exists(), "file_mtime": disk_mtime,
              "derivatives": derivatives})
        return 0

    kv("rel_path", rel)
    kv("album", row["album"])
    kv("file", f"{row['filename']} · {ops.bytes_h(row['size'])} · "
               f"{row['width']}×{row['height']}" if row["width"] else row["filename"])
    kv("on disk", "yes" if src.exists() else "NO — the row is stale, run `scan`")
    kv("taken", row["taken_at"] or "— (no EXIF date; sorted by mtime)")
    drift = ""
    if disk_mtime is not None and abs(disk_mtime - row["mtime"]) >= 1.0:
        drift = f"  ← file says {ops.stamp(disk_mtime)} (stale index)"
    kv("mtime", f"{ops.stamp(row['mtime'])}{drift}")
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
    for label, value in photos.prettify_exif(exif, i18n.DEFAULT_LANG):
        out(f"  {label:<18}{value}")
    if args.exif:
        head("exif (raw)")
        for key in sorted(exif):
            out(f"  {key:<26}{exif[key]}")
    return 0


def cmd_trip(args) -> int:
    db.conn()
    if not args.album:
        kv("trips", f"{len(trips.TRIPS)} configured in aperture/trips.py")
        for key, cfg in trips.TRIPS.items():
            exists = albums.album_exists(key)
            out(f"  {key:<16}{cfg.get('title')} · {len(cfg.get('stops', []))} stop(s)"
                f"{'' if exists else '  ← no album with this path!'}")
        out()
        out("a trip attaches to the album whose lower-cased path equals its key")
        return 0
    album = ops.norm_album(args.album)
    trip = trips.trip_for_album(album, args.lang)
    if trip is None:
        return fail(f"no trip configured for {album!r} "
                    f"(keys: {', '.join(trips.TRIPS) or 'none'})")
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
    hint("  the full structure, exactly as the template gets it: `trip <album> --json`")
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
    and the CLI package, whose own reports quote keys)."""
    parts = []
    for path in sorted((templating.WEB_DIR / "templates").glob("*.html")):
        parts.append(path.read_text(encoding="utf-8"))
    for path in sorted(p for p in templating.WEB_DIR.parent.rglob("*.py")
                       if "console" not in p.parts and "cli" not in p.parts):
        if path.name == "i18n.py":
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _js_ui_strings() -> dict[str, set[str]]:
    """Key sets of the UI_STRINGS blocks in app.js, per language. Parsed by
    indentation rather than by evaluating JS — the block is hand-formatted
    and stays that way."""
    text = (templating.WEB_DIR / "static" / "app.js").read_text(encoding="utf-8")
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


# ----- tags -------------------------------------------------------------
def _sidecar_tags_on_disk(album: str | None = None) -> tuple[dict, list[str]]:
    """Every `.tags` sidecar under PHOTOS_DIR, parsed the way the scanner
    parses it: {rel_path: [tag, ...]}, plus the sidecars whose photo is gone.

    Read off the filesystem rather than the index on purpose — the whole point
    of this command is catching the two drifting apart.
    """
    found: dict[str, list[str]] = {}
    orphans: list[str] = []
    root = settings.photos_dir
    base = (root / album) if album else root
    if not base.is_dir():
        return found, orphans
    for path in sorted(base.rglob("*.tags")):
        photo = path.with_suffix("")          # strip `.tags`, keep `.png`
        rel = photo.relative_to(root).as_posix()
        if not photo.is_file():
            orphans.append(path.relative_to(root).as_posix())
            continue
        found[rel] = scanner._read_sidecar_tags(photo)
    return found, orphans


def cmd_tags(args) -> int:
    c = db.conn()
    album = ops.norm_album(args.album)

    # What the index believes.
    where, params = "", []
    if album:
        where = "WHERE i.album = ? OR substr(i.album, 1, ?) = ?"
        params = [album, len(album) + 1, album + "/"]
    rows = c.execute(
        f"""SELECT t.name AS tag, i.rel_path AS rel
            FROM tags t
            JOIN image_tags it ON it.tag_id = t.id
            JOIN images i ON i.id = it.image_id
            {where}
            ORDER BY t.name COLLATE NOCASE, i.rel_path""",
        params,
    ).fetchall()
    indexed: dict[str, list[str]] = {}
    for row in rows:
        indexed.setdefault(row["tag"], []).append(row["rel"])

    # One tag asked for by name: just list what carries it.
    if args.tag:
        want = args.tag.strip().lower()
        hits = {t: r for t, r in indexed.items() if t.lower() == want}
        if args.json:
            dump({"tag": args.tag, "photos": sorted(next(iter(hits.values()), []))})
            return 0 if hits else 1
        if not hits:
            kv("tag", f"{args.tag!r} is on no indexed photo")
            hint(f"  known tags: {', '.join(sorted(indexed)) or '—'}")
            return 1
        for tag, rels in hits.items():
            kv("tag", f"{tag} · {len(rels)} photo(s)")
            for rel in rels[:args.limit]:
                out(f"  {rel}")
            if len(rels) > args.limit:
                out(f"  … {len(rels) - args.limit} more")
        return 0

    disk, orphans = _sidecar_tags_on_disk(album)

    # Drift: the sidecar is the source of truth, the index is the copy.
    drift_unindexed: list[str] = []   # on disk, not in the index
    drift_stale: list[str] = []       # in the index, not on disk
    by_photo_indexed: dict[str, set[str]] = {}
    for tag, rels in indexed.items():
        for rel in rels:
            by_photo_indexed.setdefault(rel, set()).add(tag.lower())
    for rel, tags in disk.items():
        have = by_photo_indexed.get(rel, set())
        for tag in tags:
            if tag.lower() not in have:
                drift_unindexed.append(f"{rel} · {tag}")
    for rel, tags in by_photo_indexed.items():
        on_disk = {t.lower() for t in disk.get(rel, [])}
        for tag in sorted(tags):
            if tag not in on_disk:
                drift_stale.append(f"{rel} · {tag}")

    if args.json:
        dump({"album": album,
              "vocabulary": {t: len(r) for t, r in indexed.items()},
              "photos_tagged": len(by_photo_indexed),
              "sidecars": len(disk),
              "drift": {"not_indexed": sorted(drift_unindexed),
                        "indexed_without_sidecar": sorted(drift_stale)},
              "orphan_sidecars": orphans})
        return 1 if (drift_unindexed or drift_stale or orphans) else 0

    kv("scope", album or "whole gallery")
    kv("vocabulary", f"{len(indexed)} tag(s) on {len(by_photo_indexed)} photo(s)")
    kv("sidecars", f"{len(disk)} `.tags` file(s) on disk")

    head("tags")
    if indexed:
        ui.columns([(t, f"{len(r)} photo(s)") for t, r in
                    sorted(indexed.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))])
    else:
        out("  none — write a `<photo>.tags` sidecar, or use the console")

    if drift_unindexed:
        head(f"on disk, not indexed  ({len(drift_unindexed)})")
        for item in sorted(drift_unindexed)[:args.limit]:
            out(f"  ! {item}")
        if len(drift_unindexed) > args.limit:
            out(f"  … {len(drift_unindexed) - args.limit} more")
        kv("fix", "`scan` — a sidecar's mtime counts as the photo's")
    if drift_stale:
        head(f"indexed, no sidecar  ({len(drift_stale)})")
        for item in sorted(drift_stale)[:args.limit]:
            out(f"  ! {item}")
        kv("fix", "`scan --force` on that album")
    if orphans:
        head(f"orphaned sidecars  ({len(orphans)})")
        for item in orphans[:args.limit]:
            out(f"  ! {item} — the photo it belongs to is gone")
    return 1 if (drift_unindexed or drift_stale or orphans) else 0


# ----- welcome ----------------------------------------------------------
def _welcome_report(mobile: bool) -> dict:
    """What the welcome hero resolves to for one device class, and which
    gallery.cfg entries were dropped getting there."""
    cfg = config.gallery_config()
    key = "welcome_mobile" if mobile else "welcome_desktop"
    spec = cfg.get(key)
    source = key
    if not spec:
        spec = cfg.get("welcome", [])
        source = "welcome" if spec else "(unset)"

    skipped = []
    if not (len(spec) == 1 and spec[0].lower() in schema.WELCOME_KEYWORDS):
        for raw in spec:
            if welcome.lookup_welcome_image(raw) is None:
                skipped.append(raw)

    feed, label, mode = welcome.welcome_feed(mobile=mobile)
    return {"device": "mobile" if mobile else "desktop",
            "source_key": source, "spec": list(spec), "skipped": skipped,
            "mode": mode, "label": label,
            "feed": [f["rel_path"] for f in feed]}


MODE_NOTE = {
    "manual": " — the cfg list, in this order",
    "showcase": " — random featured photos, so this list changes per load",
    "random": " — random photos, so this list changes per load",
}


def cmd_welcome(args) -> int:
    db.conn()
    devices = []
    if not args.mobile_only:
        devices.append(_welcome_report(mobile=False))
    if not args.desktop_only:
        devices.append(_welcome_report(mobile=True))

    if args.json:
        dump({"devices": devices,
              "feed_max": welcome.WELCOME_FEED_MAX,
              "keywords": sorted(schema.WELCOME_KEYWORDS)})
        return 1 if any(d["skipped"] for d in devices) else 0

    for report in devices:
        head(report["device"])
        kv("key", report["source_key"])
        kv("mode", report["mode"] + MODE_NOTE.get(report["mode"], ""))
        kv("label", report["label"])
        kv("shows", f"{len(report['feed'])} photo(s)")
        for rel in report["feed"][:args.limit]:
            out(f"  {rel}")
        if len(report["feed"]) > args.limit:
            out(f"  … {len(report['feed']) - args.limit} more")
        if report["skipped"]:
            out("")
            for raw in report["skipped"]:
                out(f"  ! {raw} — not indexed, entry skipped")
    if any(d["skipped"] for d in devices):
        kv("fix", "`scan`, or correct the path in gallery.cfg")
        return 1
    return 0


# ----- gps --------------------------------------------------------------
def cmd_gps(args) -> int:
    """Which originals still carry coordinates. WRITES with --strip."""
    db.conn()
    album = ops.norm_album(args.album)
    base = (settings.photos_dir / album) if album else settings.photos_dir
    if not base.is_dir():
        return fail(f"no such album: {args.album!r}")

    files = [p for p in sorted(base.rglob("*"))
             if p.is_file() and schema.is_image(p)
             and not scanner.is_meta_path(p.relative_to(settings.photos_dir))]

    live = ui.Live("reading EXIF", enabled=not args.json)
    carrying: list[str] = []
    stripped: list[str] = []
    unreadable: list[str] = []
    for seen, path in enumerate(files, 1):
        if seen % 25 == 0 or seen == len(files):
            live.progress(seen, len(files), "photos")
        try:
            with Image.open(path) as img:
                has = scanner._has_gps(img.getexif())
        except Exception as exc:
            unreadable.append(f"{path.relative_to(settings.photos_dir).as_posix()} — {exc}")
            continue
        if not has:
            continue
        rel = path.relative_to(settings.photos_dir).as_posix()
        carrying.append(rel)
        if args.strip and scanner.strip_gps_inplace(path):
            stripped.append(rel)
    live.done()

    if args.json:
        dump({"album": album, "checked": len(files),
              "with_gps": carrying, "stripped": stripped,
              "unreadable": unreadable,
              "settings": {"hide_gps": bool(settings.hide_gps),
                           "strip_gps": bool(settings.strip_gps)}})
        return 1 if carrying and not args.strip else 0

    kv("scope", album or "whole gallery")
    kv("checked", f"{len(files)} original(s)")
    kv("settings", f"hide_gps={int(settings.hide_gps)} · strip_gps={int(settings.strip_gps)}",
       "" if settings.strip_gps else ui.C.ye)
    kv("with gps", f"{len(carrying)} photo(s)", ui.C.gn if not carrying else ui.C.ye)
    if carrying:
        head("coordinates present")
        for rel in carrying[:args.limit]:
            mark = "stripped" if rel in stripped else "!"
            out(f"  {mark:>8}  {rel}")
        if len(carrying) > args.limit:
            out(f"  … {len(carrying) - args.limit} more")
    if unreadable:
        head(f"unreadable  ({len(unreadable)})")
        for item in unreadable[:args.limit]:
            out(f"  ! {item}")
    if args.strip:
        kv("stripped", f"{len(stripped)} file(s) rewritten in place")
        if stripped:
            kv("next", "`scan --force` on that album — the mtimes changed")
    elif carrying:
        kv("fix", "`gps --strip` rewrites them in place (originals are modified)")
    return 1 if carrying and not args.strip else 0


# ----- album ------------------------------------------------------------
def cmd_album(args) -> int:
    c = db.conn()
    album = ops.norm_album(args.album)

    if not album:
        listing = []
        for name in albums.all_album_nodes():
            row = c.execute(
                "SELECT COUNT(*) AS n FROM images WHERE album = ? OR substr(album, 1, ?) = ?",
                (name, len(name) + 1, name + "/")).fetchone()
            cfg = config.album_config(name)
            listing.append({"album": name, "photos": row["n"], "has_cfg": bool(cfg),
                            "showcase": albums.album_is_showcase(name),
                            "collection": config.album_collection(name, cfg)})
        if args.json:
            dump({"albums": listing})
            return 0
        kv("albums", f"{len(listing)}")
        head("albums")
        ui.columns([(a["album"],
                     f"{a['photos']:>5} photo(s)"
                     + ("  ·  cfg" if a["has_cfg"] else "")
                     + ("  ·  showcase" if a["showcase"] else "")
                     + ("  ·  collection" if a["collection"] else ""))
                    for a in listing])
        hint("  `album <name>` for one in full")
        return 0

    cfg = config.album_config(album)
    rows = c.execute(
        "SELECT rel_path, taken_at, size FROM images "
        "WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY taken_at",
        (album, len(album) + 1, album + "/")).fetchall()
    dates = [r["taken_at"] for r in rows if r["taken_at"]]
    meta = config.album_meta_dir(album)
    descriptions = sorted(p.name for p in meta.glob("album_*.md")) if meta else []
    children = [n for n in albums.all_album_nodes() if n.startswith(album + "/")]
    featured = albums.resolve_photo_refs(album, cfg.get("featured", []))
    icon = theme.album_icon_file(album)
    font = theme.album_font_file(album)

    info = {
        "album": album,
        "photos": len(rows),
        "bytes": sum(r["size"] or 0 for r in rows),
        "span": [dates[0], dates[-1]] if dates else None,
        "undated": sum(1 for r in rows if not r["taken_at"]),
        "has_cfg": bool(cfg),
        "cfg": dict(cfg),
        "cover": albums.config_cover_rel(album, cfgio.first(cfg, "cover")),
        "featured": featured,
        "showcase": albums.album_is_showcase(album),
        "collection": config.album_collection(album, cfg),
        "tags": config.album_tags(album, cfg),
        "descriptions": descriptions,
        "icon": icon.name if icon else None,
        "font": font.name if font else None,
        "sub_albums": children,
        "issues": checks.album(album),
    }
    if args.json:
        dump(info)
        return 1 if info["issues"] else 0

    kv("album", album)
    kv("photos", f"{info['photos']} · {ops.bytes_h(info['bytes'])}"
                 + (f" · {info['undated']} undated" if info["undated"] else ""))
    if info["span"]:
        kv("span", f"{info['span'][0][:10]} → {info['span'][1][:10]}")
    kv("flags", ", ".join(filter(None, [
        "showcase" if info["showcase"] else None,
        "collection" if info["collection"] else None,
    ])) or "—")
    kv("cover", info["cover"] or "auto (newest photo)")
    kv("featured", f"{len(featured)} photo(s)" if featured else "—")
    kv("tags", ", ".join(info["tags"]) or "—")
    kv("look", ", ".join(filter(None, [
        f"icon={info['icon']}" if info["icon"] else None,
        f"font={info['font']}" if info["font"] else None,
        f"effect={cfgio.first(cfg, 'effect')}" if cfg.get("effect") else None,
    ])) or "—")
    kv("text", ", ".join(info["descriptions"]) or "no album_*.md")
    if children:
        head(f"sub-albums  ({len(children)})")
        for name in children[:args.limit]:
            out(f"  {name}")
    if info["issues"]:
        head(f"cfg issues  ({len(info['issues'])})")
        for issue in info["issues"]:
            out(f"  {issue['level']:>5}  {issue['key']} — {issue['detail']}")
        return 1
    return 0


# ----- search -----------------------------------------------------------
def cmd_search(args) -> int:
    """The same query the /search page runs, so what this lists is what the
    page would list."""
    c = db.conn()
    query = (args.query or "").strip()
    if not query:
        return fail("nothing to search for")
    like = f"%{query}%"
    rows = c.execute(
        """SELECT DISTINCT i.rel_path, i.album, i.filename, i.taken_at
           FROM images i
           LEFT JOIN image_tags it ON it.image_id = i.id
           LEFT JOIN tags t ON t.id = it.tag_id
           WHERE i.album LIKE ? OR i.filename LIKE ? OR t.name LIKE ?
           ORDER BY i.taken_at IS NULL, i.taken_at DESC, i.filename""",
        (like, like, like)).fetchall()

    album = ops.norm_album(args.album)
    if album:
        rows = [r for r in rows
                if r["album"] == album or r["album"].startswith(album + "/")]

    if args.json:
        dump({"query": query, "album": album, "matches": len(rows),
              "photos": [dict(r) for r in rows[:args.limit]]})
        return 0 if rows else 1

    kv("query", query + (f" · in {album}" if album else ""))
    kv("matches", f"{len(rows)} photo(s)")
    if not rows:
        hint("  the page matches album name, file name and tag — nothing else")
        return 1
    head("matches")
    ui.columns([(r["rel_path"], (r["taken_at"] or "undated")[:10])
                for r in rows[:args.limit]])
    if len(rows) > args.limit:
        out(f"  … {len(rows) - args.limit} more  (--limit)")
    return 0


# ----- export -----------------------------------------------------------
def cmd_export(args) -> int:
    """Snapshot every hand-written file: gallery.cfg, `.gallery/` and each
    `.album/`.

    Photos are deliberately left out — they are the one thing that already is
    the backup. What this captures is the part that cannot be regenerated: the
    config, the descriptions, the icons and title fonts, and the gallery's own
    branding assets.
    """
    import tarfile

    root = settings.photos_dir
    members: list[tuple[Path, str]] = []
    # gallery.cfg lives inside `.gallery/`, which the walk below takes whole
    metas = sorted(root.rglob(scanner.ALBUM_META_DIR))
    brand_dir = root / scanner.GALLERY_META_DIR
    if brand_dir.is_dir():
        metas.insert(0, brand_dir)
    for meta in metas:
        if not meta.is_dir():
            continue
        for path in sorted(meta.rglob("*")):
            if path.is_file() and path.name not in ("Thumbs.db", ".DS_Store"):
                members.append((path, path.relative_to(root).as_posix()))

    total = sum(p.stat().st_size for p, _ in members)
    if args.list:
        if args.json:
            dump({"files": [name for _, name in members], "bytes": total})
            return 0
        kv("contents", f"{len(members)} file(s) · {ops.bytes_h(total)}")
        kv("archive", "not written — drop --list to create it")
        head("files")
        for _, name in members[:args.limit]:
            out(f"  {name}")
        if len(members) > args.limit:
            out(f"  … {len(members) - args.limit} more")
        return 0

    target = Path(args.out or f"gallery-config-{datetime.now():%Y%m%dT%H%M%S}.tar.gz")
    if target.exists() and not args.force:
        return fail(f"{target} exists — pass --force to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as tar:
        for path, name in members:
            tar.add(path, arcname=name)

    if args.json:
        dump({"archive": str(target), "files": len(members),
              "bytes": target.stat().st_size})
        return 0
    kv("archive", str(target))
    kv("contents", f"{len(members)} file(s) · {ops.bytes_h(target.stat().st_size)}")
    hint("  restore with:  tar -xzf <archive> -C <photos dir>")
    return 0


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
    head("result")
    if not total:
        kv("result", ui.state("no problems found"))
    elif not hard:
        kv("result", f"{total} note(s), nothing broken "
                     f"(blank/untranslated/unused are informational)")
    else:
        kv("result", ui.state(f"{hard} problem(s) that affect rendering", "warn"))
        kv("reminder", "new Japanese glyphs need `python tools/build_jp_subset.py`")
    return 1 if hard else 0
