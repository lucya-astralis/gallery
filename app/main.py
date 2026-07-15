import json
import logging
import os
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, i18n, scanner, watcher

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

try:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
FULLS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

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


def _public_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


templates.env.globals["public_base_url"] = _public_base_url


# ----- showcase helpers -------------------------------------------------
SHOWCASE_MARKER = scanner.SHOWCASE_MARKER


def _display_name(s: str) -> str:
    """Strip a single leading showcase-marker char for human display."""
    if SHOWCASE_MARKER and s.startswith(SHOWCASE_MARKER):
        stripped = s[len(SHOWCASE_MARKER):].lstrip("-_ ")
        return stripped or s
    return s


def _strip_marker_segment(s: str) -> str:
    if SHOWCASE_MARKER and s.startswith(SHOWCASE_MARKER):
        return s[len(SHOWCASE_MARKER):] or s
    return s


def _pretty_rel(rel_path: str) -> str:
    """Strip the leading showcase marker from each segment of a rel_path so
    featured links don't expose the internal `_` prefix."""
    if not SHOWCASE_MARKER:
        return rel_path
    parts = rel_path.split("/")
    if not parts:
        return rel_path
    return "/".join(_strip_marker_segment(p) for p in parts)


templates.env.globals["display_name"] = _display_name
templates.env.globals["pretty_rel"] = _pretty_rel
templates.env.globals["showcase_marker"] = SHOWCASE_MARKER
# month_label is provided per request by _i18n_context (localized)


