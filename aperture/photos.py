"""Photos as queries.

The scope an album stands for, the rows it returns, how they sort (by date,
name, size, curated order or by day), how a photo is serialized for a page or
the API, and the EXIF readout.
"""

from __future__ import annotations

import json
import re
from datetime import date

from . import albums, capture, cfgio, config, db, i18n, scanner, search, stats, trips


def showcase_rows(album: str | None = None, limit: int = 50, random_order: bool = False,
                   subtree: bool | None = None):
    """Featured photos, optionally filtered to one album. `subtree=True`
    widens the filter to the album's whole folder tree, so photos featured
    inside sub-albums surface on the parent album's page too; `None` lets the
    album decide (a `collection = true` album is its whole subtree, see
    photo_scope). substr() (not LIKE) keeps `_`/`%` in album names from
    acting as wildcards."""
    c = db.conn()
    if album is not None:
        where_simple, _join, params, _coll, _wide = photo_scope(album, subtree)
        where = f"WHERE is_showcase = 1 AND {where_simple}"
    else:
        listed, listed_params = albums.unlisted_clause()
        where = f"WHERE is_showcase = 1 AND {listed}"
        params = tuple(listed_params)
    order = (
        "ORDER BY RANDOM()"
        if random_order
        else "ORDER BY taken_at IS NULL, taken_at DESC, mtime DESC, filename ASC"
    )
    rows = c.execute(
        f"SELECT * FROM images {where} {order} LIMIT ?",
        params + (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def serialize_photo(row: dict, base: str, tags: list[str] | None = None) -> dict:
    """One photo as the API returns it. The `_abs` URLs are what an external
    embedder needs (they carry PUBLIC_BASE_URL, see context.public_base_url); the
    relative ones are handier same-origin. `tags` is only attached when the
    caller actually loaded them (see tags_for_images) — absent means "not
    requested", never "none"."""
    rel = row["rel_path"]
    item = {
        "rel_path": rel,
        "album": row["album"],
        "filename": row["filename"],
        "width": row.get("width"),
        "height": row.get("height"),
        "size": row.get("size"),
        "taken_at": row.get("taken_at"),
        "mtime": row.get("mtime"),
        "featured": bool(row.get("is_showcase")),
        # the file URLs carry the photo's version stamp (scanner.media_url)
        "urls": {
            "thumb": scanner.media_url("thumb", row),
            "preview": scanner.media_url("preview", row),
            "full": scanner.media_url("full", row),
            "page": f"/image/{rel}",
            "api": f"/api/photo/{rel}",
            "thumb_abs": scanner.media_url("thumb", row, base),
            "preview_abs": scanner.media_url("preview", row, base),
            "full_abs": scanner.media_url("full", row, base),
            "page_abs": f"{base}/image/{rel}",
            "api_abs": f"{base}/api/photo/{rel}",
        },
    }
    if tags is not None:
        item["tags"] = tags
    return item


def tags_for_images(ids: list[int]) -> dict[int, list[str]]:
    """{image_id: [tag, ...]} for a batch of photos — one query instead of one
    per row. The id list travels as a JSON array (like albums.recompute_featured) so
    a large page can't hit the host-parameter limit."""
    if not ids:
        return {}
    rows = db.conn().execute(
        """SELECT it.image_id AS iid, t.name AS name FROM image_tags it
           JOIN tags t ON t.id = it.tag_id
           WHERE it.image_id IN (SELECT value FROM json_each(?))
           ORDER BY t.name""",
        (json.dumps(ids),),
    ).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["iid"], []).append(r["name"])
    return out


def photo_scope(album: str, subtree: bool | None = None) -> tuple[str, str, tuple, bool, bool]:
    """SQL scope for "the photos of this album", collection-aware.

    Returns (where_simple, where_join, params, collection, subtree) —
    `where_simple` for queries over `images` alone, `where_join` for the ones
    that join (columns qualified as `i.`). By default a `collection = true`
    album resolves to its whole subtree and every other album to its own
    folder; `subtree=True/False` forces either scope regardless of the cfg.
    substr() (not LIKE) so `_`/`%` in album names can't act as wildcards."""
    collection = config.album_collection(album)
    wide = collection if subtree is None else bool(subtree)
    if not wide:
        return ("album = ?", "i.album = ?", (album,), collection, False)
    # a whole subtree stops at any unlisted album inside it
    # (albums.unlisted_clause), unless this album is one of them
    prefix = album + "/"
    listed, listed_params = albums.unlisted_clause("album", keep=album)
    listed_i, _same = albums.unlisted_clause("i.album", keep=album)
    return (f"(album = ? OR substr(album, 1, ?) = ?) AND {listed}",
            f"(i.album = ? OR substr(i.album, 1, ?) = ?) AND {listed_i}",
            (album, len(prefix), prefix, *listed_params), collection, True)


# ----- album stats (auto EXIF/size readouts + editorial cfg facts) -------
# The little HUD-style KEY / VALUE block under an album's description. Two
# groups feed it:
#   * capture  — derived automatically from the album's own photos (EXIF +
#                file size), so they cost zero upkeep: SPAN (date range),
#                DEVICE, FOCAL, APERTURE, DATA. A missing field just drops
#                that one line.
#   * context  — editorial, from album.cfg: `loc = City, Country` plus any
#                number of freeform `stat = Label: Value` lines.
# The KEY labels are HUD tokens and stay English by design; only the SPAN
# *value* localises (i18n.date_span). `stats = off` in album.cfg hides the
# whole block.
def humanize_bytes(n: int | None) -> str | None:
    """1311994866 -> '1.2 GB'. None for empty/zero."""
    if not n or n <= 0:
        return None
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} {units[i]}"
    return f"{f:.0f} {units[i]}" if f >= 100 else f"{f:.1f} {units[i]}"


