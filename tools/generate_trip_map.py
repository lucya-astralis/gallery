# Generates app/templates/_trip_map.html from a MapSVG prefecture map of Japan.
# - parses all 47 prefecture paths, converts to absolute polylines
# - verifies whether the map's y-axis is linear or Mercator in latitude
#   (by checking where known city coords land relative to prefecture bboxes)
# - simplifies geometry (Douglas-Peucker), drops sub-pixel islands
# - crops the viewBox to the route region (Kansai -> Hokkaido)
# - emits the final inline-SVG partial with route arcs, city dots and labels
#
# Run from anywhere: `python tools/generate_trip_map.py` — paths are resolved
# relative to this file. Tune CITIES / segment bulges / LBL offsets below when
# the itinerary changes, then re-run.
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "tools" / "japan-prefectures.svg"
OUT = REPO / "app" / "templates" / "_trip_map.html"

svg = SRC.read_text(encoding="utf-8")

# ---- geo reference from the MapSVG header --------------------------------
m = re.search(r'geoViewBox="([\d.\- ]+)"', svg)
west, north, east, south = (float(x) for x in m.group(1).split())
W = float(re.search(r'width="([\d.]+)"', svg).group(1))
H = float(re.search(r'height="([\d.]+)"', svg).group(1))
print(f"geo: lon {west}..{east}  lat {south}..{north}  px {W:.1f}x{H:.1f}")