def _showcase_rows(album: str | None = None, limit: int = 50, random_order: bool = False,
                   subtree: bool = False):
    """Featured photos, optionally filtered to one album. `subtree=True`
    widens the filter to the album's whole folder tree, so photos featured
    inside sub-albums surface on the parent album's page too. substr()
    (not LIKE) keeps `_`/`%` in album names from acting as wildcards."""
    c = db.conn()
    if album is not None and subtree:
        prefix = album + "/"
        where = "WHERE is_showcase = 1 AND (album = ? OR substr(album, 1, ?) = ?)"
        params: tuple = (album, len(prefix), prefix)
    elif album is not None:
        where = "WHERE is_showcase = 1 AND album = ?"
        params = (album,)
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
    a showcase is decided by `_album_is_showcase` (album.cfg `showcase = …`,
    legacy `_` folder marker as fallback). Same card shape as `albums_index`
    (album, name, count, latest, cover, sub_count) so the showcase-album
    partial can be reused on both pages."""
    cards = [c for c in _top_level_album_cards() if _album_is_showcase(c["album"])]
    order = "curated" if _curated_album_positions() else "latest_desc"
    cards = _sorted_album_cards(cards, order)
    return cards[:limit] if limit is not None else cards


def _serialize_showcase_item(row: dict, base: str) -> dict:
    rel = row["rel_path"]
    return {
        "rel_path": rel,
        "album": row["album"],
        "filename": row["filename"],
        "display_album": _display_name(row["album"]),
        "display_filename": _display_name(row["filename"]),
        "width": row.get("width"),
        "height": row.get("height"),
        "size": row.get("size"),
        "taken_at": row.get("taken_at"),
        "urls": {
            "thumb": f"/thumb/{rel}",
            "preview": f"/preview/{rel}",
            "full": f"/full/{rel}",
            "page": f"/image/{rel}",
            "thumb_abs": f"{base}/thumb/{rel}",
            "preview_abs": f"{base}/preview/{rel}",
            "full_abs": f"{base}/full/{rel}",
            "page_abs": f"{base}/image/{rel}",
        },
    }


def _json_cors(payload, max_age: int = 300) -> JSONResponse:
    resp = JSONResponse(payload)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    return resp


# ----- album tree -------------------------------------------------------
# Albums are directories and nest arbitrarily (e.g. "japan/tokyo"). The
# `images.album` column stores each photo's full parent-directory path, so
# the album *tree* is derived from those strings — intermediate folders that
# hold only sub-folders (no direct photos of their own) are still found.
def _distinct_albums() -> list[str]:
    c = db.conn()
    return [r["album"] for r in c.execute("SELECT DISTINCT album FROM images").fetchall()]


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
        "name": _display_name(album.rsplit("/", 1)[-1]),
        "count": agg["count"] if agg else 0,
        "latest": agg["latest"] if agg else None,
        "cover": cover_rel,
        "sub_count": len(_child_album_names(album, all_albums)),
    }


def _top_level_album_cards(all_albums: list[str] | None = None) -> list[dict]:
    """One card per top-level album (unsorted)."""
    all_albums = all_albums if all_albums is not None else _distinct_albums()
    return [_album_card(n, all_albums) for n in _child_album_names(None, all_albums)]


def _album_order_key(path: str) -> str:
    """Normalize an album path for matching against gallery.cfg
    `album_order` entries: marker-stripped segments, lower-cased, so an
    entry works with or without the showcase marker / exact casing."""
    segs = path.replace("\\", "/").strip().strip("/").split("/")
    return "/".join(_strip_marker_segment(s) for s in segs).lower()


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
    provides the tie-break for every other key."""
    cards = sorted(cards, key=lambda a: a["album"].lower())
    if sort_key == "curated":
        # listed albums first, in their configured order; everything not
        # listed follows newest-first (stable sorts keep both groups tidy)
        pos = _curated_album_positions()
        cards.sort(key=lambda a: a["latest"] or "", reverse=True)
        cards.sort(key=lambda a: pos.get(_album_order_key(a["album"]), len(pos)))
    elif sort_key == "name_desc":
        cards.sort(key=lambda a: a["album"].lower(), reverse=True)
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
    """[{name, path}, ...] for each ancestor segment of an album path, so
    templates can render HOME / ALBUMS / japan / tokyo with linkable parts."""
    acc: list[str] = []
    out: list[dict] = []
    for seg in album.split("/"):
        if not seg:
            continue
        acc.append(seg)
        out.append({"name": _display_name(seg), "path": "/".join(acc)})
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
#
# This is the only place looked at — a cfg or description left in the photo
# folder itself is ignored. gallery.cfg is NOT part of this: it configures
# the gallery as a whole and stays at the root of PHOTOS_DIR.
#
# album.cfg keys (file sits in the album's `.album/` folder):
#   collection = true    -> the album shows every photo in its subtree (its
#                           own + all sub-folders) as one flat collection.
#   cover = sub/pic.jpg  -> pin the album cover (path relative to the album)
#                           instead of auto-picking the newest photo.
#   showcase = true      -> showcase album (★ rail on /albums + welcome).
#   featured = a.jpg, …  -> featured photos (see _recompute_featured).
#   reel = featured|random|off -> what the album's hero slideshow shows.
#   order = a.jpg, …     -> curated photo order ("Curated" sort option).
#   sort = curated|date_desc|… -> preselect the sort option for this album.
#   effect = sakura      -> ambient effect layer on this album's page
#                           (whitelisted in ALBUM_EFFECTS; see initAlbumFx).
#   font = Musashi.otf   -> display face for the album's hero title; the file
#                           sits next to the cfg in `.album/` (see the
#                           per-album title font section further down).
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
ALBUM_FONT_FAMILY = "album-title"

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


def _album_font_css_url(album: str) -> str | None:
    """Cache-busting URL of the album's generated font stylesheet, or None
    when the album configures no font. Versioned by the font's mtime (same
    idea as _static_url) so swapping the file can't be masked by a stale
    cache — both this sheet and the font it points at are cached hard."""
    font = _album_font_file(album)
    if font is None:
        return None
    try:
        version = int(font.stat().st_mtime)
    except OSError:
        version = 0
    return f"/album-font.css/{quote(album)}?v={version}"


# ----- showcase / featured (album.cfg owns it; `_` marker is fallback) --
# album.cfg is the source of truth for two things that used to be driven by
# the `_` prefix:
#   showcase = true|false   -> is this a showcase album? (★ on /albums)
#   featured = a.jpg, b.jpg -> which photos are featured (welcome hero,
#                              /api/showcase, the featured hero slideshow of
#                              the album and its parents); paths may point
#                              into sub-folders, bare filenames also match
#                              anywhere in the subtree, and `*`/`all`
#                              features every photo directly in the album.
# When a key is ABSENT the legacy marker still applies, so existing albums
# keep working until migrated; when present, album.cfg wins (and can switch
# a marked album/photo back off).
def _album_is_showcase(album: str) -> bool:
    cfg = _album_config(album)
    if "showcase" in cfg:
        return _cfg_bool(_cfg_first(cfg, "showcase"))
    return bool(SHOWCASE_MARKER) and album.rsplit("/", 1)[-1].startswith(SHOWCASE_MARKER)


