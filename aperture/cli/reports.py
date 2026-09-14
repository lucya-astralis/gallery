"""The commands that LOOK: what the app would resolve, printed.

`cfg`, `photo`, `album`, `search`, `tags`, `gps`, `welcome`, `trip`, `i18n`
and `export` render the payloads of aperture/reports.py (and, for `gps`,
aperture/ops.py) — the same functions the console serves — so a report reads
the same from either front end. None of them writes anything, except `export`
its archive and `gps --strip` the originals it names.
"""

import json
from datetime import datetime
from pathlib import Path

from .. import db, ops, reports, termui as ui
from ..runtime import settings

from .render import dump, fail, head, hint, kv, out


def cmd_cfg(args) -> int:
    if not args.gallery and not args.album:
        return fail("give an album path, or --gallery for the gallery-wide config")
    report = reports.cfg_report(args.album, gallery=args.gallery)
    issues = report["issues"]
    code = 1 if any(i["level"] == "error" for i in issues) else 0
    if args.json:
        dump(report)
        return code

    kv("file", report["file"])
    kv("exists", "yes" if report["exists"] else "NO — the app falls back to defaults")
    cfg = report["parsed"]
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
    res = report["resolved"]
    if res:
        head("resolved")
        ui.columns([
            ("showcase album", str(res["showcase"])),
            ("collection", str(res["collection"])),
            ("cover", res["cover"] or "— (no photo found)"),
            ("reel", f"{res['reel_mode']} ({res['reel']} photo(s))"),
            ("tags", ", ".join(res["tags"]) or "—"),
            ("descriptions", ", ".join(res["descriptions"]) or "—"),
            ("wallpaper", res["wallpaper"]),
            ("wallpaper mobile", res["wallpaper_mobile"]),
        ], key_tint=ui.C.gy)
    if issues:
        head(f"issues  ({len(issues)})")
        for item in issues:
            out(f"  [{item['level']}] {item['key']}: {item['detail']}")
        return code
    head("issues")
    out("  none")
    return 0


def cmd_photo(args) -> int:
    try:
        report = reports.photo_report(args.rel_path)
    except reports.NotFound as miss:
        if not miss.suggestions:
            return fail(str(miss))
        out(f"no exact match for {args.rel_path!r} — did you mean:")
        for rel in miss.suggestions:
            out(f"  {rel}")
        return 1
    if args.json:
        dump({k: v for k, v in report.items() if k not in ("exif_pretty", "urls")})
        return 0

    rel = report["rel_path"]
    kv("rel_path", rel)
    kv("album", report["album"])
    kv("file", f"{report['filename']} · {ops.bytes_h(report['size'])} · "
               f"{report['width']}×{report['height']}" if report["width"] else report["filename"])
    kv("on disk", "yes" if report["file_exists"] else "NO — the row is stale, run `scan`")
    kv("taken", report["taken_at"] or "— (no EXIF date; sorted by mtime)")
    drift = ""
    disk_mtime = report["file_mtime"]
    if disk_mtime is not None and abs(disk_mtime - report["mtime"]) >= 1.0:
        drift = f"  ← file says {ops.stamp(disk_mtime)} (stale index)"
    kv("mtime", f"{ops.stamp(report['mtime'])}{drift}")
    kv("indexed", report["indexed_at"])
    sources = report["featured_by"]
    kv("featured", ("yes" if report["is_showcase"] else "no") +
                   (" · " + ", ".join(f"{s['album']} → featured = {s['entry']}" for s in sources)
                    if sources else " · no album.cfg entry features it"))
    kv("tags", ", ".join(report["tags"]) or "—")
    head("derivatives")
    for kind, info in report["derivatives"].items():
        out(f"  {kind:<9}{info['state']:<8}{info['path']}")
    head("urls")
    for url in report["urls"]:
        out(f"  {url}")
    exif = report["exif"]
    head(f"exif  ({len(exif)} raw key(s))")
    for label, value in report["exif_pretty"]:
        out(f"  {label:<18}{value}")
    if args.exif:
        head("exif (raw)")
        for key in sorted(exif):
            out(f"  {key:<26}{exif[key]}")
    return 0


def cmd_trip(args) -> int:
    if not args.album:
        listing = reports.trips_report()["trips"]
        if args.json:
            dump({"trips": listing})
            return 0
        kv("trips", f"{len(listing)} configured in aperture/trips.py")
        for trip in listing:
            out(f"  {trip['key']:<16}{trip['title']} · {trip['stops']} stop(s)"
                f"{'' if trip['album_exists'] else '  ← no album with this path!'}")
        out()
        out("a trip attaches to the album whose lower-cased path equals its key")
        return 0
    try:
        trip = reports.trip_report(args.album, args.lang)
    except reports.NotFound as miss:
        return fail(f"{miss} (keys: {', '.join(miss.suggestions) or 'none'})")
    if args.json:
        dump({k: v for k, v in trip.items() if k != "album"})
        return 0
    kv("album", trip["album"])
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


