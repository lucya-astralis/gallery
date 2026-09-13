"""The HTML pages: welcome, albums, one album, one photo, search, stats."""

from __future__ import annotations

import json
from collections import Counter
from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import (
    albums, brand, branding, cfgio, config, db, i18n, photos, schema, search, stats, theme,
    trips, welcome,
)
from ..runtime import settings
from . import context, media

router = APIRouter()


@router.get("/lang/{code}")
def set_lang(code: str, next: str = "/"):
    """Language switcher target (nav selector links here). Sets the `lang`
    cookie and bounces back to `next`. Only same-site relative paths are
    accepted as redirect targets — anything else falls back to the welcome
    page, so this can't be abused as an open redirect."""
    code = code.strip().lower()
    if code not in i18n.LANGS:
        raise HTTPException(404, "unknown language")
    if not next.startswith("/") or next.startswith("//") or "\\" in next:
        next = "/"
    resp = RedirectResponse(next, status_code=303)
    # deliberately NOT httponly: app.js reads the cookie on bfcache restores
    # (pageshow) to detect a stale-language page and reload it — Safari keeps
    # pages in the back/forward cache even with Cache-Control: no-store.
    resp.set_cookie("lang", code, max_age=365 * 24 * 3600, path="/",
                    samesite="lax")
    return resp


@router.get("/", response_class=HTMLResponse)
def welcome_page(request: Request):
    c = db.conn()
    feed, feed_label, feed_mode = welcome.welcome_feed(mobile=context.is_mobile_request(request))
    # what the gallery holds, without its unlisted albums
    listed, listed_params = albums.unlisted_clause()
    counts = c.execute(f"SELECT COUNT(*) AS images FROM images WHERE {listed}",
                       listed_params).fetchone()
    # "Albums" = top-level folders (parents of nested albums count once).
    top_level_albums = len(albums.child_album_names(None))
    showcase_count = c.execute(
        f"SELECT COUNT(*) AS n FROM images WHERE is_showcase = 1 AND {listed}",
        listed_params,
    ).fetchone()
    showcase_albums = albums.showcase_album_rows(limit=6)
    return context.templates.TemplateResponse(
        request, "welcome.html",
        {
            "shuffle": feed,
            "feed_label": feed_label,
            "feed_mode": feed_mode,
            "image_count": counts["images"] if counts else 0,
            "album_count": top_level_albums,
            "showcase_count": showcase_count["n"] if showcase_count else 0,
            "showcase_albums": showcase_albums,
            "updated_at": welcome.archive_updated_at(),
        },
        # the hero feed can differ per device class (welcome_mobile/_desktop),
        # so shared caches must key on the UA
        headers={"Vary": "User-Agent"},
    )


@router.get("/albums", response_class=HTMLResponse)
def albums_index(request: Request, sort: str | None = None):
    # "Curated" only exists as a sort option while gallery.cfg defines an
    # album_order; gallery.cfg `album_sort` presets the default sort.
    has_curated = bool(albums.curated_album_positions())
    allowed = set(photos.SORT_ALBUM_SQL) | ({photos.SORT_CURATED} if has_curated else set())
    default_sort = photos.pick_sort(cfgio.first(config.gallery_config(), "album_sort"), allowed, photos.SORT_ALBUM_DEFAULT)
    current_sort = photos.pick_sort(sort, allowed, default_sort)
    cards = albums.sorted_album_cards(albums.top_level_album_cards(), current_sort)
    # annotate here so the template only has to read the flag
    for a in cards:
        a["is_showcase"] = albums.album_is_showcase(a["album"])
    showcase_albums = [a for a in cards if a["is_showcase"]]
    archive_albums = [a for a in cards if not a["is_showcase"]]
    # `#group` markers in album_order frame the Curated view into labeled
    # sections; every other sort keeps the flat archive grid
    album_sections = (albums.curated_album_sections(archive_albums)
                      if current_sort == photos.SORT_CURATED else [])
    sort_options = photos.album_sort_options_for_template(current_sort, curated=has_curated,
                                                    lang=context.request_lang(request))
    return context.templates.TemplateResponse(
        request, "index.html",
        {
            "albums": cards,
            "showcase_albums": showcase_albums,
            "archive_albums": archive_albums,
            "album_sections": album_sections,
            "current_sort": current_sort,
            "default_sort": default_sort,
            "sort_options": sort_options,
            "sort_label": photos.active_sort_label(sort_options),
        },
    )


