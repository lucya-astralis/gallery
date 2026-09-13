"""Gallery-wide statistics for the public /stats page.

One pass over the `images` table produces every dataset the page draws: the
headline counters, the monthly timeline, the weekday/hour rhythm, and the
capture facts that only exist inside `exif_json` (camera, focal length,
aperture, ISO, orientation). Reading the JSON blob 800 times is the expensive
part, so it happens exactly once here rather than once per chart.

What this module deliberately does NOT know:
  * album display names and album marks — those live behind album.cfg and
    the gallery's resolvers, so album rows come out keyed by folder path and the
    route decorates them (see /stats in gallery/pages.py).
  * how anything is drawn. Every dataset is returned already reduced to
    {label, value, pct, …} rows, where `pct` is 0–100 against the series
    maximum. The Jinja chart macros in _charts.html only place rectangles;
    they never do arithmetic, which is what keeps the SVG free of anything
    the CSP would have to allow.

Every number here comes from photos the gallery already serves publicly —
there is no per-visitor data, nothing is logged, and the page is safe to
hand to anyone.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime

from . import search

# ISO is continuous and phone cameras land on arbitrary values (320, 500,
# 1250 …), so it is the one capture fact that is bucketed rather than
# counted per exact value. Upper bounds, inclusive; the last row catches
# everything above.
_ISO_BUCKETS = ((50, "≤ 50"), (100, "51–100"), (200, "101–200"), (400, "201–400"),
                (800, "401–800"), (1600, "801–1600"))
_ISO_OVER = "1600+"

# how many rows a "top N" ranking keeps before the tail is folded into one
TOP_N = 8
TOP_N_ALBUMS = 10
# the timeline shows a rolling window ending at the newest photo. Older
# material still counts towards every other figure on the page — the window
# is about legibility, not about hiding anything, and the page says so.
MONTHS_WINDOW = 24


def exif_number(v):
    """EXIF numbers arrive as int, float or Rational depending on the tag and
    the writer. Anything that is not a real, positive number is not a fact."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:  # NaN / inf / 0
        return None
    return float(v)


def fmt_num(v: float) -> str:
    """2.0 -> '2', 1.60 -> '1.6'. Shared with the album stat rows."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def clean_device(make: str | None, model: str | None) -> str | None:
    """Human camera name from EXIF Make/Model: 'Apple' + 'iPhone 17' ->
    'iPhone 17'; 'FUJIFILM' + 'X100V' -> 'FUJIFILM X100V'. Drops a Make the
    Model already echoes.

    One definition for the album pages and the /stats camera chart. There
    used to be two, and they disagreed about the same photos: the album said
    "iPhone 17", the chart said "Apple iPhone 17"."""
    make = str(make or "").strip()
    model = str(model or "").strip()
    if not model:
        return make or None
    # Apple brands by model alone ("iPhone 17", never "Apple iPhone 17")
    if make.lower() == "apple":
        return model
    if make and make.split()[0].lower() in model.lower():
        return model
    return f"{make} {model}" if make else model


def _rank(counter: Counter, top: int = TOP_N, link=None) -> list[dict]:
    """Counter -> descending rows with a percentage of the series maximum.
    Every row is kept; the ones past `top` are marked `extra`, and the chart
    folds them behind a disclosure (see fold). `link(key)` gives each row
    its search."""
    items = counter.most_common()
    queries = [link(k) for k, _ in items] if link else None
    return fold(_rows([(str(k), n) for k, n in items], queries), top)


# One month with 435 photos next to twenty months with three or four is a
# real shape, and the chart should show it — but a bar 0.7% long is
# indistinguishable from an empty slot, which turns "rarely" into "never".
# A non-zero value therefore never draws shorter than this. Zero still draws
# nothing at all, so the one distinction that matters stays intact.
MIN_PCT = 4.0


def _pct(n: int, top: int) -> float:
    """Value as a share of the series maximum, floored so a small non-zero
    bar stays visible (see MIN_PCT)."""
    if not n or not top:
        return 0.0
    return max(MIN_PCT, round(n * 100.0 / top, 2))


def _band(n: int, top: int) -> int:
    """Which step of the accent ramp a value sits on, 1 (faintest) to 3.

    Colour here encodes MAGNITUDE, which is the one thing a chart means — so
    it is derived from the value against the series maximum, never from the
    row's position. That distinction matters for the charts whose rows are
    NOT ranked (focal length, aperture, ISO and the calendar axes run in
    their natural order): shading those by position would say "the leftmost
    is the biggest", which is a lie the eye believes before it reads a
    number."""
    if not n or not top:
        return 0
    share = n * 100.0 / top
    return 3 if share >= 55 else 2 if share >= 20 else 1


def _rows(pairs: list[tuple[str, int]], queries: list[str | None] | None = None) -> list[dict]:
    """Shared row shape for every chart: a label, a raw value, and the value
    as a percentage of the series MAXIMUM (never of the total — these are bar
    lengths, not slices of a pie). The single highest row is marked `peak` so
    exactly one bar per chart can carry the accent instead of all of them
    shouting equally; ties go to the first, which is the ranked winner.
    `queries`, parallel to `pairs`, is the search each row links to (the
    bars macro turns a row's `q` into a link to those photos)."""
    top = max((n for _, n in pairs), default=0)
    marked = False
    out = []
    for i, (label, n) in enumerate(pairs):
        is_peak = not marked and n == top and n > 0
        marked = marked or is_peak
        out.append({
            "label": label,
            "value": n,
            "pct": _pct(n, top),
            "peak": is_peak,
            "band": _band(n, top),
            "q": queries[i] if queries else None,
            # past the fold of a long ranking (see fold)
            "extra": False,
        })
    return out


def rows(pairs: list[tuple[str, int]]) -> list[dict]:
    """Public entry to the row shape above, for the one dataset the route
    builds itself (tags come from a join, not from the images table)."""
    return _rows(pairs)


def fold(rows: list[dict], keep: int) -> list[dict]:
    """Mark every row past the first `keep` as `extra`.

    A ranking used to end in one "+14 more" row that summed the tail and
    named none of it. The rows stay now, and the bars macro shows the first
    `keep` and folds the rest into a <details> -- a native disclosure, so it
    opens without a script. `pct` is untouched: a folded bar is still
    measured against the whole series, not against what is left."""
    for row in rows[keep:]:
        row["extra"] = True
    return rows


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) + delta
    return i // 12, i % 12 + 1


