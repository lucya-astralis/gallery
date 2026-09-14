"""The full-screen surfaces: the dashboard, the menu, help, term, home.

Everything here paints a whole screen rather than a report, and everything
here is optional -- each is a view onto commands that exist on their own.
`menu` runs those commands; it reaches main() through _dispatch(), late, so
that entry.py can import this module to build the parser.
"""

import argparse
import time

from .. import brand, control, db, ops, reports, termui as ui
from ..gallery import api

from .render import _render_system, _screen, dump, head, hint, kv


def _dispatch(argv: list[str]) -> int:
    """Run one command the way the shell would.

    Imported late on purpose: entry.py imports THIS module to build the
    parser, so the way back can only ever be a call, never an import."""
    from .entry import main
    return main(argv)


# ----- dashboard / menu / help -----------------------------------------
# `python -m aperture.cli` with no arguments lands here: the masthead, what the
# server is doing, and what the archive currently holds. On a terminal it
# then drops into the menu; piped or redirected it just prints and exits.
SUBTITLE_FMT = "{title}  ·  CLI v{app}  ·  API v{api}"


def cmd_dash(args) -> int:
    if args.json:
        st, live = ops.server_status()
        report = reports.archive_report()
        dump({**{k: report[k] for k in ("index", "span", "albums", "months", "formats")},
              "server": st, "live": live,
              "paused": control.pause_info() is not None})
        return 0
    if getattr(args, "watch", False):
        return _watch_dash(args)
    _render_dash()
    return 0


def _render_dash(footer: bool = True) -> None:
    """The dashboard as its own screen."""
    with _screen("ops console"):
        _dash_body(footer)


def _watch_dash(args) -> int:
    """Repaint the dashboard on a timer — the closest thing to a live view of
    what the indexer is up to. Ctrl-C leaves it."""
    if not ui.ansi():
        # Without cursor control every refresh would append another full
        # dashboard — an endless log instead of a live view.
        ui.warn("--watch needs a terminal that can repaint; printing once instead")
        _render_dash()
        return 1
    interval = max(1.0, float(getattr(args, "interval", 5.0) or 5.0))
    ui.hide_cursor()
    try:
        while True:
            ui.clear_screen()
            _render_dash(footer=False)
            countdown = ui.Live("live · ctrl-c to stop · next refresh in")
            end = time.time() + interval
            while time.time() < end:
                countdown.tick(f"{max(0, end - time.time()):.0f}s")
                time.sleep(0.2)
            countdown.done()
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        ui.show_cursor()