# ----- tags -------------------------------------------------------------
def cmd_tags(args) -> int:
    db.conn()
    # One tag asked for by name: just list what carries it.
    if args.tag:
        report = reports.tag_report(args.tag, args.album)
        if args.json:
            dump({"tag": args.tag, "photos": report["photos"]})
            return 0 if report["found"] else 1
        if not report["found"]:
            kv("tag", f"{args.tag!r} is on no indexed photo")
            hint(f"  known tags: {', '.join(report['known']) or '—'}")
            return 1
        rels = report["photos"]
        kv("tag", f"{report['tag']} · {len(rels)} photo(s)")
        for rel in rels[:args.limit]:
            out(f"  {rel}")
        if len(rels) > args.limit:
            out(f"  … {len(rels) - args.limit} more")
        return 0

    report = reports.tags_report(args.album)
    unindexed = report["drift"]["not_indexed"]
    stale = report["drift"]["indexed_without_sidecar"]
    orphans = report["orphan_sidecars"]
    code = 1 if (unindexed or stale or orphans) else 0
    if args.json:
        dump(report)
        return code

    vocabulary = report["vocabulary"]
    kv("scope", report["album"] or "whole gallery")
    kv("vocabulary", f"{len(vocabulary)} tag(s) on {report['photos_tagged']} photo(s)")
    kv("sidecars", f"{report['sidecars']} `.tags` file(s) on disk")

    head("tags")
    if vocabulary:
        ui.columns([(t, f"{n} photo(s)") for t, n in
                    sorted(vocabulary.items(), key=lambda kv: (-kv[1], kv[0].lower()))])
    else:
        out("  none — write a `<photo>.tags` sidecar, or use the console")

    if unindexed:
        head(f"on disk, not indexed  ({len(unindexed)})")
        for item in unindexed[:args.limit]:
            out(f"  ! {item}")
        if len(unindexed) > args.limit:
            out(f"  … {len(unindexed) - args.limit} more")
        kv("fix", "`scan` — a sidecar's mtime counts as the photo's")
    if stale:
        head(f"indexed, no sidecar  ({len(stale)})")
        for item in stale[:args.limit]:
            out(f"  ! {item}")
        kv("fix", "`scan --force` on that album")
    if orphans:
        head(f"orphaned sidecars  ({len(orphans)})")
        for item in orphans[:args.limit]:
            out(f"  ! {item} — the photo it belongs to is gone")
    return code


# ----- welcome ----------------------------------------------------------
MODE_NOTE = {
    "manual": " — the cfg list, in this order",
    "showcase": " — random featured photos, so this list changes per load",
    "random": " — random photos, so this list changes per load",
}


def cmd_welcome(args) -> int:
    report = reports.welcome_report(desktop=not args.mobile_only,
                                    mobile=not args.desktop_only)
    devices = report["devices"]
    code = 1 if any(d["skipped"] for d in devices) else 0
    if args.json:
        dump(report)
        return code

    for device in devices:
        head(device["device"])
        kv("key", device["source_key"])
        kv("mode", device["mode"] + MODE_NOTE.get(device["mode"], ""))
        kv("label", device["label"])
        kv("shows", f"{len(device['feed'])} photo(s)")
        for rel in device["feed"][:args.limit]:
            out(f"  {rel}")
        if len(device["feed"]) > args.limit:
            out(f"  … {len(device['feed']) - args.limit} more")
        if device["skipped"]:
            out("")
            for raw in device["skipped"]:
                out(f"  ! {raw} — not indexed, entry skipped")
    if code:
        kv("fix", "`scan`, or correct the path in gallery.cfg")
    return code


# ----- gps --------------------------------------------------------------
def cmd_gps(args) -> int:
    """Which originals still carry coordinates. WRITES with --strip."""
    db.conn()
    live = ui.Live("reading EXIF", enabled=not args.json)
    try:
        report = ops.gps_audit(args.album, strip=args.strip,
                               progress=lambda label, done, total:
                                   live.progress(done, total, "photos"))
    finally:
        live.done()
    carrying, stripped, unreadable = report["with_gps"], report["stripped"], report["unreadable"]
    code = 1 if carrying and not args.strip else 0
    if args.json:
        dump(report)
        return code

    kv("scope", report["album"] or "whole gallery")
    kv("checked", f"{report['checked']} original(s)")
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
            kv("next", "`scan` on that album — the mtimes changed")
    elif carrying:
        kv("fix", "`gps --strip` rewrites them in place (originals are modified)")
    return code


