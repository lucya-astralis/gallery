"""The commands that DO something: the indexer, the derivatives, the door.

`status`, `scan`, `pause` and `resume` talk to the running server the only way
anything does -- the flag-file channel in DATA_DIR/control (aperture/control.py),
never an HTTP call, so there is one place a scan can begin whoever asked.
`doctor`, `thumbs`, `featured` and `disk` work on the index and the derivative
trees; `passwd` sets the console's password.
"""

import sys
import time

from .. import albums, control, db, ops, scanner, termui as ui
from ..runtime import settings

from .render import _render_system, _screen, dump, fail, head, kv, out


def cmd_status(args) -> int:
    report = ops.status()
    if args.json:
        dump(report)
        return 0
    with _screen("status"):
        _render_status_body(report["server"], report["live"],
                            report["pause"], report["index"])
    return 0


def _render_status_body(st, live, pause, counts) -> None:
    _render_system(st, live, pause)
    head("index")
    kv("totals", f"{counts['images']} photos · {counts['albums']} albums · "
                f"{counts['featured']} featured · {counts['tags']} tags · "
                f"{ops.bytes_h(counts['bytes'])} of originals · db {ops.bytes_h(counts['db_bytes'])}")
    kv("paths", f"photos={settings.photos_dir}")
    kv("", f"thumbs={settings.thumbs_dir}")
    kv("", f"previews={settings.previews_dir}")
    kv("", f"data={settings.data_dir}")
    cfg = (st or {}).get("config") or {}
    kv("config", f"scan_interval={cfg.get('scan_interval', settings.scan_interval)}s · "
                 f"thumb={cfg.get('thumb_size', settings.thumb_size)} · "
                 f"preview={cfg.get('preview_size', settings.preview_size)} · "
                 f"watcher={'on' if settings.enable_watcher else 'off'} · "
                 f"hide_gps={int(settings.hide_gps)} · strip_gps={int(settings.strip_gps)}")


def _wait_for_scan(request_id: str, timeout: float, quiet: bool = False) -> dict | None:
    """Poll status.json until the server reports our request as finished,
    spinning while we wait so a long scan does not look like a hang."""
    deadline = time.time() + timeout
    started = time.time()
    live = ui.Live("waiting for the server", enabled=not quiet)
    try:
        while time.time() < deadline:
            st = control.read_status()
            last = (st or {}).get("last_scan") or {}
            if last.get("request_id") == request_id:
                return last
            if not control.status_is_live(st):
                return None
            live.label = (f"scanning ({st.get('scan_trigger')})" if st.get("scanning")
                          else "waiting for the server")
            live.tick(ops.dur(time.time() - started))
            time.sleep(0.2)
        return None
    finally:
        live.done()


def _print_scan_result(summary: dict) -> None:
    res = summary.get("result") or {}
    if summary.get("error"):
        kv("finished", ui.state(f"with errors in {ops.dur(summary.get('seconds'))} · "
                                f"{summary['error']}", "bad"))
    else:
        kv("finished", f"in {ops.dur(summary.get('seconds'))}")
    for key in ("indexed", "thumbnails", "previews", "removed", "failed", "total_seen"):
        if key in res:
            kv(key, res[key])
    if res.get("failed"):
        kv("note", "unreadable files stay in the gallery without a thumbnail — "
                   "see `doctor`")
    if res.get("held"):
        kv("held", ui.state("the walk found no photos, so the index was left "
                            "as it is — is the share mounted? `scan --force` "
                            "clears it if the gallery really is empty", "warn"))


def cmd_scan(args) -> int:
    album = ops.norm_album(args.album)
    if album and not (settings.photos_dir / album).is_dir():
        return fail(f"no such album folder: {album}")
    st, live = ops.server_status()

    if live and not args.local:
        req = control.request_scan(album=album, force=args.force)
        scope = f" of {album}" if album else ""
        if not args.json:
            kv("requested", f"{album or 'whole gallery'}"
                            f"{' · force' if args.force else ''} · the server picks it up "
                            f"within {control.CONTROL_TICK:.0f}s")
            kv("request id", req["id"])
        if args.no_wait:
            if args.json:
                dump({"queued": req, "waited": False})
            return 0
        summary = _wait_for_scan(req["id"], args.timeout, quiet=args.json)
        if summary is None:
            if args.json:
                dump({"queued": req, "waited": True, "result": None,
                      "error": "timeout or server gone"})
            else:
                kv("gave up", ui.state(f"after {ops.dur(args.timeout)} — the scan may still be "
                                       f"running, check `status`", "warn"))
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
        kv("mode", "server not running — scanning in this process")

    db.conn()
    started = time.time()
    result = scanner.full_scan(
        settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
        previews_dir=settings.previews_dir, preview_size=settings.preview_size,
        root=album, force=args.force,
    )
    albums.recompute_featured()
    summary = {"result": result, "seconds": round(time.time() - started, 3), "error": None}
    if args.json:
        dump(summary)
        return 0
    _print_scan_result(summary)
    return 0


