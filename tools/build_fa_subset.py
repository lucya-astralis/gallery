"""Rebuild the Font Awesome glyph subset + its stylesheet.

    app/static/fonts/fa-solid-subset.woff2   (font, generated)
    app/static/fa-icons.css                  (stylesheet, generated)

Font Awesome Free ships ~1400 solid icons in a 117 KB woff2. The site uses a
couple of dozen, so — exactly like the Noto Sans JP subset next door — only
the glyphs actually referenced get baked in, and the @font-face plus one
`--fa` rule per icon are emitted from the same scan. Run this whenever you
add, rename or remove an `fa-*` class anywhere:

    python tools/build_fa_subset.py

How an icon gets in:
  * write it in the markup as  <i class="fa fa-camera" aria-hidden="true"></i>
  * re-run this script.

The scan reads app/templates/*.html, app/static/app.js and app/main.py and
picks up every `fa-<name>` token. A name that Font Awesome doesn't know is a
hard error rather than a silently blank box — check the spelling against
tools/fa-icons.json (name -> codepoint, extracted from the upstream package).

Icons are decorative here: they always sit next to a real text label or an
aria-label, and carry aria-hidden="true". Nothing is icon-only.

Do NOT delete fa-solid-900.woff2 — that is the full upstream source this
subset is cut from, and there is no way to rebuild without it.

Font Awesome Free 7.3.1, https://fontawesome.com
  icons  CC BY 4.0 · fonts  SIL OFL 1.1 · code  MIT
  (full text in app/static/fonts/FONTAWESOME-LICENSE.txt)

Requires fonttools + brotli (pip install fonttools brotli).
"""

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "app" / "static" / "fonts"
SRC = FONT_DIR / "fa-solid-900.woff2"
OUT_FONT = FONT_DIR / "fa-solid-subset.woff2"
OUT_CSS = ROOT / "app" / "static" / "fa-icons.css"
ICON_MAP = Path(__file__).resolve().parent / "fa-icons.json"

# `fa` itself is the base class, not an icon; `fa-fw`/`fa-lg` are modifiers
# this sheet defines by hand. Anything else matching fa-<name> is an icon.
NOT_ICONS = {"fw", "lg", "sm", "xs", "solid", "regular", "brands"}

CSS_HEADER = """/* ==================================================================
   FONT AWESOME ICONS  —  GENERATED FILE, DO NOT EDIT BY HAND
   ==================================================================
   Rebuild with:  python tools/build_fa_subset.py
   The font is a glyph subset cut down to exactly the icons listed
   below; adding a class to a template without re-running the script
   renders a blank box.

   Font Awesome Free 7.3.1 (https://fontawesome.com)
   icons CC BY 4.0 · fonts SIL OFL 1.1 · code MIT
   ================================================================== */
@font-face{
  font-family:'Font Awesome Solid';
  src:url('/static/fonts/fa-solid-subset.woff2') format('woff2');
  font-weight:900;
  font-style:normal;
  /* block, not swap: there is no sensible fallback glyph for an icon font,
     and swap would flash the raw private-use codepoint as tofu first */
  font-display:block;
}
.fa{
  font-family:'Font Awesome Solid';
  font-weight:900;
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


def collect_names() -> dict:
    """Every fa-<name> token used in the site, mapped to the files using it."""
    files = sorted((ROOT / "app" / "templates").glob("*.html"))
    files += [ROOT / "app" / "static" / "app.js", ROOT / "app" / "main.py"]

    # class attributes only — a bare fa-* search over the whole file also
    # swallows the woff2 filename in the <link rel=preload>. Jinja tags inside
    # the attribute are fine, they sit within the same quotes:
    #   class="fa {% if x %}fa-shuffle{% else %}fa-star{% endif %}"
    attrs = re.compile(r"""class\s*=\s*["']([^"']*)["']""")
    # classList.add('fa-…') / className = '… fa-… ' in JS
    js_str = re.compile(r"""["']([^"'\n]*\bfa-[a-z0-9-]+[^"'\n]*)["']""")
    token = re.compile(r"\bfa-([a-z0-9]+(?:-[a-z0-9]+)*)\b")

    used: dict[str, set[str]] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  ! skipped {f}: {e}")
            continue
        chunks = attrs.findall(text)
        if f.suffix in (".js", ".py"):
            chunks += js_str.findall(text)
        for chunk in chunks:
            for name in token.findall(chunk):
                if name in NOT_ICONS:
                    continue
                used.setdefault(name, set()).add(f.name)
    return used


def main() -> int:
    if not SRC.is_file():
        print(f"source font not found: {SRC}\n"
              "(it is the full upstream fa-solid-900.woff2 — restore it from "
              "the @fortawesome/fontawesome-free package)")
        return 1
    if not ICON_MAP.is_file():
        print(f"icon map not found: {ICON_MAP}")
        return 1

    catalog = json.loads(ICON_MAP.read_text(encoding="utf-8"))
    used = collect_names()
    if not used:
        print("no fa-* classes found in the templates — nothing to build")
        return 1

    unknown = sorted(n for n in used if n not in catalog)
    if unknown:
        print("UNKNOWN ICON NAMES (typo? not in Font Awesome Free solid?):")
        for n in unknown:
            print(f"  fa-{n}  used in {', '.join(sorted(used[n]))}")
        return 1

    names = sorted(used)
    codepoints = {n: int(catalog[n], 16) for n in names}
    print(f"{len(names)} icons: {', '.join(names)}")

    spec = ",".join(f"{cp:04X}" for cp in sorted(set(codepoints.values())))
    from fontTools.subset import main as subset_main
    subset_main([
        str(SRC),
        f"--unicodes={spec}",
        "--flavor=woff2",
        f"--output-file={OUT_FONT}",
    ])

    # every requested icon must actually be in the subset's cmap
    from fontTools.ttLib import TTFont
    cmap = TTFont(OUT_FONT).getBestCmap()
    missing = [f"fa-{n} (U+{cp:04X})" for n, cp in sorted(codepoints.items())
               if cp not in cmap]
    if missing:
        print("MISSING GLYPHS (not in source font?):", ", ".join(missing))
        return 1

    rules = "".join(
        f'.fa-{n}{{ --fa:"\\{catalog[n]}"; }}\n' for n in names
    )
    OUT_CSS.write_text(CSS_HEADER + rules, encoding="utf-8", newline="\n")

    print(f"ok: {OUT_FONT.name} {OUT_FONT.stat().st_size / 1024:.1f} KB, "
          f"{OUT_CSS.name} {OUT_CSS.stat().st_size / 1024:.1f} KB, "
          f"{len(cmap)} codepoints mapped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
