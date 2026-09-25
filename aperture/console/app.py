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
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import markdown
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import brand, checks, colors, config, db, palette, scanner, search, theme, update_check
from ..paths import (PathRefused, relative_to_photos, sidecar_target,
                     writable_target)
from ..runtime import settings
from .. import cfgio, links, schema, templating
from . import imagemeta, opsapi, security
from .library import Library, asset_kinds

BASE_DIR = Path(__file__).resolve().parent

try:  # HEIC support is optional -- the tool works without it, minus previews
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - depends on the wheel being installed
    pass

from PIL import Image

# A 64 MP ceiling on anything decoded here. Pillow's own default is ~89 MP and
# only warns; a metadata folder holds icons and backdrops, so nothing
# legitimate comes close and a crafted file should not get to allocate for it.
Image.MAX_IMAGE_PIXELS = 64 * 1024 * 1024

app = FastAPI(title=f"{brand.PRODUCT} console", docs_url=None, redoc_url=None,
              openapi_url=None)
# The icon sheet is the gallery's too, generated next to the glyph subset it
# names (tools/build_fa_subset.py scans both surfaces). Served from where it is
# built for the same reason as the fonts below; a route, because a mount
# cannot hold one file, and declared BEFORE /static so it wins.
@app.get("/static/fa-icons.css", include_in_schema=False)
def fa_icons():
    return FileResponse(templating.WEB_DIR / "static" / "fa-icons.css",
                        media_type="text/css")


# The maker's picture on the Changelog place. It ships with the gallery's
# static files; a route for the same reason as the icon sheet above, and
# declared before /static for the same reason too.
MAKER_PFP = templating.WEB_DIR / "static" / "pfp.webp"


@app.get("/static/maker.webp", include_in_schema=False)
def maker_pfp():
    if not MAKER_PFP.is_file():
        raise HTTPException(404, "no picture")
    return FileResponse(MAKER_PFP, media_type="image/webp")


# The console's chrome is the gallery's chrome, down to the same nine font
# files. They are served out of the gallery's static/ rather than copied
# here: a copy of a font is a copy that goes stale quietly, and both
# surfaces have shipped in one package since 1.0. Mounted BEFORE /static
# so the longer path wins -- Starlette matches routes in order.
app.mount("/static/fonts",
          StaticFiles(directory=templating.WEB_DIR / "static" / "fonts"),
          name="fonts")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = templating.make_templates(BASE_DIR)

