"""The Console -- Aperture's operator surface, on its own port.

Was the standalone Configurator. It is now part of the same package as the
gallery and shares its settings, but it is still a SEPARATE ASGI app on a
SEPARATE listener, and that separation is the point: the public port serves
pages and never reaches a route in this module, and this port is where the one
write path into the photo tree lives.

Two invariants hold whatever else changes here:

  * it writes ONLY inside `<album>/.album/` and `photos/.gallery/` -- a
    photograph is not addressable through any route below;
  * it never writes the index. Operational requests (scan, pause) go through
    the control channel in `aperture/control.py`, the same one the CLI uses,
    so the indexer stays the single writer on the database.

The gallery re-reads gallery.cfg and album.cfg per request, so a save made
here shows up on its next page load with nothing to restart.
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import brand
from ..paths import (PathRefused, relative_to_photos, sidecar_target,
                     writable_target)
from ..runtime import settings
from .. import cfgio, schema
from . import imagemeta, opsapi, security, validate
from .library import Library, asset_kinds, is_image

# The console ships with the app now, so it carries the app's version rather
# than one of its own -- there is no combination of the two to report.
APP_VERSION = brand.VERSION

BASE_DIR = Path(__file__).resolve().parent

# One environment, read once, in aperture/runtime.py. The console used to have
# its own resolution rules for the same variable names, which meant PHOTOS_DIR
# could point at two different folders depending on which process you asked.
PHOTOS_DIR = settings.photos_dir
DATA_DIR = settings.data_dir
# The gallery's own thumbnail tree. Nothing breaks without it -- previews just
# get generated here instead. (Phase 04 drops the fallback cache entirely and
# reads the index.)
THUMBS_DIR = settings.thumbs_dir
CACHE_DIR = settings.console_dir / "thumbcache"
BACKUP_DIR = settings.backup_dir
READ_ONLY = settings.console_read_only
THUMB_SIZE = settings.console_thumb_size
BACKUPS = settings.console_backups
MAX_UPLOAD = settings.console_max_upload

try:  # HEIC support is optional -- the tool works without it, minus previews
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - depends on the wheel being installed
    pass

from PIL import Image, ImageOps

# A 64 MP ceiling on anything decoded here. Pillow's own default is ~89 MP and
# only warns; a metadata folder holds icons and backdrops, so nothing
# legitimate comes close and a crafted file should not get to allocate for it.
Image.MAX_IMAGE_PIXELS = 64 * 1024 * 1024

app = FastAPI(title=f"{brand.PRODUCT} console", docs_url=None, redoc_url=None,
              openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

lib = Library(PHOTOS_DIR)

# The operations surface: status, scan, pause/resume, doctor. Its own module
# because it shares nothing with the cfg editor below except this app and the
# door in front of it — it reads the index and writes the control channel,
# never the photo tree.
app.include_router(opsapi.router)

# Authentication and CSRF for every route on this app, in one place. Declared
# here rather than per-route so a route added later is closed by default: the
# open list in security.py is a short, explicit set, and anything not on it
# needs a session.
app.middleware("http")(security.guard)


@app.middleware("http")
async def console_headers(request: Request, call_next):
    """The gallery's header set, plus no-store on everything.

    An operator surface has no business in a cache — not the browser's, not a
    proxy's. The rest is the same policy the public side sends, because the
    console is the same kind of document with more at stake.
    """
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return response


# Same shape as the gallery's, with one addition: `sandbox` on nothing here,
# but `media-src`/`font-src` are needed because the console previews the
# wallpapers and title faces an operator uploads.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self'; "
    "font-src 'self'; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _target(album: str | None, name: str, *, scope: str = "album",
            allowed_exts: set[str] | None = None) -> Path:
    """Every write in this module resolves its path here.

    `lib.cfg_path()` and friends still say WHICH file a route means; this says
    whether that file may be written, and it is the only thing that does. A
    PathRefused becomes a 400 with the reason, because the reasons are things
    an operator can act on ("that is a reserved device name") rather than
    internal detail.
    """
    try:
        return writable_target(PHOTOS_DIR, album, name, scope=scope,
                               allowed_exts=allowed_exts)
    except PathRefused as exc:
        raise HTTPException(400, str(exc))


def _cfg_target(album: str) -> Path:
    return _target(album, schema.ALBUM_CFG_NAME)


def _desc_target(album: str, lang: str) -> Path:
    return _target(album, "album_%s.md" % lang)


def _writes(request: Request, action: str, target: Path, before: str | None) -> None:
    """Record one completed write. Called after the file is on disk, with the
    hash it had beforehand, so the log says what changed and not merely that
    something did."""
    security.audit(request, action, relative_to_photos(PHOTOS_DIR, target),
                   before=before, after=security.sha256_of(target))


def _static_url(path: str) -> str:
    """`/static/<path>` stamped with the file's mtime.

    Without this a browser keeps serving the app.js it cached before an
    update, and the UI silently runs last week's code against this week's API.
    """
    try:
        stamp = int((BASE_DIR / "static" / path).stat().st_mtime)
    except OSError:
        return "/static/%s" % path
    return "/static/%s?v=%d" % (path, stamp)


templates.env.globals["static_url"] = _static_url


# ----- helpers ----------------------------------------------------------
def _guard_write() -> None:
    if READ_ONLY:
        raise HTTPException(403, "the console is mounted read-only")


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


def _gallery_thumb(rel: str, source_mtime: float) -> Path | None:
    """The gallery's own thumbnail for a photo, when it has one that is not
    stale. Mirrors the gallery's layout: THUMBS_DIR holds the photo tree with
    every file re-suffixed to .jpg."""
    if not THUMBS_DIR.is_dir():
        return None
    try:
        candidate = (THUMBS_DIR / rel).with_suffix(".jpg").resolve()
        candidate.relative_to(THUMBS_DIR.resolve())
    except (ValueError, OSError):
        return None
    try:
        if candidate.is_file() and candidate.stat().st_mtime >= source_mtime:
            return candidate
    except OSError:
        return None
    return None


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


# ----- the door ---------------------------------------------------------
@app.get("/login")
def login_page(request: Request, next: str = "/"):
    """The one page a signed-out visitor can see. Deliberately its own
    document rather than a modal on the app: nothing of the console — not the
    album tree, not the photo counts, not the mount path — renders before
    there is a session."""
    if security.open_access() or security.current(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "vendor": brand.CONTEXT,
    })


@app.get("/api/session")
def api_session(request: Request):
    """Who this browser is, and the CSRF token its next write must carry."""
    return security.state(request)


@app.post("/api/session")
async def api_session_open(request: Request):
    if security.open_access():
        raise HTTPException(409, "this console has no password set")
    body = await _json_body(request)
    password = body.get("password")
    if not isinstance(password, str) or not password:
        raise HTTPException(400, "expected a `password`")
    sid, sess = security.login(request, password)
    response = JSONResponse({"ok": True, "csrf": sess.csrf})
    security.issue_cookie(response, request, sid)
    return response


@app.delete("/api/session")
def api_session_close(request: Request):
    security.logout(request)
    response = JSONResponse({"ok": True})
    security.clear_cookie(response)
    return response


# ----- page -------------------------------------------------------------
@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "photos_dir": PHOTOS_DIR.as_posix(),
        "read_only": READ_ONLY,
        "app_version": APP_VERSION,
        # "open" means no password is configured. The UI says so out loud —
        # an unauthenticated console should never look like an authenticated
        # one (see security.assert_safe_binding).
        "auth_mode": "open" if security.open_access() else "password",
    })


@app.get("/api/meta")
def api_meta():
    """Everything the UI needs to build its forms: the key list, the write
    style and allowed values per key, and the help text."""
    return {
        "version": APP_VERSION,
        "photos_dir": PHOTOS_DIR.as_posix(),
        "shared_thumbs": THUMBS_DIR.is_dir(),
        "read_only": READ_ONLY,
        "album_keys": schema.ALBUM_KEYS,
        "gallery_keys": schema.GALLERY_KEYS,
        "spec": schema.KEY_SPEC,
        "help": schema.HELP,
        # The theme keys are spelled the same in both files but mean one tier
        # down on the gallery tab — a file in .gallery/ rather than in an
        # .album/, the whole site rather than one album. Only the differences
        # travel; the UI falls through to `spec`/`help` for everything else.
        "gallery_spec": schema.GALLERY_SPEC,
        "gallery_help": schema.GALLERY_HELP,
        "langs": schema.LANGS,
        "effects": schema.EFFECTS,
        "reel_values": schema.REEL_VALUES,
        "photo_sorts": schema.PHOTO_SORTS,
        "gallery_album_sorts": schema.GALLERY_ALBUM_SORTS,
        "welcome_keywords": schema.WELCOME_KEYWORDS,
        "icon_exts": sorted(schema.ICON_EXTS),
        "font_exts": sorted(schema.FONT_EXTS),
        "wallpaper_exts": sorted(schema.WALLPAPER_EXTS),
        "wallpaper_image_exts": sorted(schema.WALLPAPER_IMAGE_EXTS),
        # what .gallery/ accepts: the marks, plus the face and the backdrop
        # the site's own theme block names there
        "brand_exts": sorted(schema.GALLERY_EXTS),
        "gallery_meta_dir": schema.GALLERY_META_DIR,
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
    path = _cfg_target(album)
    before = security.sha256_of(path)
    cfg_file = cfgio.CfgFile.load(path)
    cfg_file.apply(updates, schema.KEY_SPEC)
    _backup(path, "album")
    cfg_file.save(path)
    _writes(request, "album.cfg", path, before)
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
    path = _cfg_target(album)
    before = security.sha256_of(path)
    _backup(path, "album")
    cfg_file = cfgio.CfgFile(text)
    cfg_file.save(path)
    _writes(request, "album.cfg (raw)", path, before)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_album(lib, album, values)}


@app.delete("/api/album/cfg")
def api_album_cfg_delete(request: Request, path: str = ""):
    _guard_write()
    album = _album_or_400(path)
    cfg_path = _cfg_target(album)
    before = security.sha256_of(cfg_path)
    _backup(cfg_path, "album")
    cfg_path.unlink(missing_ok=True)
    _writes(request, "album.cfg deleted", cfg_path, before)
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
    target = _desc_target(album, lang)
    before = security.sha256_of(target)
    _backup(target, "desc")
    lib.write_desc(album, lang, text)
    _writes(request, "description (%s)" % lang, target, before)
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
        # what the logo / favicon / portrait pickers can offer
        "assets": lib.brand_assets(),
    }


@app.put("/api/gallery/cfg")
async def api_gallery_cfg(request: Request):
    _guard_write()
    body = await _json_body(request)
    updates = _updates_from(body.get("values", {}))
    path = _target(None, schema.GALLERY_CFG_NAME, scope="gallery")
    before = security.sha256_of(path)
    cfg_file = cfgio.CfgFile.load(path, cfgio.GROUP_KEYS)
    cfg_file.apply(updates, schema.KEY_SPEC)
    _backup(path, "gallery")
    cfg_file.save(path)
    _writes(request, "gallery.cfg", path, before)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_gallery(lib, values),
            "assets": lib.brand_assets()}


@app.put("/api/gallery/raw")
async def api_gallery_raw(request: Request):
    _guard_write()
    body = await _json_body(request)
    text = body.get("raw")
    if not isinstance(text, str):
        raise HTTPException(400, "expected a `raw` string")
    path = _target(None, schema.GALLERY_CFG_NAME, scope="gallery")
    before = security.sha256_of(path)
    _backup(path, "gallery")
    cfg_file = cfgio.CfgFile(text, cfgio.GROUP_KEYS)
    cfg_file.save(path)
    _writes(request, "gallery.cfg (raw)", path, before)
    values = cfg_file.values()
    return {"ok": True, "values": values, "raw": cfg_file.text(),
            "issues": validate.check_gallery(lib, values),
            "assets": lib.brand_assets()}


# ----- photos -----------------------------------------------------------
@app.get("/api/photos")
def api_photos(path: str = "", recursive: int = 0, limit: int = 5000,
               tags: int = 0):
    """Photos in one folder, plus its sub-folders so a picker can drill in.

    `recursive` defaults to off: a trip album holds hundreds of photos across a
    dozen sub-folders, and flattening that into one wall is exactly what makes
    picking a cover painful. `tags=1` also returns each photo's sidecar tags.
    """
    album = _album_or_400(path) if path else ""
    photos = lib.photos(album, recursive=bool(recursive))
    shown = photos[:limit]
    if tags:
        for photo in shown:
            photo["tags"] = lib.read_tags(photo["rel"])
    return {
        "album": album,
        "parent": album.rsplit("/", 1)[0] if "/" in album else ("" if album else None),
        "folders": lib.folders(album),
        "total": len(photos),
        "photos": shown,
    }


@app.get("/api/thumb")
def api_thumb(path: str, size: int = 0):
    """A small JPEG of one photo.

    The gallery already renders a thumbnail per photo into THUMBS_DIR, so when
    that folder is mounted this hands the existing file straight back -- no
    decode of a 20 MB original just to draw a 200px tile. Only a photo the
    gallery has not thumbed yet (or a stale one) falls through to Pillow, and
    that result is cached under DATA_DIR so it happens once.
    """
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not source.is_file() or not is_image(source.name):
        raise HTTPException(404, "no such photo")
    size = max(64, min(size or THUMB_SIZE, 1600))
    st = source.stat()

    shared = _gallery_thumb(path, st.st_mtime)
    if shared is not None:
        return FileResponse(shared, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400",
                                     "X-Thumb-Source": "gallery"})

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
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-Thumb-Source": "generated"})


# ----- per-image metadata and tags --------------------------------------
@app.get("/api/image")
def api_image(path: str):
    """One photo: its read-only EXIF summary and its editable tags."""
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not source.is_file() or not is_image(source.name):
        raise HTTPException(404, "no such photo")
    rel = path.replace("\\", "/").strip().strip("/")
    return {
        "rel": rel,
        "name": source.name,
        "album": rel.rsplit("/", 1)[0] if "/" in rel else "",
        "meta": imagemeta.read(source),
        "tags": lib.read_tags(rel),
        "has_sidecar": lib.tags_path(rel).is_file(),
    }


@app.get("/api/tags")
def api_tags():
    """Every tag in use, with its photo count -- the vocabulary the tag input
    autocompletes against, so the same idea does not end up spelled three
    ways across an album."""
    counts = lib.all_tags()
    return {
        "tags": [{"name": name, "count": count}
                 for name, count in sorted(counts.items(),
                                           key=lambda kv: (-kv[1], kv[0].lower()))],
        "total": len(counts),
    }


@app.put("/api/tags")
async def api_tags_write(request: Request):
    """Apply a tag change to one or many photos.

    `set` replaces each photo's tags outright; `add` and `remove` edit them in
    place, which is what bulk tagging needs -- selecting forty photos should
    add a tag without flattening whatever else each one already carries.
    """
    _guard_write()
    body = await _json_body(request)
    rels = body.get("photos")
    if not isinstance(rels, list) or not rels:
        raise HTTPException(400, "expected a non-empty `photos` list")
    if len(rels) > 5000:
        raise HTTPException(413, "too many photos in one call")

    def clean(field: str) -> list[str]:
        raw = body.get(field)
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise HTTPException(400, "`%s` must be a list" % field)
        return [str(t).strip() for t in raw if str(t).strip()]

    replace = body.get("set")
    add, remove = clean("add"), clean("remove")
    lowered_remove = {t.lower() for t in remove}

    results: dict[str, list[str]] = {}
    for raw_rel in rels:
        rel = str(raw_rel).replace("\\", "/").strip().strip("/")
        # sidecar_target proves the photo exists and IS a photograph, then
        # builds the sidecar's name from it. Nothing about the file written
        # here comes from the request except which picture it belongs to.
        try:
            sidecar = sidecar_target(PHOTOS_DIR, rel, is_image=is_image)
        except PathRefused as exc:
            raise HTTPException(400, str(exc))

        if replace is not None:
            if not isinstance(replace, list):
                raise HTTPException(400, "`set` must be a list")
            wanted = [str(t).strip() for t in replace if str(t).strip()]
        else:
            wanted = lib.read_tags(rel)
            have = {t.lower() for t in wanted}
            wanted = [t for t in wanted if t.lower() not in lowered_remove]
            for tag in add:
                if tag.lower() not in have:
                    wanted.append(tag)
                    have.add(tag.lower())
        before = security.sha256_of(sidecar)
        _backup(sidecar, "tags")
        results[rel] = lib.write_tags(rel, wanted)
        _writes(request, "tags", sidecar, before)

    return {"ok": True, "changed": len(results), "tags": results}


# ----- .album assets ----------------------------------------------------
# Content types for everything the .album/ folder can hold. Derived from the
# schema whitelists rather than hand-listed: this map used to be its own
# hardcoded set and silently fell behind when wallpapers were added, so a
# .jpg backdrop answered 415 and never previewed.
_ASSET_TYPES = schema.MIME
_MISSING_TYPES = (schema.ICON_EXTS | schema.FONT_EXTS | schema.WALLPAPER_EXTS
                  | schema.BRAND_EXTS) - set(_ASSET_TYPES)
assert not _MISSING_TYPES, "no content type for %s" % sorted(_MISSING_TYPES)


# ----- what a file claims to be, and what it is -------------------------
# The extension allowlist says which KIND of file may land in a metadata
# folder; these signatures say whether the bytes agree. Both are needed: an
# HTML page named `icon.png` passes the first check and fails the second, and
# it is the second that decides what a browser does with it.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".png":   (bytes.fromhex("89504e470d0a1a0a"),),
    ".jpg":   (bytes.fromhex("ffd8ff"),),
    ".jpeg":  (bytes.fromhex("ffd8ff"),),
    ".gif":   (b"GIF87a", b"GIF89a"),
    ".otf":   (b"OTTO",),
    ".ttf":   (bytes.fromhex("00010000"), b"true", b"ttcf"),
    ".woff":  (b"wOFF",),
    ".woff2": (b"wOF2",),
    ".webm":  (bytes.fromhex("1a45dfa3"),),
}
# These carry their marker at a fixed offset rather than at the start.
_TAGGED = {".webp": (b"RIFF", 8, b"WEBP"),
           ".avif": (None, 4, b"ftyp"),
           ".mp4":  (None, 4, b"ftyp")}


def _sniff(name: str, payload: bytes) -> None:
    """Refuse a file whose bytes do not match its extension.

    SVG is the one that cannot be checked this way -- it is XML, so the test
    is that it parses as one and starts with an SVG or XML element. That does
    not make an SVG safe by itself; the CSP the console and the gallery both
    send is what stops script inside one from running.
    """
    ext = Path(name).suffix.lower()
    if not payload:
        raise HTTPException(400, "the uploaded file is empty")

    if ext == ".svg":
        head = payload[:512].lstrip(bytes.fromhex("efbbbf")).lstrip()
        if not (head.startswith(b"<?xml") or head.startswith(b"<svg")
                or head.startswith(b"<!DOCTYPE svg")):
            raise HTTPException(415, "that does not look like an SVG")
        return

    if ext in _TAGGED:
        prefix, offset, marker = _TAGGED[ext]
        ok = payload[offset:offset + len(marker)] == marker
        if prefix is not None:
            ok = ok and payload.startswith(prefix)
        if not ok:
            raise HTTPException(415, "the file's contents are not %s" % ext[1:].upper())
        return

    magic = _SIGNATURES.get(ext)
    if magic and not any(payload.startswith(m) for m in magic):
        raise HTTPException(415, "the file's contents are not %s" % ext[1:].upper())


# Assets live in one of two folders, and every route below takes the same
# `scope` to say which: an album's own `.album/`, or the gallery-wide
# `.gallery/` holding the logo, the operator's portrait, the footer badges,
# and the site's own display face and backdrop. `path` is only read in the
# album scope.
_SCOPES = ("album", "gallery")


def _asset_dir(scope: str, path: str) -> Path:
    if scope not in _SCOPES:
        raise HTTPException(400, "scope must be one of %s" % ", ".join(_SCOPES))
    if scope == "gallery":
        return lib.gallery_meta_dir()
    return lib.meta_dir(_album_or_400(path))


def _asset_listing(scope: str, path: str) -> list[dict]:
    return lib.brand_assets() if scope == "gallery" else lib.assets(_album_or_400(path))


@app.get("/api/asset")
def api_asset(path: str = "", name: str = "", scope: str = "album"):
    """Serve one file out of an album's .album/ folder — icon and wallpaper
    previews, loading a title font into the UI — or out of the gallery's
    .gallery/, which is where its own mark and badges sit."""
    if Path(name).name != name:
        raise HTTPException(400, "asset names are bare filenames")
    target = _asset_dir(scope, path) / name
    if not target.is_file():
        raise HTTPException(404, "no such asset")
    ext = target.suffix.lower()
    if ext not in _ASSET_TYPES:
        raise HTTPException(415, "not a servable asset type")
    return FileResponse(target, media_type=_ASSET_TYPES[ext],
                        headers={"Cache-Control": "no-cache"})


# Icons, title fonts and page wallpapers all live side by side in .album/.
# The gallery folder holds the same three roles one tier down — its face, its
# backdrop — plus the marks and badges that only exist there, so its whitelist
# is the wider of the two rather than a different one.
_ASSET_EXTS = schema.ICON_EXTS | schema.FONT_EXTS | schema.WALLPAPER_EXTS
_SCOPE_EXTS = {"album": _ASSET_EXTS, "gallery": schema.GALLERY_EXTS}


@app.post("/api/asset")
async def api_asset_upload(request: Request, path: str = Form(""),
                           file: UploadFile = File(...),
                           scope: str = Form("album")):
    """Drop an icon, a title font or a page wallpaper into an album's
    .album/ folder — or a logo, a portrait, a badge, or the site's own face
    or backdrop into the gallery's .gallery/."""
    _guard_write()
    if scope not in _SCOPES:
        raise HTTPException(400, "scope must be one of %s" % ", ".join(_SCOPES))
    album = _album_or_400(path) if scope == "album" else None
    # The filename is the only thing on this route that a client chooses, so
    # it is the only thing that has to be proved. _target refuses a name that
    # is a path, a device, an alternate data stream or the wrong kind of file,
    # and refuses a destination reached through a symlink.
    name = Path(file.filename or "").name
    target = _target(album, name, scope=scope, allowed_exts=_SCOPE_EXTS[scope])

    payload = await file.read(MAX_UPLOAD + 1)
    if len(payload) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than %d MB" % (MAX_UPLOAD // 1048576))
    _sniff(name, payload)

    before = security.sha256_of(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _backup(target, "asset")
    target.write_bytes(payload)
    _writes(request, "asset uploaded", target, before)
    kinds = ["brand"] if scope == "gallery" else asset_kinds(name)
    return {"ok": True, "name": name, "kinds": kinds, "kind": kinds[0],
            "assets": _asset_listing(scope, path)}


@app.delete("/api/asset")
def api_asset_delete(request: Request, path: str = "", name: str = "",
                     scope: str = "album"):
    _guard_write()
    if scope not in _SCOPES:
        raise HTTPException(400, "scope must be one of %s" % ", ".join(_SCOPES))
    album = _album_or_400(path) if scope == "album" else None
    target = _target(album, name, scope=scope, allowed_exts=_SCOPE_EXTS[scope])
    if not target.is_file():
        raise HTTPException(404, "no such asset")
    before = security.sha256_of(target)
    _backup(target, "asset")
    target.unlink()
    _writes(request, "asset deleted", target, before)
    return {"ok": True, "assets": _asset_listing(scope, path)}


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
def api_health(request: Request):
    """A liveness probe, and the ONE route that answers without a session —
    so it says only what a container orchestrator needs. The mount path, the
    album counts and whether a thumbnail tree is shared used to be in here;
    they are facts about the deployment and now live behind the door, in
    /api/meta."""
    payload = {"ok": PHOTOS_DIR.is_dir(), "version": APP_VERSION,
               "auth": "open" if security.open_access() else "password"}
    if security.current(request) or security.open_access():
        payload.update(photos_dir=PHOTOS_DIR.as_posix(),
                       shared_thumbs=THUMBS_DIR.is_dir(),
                       read_only=READ_ONLY)
    return payload


@app.exception_handler(ValueError)
def _value_error(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def create_console_app() -> FastAPI:
    """The console as server.py wants it.

    A factory rather than a bare import because of the first line: the binding
    check can end the process, and it has to do that BEFORE a socket exists,
    not after the first request finds out there is no password.
    """
    security.assert_safe_binding()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return app
