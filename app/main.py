import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, scanner, watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("main")

PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "./photos")).resolve()
THUMBS_DIR = Path(os.environ.get("THUMBS_DIR", "./thumbnails")).resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "480"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "0"))  # seconds; 0 disables periodic rescan
ENABLE_WATCHER = os.environ.get("ENABLE_WATCHER", "1") not in ("0", "false", "False", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
HIDE_GPS = os.environ.get("HIDE_GPS", "1") not in ("0", "false", "False", "")

_scan_lock = threading.Lock()
_scan_running = threading.Event()

try:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass  # read-only mount is fine
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="imageslucya")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _periodic_scan_loop():
    while True:
        time.sleep(SCAN_INTERVAL)
        if not _scan_lock.acquire(blocking=False):
            continue
        try:
            _scan_running.set()
            result = scanner.full_scan(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE)
            if result["indexed"] or result["thumbnails"] or result["removed"]:
                log.info("periodic scan: %s", result)
        except Exception as e:
            log.warning("periodic scan failed: %s", e)
        finally:
            _scan_running.clear()
            _scan_lock.release()


@app.on_event("startup")
def _startup():
    db.init(DATA_DIR)
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds",
        PHOTOS_DIR, THUMBS_DIR, DATA_DIR, THUMB_SIZE, ENABLE_WATCHER, SCAN_INTERVAL,
    )
    threading.Thread(
        target=lambda: scanner.full_scan(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE),
        daemon=True,
    ).start()
    if ENABLE_WATCHER:
        try:
            watcher.start(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE)
        except Exception as e:
            log.warning("watcher failed to start: %s", e)
    if SCAN_INTERVAL > 0:
        threading.Thread(target=_periodic_scan_loop, daemon=True).start()
        log.info("periodic rescan every %d seconds", SCAN_INTERVAL)


def require_admin(x_admin_token: str | None = Header(default=None)):
    if not ADMIN_TOKEN:
        raise HTTPException(403, "admin features disabled (set ADMIN_TOKEN to enable)")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(401, "invalid or missing admin token")
    return True


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
def index(request: Request):
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
        },
    )


def _prettify_exif(exif: dict) -> list[tuple[str, str]]:
    if not exif:
        return []
    keys = [
        ("Make", "Kamera-Hersteller"),
        ("Model", "Kamera-Modell"),
        ("LensModel", "Objektiv"),
        ("DateTimeOriginal", "Aufnahmedatum"),
        ("ExposureTime", "Belichtungszeit"),
        ("FNumber", "Blende"),
        ("ISOSpeedRatings", "ISO"),
        ("FocalLength", "Brennweite"),
        ("FocalLengthIn35mmFilm", "Brennweite (KB)"),
        ("Flash", "Blitz"),
        ("WhiteBalance", "Weißabgleich"),
        ("ExposureProgram", "Belichtungsprogramm"),
        ("MeteringMode", "Messmethode"),
        ("Orientation", "Orientierung"),
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


@app.get("/full/{album}/{filename:path}")
def serve_full(album: str, filename: str):
    rel = _safe_rel(album, filename).as_posix()
    src = PHOTOS_DIR / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(src), headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/api/auth/status")
def api_auth_status(x_admin_token: str | None = Header(default=None)):
    if not ADMIN_TOKEN:
        return {"admin_required": False, "authenticated": False, "admin_enabled": False}
    authed = bool(x_admin_token and hmac.compare_digest(x_admin_token, ADMIN_TOKEN))
    return {"admin_required": True, "authenticated": authed, "admin_enabled": True}


@app.post("/api/scan")
def api_scan(_: bool = Depends(require_admin)):
    if _scan_running.is_set():
        raise HTTPException(429, "scan already in progress")
    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(429, "scan already in progress")
    try:
        _scan_running.set()
        result = scanner.full_scan(PHOTOS_DIR, THUMBS_DIR, THUMB_SIZE)
    finally:
        _scan_running.clear()
        _scan_lock.release()
    return JSONResponse(result)


@app.post("/api/image/{album}/{filename}/tags")
def api_set_tags(album: str, filename: str, tags: str = Form(""), _: bool = Depends(require_admin)):
    rel = _safe_rel(album, filename).as_posix()
    c = db.conn()
    row = c.execute("SELECT id FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if not row:
        raise HTTPException(404, "image not found")
    image_id = row["id"]
    names = [t.strip() for t in tags.split(",") if t.strip()]
    seen: set[str] = set()
    dedup: list[str] = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(n)
    with db.lock():
        c.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
        for name in dedup:
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            c.execute("INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)", (image_id, tag_id))
        c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)")
        c.commit()
    return JSONResponse({"tags": dedup})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    q = q.strip()
    c = db.conn()
    if not q:
        return RedirectResponse("/")
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
