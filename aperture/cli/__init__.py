"""Operator CLI for the gallery backend.

    python -m aperture.cli <command> [options]
    docker compose exec aperture python -m aperture.cli <command>

Two kinds of command live in here:

  * Ones that talk to the RUNNING server -- `status`, `scan`, `pause`,
    `resume`. They go through the flag-file channel in `DATA_DIR/control`
    (see aperture/control.py), because the HTTP surface is read-only by design
    and is going to stay that way.
  * Ones that just look at the index, the photo tree and the config the same
    way the app does -- `doctor`, `thumbs`, `featured`, `cfg`, `photo`,
    `trip`, `i18n`. Those run standalone and need no server at all; they
    import the gallery's own modules to reuse their resolution helpers, so
    what they report is what the pages actually render.

Every command takes `--json` for a machine-readable dump. `doctor` exits
non-zero when it found something, so it works as a cron / CI check.

Nothing in here writes to `photos/` -- the originals stay untouched. The
commands that DO write are marked in their help: they touch the SQLite index
(`scan`, `featured --recompute`) or the generated thumbnail/preview files
(`thumbs --rebuild`, `thumbs --prune --apply`).

The file this used to be was 1 982 lines. It is now five, by what a command
DOES rather than by what it is about:

    render.py    the terminal vocabulary every command writes with
    operate.py   the commands that act on the indexer and the derivatives
    reports.py   the commands that only look
    screens.py   the full-screen surfaces (dash, menu, help, term, home)
    entry.py     the parser and main()
"""

import logging

# Importing the app emits its own startup notes (a missing pillow-heif, say).
# Those would land above the masthead, so they are muted here and reported by
# the dashboard instead -- anything a command logs while running still shows.
# One place for it, because this import pulls in every module below.
logging.disable(logging.CRITICAL)
from .entry import build_parser, main  # noqa: E402
logging.disable(logging.NOTSET)

__all__ = ["build_parser", "main"]
