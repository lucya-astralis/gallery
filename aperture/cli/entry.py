"""The command line itself: the parser, the presentation flags, main().

One place that knows every command, so `--help` and the menu cannot disagree
about what exists.
"""

import argparse
import sys

from .. import brand, i18n, termui as ui
from ..ops import UnknownAlbum

from .operate import cmd_disk, cmd_doctor, cmd_featured, cmd_passwd, cmd_pause, cmd_resume, cmd_scan, cmd_status, cmd_thumbs
from .render import fail
from .reports import cmd_album, cmd_cfg, cmd_export, cmd_gps, cmd_i18n, cmd_photo, cmd_search, cmd_tags, cmd_trip, cmd_welcome
from .screens import cmd_dash, cmd_help, cmd_home, cmd_menu, cmd_term


# ----- argument parsing -------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m aperture.cli",
        description="Operator CLI for the gallery backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run without arguments for the dashboard and the interactive menu.\n"
               "Server control (status/scan/pause/resume) goes through the flag files in\n"
               "DATA_DIR/control — there is no control HTTP endpoint, by design.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_text, **kwargs):
        sp = sub.add_parser(name, help=help_text, description=help_text, **kwargs)
        sp.set_defaults(func=func)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        # The four below are consumed by _take_presentation_flags before
        # parsing (so they also work in front of the command); they are
        # declared here purely so `<command> --help` lists them.
        sp.add_argument("--no-color", action="store_true", help="plain output, no ANSI")
        sp.add_argument("--color", action="store_true",
                        help="force colour even when stdout is not a terminal")
        sp.add_argument("-i", "--interactive", action="store_true",
                        help="force the prompts on when terminal detection fails")
        sp.add_argument("--logo", choices=LOGO_MODES, default="ascii",
                        help="masthead: letterforms (default), a picture protocol, or nothing")
        return sp

    sp = add("dash", cmd_dash, "Masthead, live state and archive statistics on one screen.")
    sp.add_argument("--watch", action="store_true", help="repaint on a timer until ctrl-c")
    sp.add_argument("--interval", type=float, default=5.0,
                    help="seconds between repaints with --watch (default: 5)")
    add("menu", cmd_menu, "Interactive console (needs a terminal).")
    add("help", cmd_help, "Command overview with the usage cheat sheet.")
    add("term", cmd_term, "What this terminal supports, and why colour / the menu are off.")

    sp = add("passwd", cmd_passwd,
             "Set, change or clear the console's operator password.")
    sp.add_argument("--stdin", action="store_true",
                    help="read the password from stdin instead of prompting")
    sp.add_argument("--clear", action="store_true",
                    help="remove the password; the console then only starts "
                         "on loopback or with CONSOLE_ALLOW_OPEN=1")

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

    add("disk", cmd_disk, "What thumbnails, previews and HEIC conversions cost on disk, "
                          "against the originals, and how full each volume is.")

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

    sp = add("tags", cmd_tags, "Per-photo tags: the vocabulary, and `.tags` sidecar vs index drift.")
    sp.add_argument("tag", nargs="?", help="one tag — list the photos carrying it")
    sp.add_argument("--album", help="limit to an album and its sub-albums")
    sp.add_argument("--limit", type=int, default=40, help="rows per group (default: 40)")

    sp = add("welcome", cmd_welcome, "What the welcome hero resolves to, per device class.")
    sp.add_argument("--desktop", dest="desktop_only", action="store_true",
                    help="only the desktop feed")
    sp.add_argument("--mobile", dest="mobile_only", action="store_true",
                    help="only the mobile feed")
    sp.add_argument("--limit", type=int, default=30, help="entries shown (default: 30)")

    sp = add("gps", cmd_gps, "Which originals still carry coordinates "
                             "(--strip REWRITES those files).")
    sp.add_argument("album", nargs="?", help="album (blank = whole gallery)")
    sp.add_argument("--strip", action="store_true",
                    help="remove the GPS block from every hit, in place")
    sp.add_argument("--limit", type=int, default=40, help="rows shown (default: 40)")

    sp = add("album", cmd_album, "One album in full, or the list of them.")
    sp.add_argument("album", nargs="?", help="album (blank = list every album)")
    sp.add_argument("--limit", type=int, default=40, help="rows shown (default: 40)")

    sp = add("search", cmd_search, "Run the /search query from the terminal.")
    sp.add_argument("query", nargs="?", help="matches album name, file name and tag")
    sp.add_argument("--album", help="limit to an album and its sub-albums")
    sp.add_argument("--limit", type=int, default=40, help="rows shown (default: 40)")

    sp = add("export", cmd_export, "Archive gallery.cfg and every .album/ folder "
                                   "(writes the archive, reads photos/).")
    sp.add_argument("--out", help="archive path (default: ./gallery-config-<stamp>.tar.gz)")
    sp.add_argument("--list", action="store_true", help="show what would go in, write nothing")
    sp.add_argument("--force", action="store_true", help="overwrite an existing archive")
    sp.add_argument("--limit", type=int, default=40, help="rows shown with --list (default: 40)")

    return p