lib = Library(settings.photos_dir)

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
        return writable_target(settings.photos_dir, album, name, scope=scope,
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
    security.audit(request, action, relative_to_photos(settings.photos_dir, target),
                   before=before, after=security.sha256_of(target))


# ----- helpers ----------------------------------------------------------
def _guard_write() -> None:
    if settings.console_read_only:
        raise HTTPException(403, "the console is mounted read-only")


def _album_or_400(album: str) -> str:
    """Validate an album path and hand back its normalized form.

    An empty album is refused rather than resolved. It used to pass: "" strips
    to "", `lib.safe("")` hands back the photos root, the root IS a directory,
    so a request that named no album opened a form for
    `photos/.album/album.cfg` -- a file the gallery never reads, in the folder
    the operator hands us as input. Only `paths.writable_target()` stopped the
    save, which made the read routes promise something the write routes then
    refused. The gallery-wide settings have their own routes (/api/gallery/*)
    and their own asset scope.
    """
    album = (album or "").replace("\\", "/").strip().strip("/")
    if not album:
        raise HTTPException(400, "an album is required: the root of the photo share "
                                 "is not an album — the gallery-wide settings live "
                                 "under /api/gallery")
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
    folder = settings.backup_dir / ("%s-%s" % (label, slug))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(path, folder / ("%s-%s" % (stamp, path.name)))
    except OSError:
        return
    keep = sorted(folder.iterdir(), reverse=True)[:settings.console_backups]
    for stale in sorted(folder.iterdir(), reverse=True)[settings.console_backups:]:
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


# ----- the door ---------------------------------------------------------
# Why the operator is looking at the door, as an ALLOWLIST. Landing on a
# sign-in page with no explanation reads as the tool having thrown you out
# for no reason, and the commonest cause — half an hour away from the desk —
# is the one worth naming. The query string is attacker-controlled and this
# text renders on an UNAUTHENTICATED page, so nothing from it is ever echoed:
# the parameter only picks a key here, and an unknown key says nothing at all.
LOGIN_REASONS = {
    "timeout": "That session had been idle for %d minutes, so it ended."
               % (security.IDLE_TIMEOUT // 60),
    "signout": "Signed out. The console is closed until you sign in again.",
    "expired": "That session reached its twelve-hour limit and ended.",
    "password": "The console password was changed, which ends every session. "
                "Sign in with the new one.",
}


# ----- the console's look ---------------------------------------------------
# The console wears the SITE's accent (gallery.cfg `accent`, never an album's)
# and the palettes derived from it -- the same sheet the gallery generates,
# from the same function -- and its backdrop, the `nova` SVG, is turned to the
# same hue. Both are open before sign-in, because the door is the same room.
# `?accent=#hex` asks for a preview of an unsaved colour: nothing is read from
# or written to a file for it, the hex is parsed to three ints like any cfg
# value, and the answer is never cached.
NOVA_CUTS = {"nova-wide-16-9.svg", "nova-square.svg"}
NO_STORE = {"Cache-Control": "no-store"}
LOOK_CACHE = {"Cache-Control": "no-cache"}


def _look_accent(accent: str | None) -> dict | None:
    if accent is not None:
        rgb = theme.parse_hex_color(accent)
        return None if rgb is None else theme.accent_shades(rgb)
    return theme.page_accent(None)


def look_version() -> int:
    """Cache stamp for the console's look: gallery.cfg's mtime."""
    try:
        return int(config.GALLERY_CFG_PATH.stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["look_version"] = look_version


@app.get("/theme.css")
def console_theme_css(accent: str | None = None):
    shades = _look_accent(accent)
    css = theme.accent_sheet(shades, theme.accent_decls(shades) if shades else [])
    return Response(css, media_type="text/css",
                    headers=NO_STORE if accent is not None else LOOK_CACHE)


@app.get("/bg/{name}")
def console_backdrop(name: str, accent: str | None = None):
    if name not in NOVA_CUTS:
        raise HTTPException(404, "not found")
    svg = (BASE_DIR / "static" / "bg" / name).read_text(encoding="utf-8")
    shades = _look_accent(accent)
    if shades is not None:
        svg = palette.recolour_svg(svg, shades["_rgb"])
    return Response(svg, media_type="image/svg+xml",
                    headers=NO_STORE if accent is not None else LOOK_CACHE)


@app.get("/login")
def login_page(request: Request, reason: str = ""):
    """The one page a signed-out visitor can see. Deliberately its own
    document rather than a modal on the app: nothing of the console — not the
    album tree, not the photo counts, not the mount path — renders before
    there is a session.

    It took a `next` until 1.3.0. The console has no router — no pushState, no
    hash, `/` is its only URL — so the parameter could never carry anything
    but `/`, which made it an open-redirect sink standing open for no benefit.
    What is actually lost across the door is which SCREEN you were on, and
    that is remembered on the console's side of it (app.js, the return note).
    """
    if security.open_access() or security.current(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "vendor": brand.CONTEXT,
        # the same stamp the console's footer carries, because behind a
        # password the footer is not reachable yet
        "app_version": brand.VERSION,
        "reason": LOGIN_REASONS.get(reason, ""),
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
# Every place the console has is an address of its own, so Back, a reload and
# a bookmark land where they were. They are all the one page: the client
# reads the path and draws the place. Listed rather than caught with a
# wildcard, so an unknown path is still a 404 and /login, /api and /static
# can never be shadowed by it. The door guards them like any other route.
@app.get("/")
@app.get("/library")
@app.get("/library/{rest:path}")
@app.get("/albums")
@app.get("/albums/{rest:path}")
@app.get("/tags")
@app.get("/site")
@app.get("/links")
@app.get("/system")
@app.get("/system/{rest:path}")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "photos_dir": settings.photos_dir.as_posix(),
        "read_only": settings.console_read_only,
        "app_version": brand.VERSION,
        # "open" means no password is configured. The UI says so out loud —
        # an unauthenticated console should never look like an authenticated
        # one (see security.assert_safe_binding).
        "auth_mode": "open" if security.open_access() else "password",
        "palette": config.site_palette(),
    })


@app.get("/api/meta")
def api_meta():
    """Everything the UI needs to build its forms: the key list, the write
    style and allowed values per key, and the help text."""
    return {
        "version": brand.VERSION,
        "photos_dir": settings.photos_dir.as_posix(),
        "read_only": settings.console_read_only,
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
        # the colour names the Library's facet offers, in wheel order
        "color_names": colors.NAMES,
        # the identity tint the console wears, the same one the gallery does
        "palette": config.site_palette(),
        "welcome_keywords": list(schema.WELCOME_KEYWORDS),
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
# ----- about ------------------------------------------------------------
# The Changelog place: the release notes this build ships with, where the code
# lives and who makes it. CHANGELOG.md sits next to the package -- the image
# copies it there (see the Dockerfile) -- and is rendered here rather than in
# the browser, which under this app's CSP runs no script but its own.
_HEADING = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_RELEASE = re.compile(r"(\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})")
_RELATIVE_HREF = re.compile(r'href="(?!https?:|#|mailto:)([^"]+)"')


def _release_notes(text: str) -> list[dict]:
    """CHANGELOG.md -> [{version, date, html}], in the file's order (newest
    first). Only a `## X.Y.Z — date` heading is a release; any other `##`
    heading ends the release above it without being one. A link relative to
    the repository points at it on GitHub, where it resolves."""
    headings = list(_HEADING.finditer(text))
    out: list[dict] = []
    for i, heading in enumerate(headings):
        release = _RELEASE.fullmatch(heading.group(1))
        if not release:
            continue
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        # the rule that separates one entry from the next is not part of it
        body = re.sub(r"\n-{3,}\s*$", "", text[heading.end():end].strip()).strip()
        html = markdown.markdown(body, extensions=["extra", "sane_lists"], output_format="html5")
        html = _RELATIVE_HREF.sub(
            lambda m: 'href="%s/blob/main/%s"' % (brand.REPO_URL, m.group(1)), html)
        out.append({"version": release.group(1), "date": release.group(2), "html": html})
    return out


@app.get("/api/about")
def api_about():
    """What the Changelog place shows. Behind the door like every route here."""
    try:
        text = update_check.CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return {
        "product": brand.PRODUCT,
        "version": brand.VERSION,
        "repo": brand.REPO_URL,
        "maker": {
            "name": brand.MAKER_NAME,
            "url": brand.MAKER_URL,
            "pfp": "/static/maker.webp" if MAKER_PFP.is_file() else None,
        },
        # the label it ships under, and the design language it wears
        "vendor": {"name": brand.NAME, "url": brand.URL},
        "design": {"name": brand.DESIGN_NAME, "url": brand.DESIGN_URL},
        "releases": _release_notes(text),
    }


@app.get("/api/updates")
def api_updates(fresh: bool = False):
    """Whether images.lucya.sh runs a newer aperture (aperture/update_check.py). The
    server asks, not the page: the console's CSP connects nowhere but here,
    and one cached answer serves every open tab. `fresh` is the Check again
    button, honoured at most once a minute."""
    return update_check.status(fresh=fresh)


@app.get("/api/tree")
def api_tree():
    if not settings.photos_dir.is_dir():
        raise HTTPException(500, "PHOTOS_DIR %s is not a directory" % settings.photos_dir)
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
        "issues": checks.album(album, values),
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
            "issues": checks.album(album, values)}


@app.post("/api/album/cfg/preview")
async def api_album_cfg_preview(request: Request):
    """What a save WOULD write, without writing it: the file before and
    after, and what the checks would say about the result. The same parser
    and the same apply() the save runs, so the diff the console shows is the
    diff that lands."""
    body = await _json_body(request)
    album = _album_or_400(body.get("album", ""))
    cfg_file = cfgio.CfgFile.load(lib.cfg_path(album))
    before = cfg_file.text()
    cfg_file.apply(_updates_from(body.get("values", {})), schema.KEY_SPEC)
    return {"before": before, "after": cfg_file.text(),
            "issues": checks.album(album, cfg_file.values())}


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
            "issues": checks.album(album, values)}


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
        "issues": checks.gallery(values),
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
            "issues": checks.gallery(values),
            "assets": lib.brand_assets()}


