"""The JSON API, CORS-enabled. Its contract is versioned by API_VERSION."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import albums, brand, branding, cfgio, config, db, i18n, photos, schema, theme, trips
from ..runtime import settings
from . import context, media

log = logging.getLogger("aperture.api")
router = APIRouter()


@router.get("/api/trip-weather")
def api_trip_weather(trip: str):
    cfg = trips.TRIPS.get(trip)
    if not cfg:
        raise HTTPException(404, "unknown trip")
    now = time.time()
    # the lock doubles as stampede protection: concurrent misses queue up
    # behind the one request actually talking to Open-Meteo (sync endpoint,
    # so this blocks a threadpool worker, not the event loop)
    with trips.weather_lock:
        cached = trips.weather_cache.get(trip)
        fresh = cached is not None and now - cached[0] < trips.WEATHER_TTL
        # A recent failure is remembered as well, so an upstream outage costs
        # one timeout per WEATHER_RETRY_AFTER instead of one per page view —
        # the threadpool worker each of those parks for 8 seconds is the whole
        # reason this backoff exists.
        cooling = now - trips.weather_failed_at.get(trip, 0.0) < trips.WEATHER_RETRY_AFTER
        if fresh:
            data = cached[1]
        elif cooling:
            if cached is None:
                raise HTTPException(502, "weather upstream unavailable")
            data = cached[1]  # stale beats nothing
        else:
            try:
                data = trips.fetch_trip_weather(cfg)
                trips.weather_cache[trip] = (now, data)
                trips.weather_failed_at.pop(trip, None)
            except Exception:
                log.warning("trip weather fetch failed (%s)", trip, exc_info=True)
                trips.weather_failed_at[trip] = time.time()
                if cached is None:
                    raise HTTPException(502, "weather upstream unavailable")
                data = cached[1]  # stale beats nothing; retried after the backoff
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=600"})


# ----- JSON API ---------------------------------------------------------
# A read-only JSON view of everything the pages render, CORS-open so the
# gallery can be embedded elsewhere. Three rules hold across all of it:
#
#   * an album named in `album=` / the path resolves exactly like its page
#     does — an album.cfg `collection = true` album answers with its WHOLE
#     subtree (see photos.photo_scope), and says so via `scope.collection`.
#     `subtree=0|1` overrides that per request.
#   * photos always come back through photos.serialize_photo and albums through
#     albums.serialize_album, so the shapes are identical everywhere.
#   * anything language-dependent (descriptions, EXIF labels, date spans)
#     follows `lang=`, else the request's own language, and says which one
#     it picked in `lang`.
#
#   GET /api                   this index
#   GET /api/stats             gallery-wide counters
#   GET /api/albums            album cards (top level, or below ?parent=)
#   GET /api/album/{album}     one album: meta, description, stats, reel, images
#   GET /api/photos            photo query: album / tag / q / featured, paged
#   GET /api/photo/{rel_path}  one photo: EXIF, tags, neighbours
#   GET /api/tags              photo tags with counts
#   GET /api/showcase          featured photos (the original endpoint)
#   GET /api/shuffle           random photos
#   GET /api/trip-weather      trip stop conditions (see the trip section)
API_VERSION = 2
API_MAX_LIMIT = 200
API_VARY = "Accept-Language, Cookie"


  # for the language-dependent payloads
def _api_lang(request: Request, lang: str | None = None) -> str:
    """An explicit `lang=` wins over the visitor's cookie/Accept-Language, so
    an embedder can pin the language it wants without setting cookies."""
    code = (lang or "").strip().lower()
    return code if code in i18n.LANGS else context.request_lang(request)


def _api_limit(limit: int, default_max: int = API_MAX_LIMIT) -> int:
    return max(1, min(default_max, limit))


@router.get("/api")
def api_index(request: Request):
    """What this API offers, so a client can discover it without the README."""
    base = context.public_base_url(request)
    image_sorts = [photos.SORT_CURATED, photos.SORT_DAYS] + list(photos.SORT_IMAGE_SQL)
    album_sorts = [photos.SORT_CURATED] + list(photos.SORT_ALBUM_SQL)
    return context.json_cors({
        # the archive's own name, and separately the software serving it —
        # a client that wants to credit one should not end up citing the other
        "name": branding.site_brand()["title"],
        "product": brand.PRODUCT,
        "product_version": brand.VERSION,
        "vendor": brand.NAME,
        "vendor_url": brand.URL,
        "version": API_VERSION,
        "base_url": base,
        "languages": list(i18n.LANGS),
        "limits": {"max_limit": API_MAX_LIMIT},
        "sorts": {"images": image_sorts, "albums": album_sorts},
        "endpoints": [
            {"path": "/api/stats", "about": "gallery-wide counters", "params": {}},
            {"path": "/api/albums", "about": "album cards", "params": {
                "parent": "album path; omit for the top level",
                "sort": "|".join(album_sorts),
                "depth": "1..4 — nest sub-albums as `children`",
                "showcase": "1 = showcase albums only",
                "limit": f"1..{API_MAX_LIMIT}",
            }},
            {"path": "/api/album/{album}", "about": "one album in full", "params": {
                "images": "1 = include the photo grid",
                "sort": "|".join(image_sorts),
                "tag": "filter the grid by photo tag",
                "subtree": "0|1 — override the album's collection scope",
                "limit": f"1..{API_MAX_LIMIT}", "offset": "paging offset",
                "lang": "|".join(i18n.LANGS),
            }},
            {"path": "/api/photos", "about": "photo query", "params": {
                "album": "scope to an album (collection-aware)",
                "subtree": "0|1 — override that scope",
                "tag": "photo tag", "q": "search album / filename / tag",
                "featured": "1 = featured photos only",
                "sort": "|".join(photos.SORT_IMAGE_SQL), "random": "1 = random order",
                "tags": "1 = include each photo's tags",
                "limit": f"1..{API_MAX_LIMIT}", "offset": "paging offset",
            }},
            {"path": "/api/photo/{rel_path}", "about": "one photo with EXIF + neighbours",
             "params": {"col": "collection root the neighbours walk",
                        "sort": "|".join(image_sorts),
                        "neighbours": "0 = skip prev/next",
                        "lang": "|".join(i18n.LANGS)}},
            {"path": "/api/tags", "about": "photo tags with counts", "params": {
                "album": "scope to an album (collection-aware)",
                "subtree": "0|1 — override that scope",
                "limit": f"1..{API_MAX_LIMIT}",
            }},
            {"path": "/api/showcase", "about": "featured photos", "params": {
                "album": "scope to an album (collection-aware)",
                "subtree": "0|1 — override that scope",
                "random": "1 = random order", "limit": f"1..{API_MAX_LIMIT}",
            }},
            {"path": "/api/shuffle", "about": "random photos", "params": {
                "album": "scope to an album (collection-aware)", "limit": "1..24",
            }},
            {"path": "/api/trip-weather", "about": "current conditions per trip stop",
             "params": {"trip": "trip key (see an album's `trip`)"}},
        ],
    })


@router.get("/api/stats")
def api_stats(request: Request, lang: str | None = None):
    """Gallery-wide counters — the numbers the welcome screen shows, plus the
    totals that are only interesting to an API client."""
    code = _api_lang(request, lang)
    c = db.conn()
    row = c.execute(
        """SELECT COUNT(*) AS images, COALESCE(SUM(size), 0) AS bytes,
                  COALESCE(SUM(is_showcase), 0) AS featured,
                  MIN(taken_at) AS first, MAX(taken_at) AS last
           FROM images"""
    ).fetchone()
    tags = c.execute("SELECT COUNT(*) AS n FROM tags").fetchone()["n"]
    return context.json_cors({
        "images": row["images"],
        "featured": row["featured"],
        "albums": {
            "top_level": len(albums.child_album_names(None)),
            "total": len(albums.all_album_nodes()),
            "showcase": len(albums.showcase_album_rows()),
        },
        "tags": tags,
        "bytes": row["bytes"],
        "bytes_h": photos.humanize_bytes(row["bytes"]),
        "span": {
            "from": row["first"],
            "to": row["last"],
            "label": i18n.date_span(code, row["first"], row["last"]) or None,
        },
        "lang": code,
    }, vary=API_VARY)


@router.get("/api/albums")
def api_albums(request: Request, parent: str | None = None, sort: str | None = None,
               depth: int = 1, showcase: bool | None = None, limit: int = API_MAX_LIMIT):
    """Album cards: the top level by default, the children of `parent`
    otherwise. `depth > 1` nests each card's own children under `children`,
    so one request can pull a whole branch of the tree."""
    base = context.public_base_url(request)
    root: str | None = None
    if parent:
        root = albums.resolve_album_path(parent)
        if root is None:
            raise HTTPException(404, "album not found")
    has_curated = bool(albums.curated_album_positions())
    allowed = set(photos.SORT_ALBUM_SQL) | ({photos.SORT_CURATED} if has_curated else set())
    default_sort = photos.pick_sort(cfgio.first(config.gallery_config(), "album_sort"), allowed, photos.SORT_ALBUM_DEFAULT)
    current_sort = photos.pick_sort(sort, allowed, default_sort)
    depth = max(1, min(4, depth))
    limit = _api_limit(limit)
    all_albums = albums.distinct_albums()

    def branch(node: str | None, level: int) -> list[dict]:
        cards = [albums.album_card(n, all_albums) for n in albums.child_album_names(node, all_albums)]
        cards = albums.sorted_album_cards(cards, current_sort)
        out = []
        for card in cards:
            item = albums.serialize_album(card, base)
            # ?showcase= splits the ★ rail from the archive grid, like /albums
            if showcase is not None and level == 1 and item["is_showcase"] != bool(showcase):
                continue
            if level < depth and card["sub_count"]:
                item["children"] = branch(card["album"], level + 1)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    listing = branch(root, 1)
    payload = {
        "count": len(listing),
        "parent": root,
        "sort": current_sort,
        "default_sort": default_sort,
        "depth": depth,
        "albums": listing,
    }
    # `#group` markers in gallery.cfg album_order frame the curated top-level
    # view into labeled sections; mirrored here so a client can rebuild it
    if root is None and current_sort == photos.SORT_CURATED:
        sections = albums.curated_album_sections(
            albums.sorted_album_cards(albums.top_level_album_cards(all_albums), photos.SORT_CURATED))
        payload["sections"] = [
            {"label": s["label"], "albums": [albums.serialize_album(c, base) for c in s["cards"]]}
            for s in sections
        ]
    return context.json_cors(payload)


@router.get("/api/album/{album:path}")
def api_album(request: Request, album: str, images: bool = False, sort: str | None = None,
              tag: str | None = None, subtree: bool | None = None, limit: int = API_MAX_LIMIT,
              offset: int = 0, tags: bool = False, lang: str | None = None):
    """One album in full: the same card as /api/albums plus everything its
    page renders — description, stats block, album tags, hero reel, immediate
    sub-albums, the photo tags available inside it and (with `images=1`) the
    photo grid itself, in the album's own sort order.

    The scope is the album's own: with `collection = true` in album.cfg both
    the grid and the counters span the whole subtree, exactly like the page.
    `scope` in the response spells out which it was."""
    resolved = albums.resolve_album_path(album)
    if resolved is None:
        raise HTTPException(404, "album not found")
    album = resolved
    albums.refresh_featured_on_cfg_change(album)
    cfg = config.album_config(album)
    code = _api_lang(request, lang)
    base = context.public_base_url(request)

    where_simple, where_join, scope_params, collection, wide = photos.photo_scope(album, subtree)
    c = db.conn()
    curated_order = photos.curated_photo_order(album, cfg)
    day_count = photos.scope_day_count(where_simple, scope_params)
    current_sort, default_sort, base_sort = photos.resolve_image_sort(
        cfg, sort, curated_order, days=day_count > 1)
    reel_mode, reel_rows = albums.album_reel(album, cfg)
    sub_albums = albums.sorted_album_cards([albums.album_card(n) for n in albums.child_album_names(album)], "name_asc")
    photo_tags = [r["name"] for r in c.execute(
        f"""SELECT DISTINCT t.name FROM tags t
           JOIN image_tags it ON it.tag_id = t.id
           JOIN images i ON i.id = it.image_id
           WHERE {where_join} ORDER BY t.name""",
        scope_params,
    ).fetchall()]
    # stats describe the album, so they are computed over its whole photo set
    # and never over a ?tag= filtered view (same rule as the page)
    stat_src = [dict(r) for r in c.execute(
        f"SELECT size, width, height, taken_at, exif_json FROM images WHERE {where_simple}",
        scope_params,
    ).fetchall()]
    font_css = theme.album_font_css_url(album)
    theme_css = theme.theme_css_url(album)
    effect = (cfgio.first(cfg, "effect") or "").strip().lower()

    payload = {
        "album": albums.serialize_album(albums.album_card(album), base),
        "breadcrumbs": albums.album_breadcrumbs(album),
        "scope": {"album": album, "collection": collection, "subtree": wide},
        "description": {"html": albums.album_description(album, code), "lang": code},
        "stats": albums.album_stats(stat_src, cfg, code),
        "effect": effect if effect in schema.EFFECTS else None,
        "font": {"css": font_css, "scale": theme.album_font_scale(album),
                 "preload": theme.album_font_preload(album)} if font_css else None,
        "theme": {"css": theme_css, "accent": (theme.page_accent(album) or {}).get("acc"),
                  "wallpaper": theme.wallpaper_decls(album) or None} if theme_css else None,
        "trip": trips.trip_for_album(album, code),
        "reel": {"mode": reel_mode, "items": photos.serialize_photos(reel_rows, base)},
        "sub_albums": [albums.serialize_album(s, base) for s in sub_albums],
        "photo_tags": photo_tags,
        "sort": {
            "current": current_sort,
            "default": default_sort,
            "options": photos.image_sort_options_for_template(current_sort, bool(curated_order), code,
                                                        days=day_count > 1),
        },
        "lang": code,
    }
    if images:
        limit = _api_limit(limit)
        rows, total, _scope = photos.photo_rows(
            album=album, subtree=subtree, tag=tag,
            order_sql=photos.SORT_IMAGE_SQL[base_sort],
            limit=None if current_sort == photos.SORT_CURATED else limit,
            offset=0 if current_sort == photos.SORT_CURATED else offset,
        )
        if current_sort == photos.SORT_CURATED:
            # curated order is a cfg list, not SQL — reorder the full set, then page
            rows = photos.apply_curated_order(rows, curated_order)[max(0, offset):max(0, offset) + limit]
        payload["images"] = {
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": max(0, offset),
            "tag": tag,
            "items": photos.serialize_photos(rows, base, with_tags=tags),
        }
    else:
        payload["images"] = {"total": photos.photo_rows(album=album, subtree=subtree, tag=tag, limit=0)[1]}
    return context.json_cors(payload, vary=API_VARY)


@router.get("/api/photos")
def api_photos(request: Request, album: str | None = None, subtree: bool | None = None,
               tag: str | None = None, q: str | None = None, featured: bool = False,
               sort: str | None = None, random: bool = False, tags: bool = False,
               limit: int = 50, offset: int = 0):
    """Photos across the gallery or inside one album, with the filters the UI
    offers (tag, search, featured) and offset paging. An `album` that is a
    collection is scoped to its whole subtree — `scope` in the response says
    so, and `subtree=0` forces the plain folder scope."""
    if album:
        resolved = albums.resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    current_sort = photos.pick_sort(sort, photos.SORT_IMAGE_SQL, photos.SORT_IMAGE_DEFAULT)
    limit = _api_limit(limit)
    rows, total, scope = photos.photo_rows(
        album=album, subtree=subtree, tag=tag, q=(q or "").strip() or None,
        featured=featured, order_sql=photos.SORT_IMAGE_SQL[current_sort],
        random_order=bool(random), limit=limit, offset=offset,
    )
    base = context.public_base_url(request)
    return context.json_cors({
        "count": len(rows),
        "total": total,
        "limit": limit,
        "offset": max(0, offset),
        "sort": "random" if random else current_sort,
        "scope": scope,
        "filters": {"tag": tag, "q": (q or "").strip() or None, "featured": bool(featured)},
        "items": photos.serialize_photos(rows, base, with_tags=tags),
    })


@router.get("/api/photo/{rel_path:path}")
def api_photo(request: Request, rel_path: str, col: str | None = None,
              sort: str | None = None, neighbours: bool = True, lang: str | None = None):
    """One photo with everything its page shows: dimensions, EXIF (formatted
    for the active language and raw), the embedded description, its tags, and
    the prev/next neighbours in the order the grid would walk them.

    Neighbours are scoped to the photo's own folder unless `col=<album>` names
    a collection root above it — then they span that collection, matching what
    the single-image view does when you enter it from a collection album."""
    raw = rel_path.strip("/")
    if "/" not in raw:
        raise HTTPException(404, "image not found")
    album, _, filename = raw.rpartition("/")
    rel = media.safe_rel(album, filename).as_posix()
    c = db.conn()
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    row = dict(row)
    code = _api_lang(request, lang)
    base = context.public_base_url(request)
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    if settings.hide_gps:
        exif.pop("GPSInfo", None)
    photo_tags = photos.tags_for_images([row["id"]]).get(row["id"], [])
    item = photos.serialize_photo(row, base, photo_tags)
    item.update({
        "breadcrumbs": albums.album_breadcrumbs(row["album"]),
        "description": photos.extract_description(exif),
        "exif": [{"key": k, "val": v} for k, v in photos.prettify_exif(exif, code)],
        # already plain JSON types — the column stores it as JSON (scanner.py)
        "exif_raw": exif,
        "album_url": {"page": f"/album/{row['album']}", "api": f"/api/album/{row['album']}"},
        "lang": code,
    })

    col_root = (col or "").strip("/")
    scope_album = row["album"]
    if (col_root and (scope_album == col_root or scope_album.startswith(col_root + "/"))
            and config.album_collection(col_root)):
        scope_album = col_root
    else:
        col_root = ""
    if neighbours:
        cfg = config.album_config(scope_album)
        curated_order = photos.curated_photo_order(scope_album, cfg)
        where_simple, _wj, sp, _coll, _wide = photos.photo_scope(
            scope_album, True if col_root else None)
        current_sort, default_sort, base_sort = photos.resolve_image_sort(
            cfg, sort, curated_order, days=photos.scope_day_count(where_simple, sp) > 1)
        # the neighbour walk has to mirror the grid exactly, so it uses the
        # same scope (collection root or own folder) and the same order
        rows, _total, _scope = photos.photo_rows(
            album=scope_album, subtree=True if col_root else None,
            order_sql=photos.SORT_IMAGE_SQL[base_sort], limit=None,
        )
        rel_list = [r["rel_path"] for r in rows]
        if current_sort == photos.SORT_CURATED:
            pos = {r: i for i, r in enumerate(curated_order)}
            rel_list.sort(key=lambda r: pos.get(r, len(pos)))
        idx = rel_list.index(rel) if rel in rel_list else -1
        item["neighbours"] = {
            "scope": {"album": scope_album, "collection_root": col_root or None,
                      "count": len(rel_list)},
            "sort": current_sort,
            "index": idx,
            "prev": rel_list[idx - 1] if idx > 0 else None,
            "next": rel_list[idx + 1] if 0 <= idx < len(rel_list) - 1 else None,
        }
    return context.json_cors(item, vary=API_VARY)


@router.get("/api/tags")
def api_tags(request: Request, album: str | None = None, subtree: bool | None = None,
             limit: int = API_MAX_LIMIT):
    """Photo tags (the `.tags` sidecar ones that drive the ?tag= filter) with
    how many photos carry each, most-used first. Scoped to an album — again
    collection-aware — when `album` is given. An album's own display tags are
    a different thing and live on /api/album."""
    where, params = "", []
    scope = {"album": None, "collection": False, "subtree": False}
    if album:
        resolved = albums.resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        _simple, where_join, scope_params, collection, wide = photos.photo_scope(resolved, subtree)
        where = f"WHERE {where_join}"
        params = list(scope_params)
        scope = {"album": resolved, "collection": collection, "subtree": wide}
    limit = _api_limit(limit)
    rows = db.conn().execute(
        f"""SELECT t.name AS name, COUNT(*) AS count FROM tags t
           JOIN image_tags it ON it.tag_id = t.id
           JOIN images i ON i.id = it.image_id
           {where}
           GROUP BY t.name ORDER BY count DESC, t.name ASC LIMIT ?""",
        params + [limit],
    ).fetchall()
    return context.json_cors({
        "count": len(rows),
        "scope": scope,
        "items": [{"name": r["name"], "count": r["count"]} for r in rows],
    })


@router.get("/api/showcase")
def api_showcase(request: Request, limit: int = 50, album: str | None = None,
                 random: bool = False, subtree: bool | None = None, tags: bool = False):
    """
    Returns showcased photos as JSON. CORS-enabled for cross-origin embedding.

    Query params:
      limit:   max number of items, 1..200 (default 50)
      album:   optional album filter — a `collection = true` album covers its
               whole subtree, just as its page does
      subtree: 0|1 to force the scope regardless of the album's collection flag
      random:  pass `?random=1` to randomise order; default is newest-first
      tags:    pass `?tags=1` to include each photo's tags
    """
    if album:
        resolved = albums.resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    limit = _api_limit(limit)
    rows, total, scope = photos.photo_rows(
        album=album, subtree=subtree, featured=True,
        random_order=bool(random), limit=limit,
    )
    base = context.public_base_url(request)
    items = photos.serialize_photos(rows, base, with_tags=tags)
    return context.json_cors(
        {
            "count": len(items),
            "total": total,
            "scope": scope,
            "items": items,
        }
    )


@router.get("/api/shuffle")
def api_shuffle(request: Request, limit: int = 8, album: str | None = None,
                subtree: bool | None = None):
    """Random photos. Returns a bare array — the welcome hero's ⟳ TUNE button
    reads it directly (see app.js), so the shape stays as it is."""
    limit = _api_limit(limit, 24)
    if album:
        resolved = albums.resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    rows, _total, _scope = photos.photo_rows(album=album, subtree=subtree,
                                       random_order=True, limit=limit)
    base = context.public_base_url(request)
    # no-store: a cached "random" is not random (the ⟳ TUNE button re-asks)
    return context.json_cors(photos.serialize_photos(rows, base), max_age=0)


@router.options("/api/{rest:path}")
def api_options(rest: str):
    # CORS pre-flight (most simple GETs don't trigger this, but be polite)
    return context.json_cors({})