LOGO_MODES = ("auto", "kitty", "iterm", "blocks", "ascii", "off")


# Commands whose output is wrapped in the shared frame by the dispatcher.
# The rest draw their own screen (see _screen) or are pure JSON.
FRAMED_COMMANDS = {"scan", "pause", "resume", "doctor", "thumbs", "featured", "disk",
                   "passwd",
                   "cfg", "photo", "trip", "i18n",
                   "tags", "welcome", "gps", "album", "search", "export"}


def _take_presentation_flags(argv: list[str]):
    """Pull the look-and-feel flags out of argv wherever they sit.

    They are not really per-command options: they configure the console
    itself. Handling them here makes `--logo blocks status` work as well as
    `status --logo blocks`, lets the bare `python -m aperture.cli` take them,
    and — because they are applied to the console module rather than to one
    parsed namespace — keeps them in effect for the commands the menu starts
    afterwards.
    """
    rest: list[str] = []
    flags = {"color": False, "no_color": False, "interactive": False, "logo": None}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--color":
            flags["color"] = True
        elif arg == "--no-color":
            flags["no_color"] = True
        elif arg in ("-i", "--interactive"):
            flags["interactive"] = True
        elif arg == "--logo" and i + 1 < len(argv):
            flags["logo"] = argv[i + 1]
            i += 1
        elif arg.startswith("--logo="):
            flags["logo"] = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1
    return rest, flags


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, flags = _take_presentation_flags(argv)
    if flags["logo"] is not None:
        if flags["logo"] not in LOGO_MODES:
            return fail(f"--logo must be one of {', '.join(LOGO_MODES)}")
        ui.set_logo_mode(flags["logo"])
    if flags["interactive"]:
        ui.force_interactive(True)
    if not argv:
        ui.init_color(False if flags["no_color"] else (True if flags["color"] else None))
        try:
            return cmd_home()
        except KeyboardInterrupt:
            print()
            return 130
    args = build_parser().parse_args(argv)
    # JSON never carries escape codes; --color / --no-color are the manual
    # overrides for when the detection gets it wrong (see `term`).
    if args.json or flags["no_color"]:
        ui.init_color(False)
    else:
        ui.init_color(True if flags["color"] else None)
    try:
        # Reports share the screens\' frame, so the whole CLI reads as one
        # interface. `dash`/`menu`/`help`/`term`/`status` draw their own (they
        # repaint, or nest a body), and --json output must stay plain.
        if args.command in FRAMED_COMMANDS and not args.json:
            with ui.screen(brand.PRODUCT, args.command):
                return args.func(args) or 0
        return args.func(args) or 0
    except UnknownAlbum as exc:
        return fail(f"no such album: {exc.args[0]!r} — `album` lists them")
    except KeyboardInterrupt:
        print()
        return 130
    except BrokenPipeError:  # `| head`
        return 0
