import colorsys
import json
import logging
import os
import re
import threading
import time
import urllib.request
from collections import Counter
from datetime import date, datetime
from functools import partial
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import control, db, i18n, scanner, watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("main")

PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "./photos")).resolve()
THUMBS_DIR = Path(os.environ.get("THUMBS_DIR", "./thumbnails")).resolve()
PREVIEWS_DIR = Path(os.environ.get("PREVIEWS_DIR", "./previews")).resolve()
FULLS_DIR = Path(os.environ.get("FULLS_DIR", str(PREVIEWS_DIR / "_full"))).resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "480"))
PREVIEW_SIZE = int(os.environ.get("PREVIEW_SIZE", "1600"))
# Default 300s (5 min): the file watcher does not get events over SMB/CIFS/NFS
# shares, so a periodic full scan is what actually picks up newly added albums
# and sub-folders there. Set SCAN_INTERVAL=0 to disable.
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
ENABLE_WATCHER = os.environ.get("ENABLE_WATCHER", "1") not in ("0", "false", "False", "")
HIDE_GPS = os.environ.get("HIDE_GPS", "1") not in ("0", "false", "False", "")
STRIP_GPS = os.environ.get("STRIP_GPS", "1") not in ("0", "false", "False", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

_scan_lock = threading.Lock()
_STARTED_AT = time.time()

try:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
FULLS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Flag-file channel the CLI talks to this process through (app/control.py).
control.configure(DATA_DIR)

app = FastAPI(title="lucya.systems gallery", docs_url=None, redoc_url=None, openapi_url=None)

BASE_DIR = Path(__file__).parent


# ----- language (EN / DE / JP) -------------------------------------------
# The site is served in three languages. The `lang` cookie (set via the
# nav selector -> /lang/{code}) wins; first-time visitors fall back to
# their Accept-Language header, then English. Album descriptions live in
# per-language markdown files (album_en.md / album_de.md / album_jp.md,
# see _album_description); UI strings come from i18n.py.
def _request_lang(request: Request) -> str:
    cookie = (request.cookies.get("lang") or "").strip().lower()
    if cookie in i18n.LANGS:
        return cookie
    accept = request.headers.get("accept-language", "").lower()
    for part in accept.split(","):
        code = part.split(";", 1)[0].strip()[:2]
        if code == "de":
            return "de"
        if code == "ja":
            return "jp"
        if code == "en":
            return "en"
    return i18n.DEFAULT_LANG


def _i18n_context(request: Request) -> dict:
    """Per-request template context: `t('key')` translates into the active
    language, `lang`/`html_lang` drive the selector and <html lang=…>, and
    the localized month_label overrides the app-wide default for the
    album-card date chips."""
    lang = _request_lang(request)
    return {
        "lang": lang,
        "html_lang": i18n.HTML_LANG[lang],
        "langs": [
            {"code": code, "label": i18n.LANG_LABELS[code], "active": code == lang}
            for code in i18n.LANGS
        ],
        "t": partial(i18n.t, lang),
        "month_label": partial(i18n.month_label, lang),
        "date_label": partial(i18n.date_label, lang),
    }


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"),
                            context_processors=[_i18n_context])
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _static_url(path: str) -> str:
    """Cache-busting URL for a file under /static: appends the file's mtime as
    `?v=` so a browser re-fetches the asset the moment it actually changes, but
    keeps serving from cache otherwise. Without this, edits to style.css / app.js
    can sit behind a stale browser cache. Falls back to the bare path if the file
    is missing."""
    try:
        version = int((BASE_DIR / "static" / path).stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = _static_url

# The gallery's own release version, shown in the nav and the footer.
# Distinct from API_VERSION, which versions the JSON API contract.
APP_VERSION = "5.1"
templates.env.globals["app_version"] = APP_VERSION


def _public_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


templates.env.globals["public_base_url"] = _public_base_url
# month_label is provided per request by _i18n_context (localized)


def _showcase_rows(album: str | None = None, limit: int = 50, random_order: bool = False,
                   subtree: bool | None = None):
    """Featured photos, optionally filtered to one album. `subtree=True`
    widens the filter to the album's whole folder tree, so photos featured
    inside sub-albums surface on the parent album's page too; `None` lets the
    album decide (a `collection = true` album is its whole subtree, see
    _photo_scope). substr() (not LIKE) keeps `_`/`%` in album names from
    acting as wildcards."""
    c = db.conn()
    if album is not None:
        where_simple, _join, params, _coll, _wide = _photo_scope(album, subtree)
        where = f"WHERE is_showcase = 1 AND {where_simple}"
    else:
        where = "WHERE is_showcase = 1"
        params = ()
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


def _showcase_album_rows(limit: int | None = None):
    """Top-level showcase albums for the ★ FEATURED rails (welcome screen
    and /albums). Newest-active first — unless gallery.cfg defines a curated
    `album_order`, which then fixes the rail order too. Whether an album is
    a showcase is decided by `_album_is_showcase` (album.cfg
    `showcase = …`). Same card shape as `albums_index`
    (album, name, count, latest, cover, sub_count) so the showcase-album
    partial can be reused on both pages."""
    cards = [c for c in _top_level_album_cards() if _album_is_showcase(c["album"])]
    order = "curated" if _curated_album_positions() else "latest_desc"
    cards = _sorted_album_cards(cards, order)
    return cards[:limit] if limit is not None else cards


def _serialize_photo(row: dict, base: str, tags: list[str] | None = None) -> dict:
    """One photo as the API returns it. The `_abs` URLs are what an external
    embedder needs (they carry PUBLIC_BASE_URL, see _public_base_url); the
    relative ones are handier same-origin. `tags` is only attached when the
    caller actually loaded them (see _tags_for_images) — absent means "not
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
        "urls": {
            "thumb": f"/thumb/{rel}",
            "preview": f"/preview/{rel}",
            "full": f"/full/{rel}",
            "page": f"/image/{rel}",
            "api": f"/api/photo/{rel}",
            "thumb_abs": f"{base}/thumb/{rel}",
            "preview_abs": f"{base}/preview/{rel}",
            "full_abs": f"{base}/full/{rel}",
            "page_abs": f"{base}/image/{rel}",
            "api_abs": f"{base}/api/photo/{rel}",
        },
    }
    if tags is not None:
        item["tags"] = tags
    return item


def _tags_for_images(ids: list[int]) -> dict[int, list[str]]:
    """{image_id: [tag, ...]} for a batch of photos — one query instead of one
    per row. The id list travels as a JSON array (like _recompute_featured) so
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


def _json_cors(payload, max_age: int = 300, vary: str | None = None) -> JSONResponse:
    resp = JSONResponse(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Cache-Control"] = f"public, max-age={max_age}" if max_age > 0 else "no-store"
    if vary:
        # language-dependent payloads (descriptions, stats, EXIF labels) must
        # not be served to another language out of a shared cache
        resp.headers["Vary"] = vary
    return resp


# ----- album tree -------------------------------------------------------
# Albums are directories and nest arbitrarily (e.g. "japan/tokyo"). The
# `images.album` column stores each photo's full parent-directory path, so
# the album *tree* is derived from those strings — intermediate folders that
# hold only sub-folders (no direct photos of their own) are still found.
def _distinct_albums() -> list[str]:
    c = db.conn()
    return [r["album"] for r in c.execute("SELECT DISTINCT album FROM images").fetchall()]


def _albums_with_ancestors() -> list[str]:
    """Every folder that can carry an album.cfg: the albums holding photos
    plus all their parents. A parent album whose photos all live in
    sub-folders has no rows in `images`, so walking _distinct_albums() alone
    would skip its cfg entirely (its `featured = sub/pic.jpg` never applied,
    see _recompute_featured)."""
    out: set[str] = set()
    for album in _distinct_albums():
        parts = album.split("/")
        for i in range(1, len(parts) + 1):
            out.add("/".join(parts[:i]))
    return sorted(out)


def _child_album_names(parent: str | None, all_albums: list[str] | None = None) -> list[str]:
    """Immediate sub-folder album-paths directly under `parent`
    (top-level albums when `parent` is None)."""
    albums = all_albums if all_albums is not None else _distinct_albums()
    prefix = (parent + "/") if parent else ""
    plen = len(prefix)
    out: list[str] = []
    seen: set[str] = set()
    for a in albums:
        if prefix:
            if not a.startswith(prefix):
                continue
            rest = a[plen:]
        else:
            rest = a
        if not rest:
            continue
        full = prefix + rest.split("/", 1)[0]
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _album_cover_rel(album: str) -> str | None:
    """Cover for an album node: the album.cfg-pinned cover wins, otherwise
    the newest photo from anywhere in the subtree. `substr(...)` (not LIKE)
    so album names containing `_`/`%` don't act as wildcards."""
    cover_rel = _config_cover_rel(album, _cfg_first(_album_config(album), "cover"))
    if cover_rel:
        return cover_rel
    prefix = album + "/"
    row = db.conn().execute(
        "SELECT rel_path FROM images WHERE (album = ? OR substr(album, 1, ?) = ?) "
        "ORDER BY taken_at IS NULL, taken_at DESC, mtime DESC LIMIT 1",
        (album, len(prefix), prefix),
    ).fetchone()
    return row["rel_path"] if row else None


def _album_card(album: str, all_albums: list[str] | None = None) -> dict:
    """Display info for one album node: recursive photo count, latest
    activity, a cover image from anywhere in its subtree, and how many
    immediate sub-albums it has. `substr(...)` (not LIKE) is used for the
    subtree prefix so album names containing `_`/`%` don't act as wildcards."""
    c = db.conn()
    prefix = album + "/"
    cond = "(album = ? OR substr(album, 1, ?) = ?)"
    params = (album, len(prefix), prefix)
    agg = c.execute(
        f"SELECT COUNT(*) AS count, MAX(taken_at) AS latest FROM images WHERE {cond}",
        params,
    ).fetchone()
    cover_rel = _album_cover_rel(album)
    return {
        "album": album,
        # album.cfg `name = …` when set, else the folder name (_album_display_name)
        "name": _album_display_name(album),
        "count": agg["count"] if agg else 0,
        "latest": agg["latest"] if agg else None,
        "cover": cover_rel,
        # album.cfg `icon = …`, the album's own mark (None when it sets none)
        "icon": _album_icon_url(album),
        "sub_count": len(_child_album_names(album, all_albums)),
    }


def _top_level_album_cards(all_albums: list[str] | None = None) -> list[dict]:
    """One card per top-level album (unsorted)."""
    all_albums = all_albums if all_albums is not None else _distinct_albums()
    return [_album_card(n, all_albums) for n in _child_album_names(None, all_albums)]


def _album_order_key(path: str) -> str:
    """Normalize an album path for matching against gallery.cfg
    `album_order` entries: lower-cased, so an entry works regardless of the
    exact casing on disk."""
    return path.replace("\\", "/").strip().strip("/").lower()


def _curated_album_positions() -> dict[str, int]:
    """gallery.cfg `album_order` as {normalized album path: position}.
    `#group` frame markers don't take part in the ordering and are skipped.
    Empty dict when no curated album order is configured."""
    pos: dict[str, int] = {}
    for item in _gallery_config().get("album_order", []):
        if item.startswith("#"):
            continue
        key = _album_order_key(item)
        if key and key not in pos:
            pos[key] = len(pos)
    return pos


def _sorted_album_cards(cards: list[dict], sort_key: str) -> list[dict]:
    """Order album cards by one of the SORT_ALBUM keys or "curated"
    (gallery.cfg `album_order`). A leading stable name-ascending pass
    provides the tie-break for every other key.

    "By name" sorts on the name the reader SEES (album.cfg `name = …` via
    _album_card), not the folder path — a grid of pretty names ordered by
    hidden folder names just looks broken. The folder path stays the
    tie-break so two albums sharing a display name keep a stable order."""
    def shown(a: dict) -> tuple[str, str]:
        return ((a.get("name") or a["album"]).lower(), a["album"].lower())
    cards = sorted(cards, key=shown)
    if sort_key == "curated":
        # listed albums first, in their configured order; everything not
        # listed follows newest-first (stable sorts keep both groups tidy)
        pos = _curated_album_positions()
        cards.sort(key=lambda a: a["latest"] or "", reverse=True)
        cards.sort(key=lambda a: pos.get(_album_order_key(a["album"]), len(pos)))
    elif sort_key == "name_desc":
        cards.sort(key=shown, reverse=True)
    elif sort_key == "count_desc":
        cards.sort(key=lambda a: a["count"], reverse=True)
    elif sort_key == "count_asc":
        cards.sort(key=lambda a: a["count"])
    elif sort_key == "latest_asc":
        cards.sort(key=lambda a: (a["latest"] is None, a["latest"] or ""))
    elif sort_key == "latest_desc":
        cards.sort(key=lambda a: a["latest"] or "", reverse=True)
    # name_asc: already sorted
    return cards


def _curated_album_sections(cards: list[dict]) -> list[dict]:
    """Split album cards (already in curated order) into the framed groups
    of the Curated /albums view: a gallery.cfg `album_order` line like
    `#trips` opens a named group that frames every album listed below it.
    Returns [{label, cards}, ...] in cfg order — label None for the
    frameless chunks (albums listed above the first marker, plus a trailing
    chunk for albums that aren't listed at all) — or [] when the order
    defines no groups, so callers keep the flat grid."""
    entries = _gallery_config().get("album_order", [])
    if not any(e.startswith("#") for e in entries):
        return []
    labels = [""]  # section labels in cfg order; "" = the frameless lead
    key_label: dict[str, str] = {}
    label = ""
    for e in entries:
        if e.startswith("#"):
            label = e[1:].strip()
            if label not in labels:
                labels.append(label)
        else:
            k = _album_order_key(e)
            if k and k not in key_label:
                key_label[k] = label
    buckets: dict[str, list[dict]] = {lab: [] for lab in labels}
    unlisted: list[dict] = []
    for card in cards:
        lab = key_label.get(_album_order_key(card["album"]))
        (unlisted if lab is None else buckets[lab]).append(card)
    sections = [{"label": lab or None, "cards": buckets[lab]}
                for lab in labels if buckets[lab]]
    if unlisted:
        sections.append({"label": None, "cards": unlisted})
    return sections


def _album_breadcrumbs(album: str) -> list[dict]:
    """[{name, path, icon}, ...] for each ancestor segment of an album path,
    so templates can render HOME / ALBUMS / japan / tokyo with linkable
    parts. `icon` is that segment's album.cfg mark, or None."""
    acc: list[str] = []
    out: list[dict] = []
    for seg in album.split("/"):
        if not seg:
            continue
        acc.append(seg)
        path = "/".join(acc)
        out.append({"name": _album_display_name(path), "path": path,
                    "icon": _album_icon_url(path)})
    return out


def _render_markdown(text: str) -> str:
    """Render an album-description markdown string to HTML. python-markdown is
    a pure-Python dependency (requirements.txt); if it's ever missing we still
    produce readable paragraphs rather than crashing the album page."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        import markdown as _md
        return _md.markdown(text, extensions=["extra", "sane_lists"], output_format="html5")
    except Exception:
        import html as _html
        blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
        return "".join("<p>" + _html.escape(b).replace("\n", "<br>") + "</p>" for b in blocks)


def _album_description(album: str, lang: str = i18n.DEFAULT_LANG) -> str | None:
    """An album's description is a per-language markdown file in its `.album/`
    folder: album_en.md / album_de.md / album_jp.md. The active language wins;
    a missing translation falls back to English, then to a plain album.md,
    then to the first *.md in the folder — so a partially translated gallery
    still shows something everywhere. Rendered to HTML; None when the folder
    has no markdown at all."""
    meta = _album_meta_dir(album)
    if meta is None:
        return None
    candidates = [meta / f"album_{lang}.md",
                  meta / f"album_{i18n.DEFAULT_LANG}.md",
                  meta / "album.md"]
    md_file = next((p for p in candidates if p.is_file()), None)
    if md_file is None:
        md_file = next(iter(sorted(p for p in meta.glob("*.md") if p.is_file())), None)
    if md_file is None:
        return None
    try:
        raw = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _render_markdown(raw) or None


# ----- config file format (album.cfg / gallery.cfg) ----------------------
# Both files share one format: plain `key = value` lines, `#`/`;` comments.
# List values accumulate — comma-separate, repeat the key, or (easiest to
# read) put one entry per line below the key: any non-comment line without
# a `=` continues the key above it.
#
# Everything that describes an album rather than being one of its photos
# lives together in a `.album/` folder inside the album (ALBUM_META_DIR),
# so the photo folder itself stays nothing but photos:
#
#   photos/japan_2026/.album/album.cfg          <- settings (keys below)
#   photos/japan_2026/.album/album_en.md        <- description, per language
#   photos/japan_2026/.album/MusashiBrush.otf   <- `font = …` title face
#   photos/japan_2026/.album/icon.svg           <- `icon = …` album mark
#
# This is the only place looked at — a cfg or description left in the photo
# folder itself is ignored. gallery.cfg is NOT part of this: it configures
# the gallery as a whole and stays at the root of PHOTOS_DIR.
#
# album.cfg keys (file sits in the album's `.album/` folder):
#   name = Japan 2026    -> the album's display name, used everywhere the album
#                           is named (cards, breadcrumbs, hero title, API).
#                           The folder name stays the URL; this is only what
#                           the reader sees. Unset = the folder name with its
#                           underscores relaxed into spaces.
#   collection = true    -> the album shows every photo in its subtree (its
#                           own + all sub-folders) as one flat collection.
#   cover = sub/pic.jpg  -> pin the album cover (path relative to the album)
#                           instead of auto-picking the newest photo.
#   showcase = true      -> showcase album (★ rail on /albums + welcome).
#   featured = a.jpg, …  -> featured photos (see _recompute_featured).
#   reel = featured|random|off -> what the album's hero slideshow shows.
#   order = a.jpg, …     -> curated photo order ("Curated" sort option).
#   sort = curated|date_desc|… -> preselect the sort option for this album.
#   tags = paris, night  -> the album's tags, shown under its hero title.
#                           Album-level and display-only: unrelated to the
#                           per-image tags a `.tags` sidecar feeds into the
#                           tags/image_tags tables (scanner.py), which are
#                           what the ?tag= grid filter reads.
#   effect = sakura      -> ambient effect layer on this album's page
#                           (whitelisted in ALBUM_EFFECTS; see initAlbumFx).
#   icon = mark.svg      -> the album's own mark, shown wherever the album is
#                           named; the file sits next to the cfg in `.album/`
#                           (see the per-album icon section further down).
#   font = Musashi.otf   -> display face for the album's hero title; the file
#                           sits next to the cfg in `.album/` (see the
#                           per-album title font section further down).
#   font_scale = 1.25    -> size multiplier for that face (see the same
#                           section); only read when `font` is set.
#   wallpaper = bg.mp4   -> the album's own page backdrop on desktop (video or
#                           still); sub-albums inherit it (see the per-album
#                           wallpaper section further down).
#   wallpaper_mobile = bg.jpg -> the same for phones. Stills only — phones
#                           never load a backdrop clip. Either key alone is
#                           fine; the missing side falls back to the site
#                           default (bg.mp4 / bg-poster.jpg).
#   accent = #7ad1ff     -> this album's pages wear their own accent colour
#                           instead of the site's; sub-albums inherit it
#                           (see the per-album theme section further down).
#   wallpaper_tint = off -> how much colour the backdrop keeps. Unset = the
#                           site treatment (near-greyscale); `off` = the
#                           picture in full colour; 0–1 = partial.
#   wallpaper_dim = .9   -> brightness of that same backdrop, 1 = untouched.
#                           Unset = the site default (0.72).
#   loc = Paris, France  -> Location line in the stats block under the description.
#   stat = Label: Value  -> one freeform KEY / VALUE stat line (repeat the key
#                           for more). Avoid commas in the value — the parser
#                           comma-splits list values (see _parse_cfg). These
#                           sit above the auto SPAN/DEVICE/FOCAL/APERTURE/DATA
#                           readouts derived from the photos' EXIF (_album_stats).
#   stats = off          -> hide the whole stats block for this album.
ALBUM_META_DIR = scanner.ALBUM_META_DIR

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", "none", "hide"}

# Ambient per-album page effects (album.cfg `effect = ...`). Whitelisted so
# a cfg typo can't inject arbitrary class names / JS hooks into the page.
ALBUM_EFFECTS = {"sakura"}


def _cfg_bool(v: str | None) -> bool:
    return str(v or "").strip().lower() in _TRUE


def _parse_cfg(text: str, group_keys: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Parse cfg text into a lower-cased key -> [values] dict. Repeated keys
    and comma lists accumulate in order; bare lines append to the key above
    (one entry per line). A key given with an empty value still registers
    (empty list), so "present but empty" is distinguishable from "absent".
    Inside a key listed in `group_keys`, a bare `#label` line (# glued to
    the label) is kept as a "#label" group-marker entry; `# spaced`, `##`
    and `;` comment styles still vanish everywhere."""
    out: dict[str, list[str]] = {}
    key: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line[0] in "#;":
            label = line[1:].strip()
            if (line[0] == "#" and label and key in group_keys
                    and not line[1].isspace() and line[1] not in "#;"):
                out[key].append("#" + label)
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip().lower()
            out.setdefault(key, [])
        elif key is None:
            continue  # stray line before any key
        else:
            val = line
        out[key].extend(i.strip() for i in val.split(",") if i.strip())
    return out


def _cfg_first(cfg: dict[str, list[str]], key: str) -> str | None:
    """First configured value for a scalar key, or None."""
    vals = cfg.get(key)
    return vals[0] if vals else None


def _album_meta_dir(album: str) -> Path | None:
    """The album's `.album/` metadata folder (see the format notes above), or
    None when the album path is bogus or the folder doesn't exist."""
    folder = (PHOTOS_DIR / album / ALBUM_META_DIR).resolve()
    try:
        folder.relative_to(PHOTOS_DIR)  # guard against path traversal
    except ValueError:
        return None
    return folder if folder.is_dir() else None


def _album_config(album: str) -> dict[str, list[str]]:
    """Parse the album's `album.cfg` (see _parse_cfg), or {} when there's no
    such file. Cheap enough to call per album card."""
    meta = _album_meta_dir(album)
    if meta is None:
        return {}
    cfg_path = meta / "album.cfg"
    if not cfg_path.is_file():
        return {}
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_cfg(text)


def _album_display_name(album: str, cfg: dict[str, list[str]] | None = None) -> str:
    """What an album is CALLED, as opposed to where it lives. album.cfg
    `name = Japan 2026` wins; without one the folder's own last segment is
    used with underscores relaxed into spaces (`japan_2026` -> `japan 2026`),
    which is what the hero title always did on its own.

    The folder name stays the identity everywhere it matters — URLs, cfg
    lookups, the `album` column, cover paths — so renaming here is free and
    never breaks a link. _parse_cfg comma-splits values, so the parts are
    rejoined the way `loc` is (_album_stats): a name may contain commas."""
    cfg = _album_config(album) if cfg is None else cfg
    pretty = ", ".join(v.strip() for v in (cfg.get("name") or []) if v.strip())
    if pretty:
        return pretty
    return (album or "").rsplit("/", 1)[-1].replace("_", " ")


def _album_tags(album: str, cfg: dict[str, list[str]] | None = None) -> list[str]:
    """The album's `tags = a, b, c`, de-duplicated, order kept. A leading `#`
    is optional in the cfg — the hero renders one either way, so accept both
    spellings rather than printing `##night`.

    These describe the ALBUM and are display-only. The per-image tags that a
    `.tags` sidecar feeds into the tags/image_tags tables are a separate
    thing (scanner._read_sidecar_tags) and still own the ?tag= grid filter."""
    cfg = _album_config(album) if cfg is None else cfg
    out: list[str] = []
    seen: set[str] = set()
    for raw in cfg.get("tags") or []:
        name = raw.strip().lstrip("#").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _album_collection(album: str, cfg: dict[str, list[str]] | None = None) -> bool:
    """album.cfg `collection = true`: the album stands for its whole subtree
    (its own photos + every sub-folder's) rather than just the photos sitting
    directly in it. One definition for the album page, the single-image
    neighbour scroll and the JSON API — see _photo_scope."""
    cfg = _album_config(album) if cfg is None else cfg
    return _cfg_bool(_cfg_first(cfg, "collection"))


def _photo_scope(album: str, subtree: bool | None = None) -> tuple[str, str, tuple, bool, bool]:
    """SQL scope for "the photos of this album", collection-aware.

    Returns (where_simple, where_join, params, collection, subtree) —
    `where_simple` for queries over `images` alone, `where_join` for the ones
    that join (columns qualified as `i.`). By default a `collection = true`
    album resolves to its whole subtree and every other album to its own
    folder; `subtree=True/False` forces either scope regardless of the cfg.
    substr() (not LIKE) so `_`/`%` in album names can't act as wildcards."""
    collection = _album_collection(album)
    wide = collection if subtree is None else bool(subtree)
    if wide:
        prefix = album + "/"
        return ("(album = ? OR substr(album, 1, ?) = ?)",
                "(i.album = ? OR substr(i.album, 1, ?) = ?)",
                (album, len(prefix), prefix), collection, True)
    return ("album = ?", "i.album = ?", (album,), collection, False)


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

def _humanize_bytes(n: int | None) -> str | None:
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


def _clean_device(make: str | None, model: str | None) -> str | None:
    """Human camera name from EXIF Make/Model: 'Apple' + 'iPhone 17' ->
    'iPhone 17'; 'FUJIFILM' + 'X100V' -> 'FUJIFILM X100V'. Drops a Make the
    Model already echoes."""
    make = (make or "").strip()
    model = (model or "").strip()
    if not model:
        return make or None
    # Apple brands by model alone ("iPhone 17", never "Apple iPhone 17")
    if make.lower() == "apple":
        return model
    if make and make.split()[0].lower() in model.lower():
        return model
    return f"{make} {model}" if make else model


def _fmt_num(v: float) -> str:
    """2.0 -> '2', 1.6 -> '1.6' (trims a trailing zero decimal)."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _album_stats(images: list[dict], cfg: dict[str, list[str]], lang: str) -> dict:
    """Stats block for the description card: {'context': [...], 'capture': [...],
    'has': bool}. Each entry is {'key': LABEL, 'val': text}. `images` is the
    album's whole photo set (unfiltered by any ?tag=), so the readouts describe
    the album, not the current grid view."""
    if (_cfg_first(cfg, "stats") or "").strip().lower() in _FALSE:
        return {"context": [], "capture": [], "has": False}

    # --- context: editorial, from album.cfg -----------------------------
    context: list[dict] = []
    loc = ", ".join(v.strip() for v in (cfg.get("loc") or []) if v.strip())
    if loc:
        context.append({"key": i18n.t(lang, "stat.location"), "val": loc})
    # freeform `stat = Label: Value` (one fact per line; avoid commas in the
    # value — the cfg parser comma-splits list values, see _parse_cfg).
    for raw in cfg.get("stat") or []:
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            k, _, v = raw.partition(":")
            entry = {"key": k.strip(), "val": v.strip()}
        else:
            entry = {"key": "", "val": raw}
        if entry["val"]:
            context.append(entry)

    # --- capture: auto, from the photos' EXIF + size --------------------
    total = 0
    tmin = tmax = None
    devices: Counter = Counter()
    fnums: list[float] = []
    focals: list[int] = []
    for im in images:
        total += im.get("size") or 0
        t = im.get("taken_at")
        if t:
            tmin = t if tmin is None or t < tmin else tmin
            tmax = t if tmax is None or t > tmax else tmax
        try:
            exif = json.loads(im["exif_json"]) if im.get("exif_json") else {}
        except (ValueError, TypeError):
            exif = {}
        dev = _clean_device(exif.get("Make"), exif.get("Model"))
        if dev:
            devices[dev] += 1
        fn = exif.get("FNumber")
        if isinstance(fn, (int, float)) and fn > 0:
            fnums.append(float(fn))
        fl = exif.get("FocalLengthIn35mmFilm") or exif.get("FocalLength")
        if isinstance(fl, (int, float)) and fl > 0:
            focals.append(round(float(fl)))

    capture: list[dict] = []
    span = i18n.date_span(lang, tmin, tmax)
    if span:
        capture.append({"key": i18n.t(lang, "stat.span"), "val": span})
    if devices:
        dev = devices.most_common(1)[0][0]
        # a couple of stray cameras shouldn't hide the dominant one, but note them
        if len(devices) > 1:
            dev = f"{dev} +{len(devices) - 1}"
        capture.append({"key": i18n.t(lang, "stat.device"), "val": dev})
    if focals:
        lo, hi = min(focals), max(focals)
        capture.append({"key": i18n.t(lang, "stat.focal"),
                        "val": f"{lo} mm" if lo == hi else f"{lo}–{hi} mm"})
    if fnums:
        lo, hi = min(fnums), max(fnums)
        val = f"ƒ{_fmt_num(lo)}" if lo == hi else f"ƒ{_fmt_num(lo)}–{_fmt_num(hi)}"
        capture.append({"key": i18n.t(lang, "stat.aperture"), "val": val})
    data = _humanize_bytes(total)
    if data:
        capture.append({"key": i18n.t(lang, "stat.data"), "val": data})

    return {"context": context, "capture": capture, "has": bool(context or capture)}


def _config_cover_rel(album: str, manual: str | None) -> str | None:
    """Resolve an album.cfg `cover` value (path relative to the album, with
    or without the album prefix) to a real indexed rel_path, or None."""
    if not manual:
        return None
    rel = manual.strip().strip("/")
    if not rel:
        return None
    if not (rel == album or rel.startswith(album + "/")):
        rel = f"{album}/{rel}"
    row = db.conn().execute(
        "SELECT rel_path FROM images WHERE rel_path = ?", (rel,)
    ).fetchone()
    return row["rel_path"] if row else None


# ----- per-album title font (album.cfg `font = ...`) --------------------
# An album can bring its own display face for its hero title: drop the font
# file into the album's `.album/` folder and name it in album.cfg
#   font = MusashiBrush.otf
# The face reaches the page as a generated stylesheet rather than an inline
# <style>, because the CSP (style-src 'self', see CSP below) drops inline
# styles: /album-font.css/{album} carries the @font-face plus the
# --album-title-font custom property that style.css's `.album-font
# .album-hero__title` rule reads, and /album-font/{album} serves the file.
# The family name is a constant — only one album's sheet ever loads on a
# page, so it cannot collide.
#
# The sheet also carries --album-title-scale from
#   font_scale = 1.25
# a multiplier on the hero title's size. Display faces differ a lot in how
# much of the em they actually ink — a brush face lands visibly smaller than
# a geometric one at the same px — so the album that ships the face is also
# where its size is tuned, rather than the shared clamp in style.css.
ALBUM_FONT_FAMILY = "album-title"

# Guard rails for `font_scale`: enough room to fix a face that reads a size
# off, not enough for a cfg typo (font_scale = 100) to blow the title across
# the page. Out-of-range and unparseable values fall back to no scaling.
ALBUM_FONT_SCALE_RANGE = (0.5, 2.5)

# Extension -> (CSS `format()` hint, response media type). Doubles as the
# whitelist of what may be served: a `font = …` naming anything else (an
# album_en.md, say) resolves to nothing.
ALBUM_FONT_TYPES = {
    ".otf": ("opentype", "font/otf"),
    ".ttf": ("truetype", "font/ttf"),
    ".woff2": ("woff2", "font/woff2"),
    ".woff": ("woff", "font/woff"),
}


def _album_font_file(album: str) -> Path | None:
    """The album's configured title font as a real file, or None. The cfg
    value is a bare filename resolved inside the album's `.album/` folder:
    anything carrying a path separator, or an extension outside
    ALBUM_FONT_TYPES, is rejected — so this only ever resolves to a font
    sitting next to the album.cfg that named it."""
    meta = _album_meta_dir(album)
    if meta is None:
        return None
    name = (_cfg_first(_album_config(album), "font") or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in ALBUM_FONT_TYPES:
        return None
    path = meta / name
    return path if path.is_file() else None


def _album_font_scale(album: str) -> float | None:
    """The album's `font_scale` as a float, or None when it is unset, not a
    number, or outside ALBUM_FONT_SCALE_RANGE. None means "don't emit the
    property" — style.css then falls back to its own default of 1."""
    raw = (_cfg_first(_album_config(album), "font_scale") or "").strip()
    if not raw:
        return None
    try:
        scale = float(raw.replace(",", "."))
    except ValueError:
        return None
    lo, hi = ALBUM_FONT_SCALE_RANGE
    return scale if lo <= scale <= hi else None


def _album_font_version(album: str) -> int:
    """Cache-busting stamp for an album's generated font sheet: the newest
    mtime of the font file and of the album.cfg naming it (same idea as
    _static_url). The cfg has to count — the sheet carries `font_scale`
    too, and retuning that never touches the font file, so versioning on
    the font alone would leave the edit masked by a year-long cache."""
    meta = _album_meta_dir(album)
    sources = [_album_font_file(album), (meta / "album.cfg") if meta else None]
    stamps = []
    for path in filter(None, sources):
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return max(stamps, default=0)


def _album_font_css_url(album: str) -> str | None:
    """Cache-busting URL of the album's generated font stylesheet, or None
    when the album configures no font — both this sheet and the font it
    points at are cached hard, so the version is what makes edits land."""
    if _album_font_file(album) is None:
        return None
    return f"/album-font.css/{quote(album)}?v={_album_font_version(album)}"


def _album_font_preload(album: str) -> dict | None:
    """Preload descriptor (font url + media type) for the album's title face,
    or None when it configures none. Without it the browser can't even learn
    the font's URL until it has fetched AND parsed the generated
    /album-font.css sheet, and only then fetches the file — a serial waterfall
    that leaves the hero title in the fallback face for a visible beat before
    it swaps in. Preloading the file in the page head runs that download in
    parallel with the stylesheet instead, so the face is there by first paint
    and the swap never shows. Same url/version as the @font-face src, so both
    requests hit one cache entry."""
    font = _album_font_file(album)
    if font is None:
        return None
    _fmt, mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    return {
        "href": f"/album-font/{quote(album)}?v={_album_font_version(album)}",
        "type": mime,
    }


# ----- per-album theme (album.cfg `accent` / `wallpaper_tint` / `_dim`) ---
# An album can repaint the one accent colour of its own pages, and retune how
# the backdrop behind them is treated:
#   accent          = #7ad1ff   the accent for this album's pages
#   wallpaper_tint  = off       how much colour the backdrop keeps
#   wallpaper_dim   = .9        how bright it is
# All three reach the page the way the title face does — as a generated
# stylesheet (/album-theme.css/{album}), because the CSP drops inline styles
# (style-src 'self'; see CSP below). The sheet only redefines tokens style.css
# already reads, so no rule anywhere has to know that an album can carry an
# accent of its own.
#
# They inherit down the tree the way `wallpaper` does: a sub-album that sets
# nothing takes the nearest ancestor's, otherwise `japan_2026/kansai` would
# drop back to the site colours mid-browse while still wearing its parent's
# wallpaper.
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Guard rails, same spirit as ALBUM_FONT_SCALE_RANGE: room to tune, not room
# for a typo to black the page out — `wallpaper_dim = 0` is a backdrop nobody
# can see, so the floor is a real one.
ALBUM_WALLPAPER_DIM_RANGE = (0.25, 1.0)
# The site treatment, restated here because the generated sheet has to emit a
# COMPLETE filter: it replaces --wallpaper-filter, it cannot patch one
# function out of it. Keep in step with the token in style.css.
WALLPAPER_TINT_DEFAULT = 0.92
WALLPAPER_DIM_DEFAULT = 0.72
WALLPAPER_CONTRAST = 1.04
# Contrast floor both accent readings are held to: --acc is small text on
# black AND a face under black label text, so both want luminance.
ACCENT_MIN_CONTRAST = 4.5


def _srgb_lum(rgb):
    """WCAG relative luminance of an 8-bit sRGB triple."""
    chan = []
    for v in rgb:
        v /= 255.0
        chan.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = chan
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = _srgb_lum(a), _srgb_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hls_rgb(h: float, l: float, s: float) -> tuple[int, int, int]:
    return tuple(round(c * 255)
                 for c in colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s))


def _parse_hex_color(raw: str | None) -> tuple[int, int, int] | None:
    """`#abc` / `#aabbcc` -> (r, g, b). Anything else is None: the value ends
    up inside a generated stylesheet, so only three parsed integers ever get
    near it — never a string that came out of a cfg."""
    raw = (raw or "").strip()
    if not _HEX_COLOR.match(raw):
        return None
    body = raw[1:]
    if len(body) == 3:
        body = "".join(c * 2 for c in body)
    return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))


