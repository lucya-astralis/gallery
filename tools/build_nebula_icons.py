"""Rebuild the Nebula icon sheet, the twin of fa-icons.css.

    aperture/gallery/static/nebula-icons.css    (stylesheet, generated)

With NEBULA_FACES=1 the pages link this sheet instead of fa-icons.css (see
base.html and the console's two templates). The markup does not change: it
keeps writing <i class="fa fa-camera">, and this sheet points the same `.fa`
and `.fa-<name>` classes at the Nebula Icons font instead of the Font Awesome
subset. So both sets cover exactly the same names, and switching is a choice
of one <link>.

Nebula Icons is drawn and built in the nebula repo (assets/fonts/build.py),
which also writes the name -> codepoint map. Copy both here:

    nebula/assets/fonts/NebulaIcons.woff2   -> aperture/gallery/static/fonts/
    nebula/assets/fonts/nebula-icons.json   -> tools/

then run

    python tools/build_nebula_icons.py

The names come from the same scan as tools/build_fa_subset.py. A Font Awesome
name used anywhere that has no entry in FA_TO_NEBULA below is a hard error,
not a blank box in production: draw the icon in nebula, or map it here.

Requires fonttools + brotli (pip install fonttools brotli).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_fa_subset import ROOT, collect_names  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FONT = ROOT / "aperture" / "gallery" / "static" / "fonts" / "NebulaIcons.woff2"
OUT_CSS = ROOT / "aperture" / "gallery" / "static" / "nebula-icons.css"
ICON_MAP = Path(__file__).resolve().parent / "nebula-icons.json"

# Font Awesome's name (what the markup says) -> Nebula's (what it is for).
# Several Font Awesome shapes are one Nebula icon: Nebula has no circles, so
# a circle-<x> is the cut box, and a trash-can is the trash.
FA_TO_NEBULA = {
    "align-left": "text", "angles-down": "angles-down", "angles-up": "angles-up",
    "arrow-down": "arrow-down", "arrow-down-wide-short": "sort", "arrow-right": "arrow-right",
    "arrow-turn-up": "turn-up", "arrow-up": "arrow-up", "arrow-up-right-from-square": "external",
    "arrows-rotate": "refresh", "box-archive": "archive", "calendar-day": "calendar",
    "camera": "camera", "chart-simple": "chart", "check": "check",
    "chevron-down": "chevron-down", "chevron-left": "chevron-left",
    "chevron-right": "chevron-right", "chevron-up": "chevron-up", "circle": "point",
    "circle-check": "ok", "circle-exclamation": "alert", "circle-info": "info",
    "circle-up": "box-up", "clock-rotate-left": "history", "code-branch": "branch",
    "code-merge": "merge", "copy": "copy", "database": "database", "download": "download",
    "eraser": "eraser", "expand": "expand", "eye": "eye", "eye-slash": "eye-off",
    "file": "file", "file-code": "file-code", "film": "film", "filter": "filter",
    "floppy-disk": "save", "folder": "folder", "folder-open": "folder-open",
    "folder-tree": "tree", "gauge": "gauge", "globe": "globe", "grip-vertical": "grip",
    "hammer": "hammer", "hard-drive": "drive", "heading": "heading", "house": "home",
    "id-card": "id-card", "image": "image", "images": "images", "key": "key",
    "keyboard": "keyboard", "layer-group": "layers", "link": "link", "link-slash": "link-off",
    "list": "list", "list-check": "checklist", "location-dot": "pin", "lock": "lock",
    "lock-open": "unlock", "magnifying-glass": "search", "microchip": "chip",
    "palette": "palette", "pause": "pause", "pen": "edit", "play": "play", "plus": "plus",
    "right-from-bracket": "sign-out", "right-to-bracket": "sign-in", "rotate": "refresh",
    "rotate-left": "rotate-left", "rotate-right": "rotate-right", "route": "route",
    "scale-balanced": "balance", "screwdriver-wrench": "settings", "scroll": "scroll",
    "server": "server", "shield-halved": "shield", "shoe-prints": "steps",
    "shuffle": "shuffle", "sliders": "sliders", "star": "star", "stethoscope": "health",
    "table-cells": "grid", "tag": "tag", "tags": "tags", "trash": "trash",
    "trash-can": "trash", "triangle-exclamation": "warning", "upload": "upload",
    "user-pen": "user-edit", "xmark": "close",
}

CSS_HEADER = """/* ==================================================================
   NEBULA ICONS  —  GENERATED FILE, DO NOT EDIT BY HAND
   ==================================================================
   Rebuild with:  python tools/build_nebula_icons.py
   Linked instead of fa-icons.css when NEBULA_FACES=1. The class names
   are Font Awesome's, so the markup is the same under either set; each
   one points at the Nebula Icons glyph drawn for the same job.

   Nebula Icons: drawn for the lucya.systems design language (nebula,
   assets/fonts/build.py) — line icons on an 8 x 8 grid, square ends,
   corners cut, the stroke Nebula Mono is drawn with.
   ================================================================== */
@font-face{
  font-family:'Nebula Icons';
  src:url('/static/fonts/NebulaIcons.woff2') format('woff2');
  font-weight:400;
  font-style:normal;
  /* block, not swap: there is no sensible fallback glyph for an icon font,
     and swap would flash the raw private-use codepoint as tofu first */
  font-display:block;
}
.fa{
  font-family:'Nebula Icons';
  font-weight:400;
  font-style:normal;
  font-variant:normal;
  line-height:1;
  display:inline-block;
  text-rendering:auto;
  -webkit-font-smoothing:antialiased;
  /* icons sit beside mono labels that are tracked out; the glyph must not
     inherit that letter-spacing or it drifts off its own box */
  letter-spacing:0;
}
.fa::before{ content:var(--fa); }
/* fixed-width variant: keeps icons in a column (kv lists, menus) aligned
   however wide the individual glyph is */
.fa-fw{ width:1.25em; text-align:center; }

"""


def main() -> int:
    for path in (FONT, ICON_MAP):
        if not path.is_file():
            print(f"not found: {path}\n(copy it from the nebula repo, see the docstring)")
            return 1
    nebula = json.loads(ICON_MAP.read_text(encoding="utf-8"))
    used = collect_names()

    unmapped = sorted(n for n in used if n not in FA_TO_NEBULA)
    undrawn = sorted(n for n in used if n in FA_TO_NEBULA and FA_TO_NEBULA[n] not in nebula)
    if unmapped or undrawn:
        for n in unmapped:
            print(f"  fa-{n}: no Nebula icon mapped (used in {', '.join(sorted(used[n]))})")
        for n in undrawn:
            print(f"  fa-{n}: maps to '{FA_TO_NEBULA[n]}', which nebula-icons.json does not have")
        return 1

    from fontTools.ttLib import TTFont
    cmap = TTFont(FONT).getBestCmap()
    names = sorted(used)
    missing = [n for n in names if int(nebula[FA_TO_NEBULA[n]], 16) not in cmap]
    if missing:
        print("MISSING GLYPHS (NebulaIcons.woff2 older than nebula-icons.json?):", ", ".join(missing))
        return 1

    rules = "".join(f'.fa-{n}{{ --fa:"\\{nebula[FA_TO_NEBULA[n]]}"; }}\n' for n in names)
    OUT_CSS.write_text(CSS_HEADER + rules, encoding="utf-8", newline="\n")
    print(f"ok: {OUT_CSS.name}, {len(names)} icons onto "
          f"{len({FA_TO_NEBULA[n] for n in names})} Nebula glyphs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
