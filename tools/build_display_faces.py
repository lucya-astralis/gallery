"""Compress the shipped display faces to woff2.

Space Grotesk, JetBrains Mono and Noto Sans JP arrive here as woff2 already
(tools/build_font_instances.py instantiates them out of their variable
sources). The fixed-weight display faces did not: they sat in
aperture/static/fonts/ as the .otf / .ttf the foundry shipped and were served
raw. Ethnocentric alone is 68 KB that way and it is on the first paint of
every page — the wordmark is drawn in it — against ~30 KB as woff2, which
is the same outlines with a better container.

    python tools/build_display_faces.py

Writes, next to each source:
    aperture/static/fonts/<name>.woff2

The .otf/.ttf sources stay where they are, exactly like the variable
sources build_font_instances.py reads: this script is the only thing that
regenerates the woff2, and the @font-face blocks in aperture/static/style.css
are what point at the result. Add a face here and change its `src:` there.

No subsetting happens here on purpose. The display face draws the archive's
own name out of gallery.cfg and an album's title out of album.cfg — text
this repo does not get to see — so dropping glyphs it "does not use" would
be a decision made on the wrong evidence. See tools/build_jp_subset.py for
the one place a subset IS safe, where the text is in this repo.

Requires fonttools + brotli (pip install fonttools brotli).
"""
import sys
from pathlib import Path

from fontTools.ttLib import TTFont

FONTS = Path(__file__).resolve().parent.parent / "aperture" / "static" / "fonts"

# every fixed-weight face style.css declares by file rather than by instance
SOURCES = [
    "Ethnocentric-Regular.otf",
    "ChakraPetch-Regular.ttf",
    "ChakraPetch-Medium.ttf",
    "ChakraPetch-Bold.ttf",
]


def build(name: str) -> tuple[int, int] | None:
    src = FONTS / name
    if not src.exists():
        print(f"  ! missing {src.name}")
        return None
    dst = src.with_suffix(".woff2")
    font = TTFont(str(src))
    font.flavor = "woff2"
    font.save(str(dst))
    before, after = src.stat().st_size, dst.stat().st_size
    print(f"  {src.name:<32} {before/1024:6.1f} KB -> {dst.name:<28} {after/1024:6.1f} KB")
    return before, after


def main() -> int:
    print(f"display faces -> woff2  ({FONTS})")
    total_before = total_after = 0
    for name in SOURCES:
        got = build(name)
        if got:
            total_before += got[0]
            total_after += got[1]
    if not total_before:
        return 1
    print(f"  {'total':<32} {total_before/1024:6.1f} KB -> {'':<28} {total_after/1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