def merc(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

MERC_N, MERC_S = merc(north), merc(south)

def geo_x(lon):
    return (lon - west) / (east - west) * W

def geo_y_linear(lat):
    return (north - lat) / (north - south) * H

def geo_y_merc(lat):
    return (MERC_N - merc(lat)) / (MERC_N - MERC_S) * H

# ---- parse paths ----------------------------------------------------------
paths = re.findall(r'<path\b[^>]*?d="([^"]+)"[^>]*?title="([^"]+)"', svg, re.S)
if len(paths) != 47:
    # attribute order may vary
    paths = [(d, t) for t, d in re.findall(r'<path\b[^>]*?title="([^"]+)"[^>]*?d="([^"]+)"', svg, re.S)]
print(f"paths: {len(paths)}")

cmds_seen = set(re.findall(r"[a-zA-Z]", " ".join(d for d, _ in paths)))
print("commands:", sorted(cmds_seen))
if not cmds_seen <= set("mMlLhHvVzZ"):
    sys.exit("unsupported path commands present — extend the parser")

NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

def parse_path(d):
    """-> list of rings, each ring a list of (x, y) absolute points."""
    tokens = re.findall(r"[a-zA-Z]|" + NUM.pattern, d)
    rings, ring = [], []
    cx = cy = 0.0
    i, cmd = 0, None
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            continue
        if cmd in ("m", "M"):
            dx, dy = float(t), float(tokens[i + 1])
            i += 2
            if ring:
                rings.append(ring)
            if cmd == "m":
                cx, cy = cx + dx, cy + dy
            else:
                cx, cy = dx, dy
            ring = [(cx, cy)]
            cmd = "l" if cmd == "m" else "L"  # implicit lineto
        elif cmd in ("l", "L"):
            dx, dy = float(t), float(tokens[i + 1])
            i += 2
            if cmd == "l":
                cx, cy = cx + dx, cy + dy
            else:
                cx, cy = dx, dy
            ring.append((cx, cy))
        elif cmd in ("h", "H"):
            v = float(t)
            i += 1
            cx = cx + v if cmd == "h" else v
            ring.append((cx, cy))
        elif cmd in ("v", "V"):
            v = float(t)
            i += 1
            cy = cy + v if cmd == "v" else v
            ring.append((cx, cy))
        else:
            sys.exit(f"unhandled command {cmd}")
        # z/Z: closes ring; handled when next m starts or at end
        if i < len(tokens) and tokens[i] in "zZ":
            i += 1
            if ring:
                rings.append(ring)
                ring = []
            # after z, pen returns to ring start
            if rings[-1]:
                cx, cy = rings[-1][0]
            cmd = None
    if ring:
        rings.append(ring)
    return rings

prefs = {}  # title -> rings
for d, title in paths:
    title = title.strip()
    key = "Hokkaido" if title.startswith("Hokkaido") else title  # encoding artifact in source
    prefs[key] = parse_path(d)

def bbox(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)

# ---- projection check -----------------------------------------------------
CITIES = {
    "Osaka":   {"lat": 34.6937, "lon": 135.5023, "pref": "Osaka"},
    "Sapporo": {"lat": 43.0618, "lon": 141.3545, "pref": "Hokkaido"},
    "Tokyo":   {"lat": 35.6762, "lon": 139.6503, "pref": "Tokyo"},
}
for name, c in CITIES.items():
    x = geo_x(c["lon"])
    yl, ym = geo_y_linear(c["lat"]), geo_y_merc(c["lat"])
    bx0, by0, bx1, by1 = bbox(prefs[c["pref"]])
    print(f"{name:8s} x={x:7.1f}  y_lin={yl:7.1f}  y_merc={ym:7.1f}   "
          f"{c['pref']} bbox x {bx0:.0f}..{bx1:.0f}  y {by0:.0f}..{by1:.0f}")

# Mercator confirmed by the check above (linear lands outside the bboxes).
def pt(lon, lat):
    return geo_x(lon), geo_y_merc(lat)

# ---- crop to the route region (Kansai -> Hokkaido) ------------------------
X0, Y_TOP = pt(130.6, 45.523885)   # left edge / map top
X1, _ = pt(146.06, 45.0)
_, Y_BOT = pt(0 + west, 32.4)      # bottom at 32.4N (keeps Shikoku)
VB = (round(X0, 1), -6.0, round(X1 - X0, 1), round(Y_BOT + 6 + 6, 1))
print(f"viewBox: {VB}  aspect w/h={(VB[2] / VB[3]):.3f}")

# ---- simplify -------------------------------------------------------------
EPS = 0.55        # Douglas-Peucker tolerance (svg units; ~0.4px at render size)
MIN_DIAG = 2.4    # drop islands smaller than this bbox diagonal (speckles)

def dp(points, eps):
    """Iterative Douglas-Peucker on an open polyline."""
    if len(points) < 3:
        return points[:]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        dmax, imax = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                t = max(0.0, min(1.0, t))
                qx, qy = ax + t * dx, ay + t * dy
                d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 > dmax:
                dmax, imax = d2, i
        if dmax > eps * eps:
            keep[imax] = True
            stack.append((a, imax))
            stack.append((imax, b))
    return [p for p, k in zip(points, keep) if k]

def ring_bbox(r):
    xs = [p[0] for p in r]
    ys = [p[1] for p in r]
    return min(xs), min(ys), max(xs), max(ys)

def crop_keep(r):
    """Keep rings that intersect the crop box (2u margin)."""
    x0, y0, x1, y1 = ring_bbox(r)
    cx0, cy0, cw, ch = VB
    return not (x1 < cx0 - 2 or x0 > cx0 + cw + 2 or y1 < cy0 - 2 or y0 > cy0 + ch + 2)

def fmt(v):
    s = f"{v:.1f}"
    return s.rstrip("0").rstrip(".") if "." in s else s

def rings_to_d(rings):
    parts = []
    for r in rings:
        coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in r)
        parts.append(f"M{coords} Z" if len(r) > 2 else f"M{coords}")
    return "".join(parts)

def process(rings):
    out = []
    for r in rings:
        if not crop_keep(r):
            continue
        x0, y0, x1, y1 = ring_bbox(r)
        if math.hypot(x1 - x0, y1 - y0) < MIN_DIAG:
            continue
        s = dp(r, EPS)
        if len(s) >= 3:
            out.append(s)
    return out

pts_before = sum(len(r) for rings in prefs.values() for r in rings)
visited_prefs = {"Osaka": "Osaka", "Tokyo": "Tokyo", "Hokkaido": "Sapporo"}  # pref -> stop city
land_rings, visited = [], {}
for name, rings in prefs.items():
    slim = process(rings)
    if name in visited_prefs:
        visited[visited_prefs[name]] = slim
    else:
        land_rings.extend(slim)