# how many tags the /stats chart shows before the rest folds
TAGS_SHOWN = 12


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    """Public statistics: what the archive holds, charted.

    Everything drawn comes out of app.stats.collect() — one pass over the
    images table — plus the album/tag facts the rest of this module already
    knows how to resolve. Nothing about the visitor is read or written, which
    is what makes the page shareable; the lead paragraph says so.

    The charts are server-rendered SVG geometry (see _charts.html), so this
    route hands the template finished rows and the page needs no script.
    """
    lang = context.request_lang(request)
    c = db.conn()
    data = stats.collect(
        c,
        month_name=partial(i18n.month_short, lang),
        weekday_name=partial(i18n.weekday_index, lang),
        # unlisted albums are not part of what the archive shows it holds
        where=albums.unlisted_clause(),
    )

    # Album bars are rolled up to TOP-LEVEL albums, counting each one's whole
    # folder tree — the same total the album cards and /albums already show,
    # and the way a reader means the question. Charting the leaf folders
    # instead put "sapporo" and "USJ" on the axis with nothing saying which
    # trip they belong to. Each bar carries the album's display name
    # (album.cfg `name =`, else the folder) and links into it, so the chart
    # doubles as navigation the way the trip timeline does.
    by_top: Counter = Counter()
    for path, n in data["albums_raw"].items():
        by_top[path.split("/", 1)[0]] += n
    album_chart = stats.album_rows(
        by_top,
        label_of=config.album_display_name,
    )
    shapes = stats.stack(stats.shape_rows(
        data["shapes_raw"],
        label_of=lambda k: i18n.t(lang, f"stats.shape.{k}"),
    ))

    listed_i, listed_params = albums.unlisted_clause("i.album")
    tag_rows = c.execute(
        f"""SELECT t.name AS name, COUNT(*) AS n
             FROM image_tags it JOIN tags t ON t.id = it.tag_id
             JOIN images i ON i.id = it.image_id
            WHERE {listed_i}
            GROUP BY t.id ORDER BY n DESC, t.name""",
        listed_params,
    ).fetchall()
    # one lonely tag is a fact, not a distribution — the chart only earns its
    # card once there is something to compare. Past a dozen the rest folds.
    tags = (stats.fold(stats.rows([(r["name"], r["n"]) for r in tag_rows]), TAGS_SHOWN)
            if len(tag_rows) >= 3 else [])
    tag_total = len(tag_rows)

    no_exif = data["total"] - sum(r["value"] for r in data["cameras"])
    cover = photos.showcase_rows(limit=1, random_order=True)

    return context.templates.TemplateResponse(
        request, "stats.html",
        {
            "figs": {
                "photos": data["total"],
                "albums": len(albums.child_album_names(None)),
                "folders": len([n for n in albums.all_album_nodes() if not albums.is_unlisted(n)]),
                "featured": data["featured"],
                "tags": tag_total,
                "days": data["active_days"],
                "data": photos.humanize_bytes(data["bytes"]),
                "pixels": photos.humanize_pixels(data["pixels"]),
            },
            "span_label": i18n.date_span(lang, data["first"], data["last"]),
            "busiest_label": (
                f"{i18n.fmt_date(lang, data['busiest_day'])} · {data['busiest_n']}"
                if data["busiest_day"] else None
            ),
            "months": data["months"],
            "months_window": stats.MONTHS_WINDOW,
            "weekdays": data["weekdays"],
            "hour_dial": stats.dial(data["hours"]),
            "cameras": data["cameras"],
            "focals": data["focals"],
            "apertures": data["apertures"],
            "isos": data["isos"],
            "album_chart": album_chart,
            "shapes": shapes,
            "tags": tags,
            "no_exif": no_exif if no_exif > 0 else 0,
            "og_cover": cover[0]["rel_path"] if cover else None,
        },
    )