def _accent_shades(rgb: tuple[int, int, int]) -> dict:
    """The three faces style.css needs, derived from one colour by moving
    only LIGHTNESS along its own hue. Each answers a legibility question the
    sheet cannot answer for itself:
      acc   small text on black AND a face under black label text — one
            constraint either way, luminance, so a too-dark cfg colour is
            LIFTED rather than rendered unreadable (`lifted` says so, and the
            cfg checkers surface it)
      deep  the single face that carries WHITE text (the hero CTA), so it
            goes the other way until white reads on it
      soft  the hover step above acc, again under black text
    Saturation is capped on `deep` alone: at full chroma a mid-lightness hue
    turns electric, which none of the other shades of the same colour do."""
    h, l, sat = colorsys.rgb_to_hls(*[v / 255 for v in rgb])
    acc_l = l
    while (acc_l < 0.97
           and _contrast(_hls_rgb(h, acc_l, sat), (0, 0, 0)) < ACCENT_MIN_CONTRAST):
        acc_l += 0.02
    deep_s, deep_l = min(sat, 0.78), min(acc_l, 0.58)
    while (deep_l > 0.12
           and _contrast(_hls_rgb(h, deep_l, deep_s), (255, 255, 255)) < ACCENT_MIN_CONTRAST):
        deep_l -= 0.02
    acc = _hls_rgb(h, acc_l, sat)
    return {
        "acc": "#%02x%02x%02x" % acc,
        "rgb": "%d,%d,%d" % acc,
        "deep": "#%02x%02x%02x" % _hls_rgb(h, deep_l, deep_s),
        "soft": "#%02x%02x%02x" % _hls_rgb(h, acc_l + (1 - acc_l) * 0.42, sat),
        "lifted": acc != tuple(rgb),
    }