# ----- sort options -----------------------------------------------------
# Labels are i18n keys (see i18n.STRINGS), resolved per request language in
# the *_for_template helpers.
# image grid (inside an album / search results)
SORT_IMAGE_OPTIONS = [
    ("date_desc", "sort.date_desc", "taken_at IS NULL, taken_at DESC, mtime DESC, filename ASC"),
    ("date_asc",  "sort.date_asc",  "taken_at IS NULL, taken_at ASC,  mtime ASC,  filename ASC"),
    ("name_asc",  "sort.name_asc",  "filename COLLATE NOCASE ASC"),
    ("name_desc", "sort.name_desc", "filename COLLATE NOCASE DESC"),
    ("size_desc", "sort.size_desc", "size DESC, filename ASC"),
    ("size_asc",  "sort.size_asc",  "size ASC, filename ASC"),
]
SORT_IMAGE_DEFAULT = "date_desc"
SORT_IMAGE_SQL = {k: sql for k, _, sql in SORT_IMAGE_OPTIONS}

# album list (front page)
SORT_ALBUM_OPTIONS = [
    ("latest_desc", "sort.latest_desc",    "MAX(taken_at) IS NULL, MAX(taken_at) DESC, album COLLATE NOCASE ASC"),
    ("latest_asc",  "sort.latest_asc",     "MAX(taken_at) IS NULL, MAX(taken_at) ASC,  album COLLATE NOCASE ASC"),
    ("name_asc",    "sort.album_name_asc", "album COLLATE NOCASE ASC"),
    ("name_desc",   "sort.album_name_desc","album COLLATE NOCASE DESC"),
    ("count_desc",  "sort.count_desc",     "count DESC, album COLLATE NOCASE ASC"),
    ("count_asc",   "sort.count_asc",      "count ASC, album COLLATE NOCASE ASC"),
]
SORT_ALBUM_DEFAULT = "latest_desc"
SORT_ALBUM_SQL = {k: sql for k, _, sql in SORT_ALBUM_OPTIONS}

# pseudo sort key backed by a cfg list (album.cfg `order` / gallery.cfg
# `album_order`) instead of SQL; only offered when such a list exists
SORT_CURATED = "curated"
SORT_CURATED_LABEL_KEY = "sort.curated"

# Second pseudo key: newest day first, with the grid split into one framed
# section per capture day — the same "labeled set" language the Curated view
# uses on /albums, but derived from EXIF instead of a cfg list. It runs on the
# plain `date_desc` SQL (which also parks undated photos at the end, so their
# section stays last); only the grouping is extra, so every consumer that
# doesn't render sections (the API, the neighbour walk on the image page) just
# gets the ordinary newest-first list. Offered only when an album's photos
# actually span more than one day (see scope_day_count).
SORT_DAYS = "days"
SORT_DAYS_LABEL_KEY = "sort.days"
SORT_DAYS_BASE = "date_desc"


def pick_sort(value: str | None, allowed, default: str) -> str:
    return value if value in allowed else default


def scope_day_count(where_sql: str, params) -> int:
    """How many distinct capture days the photos in a scope cover. Drives
    whether the "By day" sort is offered at all — a single-day album (or one
    without any EXIF dates) has nothing to group."""
    row = db.conn().execute(
        f"SELECT COUNT(DISTINCT substr(taken_at, 1, 10)) FROM images "
        f"WHERE {where_sql} AND taken_at IS NOT NULL",
        params,
    ).fetchone()
    return int(row[0] or 0) if row else 0