def cmd_pause(args) -> int:
    reason = " ".join(args.reason).strip() if args.reason else ""
    info = control.pause(reason or None)
    _, live = ops.server_status()
    if args.json:
        dump({"paused": True, "info": info, "server_live": live})
        return 0
    kv("paused", ui.state("yes", "warn") + (f" · {reason}" if reason else "")
       + ("" if live else " · server is not running, takes effect at its next start"))
    kv("periodic", "off")
    kv("watcher", "keeps queueing events, processes them on resume")
    kv("manual", "a `scan` still works, it ignores the pause")
    kv("note", "the pause survives a restart — lift it with `resume`")
    return 0


def cmd_resume(args) -> int:
    was_paused = control.resume()
    _, live = ops.server_status()
    if args.scan and live:
        control.request_scan()
    if args.json:
        dump({"was_paused": was_paused, "server_live": live, "scan_requested": bool(args.scan and live)})
        return 0
    kv("resumed", ui.state("yes") if was_paused else "it was not paused")
    if live:
        kv("watcher", f"queued events are drained within ~{control.CONTROL_TICK:.0f}s")
        if args.scan:
            kv("scan", "requested")
    else:
        kv("server", "not running — indexing starts with it")
    return 0


def cmd_doctor(args) -> int:
    """The one command with a meaningful exit code: non-zero when it found
    something, so it works as a cron or CI check. The checking itself is
    ops.doctor(); this is the spinner and the report."""
    live = ui.Live("checking", enabled=not args.json)
    try:
        report = ops.doctor(args.album, limit_slow=args.limit_slow,
                            progress=lambda label, *rest:
                                live.progress(rest[0], rest[1], label) if rest
                                else live.tick(label))
    finally:
        live.done()

    problems, total = report["problems"], report["total"]
    if args.json:
        dump(report)
        return 1 if total else 0

    kv("scope", report["scope"] or "whole gallery")
    kv("checked", f"{report['photos_on_disk']} file(s) on disk · "
                  f"{report['rows']} row(s) indexed")
    if not total:
        kv("result", ui.state("no problems found"))
        return 0
    for check in sorted(problems):
        items = problems[check]
        head(f"{check}  ({len(items)})")
        for item in items[:args.limit]:
            if check == "config":
                where = item['album'] or ('links.cfg' if item['scope'] == 'links' else 'gallery.cfg')
                out(f"  [{item['level']}] {where} · {item['key']}: {item['detail']}")
            else:
                out(f"  {item['rel_path']}")
                out(f"      {item['detail']}")
        if len(items) > args.limit:
            out(f"  … {len(items) - args.limit} more (--limit {len(items)} to see all, or --json)")
    head("what now")
    ui.columns([
        ("thumbs --rebuild", "missing or stale thumbnails and previews"),
        ("thumbs --prune", "generated files with no source photo"),
        ("scan [--force]", "unindexed files, stale index (--force ignores mtimes)"),
        ("featured --recompute", "flags that drifted away from album.cfg"),
    ])
    print()
    kv("total", f"{total} problem(s) found")
    return 1


def cmd_thumbs(args) -> int:
    """Inspect, rebuild and prune in this process. The console queues the
    same two writes as jobs for the indexer (ops.JOBS); the work itself is
    ops.rebuild_derivatives / ops.prune_derivatives either way."""
    db.conn()
    report = ops.derivatives_report(args.album, rebuild_all=args.all)
    album, orphans = report["scope"], report["orphans"]

    built = failed = pruned = 0
    if args.rebuild:
        live = ui.Live("building", enabled=not args.json)
        try:
            res = ops.rebuild_derivatives(album, args.all,
                                          progress=lambda label, done, total:
                                              live.progress(done, total, label))
        finally:
            live.done()
        built, failed = res["built"], res["failed"]
        for item in res["broken"]:
            ui.warn(f"  failed: {item}")
    if args.prune and args.apply and album is None:
        res = ops.prune_derivatives()
        pruned = res["pruned"]
        for error in res["errors"]:
            out(f"  could not delete {error}")

    if args.json:
        dump({"scope": album, "photos": report["photos"], "to_build": report["to_build"],
              "built": built, "failed": failed,
              "orphans": orphans, "pruned": pruned,
              "applied": bool(args.apply)})
        return 0

    todo = report["to_build"]
    kv("scope", album or "whole gallery")
    kv("photos", str(report["photos"]))
    kv("to build", f"{todo} derivative(s)" + (" (--all: rebuilding everything)" if args.all else ""))
    kv("orphans", f"{len(orphans)} generated file(s) without a source photo"
                  f" · {ops.bytes_h(report['orphan_bytes'])}"
                  if album is None else "not checked (album scope)")
    if args.rebuild:
        kv("built", f"{built} ok, {failed} failed")
    elif todo:
        kv("dry run", "add --rebuild to actually generate them")
    if args.prune:
        if args.apply:
            kv("pruned", f"{pruned} file(s) deleted")
        else:
            head("orphans")
            for f in orphans[:args.limit]:
                out(f"  {f}")
            if len(orphans) > args.limit:
                out(f"  … {len(orphans) - args.limit} more")
            kv("dry run", "add --apply to actually delete these")
    return 0


