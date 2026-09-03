"""Rebuild the Noto Sans JP glyph subset (app/static/fonts/NotoSansJP-subset.woff2)
and its static per-weight instances (NotoSansJP-subset-<w>.woff2).

The site ships a tiny woff2 subset of Noto Sans JP instead of the 8.8 MB
variable TTF. That means every Japanese glyph the site can ever render must
be baked in at build time — a character missing from the subset shows as
tofu. Run this script whenever Japanese text changes anywhere:

    python tools/build_jp_subset.py

What goes into the subset:
  * both full kana blocks + CJK punctuation (U+3000-30FF), so day-to-day
    kana-only edits never need a rebuild,
  * every CJK ideograph / fullwidth form actually used right now, scanned
    from: photos/**/album*.md (the per-language album descriptions),
    photos/**/*.cfg (album names, and the branding block in gallery.cfg —
    site_name, site_desc_jp and the rest), app/i18n.py (UI strings),
    app/templates/*.html, app/static/app.js and app/main.py (trip config).

Note the limit this has: the cfg files are RUNTIME data, read per request,
while the subset is built here. Editing Japanese branding text on a running
gallery therefore needs this script re-run and the woff2 redeployed — or the
text kept to kana and punctuation, which are baked in whole.

Only codepoints inside the @font-face unicode-range in style.css
(U+3000-30FF, U+4E00-9FFF, U+FF00-FFEF) are kept — anything else falls
through to the site's Latin fonts anyway.

Requires fonttools + brotli (pip install fonttools brotli).
"""

import sys
from pathlib import Path

# the summary prints the collected kanji; Windows consoles default to cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "app" / "static" / "fonts"
SRC = FONT_DIR / "NotoSansJP-VariableFont_wght.ttf"
OUT = FONT_DIR / "NotoSansJP-subset.woff2"

# keep in sync with the Noto Sans JP @font-face unicode-range in style.css
JP_RANGES = ((0x3000, 0x30FF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))

# always fully included: CJK punctuation + hiragana + katakana
BASE_RANGE = (0x3000, 0x30FF)


def _in_jp_ranges(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in JP_RANGES)


def collect_text() -> str:
    files: list[Path] = []
    files += sorted((ROOT / "photos").rglob("album*.md"))
    files += sorted((ROOT / "photos").rglob("*.cfg"))
    files += sorted((ROOT / "app" / "templates").glob("*.html"))
    files += [ROOT / "app" / "i18n.py", ROOT / "app" / "main.py",
              ROOT / "app" / "static" / "app.js"]
    chunks = []
    for f in files:
        try:
            chunks.append(f.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            print(f"  ! skipped {f}: {e}")
    return "".join(chunks)


def main() -> int:
    if not SRC.is_file():
        print(f"source font not found: {SRC}")
        return 1

    text = collect_text()
    used = {cp for cp in map(ord, set(text)) if _in_jp_ranges(cp)}
    extra = sorted(cp for cp in used if not (BASE_RANGE[0] <= cp <= BASE_RANGE[1]))

    unicodes = [f"{BASE_RANGE[0]:04X}-{BASE_RANGE[1]:04X}"]
    unicodes += [f"{cp:04X}" for cp in extra]
    spec = ",".join(unicodes)
    print(f"kana/punct block + {len(extra)} extra glyphs "
          f"({''.join(chr(cp) for cp in extra)})")

    from fontTools.subset import main as subset_main
    subset_main([
        str(SRC),
        f"--unicodes={spec}",
        "--flavor=woff2",
        f"--output-file={OUT}",
    ])

    # sanity check: every scanned codepoint must have made it into the cmap
    from fontTools.ttLib import TTFont
    cmap = TTFont(OUT).getBestCmap()
    missing = [f"U+{cp:04X} {chr(cp)}" for cp in sorted(used) if cp not in cmap]
    if missing:
        print("MISSING GLYPHS (not in source font?):", ", ".join(missing))
        return 1
    print(f"ok: {OUT.name} rebuilt, {OUT.stat().st_size / 1024:.1f} KB, "
          f"{len(cmap)} codepoints mapped")

    # the pages render the static per-weight instances of this subset, not
    # the variable file itself (see tools/build_font_instances.py) — they
    # have to follow every rebuild or the new glyphs stay tofu on screen
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_font_instances import build_jp
    return build_jp()


if __name__ == "__main__":
    sys.exit(main())