@router.get("/album/{album:path}", response_class=HTMLResponse)
def album_view(request: Request, album: str, tag: str | None = None, sort: str | None = None):
    album = album.strip("/")
    if not album:
        raise HTTPException(404, "album not found")
    # a just-saved album.cfg must be visible on this very reload (reel mode,
    # featured set, grid stars) without waiting for the watcher's debounce
    albums.refresh_featured_on_cfg_change(album)
    album_cfg = config.album_config(album)
    c = db.conn()
    # Collection mode (album.cfg `collection = true`): the grid shows every
    # photo in this album's whole subtree (its own + all sub-folders) as one
    # flat set, instead of only the photos sitting directly in this folder.
    # /api/album + /api/photos resolve the same scope through photos.photo_scope.
    where_simple, where_join, scope_params, collection, _wide = photos.photo_scope(album)
    # album.cfg `order` adds a "Curated" sort option, photos spanning more
    # than one day add "By day"; `sort` presets the default sort for this
    # album (query param still wins).
    curated_order = photos.curated_photo_order(album, album_cfg)
    day_count = photos.scope_day_count(where_simple, scope_params)
    current_sort, default_sort, base_sort = photos.resolve_image_sort(
        album_cfg, sort, curated_order, days=day_count > 1)
    # qualify column names so the JOIN query below isn't ambiguous
    qualified_sql = photos.qualify_sort(photos.SORT_IMAGE_SQL[base_sort])
    if tag:
        rows = c.execute(
            f"""SELECT i.* FROM images i
               JOIN image_tags it ON it.image_id = i.id
               JOIN tags t ON t.id = it.tag_id
               WHERE {where_join} AND t.name = ?
               ORDER BY {qualified_sql}""",
            (*scope_params, tag),
        ).fetchall()
    else:
        order_sql = photos.SORT_IMAGE_SQL[base_sort]
        rows = c.execute(
            f"SELECT * FROM images WHERE {where_simple} ORDER BY {order_sql}",
            scope_params,
        ).fetchall()
    images = [dict(r) for r in rows]
    if current_sort == photos.SORT_CURATED:
        images = photos.apply_curated_order(images, curated_order)
    # Immediate sub-folders of this album, shown as folder cards above the
    # image grid. Listed alphabetically so the folder view is predictable.
    sub_albums = albums.sorted_album_cards(
        [albums.album_card(n) for n in albums.child_album_names(album)], "name_asc"
    )
    if not rows and not sub_albums:
        # nothing directly here and no sub-folders: only a 404 if the album
        # truly has no photos anywhere (a tag filter may have hidden them).
        exists = c.execute(
            f"SELECT 1 FROM images WHERE {where_simple} LIMIT 1", scope_params
        ).fetchone()
        if not exists:
            raise HTTPException(404, "album not found")
    tag_rows = c.execute(
        f"""SELECT DISTINCT t.name FROM tags t
           JOIN image_tags it ON it.tag_id = t.id
           JOIN images i ON i.id = it.image_id
           WHERE {where_join} ORDER BY t.name""",
        scope_params,
    ).fetchall()
    # Showcase status comes from album.cfg (`showcase = …`).
    album_is_showcase = albums.album_is_showcase(album)
    # Hero reel (album.cfg `reel`, like the welcome feed) — see albums.album_reel,
    # which /api/album serves from as well.
    reel_mode, featured = albums.album_reel(album, album_cfg)
    lang = context.request_lang(request)
    sort_options = photos.image_sort_options_for_template(current_sort, curated=bool(curated_order),
                                                    lang=lang, days=day_count > 1)
    trip = trips.trip_for_album(album, lang)
    # "By day": the grid renders as one framed section per capture day
    # instead of a single flat grid (album.html falls back to the flat grid
    # whenever this is None).
    day_sections = (photos.day_sections(images, trip or trips.ancestor_trip(album, lang), lang)
                    if current_sort == photos.SORT_DAYS else None)
    effect = (cfgio.first(album_cfg, "effect") or "").strip().lower()
    # Stats block under the description. Computed over the album's WHOLE photo
    # set, never the ?tag=-filtered grid — the readouts describe the album, so
    # a tag filter mustn't skew SPAN/DEVICE/DATA. Reuse `images` when it already
    # is the full set (no tag), else fetch the album scope just for the stats.
    if tag:
        stat_src = [dict(r) for r in c.execute(
            f"SELECT size, width, height, taken_at, exif_json FROM images WHERE {where_simple}",
            scope_params,
        ).fetchall()]
    else:
        stat_src = images
    album_stats = albums.album_stats(stat_src, album_cfg, lang)
    return context.templates.TemplateResponse(
        request, "album.html",
        {
            "album": album,
            # album.cfg `unlisted`: the page answers, but asks not to be indexed
            "unlisted": albums.is_unlisted(album),
            # base.html paints the backdrop from this; a sub-album with no
            # wallpaper of its own inherits its nearest ancestor's
            "bg_album": album,
            "breadcrumbs": albums.album_breadcrumbs(album),
            "album_description": albums.album_description(album, lang),
            # cover photo for the mobile hero header (see .album-hero)
            "album_cover": albums.album_cover_rel(album),
            # the album's own mark (album.cfg `icon = ...`), shown next to
            # the hero title; None when it configures none
            "album_icon": theme.album_icon_url(album),
            # ambient page effect (album.cfg `effect = ...`, whitelisted)
            "album_effect": effect if effect in schema.EFFECTS else None,
            # album.cfg `tags = ...`, shown under the hero title. NOT the
            # per-image `tags` below, which drive the ?tag= grid filter.
            "album_tags": config.album_tags(album),
            # stats block under the description (auto EXIF/size readouts +
            # editorial `loc`/`stat` from album.cfg); see albums.album_stats
            "album_stats": album_stats,
            # generated stylesheet for the album's own title face
            # (album.cfg `font = ...`); None when it configures none
            "album_font_css": theme.album_font_css_url(album),
            # preload for that same face, so it downloads alongside the sheet
            # instead of after it (no fallback→face swap on load)
            "album_font_preload": theme.album_font_preload(album),
            "trip": trip,
            "collection": collection,
            "sub_albums": sub_albums,
            "album_is_showcase": album_is_showcase,
            "featured": featured,
            "reel_mode": reel_mode,
            "images": images,
            # None unless the "By day" sort is active — see photos.day_sections
            "day_sections": day_sections,
            "tags": [r["name"] for r in tag_rows],
            "active_tag": tag,
            "current_sort": current_sort,
            "default_sort": default_sort,
            "sort_options": sort_options,
            "sort_label": photos.active_sort_label(sort_options),
        },
    )


