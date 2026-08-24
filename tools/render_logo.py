"""Rasterise the site logo to a PNG for the terminal CLI.

    python tools/render_logo.py

Why a build step instead of rendering the SVG at runtime: every Python SVG
renderer pulls in a native stack (cairo, librsvg, resvg). The logo changes
about never, so it gets rasterised once, on a developer machine, and the
container keeps the dependency list it has (Pillow, nothing else).

This is deliberately NOT a general SVG renderer. It understands exactly what
`app/static/logo/lucya_logo.svg` uses — nested `matrix(...)` groups and paths
built from absolute `M`, `L`, `C`, `Z` — and raises on anything else, so a
redesigned logo fails here loudly instead of silently rendering garbage.

Re-run it after changing the SVG, the same way Japanese text changes need
`python tools/build_jp_subset.py`.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SVG = REPO / "app" / "static" / "logo" / "lucya_logo.svg"
DEFAULT_OUT = REPO / "app" / "static" / "logo" / "lucya_logo.png"

SVG_NS = "{http://www.w3.org/2000/svg}"
# Curves are flattened to line segments; 24 per cubic is far below one pixel
# of error at the sizes we render, and the supersampling smooths the rest.
CURVE_STEPS = 24
SUPERSAMPLE = 4

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"([MLCZmlcz])")


class Unsupported(Exception):
    """The SVG uses something this little renderer does not implement."""


# ----- geometry ---------------------------------------------------------
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(m, n):
    """Compose two SVG matrices (a, b, c, d, e, f), outer * inner."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(m, point):
    a, b, c, d, e, f = m
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(text: str):
    """Only `matrix(...)` — that is all Serif/Affinity exports here."""
    if not text or not text.strip():
        return IDENTITY
    result = IDENTITY
    for kind, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        values = [float(v) for v in _NUM.findall(args)]
        if kind == "matrix" and len(values) == 6:
            result = multiply(result, tuple(values))
        elif kind == "translate" and len(values) in (1, 2):
            tx, ty = (values + [0.0])[:2]
            result = multiply(result, (1, 0, 0, 1, tx, ty))
        elif kind == "scale" and len(values) in (1, 2):
            sx = values[0]
            sy = values[1] if len(values) > 1 else sx
            result = multiply(result, (sx, 0, 0, sy, 0, 0))
        else:
            raise Unsupported(f"transform {kind}({args})")
    return result


def cubic(p0, p1, p2, p3, steps=CURVE_STEPS):
    """Flatten a cubic bezier to points (the start point is not repeated)."""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        x = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
             + 3 * u * t * t * p2[0] + t * t * t * p3[0])
        y = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
             + 3 * u * t * t * p2[1] + t * t * t * p3[1])
        out.append((x, y))
    return out


def parse_path(d: str):
    """`d` -> a list of subpaths, each a list of points. Absolute M/L/C/Z."""
    tokens = [t for t in _CMD.split(d) if t.strip()]
    subpaths, current, cursor = [], [], (0.0, 0.0)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        if cmd not in "MLCZ":
            raise Unsupported(f"path command {cmd!r} (only absolute M/L/C/Z)")
        args = []
        if i + 1 < len(tokens) and tokens[i + 1] not in "MLCZ":
            args = [float(v) for v in _NUM.findall(tokens[i + 1])]
            i += 1
        i += 1
        if cmd == "M":
            if current:
                subpaths.append(current)
            cursor = (args[0], args[1])
            current = [cursor]
            # extra pairs after an M are implicit line-tos
            for j in range(2, len(args), 2):
                cursor = (args[j], args[j + 1])
                current.append(cursor)
        elif cmd == "L":
            for j in range(0, len(args), 2):
                cursor = (args[j], args[j + 1])
                current.append(cursor)
        elif cmd == "C":
            for j in range(0, len(args), 6):
                p1 = (args[j], args[j + 1])
                p2 = (args[j + 2], args[j + 3])
                p3 = (args[j + 4], args[j + 5])
                current.extend(cubic(cursor, p1, p2, p3))
                cursor = p3
        elif cmd == "Z":
            if current:
                subpaths.append(current)
                cursor = current[0]
                current = []
    if current:
        subpaths.append(current)
    return subpaths


# ----- colour -----------------------------------------------------------
NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "none": None}


def parse_fill(style: str, attrib: dict):
    raw = None
    for token in (style or "").split(";"):
        if token.strip().startswith("fill:"):
            raw = token.split(":", 1)[1].strip()
    raw = raw or attrib.get("fill") or "black"
    if raw in NAMED:
        return NAMED[raw]
    if raw.startswith("#"):
        hexa = raw[1:]
        if len(hexa) == 3:
            hexa = "".join(ch * 2 for ch in hexa)
        if len(hexa) == 6:
            return tuple(int(hexa[k:k + 2], 16) for k in (0, 2, 4))
    match = re.match(r"rgb\(([^)]*)\)", raw)
    if match:
        values = [int(float(v)) for v in _NUM.findall(match.group(1))]
        if len(values) >= 3:
            return tuple(values[:3])
    raise Unsupported(f"fill {raw!r}")


# ----- rendering --------------------------------------------------------
def collect(node, matrix, shapes):
    matrix = multiply(matrix, parse_transform(node.get("transform", "")))
    tag = node.tag.replace(SVG_NS, "")
    if tag == "path":
        fill = parse_fill(node.get("style", ""), node.attrib)
        if fill is not None:
            for points in parse_path(node.get("d", "")):
                if len(points) >= 3:
                    shapes.append(([apply(matrix, p) for p in points], fill))
    elif tag in ("g", "svg"):
        for child in node:
            collect(child, matrix, shapes)
    elif tag in ("title", "desc", "defs", "metadata", "style"):
        pass
    else:
        raise Unsupported(f"element <{tag}>")
    return shapes


def render(svg_path: Path, width: int) -> Image.Image:
    root = ET.parse(svg_path).getroot()
    box = [float(v) for v in _NUM.findall(root.get("viewBox", ""))]
    if len(box) != 4:
        raise Unsupported("the <svg> needs a viewBox")
    min_x, min_y, box_w, box_h = box
    height = max(1, round(width * box_h / box_w))

    scale = width / box_w * SUPERSAMPLE
    base = multiply((scale, 0, 0, scale, -min_x * scale, -min_y * scale), IDENTITY)
    shapes = collect(root, base, [])

    canvas = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    for points, fill in shapes:
        draw.polygon(points, fill=fill + (255,))
    # Downsampling the supersampled canvas is what gives the edges their
    # anti-aliasing — ImageDraw itself has none.
    return canvas.resize((width, height), Image.LANCZOS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--svg", type=Path, default=DEFAULT_SVG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=512,
                    help="output width in px (default: 512)")
    args = ap.parse_args(argv)
    try:
        image = render(args.svg, args.width)
    except Unsupported as e:
        print(f"error: this renderer does not support: {e}", file=sys.stderr)
        print("       it only covers the shapes the current logo uses — extend it, "
              "or rasterise with a real SVG renderer.", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(f"{args.svg.name} -> {args.out} ({image.width}x{image.height}, "
          f"{args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