@app.post("/api/gallery/cfg/preview")
async def api_gallery_cfg_preview(request: Request):
    """gallery.cfg's dry run -- see api_album_cfg_preview."""
    body = await _json_body(request)
    cfg_file = cfgio.CfgFile.load(lib.gallery_cfg_path(), cfgio.GROUP_KEYS)
    before = cfg_file.text()
    cfg_file.apply(_updates_from(body.get("values", {})), schema.KEY_SPEC)
    return {"before": before, "after": cfg_file.text(),
            "issues": checks.gallery(cfg_file.values())}


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
            "issues": checks.gallery(values),
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
    # The one route where the photo root IS a legitimate scope: the picker
    # starts there and drills in. Normalized before the gate, so "/" means
    # the root exactly like "" does.
    album = path.replace(chr(92), "/").strip().strip("/")
    album = _album_or_400(album) if album else ""
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
def api_thumb(path: str):
    """The gallery's own grid thumbnail of one photo, built first when it is
    missing or older than the photo.

    It is the file /thumb serves visitors, out of the same tree and from the
    same encoder, so whichever of the two asks first builds it for both. Every
    size the console draws a photo at fits inside THUMB_SIZE; there is no
    second tier to keep."""
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    rel = relative_to_photos(settings.photos_dir, source)
    # A metadata folder holds marks and backdrops, not photos; a thumbnail of
    # one would land in the gallery's tree as an orphan.
    if not source.is_file() or not schema.is_image(source.name) or scanner.is_meta_path(Path(rel)):
        raise HTTPException(404, "no such photo")
    thumb = scanner.ensure_thumb(settings.photos_dir, settings.thumbs_dir, rel, settings.thumb_size)
    if thumb is None:
        raise HTTPException(415, "cannot decode %s" % source.name)
    return FileResponse(thumb, media_type=schema.MIME[scanner.THUMB_EXT],
                        headers={"Cache-Control": "private, max-age=86400"})