def _dash_body(footer: bool = True) -> None:
    """The dashboard content — drawn inside whatever frame is already open,
    so the menu can lead with it without nesting a second box."""
    st, live = ops.server_status()
    pause = control.pause_info()
    report = reports.archive_report()
    counts, span = report["index"], report["span"]

    ui.logo(SUBTITLE_FMT.format(title=brand.PRODUCT.upper(),
                                app=brand.VERSION, api=api.API_VERSION))
    ui.rule("system")
    _render_system(st, live, pause)

    head("archive")
    kv("photos", f"{counts['images']:,}".replace(",", " "))
    kv("albums", f"{counts['albums']} with photos · {report['album_nodes']} incl. parents")
    kv("featured", f"{counts['featured']} photo(s) · "
                   f"{report['showcase_albums']} showcase album(s)")
    kv("tags", str(counts["tags"]))
    kv("originals", ops.bytes_h(counts["bytes"]))
    kv("span", f"{(span['from'] or '—')[:10]} → {(span['to'] or '—')[:10]}")
    kv("database", ops.bytes_h(counts["db_bytes"]))

    largest = report["albums"][:6]
    if largest:
        head("largest albums")
        peak = largest[0]["n"]
        name_w = min(28, max(len(r["album"]) for r in largest))
        for r in largest:
            name = r["album"]
            if len(name) > name_w:
                name = "…" + name[-(name_w - 1):]
            print(f"  {name:<{name_w}}  {ui.bar(r['n'], peak, 22)} "
                  f"{ui.C.bold}{r['n']:>5}{ui.C.off} {ui.C.gy}{ops.bytes_h(r['bytes'])}{ui.C.off}")

    months = report["months"]
    if months:
        head("activity (by capture month)")
        peak = max(r["n"] for r in months)
        for r in months:
            print(f"  {r['ym']}   {ui.bar(r['n'], peak, 30, ui.C.mg)} "
                  f"{ui.C.bold}{r['n']:>5}{ui.C.off}")

    formats = report["formats"]
    if formats:
        head("formats")
        kv("types", " · ".join(f"{ext} {n}" for ext, n in formats[:6]))
    kv("heic/heif", ui.state("supported") if report["heic"]
       else ui.state("NOT supported — pillow-heif is missing", "warn"))

    head("health")
    on_disk = report["photos_on_disk"]
    if on_disk is not None:
        tiers = {t["key"]: t for t in ops.disk_usage()["tiers"]}
        drift = on_disk - counts["images"]
        if drift == 0:
            kv("index", f"{ui.state('in sync')} · {on_disk} file(s) on disk = {counts['images']} row(s)")
        else:
            what = "not indexed" if drift > 0 else "indexed but gone"
            kv("index", f"{ui.state(f'{abs(drift)} file(s) {what}', 'warn')}"
                        f" · {on_disk} on disk / {counts['images']} indexed")
        kv("cache", f"{tiers['thumbnails']['files']} thumb(s) {ops.bytes_h(tiers['thumbnails']['bytes'])} · "
                    f"{tiers['previews']['files']} preview(s) {ops.bytes_h(tiers['previews']['bytes'])}")
        hint("  a full check (config, derivatives, drift) is `doctor`, the space is `disk`")
    else:
        hint(f"  skipped — over {reports.QUICK_CHECK_MAX_ROWS} rows; run `doctor` for the full check")

    if footer:
        head("commands")
        _command_columns()
        print()
        hint("  <command> --help   ·   `menu` for the console   ·   `dash --watch` for a live view")


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
    ("disk", "what thumbnails and previews cost on disk", []),
    ("featured", "which album.cfg entry features which photo",
     [("arg", "album", "album (blank = all)")]),
    ("cfg", "album.cfg / gallery.cfg as the app parses it",
     [("arg", "album", "album (blank = gallery.cfg)")]),
    ("photo", "one photo in full", [("arg!", "rel_path", "rel_path")]),
    ("album", "one album in full, or the list of them",
     [("arg", "album", "album (blank = list every album)")]),
    ("trip", "trip dashboard", [("arg", "album", "album (blank = list trips)")]),
    ("welcome", "what the welcome hero resolves to", []),
    ("tags", "per-photo tags, and sidecar vs index drift",
     [("arg", "tag", "tag (blank = the whole vocabulary)"),
      ("opt", "--album", "album (blank = whole gallery)")]),
    ("search", "run the /search query", [("arg!", "query", "search for")]),
    ("gps", "originals still carrying coordinates",
     [("arg", "album", "album (blank = whole gallery)"),
      ("flag", "--strip", "remove the coordinates from every hit? [y/N]")]),
    ("export", "archive gallery.cfg and every .album/",
     [("flag", "--list", "only list what would go in? [y/N]")]),
    ("i18n", "EN/DE/JP completeness + app.js mirror", []),
    ("dash", "redraw this dashboard", []),
]


def _ask(prompt: str) -> str:
    try:
        return ui.read_line(f"{ui.C.cy}  {prompt}{ui.C.off} {ui.C.gy}›{ui.C.off} ")
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


def _menu_status_line() -> None:
    """One live line above the menu: is the server up, is it paused, is a scan
    running, how big is the index. Re-read on every repaint."""
    st, live = ops.server_status()
    pause = control.pause_info()
    counts = ops.index_counts(db.conn())
    if not live:
        server = ui.state("server down", "idle")
    elif st.get("scanning"):
        server = ui.state(f"scanning ({st.get('scan_trigger')})", "warn")
    else:
        server = ui.state("server up", "ok")
    indexer = ui.state("PAUSED", "warn") if pause else ui.state("indexing", "ok")
    queued = (st or {}).get("watcher", {}).get("pending", 0) if live else 0
    queue = f" · {ui.state(f'{queued} queued', 'warn')}" if queued else ""
    print(f"  {server} · {indexer}{queue} · "
          f"{ui.C.bold}{counts['images']}{ui.C.off} photos · "
          f"{counts['albums']} albums · {counts['featured']} featured")