def _parse_ts(raw) -> datetime | None:
    """`taken_at` is written as an ISO wall-clock string ('2026-08-29T16:29:34')
    with no zone — read it as-is; a photo's hour means the hour it was taken
    where it was taken, which is exactly what the rhythm charts want."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:19])
    except ValueError:
        return None


def collect(conn, month_name, weekday_name) -> dict:
    """Every dataset the /stats page draws.

    `month_name(year, month)` and `weekday_name(idx)` are injected so the
    labels come out in the viewer's language without this module importing
    i18n (idx 0 = Monday).
    """
    rows = conn.execute(
        "SELECT album, size, width, height, taken_at, exif_json, is_showcase FROM images"
    ).fetchall()

    total = len(rows)
    total_bytes = 0
    featured = 0
    per_album: Counter = Counter()
    per_month: Counter = Counter()
    per_day: Counter = Counter()
    per_weekday: Counter = Counter()
    per_hour: Counter = Counter()
    devices: Counter = Counter()
    focals: Counter = Counter()
    apertures: Counter = Counter()
    isos: Counter = Counter()
    shapes: Counter = Counter()
    pixels = 0
    dated = 0
    first_ts = last_ts = None

    for r in rows:
        total_bytes += r["size"] or 0
        featured += 1 if r["is_showcase"] else 0
        per_album[r["album"]] += 1

        try:
            exif = json.loads(r["exif_json"]) if r["exif_json"] else {}
        except (ValueError, TypeError):
            exif = {}

        # ---- when -------------------------------------------------------
        ts = _parse_ts(r["taken_at"])
        if ts:
            dated += 1
            d = ts.date()
            per_month[_month_key(d)] += 1
            per_day[d.isoformat()] += 1
            per_weekday[d.weekday()] += 1
            per_hour[ts.hour] += 1
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

        # ---- what it was shot with --------------------------------------
        dev = clean_device(exif.get("Make"), exif.get("Model"))
        if dev:
            devices[dev] += 1
        fl = exif_number(exif.get("FocalLengthIn35mmFilm")) or exif_number(exif.get("FocalLength"))
        if fl:
            focals[round(fl)] += 1
        fn = exif_number(exif.get("FNumber"))
        if fn:
            apertures[round(fn, 1)] += 1
        iso = exif_number(exif.get("ISOSpeedRatings"))
        if iso:
            label = _ISO_OVER
            for hi, name in _ISO_BUCKETS:
                if iso <= hi:
                    label = name
                    break
            isos[label] += 1

        # ---- shape ------------------------------------------------------
        # Upright already: the scanner swaps the two for EXIF Orientation
        # 5-8 when it indexes the photo, so what is stored is what is shown.
        w, h = r["width"], r["height"]
        if w and h:
            pixels += w * h
            ratio = w / h
            shapes["landscape" if ratio > 1.02 else "portrait" if ratio < 0.98 else "square"] += 1

    # ---- monthly timeline: a rolling window ending at the newest photo ---
    months: list[dict] = []
    if last_ts:
        end_y, end_m = last_ts.year, last_ts.month
        span = []
        for back in range(MONTHS_WINDOW - 1, -1, -1):
            y, m = _shift_month(end_y, end_m, -back)
            span.append((y, m))
        # never show empty months from before the archive starts
        if first_ts:
            span = [(y, m) for (y, m) in span
                    if (y, m) >= (first_ts.year, first_ts.month)]
        peak = max((per_month.get(f"{y:04d}-{m:02d}", 0) for y, m in span), default=0) or 1
        for y, m in span:
            n = per_month.get(f"{y:04d}-{m:02d}", 0)
            months.append({
                "label": month_name(y, m),
                # the year rides along as a second line, printed only when it
                # changes, so a two-year window reads without a legend
                "year": str(y),
                "value": n,
                "pct": _pct(n, peak),
                "peak": n == peak and n > 0,
                "band": _band(n, peak),
            })

    # ---- weekday + hour: fixed-length series, so a quiet slot is a gap ----
    wk_peak = max(per_weekday.values(), default=0) or 1
    weekdays = [{
        "label": weekday_name(i),
        "value": per_weekday.get(i, 0),
        "pct": _pct(per_weekday.get(i, 0), wk_peak),
        "peak": per_weekday.get(i, 0) == wk_peak and wk_peak > 1,
        "band": _band(per_weekday.get(i, 0), wk_peak),
    } for i in range(7)]

    hr_peak = max(per_hour.values(), default=0) or 1
    hours = [{
        "label": f"{i:02d}",
        "value": per_hour.get(i, 0),
        "pct": _pct(per_hour.get(i, 0), hr_peak),
        "peak": per_hour.get(i, 0) == hr_peak and hr_peak > 1,
        "band": _band(per_hour.get(i, 0), hr_peak),
    } for i in range(24)]

    busiest_day, busiest_n = (per_day.most_common(1) or [(None, 0)])[0]

    kept_focals = [(k, n) for k, n in sorted(focals.items())
                   if n >= max(1, total // 200)][:12]
    # each ISO bucket links to the range it counts: "401–800" is iso:401-800
    iso_pairs, iso_queries, below = [], [], 0
    for hi, name in _ISO_BUCKETS:
        if isos.get(name):
            iso_pairs.append((name, isos[name]))
            iso_queries.append(search.term("iso", f"{below + 1 if below else ''}-{hi}"))
        below = hi
    if isos.get(_ISO_OVER):
        iso_pairs.append((_ISO_OVER, isos[_ISO_OVER]))
        iso_queries.append(search.term("iso", f"{below + 1}-"))

    return {
        "total": total,
        "featured": featured,
        "bytes": total_bytes,
        "pixels": pixels,
        "dated": dated,
        "first": first_ts.date().isoformat() if first_ts else None,
        "last": last_ts.date().isoformat() if last_ts else None,
        "busiest_day": busiest_day,
        "busiest_n": busiest_n,
        "active_days": len(per_day),
        # album rows stay keyed by folder path — the route turns them into
        # display names, marks and links (see the module docstring)
        "albums_raw": per_album,
        "months": months,
        "weekdays": weekdays,
        "hours": hours,
        "cameras": _rank(devices, TOP_N, link=lambda name: search.term("camera", name)),
        "focals": _rows([(f"{k} mm", n) for k, n in kept_focals],
                        [search.term("mm", k) for k, _ in kept_focals]),
        "apertures": _rows([(f"ƒ{fmt_num(k)}", n) for k, n in sorted(apertures.items())],
                           [search.term("f", fmt_num(k)) for k in sorted(apertures)]),
        "isos": _rows(iso_pairs, iso_queries),
        "shapes_raw": shapes,
    }


# ---- polar dial ---------------------------------------------------------
# The 24-hour axis is the one series on the page that is CYCLICAL: 23:00 is
# next to 00:00, and a straight row of columns cuts that neighbourhood in
# half. Drawn round, the archive's night gap is a wedge you see at a glance
# instead of two stubs at opposite ends of a bar chart. Geometry is computed
# here so the template only places lines — same rule as everywhere else.
DIAL_BOX = 240.0      # viewBox side; the SVG scales to whatever CSS gives it
DIAL_IN = 44.0        # hub radius: the spokes start here, the readout sits inside
DIAL_OUT = 100.0      # the 100%-of-peak ring
DIAL_LBL = 114.0      # where the cardinal hour labels sit


def dial(rows: list[dict], every: int = 3) -> dict:
    """A fixed cyclical series (the hours) as spokes on a clock face.

    Returns the geometry the template needs and nothing else: one spoke per
    row with its two endpoints, the two reference circles, the hour ticks,
    and the peak row for the readout in the hub. Angles start at the top and
    run clockwise — 00:00 is up.

    `every` is how often an hour gets a printed tick. It is 3, not 6: a dial
    LOOKS like a clock, and on a clock the right-hand side is 3 o'clock, not
    06:00 — with only four ticks a reader has to work out that this face
    carries twenty-four hours rather than twelve. Eight ticks state it, and
    the note in the card head says it in words.
    """
    n = len(rows) or 1
    c = DIAL_BOX / 2
    span = DIAL_OUT - DIAL_IN
    spokes = []
    labels = []
    for i, r in enumerate(rows):
        ang = math.radians(i * (360.0 / n) - 90.0)
        cos, sin = math.cos(ang), math.sin(ang)
        # a zero row draws nothing at all; the gap IS the reading
        reach = DIAL_IN + span * (r["pct"] / 100.0) if r["value"] else DIAL_IN
        spokes.append({
            "label": r["label"], "value": r["value"],
            "peak": r["peak"], "band": r["band"],
            "x1": round(c + DIAL_IN * cos, 2), "y1": round(c + DIAL_IN * sin, 2),
            "x2": round(c + reach * cos, 2),   "y2": round(c + reach * sin, 2),
        })
        if i % every == 0:
            labels.append({
                "text": r["label"],
                "x": round(c + DIAL_LBL * cos, 2),
                "y": round(c + DIAL_LBL * sin, 2),
            })
    peak = max(rows, key=lambda r: r["value"], default=None) if rows else None
    return {
        "cx": c, "cy": c, "box": DIAL_BOX,
        "r_in": DIAL_IN, "r_out": DIAL_OUT,
        "spokes": spokes, "labels": labels,
        "peak": peak if peak and peak["value"] else None,
        "rows": rows,
    }


# ---- stacked proportion bar ---------------------------------------------
def stack(rows: list[dict]) -> list[dict]:
    """Rows that genuinely sum to a whole (the orientation split) as segments
    of one bar: each gets its share of the total and the x offset it starts
    at. A ranked bar chart answers "which is biggest"; this answers "how is
    it divided", which is the actual question about orientation — and it is
    the only series on the page where the parts add up to every photo.

    Rounding is absorbed by the last segment so the bar always closes at
    exactly 100 and never leaves a hairline of background at its end."""
    total = sum(r["value"] for r in rows)
    if not total:
        return []
    out, x = [], 0.0
    for i, r in enumerate(rows):
        share = r["value"] * 100.0 / total
        w = round(100.0 - x, 2) if i == len(rows) - 1 else round(share, 2)
        out.append({
            **r,
            "share": share,
            "share_h": f"{share:.0f}%" if share >= 1 else "<1%",
            "x": round(x, 2),
            "w": w,
            # segments are ordered largest-first, so here the ramp step CAN
            # come from the position — it is the rank
            "seg": min(i + 1, 4),
        })
        x += w
    return out


def album_rows(counter: Counter, label_of, top: int = TOP_N_ALBUMS) -> list[dict]:
    """Album counts -> chart rows carrying the album path as well, so each bar
    can link into the album it measures. `label_of(path)` supplies the display
    name. Rows past `top` are folded, not dropped (see fold)."""
    items = counter.most_common()
    out = _rows([(label_of(path), n) for path, n in items])
    for row, (path, _) in zip(out, items):
        row["album"] = path
    return fold(out, top)


def shape_rows(shapes: Counter, label_of) -> list[dict]:
    """Orientation counts in a fixed order (landscape / portrait / square) so
    the row never reshuffles between two visits to the page."""
    order = [k for k in ("landscape", "portrait", "square") if shapes.get(k)]
    return _rows([(label_of(k), shapes[k]) for k in order])
