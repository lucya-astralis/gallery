import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, scanner, watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("main")

PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "./photos")).resolve()
THUMBS_DIR = Path(os.environ.get("THUMBS_DIR", "./thumbnails")).resolve()
PREVIEWS_DIR = Path(os.environ.get("PREVIEWS_DIR", "./previews")).resolve()
FULLS_DIR = Path(os.environ.get("FULLS_DIR", str(PREVIEWS_DIR / "_full"))).resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "480"))
PREVIEW_SIZE = int(os.environ.get("PREVIEW_SIZE", "1600"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "0"))
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
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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


templates.env.globals["display_name"] = _display_name
templates.env.globals["showcase_marker"] = SHOWCASE_MARKER


def _showcase_rows(album: str | None = None, limit: int = 50, random_order: bool = False):
    c = db.conn()
    if album is not None:
        where = "WHERE is_showcase = 1 AND album = ?"
        params: tuple = (album,)
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


# ----- sort options -----------------------------------------------------
# image grid (inside an album / search results)
SORT_IMAGE_OPTIONS = [
    ("date_desc", "Newest first",      "taken_at IS NULL, taken_at DESC, mtime DESC, filename ASC"),
    ("date_asc",  "Oldest first",      "taken_at IS NULL, taken_at ASC,  mtime ASC,  filename ASC"),
    ("name_asc",  "Filename A → Z",    "filename COLLATE NOCASE ASC"),
    ("name_desc", "Filename Z → A",    "filename COLLATE NOCASE DESC"),
    ("size_desc", "Largest first",     "size DESC, filename ASC"),
    ("size_asc",  "Smallest first",    "size ASC, filename ASC"),
]
SORT_IMAGE_DEFAULT = "date_desc"
SORT_IMAGE_SQL = {k: sql for k, _, sql in SORT_IMAGE_OPTIONS}

# album list (front page)
SORT_ALBUM_OPTIONS = [
    ("latest_desc", "Most recent",      "MAX(taken_at) IS NULL, MAX(taken_at) DESC, album COLLATE NOCASE ASC"),
    ("latest_asc",  "Oldest activity",  "MAX(taken_at) IS NULL, MAX(taken_at) ASC,  album COLLATE NOCASE ASC"),
    ("name_asc",    "Name A → Z",       "album COLLATE NOCASE ASC"),
    ("name_desc",   "Name Z → A",       "album COLLATE NOCASE DESC"),
    ("count_desc",  "Most photos",      "count DESC, album COLLATE NOCASE ASC"),
    ("count_asc",   "Fewest photos",    "count ASC, album COLLATE NOCASE ASC"),
]
SORT_ALBUM_DEFAULT = "latest_desc"
SORT_ALBUM_SQL = {k: sql for k, _, sql in SORT_ALBUM_OPTIONS}


def _pick_sort(value: str | None, allowed: dict[str, str], default: str) -> str:
    return value if value in allowed else default


def _image_sort_options_for_template(current: str) -> list[dict]:
    return [
        {"key": k, "label": label, "active": k == current}
        for k, label, _ in SORT_IMAGE_OPTIONS
    ]


def _album_sort_options_for_template(current: str) -> list[dict]:
    return [
        {"key": k, "label": label, "active": k == current}
        for k, label, _ in SORT_ALBUM_OPTIONS
    ]


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


def _backfill_showcase():
    """
    Recompute `is_showcase` for every indexed photo based on the current
    SHOWCASE_MARKER (which is checked against the filename only — being
    inside a showcase album does NOT auto-feature a photo). Runs on every
    startup so marker changes propagate without forcing a re-scan.
    """
    c = db.conn()
    marker = scanner.SHOWCASE_MARKER
    with db.lock():
        if not marker:
            c.execute("UPDATE images SET is_showcase = 0 WHERE is_showcase != 0")
        else:
            ml = len(marker)
            c.execute(
                """UPDATE images
                   SET is_showcase = CASE
                     WHEN substr(filename, 1, ?) = ? THEN 1
                     ELSE 0
                   END""",
                (ml, marker),
            )
        c.commit()


@app.on_event("startup")
def _startup():
    db.init(DATA_DIR)
    _backfill_showcase()
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds hide_gps=%s strip_gps=%s showcase_marker=%r",
        PHOTOS_DIR, THUMBS_DIR, DATA_DIR, THUMB_SIZE, ENABLE_WATCHER, SCAN_INTERVAL, HIDE_GPS, STRIP_GPS, scanner.SHOWCASE_MARKER,
    )
    threading.Thread(target=_run_scan, daemon=True).start()
    if ENABLE_WATCHER:
        try:
            watcher.start(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE,
                          previews_dir=PREVIEWS_DIR, preview_size=PREVIEW_SIZE,
                          fulls_dir=FULLS_DIR)
        except Exception as e:
            log.warning("watcher failed to start: %s", e)
    if SCAN_INTERVAL > 0:
        threading.Thread(target=_periodic_scan_loop, daemon=True).start()
        log.info("periodic rescan every %d seconds", SCAN_INTERVAL)