def _resolve_photo_refs(album: str, items: list[str]) -> list[str]:
    """Resolve photo references from an album.cfg list value (`featured`,
    `order`) to indexed rel_paths, keeping the given order (deduped). Each
    item is a path relative to the album (sub-folders allowed); a bare
    filename that isn't found at that exact path falls back to a filename
    match anywhere in the album's subtree, so a parent cfg can reference
    sub-folder photos without spelling out the folder (matches every
    same-named file)."""
    c = db.conn()
    prefix = album + "/"
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip().strip("/")
        if not item:
            continue
        rel = item if (item == album or item.startswith(prefix)) else f"{album}/{item}"
        row = c.execute("SELECT rel_path FROM images WHERE rel_path = ?", (rel,)).fetchone()
        rows = [row] if row else c.execute(
            "SELECT rel_path FROM images WHERE (album = ? OR substr(album, 1, ?) = ?) "
            "AND filename = ? ORDER BY rel_path",
            (album, len(prefix), prefix, item),
        ).fetchall()
        for r in rows:
            if r["rel_path"] not in seen:
                seen.add(r["rel_path"])
                out.append(r["rel_path"])
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
    """Recompute the `is_showcase` flag for every photo from album.cfg
    `featured` lists, falling back to the legacy filename marker for albums
    that don't configure it. The single owner of the column — runs at startup
    and after every scan / album.cfg change."""
    c = db.conn()
    featured: set[str] = set()
    for album in _distinct_albums():
        cfg = _album_config(album)
        if "featured" in cfg:
            featured |= _resolve_featured(album, cfg["featured"])
        elif SHOWCASE_MARKER:
            for r in c.execute("SELECT rel_path, filename FROM images WHERE album = ?", (album,)):
                if scanner.is_showcase_photo(r["filename"]):
                    featured.add(r["rel_path"])
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
# Config is keyed by the album's marker-stripped, lower-cased path so it
# matches whether or not the folder carries the showcase marker (e.g.
# `_japan_2026` -> `japan_2026`). All dates are wall-clock; the live
# countdown and current-stop highlight are computed client-side (initTrip
# in app.js) against the viewer's own clock — so it reads correctly both
# from home before the flight and on the ground once the trip is underway.
TRIPS: dict[str, dict] = {
    "japan_2026": {
        "title": "Japan 2026",
        "jp": "日本",
        # flight out (local wall-clock). 12:00 = noon departure.
        "depart": "2026-08-09T12:00:00",
        # lat/lon feed the /api/trip-weather proxy (see below); `icon` names a
        # civic emblem SVG under /static/emblems (rendered on the timeline stop)
        "stops": [
            {"city": "Osaka",   "jp": "大阪", "album": "osaka",   "icon": "osaka",   "start": "2026-08-10", "end": "2026-08-16", "lat": 34.6937, "lon": 135.5023},
            {"city": "Sapporo", "jp": "札幌", "album": "sapporo", "icon": "sapporo", "start": "2026-08-16", "end": "2026-09-16", "lat": 43.0618, "lon": 141.3545},
            {"city": "Tokyo",   "jp": "東京", "album": "tokyo",   "icon": "tokyo",   "start": "2026-09-16", "end": "2027-01-02", "lat": 35.6895, "lon": 139.6917},
        ],
    },
}