@router.get("/image/{album:path}/{filename}", response_class=HTMLResponse)
def image_view(request: Request, album: str, filename: str, sort: str | None = None, col: str | None = None):
    rel = media.safe_rel(album, filename).as_posix()
    c = db.conn()
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    if settings.hide_gps:
        exif.pop("GPSInfo", None)
    tags = [
        r["name"]
        for r in c.execute(
            """SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id = t.id
               WHERE it.image_id = ? ORDER BY t.name""",
            (row["id"],),
        ).fetchall()
    ]
    # Prev/next neighbours. Normally scoped to the image's own folder, but
    # when opened from a collection album (`?col=<root>`) the scroll spans
    # that collection's whole subtree, so you page through every collected
    # photo instead of getting stuck inside one sub-folder. The query mirrors
    # the album grid's collection query exactly, so the order lines up.
    col_root = (col or "").strip("/")
    if (
        col_root
        and (album == col_root or album.startswith(col_root + "/"))
        and config.album_collection(col_root)
    ):
        prefix = col_root + "/"
        # the walk skips unlisted albums inside the collection, unless this
        # photo lives in one (albums.unlisted_clause)
        listed, listed_params = albums.unlisted_clause(keep=album)
        where_scope = f"(album = ? OR substr(album, 1, ?) = ?) AND {listed}"
        scope_params: tuple = (col_root, len(prefix), prefix, *listed_params)
    else:
        col_root = ""  # absent / forged / no longer a collection: folder scope
        where_scope = "album = ?"
        scope_params = (album,)
    # Sort must resolve exactly like on the album grid the visitor came from
    # (same cfg scope: collection root or the image's own folder), so links
    # without an explicit ?sort= still walk the grid in the grid's order —
    # including a cfg-preset default and the curated order.
    scope_cfg = config.album_config(col_root or album)
    curated_order = photos.curated_photo_order(col_root or album, scope_cfg)
    # "By day" is chronological SQL plus grouping, so the walk resolves it
    # like date_asc and pages through the grid's day sections in order
    current_sort, default_sort, base_sort = photos.resolve_image_sort(
        scope_cfg, sort, curated_order,
        days=photos.scope_day_count(where_scope, scope_params) > 1)
    order_sql = photos.SORT_IMAGE_SQL[base_sort]
    neighbours = c.execute(
        f"SELECT rel_path FROM images WHERE {where_scope} ORDER BY {order_sql}",
        scope_params,
    ).fetchall()
    rel_list = [r["rel_path"] for r in neighbours]
    if current_sort == photos.SORT_CURATED:
        pos = {r: i for i, r in enumerate(curated_order)}
        rel_list.sort(key=lambda r: pos.get(r, len(pos)))
    idx = rel_list.index(rel) if rel in rel_list else -1
    prev_rel = rel_list[idx - 1] if idx > 0 else None
    next_rel = rel_list[idx + 1] if 0 <= idx < len(rel_list) - 1 else None
    # rows carry the search each value links to (EXIF panel, image.html)
    pretty_exif = photos.exif_rows(exif, context.request_lang(request))
    description = photos.extract_description(exif)
    return context.templates.TemplateResponse(
        request, "image.html",
        {
            "image": dict(row),
            "unlisted": albums.is_unlisted(row["album"]),
            "bg_album": row["album"],
            "breadcrumbs": albums.album_breadcrumbs(row["album"]),
            "exif": pretty_exif,
            "exif_raw": exif,
            "tags": tags,
            "prev_rel": prev_rel,
            "next_rel": next_rel,
            "description": description,
            "album_rels": rel_list,
            "collection_root": col_root or None,
            "current_index": idx,
            "current_sort": current_sort,
            "default_sort": default_sort,
        },
    )


