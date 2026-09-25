"""The colours of a photo: what `color:blue` searches and the palette strip.

Read once per photo at index time, from its thumbnail rather than the
original (a 480 px WebP says as much about the colours as 48 megapixels, and
costs nothing to open), and kept in two columns (db.py):

    colors    ",blue,black,"   the named colours that cover enough of the
                               picture to be worth searching for; the commas
                               let one instr() match a whole name
    palette   [["#1f3b73", 34], ...]   the picture's main colours with their
                               share in percent, largest first -- the strip
                               the photo page draws

NULL in `colors` means "not read yet" (the scan fills it in); an empty string
means read, and nothing counted.

The names are buckets of OKLCH hue, lightness and chroma, where the eye's
idea of "one colour" is a band of the same width all the way round. A search
names a bucket in any of the gallery's three languages, or as a hex, which
lands in the bucket it would be named by.
"""

from __future__ import annotations

import json
import re

from PIL import Image

from . import palette as _ok

# In the order a colour picker would show them: the wheel, then the neutrals.
NAMES = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink",
         "brown", "black", "grey", "white"]

# A swatch for each name -- the facet chips and the legend paint these.
SWATCH = {
    "red": "#d8403a", "orange": "#e8892c", "yellow": "#e8cf3a", "green": "#4fa85a",
    "teal": "#2fa8a0", "blue": "#3d6fd8", "purple": "#8a5ad8", "pink": "#e06aa6",
    "brown": "#8a5a36", "black": "#111114", "grey": "#8b8b94", "white": "#f2f2f5",
}

# Every spelling a visitor might type, in the gallery's three languages.
ALIASES = {
    "red": ["rot", "赤", "あか"], "orange": ["オレンジ", "橙"],
    "yellow": ["gelb", "黄", "黄色", "きいろ"], "green": ["grün", "gruen", "緑", "みどり"],
    "teal": ["türkis", "tuerkis", "cyan", "turquoise", "青緑"],
    "blue": ["blau", "青", "あお"], "purple": ["lila", "violett", "violet", "紫", "むらさき"],
    "pink": ["rosa", "pink", "ピンク", "桃色"], "brown": ["braun", "茶", "茶色"],
    "black": ["schwarz", "黒", "くろ"], "grey": ["gray", "grau", "灰", "灰色", "グレー"],
    "white": ["weiß", "weiss", "白", "しろ"],
}
_LOOKUP = {n: n for n in NAMES} | {a.lower(): n for n, spellings in ALIASES.items() for a in spellings}

# Below this chroma a colour is a neutral; the neutrals split on lightness.
NEUTRAL_C = 0.035
# How much of the picture a name has to cover to be searchable by it. A
# colour catches the eye at a far smaller share than a grey does.
MIN_SHARE_CHROMATIC = 0.07
MIN_SHARE_NEUTRAL = 0.22
PALETTE_SIZE = 5
_SAMPLE = 64          # the edge the picture is reduced to before counting
_LEVELS = 10          # colours the reduced picture is quantized to


def name_of(rgb: tuple[int, int, int]) -> str:
    """The bucket one colour falls in."""
    L, C, H = _ok.rgb_to_oklch(rgb)
    if C < NEUTRAL_C:
        return "black" if L < 0.25 else "white" if L > 0.88 else "grey"
    if L < 0.52 and 25 <= H < 100 and C < 0.13:
        return "brown"
    if L < 0.6 and 100 <= H < 125:
        return "green"            # olive: a dark yellow reads as foliage, not as yellow
    for top, name in ((15, "pink"), (45, "red"), (85, "orange"), (120, "yellow"),
                      (170, "green"), (225, "teal"), (285, "blue"), (322, "purple"),
                      (360, "pink")):
        if H < top:
            return name
    return "pink"


def parse_name(value: str) -> str | None:
    """A typed colour -- a name in any language, or #hex -- as a bucket."""
    v = (value or "").strip().lower()
    if re.fullmatch(r"#?[0-9a-f]{6}", v):
        v = v.lstrip("#")
        return name_of(tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)))
    return _LOOKUP.get(v)


def read(img: Image.Image) -> tuple[str, str]:
    """(colors, palette) columns for one picture."""
    small = img.convert("RGB")
    small.thumbnail((_SAMPLE, _SAMPLE))
    quant = small.quantize(colors=_LEVELS, method=Image.Quantize.FASTOCTREE)
    flat = quant.getpalette() or []
    counts = quant.getcolors() or []
    total = sum(n for n, _ in counts) or 1
    swatches = []
    for n, idx in counts:
        rgb = tuple(flat[idx * 3: idx * 3 + 3])
        if len(rgb) == 3:
            swatches.append((n / total, rgb))
    swatches.sort(reverse=True)

    share: dict[str, float] = {}
    for w, rgb in swatches:
        name = name_of(rgb)
        share[name] = share.get(name, 0.0) + w
    found = [n for n in NAMES if share.get(n, 0) >= (
        MIN_SHARE_NEUTRAL if n in ("black", "grey", "white") else MIN_SHARE_CHROMATIC)]
    strip = [["#%02x%02x%02x" % rgb, round(w * 100)] for w, rgb in swatches[:PALETTE_SIZE]
             if round(w * 100) >= 3]
    return ("," + ",".join(found) + ",") if found else "", json.dumps(strip)


def read_file(path) -> tuple[str, str] | None:
    try:
        with Image.open(path) as img:
            return read(img)
    except Exception:
        return None


def strip_of(raw: str | None) -> list[dict]:
    """The palette column as [{hex, share, name}] for a template."""
    try:
        rows = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    out = []
    for hexv, share in rows:
        if isinstance(hexv, str) and re.fullmatch(r"#[0-9a-f]{6}", hexv):
            rgb = tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))
            out.append({"hex": hexv, "share": int(share), "name": name_of(rgb)})
    return out


def names_of(raw: str | None) -> list[str]:
    return [n for n in (raw or "").split(",") if n in SWATCH]
