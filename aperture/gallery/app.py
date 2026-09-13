"""The gallery app.

Middleware, the error page, static files, and the routers. aperture.server
serves this on the public port; uvicorn can serve it on its own as
`aperture.gallery.app:app`.
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import brand, compress, indexer, templating
from ..runtime import ensure_dirs
from . import api, context, media, pages, shortlinks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

ensure_dirs()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Process start / stop: the indexer runs for as long as this app is
    served. See aperture/indexer.py."""
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
# LAST, always: its `/{slug}` would otherwise answer for every one-segment
# route registered after it. See gallery/shortlinks.py.
app.include_router(shortlinks.router)