# ----- per-image metadata and tags --------------------------------------
@app.get("/api/image")
def api_image(path: str):
    """One photo: its read-only EXIF summary and its editable tags."""
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not source.is_file() or not schema.is_image(source.name):
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
            sidecar = sidecar_target(settings.photos_dir, rel, is_image=schema.is_image)
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


def _retag(request: Request, change) -> dict:
    """Run `change(tags) -> tags` over every sidecar in the tree and write
    back the ones it changed. The tag manager's one write path: each file
    still goes through sidecar_target, gets a backup and an audit line, so a
    rename across four hundred photos is four hundred ordinary tag writes."""
    _guard_write()
    changed: list[str] = []
    for rel, _path in list(lib.sidecars()):
        try:
            sidecar = sidecar_target(settings.photos_dir, rel, is_image=schema.is_image)
        except PathRefused:
            continue   # an orphan sidecar: its photo is gone, nothing to retag
        have = lib.read_tags(rel)
        want = change(have)
        if want == have:
            continue
        before = security.sha256_of(sidecar)
        _backup(sidecar, "tags")
        lib.write_tags(rel, want)
        _writes(request, "tags", sidecar, before)
        changed.append(rel)
    return {"ok": True, "changed": len(changed), "photos": changed}


def _tag_name(body: dict, field: str) -> str:
    raw = body.get(field)
    name = str(raw).strip() if isinstance(raw, str) else ""
    if not name:
        raise HTTPException(400, "expected a tag in `%s`" % field)
    if "," in name or "\n" in name:
        raise HTTPException(400, "a tag cannot contain a comma or a line break")
    return name


@app.post("/api/tags/rename")
async def api_tags_rename(request: Request):
    """Rename a tag on every photo that carries it. Renaming onto a tag that
    already exists is a merge: a photo with both keeps one."""
    body = await _json_body(request)
    old, new = _tag_name(body, "from"), _tag_name(body, "to")

    def change(tags: list[str]) -> list[str]:
        if not any(t.lower() == old.lower() for t in tags):
            return tags
        out: list[str] = []
        for tag in tags:
            tag = new if tag.lower() == old.lower() else tag
            if tag.lower() not in {t.lower() for t in out}:
                out.append(tag)
        return out

    return _retag(request, change)