def _cfg_ratio(raw: str, fallback: float, span: tuple[float, float],
               off: float) -> float:
    """A 0-1 cfg number, with off/none/no as a word for one end of it. Out of
    range or unparseable falls back to the SITE value rather than to an
    extreme — a typo should not black out a page."""
    val = (raw or "").strip().lower()
    if val in _FALSE:
        return off
    if val in _TRUE:
        return 1.0
    try:
        num = float(val.replace(",", "."))
    except ValueError:
        return fallback
    lo, hi = span
    return num if lo <= num <= hi else fallback


def _cfg_inherited(album: str | None, key: str) -> tuple[str, str] | None:
    """(owning album, raw value) for the nearest album from `album` upwards
    whose cfg sets `key` to something non-empty. The generic form of the walk
    _album_wallpaper_source does for its two file keys."""
    if not album:
        return None
    parts = album.replace("\\", "/").strip("/").split("/")
    for depth in range(len(parts), 0, -1):
        owner = "/".join(parts[:depth])
        raw = (_cfg_first(_album_config(owner), key) or "").strip()
        if raw:
            return owner, raw
    return None


def _album_accent(album: str | None) -> dict | None:
    """The album's accent as the three derived faces, or None when neither it
    nor any ancestor sets a usable `accent = #hex`."""
    found = _cfg_inherited(album, "accent")
    if found is None:
        return None
    rgb = _parse_hex_color(found[1])
    return None if rgb is None else _accent_shades(rgb)


def _wallpaper_knob(album: str | None, key: str, default: float,
                    span: tuple[float, float], off: float) -> float | None:
    """One backdrop knob, resolved through three tiers: this album and its
    ancestors, then gallery.cfg, then the built-in default. Returns None when
    nobody set it at all — the caller needs to tell "nothing configured" from
    "configured to the same value the site uses", because that is what decides
    whether a stylesheet is emitted for this page at all."""
    found = _cfg_inherited(album, key)
    if found is None:
        raw = (_cfg_first(_gallery_config(), key) or "").strip()
        if not raw:
            return None
    else:
        raw = found[1]
    return _cfg_ratio(raw, default, span, off=off)


def _wallpaper_decls(album: str | None = None) -> list[str]:
    """The backdrop half of a theme sheet: `--wallpaper-filter`, plus
    `--wallpaper-bloom` when the tint is off. Empty when neither the album
    chain nor gallery.cfg touches either knob — style.css's own tokens then
    stand and, if nothing else is themed either, no sheet is emitted at all.

    Two things `tint = off` has to do, and the second one is easy to miss:
    drop the greyscale, AND drop the accent wash `.site-bg::after` lays over
    the picture. A backdrop asked for in full colour that still had a coloured
    bloom on it was, correctly, reported as still tinted (user, 2026-09-01).
    What `tint = off` does NOT drop is the dimming: full colour, but still a
    backdrop you can put text on. Only turning BOTH knobs off yields
    `filter: none`."""
    tint = _wallpaper_knob(album, "wallpaper_tint", WALLPAPER_TINT_DEFAULT,
                           (0.0, 1.0), off=0.0)
    dim = _wallpaper_knob(album, "wallpaper_dim", WALLPAPER_DIM_DEFAULT,
                          ALBUM_WALLPAPER_DIM_RANGE, off=1.0)
    if tint is None and dim is None:
        return []
    if tint is None:
        tint = WALLPAPER_TINT_DEFAULT
    if dim is None:
        dim = WALLPAPER_DIM_DEFAULT
    if tint <= 0 and dim >= 1:
        filt = "none"
    else:
        parts = []
        if tint > 0:
            parts.append("grayscale(%g)" % tint)
        if dim < 1:
            parts.append("brightness(%g)" % dim)
        parts.append("contrast(%g)" % WALLPAPER_CONTRAST)
        filt = " ".join(parts)
    decls = ["--wallpaper-filter:%s" % filt]
    if tint <= 0:
        decls.append("--wallpaper-bloom:transparent")
    return decls


def _theme_version(album: str | None) -> int:
    """Cache-busting stamp: the newest mtime of every cfg that can own a value
    on this page — each album.cfg from the album up to the root, plus
    gallery.cfg. Editing a VALUE never touches a file whose name travels in
    the URL, so the cfg mtimes are all there is to version on."""
    paths = [PHOTOS_DIR / GALLERY_CFG_NAME]
    if album:
        parts = album.replace("\\", "/").strip("/").split("/")
        for depth in range(len(parts), 0, -1):
            meta = _album_meta_dir("/".join(parts[:depth]))
            if meta is not None:
                paths.append(meta / "album.cfg")
    stamps = []
    for path in paths:
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return max(stamps, default=0)


def _theme_decls(album: str | None) -> list[str]:
    """The custom-property declarations this page's theme sheet carries.
    Every value is re-serialised from parsed numbers, never printed straight
    out of a cfg."""
    decls = []
    accent = _album_accent(album)
    if accent is not None:
        decls += ["--acc:%s" % accent["acc"], "--acc-rgb:%s" % accent["rgb"],
                  "--acc-deep:%s" % accent["deep"], "--acc-soft:%s" % accent["soft"]]
    decls += _wallpaper_decls(album)
    return decls


def _theme_css_url(album: str | None = None) -> str | None:
    """Cache-busting URL of the generated theme sheet for a page, or None when
    nothing is themed and style.css's own tokens stand. Two routes behind it:
    an album path when there is one, the site-wide sheet otherwise — a page
    with no album still needs gallery.cfg's backdrop treatment."""
    if not _theme_decls(album):
        return None
    stamp = _theme_version(album)
    if not album:
        return "/site-theme.css?v=%d" % stamp
    return "/album-theme.css/%s?v=%d" % (quote(album), stamp)