def cmd_menu(args) -> int:
    """Interactive console. Without a terminal it prints the overview instead
    of prompting — a question nobody can answer would hang a pipe or a cron
    job. `--interactive` overrides the detection, `term` explains it."""
    if not ui.interactive():
        ui.warn("no terminal detected — printing the command overview instead")
        _command_columns()
        print()
        hint("  `python -m aperture.cli term` shows what was detected")
        hint("  `python -m aperture.cli menu --interactive` forces the menu anyway")
        return 1
    db.conn()
    last: list[str] | None = None
    intro = bool(getattr(args, "intro", False))
    while True:
        if not intro:
            ui.clear_screen()
        with _screen("menu"):
            if intro:
                # entered by plain `python -m aperture.cli`: lead with the whole
                # dashboard, with the menu as the last section of the screen
                intro = False
                _dash_body(footer=False)
            else:
                ui.logo(SUBTITLE_FMT.format(title=brand.PRODUCT.upper(),
                                            app=brand.VERSION,
                                            api=api.API_VERSION))
                _menu_status_line()
            head("menu")
            ui.columns([(str(i), f"{ui.C.bold}{name:<9}{ui.C.off}{ui.C.gy}{desc}{ui.C.off}")
                        for i, (name, desc, _) in enumerate(MENU_ITEMS, 1)])
            print()
            keys = []
            if last:
                keys.append(f"{ui.C.mg}↵{ui.C.off} repeat {ui.C.bold}{' '.join(last)}{ui.C.off}")
            keys += [f"{ui.C.mg}r{ui.C.off} redraw",
                     f"{ui.C.mg}w{ui.C.off} live dashboard",
                     f"{ui.C.mg}h{ui.C.off} help",
                     f"{ui.C.mg}q{ui.C.off} quit"]
            print("  " + f"{ui.C.gy} · {ui.C.off}".join(keys))
        try:
            choice = _ask("select").lower()
        except KeyboardInterrupt:
            print()
            return 0
        if choice in ("q", "quit", "exit"):
            return 0
        if choice == "r":
            continue
        argv: list[str] | None
        if choice == "":
            if not last:
                continue
            argv = last
        elif choice == "h":
            argv = ["help"]
        elif choice == "w":
            argv = ["dash", "--watch"]
        else:
            if choice.isdigit() and 1 <= int(choice) <= len(MENU_ITEMS):
                item = MENU_ITEMS[int(choice) - 1]
            else:
                item = next((m for m in MENU_ITEMS if m[0] == choice), None)
            if item is None:
                ui.warn("  no such entry")
                time.sleep(0.8)
                continue
            argv = _menu_argv(item[0], item[2])
            if argv is None:
                time.sleep(0.8)
                continue
        last = argv
        ui.clear_screen()
        print()
        started = time.time()
        code = 0
        try:
            code = _dispatch(argv)
        except SystemExit as e:  # argparse complained; it already said why
            code = e.code if isinstance(e.code, int) else 1
        except KeyboardInterrupt:
            print()
            ui.warn("  interrupted")
        except Exception as e:  # a broken command must not kill the console
            ui.error(f"  {type(e).__name__}: {e}")
            code = 1
        print()
        ui.rule()
        verdict = ui.state("ok", "ok") if not code else ui.state(f"exit {code}", "warn")
        hint(f"  {' '.join(argv)} · {verdict} · {ops.dur(time.time() - started)}")
        try:
            _ask("enter to return")
        except KeyboardInterrupt:
            return 0


def cmd_help(args) -> int:
    with _screen("help"):
        _help_body()
    return 0