@app.post("/api/tags/delete")
async def api_tags_delete(request: Request):
    """Take a tag off every photo. A sidecar left with nothing in it is
    removed, as lib.write_tags always does."""
    body = await _json_body(request)
    name = _tag_name(body, "tag")
    return _retag(request, lambda tags: [t for t in tags if t.lower() != name.lower()])


# ----- the Library ------------------------------------------------------
@app.get("/api/library")
def api_library():
    """Every photo, with what the index knows about it and what its sidecar
    says right now.

    The FILES and the TAGS come off disk in one walk (lib.catalog), because
    the index is a scan behind and a tag written a second ago has to show. The
    capture facts come from the index, read-only -- they are what a scan
    extracted, and extracting them again here would be a second indexer. A
    photo the index has not seen yet is listed with `indexed: false`."""
    photos, tags = lib.catalog()
    facts: dict[str, dict] = {}
    try:
        for row in db.conn().execute(
                "SELECT rel_path, width, height, taken_at, camera, lens, iso, aperture, "
                "focal, is_showcase, colors, palette FROM images"):
            facts[row["rel_path"]] = dict(row)
    except sqlite3.Error:
        facts = {}
    out = []
    for photo in photos:
        fact = facts.get(photo["rel"])
        out.append({
            **photo,
            "tags": tags.get(photo["rel"], []),
            "indexed": fact is not None,
            "w": fact and fact["width"], "h": fact and fact["height"],
            "taken": fact and fact["taken_at"],
            "camera": fact and fact["camera"], "lens": fact and fact["lens"],
            "iso": fact and fact["iso"], "f": fact and fact["aperture"],
            "mm": fact and fact["focal"],
            "featured": bool(fact and fact["is_showcase"]),
            # colors.py: the named colours (the facet) and the strip
            "colors": colors.names_of(fact["colors"]) if fact else [],
            "palette": colors.strip_of(fact["palette"]) if fact else [],
        })
    return {"photos": out, "total": len(out), "indexed": len(facts)}


@app.get("/api/library/search")
def api_library_search(q: str = ""):
    """Which photos the gallery's own search grammar finds -- the same parser
    /search, /api/photos?q= and the CLI use (aperture/search.py), over the
    index. The Library filters its own list by the answer; the filters that
    are the console's alone (a tag in a sidecar, an album, a state) it applies
    itself."""
    query = search.parse(q)
    cond, params = search.condition(query, "i")
    try:
        rows = db.conn().execute(f"SELECT i.rel_path FROM images i WHERE {cond}", params).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(503, "the index cannot be read: %s" % exc)
    return {
        "matches": [row["rel_path"] for row in rows],
        "words": query.text,
        "filters": [{"label": f.label, "ok": f.ok} for f in query.filters],
    }