# ----- per-album icon (album.cfg `icon = ...`) --------------------------
# Any album can carry a small mark of its own — a civic emblem, a crest, a
# logo — rendered wherever the album is named: its card in the grids, the
# hero title, the breadcrumb, and the stops of the trip timeline. That
# last one is where this started, as three hard-coded SVGs under
# /static/emblems; the mark now belongs to the album instead, so every
# album gets one for free and the timeline simply reads its stops' albums.
#
# Same shape as the title font: drop the file into the album's `.album/`
# folder, name it in album.cfg
#   icon = kansai.svg
# and /album-icon/{album} serves it back. Nothing is looked at without that
# key, so an album without one just renders without a mark.
ALBUM_ICON_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _album_icon_file(album: str) -> Path | None:
    """The album's configured icon as a real file, or None. Mirrors
    _album_font_file: the cfg value is a bare filename resolved inside the
    album's `.album/` folder, and anything carrying a path separator or an
    extension outside ALBUM_ICON_TYPES is rejected — so this only ever
    resolves to an image sitting next to the album.cfg that named it."""
    meta = _album_meta_dir(album)
    if meta is None:
        return None
    name = (_cfg_first(_album_config(album), "icon") or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in ALBUM_ICON_TYPES:
        return None
    path = meta / name
    return path if path.is_file() else None


def _album_icon_url(album: str | None) -> str | None:
    """Cache-busting URL of an album's icon, or None when it configures
    none — the file is served with a year-long cache, so the version stamp
    is what makes an edit land. Newest mtime of the icon AND of the cfg
    naming it, because pointing `icon =` at a different file doesn't change
    this URL's path (the filename never travels in it)."""
    if not album:
        return None
    icon = _album_icon_file(album)
    if icon is None:
        return None
    meta = _album_meta_dir(album)
    stamps = []
    for path in (icon, (meta / "album.cfg") if meta else None):
        if path is None:
            continue
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return f"/album-icon/{quote(album)}?v={max(stamps, default=0)}"


# ----- per-album wallpaper (album.cfg owns it) --------------------------
# Same shape as the icon and the title font: drop the file into the album's
# `.album/` folder and name it in album.cfg
#   wallpaper        = kyoto-night.mp4     -> desktop backdrop
#   wallpaper_mobile = kyoto-night.jpg     -> phones
# Two keys, because the two are genuinely different assets: desktop can carry
# a video, phones must not (app.js never loads one there, and a 6 MB clip on
# a phone plan is the reason it doesn't). Either key on its own is fine; the
# missing side falls back to the site default (bg.mp4 / bg-poster.jpg).
#
# A sub-album with no wallpaper of its own inherits the nearest ancestor's,
# so `japan_2026` dresses `japan_2026/kansai/osaka` too — otherwise every
# nested folder would drop back to the site default mid-browse.
ALBUM_WALLPAPER_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
ALBUM_WALLPAPER_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".avif": "image/avif",
}
ALBUM_WALLPAPER_TYPES = {**ALBUM_WALLPAPER_VIDEO_TYPES, **ALBUM_WALLPAPER_IMAGE_TYPES}
# phones get a still frame, never a clip — see the note above
ALBUM_WALLPAPER_KEYS = {"desktop": ("wallpaper", ALBUM_WALLPAPER_TYPES),
                        "mobile": ("wallpaper_mobile", ALBUM_WALLPAPER_IMAGE_TYPES)}


