"""Gallery Configurator -- a standalone editor for the gallery's cfg files.

Runs on its own, next to (not inside) the gallery: it only needs the photos
folder mounted. Nothing here imports the gallery app, touches its database, or
expects it to be running -- the gallery re-reads gallery.cfg and album.cfg per
request, so a save made here shows up on its next page load.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import cfgio, schema, validate
from .library import Library, is_image

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def _default(env: str, in_container: str, beside: str) -> Path:
    """Env var wins. Without one, use the container path when it exists and
    otherwise a folder next to the checkout -- so `uvicorn configurator.app.main:app`
    works straight out of the repo, without a compose file."""
    raw = os.environ.get(env)
    if raw:
        return Path(raw).resolve()
    if Path(in_container).is_dir():
        return Path(in_container).resolve()
    return (PROJECT_DIR.parent / beside).resolve()


PHOTOS_DIR = _default("PHOTOS_DIR", "/photos", "photos")
DATA_DIR = _default("DATA_DIR", "/data", "configurator/data")
CACHE_DIR = DATA_DIR / "thumbcache"
BACKUP_DIR = DATA_DIR / "backups"
READ_ONLY = os.environ.get("READ_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "320"))
BACKUPS = int(os.environ.get("BACKUPS", "20"))
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024

try:  # HEIC support is optional -- the tool works without it, minus previews
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - depends on the wheel being installed
    pass

from PIL import Image, ImageOps

app = FastAPI(title="Gallery Configurator", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

lib = Library(PHOTOS_DIR)


# ----- helpers ----------------------------------------------------------
def _guard_write() -> None:
    if READ_ONLY:
        raise HTTPException(403, "the configurator is mounted read-only")


def _album_or_400(album: str) -> str:
    """Validate an album path and hand back its normalized form."""
    album = (album or "").replace("\\", "/").strip().strip("/")
    try:
        target = lib.safe(album)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not target.is_dir():
        raise HTTPException(404, "no such album: %r" % album)
    return album


def _backup(path: Path, label: str) -> None:
    """Keep a copy of a file before overwriting it.

    Backups live in this tool's own data volume rather than beside the
    original: the photos share is the gallery's input, and dropping `.bak`
    files into `.album/` would leave junk the gallery has to ignore.
    """
    if not path.is_file():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    folder = BACKUP_DIR / ("%s-%s" % (label, slug))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(path, folder / ("%s-%s" % (stamp, path.name)))
    except OSError:
        return
    keep = sorted(folder.iterdir(), reverse=True)[:BACKUPS]
    for stale in sorted(folder.iterdir(), reverse=True)[BACKUPS:]:
        if stale not in keep:
            stale.unlink(missing_ok=True)


def _updates_from(payload: dict) -> dict[str, list[str] | None]:
    """Normalize a {key: value} patch from the client into {key: [values]|None}.

    `null` removes a key; a string becomes a one-entry list; a list stays one.
    Empty strings and empty lists also remove -- in this GUI "cleared the
    field" always means "drop the line", never "write an empty key".
    """
    updates: dict[str, list[str] | None] = {}
    for key, value in (payload or {}).items():
        key = str(key).strip().lower()
        if key not in schema.KEY_SPEC:
            raise HTTPException(400, "unknown config key: %r" % key)
        if value is None:
            updates[key] = None
        elif isinstance(value, bool):
            updates[key] = ["true"] if value else ["false"]
        elif isinstance(value, (int, float)):
            updates[key] = [str(value)]
        elif isinstance(value, str):
            updates[key] = [value] if value.strip() else None
        elif isinstance(value, list):
            items = [str(v).strip() for v in value]
            items = [v for v in items if v]
            updates[key] = items or None
        else:
            raise HTTPException(400, "bad value for %r" % key)
    return updates


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "expected a JSON body")
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    return body


# ----- page -------------------------------------------------------------
@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "photos_dir": PHOTOS_DIR.as_posix(),
        "read_only": READ_ONLY,
    })


@app.get("/api/meta")
def api_meta():
    """Everything the UI needs to build its forms: the key list, the write
    style and allowed values per key, and the help text."""
    return {
        "photos_dir": PHOTOS_DIR.as_posix(),
        "read_only": READ_ONLY,
        "album_keys": schema.ALBUM_KEYS,
        "gallery_keys": schema.GALLERY_KEYS,
        "spec": schema.KEY_SPEC,
        "help": schema.HELP,
        "langs": schema.LANGS,
        "effects": schema.EFFECTS,
        "reel_values": schema.REEL_VALUES,
        "photo_sorts": schema.PHOTO_SORTS,
        "gallery_album_sorts": schema.GALLERY_ALBUM_SORTS,
        "welcome_keywords": schema.WELCOME_KEYWORDS,
        "icon_exts": sorted(schema.ICON_EXTS),
        "font_exts": sorted(schema.FONT_EXTS),
        "font_scale_range": list(schema.FONT_SCALE_RANGE),
    }


# ----- tree -------------------------------------------------------------
@app.get("/api/tree")
def api_tree():
    if not PHOTOS_DIR.is_dir():
        raise HTTPException(500, "PHOTOS_DIR %s is not a directory" % PHOTOS_DIR)
    tree = lib.tree()
    gallery_cfg = lib.gallery_cfg_path()
    return {
        "root": tree.as_dict(),
        "gallery_cfg": gallery_cfg.is_file(),
        "albums": lib.album_paths(),
    }


# ----- album ------------------------------------------------------------
@app.get("/api/album")
def api_album(path: str = ""):
    album = _album_or_400(path)
    cfg_file = cfgio.CfgFile.load(lib.cfg_path(album))
    values = cfg_file.values()
    return {
        "album": album,
        "name": album.rsplit("/", 1)[-1] or "photos",
        "exists": lib.cfg_path(album).is_file(),
        "values": values,
        "raw": cfg_file.text(),
        "issues": validate.check_album(lib, album, values),
        "assets": lib.assets(album),
        "descriptions": {lang: lib.read_desc(album, lang) for lang in schema.LANGS},
        "photo_count": len(lib.photos(album, recursive=True)),
        "own_count": len(lib.photos(album)),
    }


@app.put("/api/album/cfg")
async def api_album_cfg(request: Request):
    _guard_write()
    body = await _json_body(request)
    album = _album_or_400(body.get("album", ""))
    updates = _updates_from(body.get("values", {}))
    path = lib.cfg_path(album)
    cfg_file = cfgio.CfgFile.load(path)
    cfg_file.apply(updates, schema.KEY_SPEC)
    _backup(path, "album")
    cfg_file.save(path)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_album(lib, album, values)}


@app.put("/api/album/raw")
async def api_album_raw(request: Request):
    _guard_write()
    body = await _json_body(request)
    album = _album_or_400(body.get("album", ""))
    text = body.get("raw")
    if not isinstance(text, str):
        raise HTTPException(400, "expected a `raw` string")
    path = lib.cfg_path(album)
    _backup(path, "album")
    cfg_file = cfgio.CfgFile(text)
    cfg_file.save(path)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_album(lib, album, values)}


@app.delete("/api/album/cfg")
def api_album_cfg_delete(path: str = ""):
    _guard_write()
    album = _album_or_400(path)
    cfg_path = lib.cfg_path(album)
    _backup(cfg_path, "album")
    cfg_path.unlink(missing_ok=True)
    return {"ok": True}


@app.put("/api/album/description")
async def api_album_description(request: Request):
    _guard_write()
    body = await _json_body(request)
    album = _album_or_400(body.get("album", ""))
    lang = str(body.get("lang", "")).strip().lower()
    if lang not in schema.LANGS:
        raise HTTPException(400, "unknown language: %r" % lang)
    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(400, "expected a `text` string")
    _backup(lib.desc_path(album, lang), "desc")
    lib.write_desc(album, lang, text)
    return {"ok": True, "text": lib.read_desc(album, lang)}


# ----- gallery.cfg ------------------------------------------------------
@app.get("/api/gallery")
def api_gallery():
    path = lib.gallery_cfg_path()
    cfg_file = cfgio.CfgFile.load(path, cfgio.GROUP_KEYS)
    values = cfg_file.values()
    return {
        "exists": path.is_file(),
        "values": values,
        "raw": cfg_file.text(),
        "issues": validate.check_gallery(lib, values),
        "albums": lib.album_paths(),
    }


@app.put("/api/gallery/cfg")
async def api_gallery_cfg(request: Request):
    _guard_write()
    body = await _json_body(request)
    updates = _updates_from(body.get("values", {}))
    path = lib.gallery_cfg_path()
    cfg_file = cfgio.CfgFile.load(path, cfgio.GROUP_KEYS)
    cfg_file.apply(updates, schema.KEY_SPEC)
    _backup(path, "gallery")
    cfg_file.save(path)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_gallery(lib, values)}


@app.put("/api/gallery/raw")
async def api_gallery_raw(request: Request):
    _guard_write()
    body = await _json_body(request)
    text = body.get("raw")
    if not isinstance(text, str):
        raise HTTPException(400, "expected a `raw` string")
    path = lib.gallery_cfg_path()
    _backup(path, "gallery")
    cfg_file = cfgio.CfgFile(text, cfgio.GROUP_KEYS)
    cfg_file.save(path)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_gallery(lib, values)}


# ----- photos -----------------------------------------------------------
@app.get("/api/photos")
def api_photos(path: str = "", recursive: int = 1, limit: int = 5000):
    album = _album_or_400(path) if path else ""
    photos = lib.photos(album, recursive=bool(recursive))
    return {"album": album, "total": len(photos), "photos": photos[:limit]}


@app.get("/api/thumb")
def api_thumb(path: str, size: int = 0):
    """A downscaled JPEG of one photo, cached on disk by path+mtime+size."""
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not source.is_file() or not is_image(source.name):
        raise HTTPException(404, "no such photo")
    size = max(64, min(size or THUMB_SIZE, 1600))
    st = source.stat()
    token = "%s|%s|%s|%s" % (path, st.st_mtime_ns, st.st_size, size)
    cached = CACHE_DIR / (hashlib.sha1(token.encode("utf-8")).hexdigest() + ".jpg")
    if not cached.is_file():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(source) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((size, size), Image.LANCZOS)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=82, optimize=True)
        except Exception:
            raise HTTPException(415, "cannot decode %s" % source.name)
        tmp = cached.with_suffix(".tmp%d" % os.getpid())
        tmp.write_bytes(buf.getvalue())
        tmp.replace(cached)
    return FileResponse(cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


# ----- .album assets ----------------------------------------------------
@app.get("/api/asset")
def api_asset(path: str, name: str):
    """Serve one file out of an album's .album/ folder, for icon previews and
    for loading a title font into the UI."""
    album = _album_or_400(path)
    if Path(name).name != name:
        raise HTTPException(400, "asset names are bare filenames")
    target = lib.meta_dir(album) / name
    if not target.is_file():
        raise HTTPException(404, "no such asset")
    ext = target.suffix.lower()
    types = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
             ".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff",
             ".woff2": "font/woff2"}
    if ext not in types:
        raise HTTPException(415, "not a servable asset type")
    return FileResponse(target, media_type=types[ext],
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/asset")
async def api_asset_upload(path: str = Form(...), file: UploadFile = File(...)):
    """Drop an icon or a title font into an album's .album/ folder."""
    _guard_write()
    album = _album_or_400(path)
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if not name or ext not in (schema.ICON_EXTS | schema.FONT_EXTS):
        raise HTTPException(400, "only %s are accepted"
                            % ", ".join(sorted(schema.ICON_EXTS | schema.FONT_EXTS)))
    payload = await file.read(MAX_UPLOAD + 1)
    if len(payload) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than %d MB" % (MAX_UPLOAD // 1048576))
    meta = lib.meta_dir(album)
    meta.mkdir(parents=True, exist_ok=True)
    target = meta / name
    _backup(target, "asset")
    target.write_bytes(payload)
    kind = "icon" if ext in schema.ICON_EXTS else "font"
    return {"ok": True, "name": name, "kind": kind, "assets": lib.assets(album)}