@app.get("/api/preview")
def api_preview(path: str):
    """The gallery's large preview of one photo -- what the Library's loupe
    shows. Built first when missing, into the same tree the gallery serves
    it from; never the original."""
    try:
        source = lib.safe(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    rel = relative_to_photos(settings.photos_dir, source)
    if not source.is_file() or not schema.is_image(source.name) or scanner.is_meta_path(Path(rel)):
        raise HTTPException(404, "no such photo")
    preview = scanner.ensure_thumb(settings.photos_dir, settings.previews_dir, rel,
                                   settings.preview_size, ext=scanner.PREVIEW_EXT)
    if preview is None:
        raise HTTPException(415, "cannot decode %s" % source.name)
    return FileResponse(preview, media_type=schema.MIME[scanner.PREVIEW_EXT],
                        headers={"Cache-Control": "private, max-age=86400"})


# ----- .album assets ----------------------------------------------------
# Every type that folder can hold has to be one the schema can name a
# content type for. The console kept its own hardcoded map once and it
# silently fell behind when wallpapers were added: a .jpg backdrop
# answered 415 and never previewed.
_MISSING_TYPES = (schema.ICON_EXTS | schema.FONT_EXTS | schema.WALLPAPER_EXTS
                  | schema.BRAND_EXTS) - set(schema.MIME)
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
    if ext not in schema.MIME:
        raise HTTPException(415, "not a servable asset type")
    return FileResponse(target, media_type=schema.MIME[ext],
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

    payload = await file.read(settings.console_max_upload + 1)
    if len(payload) > settings.console_max_upload:
        raise HTTPException(413, "file is larger than %d MB" % (settings.console_max_upload // 1048576))
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


# ----- pretty links -----------------------------------------------------
# photos/.gallery/links.cfg — `name = album` or `name = album/photo.jpg`, one
# line per link. What a link is and how the gallery answers one lives in
# aperture/links.py; here it is only edited, through the same resolver, backup
# and audit line as gallery.cfg, into the same folder.
def _links_target() -> Path:
    return _target(None, schema.LINKS_CFG_NAME, scope="gallery")


def _links_payload(cfg_file: cfgio.CfgFile) -> dict:
    """The list as the Links screen draws it. Built from the parse the caller
    holds — after a write, the file it has just saved — for the same reason
    checks.album() takes one: a same-size edit inside one mtime tick would
    otherwise read back as the version before it."""
    values = cfg_file.values()
    issues = checks.pretty_links(values)
    entries = links.entries(values)
    for entry in entries:
        entry["issues"] = [i for i in issues if i["key"] == entry["slug"]]
    return {
        "exists": links.PATH.is_file(),
        "links": entries,
        "issues": issues,
        # The console is on its own port and cannot know the address visitors
        # use; PUBLIC_BASE_URL is what the gallery already calls it.
        "base": settings.public_base_url or None,
        "prefix": links.PREFIX,
        "slug_max": links.SLUG_MAX,
    }


@app.get("/api/links")
def api_links():
    return _links_payload(cfgio.CfgFile.load(links.PATH))


@app.put("/api/links")
async def api_links_write(request: Request):
    """Create a link, re-point one, or rename one.

    `was` names the link being edited. Without it a name that already exists
    is a 409 rather than an overwrite: two links are made from two places in
    the UI, and the second one silently taking over the first is exactly the
    kind of edit nobody meant."""
    _guard_write()
    body = await _json_body(request)
    slug = links.normalize_slug(body.get("slug"))
    was = links.normalize_slug(body.get("was")) or None
    target = links.normalize_target(body.get("target"))
    problem = links.slug_problem(slug) or links.target_problem(target)
    if problem:
        raise HTTPException(400, problem)

    path = _links_target()
    cfg_file = (cfgio.CfgFile.load(path) if path.is_file()
                else cfgio.CfgFile(links.HEADER))
    if slug != was and cfg_file.has(slug):
        raise HTTPException(409, "%s already points at %r — edit that link instead"
                            % (links.address(slug), cfgio.joined(cfg_file.values(), slug)))

    before = security.sha256_of(path)
    if was and was != slug:
        cfg_file.unset(was)
        action = "link renamed (%s -> %s)" % (links.address(was), links.address(slug))
    else:
        action = "link %s (%s)" % ("changed" if cfg_file.has(slug) else "added",
                                   links.address(slug))
    cfg_file.set(slug, [target])
    _backup(path, "links")
    cfg_file.save(path)
    _writes(request, action, path, before)
    return _links_payload(cfg_file)


@app.delete("/api/links")
def api_links_delete(request: Request, slug: str = ""):
    _guard_write()
    slug = links.normalize_slug(slug)
    path = _links_target()
    cfg_file = cfgio.CfgFile.load(path)
    if not slug or not cfg_file.has(slug):
        raise HTTPException(404, "no such link: %r" % slug)
    before = security.sha256_of(path)
    _backup(path, "links")
    cfg_file.unset(slug)
    cfg_file.save(path)
    _writes(request, "link removed (%s)" % links.address(slug), path, before)
    return _links_payload(cfg_file)


# ----- what changed here ------------------------------------------------
@app.get("/api/audit")
def api_audit(limit: int = 40):
    """The console's own writes, newest first -- the log every save already
    wrote, finally readable. Behind the door like everything else: it names
    files, addresses and sessions."""
    return {"entries": security.recent(max(1, min(limit, 200)))}


# ----- history: the backups every save already keeps --------------------
# Every overwrite drops the previous version into data/console/backups
# (_backup). These routes make that visible: the versions of one file, one
# version's text, and putting a version back -- which is itself an ordinary
# write, backed up and audited, so a restore can be undone the same way.
_HISTORY_ID = re.compile(r"\d{8}T\d{6}Z-[^/\\]+")


def _history_file(file: str, album: str | None, lang: str | None) -> tuple[Path, str]:
    """(the file on disk, its backup label) for the three kinds that keep
    one: an album.cfg, gallery.cfg, and an album's description."""
    if file == "gallery":
        return _target(None, schema.GALLERY_CFG_NAME, scope="gallery"), "gallery"
    album = _album_or_400(album or "")
    if file == "album":
        return _cfg_target(album), "album"
    if file == "desc":
        lang = str(lang or "").strip().lower()
        if lang not in schema.LANGS:
            raise HTTPException(400, "unknown language: %r" % lang)
        return _desc_target(album, lang), "desc"
    raise HTTPException(400, "expected file = album, gallery or desc")


def _backup_folder(path: Path, label: str) -> Path:
    # the same folder _backup writes into
    slug = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    return settings.backup_dir / ("%s-%s" % (label, slug))


def _version(folder: Path, version: str) -> Path:
    if not _HISTORY_ID.fullmatch(version or ""):
        raise HTTPException(400, "not a version id")
    found = folder / version
    if not found.is_file():
        raise HTTPException(404, "no such version")
    return found


@app.get("/api/history")
def api_history(file: str, album: str | None = None, lang: str | None = None):
    path, label = _history_file(file, album, lang)
    folder = _backup_folder(path, label)
    versions = []
    if folder.is_dir():
        for entry in sorted(folder.iterdir(), reverse=True):
            if not _HISTORY_ID.fullmatch(entry.name):
                continue
            stamp = entry.name[:16]
            versions.append({
                "id": entry.name,
                "saved": datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
                                 .replace(tzinfo=timezone.utc).isoformat(),
                "size": entry.stat().st_size,
            })
    current = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return {"file": relative_to_photos(settings.photos_dir, path), "current": current,
            "versions": versions}


@app.get("/api/history/version")
def api_history_version(file: str, version: str, album: str | None = None,
                        lang: str | None = None):
    path, label = _history_file(file, album, lang)
    found = _version(_backup_folder(path, label), version)
    return {"id": version, "text": found.read_text(encoding="utf-8", errors="replace")}


@app.post("/api/history/restore")
async def api_history_restore(request: Request):
    _guard_write()
    body = await _json_body(request)
    path, label = _history_file(str(body.get("file", "")), body.get("album"), body.get("lang"))
    found = _version(_backup_folder(path, label), str(body.get("version", "")))
    text = found.read_bytes()
    before = security.sha256_of(path)
    _backup(path, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    _writes(request, "%s restored" % path.name, path, before)
    return {"ok": True, "text": text.decode("utf-8", errors="replace")}


# ----- whole-gallery check ----------------------------------------------
@app.get("/api/validate")
def api_validate():
    started = time.monotonic()
    issues = checks.everything()
    return {
        "issues": issues,
        "errors": sum(1 for i in issues if i["level"] == "error"),
        "warnings": sum(1 for i in issues if i["level"] == "warn"),
        "took_ms": int((time.monotonic() - started) * 1000),
    }


@app.get("/api/health")
def api_health(request: Request):
    """A liveness probe, and the ONE route that answers without a session —
    so it says only what a container orchestrator needs. The mount path and
    the album counts used to be in here; they are facts about the deployment
    and now live behind the door, in /api/meta."""
    payload = {"ok": settings.photos_dir.is_dir(), "version": brand.VERSION,
               "auth": "open" if security.open_access() else "password"}
    if security.current(request) or security.open_access():
        payload.update(photos_dir=settings.photos_dir.as_posix(), read_only=settings.console_read_only)
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
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    return app