pts_after = sum(len(r) for r in land_rings) + sum(len(r) for rings in visited.values() for r in rings)
print(f"points: {pts_before} -> {pts_after}")

# ---- route + city markup ---------------------------------------------------
DOTS = {name: pt(c["lon"], c["lat"]) for name, c in CITIES.items()}
OSA, SPK, TYO = DOTS["Osaka"], DOTS["Sapporo"], DOTS["Tokyo"]

def q(a, b, bulge):
    """Quadratic arc a->b, control point offset perpendicular by `bulge`."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1
    nx, ny = dy / ln, -dx / ln
    cx, cy = mx + nx * bulge, my + ny * bulge
    return f"M{fmt(a[0])} {fmt(a[1])} Q{fmt(cx)} {fmt(cy)} {fmt(b[0])} {fmt(b[1])}"

# seg index = the stop the leg ends AT (no leg into stop 0 — the map starts
# at Osaka; the international arrival is told by the countdown, not the map)
segs = [
    ("trip-map__seg", 1, q(OSA, SPK, 42)),
    ("trip-map__seg", 2, q(SPK, TYO, 34)),
]

# label placement: (dx, dy, text-anchor)
LBL = {
    "Osaka":   (-8, 12, "end"),
    "Sapporo": (-8, -8, "end"),
    "Tokyo":   (10, 5, "start"),
}

def city_group(name):
    x, y = DOTS[name]
    dx, dy, anch = LBL[name]
    return (
        f'    <g class="trip-map__city" data-map-city="{name}">\n'
        f'      <circle class="trip-map__pulse" cx="{fmt(x)}" cy="{fmt(y)}" r="4.5"/>\n'
        f'      <circle class="trip-map__ring" cx="{fmt(x)}" cy="{fmt(y)}" r="10"/>\n'
        f'      <circle class="trip-map__dot" cx="{fmt(x)}" cy="{fmt(y)}" r="3.4"/>\n'
        f'      <text class="trip-map__lbl" x="{fmt(x + dx)}" y="{fmt(y + dy)}" text-anchor="{anch}">{name.upper()}</text>\n'
        f'    </g>'
    )

pref_paths = "\n".join(
    f'    <path class="trip-map__pref" data-map-pref="{city}" d="{rings_to_d(visited[city])}"/>'
    for city in ("Osaka", "Sapporo", "Tokyo")
)
seg_paths = "\n".join(
    f'      <path class="{cls}" data-map-seg="{i}" d="{d}"/>' for cls, i, d in segs
)
city_groups = "\n".join(city_group(n) for n in ("Osaka", "Sapporo", "Tokyo"))

html = f"""{{# Route map for the trip dashboard (generated — do not hand-edit paths).
   Source: tools/japan-prefectures.svg (MapSVG 47-prefecture map, Mercator),
   simplified + cropped to the route region by tools/generate_trip_map.py.
   Regenerate with that script if the itinerary gains/loses cities.
   State (is-upcoming / is-active / is-done on [data-map-city]/[data-map-pref],
   is-done / is-next on [data-map-seg]) is synced by initTrip() in app.js;
   data-map-pref carries the STOP city (Hokkaido -> "Sapporo"), matching each
   stop's data-city. #}}
<figure class="trip-map">
  <svg viewBox="{fmt(VB[0])} {fmt(VB[1])} {fmt(VB[2])} {fmt(VB[3])}" role="img"
       aria-label="Route map: Osaka, then Sapporo, then Tokyo"
       preserveAspectRatio="xMidYMid meet">
    <path class="trip-map__land" d="{rings_to_d(land_rings)}"/>
{pref_paths}
    <g class="trip-map__routes" aria-hidden="true">
{seg_paths}
    </g>
    <g class="trip-map__cities" aria-hidden="true">
{city_groups}
    </g>
  </svg>
</figure>
"""
OUT.write_text(html, encoding="utf-8")
print(f"wrote {OUT}  ({len(html.encode('utf-8')):,} bytes)")
for n, (x, y) in DOTS.items():
    print(f"  {n}: {x:.1f},{y:.1f}")
