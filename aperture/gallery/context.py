"""What every gallery response shares.

The templates and their globals, the request's language, the public base URL,
and the CORS JSON helper the API answers with.
"""

from __future__ import annotations

from functools import partial

from fastapi import Request
from fastapi.responses import JSONResponse

from .. import brand, branding, i18n, templating, theme
from ..runtime import settings


# Every route that hands out a derived or configured FILE (thumbs, previews,
# originals, album fonts and icons, wallpapers, brand assets, the generated
# theme sheets) sends this. A year is safe because none of those URLs is
# ambiguous: a photo route is keyed on the photo's own path, and the generated
# ones carry a `?v=` stamp derived from the source file's mtime, so a change
# produces a different URL rather than a stale hit. HTML is the exception and
# gets no-store — see the security_headers middleware in gallery/app.py.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000"}


# ----- language (EN / DE / JP) -------------------------------------------
# The site is served in three languages. The `lang` cookie (set via the
# nav selector -> /lang/{code}) wins; first-time visitors fall back to
# their Accept-Language header, then English. Album descriptions live in
# per-language markdown files (album_en.md / album_de.md / album_jp.md,
# see albums.album_description); UI strings come from i18n.py.
def request_lang(request: Request) -> str:
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
    lang = request_lang(request)
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
        # Who this archive belongs to (gallery.cfg — see aperture/branding.py).
        # Per request rather than a template global because one of its values,
        # the meta description, is localized.
        "brand": branding.site_brand(lang),
    }


# ----- request facts ------------------------------------------------------
def public_base_url(request: Request) -> str:
    if settings.public_base_url:
        return settings.public_base_url
    return str(request.base_url).rstrip("/")


def is_mobile_request(request: Request) -> bool:
    """Phone detection for the welcome_mobile/_desktop split. MDN's
    recommended heuristic: 'Mobi' anywhere in the User-Agent — catches
    iPhones and Android phones; Android tablets (no 'Mobi') and iPads in
    desktop mode deliberately get the desktop feed."""
    return "mobi" in request.headers.get("user-agent", "").lower()


def json_cors(payload, max_age: int = 300, vary: str | None = None) -> JSONResponse:
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


# ----- templates ----------------------------------------------------------
templates = templating.make_templates(templating.WEB_DIR, context_processors=[_i18n_context])

templates.env.globals["app_version"] = brand.VERSION

# Who BUILT the gallery. Fixed, config-free, and the counterpart to the
# per-request `brand` context (the OPERATOR's name, logo and links) that
# _i18n_context injects above.
templates.env.globals["vendor"] = brand.CONTEXT
templates.env.globals["public_base_url"] = public_base_url
templates.env.globals["site_font"] = theme.site_font
templates.env.globals["site_bg"] = theme.site_bg
templates.env.globals["theme_css_url"] = theme.theme_css_url