def _help_body() -> None:
    ui.logo(SUBTITLE_FMT.format(title=brand.PRODUCT.upper(),
                                app=brand.VERSION, api=api.API_VERSION))
    ui.rule("usage")
    ui.columns([
        ("python -m aperture.cli", "dashboard, then the interactive menu"),
        ("python -m aperture.cli <cmd>", "run one command"),
        ("… <cmd> --help", "options of that command"),
        ("… <cmd> --json", "machine-readable output"),
        ("--logo blocks", "draw the real logo as a picture (see `term`)"),
    ])
    head("commands")
    _command_columns()
    head("control channel")
    hint(f"  the server is driven through flag files in {control.control_dir()}")
    hint("  status.json (server) · paused.json + scan.request.json (this CLI)")
    hint("  there is no control HTTP endpoint — the web surface stays read-only")


_PROTOCOL_NAMES = {
    "kitty": "kitty graphics protocol (pixel-perfect)",
    "iterm": "iTerm2 inline images (pixel-perfect)",
    "blocks": "24-bit half-blocks (two pixels per cell)",
    "none": "no pictures — ASCII letterforms instead",
}


def _picture_verdict(report: dict) -> str:
    protocol = report["images"]
    text = _PROTOCOL_NAMES.get(protocol, protocol)
    if report["logo_mode"] == "ascii":
        suffix = f"{text}, but the masthead is text by default"
        return ui.state(suffix, "idle") if protocol != "none" else ui.state(text, "idle")
    if protocol == "none":
        return ui.state(text, "warn")
    if report["logo_png"] is None and protocol != "blocks":
        return ui.state(f"{text} — but the PNG is missing, run "
                        "`python tools/render_logo.py`", "warn")
    return ui.state(text, "ok")


def cmd_term(args) -> int:
    """Why this terminal is (not) getting colours and a menu. The answer to
    "works on my machine, not on the server"."""
    report = ui.term_report()
    if args.json:
        dump(report)
        return 0
    with _screen("terminal"):
        _term_body(report)
    return 0


def _term_body(report: dict) -> None:
    # the frame is already captioned TERMINAL
    ui.columns([(key, "—" if report.get(key) is None else str(report.get(key)))
                for key in ("platform", "os.name", "stdout.isatty", "stdin.isatty",
                            "mintty_out", "mintty_in", "dev_tty", "TERM", "COLORTERM",
                            "MSYSTEM", "TERM_PROGRAM", "NO_COLOR", "FORCE_COLOR",
                            "encoding", "columns")],
               key_tint=ui.C.gy)
    head("verdict")
    ui.columns([
        ("colour", ui.state("on", "ok") if report["color"] else ui.state("off", "warn")),
        ("menu", ui.state("available", "ok") if report["interactive"]
         else ui.state("not available", "warn")),
        ("repaint", ui.state("yes", "ok") if report["ansi"] else ui.state("no", "warn")),
        ("pictures", _picture_verdict(report)),
    ], key_tint=ui.C.gy)
    if not report["color"] or not report["interactive"]:
        head("what to do")
        if not report["stdout.isatty"] and not report["mintty_out"]:
            hint("  stdout is not a terminal — output is piped or redirected.")
            hint("  In Docker use `docker compose exec` (it allocates a TTY);")
            hint("  `-T` explicitly disables it. Over SSH use `ssh -t`.")
        if report["NO_COLOR"] is not None:
            hint("  NO_COLOR is set in this environment — that disables colour by design.")
        hint("  Force it: FORCE_COLOR=1, or --color / --interactive on any command.")
    if report["images"] == "none" and report["ansi"]:
        head("pictures")
        hint("  No picture protocol detected. Terminals that can:")
        hint("    kitty, ghostty          -> kitty graphics protocol")
        hint("    iTerm2, WezTerm         -> inline images")
        hint("    anything with 24-bit colour (Windows Terminal, mintty, VS Code,")
        hint("    gnome-terminal, …)      -> `--logo blocks`")
        hint("  Force one with --logo kitty|iterm|blocks; `--logo ascii` keeps the")
        hint("  letterforms, `--logo off` drops the masthead entirely.")


def cmd_home() -> int:
    """No arguments: dashboard plus menu when someone is watching, dashboard
    alone when this is a pipe or a log."""
    args = argparse.Namespace(json=False, no_color=False, color=False,
                              interactive=False, watch=False, interval=5.0,
                              intro=True, logo="ascii")
    if ui.interactive():
        return cmd_menu(args)
    cmd_dash(args)
    return 0