@router.get("/humans.txt", response_class=Response)
def humans_txt(request: Request):
    """The colophon, at the address the convention reserves for one.

    Everything the site says about itself in the chrome is the operator's
    (gallery.cfg) and everything it says about the software is fixed
    (aperture/brand.py); this is the one page that states both side by side, in
    plain text, with no styling to strip. It is also the only vendor mark a
    person can be pointed at rather than having to view source for — which
    is the whole reason /humans.txt exists as a convention.

    The archive half is skipped where the cfg names nobody: a colophon that
    invents an operator would be worse than a short one."""
    brand_ctx = branding.site_brand(context.request_lang(request))
    lines = [
        "/* SOFTWARE */",
        f"    Name      {brand.PRODUCT}",
        f"    Version   {brand.VERSION}",
        f"    Vendor    {brand.NAME}",
        f"    Site      {brand.URL}",
        "",
        "/* ARCHIVE */",
        f"    Name      {brand_ctx['title']}",
    ]
    if brand_ctx["operator"] and brand_ctx["operator"] != brand_ctx["title"]:
        lines.append(f"    Operator  {brand_ctx['operator']}")
    if brand_ctx["operator_url"]:
        lines.append(f"    Site      {brand_ctx['operator_url']}")
    lines.append(f"    Address   {context.public_base_url(request)}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


# ----- search ------------------------------------------------------------
# One field over what a visitor might remember: what the album is CALLED,
# what the file is called, what it was tagged, and what it was shot with —
# plus filters (iso:, f:, mm:, date:, …) in the grammar search.py defines.
# Results come back as albums first (the thing you were probably after) and
# photos below.
#
# Both halves are capped. A query is free-form and a short one matches most of
# the library, which as one un-paginated page meant hundreds of KB of markup
# and a wall of thumbnails to scroll past.
SEARCH_PHOTO_LIMIT = 300
SEARCH_ALBUM_LIMIT = 24


def _matches(needle: str, haystack: str | None) -> bool:
    """Case-insensitive substring test for the parts of the search that run in
    Python rather than in SQL. `casefold()` is Unicode-aware, so an album
    called "Lüneburg" is found by typing "lüneburg" or "LÜNEBURG" — sqlite's
    own `lower()` only folds ASCII, which is all the photo-row half can do."""
    return bool(haystack) and needle.casefold() in haystack.casefold()


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", sort: str | None = None):
    q = q.strip()
    c = db.conn()
    if not q:
        return RedirectResponse("/albums")
    lang = context.request_lang(request)
    current_sort = photos.pick_sort(sort, photos.SORT_IMAGE_SQL, photos.SORT_IMAGE_DEFAULT)
    qualified_sql = photos.qualify_sort(photos.SORT_IMAGE_SQL[current_sort])

    # Albums the query names. The `album` column is the FOLDER path, so on its
    # own it could not find "Japan 2026" — the name the album is called
    # everywhere on the site — for a folder called japan_2026. Matching the
    # display name (album.cfg `name = …`) and the album's own tags here is what
    # makes the search hint ("searches album names") true. The tree is a few
    # dozen entries and every cfg is cached, so this is a dict lookup, not IO.
    # Only the query's words name albums; a filter is about photos.
    query = search.parse(q)
    text = query.text
    matched_albums = [
        a for a in albums.albums_with_ancestors()
        if text and not albums.is_unlisted(a)
        and (_matches(text, a) or _matches(text, config.album_display_name(a))
                     or any(_matches(text, tag) for tag in config.album_tags(a)))
    ]
    # A matched album stands for everything under it: naming a collection has
    # to bring back its sub-albums' photos too, or "Japan 2026" would find the
    # album and none of its 436 pictures.
    scope_albums = sorted({
        node for a in matched_albums
        for node in albums.albums_with_ancestors()
        if node == a or node.startswith(a + "/")
    })

    # The words match album, file, tag, camera and lens; an album found by its
    # display name adds everything inside it. Filters narrow what that found.
    also = (("i.album IN (%s)" % ",".join("?" * len(scope_albums)), scope_albums)
            if scope_albums else None)
    where, params = search.condition(query, "i", also)
    listed, listed_params = albums.unlisted_clause("i.album")
    rows = c.execute(
        f"""SELECT i.* FROM images i
           WHERE ({where}) AND {listed}
           ORDER BY {qualified_sql}
           LIMIT ?""",
        (*params, *listed_params, SEARCH_PHOTO_LIMIT + 1),
    ).fetchall()
    # One row over the limit is fetched purely to tell "exactly this many" from
    # "this many and more" — a broad query used to render every matching row,
    # and a one-letter search meant a 340 KB page of 600 thumbnails.
    capped = len(rows) > SEARCH_PHOTO_LIMIT
    images = [dict(r) for r in rows[:SEARCH_PHOTO_LIMIT]]

    album_cards = albums.sorted_album_cards(
        [albums.album_card(a) for a in matched_albums[:SEARCH_ALBUM_LIMIT]], "name_asc")
    sort_options = photos.image_sort_options_for_template(current_sort, lang=lang)
    return context.templates.TemplateResponse(
        request, "search.html",
        {
            "query": q,
            # one chip per filter; each links to the same search without it
            "filters": [{"label": f.label, "ok": f.ok, "rest": query.without(f)}
                        for f in query.filters],
            "albums": album_cards,
            "images": images,
            "capped": capped,
            "limit": SEARCH_PHOTO_LIMIT,
            "current_sort": current_sort,
            "default_sort": photos.SORT_IMAGE_DEFAULT,
            "sort_options": sort_options,
            "sort_label": photos.active_sort_label(sort_options),
        },
    )