def _trip_for_album(album: str, lang: str = i18n.DEFAULT_LANG) -> dict | None:
    """Render-ready trip dashboard for `album`, or None when the album has
    no configured trip. Matched on the marker-stripped, lower-cased path so
    `_japan_2026` resolves to the `japan_2026` config. Each stop is wired to
    its sub-album — cover + photo count + link — so the timeline doubles as
    navigation into the city galleries (empty city folders stay unlinked).
    Human-readable dates are localized; app.js re-renders them client-side
    in the same language (read from <html lang>)."""
    key = "/".join(_strip_marker_segment(s) for s in album.split("/")).lower()
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
            "icon": s.get("icon", ""),
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
# Upstream sees only this server's IP plus fixed city coordinates.
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
        "&current=temperature_2m,weather_code,is_day&timezone=Asia%2FTokyo"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "lucya.systems-gallery"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        payload = json.load(resp)
    if isinstance(payload, dict):  # single-location responses aren't wrapped
        payload = [payload]
    out = []
    for s, loc in zip(stops, payload):
        cur = (loc or {}).get("current") or {}
        temp, code = cur.get("temperature_2m"), cur.get("weather_code")
        if temp is None or code is None:
            continue
        out.append({
            "city": s["city"],  # English key, matches data-city / data-stop-wx lookup
            "temp": float(temp),
            "code": int(code),
            "is_day": int(cur.get("is_day") or 0),
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


def _pick_sort(value: str | None, allowed, default: str) -> str:
    return value if value in allowed else default


def _image_sort_options_for_template(current: str, curated: bool = False,
                                     lang: str = i18n.DEFAULT_LANG) -> list[dict]:
    keys = ([(SORT_CURATED, SORT_CURATED_LABEL_KEY)] if curated else [])
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
    if exc.status_code == 404:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "path": request.url.path},
            status_code=404,
        )
    return Response(content=str(exc.detail), status_code=exc.status_code)


def _run_scan():
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        result = scanner.full_scan(
            PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE,
            previews_dir=PREVIEWS_DIR, preview_size=PREVIEW_SIZE,
        )
        # re-derive featured flags from album.cfg (+ legacy marker fallback)
        # now that the index reflects the current files.
        _recompute_featured()
        if result["indexed"] or result["thumbnails"] or result["previews"] or result["removed"]:
            log.info("scan: %s", result)
    except Exception as e:
        log.warning("scan failed: %s", e)
    finally:
        _scan_lock.release()


def _periodic_scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL)
        _run_scan()


@app.on_event("startup")
def _startup():
    db.init(DATA_DIR)
    _recompute_featured()
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds hide_gps=%s strip_gps=%s showcase_marker=%r",
        PHOTOS_DIR, THUMBS_DIR, DATA_DIR, THUMB_SIZE, ENABLE_WATCHER, SCAN_INTERVAL, HIDE_GPS, STRIP_GPS, scanner.SHOWCASE_MARKER,
    )
    threading.Thread(target=_run_scan, daemon=True).start()
    if ENABLE_WATCHER:
        try:
            watcher.start(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE,
                          previews_dir=PREVIEWS_DIR, preview_size=PREVIEW_SIZE,
                          fulls_dir=FULLS_DIR, on_config=_recompute_featured)
        except Exception as e:
            log.warning("watcher failed to start: %s", e)
    if SCAN_INTERVAL > 0:
        threading.Thread(target=_periodic_scan_loop, daemon=True).start()
        log.info("periodic rescan every %d seconds", SCAN_INTERVAL)


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


def _resolve_showcase_path(album: str, filename: str) -> tuple[str, str]:
    """Featured items expose marker-stripped URLs (see _pretty_rel). When a
    request comes in for a pretty path, try the marker-prefixed variants so
    the original file is found."""
    if not SHOWCASE_MARKER:
        return album, filename
    pm = SHOWCASE_MARKER
    variants = [(album, filename)]
    if not filename.startswith(pm):
        variants.append((album, pm + filename))
    if not album.startswith(pm):
        variants.append((pm + album, filename))
        if not filename.startswith(pm):
            variants.append((pm + album, pm + filename))
    for a, f in variants:
        try:
            rel = _safe_rel(a, f)
        except HTTPException:
            continue
        if (PHOTOS_DIR / rel).exists():
            return a, f
    return album, filename


# ----- gallery-wide config (gallery.cfg) ---------------------------------
# Optional `gallery.cfg` dropped into the photos ROOT (next to the album
# folders). Same format as album.cfg (see _parse_cfg: `key = value`, list
# values comma-separated / repeated keys / one entry per line). Known keys:
#   welcome = showcase            -> hero feed = random featured photos
#                                    (default; same as no file / no key)
#   welcome = random              -> hero feed = random photos, ignore featured
#   welcome = <album/file.jpg>,…  -> hand-picked hero feed in exactly this
#                                    order (paths are relative to photos/,
#                                    marker prefixes may be omitted).
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
    """Resolve one gallery.cfg welcome entry to an indexed image row. Accepts
    backslashes and marker-stripped paths (the `_` prefix may be omitted)."""
    rel = raw.replace("\\", "/").strip().strip("/")
    if not rel or "/" not in rel:
        return None
    c = db.conn()
    row = c.execute(
        "SELECT album, filename, rel_path FROM images WHERE rel_path = ?", (rel,)
    ).fetchone()
    if row:
        return row
    # second chance: re-add showcase markers the pretty path dropped
    album, _, filename = rel.rpartition("/")
    a, f = _resolve_showcase_path(album, filename)
    if (a, f) != (album, filename):
        return c.execute(
            "SELECT album, filename, rel_path FROM images WHERE rel_path = ?",
            (f"{a}/{f}",),
        ).fetchone()
    return None


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
    # annotate instead of a legacy `startswith(marker)` check in the template,
    # so album.cfg-driven showcase albums are recognized too
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