def _album_wallpaper_file(album: str, variant: str) -> Path | None:
    """The album's own configured wallpaper as a real file, or None. Mirrors
    _album_icon_file exactly: the cfg value is a bare filename resolved inside
    the album's `.album/` folder, and anything with a path separator or an
    extension outside the whitelist is rejected — so this can only ever
    resolve to a file sitting next to the album.cfg that named it. No
    inheritance here; _album_wallpaper_source walks the tree."""
    key, allowed = ALBUM_WALLPAPER_KEYS[variant]
    meta = _album_meta_dir(album)
    if meta is None:
        return None
    name = (_cfg_first(_album_config(album), key) or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in allowed:
        return None
    path = meta / name
    return path if path.is_file() else None


def _album_wallpaper_source(album: str | None, variant: str) -> tuple[str, Path] | None:
    """(owning album, file) for the wallpaper an album shows — its own, else
    the nearest ancestor's. None when nothing up the tree configures one."""
    if not album:
        return None
    parts = album.replace("\\", "/").strip("/").split("/")
    for depth in range(len(parts), 0, -1):
        owner = "/".join(parts[:depth])
        found = _album_wallpaper_file(owner, variant)
        if found is not None:
            return owner, found
    return None


def _album_wallpaper_url(album: str | None, variant: str) -> str | None:
    """Cache-busting URL, or None when neither the album nor any ancestor
    configures this variant. Version stamp is the newest mtime of the file
    AND of the cfg naming it — repointing the key at a different file does
    not change the path, since the filename never travels in the URL."""
    src = _album_wallpaper_source(album, variant)
    if src is None:
        return None
    owner, path = src
    meta = _album_meta_dir(owner)
    stamps = []
    for p in (path, (meta / "album.cfg") if meta else None):
        if p is None:
            continue
        try:
            stamps.append(int(p.stat().st_mtime))
        except OSError:
            pass
    return f"/album-wallpaper/{variant}/{quote(owner)}?v={max(stamps, default=0)}"


def _site_bg(album: str | None = None) -> dict:
    """What base.html paints behind the page. Three slots so the template
    stays dumb and CSP-safe (no inline styles anywhere):
      still_mobile / still_desktop — <picture> sources; the browser fetches
        exactly one, and the desktop still doubles as the poster frame behind
        a loading video
      video — desktop clip, or None when the album's desktop wallpaper is a
        still image and there is nothing to play
    An album that configures nothing lands on the site defaults, which is the
    same backdrop every non-album page shows."""
    desktop = _album_wallpaper_url(album, "desktop")
    mobile = _album_wallpaper_url(album, "mobile")
    src = _album_wallpaper_source(album, "desktop")
    desktop_is_video = (src is not None
                        and src[1].suffix.lower() in ALBUM_WALLPAPER_VIDEO_TYPES)
    default_still = _static_url("bg-poster.jpg")
    return {
        "still_mobile": mobile or default_still,
        # a desktop video still wants a poster behind it while it buffers
        "still_desktop": (default_still if desktop_is_video or not desktop else desktop),
        "video": (desktop if desktop_is_video else
                  (None if desktop else _static_url("bg.mp4"))),
    }


templates.env.globals["site_bg"] = _site_bg
templates.env.globals["theme_css_url"] = _theme_css_url


# ----- showcase / featured (album.cfg owns it) --------------------------
# album.cfg is the only source of truth for:
#   showcase = true|false   -> is this a showcase album? (★ on /albums)
#   featured = a.jpg, b.jpg -> which photos are featured (welcome hero,
#                              /api/showcase, the featured hero slideshow of
#                              the album and its parents); paths may point
#                              into sub-folders, bare filenames also match
#                              anywhere in the subtree, and `*`/`all`
#                              features every photo directly in the album.
# A missing key simply means "not featured" — nothing is ever inferred from
# a file or folder name.
def _album_is_showcase(album: str, cfg: dict[str, list[str]] | None = None) -> bool:
    cfg = _album_config(album) if cfg is None else cfg
    return "showcase" in cfg and _cfg_bool(_cfg_first(cfg, "showcase"))


def _resolve_photo_refs(album: str, items: list[str]) -> list[str]:
    """Resolve photo references from an album.cfg list value (`featured`,
    `order`) to indexed rel_paths, keeping the given order (deduped). Each
    item is a path relative to the album (sub-folders allowed); an item that
    isn't found at that exact path falls back to a fuzzy match inside the
    album's subtree, so a parent cfg can reference sub-folder photos:
      * a bare filename matches that filename anywhere in the subtree
        (every same-named file),
      * a path matches any photo whose rel_path ends with it.
    The fallback normalizes like `album_order` does — case-insensitive, so
    `Osaka/PIC.jpg` also finds `osaka/pic.jpg`."""
    c = db.conn()
    prefix = album + "/"
    subtree = None  # fallback pool, loaded once and only if something misses
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip().strip("/")
        if not item:
            continue
        rel = item if (item == album or item.startswith(prefix)) else f"{album}/{item}"
        row = c.execute("SELECT rel_path FROM images WHERE rel_path = ?", (rel,)).fetchone()
        if row:
            matches = [row["rel_path"]]
        else:
            if subtree is None:
                # substr() (not LIKE) keeps `_`/`%` in album names literal.
                subtree = c.execute(
                    "SELECT rel_path, filename FROM images "
                    "WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY rel_path",
                    (album, len(prefix), prefix),
                ).fetchall()
            key = _album_order_key(item)
            if "/" in key:
                matches = [r["rel_path"] for r in subtree
                           if (k := _album_order_key(r["rel_path"])) == key
                           or k.endswith("/" + key)]
            else:
                matches = [r["rel_path"] for r in subtree
                           if _album_order_key(r["filename"]) == key]
        for rel_path in matches:
            if rel_path not in seen:
                seen.add(rel_path)
                out.append(rel_path)
    return out


def _resolve_featured(album: str, items: list[str]) -> set[str]:
    """Resolve an album.cfg `featured` list to a set of indexed rel_paths.
    `*`/`all` features every photo directly in the album; everything else
    resolves like _resolve_photo_refs."""
    if any(i.strip().lower() in ("*", "all") for i in items):
        c = db.conn()
        return {r["rel_path"] for r in c.execute("SELECT rel_path FROM images WHERE album = ?", (album,))}
    return set(_resolve_photo_refs(album, items))


def _recompute_featured() -> None:
    """Recompute the `is_showcase` flag for every photo from the album.cfg
    `featured` lists. The single owner of the column — runs at startup and
    after every scan / album.cfg change."""
    c = db.conn()
    featured: set[str] = set()
    for album in _albums_with_ancestors():
        cfg = _album_config(album)
        if "featured" in cfg:
            featured |= _resolve_featured(album, cfg["featured"])
    # Apply the whole set in ONE statement. Clearing the column and adding the
    # flags back row by row would be visible to anyone reading mid-flight: the
    # app shares a single sqlite connection (db.py), so concurrent SELECTs run
    # inside this very transaction and saw the intermediate state — a reel that
    # rendered empty or half-filled while a recompute was in progress. A single
    # UPDATE has no intermediate state to observe. The set is passed as one JSON
    # array rather than N placeholders so a `featured = *` album can't run into
    # the host-parameter limit; `WHERE is_showcase <> …` keeps it to the rows
    # that actually change.
    want = "(rel_path IN (SELECT value FROM json_each(?)))"
    payload = json.dumps(sorted(featured))
    with db.lock():
        c.execute(f"UPDATE images SET is_showcase = {want} WHERE is_showcase <> {want}",
                  (payload, payload))
        c.commit()


# The watcher recomputes featured flags when an album.cfg changes, but its
# debounce means a save + immediate reload can still render stale flags (and
# a recompute racing a mid-write read may even drop them until the next
# scan). Album pages therefore stat their own album.cfg per request and run
# the recompute inline the moment the file's mtime differs from the last
# look — a cfg edit + reload then always shows the new featured state.
_cfg_seen_mtimes: dict[str, float] = {}
_cfg_seen_lock = threading.Lock()


def _refresh_featured_on_cfg_change(album: str) -> None:
    # Called with the raw path straight off the URL, before the album is
    # known to exist — so the traversal guard stays here rather than leaning
    # on _album_meta_dir, which cannot tell "bogus path" from "no folder"
    # and would let junk paths seed the mtime map and trigger a recompute.
    folder = (PHOTOS_DIR / album).resolve()
    try:
        folder.relative_to(PHOTOS_DIR)  # guard against path traversal
    except ValueError:
        return
    try:
        mtime = (folder / ALBUM_META_DIR / "album.cfg").stat().st_mtime
    except OSError:
        mtime = 0.0  # missing file is a state too (cfg deleted -> refresh)
    with _cfg_seen_lock:
        stale = _cfg_seen_mtimes.get(album) != mtime
        if stale:
            _cfg_seen_mtimes[album] = mtime
    if stale:
        _recompute_featured()


# ----- trip dashboard ---------------------------------------------------
# An optional "trip" overlay (a live flight countdown + an itinerary
# timeline with a "you are here" marker) rendered at the top of one album.
# Config is keyed by the album's lower-cased path (e.g. `japan_2026`).
# All dates are wall-clock; the live
# countdown and current-stop highlight are computed client-side (initTrip
# in app.js) against the viewer's own clock — so it reads correctly both
# from home before the flight and on the ground once the trip is underway.
TRIPS: dict[str, dict] = {
    "japan_2026": {
        "title": "Japan 2026",
        "jp": "日本",
        # flight out (local wall-clock). 12:00 = noon departure.
        "depart": "2026-08-09T12:00:00",
        # A stop is a REGION, not a single city: the trip stays put in one
        # part of the country per leg, and its album holds everything shot
        # there. lat/lon stay the region's base city (Kansai -> Osaka,
        # Hokkaido -> Sapporo, Kanto -> Tokyo) — they feed the
        # /api/trip-weather proxy (see below) and the route map's dot, both
        # of which need one point. The map's highlight is the whole region
        # (tools/generate_trip_map.py). The civic emblem on a stop is NOT
        # configured here — it is the `icon = …` of the region's own album
        # (see the per-album icon section), so the mark travels with the
        # album wherever it is named.
        "stops": [
            # A stop's end / the next stop's start is the domestic flight's
            # departure (JST wall-clock), so the countdown runs to the gate
            # rather than to midnight of the travel day.
            {"city": "Kansai",   "jp": "関西",   "album": "kansai",   "start": "2026-08-10",          "end": "2026-08-16T14:30:00", "lat": 34.6937, "lon": 135.5023},
            {"city": "Hokkaido", "jp": "北海道", "album": "hokkaido", "start": "2026-08-16T14:30:00", "end": "2026-09-16T10:30:00", "lat": 43.0618, "lon": 141.3545},
            {"city": "Kanto",    "jp": "関東",   "album": "kanto",    "start": "2026-09-16T10:30:00", "end": "2027-01-02",          "lat": 35.6895, "lon": 139.6917},
        ],
    },
}

def _trip_for_album(album: str, lang: str = i18n.DEFAULT_LANG) -> dict | None:
    """Render-ready trip dashboard for `album`, or None when the album has
    no configured trip. Matched on the lower-cased album path. Each stop is
    wired to its sub-album — cover + photo count + link — so the timeline
    doubles as navigation into the region galleries (empty folders stay
    unlinked).
    Human-readable dates are localized; app.js re-renders them client-side
    in the same language (read from <html lang>)."""
    key = album.lower()
    cfg = TRIPS.get(key)
    if not cfg:
        return None
    stops = []
    for s in cfg["stops"]:
        sub = f"{album}/{s['album']}" if s.get("album") else None
        card = _album_card(sub) if sub else None
        count = card["count"] if card else 0
        stops.append({
            "city": s["city"],
            "jp": s.get("jp", ""),
            # the stop's mark is the stop album's own `icon = …`
            "icon": _album_icon_url(sub),
            "start": s["start"],
            "end": s["end"],
            "start_h": i18n.fmt_date(lang, s["start"]),
            "end_h": i18n.fmt_date(lang, s["end"]),
            "href": f"/album/{sub}" if count else None,
            "cover": card["cover"] if card else None,
            "count": count,
        })
    return {
        "key": key,  # TRIPS key, echoed as data-trip-key for /api/trip-weather
        "title": cfg["title"],
        "jp": cfg.get("jp", ""),
        "depart": cfg["depart"],
        "depart_h": i18n.fmt_date(lang, cfg["depart"]),
        "stops": stops,
    }


# ----- trip weather (server-side proxy) ----------------------------------
# Current conditions per trip stop, fetched from the Open-Meteo forecast API
# and re-served same-origin. Proxying is what keeps this consent-free and
# CSP-clean: the visitor's browser only ever talks to this origin (no
# third-party request, no cookies, nothing stored on the device — GDPR/
# ePrivacy don't require a banner for it), and connect-src 'self' stays.
# Upstream sees only this server's IP plus fixed base-city coordinates.
# Open-Meteo is keyless and cookie-free; data is CC BY 4.0 — attributed in
# the widget tooltip (see initTrip) and README. One upstream call covers
# all stops; results are cached for WEATHER_TTL so page-view bursts cost
# at most one fetch, and the last good payload is served on upstream errors.
WEATHER_TTL = 900  # seconds; weather for a dashboard doesn't need more
_weather_lock = threading.Lock()
_weather_cache: dict[str, tuple[float, dict]] = {}  # trip key -> (fetched_at, payload)


def _fetch_trip_weather(cfg: dict) -> dict:
    """One Open-Meteo request for every stop of `cfg` (multi-location call).
    Returns the trimmed same-origin payload; raises on network trouble —
    the endpoint decides between stale-cache and 502."""
    stops = [s for s in cfg["stops"] if "lat" in s and "lon" in s]
    if not stops:
        return {"updated": int(time.time()), "stops": []}
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=" + ",".join(str(s["lat"]) for s in stops) +
        "&longitude=" + ",".join(str(s["lon"]) for s in stops) +
        "&current=temperature_2m,weather_code,is_day"
        # today's envelope, so the widget can show a hi/lo next to "now"
        "&daily=temperature_2m_max,temperature_2m_min&forecast_days=1"
        "&timezone=Asia%2FTokyo"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "lucya.systems-gallery"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.load(resp)
    if isinstance(payload, dict):  # single-location responses aren't wrapped
        payload = [payload]
    def _first(daily: dict, key: str):
        """Today's value from a `daily` block — absent/short arrays are fine
        (hi/lo is a bonus line in the widget, never a hard requirement)."""
        vals = (daily or {}).get(key) or []
        try:
            return round(float(vals[0]))
        except (IndexError, TypeError, ValueError):
            return None

    out = []
    for s, loc in zip(stops, payload):
        cur = (loc or {}).get("current") or {}
        temp, code = cur.get("temperature_2m"), cur.get("weather_code")
        if temp is None or code is None:
            continue
        daily = (loc or {}).get("daily") or {}
        out.append({
            "city": s["city"],  # English stop key, matches data-city / data-stop-wx lookup
            "temp": float(temp),
            "code": int(code),
            "is_day": int(cur.get("is_day") or 0),
            "hi": _first(daily, "temperature_2m_max"),
            "lo": _first(daily, "temperature_2m_min"),
        })
    return {"updated": int(time.time()), "stops": out}


@app.get("/api/trip-weather")
def api_trip_weather(trip: str):
    cfg = TRIPS.get(trip)
    if not cfg:
        raise HTTPException(404, "unknown trip")
    now = time.time()
    # the lock doubles as stampede protection: concurrent misses queue up
    # behind the one request actually talking to Open-Meteo (sync endpoint,
    # so this blocks a threadpool worker, not the event loop)
    with _weather_lock:
        cached = _weather_cache.get(trip)
        if cached and now - cached[0] < WEATHER_TTL:
            data = cached[1]
        else:
            try:
                data = _fetch_trip_weather(cfg)
                _weather_cache[trip] = (now, data)
            except Exception:
                log.warning("trip weather fetch failed (%s)", trip, exc_info=True)
                if not cached:
                    raise HTTPException(502, "weather upstream unavailable")
                data = cached[1]  # stale beats nothing; retried next TTL window
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=600"})


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
# actually span more than one day (see _scope_day_count).
SORT_DAYS = "days"
SORT_DAYS_LABEL_KEY = "sort.days"
SORT_DAYS_BASE = "date_desc"


def _pick_sort(value: str | None, allowed, default: str) -> str:
    return value if value in allowed else default


def _scope_day_count(where_sql: str, params) -> int:
    """How many distinct capture days the photos in a scope cover. Drives
    whether the "By day" sort is offered at all — a single-day album (or one
    without any EXIF dates) has nothing to group."""
    row = db.conn().execute(
        f"SELECT COUNT(DISTINCT substr(taken_at, 1, 10)) FROM images "
        f"WHERE {where_sql} AND taken_at IS NOT NULL",
        params,
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _resolve_image_sort(cfg: dict[str, list[str]], sort: str | None,
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
    default_sort = _pick_sort(_cfg_first(cfg, "sort"), allowed, SORT_IMAGE_DEFAULT)
    current = _pick_sort(sort, allowed, default_sort)
    if current == SORT_CURATED:
        base = SORT_IMAGE_DEFAULT
    elif current == SORT_DAYS:
        base = SORT_DAYS_BASE
    else:
        base = current
    return current, default_sort, base


def _ancestor_trip(album: str, lang: str = i18n.DEFAULT_LANG) -> dict | None:
    """The trip config of `album` or of its nearest configured ancestor. The
    trip DASHBOARD only ever renders on the album that configures it, but the
    day sections of a sub-album (japan_2026/hokkaido/sapporo) should still
    count trip days and name the leg — so they look the trip up upwards."""
    parts = album.split("/")
    for i in range(len(parts), 0, -1):
        trip = _trip_for_album("/".join(parts[:i]), lang)
        if trip:
            return trip
    return None


def _trip_stop_on(trip: dict | None, day: str, lang: str) -> str | None:
    """Name of the trip stop a given day (YYYY-MM-DD) falls into, for the day
    headers of a trip album ("14 AUG · KANSAI"). Start-inclusive, so a travel
    day is filed under the region you arrive in — the same rule initTrip()
    uses for the live "you are here" marker. None outside the itinerary, and
    for albums without a trip."""
    if not trip:
        return None
    hit = None
    for s in trip.get("stops") or []:
        start, end = (s.get("start") or "")[:10], (s.get("end") or "")[:10]
        if start and start <= day and (not end or day <= end):
            hit = s
    if not hit:
        return None
    return (hit.get("jp") or hit["city"]) if lang == "jp" else hit["city"]


def _day_sections(images: list[dict], trip: dict | None,
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
                "stop": _trip_stop_on(trip, key, lang) if key else None,
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


def _qualify_sort(order_sql: str) -> str:
    """Prefix the image columns of a SORT_IMAGE_SQL clause with the `i.` alias,
    so the same clause also works in the queries that join tags."""
    for col in ("filename", "taken_at", "mtime", "size"):
        order_sql = order_sql.replace(col, f"i.{col}")
    return order_sql


def _image_sort_options_for_template(current: str, curated: bool = False,
                                     lang: str = i18n.DEFAULT_LANG,
                                     days: bool = False) -> list[dict]:
    keys = ([(SORT_CURATED, SORT_CURATED_LABEL_KEY)] if curated else [])
    keys += ([(SORT_DAYS, SORT_DAYS_LABEL_KEY)] if days else [])
    keys += [(k, label_key) for k, label_key, _ in SORT_IMAGE_OPTIONS]
    return [{"key": k, "label": i18n.t(lang, label_key), "active": k == current}
            for k, label_key in keys]


def _album_sort_options_for_template(current: str, curated: bool = False,
                                     lang: str = i18n.DEFAULT_LANG) -> list[dict]:
    keys = ([(SORT_CURATED, SORT_CURATED_LABEL_KEY)] if curated else [])
    keys += [(k, label_key) for k, label_key, _ in SORT_ALBUM_OPTIONS]
    return [{"key": k, "label": i18n.t(lang, label_key), "active": k == current}
            for k, label_key in keys]


def _active_sort_label(options: list[dict]) -> str:
    return next((o["label"] for o in options if o["active"]), "")


def _curated_photo_order(album: str, cfg: dict[str, list[str]]) -> list[str]:
    """Resolved album.cfg `order` list (curated photo order) as rel_paths,
    [] when the album doesn't configure one."""
    items = cfg.get("order", [])
    return _resolve_photo_refs(album, items) if items else []


def _apply_curated_order(images: list[dict], curated_order: list[str]) -> list[dict]:
    """Stable-sort image dicts into the curated order: listed photos first
    in the given order, unlisted ones keep their previous (date) order."""
    pos = {rel: i for i, rel in enumerate(curated_order)}
    images.sort(key=lambda r: pos.get(r["rel_path"], len(pos)))
    return images


def _random_subtree_rows(album: str, limit: int = 8) -> list[dict]:
    """Random photos from an album's whole subtree, for album.cfg
    `reel = random`."""
    prefix = album + "/"
    rows = db.conn().execute(
        "SELECT * FROM images WHERE (album = ? OR substr(album, 1, ?) = ?) "
        "ORDER BY RANDOM() LIMIT ?",
        (album, len(prefix), prefix, limit),
    ).fetchall()
    return [dict(r) for r in rows]


CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "upgrade-insecure-requests"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # HTML renders in the language picked via the lang cookie (with an
    # Accept-Language fallback). Vary documents that for well-behaved
    # caches, but browsers do NOT reliably key their HTTP cache on
    # Vary: Cookie — after a language switch the redirect target came back
    # from the disk cache in the previous language. HTML here is tiny and
    # fully dynamic, so opt it out of caching entirely (no-store also keeps
    # Chrome/Firefox from bfcache-restoring stale-language pages); images,
    # CSS and JS keep their own long-lived cache headers.
    if response.headers.get("content-type", "").startswith("text/html"):
        extra = "Cookie, Accept-Language"
        vary = response.headers.get("vary")
        response.headers["Vary"] = f"{vary}, {extra}" if vary else extra
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "interest-cohort=(), browsing-topics=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # API clients get JSON (with the CORS headers they need to read it),
    # never the HTML 404 page
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        resp = _json_cors({"error": str(exc.detail), "status": exc.status_code}, max_age=0)
        resp.status_code = exc.status_code
        return resp
    if exc.status_code == 404:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "path": request.url.path},
            status_code=404,
        )
    return Response(content=str(exc.detail), status_code=exc.status_code)


# ----- indexer control --------------------------------------------------
# The gallery takes no orders over HTTP — there is no endpoint that makes the
# server do work, by design. Operations (pause, resume, "scan now") arrive as
# flag files in DATA_DIR/control, written by the CLI and picked up by
# _control_loop; the same loop publishes status.json, which is the only window
# the CLI has into this process. The channel itself lives in app/control.py.
_HEARTBEAT_INTERVAL = 15.0

_scan_state: dict = {
    "scanning": False,
    "started_at": None,
    "trigger": None,
    "last_scan": None,
}
_scan_state_lock = threading.Lock()


def _publish_status() -> None:
    """Snapshot this process for `python -m app.cli status`. Cheap enough to
    call on every scan edge plus a slow heartbeat."""
    with _scan_state_lock:
        state = dict(_scan_state)
    pause = control.pause_info()
    control.publish_status({
        "started_at": _STARTED_AT,
        "paused": pause is not None,
        "pause": pause,
        "scanning": state["scanning"],
        "scan_started_at": state["started_at"],
        "scan_trigger": state["trigger"],
        "last_scan": state["last_scan"],
        "pending_request": control.pending_scan_request(),
        "watcher": {
            "enabled": ENABLE_WATCHER,
            "running": watcher.is_running(),
            "pending": watcher.pending_count(),
        },
        "config": {
            "photos_dir": str(PHOTOS_DIR),
            "thumbs_dir": str(THUMBS_DIR),
            "previews_dir": str(PREVIEWS_DIR),
            "data_dir": str(DATA_DIR),
            "thumb_size": THUMB_SIZE,
            "preview_size": PREVIEW_SIZE,
            "scan_interval": SCAN_INTERVAL,
            "hide_gps": HIDE_GPS,
            "strip_gps": STRIP_GPS,
        },
    })


def _run_scan(trigger: str = "periodic", album: str | None = None,
              force: bool = False, request_id: str | None = None) -> dict | None:
    """One indexing pass, then a featured recompute. Returns the run summary,
    or None when a scan was already in flight — the lock is never waited on,
    two overlapping scans would only fight over the same rows."""
    if not _scan_lock.acquire(blocking=False):
        log.info("scan (%s) skipped: a scan is already running", trigger)
        return None
    started = time.time()
    error = None
    result = None
    with _scan_state_lock:
        _scan_state.update(scanning=True, started_at=started, trigger=trigger)
    _publish_status()
    try:
        try:
            result = scanner.full_scan(
                PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE,
                previews_dir=PREVIEWS_DIR, preview_size=PREVIEW_SIZE,
                root=album, force=force,
            )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.exception("scan failed: %s", e)
        # Re-derive featured flags from album.cfg.
        # Runs even when the walk blew up: the index is then partial, but
        # leaving is_showcase stale on top of it hides featured photos too.
        _recompute_featured()
        if result and any(result[k] for k in ("indexed", "thumbnails", "previews", "removed", "failed")):
            log.info("scan: %s", result)
        if result and result["failed"]:
            log.warning("scan: %d file(s) unreadable — see the 'thumb failed' / "
                        "'skipped' warnings above; they stay in the gallery "
                        "without a thumbnail until fixed or removed",
                        result["failed"])
    except Exception as e:
        error = error or f"{type(e).__name__}: {e}"
        log.exception("scan bookkeeping failed: %s", e)
    finally:
        finished = time.time()
        summary = {
            "trigger": trigger,
            "request_id": request_id,
            "album": album,
            "force": force,
            "started_at": started,
            "finished_at": finished,
            "seconds": round(finished - started, 3),
            "error": error,
            "result": result,
        }
        with _scan_state_lock:
            _scan_state.update(scanning=False, started_at=None, trigger=None,
                               last_scan=summary)
        _scan_lock.release()
        _publish_status()
    return summary


def _control_loop():
    """Heartbeat, control channel and periodic rescan in one thread.

    Ticks every control.CONTROL_TICK seconds, so a manual scan starts within
    ~2s instead of after a whole SCAN_INTERVAL. Runs even with
    SCAN_INTERVAL=0: the periodic pass is off then, but pause/resume, manual
    scans and the status heartbeat still work."""
    last_periodic = time.monotonic()
    last_beat = 0.0
    while True:
        time.sleep(control.CONTROL_TICK)
        try:
            req = control.take_scan_request()
            if req is not None:
                # A requested scan ignores the pause on purpose: it was asked
                # for explicitly, and it is how you index a one-off change
                # without lifting a maintenance pause.
                _run_scan(trigger="manual", album=req.get("album"),
                          force=bool(req.get("force")), request_id=req.get("id"))
                last_periodic = last_beat = time.monotonic()
                continue
            now = time.monotonic()
            if (SCAN_INTERVAL > 0 and not control.is_paused()
                    and now - last_periodic >= SCAN_INTERVAL):
                _run_scan(trigger="periodic")
                last_periodic = last_beat = time.monotonic()
                continue
            if now - last_beat >= _HEARTBEAT_INTERVAL:
                last_beat = now
                _publish_status()
        except Exception as e:
            log.warning("control tick failed: %s: %s", type(e).__name__, e)


@app.on_event("startup")
def _startup():
    db.init(DATA_DIR)
    _recompute_featured()
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds hide_gps=%s strip_gps=%s",
        PHOTOS_DIR, THUMBS_DIR, DATA_DIR, THUMB_SIZE, ENABLE_WATCHER, SCAN_INTERVAL, HIDE_GPS, STRIP_GPS,
    )
    if control.is_paused():
        info = control.pause_info() or {}
        # A pause is deliberately persistent: it survives the restart it was
        # very likely set for. No startup scan, no periodic scan; the watcher
        # still starts, but only queues events (see watcher._drain).
        log.warning("indexer PAUSED (%s) — resume with `python -m app.cli resume`",
                    info.get("reason") or "no reason given")
    else:
        threading.Thread(target=_run_scan, kwargs={"trigger": "startup"}, daemon=True).start()
    if ENABLE_WATCHER:
        try:
            watcher.start(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE,
                          previews_dir=PREVIEWS_DIR, preview_size=PREVIEW_SIZE,
                          fulls_dir=FULLS_DIR, on_config=_recompute_featured)
        except Exception as e:
            log.warning("watcher failed to start: %s", e)
    threading.Thread(target=_control_loop, daemon=True).start()
    if SCAN_INTERVAL > 0:
        log.info("periodic rescan every %d seconds", SCAN_INTERVAL)
    _publish_status()


@app.on_event("shutdown")
def _shutdown():
    # Drop the snapshot so the CLI reports "not running" right away instead
    # of waiting for the heartbeat to age out.
    control.clear_status()


def _safe_rel(album: str, filename: str) -> Path:
    """Validate an album/filename pair for the photo-serving routes (image,
    thumb, preview, full). These serve straight off disk without consulting
    the index, so the `.album/` metadata folder is refused here too — its
    contents are not photos, and the one file in it that is meant to be
    public (the `font = …` face) has its own route."""
    rel = (Path(album) / filename)
    if ".." in rel.parts or rel.is_absolute():
        raise HTTPException(400, "invalid path")
    if scanner.is_meta_path(rel):
        raise HTTPException(404, "not found")
    full = (PHOTOS_DIR / rel).resolve()
    try:
        full.relative_to(PHOTOS_DIR)
    except ValueError:
        raise HTTPException(400, "invalid path")
    return rel


# ----- gallery-wide config (gallery.cfg) ---------------------------------
# Optional `gallery.cfg` dropped into the photos ROOT (next to the album
# folders). Same format as album.cfg (see _parse_cfg: `key = value`, list
# values comma-separated / repeated keys / one entry per line). Known keys:
#   welcome = showcase            -> hero feed = random featured photos
#                                    (default; same as no file / no key)
#   welcome = random              -> hero feed = random photos, ignore featured
#   welcome = <album/file.jpg>,…  -> hand-picked hero feed in exactly this
#                                    order (paths are relative to photos/).
#                                    Unresolvable entries are skipped with a
#                                    warning; if nothing resolves, falls back
#                                    to showcase.
#   welcome_desktop / welcome_mobile -> same syntax as `welcome`, but only
#                                    for the respective device class (phones
#                                    are detected via User-Agent). `welcome`
#                                    stays the shared fallback.
#   album_order = <album>,…       -> curated album order: adds a "Curated"
#                                    entry to the /albums sort menu and fixes
#                                    the order of the ★ featured-album rails.
#                                    A bare `#label` line inside the list
#                                    (# glued to the label) opens a framed
#                                    "label" group in the Curated view; every
#                                    other sort/page keeps the flat order.
#   album_sort = curated|latest_desc|… -> preselect the /albums sort option.
#   wallpaper_tint = off          -> how much colour the SITE backdrop keeps
#                                    (off | 0–1). This is the same knob an
#                                    album.cfg has, one tier up: it moves the
#                                    default for every page, and an album that
#                                    sets its own still wins. See the per-album
#                                    theme section above.
#   wallpaper_dim = .9            -> how bright that backdrop is (off | 0.25–1).
GALLERY_CFG_NAME = "gallery.cfg"
GALLERY_GROUP_KEYS = frozenset({"album_order"})
WELCOME_FEED_MAX = 24

_WELCOME_KEYWORDS = {
    "showcase": "showcase", "auto": "showcase", "featured": "showcase",
    "random": "random", "shuffle": "random",
}
_warned_welcome: set[str] = set()


def _gallery_config() -> dict[str, list[str]]:
    """Parse photos/gallery.cfg (see _parse_cfg), or {} when there's no such
    file. Cheap enough to read per request, so edits show up without a
    restart (matching album.cfg behaviour)."""
    cfg_path = PHOTOS_DIR / GALLERY_CFG_NAME
    if not cfg_path.is_file():
        return {}
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_cfg(text, group_keys=GALLERY_GROUP_KEYS)


def _is_mobile_request(request: Request) -> bool:
    """Phone detection for the welcome_mobile/_desktop split. MDN's
    recommended heuristic: 'Mobi' anywhere in the User-Agent — catches
    iPhones and Android phones; Android tablets (no 'Mobi') and iPads in
    desktop mode deliberately get the desktop feed."""
    return "mobi" in request.headers.get("user-agent", "").lower()


def _lookup_welcome_image(raw: str):
    """Resolve one gallery.cfg welcome entry to an indexed image row.
    Backslashes are tolerated."""
    rel = raw.replace("\\", "/").strip().strip("/")
    if not rel or "/" not in rel:
        return None
    c = db.conn()
    return c.execute(
        "SELECT album, filename, rel_path FROM images WHERE rel_path = ?", (rel,)
    ).fetchone()


def _welcome_feed(mobile: bool = False) -> tuple[list[dict], str, str]:
    """Hero feed for the welcome screen honoring gallery.cfg. The device
    keys (welcome_mobile / welcome_desktop) win over the shared `welcome`
    key for their device class; each accepts the same syntax.
    Returns (feed, label, mode) with mode one of manual/showcase/random."""
    cfg = _gallery_config()
    spec = cfg.get("welcome_mobile" if mobile else "welcome_desktop") or cfg.get("welcome", [])
    mode = "showcase"
    if len(spec) == 1 and spec[0].lower() in _WELCOME_KEYWORDS:
        mode = _WELCOME_KEYWORDS[spec[0].lower()]
    elif spec:
        feed: list[dict] = []
        seen: set[str] = set()
        for raw in spec[:WELCOME_FEED_MAX]:
            row = _lookup_welcome_image(raw)
            if row is None:
                if raw not in _warned_welcome:
                    _warned_welcome.add(raw)
                    log.warning("gallery.cfg: welcome image not indexed, skipping: %r", raw)
                continue
            if row["rel_path"] in seen:
                continue
            seen.add(row["rel_path"])
            feed.append({"album": row["album"], "filename": row["filename"],
                         "rel_path": row["rel_path"]})
        if feed:
            return feed, "CURATED", "manual"
        # nothing resolved -> behave as if the key were absent
    if mode != "random":
        showcase_feed = _showcase_rows(limit=12, random_order=True)
        if showcase_feed:
            feed = [
                {"album": r["album"], "filename": r["filename"], "rel_path": r["rel_path"]}
                for r in showcase_feed
            ]
            return feed, "FEATURED", "showcase"
    feed = [
        dict(r)
        for r in db.conn().execute(
            "SELECT album, filename, rel_path FROM images ORDER BY RANDOM() LIMIT 8"
        ).fetchall()
    ]
    return feed, "RANDOM", "random"


@app.get("/lang/{code}")
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


def _archive_updated_at() -> str | None:
    """ISO timestamp of the most recent photo file in the archive, for the
    readout band's LAST UPDATE cell.

    Deliberately MAX(mtime) — the file's own date on disk — and not
    MAX(indexed_at): indexed_at is stamped when the scanner inserts a row, so
    a from-scratch DB rebuild would reset every photo to "today" and the
    archive would claim it was updated when in fact nothing new arrived.
    mtime survives rebuilds and still moves when photos are dropped in."""
    row = db.conn().execute("SELECT MAX(mtime) AS m FROM images").fetchone()
    m = row["m"] if row else None
    if not m:
        return None
    try:
        return datetime.fromtimestamp(float(m)).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


@app.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    c = db.conn()
    feed, feed_label, feed_mode = _welcome_feed(mobile=_is_mobile_request(request))
    counts = c.execute("SELECT COUNT(*) AS images FROM images").fetchone()
    # "Albums" = top-level folders (parents of nested albums count once).
    top_level_albums = len(_child_album_names(None))
    showcase_count = c.execute(
        "SELECT COUNT(*) AS n FROM images WHERE is_showcase = 1"
    ).fetchone()
    showcase_albums = _showcase_album_rows(limit=6)
    return templates.TemplateResponse(
        "welcome.html",
        {
            "request": request,
            "shuffle": feed,
            "feed_label": feed_label,
            "feed_mode": feed_mode,
            "image_count": counts["images"] if counts else 0,
            "album_count": top_level_albums,
            "showcase_count": showcase_count["n"] if showcase_count else 0,
            "showcase_albums": showcase_albums,
            "updated_at": _archive_updated_at(),
        },
        # the hero feed can differ per device class (welcome_mobile/_desktop),
        # so shared caches must key on the UA
        headers={"Vary": "User-Agent"},
    )


@app.get("/albums", response_class=HTMLResponse)
def albums_index(request: Request, sort: str | None = None):
    # "Curated" only exists as a sort option while gallery.cfg defines an
    # album_order; gallery.cfg `album_sort` presets the default sort.
    has_curated = bool(_curated_album_positions())
    allowed = set(SORT_ALBUM_SQL) | ({SORT_CURATED} if has_curated else set())
    default_sort = _pick_sort(_cfg_first(_gallery_config(), "album_sort"), allowed, SORT_ALBUM_DEFAULT)
    current_sort = _pick_sort(sort, allowed, default_sort)
    albums = _sorted_album_cards(_top_level_album_cards(), current_sort)
    # annotate here so the template only has to read the flag
    for a in albums:
        a["is_showcase"] = _album_is_showcase(a["album"])
    showcase_albums = [a for a in albums if a["is_showcase"]]
    archive_albums = [a for a in albums if not a["is_showcase"]]
    # `#group` markers in album_order frame the Curated view into labeled
    # sections; every other sort keeps the flat archive grid
    album_sections = (_curated_album_sections(archive_albums)
                      if current_sort == SORT_CURATED else [])
    sort_options = _album_sort_options_for_template(current_sort, curated=has_curated,
                                                    lang=_request_lang(request))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "albums": albums,
            "showcase_albums": showcase_albums,
            "archive_albums": archive_albums,
            "album_sections": album_sections,
            "current_sort": current_sort,
            "default_sort": default_sort,
            "sort_options": sort_options,
            "sort_label": _active_sort_label(sort_options),
        },
    )