def _safe_rel(album: str, filename: str) -> Path:
    rel = (Path(album) / filename)
    if ".." in rel.parts or rel.is_absolute():
        raise HTTPException(400, "invalid path")
    full = (PHOTOS_DIR / rel).resolve()
    try:
        full.relative_to(PHOTOS_DIR)
    except ValueError:
        raise HTTPException(400, "invalid path")
    return rel


@app.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    c = db.conn()
    # Prefer showcase photos; if none are marked, fall back to random.
    showcase_feed = _showcase_rows(limit=12, random_order=True)
    if showcase_feed:
        feed = [
            {"album": r["album"], "filename": r["filename"], "rel_path": r["rel_path"]}
            for r in showcase_feed
        ]
        feed_label = "FEATURED"
        feed_mode = "showcase"
    else:
        feed = [
            dict(r)
            for r in c.execute(
                "SELECT album, filename, rel_path FROM images ORDER BY RANDOM() LIMIT 8"
            ).fetchall()
        ]
        feed_label = "RANDOM"
        feed_mode = "random"
    counts = c.execute(
        "SELECT COUNT(*) AS images, COUNT(DISTINCT album) AS albums FROM images"
    ).fetchone()
    showcase_count = c.execute(
        "SELECT COUNT(*) AS n FROM images WHERE is_showcase = 1"
    ).fetchone()
    return templates.TemplateResponse(
        "welcome.html",
        {
            "request": request,
            "shuffle": feed,
            "feed_label": feed_label,
            "feed_mode": feed_mode,
            "image_count": counts["images"] if counts else 0,
            "album_count": counts["albums"] if counts else 0,
            "showcase_count": showcase_count["n"] if showcase_count else 0,
        },
    )


@app.get("/albums", response_class=HTMLResponse)
def albums_index(request: Request, sort: str | None = None):
    current_sort = _pick_sort(sort, SORT_ALBUM_SQL, SORT_ALBUM_DEFAULT)
    order_sql = SORT_ALBUM_SQL[current_sort]
    c = db.conn()
    rows = c.execute(
        f"""SELECT album, COUNT(*) AS count, MAX(taken_at) AS latest,
                  (SELECT rel_path FROM images i2 WHERE i2.album = images.album
                   ORDER BY taken_at IS NULL, taken_at DESC, mtime DESC LIMIT 1) AS cover
           FROM images GROUP BY album ORDER BY {order_sql}"""
    ).fetchall()
    albums = [dict(r) for r in rows]
    is_show = lambda name: bool(SHOWCASE_MARKER) and name.startswith(SHOWCASE_MARKER)
    showcase_albums = [a for a in albums if is_show(a["album"])]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "albums": albums,
            "showcase_albums": showcase_albums,
            "current_sort": current_sort,
            "default_sort": SORT_ALBUM_DEFAULT,
            "sort_options": _album_sort_options_for_template(current_sort),
            "sort_label": next(
                (label for k, label, _ in SORT_ALBUM_OPTIONS if k == current_sort),
                "",
            ),
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


