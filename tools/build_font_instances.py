"""Build static per-weight woff2 instances of the site's variable fonts.

The gallery's three text faces ship as VARIABLE fonts (Space Grotesk,
JetBrains Mono, and the Noto Sans JP glyph subset). Variable fonts are
expensive to instantiate: the first layout of a page has to process the
`gvar` deltas for every face it uses, and on a phone that put ~1 s of
main-thread work under the album entrance animations (measured 2026-09-03
with a CPU-throttled trace: the first layout took 4x longer with the
variable faces than with static ones, and the difference was the whole
stutter). Static instances at the weights the stylesheet actually uses
cost nothing to instantiate, and per-weight files mean a page downloads
only the weights it renders.

    python tools/build_font_instances.py

Writes, for each weight in WEIGHTS:
    app/static/fonts/SpaceGrotesk-<w>.woff2
    app/static/fonts/JetBrainsMono-<w>.woff2
    app/static/fonts/NotoSansJP-subset-<w>.woff2

The variable sources stay where they are (the configurator still uses
them, and they are what this script instantiates from). The JP instances
derive from the glyph SUBSET, so tools/build_jp_subset.py calls build_jp()
at the end of its own run — a Japanese text change rebuilds both.

The @font-face block at the top of app/static/style.css lists these files
with weight RANGES (400 -> 100-450, 500 -> 451-550, 600 -> 551-650,
700 -> 651-900); add a weight here and add its faces there.

Requires fonttools + brotli (pip install fonttools brotli).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "app" / "static" / "fonts"

# every weight the stylesheet asks for; 300 is declared in the old range
# but never used, bold <b> resolves to 700
WEIGHTS = (400, 500, 600, 700)

LATIN = {
    "SpaceGrotesk-VariableFont_wght.ttf": "SpaceGrotesk-{w}.woff2",
    "JetBrainsMono-VariableFont_wght.ttf": "JetBrainsMono-{w}.woff2",
}
JP_SRC = "NotoSansJP-subset.woff2"
JP_OUT = "NotoSansJP-subset-{w}.woff2"


def _instances(src: Path, pattern: str) -> int:
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    if not src.is_file():
        print(f"  ! source font not found: {src}")
        return 1
    for w in WEIGHTS:
        font = TTFont(src)
        if "fvar" not in font:
            print(f"  ! {src.name} is not a variable font")
            return 1
        axes = {a.axisTag: (a.minValue, a.maxValue) for a in font["fvar"].axes}
        lo, hi = axes.get("wght", (w, w))
        wanted = min(max(w, lo), hi)
        static = instancer.instantiateVariableFont(font, {"wght": wanted}, inplace=True)
        static.flavor = "woff2"
        out = FONT_DIR / pattern.format(w=w)
        static.save(out)
        print(f"  {out.name:32} wght {wanted:>5}  {out.stat().st_size / 1024:6.1f} KB")
    return 0


def build_latin() -> int:
    rc = 0
    for src, pattern in LATIN.items():
        print(src)
        rc |= _instances(FONT_DIR / src, pattern)
    return rc


def build_jp() -> int:
    print(JP_SRC)
    return _instances(FONT_DIR / JP_SRC, JP_OUT)


def main() -> int:
    rc = build_latin() | build_jp()
    print("ok" if rc == 0 else "FAILED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