# ----- JSON API ---------------------------------------------------------
# A read-only JSON view of everything the pages render, CORS-open so the
# gallery can be embedded elsewhere. Three rules hold across all of it:
#
#   * an album named in `album=` / the path resolves exactly like its page
#     does — an album.cfg `collection = true` album answers with its WHOLE
#     subtree (see _photo_scope), and says so via `scope.collection`.
#     `subtree=0|1` overrides that per request.
#   * photos always come back through _serialize_photo and albums through
#     _serialize_album, so the shapes are identical everywhere.
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
API_VARY = "Accept-Language, Cookie"  # for the language-dependent payloads


def _api_lang(request: Request, lang: str | None = None) -> str:
    """An explicit `lang=` wins over the visitor's cookie/Accept-Language, so
    an embedder can pin the language it wants without setting cookies."""
    code = (lang or "").strip().lower()
    return code if code in i18n.LANGS else _request_lang(request)


def _api_limit(limit: int, default_max: int = API_MAX_LIMIT) -> int:
    return max(1, min(default_max, limit))


def _all_album_nodes() -> list[str]:
    """Every album node including the intermediate folders that hold only
    sub-folders — `images.album` alone lists just the ones with photos."""
    nodes: set[str] = set()
    for a in _distinct_albums():
        parts = a.split("/")
        for i in range(1, len(parts) + 1):
            nodes.add("/".join(parts[:i]))
    return sorted(nodes)


def _album_exists(album: str) -> bool:
    prefix = album + "/"
    return db.conn().execute(
        "SELECT 1 FROM images WHERE album = ? OR substr(album, 1, ?) = ? LIMIT 1",
        (album, len(prefix), prefix),
    ).fetchone() is not None


def _resolve_album_path(album: str) -> str | None:
    """Map an album path off the URL to a real indexed album, tolerating
    different casing on any segment. None when nothing matches."""
    album = album.strip("/").replace("\\", "/")
    if not album or ".." in album.split("/"):
        return None
    if _album_exists(album):
        return album
    key = _album_order_key(album)
    return next((n for n in _all_album_nodes() if _album_order_key(n) == key), None)