def resolve_image_sort(cfg: dict[str, list[str]], sort: str | None,
                        curated_order: list[str], days: bool = False
                        ) -> tuple[str, str, str]:
    """(current, default, base) sort keys for an image scope. `default` comes
    from album.cfg `sort =`, the ?sort= query param wins over it, and both are
    filtered against what this album actually offers — the two pseudo keys are
    only allowed when their backing data exists. `base` is the SORT_IMAGE_SQL
    key to run: curated is reordered in Python afterwards, "by day" is plain
    chronological SQL that the caller may then group."""
    allowed = set(SORT_IMAGE_SQL)
    if curated_order:
        allowed.add(SORT_CURATED)
    if days:
        allowed.add(SORT_DAYS)
    default_sort = pick_sort(cfgio.first(cfg, "sort"), allowed, SORT_IMAGE_DEFAULT)
    current = pick_sort(sort, allowed, default_sort)
    if current == SORT_CURATED:
        base = SORT_IMAGE_DEFAULT
    elif current == SORT_DAYS:
        base = SORT_DAYS_BASE
    else:
        base = current
    return current, default_sort, base


def day_sections(images: list[dict], trip: dict | None,
                  lang: str = i18n.DEFAULT_LANG) -> list[dict]:
    """Split a date-ordered image list into one section per capture day, in
    whatever direction the list already has (SORT_DAYS runs newest first):
    [{key, date, weekday, day, stop, images}, …]. `day` is the trip day number
    for a trip album (counted from the outbound flight, so a gap in the photos
    still shows as a jump) and a plain 1..n index counted from the album's
    OLDEST day otherwise — so the numbers stay stable no matter which way the
    list runs. Photos without EXIF date keep their SQL position at the end and
    land in a single trailing section (date None)."""
    trip_start = ""
    if trip:
        stops = trip.get("stops") or []
        trip_start = (trip.get("depart") or "")[:10]
        if not trip_start and stops:
            trip_start = (stops[0].get("start") or "")[:10]
    sections: list[dict] = []
    by_key: dict[str, dict] = {}
    for im in images:
        key = (im.get("taken_at") or "")[:10] or ""
        sec = by_key.get(key)
        if sec is None:
            sec = {
                "key": key or "undated",
                "date": key or None,
                "date_h": i18n.fmt_date(lang, key) if key else None,
                "weekday": i18n.weekday_label(lang, key),
                "stop": trips.trip_stop_on(trip, key, lang) if key else None,
                "day": None,
                "day_h": None,
                "images": [],
            }
            by_key[key] = sec
            sections.append(sec)
        sec["images"].append(im)
    dated = sorted((s for s in sections if s["date"]), key=lambda s: s["date"])
    for i, sec in enumerate(dated):
        if trip_start:
            n = _days_between(trip_start, sec["date"])
            sec["day"] = n + 1 if n is not None and n >= 0 else None
        else:
            sec["day"] = i + 1
        if sec["day"]:
            sec["day_h"] = i18n.day_label(lang, sec["day"])
    return sections


def _days_between(start: str, end: str) -> int | None:
    """Whole days from `start` to `end` (both YYYY-MM-DD), None if either
    side doesn't parse."""
    try:
        a = date(int(start[:4]), int(start[5:7]), int(start[8:10]))
        b = date(int(end[:4]), int(end[5:7]), int(end[8:10]))
    except (ValueError, IndexError, TypeError):
        return None
    return (b - a).days


def qualify_sort(order_sql: str) -> str:
    """Prefix the image columns of a SORT_IMAGE_SQL clause with the `i.` alias,
    so the same clause also works in the queries that join tags."""
    for col in ("filename", "taken_at", "mtime", "size"):
        order_sql = order_sql.replace(col, f"i.{col}")
    return order_sql


def image_sort_options_for_template(current: str, curated: bool = False,
                                     lang: str = i18n.DEFAULT_LANG,
                                     days: bool = False) -> list[dict]:
    keys = ([(SORT_CURATED, SORT_CURATED_LABEL_KEY)] if curated else [])
    keys += ([(SORT_DAYS, SORT_DAYS_LABEL_KEY)] if days else [])
    keys += [(k, label_key) for k, label_key, _ in SORT_IMAGE_OPTIONS]
    return [{"key": k, "label": i18n.t(lang, label_key), "active": k == current}
            for k, label_key in keys]


