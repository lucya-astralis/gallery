"""The gallery app.

Middleware, the error page, static files, and the routers. aperture.server
serves this on the public port; uvicorn can serve it on its own as
`aperture.gallery.app:app`.
"""

from __future__ import annotations

import logging
import mimetypes
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import brand, compress, health, indexer, templating
from ..runtime import ensure_dirs, settings
from . import api, context, media, pages, shortlinks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("aperture.gallery")

ensure_dirs()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Process start / stop: the indexer runs for as long as this app is
    served. See aperture/indexer.py. The self-watch runs beside it."""
    health.start()
    indexer.startup()
    yield
    indexer.shutdown()


app = FastAPI(title=brand.PRODUCT, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)

# Python's mimetypes table has no entry for the web font formats, so
# StaticFiles fell back to `text/plain; charset=utf-8` for every .woff2 —
# which is what base.html's `<link rel=preload as=font type="font/woff2">`
# is then compared against, and a preload whose type doesn't match what
# arrives is thrown away and fetched a second time. Registering them here,
# before the mount, is also what tells compress.py not to gzip a font.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")

app.mount("/static", StaticFiles(directory=str(templating.WEB_DIR / "static")), name="static")

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


# ----- failsafe -----------------------------------------------------------
# When the storage is gone (aperture/health.py), every request gets a 503 that
# says so -- a page for people, JSON for the API, a bare status for images --
# built from memory, because the disk may be what is missing. 503 with
# Retry-After is also what tells a CDN and a crawler "come back later" rather
# than "this site is broken": Cloudflare serves a cached copy where it has
# one, and a search engine does not drop pages for it.
#
# Defined BEFORE security_headers on purpose: the later registration is the
# outer one, so the failsafe answer still leaves with the CSP and the rest.
FAILSAFE_OPEN = ("/healthz", "/_failsafe.css")


def _failsafe_response(request: Request) -> Response:
    path = request.url.path
    headers = {"Retry-After": str(health.RETRY_AFTER), "Cache-Control": "no-store"}
    if path == "/api" or path.startswith("/api/"):
        resp = context.json_cors({"error": "storage unavailable", "status": 503,
                                  "failsafe": True, "retry_after": health.RETRY_AFTER}, max_age=0)
        resp.status_code = 503
        resp.headers.update(headers)
        return resp
    if path.startswith(("/thumb/", "/preview/", "/full/", "/static/")):
        return Response(status_code=503, headers=headers)
    return HTMLResponse(health.failsafe_page(context.request_lang(request)),
                        status_code=503, headers=headers)


@app.middleware("http")
async def failsafe(request: Request, call_next):
    if health.is_down() and request.url.path not in FAILSAFE_OPEN:
        return _failsafe_response(request)
    try:
        return await call_next(request)
    except (OSError, sqlite3.DatabaseError) as exc:
        # Found out between two probes: the visitor who noticed first gets
        # the explanation too, and the monitor looks at once. Anything that
        # is not the ground going away stays the 500 it always was.
        await run_in_threadpool(health.suspect, exc)
        if not settings.failsafe or not health.is_storage_error(exc):
            raise
        log.warning("%s %s: %s -- answered from failsafe",
                    request.method, request.url.path, health.describe(exc))
        return _failsafe_response(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """For an uptime monitor, a load balancer or a Docker healthcheck: 200
    while the gallery can serve, 503 while it is in failsafe. Says which
    check failed, never where anything lives."""
    snap = health.snapshot()
    body = {
        "status": snap["state"],
        "failsafe": snap["failsafe"],
        "since": snap["since"],
        "checked_at": snap["checked_at"],
        "checks": {c["key"]: c["level"] for c in snap["checks"]},
        "version": brand.VERSION,
    }
    resp = context.json_cors(body, max_age=0)
    if snap["failsafe"]:
        resp.status_code = 503
        resp.headers["Retry-After"] = str(health.RETRY_AFTER)
    return resp


@app.get("/_failsafe.css", include_in_schema=False)
def failsafe_css():
    return Response(health.FAILSAFE_CSS, media_type="text/css",
                    headers={"Cache-Control": "public, max-age=3600"})


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
    # Which software served this — the header half of the attribution the
    # footer carries in words (aperture/brand.py). Not a security header; it sits
    # here because this is the one place every response passes through.
    response.headers.setdefault("X-Powered-By", f"{brand.PRODUCT}/{brand.VERSION}")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "interest-cohort=(), browsing-topics=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return response


# Added AFTER security_headers on purpose: the last middleware registered is
# the outermost one, so compression sees the finished response — headers and
# all — and appends its own `Vary: Accept-Encoding` to the Vary that the
# language handling above already set. Photos are left alone (see compress.py).
app.add_middleware(compress.CompressMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # API clients get JSON (with the CORS headers they need to read it),
    # never the HTML 404 page
    if request.url.path == "/api" or request.url.path.startswith("/api/"):
        resp = context.json_cors({"error": str(exc.detail), "status": exc.status_code}, max_age=0)
        resp.status_code = exc.status_code
        return resp
    if exc.status_code == 404:
        return context.templates.TemplateResponse(
            request, "404.html",
            {"path": request.url.path},
            status_code=404,
        )
    return Response(content=str(exc.detail), status_code=exc.status_code)


# ----- routers -----------------------------------------------------------
app.include_router(pages.router)
app.include_router(api.router)
app.include_router(media.router)
# pretty links, all under /s/ — see gallery/shortlinks.py
app.include_router(shortlinks.router)