def _photo_rows(*, album: str | None = None, subtree: bool | None = None,
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
        _simple, where_join, scope_params, collection, wide = _photo_scope(album, subtree)
        where.append(where_join)
        params += list(scope_params)
        scope.update(collection=collection, subtree=wide)
    if featured:
        where.append("i.is_showcase = 1")
    if tag:
        where.append("EXISTS (SELECT 1 FROM image_tags it JOIN tags t ON t.id = it.tag_id "
                     "WHERE it.image_id = i.id AND t.name = ?)")
        params.append(tag)
    if q:
        like = f"%{q}%"
        where.append("(i.album LIKE ? OR i.filename LIKE ? OR EXISTS ("
                     "SELECT 1 FROM image_tags it2 JOIN tags t2 ON t2.id = it2.tag_id "
                     "WHERE it2.image_id = i.id AND t2.name LIKE ?))")
        params += [like, like, like]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    c = db.conn()
    total = c.execute(f"SELECT COUNT(*) AS n FROM images i {clause}", params).fetchone()["n"]
    order = "RANDOM()" if random_order else _qualify_sort(
        order_sql or SORT_IMAGE_SQL[SORT_IMAGE_DEFAULT])
    rows = c.execute(
        f"SELECT i.* FROM images i {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [-1 if limit is None else limit, max(0, offset)],
    ).fetchall()
    return [dict(r) for r in rows], total, scope


def _serialize_photos(rows: list[dict], base: str, with_tags: bool = False) -> list[dict]:
    tag_map = _tags_for_images([r["id"] for r in rows if r.get("id")]) if with_tags else {}
    return [_serialize_photo(r, base, tag_map.get(r.get("id"), []) if with_tags else None)
            for r in rows]


def _serialize_album(card: dict, base: str) -> dict:
    """One album card as the API returns it — the same numbers the /albums
    grid shows (recursive photo count, latest activity, cover from anywhere
    in the subtree) plus the cfg flags a client needs to render it: whether
    it is a showcase album and whether it is a collection."""
    album = card["album"]
    cfg = _album_config(album)
    cover = card.get("cover")
    icon = card.get("icon") or _album_icon_url(album)
    return {
        "album": album,
        "name": card["name"],
        "count": card["count"],
        "latest": card["latest"],
        "sub_count": card["sub_count"],
        "is_showcase": _album_is_showcase(album, cfg),
        "collection": _album_collection(album, cfg),
        "tags": _album_tags(album, cfg),
        "cover": {
            "rel_path": cover,
            "urls": {
                "thumb": f"/thumb/{cover}",
                "preview": f"/preview/{cover}",
                "thumb_abs": f"{base}/thumb/{cover}",
                "preview_abs": f"{base}/preview/{cover}",
            },
        } if cover else None,
        # album.cfg `icon = …` — the album's own mark, null when it sets none
        "icon": {"url": icon, "url_abs": f"{base}{icon}"} if icon else None,
        "urls": {
            "page": f"/album/{album}",
            "api": f"/api/album/{album}",
            "page_abs": f"{base}/album/{album}",
            "api_abs": f"{base}/api/album/{album}",
        },
    }


def _album_reel(album: str, cfg: dict[str, list[str]], limit: int = 8) -> tuple[str, list[dict]]:
    """The album's hero slideshow (album.cfg `reel`), as (mode, rows).

    `featured` (the default) shows featured photos from this album AND its
    sub-albums, so a photo featured inside e.g. japan_2026/kansai surfaces on
    the japan_2026 page too — a showcase ALBUM doesn't auto-promote its
    contents, each photo opts in via album.cfg `featured`. The album's own
    `featured` list sets the order, exactly as written; anything featured by
    sub-album cfgs follows newest-first. `random` fills it from the subtree instead, `off` empties
    it. Shared by the album page and /api/album."""
    mode = (_cfg_first(cfg, "reel") or "").strip().lower()
    if mode in _FALSE:
        return "off", []
    if mode in ("random", "shuffle"):
        return "random", _random_subtree_rows(album, limit=limit)
    # fetch wide before trimming so a date-based LIMIT can't cut off early
    # entries of the configured list
    rows = _showcase_rows(album=album, limit=100, random_order=False, subtree=True)
    order_items = [i for i in cfg.get("featured", []) if i.strip().lower() not in ("*", "all")]
    if order_items:
        rows = _apply_curated_order(rows, _resolve_photo_refs(album, order_items))
    return "featured", rows[:limit]


@app.get("/api")
def api_index(request: Request):
    """What this API offers, so a client can discover it without the README."""
    base = _public_base_url(request)
    image_sorts = [SORT_CURATED, SORT_DAYS] + list(SORT_IMAGE_SQL)
    album_sorts = [SORT_CURATED] + list(SORT_ALBUM_SQL)
    return _json_cors({
        "name": app.title,
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
                "sort": "|".join(SORT_IMAGE_SQL), "random": "1 = random order",
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


@app.get("/api/stats")
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
    return _json_cors({
        "images": row["images"],
        "featured": row["featured"],
        "albums": {
            "top_level": len(_child_album_names(None)),
            "total": len(_all_album_nodes()),
            "showcase": len(_showcase_album_rows()),
        },
        "tags": tags,
        "bytes": row["bytes"],
        "bytes_h": _humanize_bytes(row["bytes"]),
        "span": {
            "from": row["first"],
            "to": row["last"],
            "label": i18n.date_span(code, row["first"], row["last"]) or None,
        },
        "lang": code,
    }, vary=API_VARY)


@app.get("/api/albums")
def api_albums(request: Request, parent: str | None = None, sort: str | None = None,
               depth: int = 1, showcase: bool | None = None, limit: int = API_MAX_LIMIT):
    """Album cards: the top level by default, the children of `parent`
    otherwise. `depth > 1` nests each card's own children under `children`,
    so one request can pull a whole branch of the tree."""
    base = _public_base_url(request)
    root: str | None = None
    if parent:
        root = _resolve_album_path(parent)
        if root is None:
            raise HTTPException(404, "album not found")
    has_curated = bool(_curated_album_positions())
    allowed = set(SORT_ALBUM_SQL) | ({SORT_CURATED} if has_curated else set())
    default_sort = _pick_sort(_cfg_first(_gallery_config(), "album_sort"), allowed, SORT_ALBUM_DEFAULT)
    current_sort = _pick_sort(sort, allowed, default_sort)
    depth = max(1, min(4, depth))
    limit = _api_limit(limit)
    all_albums = _distinct_albums()

    def branch(node: str | None, level: int) -> list[dict]:
        cards = [_album_card(n, all_albums) for n in _child_album_names(node, all_albums)]
        cards = _sorted_album_cards(cards, current_sort)
        out = []
        for card in cards:
            item = _serialize_album(card, base)
            # ?showcase= splits the ★ rail from the archive grid, like /albums
            if showcase is not None and level == 1 and item["is_showcase"] != bool(showcase):
                continue
            if level < depth and card["sub_count"]:
                item["children"] = branch(card["album"], level + 1)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    albums = branch(root, 1)
    payload = {
        "count": len(albums),
        "parent": root,
        "sort": current_sort,
        "default_sort": default_sort,
        "depth": depth,
        "albums": albums,
    }
    # `#group` markers in gallery.cfg album_order frame the curated top-level
    # view into labeled sections; mirrored here so a client can rebuild it
    if root is None and current_sort == SORT_CURATED:
        sections = _curated_album_sections(
            _sorted_album_cards(_top_level_album_cards(all_albums), SORT_CURATED))
        payload["sections"] = [
            {"label": s["label"], "albums": [_serialize_album(c, base) for c in s["cards"]]}
            for s in sections
        ]
    return _json_cors(payload)


@app.get("/api/album/{album:path}")
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
    resolved = _resolve_album_path(album)
    if resolved is None:
        raise HTTPException(404, "album not found")
    album = resolved
    _refresh_featured_on_cfg_change(album)
    cfg = _album_config(album)
    code = _api_lang(request, lang)
    base = _public_base_url(request)

    where_simple, where_join, scope_params, collection, wide = _photo_scope(album, subtree)
    c = db.conn()
    curated_order = _curated_photo_order(album, cfg)
    day_count = _scope_day_count(where_simple, scope_params)
    current_sort, default_sort, base_sort = _resolve_image_sort(
        cfg, sort, curated_order, days=day_count > 1)
    reel_mode, reel_rows = _album_reel(album, cfg)
    sub_albums = _sorted_album_cards([_album_card(n) for n in _child_album_names(album)], "name_asc")
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
    font_css = _album_font_css_url(album)
    theme_css = _theme_css_url(album)
    effect = (_cfg_first(cfg, "effect") or "").strip().lower()

    payload = {
        "album": _serialize_album(_album_card(album), base),
        "breadcrumbs": _album_breadcrumbs(album),
        "scope": {"album": album, "collection": collection, "subtree": wide},
        "description": {"html": _album_description(album, code), "lang": code},
        "stats": _album_stats(stat_src, cfg, code),
        "effect": effect if effect in ALBUM_EFFECTS else None,
        "font": {"css": font_css, "scale": _album_font_scale(album),
                 "preload": _album_font_preload(album)} if font_css else None,
        "theme": {"css": theme_css, "accent": (_album_accent(album) or {}).get("acc"),
                  "wallpaper": _wallpaper_decls(album) or None} if theme_css else None,
        "trip": _trip_for_album(album, code),
        "reel": {"mode": reel_mode, "items": _serialize_photos(reel_rows, base)},
        "sub_albums": [_serialize_album(s, base) for s in sub_albums],
        "photo_tags": photo_tags,
        "sort": {
            "current": current_sort,
            "default": default_sort,
            "options": _image_sort_options_for_template(current_sort, bool(curated_order), code,
                                                        days=day_count > 1),
        },
        "lang": code,
    }
    if images:
        limit = _api_limit(limit)
        rows, total, _scope = _photo_rows(
            album=album, subtree=subtree, tag=tag,
            order_sql=SORT_IMAGE_SQL[base_sort],
            limit=None if current_sort == SORT_CURATED else limit,
            offset=0 if current_sort == SORT_CURATED else offset,
        )
        if current_sort == SORT_CURATED:
            # curated order is a cfg list, not SQL — reorder the full set, then page
            rows = _apply_curated_order(rows, curated_order)[max(0, offset):max(0, offset) + limit]
        payload["images"] = {
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": max(0, offset),
            "tag": tag,
            "items": _serialize_photos(rows, base, with_tags=tags),
        }
    else:
        payload["images"] = {"total": _photo_rows(album=album, subtree=subtree, tag=tag, limit=0)[1]}
    return _json_cors(payload, vary=API_VARY)


@app.get("/api/photos")
def api_photos(request: Request, album: str | None = None, subtree: bool | None = None,
               tag: str | None = None, q: str | None = None, featured: bool = False,
               sort: str | None = None, random: bool = False, tags: bool = False,
               limit: int = 50, offset: int = 0):
    """Photos across the gallery or inside one album, with the filters the UI
    offers (tag, search, featured) and offset paging. An `album` that is a
    collection is scoped to its whole subtree — `scope` in the response says
    so, and `subtree=0` forces the plain folder scope."""
    if album:
        resolved = _resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    current_sort = _pick_sort(sort, SORT_IMAGE_SQL, SORT_IMAGE_DEFAULT)
    limit = _api_limit(limit)
    rows, total, scope = _photo_rows(
        album=album, subtree=subtree, tag=tag, q=(q or "").strip() or None,
        featured=featured, order_sql=SORT_IMAGE_SQL[current_sort],
        random_order=bool(random), limit=limit, offset=offset,
    )
    base = _public_base_url(request)
    return _json_cors({
        "count": len(rows),
        "total": total,
        "limit": limit,
        "offset": max(0, offset),
        "sort": "random" if random else current_sort,
        "scope": scope,
        "filters": {"tag": tag, "q": (q or "").strip() or None, "featured": bool(featured)},
        "items": _serialize_photos(rows, base, with_tags=tags),
    })


@app.get("/api/photo/{rel_path:path}")
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
    rel = _safe_rel(album, filename).as_posix()
    c = db.conn()
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    row = dict(row)
    code = _api_lang(request, lang)
    base = _public_base_url(request)
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    if HIDE_GPS:
        exif.pop("GPSInfo", None)
    photo_tags = _tags_for_images([row["id"]]).get(row["id"], [])
    item = _serialize_photo(row, base, photo_tags)
    item.update({
        "breadcrumbs": _album_breadcrumbs(row["album"]),
        "description": _extract_description(exif),
        "exif": [{"key": k, "val": v} for k, v in _prettify_exif(exif, code)],
        # already plain JSON types — the column stores it as JSON (scanner.py)
        "exif_raw": exif,
        "album_url": {"page": f"/album/{row['album']}", "api": f"/api/album/{row['album']}"},
        "lang": code,
    })

    col_root = (col or "").strip("/")
    scope_album = row["album"]
    if (col_root and (scope_album == col_root or scope_album.startswith(col_root + "/"))
            and _album_collection(col_root)):
        scope_album = col_root
    else:
        col_root = ""
    if neighbours:
        cfg = _album_config(scope_album)
        curated_order = _curated_photo_order(scope_album, cfg)
        where_simple, _wj, sp, _coll, _wide = _photo_scope(
            scope_album, True if col_root else None)
        current_sort, default_sort, base_sort = _resolve_image_sort(
            cfg, sort, curated_order, days=_scope_day_count(where_simple, sp) > 1)
        # the neighbour walk has to mirror the grid exactly, so it uses the
        # same scope (collection root or own folder) and the same order
        rows, _total, _scope = _photo_rows(
            album=scope_album, subtree=True if col_root else None,
            order_sql=SORT_IMAGE_SQL[base_sort], limit=None,
        )
        rel_list = [r["rel_path"] for r in rows]
        if current_sort == SORT_CURATED:
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
    return _json_cors(item, vary=API_VARY)


@app.get("/api/tags")
def api_tags(request: Request, album: str | None = None, subtree: bool | None = None,
             limit: int = API_MAX_LIMIT):
    """Photo tags (the `.tags` sidecar ones that drive the ?tag= filter) with
    how many photos carry each, most-used first. Scoped to an album — again
    collection-aware — when `album` is given. An album's own display tags are
    a different thing and live on /api/album."""
    where, params = "", []
    scope = {"album": None, "collection": False, "subtree": False}
    if album:
        resolved = _resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        _simple, where_join, scope_params, collection, wide = _photo_scope(resolved, subtree)
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
    return _json_cors({
        "count": len(rows),
        "scope": scope,
        "items": [{"name": r["name"], "count": r["count"]} for r in rows],
    })


@app.get("/api/showcase")
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
        resolved = _resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    limit = _api_limit(limit)
    rows, total, scope = _photo_rows(
        album=album, subtree=subtree, featured=True,
        random_order=bool(random), limit=limit,
    )
    base = _public_base_url(request)
    items = _serialize_photos(rows, base, with_tags=tags)
    return _json_cors(
        {
            "count": len(items),
            "total": total,
            "scope": scope,
            "items": items,
        }
    )


@app.get("/api/shuffle")
def api_shuffle(request: Request, limit: int = 8, album: str | None = None,
                subtree: bool | None = None):
    """Random photos. Returns a bare array — the welcome hero's ⟳ TUNE button
    reads it directly (see app.js), so the shape stays as it is."""
    limit = _api_limit(limit, 24)
    if album:
        resolved = _resolve_album_path(album)
        if resolved is None:
            raise HTTPException(404, "album not found")
        album = resolved
    rows, _total, _scope = _photo_rows(album=album, subtree=subtree,
                                       random_order=True, limit=limit)
    base = _public_base_url(request)
    # no-store: a cached "random" is not random (the ⟳ TUNE button re-asks)
    return _json_cors(_serialize_photos(rows, base), max_age=0)


@app.options("/api/{rest:path}")
def api_options(rest: str):
    # CORS pre-flight (most simple GETs don't trigger this, but be polite)
    return _json_cors({})


@app.get("/album/{album:path}", response_class=HTMLResponse)
def album_view(request: Request, album: str, tag: str | None = None, sort: str | None = None):
    album = album.strip("/")
    if not album:
        raise HTTPException(404, "album not found")
    # a just-saved album.cfg must be visible on this very reload (reel mode,
    # featured set, grid stars) without waiting for the watcher's debounce
    _refresh_featured_on_cfg_change(album)
    album_cfg = _album_config(album)
    c = db.conn()
    # Collection mode (album.cfg `collection = true`): the grid shows every
    # photo in this album's whole subtree (its own + all sub-folders) as one
    # flat set, instead of only the photos sitting directly in this folder.
    # /api/album + /api/photos resolve the same scope through _photo_scope.
    where_simple, where_join, scope_params, collection, _wide = _photo_scope(album)
    # album.cfg `order` adds a "Curated" sort option, photos spanning more
    # than one day add "By day"; `sort` presets the default sort for this
    # album (query param still wins).
    curated_order = _curated_photo_order(album, album_cfg)
    day_count = _scope_day_count(where_simple, scope_params)
    current_sort, default_sort, base_sort = _resolve_image_sort(
        album_cfg, sort, curated_order, days=day_count > 1)
    # qualify column names so the JOIN query below isn't ambiguous
    qualified_sql = _qualify_sort(SORT_IMAGE_SQL[base_sort])
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
        order_sql = SORT_IMAGE_SQL[base_sort]
        rows = c.execute(
            f"SELECT * FROM images WHERE {where_simple} ORDER BY {order_sql}",
            scope_params,
        ).fetchall()
    images = [dict(r) for r in rows]
    if current_sort == SORT_CURATED:
        images = _apply_curated_order(images, curated_order)
    # Immediate sub-folders of this album, shown as folder cards above the
    # image grid. Listed alphabetically so the folder view is predictable.
    sub_albums = _sorted_album_cards(
        [_album_card(n) for n in _child_album_names(album)], "name_asc"
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
    album_is_showcase = _album_is_showcase(album)
    # Hero reel (album.cfg `reel`, like the welcome feed) — see _album_reel,
    # which /api/album serves from as well.
    reel_mode, featured = _album_reel(album, album_cfg)
    lang = _request_lang(request)
    sort_options = _image_sort_options_for_template(current_sort, curated=bool(curated_order),
                                                    lang=lang, days=day_count > 1)
    trip = _trip_for_album(album, lang)
    # "By day": the grid renders as one framed section per capture day
    # instead of a single flat grid (album.html falls back to the flat grid
    # whenever this is None).
    day_sections = (_day_sections(images, trip or _ancestor_trip(album, lang), lang)
                    if current_sort == SORT_DAYS else None)
    effect = (_cfg_first(album_cfg, "effect") or "").strip().lower()
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
    album_stats = _album_stats(stat_src, album_cfg, lang)
    return templates.TemplateResponse(
        "album.html",
        {
            "request": request,
            "album": album,
            # base.html paints the backdrop from this; a sub-album with no
            # wallpaper of its own inherits its nearest ancestor's
            "bg_album": album,
            "breadcrumbs": _album_breadcrumbs(album),
            "album_description": _album_description(album, lang),
            # cover photo for the mobile hero header (see .album-hero)
            "album_cover": _album_cover_rel(album),
            # the album's own mark (album.cfg `icon = ...`), shown next to
            # the hero title; None when it configures none
            "album_icon": _album_icon_url(album),
            # ambient page effect (album.cfg `effect = ...`, whitelisted)
            "album_effect": effect if effect in ALBUM_EFFECTS else None,
            # album.cfg `tags = ...`, shown under the hero title. NOT the
            # per-image `tags` below, which drive the ?tag= grid filter.
            "album_tags": _album_tags(album),
            # stats block under the description (auto EXIF/size readouts +
            # editorial `loc`/`stat` from album.cfg); see _album_stats
            "album_stats": album_stats,
            # generated stylesheet for the album's own title face
            # (album.cfg `font = ...`); None when it configures none
            "album_font_css": _album_font_css_url(album),
            # preload for that same face, so it downloads alongside the sheet
            # instead of after it (no fallback→face swap on load)
            "album_font_preload": _album_font_preload(album),
            "trip": trip,
            "collection": collection,
            "sub_albums": sub_albums,
            "album_is_showcase": album_is_showcase,
            "featured": featured,
            "reel_mode": reel_mode,
            "images": images,
            # None unless the "By day" sort is active — see _day_sections
            "day_sections": day_sections,
            "tags": [r["name"] for r in tag_rows],
            "active_tag": tag,
            "current_sort": current_sort,
            "default_sort": default_sort,
            "sort_options": sort_options,
            "sort_label": _active_sort_label(sort_options),
        },
    )


@app.get("/image/{album:path}/{filename}", response_class=HTMLResponse)
def image_view(request: Request, album: str, filename: str, sort: str | None = None, col: str | None = None):
    rel = _safe_rel(album, filename).as_posix()
    c = db.conn()
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    if HIDE_GPS:
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
        and _album_collection(col_root)
    ):
        prefix = col_root + "/"
        where_scope = "(album = ? OR substr(album, 1, ?) = ?)"
        scope_params: tuple = (col_root, len(prefix), prefix)
    else:
        col_root = ""  # absent / forged / no longer a collection: folder scope
        where_scope = "album = ?"
        scope_params = (album,)
    # Sort must resolve exactly like on the album grid the visitor came from
    # (same cfg scope: collection root or the image's own folder), so links
    # without an explicit ?sort= still walk the grid in the grid's order —
    # including a cfg-preset default and the curated order.
    scope_cfg = _album_config(col_root or album)
    curated_order = _curated_photo_order(col_root or album, scope_cfg)
    # "By day" is chronological SQL plus grouping, so the walk resolves it
    # like date_asc and pages through the grid's day sections in order
    current_sort, default_sort, base_sort = _resolve_image_sort(
        scope_cfg, sort, curated_order,
        days=_scope_day_count(where_scope, scope_params) > 1)
    order_sql = SORT_IMAGE_SQL[base_sort]
    neighbours = c.execute(
        f"SELECT rel_path FROM images WHERE {where_scope} ORDER BY {order_sql}",
        scope_params,
    ).fetchall()
    rel_list = [r["rel_path"] for r in neighbours]
    if current_sort == SORT_CURATED:
        pos = {r: i for i, r in enumerate(curated_order)}
        rel_list.sort(key=lambda r: pos.get(r, len(pos)))
    idx = rel_list.index(rel) if rel in rel_list else -1
    prev_rel = rel_list[idx - 1] if idx > 0 else None
    next_rel = rel_list[idx + 1] if 0 <= idx < len(rel_list) - 1 else None
    pretty_exif = _prettify_exif(exif, _request_lang(request))
    description = _extract_description(exif)
    return templates.TemplateResponse(
        "image.html",
        {
            "request": request,
            "image": dict(row),
            "bg_album": row["album"],
            "breadcrumbs": _album_breadcrumbs(row["album"]),
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


def _extract_description(exif: dict) -> str | None:
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


def _prettify_exif(exif: dict, lang: str = i18n.DEFAULT_LANG) -> list[tuple[str, str]]:
    if not exif:
        return []
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
    out: list[tuple[str, str]] = []
    for k, label_key in keys:
        if k in exif and exif[k] not in (None, "", []):
            v = exif[k]
            if k == "ExposureTime" and isinstance(v, (int, float)) and v > 0:
                if v < 1:
                    v = f"1/{round(1/v)} s"
                else:
                    v = f"{v} s"
            elif k == "FNumber" and isinstance(v, (int, float)):
                v = f"f/{v:.1f}"
            elif k in ("FocalLength", "FocalLengthIn35mmFilm") and isinstance(v, (int, float)):
                v = f"{v:.0f} mm"
            out.append((i18n.t(lang, label_key), str(v)))
    gps = exif.get("GPSInfo")
    if isinstance(gps, dict):
        lat = _gps_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _gps_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat is not None and lon is not None:
            out.append((i18n.t(lang, "exif.gps"), f"{lat:.6f}, {lon:.6f}"))
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


def _theme_css_response(album: str | None):
    """Shared body of the two theme routes. The sheet only redefines tokens
    style.css already declares, so it can never introduce a rule — and every
    value is re-serialised from parsed numbers (three ints for a colour,
    floats for the filter), so nothing that came out of a cfg is ever printed
    into the CSS verbatim."""
    decls = _theme_decls(album)
    if not decls:
        raise HTTPException(404, "not found")
    return Response(":root{%s}" % ";".join(decls), media_type="text/css",
                    headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/site-theme.css")
def site_theme_css():
    """gallery.cfg's `wallpaper_tint` / `wallpaper_dim` for every page that
    isn't an album's — the site's own default backdrop is dressed here."""
    return _theme_css_response(None)


@app.get("/album-theme.css/{album:path}")
def album_theme_css(album: str):
    """An album's own `accent` / `wallpaper_tint` / `wallpaper_dim` — the CSP
    drops inline styles, so this is how per-album colour reaches the page
    (see the section on it above)."""
    return _theme_css_response(album)


@app.get("/album-font.css/{album:path}")
def album_font_css(album: str):
    """The @font-face + --album-title-font binding for an album's
    `font = …` (plus --album-title-scale for its `font_scale = …`), as a
    real stylesheet — the CSP drops inline styles, so this is how a
    per-album face reaches the page (see the section on it above). The
    album path is percent-encoded into the url() so a folder name can
    never break out of the CSS string; the scale is re-serialised from a
    validated float, so it cannot carry anything but a number either."""
    font = _album_font_file(album)
    if font is None:
        raise HTTPException(404, "not found")
    fmt, _mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    src = f"/album-font/{quote(album)}?v={_album_font_version(album)}"
    scale = _album_font_scale(album)
    root = f"--album-title-font:'{ALBUM_FONT_FAMILY}'"
    if scale is not None:
        root += f";--album-title-scale:{scale:g}"
    css = (
        "@font-face{"
        f"font-family:'{ALBUM_FONT_FAMILY}';"
        f"src:url('{src}') format('{fmt}');"
        "font-weight:400;font-style:normal;font-display:swap}"
        f":root{{{root}}}"
    )
    return Response(css, media_type="text/css",
                    headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/album-font/{album:path}")
def serve_album_font(album: str):
    """The font file an album's cfg names in `font = …`. The filename never
    comes from the URL — it is read back out of the album.cfg — so this
    route cannot be used to pull anything else out of an album."""
    font = _album_font_file(album)
    if font is None:
        raise HTTPException(404, "not found")
    _fmt, mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    return FileResponse(str(font), media_type=mime,
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/album-icon/{album:path}")
def serve_album_icon(album: str):
    """The image an album's cfg names in `icon = …`. Like the font route,
    the filename never comes from the URL — it is read back out of the
    album.cfg — so this cannot be used to pull anything else out of an
    album's `.album/` folder."""
    icon = _album_icon_file(album)
    if icon is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(icon), media_type=ALBUM_ICON_TYPES[icon.suffix.lower()],
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/album-wallpaper/{variant}/{album:path}")
def serve_album_wallpaper(variant: str, album: str):
    """The backdrop an album's cfg names in `wallpaper =` / `wallpaper_mobile =`.
    Like the icon and font routes the filename never comes from the URL — it is
    read back out of the album.cfg — so this cannot be used to pull anything
    else out of an album's `.album/` folder. The album in the path is the
    OWNING album (the one that set the key), which _album_wallpaper_url has
    already resolved, so a sub-album never serves through its parent's URL."""
    if variant not in ALBUM_WALLPAPER_KEYS:
        raise HTTPException(404, "not found")
    path = _album_wallpaper_file(album, variant)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(path),
                        media_type=ALBUM_WALLPAPER_TYPES[path.suffix.lower()],
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/thumb/{album}/{filename:path}")
def serve_thumb(album: str, filename: str):
    rel = _safe_rel(album, filename).as_posix()
    src = PHOTOS_DIR / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    dst = (THUMBS_DIR / rel).with_suffix(".jpg")
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        t = scanner.ensure_thumb(PHOTOS_DIR, THUMBS_DIR, rel, THUMB_SIZE)
        if not t:
            raise HTTPException(500, "thumb generation failed")
        dst = t
    return FileResponse(str(dst), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/preview/{album}/{filename:path}")
def serve_preview(album: str, filename: str):
    rel = _safe_rel(album, filename).as_posix()
    src = PHOTOS_DIR / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    dst = (PREVIEWS_DIR / rel).with_suffix(".jpg")
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        t = scanner.ensure_thumb(PHOTOS_DIR, PREVIEWS_DIR, rel, PREVIEW_SIZE)
        if not t:
            raise HTTPException(500, "preview generation failed")
        dst = t
    return FileResponse(str(dst), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/full/{album}/{filename:path}")
def serve_full(album: str, filename: str):
    rel = _safe_rel(album, filename).as_posix()
    src = PHOTOS_DIR / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    if scanner.needs_jpeg_conversion(src):
        dst = scanner.ensure_full_jpeg(PHOTOS_DIR, FULLS_DIR, rel)
        if not dst:
            raise HTTPException(500, "full conversion failed")
        return FileResponse(str(dst), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"})
    return FileResponse(str(src), headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", sort: str | None = None):
    q = q.strip()
    c = db.conn()
    if not q:
        return RedirectResponse("/albums")
    current_sort = _pick_sort(sort, SORT_IMAGE_SQL, SORT_IMAGE_DEFAULT)
    qualified_sql = _qualify_sort(SORT_IMAGE_SQL[current_sort])
    like = f"%{q}%"
    rows = c.execute(
        f"""SELECT DISTINCT i.* FROM images i
           LEFT JOIN image_tags it ON it.image_id = i.id
           LEFT JOIN tags t ON t.id = it.tag_id
           WHERE i.album LIKE ? OR i.filename LIKE ? OR t.name LIKE ?
           ORDER BY {qualified_sql}""",
        (like, like, like),
    ).fetchall()
    sort_options = _image_sort_options_for_template(current_sort, lang=_request_lang(request))
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q,
            "images": [dict(r) for r in rows],
            "current_sort": current_sort,
            "default_sort": SORT_IMAGE_DEFAULT,
            "sort_options": sort_options,
            "sort_label": _active_sort_label(sort_options),
        },
    )