def cmd_featured(args) -> int:
    db.conn()
    if args.recompute:
        albums.recompute_featured()
    report = ops.featured_report(args.album)
    by_album, unresolved = report["albums"], report["unresolved"]
    flagged = set(report["flagged"])
    drift_missing = report["drift"]["not_flagged"]
    drift_extra = report["drift"]["flagged_without_rule"]
    showcase_albums = report["showcase_albums"]

    if args.json:
        dump({**{k: v for k, v in report.items() if k != "flagged"},
              "recomputed": bool(args.recompute)})
        return 0

    kv("featured", f"{report['expected']} photo(s) from {len(by_album)} album.cfg file(s)")
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
        kv("fix", "`featured --recompute`, or any scan")
    return 1 if (unresolved or drift_missing or drift_extra) else 0


def cmd_disk(args) -> int:
    """What the generated trees cost on disk, and how full their volumes are."""
    report = ops.disk_usage()
    if args.json:
        dump(report)
        return 0
    originals = report["originals"]["bytes"]
    for tier in report["tiers"]:
        if not tier["exists"]:
            kv(tier["key"], ui.state("not there", "idle") + f" · {tier['path']}")
            continue
        per = f" · {ops.bytes_h(tier['per_photo'])}/photo" if tier["per_photo"] else ""
        kv(tier["key"], f"{tier['files']} file(s) · {ops.bytes_h(tier['bytes'])}{per}")
        kv("", tier["path"])
        for ext in tier["stale_formats"]:
            old = tier["formats"][ext]
            kv("", ui.state(f"{old['files']} {ext} file(s), {ops.bytes_h(old['bytes'])} "
                            f"— a format this tier no longer writes", "warn"))
    head("against the originals")
    ratio = report["ratio"]
    kv("generated", f"{ops.bytes_h(report['derivatives']['bytes'])}"
                    + (f" · {ratio * 100:.1f}% of the originals" if ratio is not None else ""))
    kv("originals", f"{report['originals']['files']} photo(s) · {ops.bytes_h(originals)}")
    kv("database", ops.bytes_h(report["database"]["bytes"]))
    head("volumes")
    for vol in report["volumes"]:
        used = vol["used"] / vol["total"] * 100 if vol["total"] else 0
        kv(", ".join(vol["holds"]), f"{ops.bytes_h(vol['free'])} free of "
                                    f"{ops.bytes_h(vol['total'])} · {used:.0f}% used",
           ui.C.ye if used >= 90 else "")
    if any(t["stale_formats"] for t in report["tiers"]):
        kv("fix", "`thumbs --prune` lists the old-format files, `--apply` deletes them")
    hint(f"  walked in {report['took_ms']} ms")
    return 0


def cmd_passwd(args) -> int:
    """Set, change or clear the console's operator password.

    The password is never an argument: argv lands in shell history, in `ps`
    output and in a container's inspect JSON. It comes from a terminal prompt
    that does not echo, or from stdin for the scripted case:

        python -m aperture.cli passwd
        printf '%s' "$SECRET" | python -m aperture.cli passwd --stdin
        python -m aperture.cli passwd --clear
    """
    from ..console import security   # aperture.console, not aperture.cli.console

    if args.clear:
        existed = security.clear_password()
        if args.json:
            dump({"ok": True, "cleared": existed, "password_set": False})
            return 0
        with _screen("console password"):
            if existed:
                out(f"{ui.C.ye}Password cleared.{ui.C.off}")
                out("")
                out("The console now runs OPEN wherever it is allowed to run at all —")
                out("loopback, or CONSOLE_ALLOW_OPEN=1. Anywhere else it will refuse")
                out("to start until a password is set again.")
            else:
                out("No password was set.")
        return 0

    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
        confirm = password
    else:
        import getpass
        try:
            password = getpass.getpass("New console password: ")
            confirm = getpass.getpass("Again: ")
        except (EOFError, KeyboardInterrupt):
            out("")
            return 1

    if password != confirm:
        out(f"{ui.C.rd}The two entries do not match.{ui.C.off}")
        return 1
    try:
        path = security.set_password(password)
    except ValueError as exc:
        out(f"{ui.C.rd}{exc}{ui.C.off}")
        return 1

    if args.json:
        dump({"ok": True, "password_set": True, "file": str(path)})
        return 0
    with _screen("console password"):
        out(f"{ui.C.gn}Password set.{ui.C.off}")
        out("")
        kv("stored in", str(path))
        kv("hash", "scrypt (n=%d, r=%d, p=%d)"
           % (security.SCRYPT["n"], security.SCRYPT["r"], security.SCRYPT["p"]))
        out("")
        out("Every open console session was ended. The console now asks for this")
        out("password wherever it listens.")
    return 0