# ----- album ------------------------------------------------------------
def cmd_album(args) -> int:
    if not args.album:
        listing = reports.albums_report()["albums"]
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

    info = reports.album_report(args.album)
    if args.json:
        dump(info)
        return 1 if info["issues"] else 0

    cfg = info["cfg"]
    kv("album", info["album"])
    kv("photos", f"{info['photos']} · {ops.bytes_h(info['bytes'])}"
                 + (f" · {info['undated']} undated" if info["undated"] else ""))
    if info["span"]:
        kv("span", f"{info['span'][0][:10]} → {info['span'][1][:10]}")
    kv("flags", ", ".join(filter(None, [
        "showcase" if info["showcase"] else None,
        "collection" if info["collection"] else None,
    ])) or "—")
    kv("cover", info["cover"] or "auto (newest photo)")
    kv("featured", f"{len(info['featured'])} photo(s)" if info["featured"] else "—")
    kv("tags", ", ".join(info["tags"]) or "—")
    kv("look", ", ".join(filter(None, [
        f"icon={info['icon']}" if info["icon"] else None,
        f"font={info['font']}" if info["font"] else None,
        f"effect={cfg['effect'][0]}" if cfg.get("effect") else None,
    ])) or "—")
    kv("text", ", ".join(info["descriptions"]) or "no album_*.md")
    children = info["sub_albums"]
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
    """The query the /search page runs, in the same grammar (search.py:
    words plus camera: lens: iso: f: mm: date: filters), so what this lists is
    what the page would list."""
    if not (args.query or "").strip():
        return fail("nothing to search for")
    report = reports.search_report(args.query, args.album, limit=args.limit)
    if args.json:
        dump(report)
        return 0 if report["matches"] else 1

    kv("query", report["query"] + (f" · in {report['album']}" if report["album"] else ""))
    kv("matches", f"{report['matches']} photo(s)")
    if report["ignored"]:
        kv("ignored", ui.state(", ".join(report["ignored"]) + " — not understood", "warn"))
    if not report["matches"]:
        hint("  words match album, file name, tag, camera and lens; "
             "narrow with camera: lens: iso: f: mm: date:")
        return 1
    head("matches")
    ui.columns([(r["rel_path"], (r["taken_at"] or "undated")[:10])
                for r in report["photos"]])
    if report["matches"] > args.limit:
        out(f"  … {report['matches'] - args.limit} more  (--limit)")
    return 0


# ----- export -----------------------------------------------------------
def cmd_export(args) -> int:
    """Snapshot every hand-written file: gallery.cfg, `.gallery/` and each
    `.album/` — see reports.export_members for what goes in and why."""
    if args.list:
        report = reports.export_report()
        if args.json:
            dump(report)
            return 0
        files = report["files"]
        kv("contents", f"{len(files)} file(s) · {ops.bytes_h(report['bytes'])}")
        kv("archive", "not written — drop --list to create it")
        head("files")
        for name in files[:args.limit]:
            out(f"  {name}")
        if len(files) > args.limit:
            out(f"  … {len(files) - args.limit} more")
        return 0

    target = Path(args.out or f"gallery-config-{datetime.now():%Y%m%dT%H%M%S}.tar.gz")
    if target.exists() and not args.force:
        return fail(f"{target} exists — pass --force to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as fh:
        result = reports.write_export(fh)

    if args.json:
        dump({"archive": str(target), "files": result["files"],
              "bytes": target.stat().st_size})
        return 0
    kv("archive", str(target))
    kv("contents", f"{result['files']} file(s) · {ops.bytes_h(target.stat().st_size)}")
    hint("  restore with:  tar -xzf <archive> -C <photos dir>")
    return 0


def cmd_i18n(args) -> int:
    report = reports.i18n_report()
    problems, total, hard = report["problems"], report["total"], report["hard"]
    if args.json:
        dump({k: v for k, v in report.items() if k not in ("hard", "used")})
        return 1 if hard else 0

    kv("table", f"{report['keys']} key(s) × {len(report['languages'])} language(s) in app/i18n.py")
    kv("app.js", " · ".join(f"{k}:{n}" for k, n in report["js_languages"].items()) or "—")
    kv("used", f"{report['used']} of them referenced in templates / app code")
    for kind in reports.I18N_ORDER:
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