@app.get("/album/{album}", response_class=HTMLResponse)
def album_view(request: Request, album: str, tag: str | None = None, sort: str | None = None):
    current_sort = _pick_sort(sort, SORT_IMAGE_SQL, SORT_IMAGE_DEFAULT)
    # qualify column names so the JOIN query below isn't ambiguous
    qualified_sql = SORT_IMAGE_SQL[current_sort].replace("filename", "i.filename")
    qualified_sql = qualified_sql.replace("taken_at", "i.taken_at")
    qualified_sql = qualified_sql.replace("mtime", "i.mtime")
    qualified_sql = qualified_sql.replace("size", "i.size")
    c = db.conn()
    if tag:
        rows = c.execute(
            f"""SELECT i.* FROM images i
               JOIN image_tags it ON it.image_id = i.id
               JOIN tags t ON t.id = it.tag_id
               WHERE i.album = ? AND t.name = ?
               ORDER BY {qualified_sql}""",
            (album, tag),
        ).fetchall()
    else:
        order_sql = SORT_IMAGE_SQL[current_sort]
        rows = c.execute(
            f"SELECT * FROM images WHERE album = ? ORDER BY {order_sql}",
            (album,),
        ).fetchall()
    if not rows and tag is None:
        c2 = c.execute("SELECT 1 FROM images WHERE album = ? LIMIT 1", (album,)).fetchone()
        if not c2:
            raise HTTPException(404, "album not found")
    tag_rows = c.execute(
        """SELECT DISTINCT t.name FROM tags t
           JOIN image_tags it ON it.tag_id = t.id
           JOIN images i ON i.id = it.image_id
           WHERE i.album = ? ORDER BY t.name""",
        (album,),
    ).fetchall()
    album_is_showcase = bool(SHOWCASE_MARKER) and album.startswith(SHOWCASE_MARKER)
    # Featured strip = photos in this album with their own filename marker.
    # A showcase ALBUM doesn't auto-promote its contents — each photo opts
    # in independently with a `_` filename prefix.
    featured = _showcase_rows(album=album, limit=8, random_order=False)
    return templates.TemplateResponse(
        "album.html",
        {
            "request": request,
            "album": album,
            "album_is_showcase": album_is_showcase,
            "featured": featured,
            "images": [dict(r) for r in rows],
            "tags": [r["name"] for r in tag_rows],
            "active_tag": tag,
            "current_sort": current_sort,
            "default_sort": SORT_IMAGE_DEFAULT,
            "sort_options": _image_sort_options_for_template(current_sort),
            "sort_label": next(
                (label for k, label, _ in SORT_IMAGE_OPTIONS if k == current_sort),
                "",
            ),
        },
    )


@app.get("/image/{album}/{filename}", response_class=HTMLResponse)
def image_view(request: Request, album: str, filename: str, sort: str | None = None):
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
    current_sort = _pick_sort(sort, SORT_IMAGE_SQL, SORT_IMAGE_DEFAULT)
    order_sql = SORT_IMAGE_SQL[current_sort]
    neighbours = c.execute(
        f"SELECT rel_path FROM images WHERE album = ? ORDER BY {order_sql}",
        (album,),
    ).fetchall()
    rel_list = [r["rel_path"] for r in neighbours]
    idx = rel_list.index(rel) if rel in rel_list else -1
    prev_rel = rel_list[idx - 1] if idx > 0 else None
    next_rel = rel_list[idx + 1] if 0 <= idx < len(rel_list) - 1 else None
    pretty_exif = _prettify_exif(exif)
    description = _extract_description(exif)
    return templates.TemplateResponse(
        "image.html",
        {
            "request": request,
            "image": dict(row),
            "exif": pretty_exif,
            "exif_raw": exif,
            "tags": tags,
            "prev_rel": prev_rel,
            "next_rel": next_rel,
            "description": description,
            "album_rels": rel_list,
            "current_index": idx,
            "current_sort": current_sort,
            "default_sort": SORT_IMAGE_DEFAULT,
        },
    )


def _extract_description(exif: dict) -> str | None:
    if not exif:
        return None
    for key in ("ImageDescription", "XPComment", "XPSubject", "XPTitle", "UserComment"):
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


def _prettify_exif(exif: dict) -> list[tuple[str, str]]:
    if not exif:
        return []
    keys = [
        ("Make", "Camera make"),
        ("Model", "Camera model"),
        ("LensModel", "Lens"),
        ("DateTimeOriginal", "Date taken"),
        ("ExposureTime", "Exposure"),
        ("FNumber", "Aperture"),
        ("ISOSpeedRatings", "ISO"),
        ("FocalLength", "Focal length"),
        ("FocalLengthIn35mmFilm", "Focal length (35mm eq.)"),
        ("Flash", "Flash"),
        ("WhiteBalance", "White balance"),
        ("ExposureProgram", "Exposure program"),
        ("MeteringMode", "Metering mode"),
        ("Orientation", "Orientation"),
        ("Software", "Software"),
    ]
    out: list[tuple[str, str]] = []
    for k, label in keys:
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
            out.append((label, str(v)))
    gps = exif.get("GPSInfo")
    if isinstance(gps, dict):
        lat = _gps_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _gps_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat is not None and lon is not None:
            out.append(("GPS", f"{lat:.6f}, {lon:.6f}"))
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
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q,
            "images": [dict(r) for r in rows],
            "current_sort": current_sort,
            "default_sort": SORT_IMAGE_DEFAULT,
            "sort_options": _image_sort_options_for_template(current_sort),
            "sort_label": next(
                (label for k, label, _ in SORT_IMAGE_OPTIONS if k == current_sort),
                "",
            ),
        },
    )