@app.get("/api/showcase")
def api_showcase(request: Request, limit: int = 50, album: str | None = None, random: bool = False):
    """
    Returns showcased photos as JSON. CORS-enabled for cross-origin embedding.

    Query params:
      limit:  max number of items, 1..200 (default 50)
      album:  optional album-name filter
      random: pass `?random=1` to randomise order; default is newest-first
    """
    limit = max(1, min(200, limit))
    rows = _showcase_rows(album=album, limit=limit, random_order=bool(random))
    base = _public_base_url(request)
    items = [_serialize_showcase_item(r, base) for r in rows]
    return _json_cors(
        {
            "count": len(items),
            "marker": SHOWCASE_MARKER,
            "items": items,
        }
    )


@app.options("/api/showcase")
def api_showcase_options():
    # CORS pre-flight (most simple GETs don't trigger this but be polite)
    return _json_cors({})


@app.get("/api/shuffle")
def api_shuffle(limit: int = 8):
    limit = max(1, min(24, limit))
    c = db.conn()
    rows = c.execute(
        "SELECT album, filename, rel_path FROM images ORDER BY RANDOM() LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/album/{album:path}", response_class=HTMLResponse)
def album_view(request: Request, album: str, tag: str | None = None, sort: str | None = None):
    album = album.strip("/")
    if not album:
        raise HTTPException(404, "album not found")
    # a just-saved album.cfg must be visible on this very reload (reel mode,
    # featured set, grid stars) without waiting for the watcher's debounce
    _refresh_featured_on_cfg_change(album)
    album_cfg = _album_config(album)
    # album.cfg `order` adds a "Curated" sort option; `sort` presets the
    # default sort for this album (query param still wins).
    curated_order = _curated_photo_order(album, album_cfg)
    allowed = set(SORT_IMAGE_SQL) | ({SORT_CURATED} if curated_order else set())
    default_sort = _pick_sort(_cfg_first(album_cfg, "sort"), allowed, SORT_IMAGE_DEFAULT)
    current_sort = _pick_sort(sort, allowed, default_sort)
    # curated is reordered in Python below; SQL runs with the date default
    base_sort = SORT_IMAGE_DEFAULT if current_sort == SORT_CURATED else current_sort
    # qualify column names so the JOIN query below isn't ambiguous
    qualified_sql = SORT_IMAGE_SQL[base_sort].replace("filename", "i.filename")
    qualified_sql = qualified_sql.replace("taken_at", "i.taken_at")
    qualified_sql = qualified_sql.replace("mtime", "i.mtime")
    qualified_sql = qualified_sql.replace("size", "i.size")
    c = db.conn()
    # Collection mode (album.cfg `collection = true`): the grid shows every
    # photo in this album's whole subtree (its own + all sub-folders) as one
    # flat set, instead of only the photos sitting directly in this folder.
    collection = _cfg_bool(_cfg_first(album_cfg, "collection"))
    if collection:
        prefix = album + "/"
        where_simple = "(album = ? OR substr(album, 1, ?) = ?)"
        where_join = "(i.album = ? OR substr(i.album, 1, ?) = ?)"
        scope_params: tuple = (album, len(prefix), prefix)
    else:
        where_simple = "album = ?"
        where_join = "i.album = ?"
        scope_params = (album,)
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
    # Showcase status now comes from album.cfg (`showcase = …`), with the
    # legacy `_` folder-name marker as a fallback.
    album_is_showcase = _album_is_showcase(album)
    # Hero reel (album.cfg `reel`, like the welcome feed): default/featured
    # shows featured photos from this album AND its sub-albums (subtree), so
    # photos featured inside e.g. japan_2026/osaka surface on the japan_2026
    # page too — a showcase ALBUM doesn't auto-promote its contents, each
    # photo opts in via album.cfg `featured` or the legacy `_` prefix.
    # `reel = random` fills it with random subtree photos instead, and
    # `reel = off` hides the slideshow entirely.
    reel_mode = (_cfg_first(album_cfg, "reel") or "").strip().lower()
    if reel_mode in _FALSE:
        featured = []
        reel_mode = "off"
    elif reel_mode in ("random", "shuffle"):
        featured = _random_subtree_rows(album, limit=8)
        reel_mode = "random"
    else:
        # The reel follows the album.cfg `featured` list order: photos from
        # this album's own list come first, exactly as written; anything
        # featured by sub-album cfgs or the legacy marker follows newest-
        # first. Fetch wide before trimming so a date-based LIMIT can't cut
        # off early list entries.
        featured = _showcase_rows(album=album, limit=100, random_order=False, subtree=True)
        order_items = [i for i in album_cfg.get("featured", [])
                       if i.strip().lower() not in ("*", "all")]
        if order_items:
            featured = _apply_curated_order(featured, _resolve_photo_refs(album, order_items))
        featured = featured[:8]
        reel_mode = "featured"
    lang = _request_lang(request)
    sort_options = _image_sort_options_for_template(current_sort, curated=bool(curated_order),
                                                    lang=lang)
    effect = (_cfg_first(album_cfg, "effect") or "").strip().lower()
    return templates.TemplateResponse(
        "album.html",
        {
            "request": request,
            "album": album,
            "breadcrumbs": _album_breadcrumbs(album),
            "album_description": _album_description(album, lang),
            # cover photo for the mobile hero header (see .album-hero)
            "album_cover": _album_cover_rel(album),
            # ambient page effect (album.cfg `effect = ...`, whitelisted)
            "album_effect": effect if effect in ALBUM_EFFECTS else None,
            # generated stylesheet for the album's own title face
            # (album.cfg `font = ...`); None when it configures none
            "album_font_css": _album_font_css_url(album),
            "trip": _trip_for_album(album, lang),
            "collection": collection,
            "sub_albums": sub_albums,
            "album_is_showcase": album_is_showcase,
            "featured": featured,
            "reel_mode": reel_mode,
            "images": images,
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
    album, filename = _resolve_showcase_path(album, filename)
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
        and _cfg_bool(_cfg_first(_album_config(col_root), "collection"))
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
    allowed = set(SORT_IMAGE_SQL) | ({SORT_CURATED} if curated_order else set())
    default_sort = _pick_sort(_cfg_first(scope_cfg, "sort"), allowed, SORT_IMAGE_DEFAULT)
    current_sort = _pick_sort(sort, allowed, default_sort)
    base_sort = SORT_IMAGE_DEFAULT if current_sort == SORT_CURATED else current_sort
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


@app.get("/album-font.css/{album:path}")
def album_font_css(album: str):
    """The @font-face + --album-title-font binding for an album's
    `font = …`, as a real stylesheet — the CSP drops inline styles, so this
    is how a per-album face reaches the page (see the section on it above).
    The album path is percent-encoded into the url() so a folder name can
    never break out of the CSS string."""
    font = _album_font_file(album)
    if font is None:
        raise HTTPException(404, "not found")
    fmt, _mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    try:
        version = int(font.stat().st_mtime)
    except OSError:
        version = 0
    src = f"/album-font/{quote(album)}?v={version}"
    css = (
        "@font-face{"
        f"font-family:'{ALBUM_FONT_FAMILY}';"
        f"src:url('{src}') format('{fmt}');"
        "font-weight:400;font-style:normal;font-display:swap}"
        f":root{{--album-title-font:'{ALBUM_FONT_FAMILY}'}}"
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


@app.get("/thumb/{album}/{filename:path}")
def serve_thumb(album: str, filename: str):
    album, filename = _resolve_showcase_path(album, filename)
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
    album, filename = _resolve_showcase_path(album, filename)
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
    album, filename = _resolve_showcase_path(album, filename)
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
    qualified_sql = SORT_IMAGE_SQL[current_sort].replace("filename", "i.filename")
    qualified_sql = qualified_sql.replace("taken_at", "i.taken_at")
    qualified_sql = qualified_sql.replace("mtime", "i.mtime")
    qualified_sql = qualified_sql.replace("size", "i.size")
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