@app.delete("/api/asset")
def api_asset_delete(path: str, name: str):
    _guard_write()
    album = _album_or_400(path)
    if Path(name).name != name:
        raise HTTPException(400, "asset names are bare filenames")
    ext = Path(name).suffix.lower()
    if ext not in (schema.ICON_EXTS | schema.FONT_EXTS):
        raise HTTPException(400, "only icons and fonts can be deleted here")
    target = lib.meta_dir(album) / name
    if not target.is_file():
        raise HTTPException(404, "no such asset")
    _backup(target, "asset")
    target.unlink()
    return {"ok": True, "assets": lib.assets(album)}


# ----- whole-gallery check ----------------------------------------------
@app.get("/api/validate")
def api_validate():
    started = time.monotonic()
    issues = validate.check_all(lib)
    return {
        "issues": issues,
        "errors": sum(1 for i in issues if i["level"] == "error"),
        "warnings": sum(1 for i in issues if i["level"] == "warn"),
        "took_ms": int((time.monotonic() - started) * 1000),
    }


@app.get("/api/health")
def api_health():
    return {"ok": PHOTOS_DIR.is_dir(), "photos_dir": PHOTOS_DIR.as_posix(),
            "read_only": READ_ONLY}


@app.exception_handler(ValueError)
def _value_error(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)
