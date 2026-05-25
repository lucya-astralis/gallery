import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
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


@app.on_event("startup")
def _startup():
    db.init(DATA_DIR)
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds hide_gps=%s strip_gps=%s",
        PHOTOS_DIR, THUMBS_DIR, DATA_DIR, THUMB_SIZE, ENABLE_WATCHER, SCAN_INTERVAL, HIDE_GPS, STRIP_GPS,
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
    shuffle = [
        dict(r) for r in c.execute(
            "SELECT album, filename, rel_path FROM images ORDER BY RANDOM() LIMIT 8"
        ).fetchall()
    ]
    counts = c.execute(
        "SELECT COUNT(*) AS images, COUNT(DISTINCT album) AS albums FROM images"
    ).fetchone()
    return templates.TemplateResponse(
        "welcome.html",
        {
            "request": request,
            "shuffle": shuffle,
            "image_count": counts["images"] if counts else 0,
            "album_count": counts["albums"] if counts else 0,
        },
    )


@app.get("/albums", response_class=HTMLResponse)
def albums_index(request: Request):
    c = db.conn()
    rows = c.execute(
        """SELECT album, COUNT(*) AS count, MAX(taken_at) AS latest,
                  (SELECT rel_path FROM images i2 WHERE i2.album = images.album
                   ORDER BY taken_at DESC, mtime DESC LIMIT 1) AS cover
           FROM images GROUP BY album ORDER BY album"""
    ).fetchall()
    albums = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "index.html", {"request": request, "albums": albums}
    )


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
def album_view(request: Request, album: str, tag: str | None = None):
    c = db.conn()
    if tag:
        rows = c.execute(
            """SELECT i.* FROM images i
               JOIN image_tags it ON it.image_id = i.id
               JOIN tags t ON t.id = it.tag_id
               WHERE i.album = ? AND t.name = ?
               ORDER BY i.taken_at DESC, i.mtime DESC""",
            (album, tag),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM images WHERE album = ? ORDER BY taken_at DESC, mtime DESC",
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
    return templates.TemplateResponse(
        "album.html",
        {
            "request": request,
            "album": album,
            "images": [dict(r) for r in rows],
            "tags": [r["name"] for r in tag_rows],
            "active_tag": tag,
        },
    )


@app.get("/image/{album}/{filename}", response_class=HTMLResponse)
def image_view(request: Request, album: str, filename: str):
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
    neighbours = c.execute(
        "SELECT rel_path FROM images WHERE album = ? ORDER BY taken_at DESC, mtime DESC",
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
def search(request: Request, q: str = ""):
    q = q.strip()
    c = db.conn()
    if not q:
        return RedirectResponse("/albums")
    like = f"%{q}%"
    rows = c.execute(
        """SELECT DISTINCT i.* FROM images i
           LEFT JOIN image_tags it ON it.image_id = i.id
           LEFT JOIN tags t ON t.id = it.tag_id
           WHERE i.album LIKE ? OR i.filename LIKE ? OR t.name LIKE ?
           ORDER BY i.taken_at DESC, i.mtime DESC""",
        (like, like, like),
    ).fetchall()
    return templates.TemplateResponse(
        "search.html",
        {"request": request, "query": q, "images": [dict(r) for r in rows]},
    )
