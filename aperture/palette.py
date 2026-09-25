"""Nebula's identity tints, derived from the accent.

The three palettes gallery.cfg `palette` picks between (schema.PALETTES)
were drawn against the built-in accent and the title silver: mist is that
silver leaning toward the accent, stardust four silvers with a faint cast
each. Drawn as hex values they only fit that one accent -- repaint the site
in green and a lilac mist is a stranger in it. So they are kept here as what
they really are, positions RELATIVE to the accent, and resolved against
whichever accent the page wears:

    lightness   a fixed ladder -- the silver's, never the accent's
    chroma      a fixed amount, scaled down as the accent itself goes grey
    hue         the accent's own hue, plus a fixed offset per kind

Worked in OKLCH, where those three are independent: the same offset reads
as the same step of colour at any hue, which HLS does not promise. For the
built-in accent this reproduces the hex values the sheets carry (a test
holds them together), and silver has no hue to derive, so it is left alone.

The same accent recolours the console's backdrop (`nova`, an SVG drawn in
the built-in violet): `recolour_svg` turns every chromatic stop around to
the accent's hue and leaves the greys where they are.
"""

from __future__ import annotations

import math
import re

# The accent the palettes were drawn against (the built-in --acc) and its
# chroma: a new accent's own chroma is measured against it.
BASE_ACCENT = (0x61, 0x6E, 0xF3)

# Lightness / chroma of each step, and the hue offset from the accent.
# glyph = the icon tint (--mist / --glyph-<kind>); ramp = --mist-1..4 /
# --ramp-<kind>-1..4, light to dark.
MIST = {"dh": 0.0, "glyph": (0.80, 0.050),
        "ramp": [(0.90, 0.030), (0.78, 0.050), (0.66, 0.071), (0.54, 0.090)]}
_CAST = [(0.90, 0.036), (0.78, 0.060), (0.66, 0.060), (0.54, 0.060)]
STARDUST = {
    "album":   {"dh": 25.5,  "glyph": (0.78, 0.060), "ramp": _CAST},
    "tag":     {"dh": 75.5,  "glyph": (0.76, 0.060), "ramp": _CAST},
    "time":    {"dh": -29.5, "glyph": (0.78, 0.060), "ramp": _CAST},
    "machine": {"dh": 10.0,  "glyph": (0.80, 0.014),
                "ramp": [(0.90, 0.0095), (0.78, 0.0155), (0.66, 0.0147), (0.54, 0.014)]},
}
KINDS = ("album", "tag", "time", "machine")


# ----- OKLab / OKLCH ------------------------------------------------------
def _to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def rgb_to_oklch(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_to_lin(v / 255) for v in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_to_lin(L: float, C: float, H: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def oklch_to_rgb(L: float, C: float, H: float) -> tuple[int, int, int]:
    """Back to 8-bit sRGB. Out of gamut, the CHROMA gives way (the hue and
    the lightness are what the palette promises), in small steps until the
    colour exists."""
    while True:
        lin = _oklch_to_lin(L, C, H)
        if all(-0.0005 <= c <= 1.0005 for c in lin) or C <= 0:
            break
        C = max(0.0, C - 0.004)
    return tuple(round(min(1.0, max(0.0, _to_srgb(max(0.0, c)))) * 255) for c in lin)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


# ----- the palettes ---------------------------------------------------------
def _frame(accent: tuple[int, int, int]) -> tuple[float, float]:
    """The accent's hue, and how much of each palette's chroma it allows: a
    greyer accent gets greyer tints, a grey one plain silver."""
    _, base_c, _ = rgb_to_oklch(BASE_ACCENT)
    _, c, h = rgb_to_oklch(accent)
    return h, min(1.0, c / base_c)


def _tone(step: tuple[float, float], hue: float, k: float) -> str:
    L, C = step
    return _hex(oklch_to_rgb(L, C * k, hue))


def mist_tokens(accent: tuple[int, int, int]) -> dict[str, str]:
    """--mist and --mist-1..4 for this accent."""
    hue, k = _frame(accent)
    h = hue + MIST["dh"]
    out = {"--mist": _tone(MIST["glyph"], h, k)}
    for i, step in enumerate(MIST["ramp"], 1):
        out["--mist-%d" % i] = _tone(step, h, k)
    return out


def stardust_tokens(accent: tuple[int, int, int]) -> dict[str, str]:
    """--glyph-<kind> and --ramp-<kind>-1..4 for this accent."""
    hue, k = _frame(accent)
    out = {}
    for kind in KINDS:
        spec = STARDUST[kind]
        h = hue + spec["dh"]
        out["--glyph-" + kind] = _tone(spec["glyph"], h, k)
        for i, step in enumerate(spec["ramp"], 1):
            out["--ramp-%s-%d" % (kind, i)] = _tone(step, h, k)
    return out


def palette_blocks(accent: tuple[int, int, int]) -> list[tuple[str, list[str]]]:
    """The palette half of a theme sheet: (selector, declarations) pairs.
    mist goes on :root, so a page that wears silver still wins with its own
    :root[data-palette="silver"] block (higher specificity, no accent in it);
    stardust goes on its own selector, loaded after the sheet that declares
    the built-in values so it overrides them. The console's palette picker
    previews each palette on a card ([data-palette-preview]), and those
    cards have to turn with the accent too."""
    mist = ["%s:%s" % kv for kv in mist_tokens(accent).items()]
    stardust = ["%s:%s" % kv for kv in stardust_tokens(accent).items()]
    return [
        (":root", mist),
        ('[data-palette-preview="mist"]', mist),
        (':root[data-palette="stardust"],[data-palette-preview="stardust"]', stardust),
    ]


# ----- the console's backdrop --------------------------------------------
_HEX6 = re.compile(r"#([0-9a-fA-F]{6})\b")


def recolour_svg(svg: str, accent: tuple[int, int, int]) -> str:
    """Turn an SVG drawn around the built-in accent to another accent. Every
    six-digit colour with a real hue is rotated by the angle between the two
    accents and its chroma scaled the way the palettes scale theirs; greys
    (the shards, the black) are left exactly as they are. Lightness never
    moves, so the artwork keeps its depth whatever the colour."""
    base_h = rgb_to_oklch(BASE_ACCENT)[2]
    hue, k = _frame(accent)
    turn = hue - base_h

    def swap(m: re.Match) -> str:
        body = m.group(1)
        rgb = tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))
        L, C, H = rgb_to_oklch(rgb)
        if C < 0.02:
            return m.group(0)
        return _hex(oklch_to_rgb(L, C * k, H + turn)).lower()

    return _HEX6.sub(swap, svg)