def album_sort_options_for_template(current: str, curated: bool = False,
                                     lang: str = i18n.DEFAULT_LANG) -> list[dict]:
    keys = ([(SORT_CURATED, SORT_CURATED_LABEL_KEY)] if curated else [])
    keys += [(k, label_key) for k, label_key, _ in SORT_ALBUM_OPTIONS]
    return [{"key": k, "label": i18n.t(lang, label_key), "active": k == current}
            for k, label_key in keys]


def active_sort_label(options: list[dict]) -> str:
    return next((o["label"] for o in options if o["active"]), "")


def curated_photo_order(album: str, cfg: dict[str, list[str]]) -> list[str]:
    """Resolved album.cfg `order` list (curated photo order) as rel_paths,
    [] when the album doesn't configure one."""
    items = cfg.get("order", [])
    return albums.resolve_photo_refs(album, items) if items else []


def apply_curated_order(images: list[dict], curated_order: list[str]) -> list[dict]:
    """Stable-sort image dicts into the curated order: listed photos first
    in the given order, unlisted ones keep their previous (date) order."""
    pos = {rel: i for i, rel in enumerate(curated_order)}
    images.sort(key=lambda r: pos.get(r["rel_path"], len(pos)))
    return images


def random_subtree_rows(album: str, limit: int = 8) -> list[dict]:
    """Random photos from an album's whole subtree, for album.cfg
    `reel = random`."""
    prefix = album + "/"
    listed, listed_params = albums.unlisted_clause(keep=album)
    rows = db.conn().execute(
        "SELECT * FROM images WHERE (album = ? OR substr(album, 1, ?) = ?) "
        f"AND {listed} ORDER BY RANDOM() LIMIT ?",
        (album, len(prefix), prefix, *listed_params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def humanize_pixels(n: int | None) -> str | None:
    """Total sensor area as something a person can read: 1_240_000_000 ->
    '1.2 GP'. Megapixels below a billion, gigapixels above."""
    if not n or n <= 0:
        return None
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        return f"{v:.0f} GP" if v >= 100 else f"{v:.1f} GP"
    v = n / 1_000_000
    return f"{v:.0f} MP"


def photo_rows(*, album: str | None = None, subtree: bool | None = None,
                tag: str | None = None, q: str | None = None, featured: bool = False,
                order_sql: str | None = None, random_order: bool = False,
                limit: int | None = None, offset: int = 0):
    """The one photo query behind /api/photos, /api/album and /api/showcase.
    Returns (rows, total, scope) — `total` counts the whole match, not the
    page. Filters compose; the tag/search ones use EXISTS rather than a JOIN
    so a photo with several tags still comes back once (no DISTINCT needed,
    which would fight the ORDER BY). `limit=None` fetches everything, which
    is what the curated sort needs before it reorders in Python."""
    where: list[str] = []
    params: list = []
    scope = {"album": album, "collection": False, "subtree": False}
    if album:
        _simple, where_join, scope_params, collection, wide = photo_scope(album, subtree)
        where.append(where_join)
        params += list(scope_params)
        scope.update(collection=collection, subtree=wide)
    else:
        # the gallery at large leaves unlisted albums out; asked for by name,
        # an unlisted album answers like its page does
        listed, listed_params = albums.unlisted_clause("i.album")
        where.append(listed)
        params += listed_params
    if featured:
        where.append("i.is_showcase = 1")
    if tag:
        where.append("EXISTS (SELECT 1 FROM image_tags it JOIN tags t ON t.id = it.tag_id "
                     "WHERE it.image_id = i.id AND t.name = ?)")
        params.append(tag)
    if q:
        # the grammar the /search page uses (search.py): words plus filters
        cond, cond_params = search.condition(search.parse(q), "i")
        where.append(f"({cond})")
        params += cond_params
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    c = db.conn()
    total = c.execute(f"SELECT COUNT(*) AS n FROM images i {clause}", params).fetchone()["n"]
    order = "RANDOM()" if random_order else qualify_sort(
        order_sql or SORT_IMAGE_SQL[SORT_IMAGE_DEFAULT])
    rows = c.execute(
        f"SELECT i.* FROM images i {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [-1 if limit is None else limit, max(0, offset)],
    ).fetchall()
    return [dict(r) for r in rows], total, scope


def serialize_photos(rows: list[dict], base: str, with_tags: bool = False) -> list[dict]:
    tag_map = tags_for_images([r["id"] for r in rows if r.get("id")]) if with_tags else {}
    return [serialize_photo(r, base, tag_map.get(r.get("id"), []) if with_tags else None)
            for r in rows]


def extract_description(exif: dict) -> str | None:
    if not exif:
        return None
    # XMP-dc:Description (the standard "description" field) takes priority;
    # the EXIF/XP keys remain as fallbacks for files that only carry those.
    for key in (scanner.XMP_DESCRIPTION_KEY, "ImageDescription", "XPComment", "XPSubject", "XPTitle", "UserComment"):
        v = exif.get(key)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (list, tuple)):
            try:
                v = bytes(v).decode("utf-16-le", errors="ignore")
            except Exception:
                v = " ".join(str(x) for x in v)
        s = str(v).replace("\x00", "").strip()
        if s:
            return s
    return None


def prettify_exif(exif: dict, lang: str = i18n.DEFAULT_LANG) -> list[tuple[str, str]]:
    """The EXIF readout as (label, value) pairs -- the API and CLI shape."""
    return [(row["key"], row["val"]) for row in exif_rows(exif, lang)]


def exif_rows(exif: dict, lang: str = i18n.DEFAULT_LANG) -> list[dict]:
    """The EXIF readout as {key, val, q} rows. `q` is the search that finds
    every photo sharing that value (search.py), None where the value is not
    one of the searchable facts. The facts come from capture.facts, the same
    reading the index stores, so a link always finds at least this photo."""
    if not exif:
        return []
    fact = capture.facts(exif)
    has_35mm = stats.exif_number(exif.get("FocalLengthIn35mmFilm")) is not None
    keys = [
        ("Make", "exif.make"),
        ("Model", "exif.model"),
        ("LensModel", "exif.lens"),
        ("DateTimeOriginal", "exif.date_taken"),
        ("ExposureTime", "exif.exposure"),
        ("FNumber", "exif.aperture"),
        ("ISOSpeedRatings", "exif.iso"),
        ("FocalLength", "exif.focal"),
        ("FocalLengthIn35mmFilm", "exif.focal35"),
        ("Flash", "exif.flash"),
        ("WhiteBalance", "exif.wb"),
        ("ExposureProgram", "exif.program"),
        ("MeteringMode", "exif.metering"),
        ("Orientation", "exif.orientation"),
        ("Software", "exif.software"),
    ]
    out: list[dict] = []
    for k, label_key in keys:
        if k in exif and exif[k] not in (None, "", []):
            v = exif[k]
            q = None
            if k == "ExposureTime" and isinstance(v, (int, float)) and v > 0:
                if v < 1:
                    v = f"1/{round(1/v)} s"
                else:
                    v = f"{v} s"
            elif k == "FNumber" and isinstance(v, (int, float)):
                v = f"f/{v:.1f}"
            elif k in ("FocalLength", "FocalLengthIn35mmFilm") and isinstance(v, (int, float)):
                v = f"{v:.0f} mm"
            if k == "Model" and fact["camera"]:
                q = search.term("camera", fact["camera"])
            elif k == "LensModel" and fact["lens"]:
                q = search.term("lens", fact["lens"])
            elif k == "FNumber" and fact["aperture"]:
                q = search.term("f", stats.fmt_num(fact["aperture"]))
            elif k == "ISOSpeedRatings" and fact["iso"]:
                q = search.term("iso", fact["iso"])
            # the index keeps the 35 mm equivalent when there is one, so only
            # that line links -- the real focal length would find nothing
            elif k == "FocalLengthIn35mmFilm" and fact["focal"]:
                q = search.term("mm", fact["focal"])
            elif k == "FocalLength" and fact["focal"] and not has_35mm:
                q = search.term("mm", fact["focal"])
            elif k == "DateTimeOriginal" and isinstance(v, str) and re.match(r"\d{4}:\d{2}:\d{2}", v):
                q = search.term("date", v[:10].replace(":", "-"))
            out.append({"key": i18n.t(lang, label_key), "val": str(v), "q": q})
    gps = exif.get("GPSInfo")
    if isinstance(gps, dict):
        lat = _gps_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _gps_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat is not None and lon is not None:
            out.append({"key": i18n.t(lang, "exif.gps"), "val": f"{lat:.6f}, {lon:.6f}", "q": None})
    return out


def _gps_to_deg(coord, ref):
    if not coord or not isinstance(coord, (list, tuple)) or len(coord) < 3:
        return None
    try:
        d, m, s = [float(x) for x in coord[:3]]
        deg = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            deg = -deg
        return deg
    except Exception:
        return None
