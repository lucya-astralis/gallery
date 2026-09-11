"""Files.

Thumbnails, previews and originals -- behind the one guard that turns a URL
into a filesystem path -- and the theme, font, icon, wallpaper and brand
assets the cfg files name.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from .. import branding, cfgio, config, scanner, schema, theme
from ..runtime import settings
from . import context

router = APIRouter()


def safe_rel(album: str, filename: str) -> Path:
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
    # These routes serve a PHOTO. Without this the extension was never looked
    # at, and /full/ handed out any other file sitting in an album folder
    # verbatim — a `.tags` sidecar, a stray note. Nothing links to those and
    # nothing is meant to read them over HTTP.
    if not schema.is_image(rel):
        raise HTTPException(404, "not found")
    full = (settings.photos_dir / rel).resolve()
    try:
        full.relative_to(settings.photos_dir)
    except ValueError:
        raise HTTPException(400, "invalid path")
    return rel


def _theme_css_response(album: str | None):
    """Shared body of the two theme routes. The sheet only redefines tokens
    style.css already declares, so it can never introduce a rule — and every
    value is re-serialised from parsed numbers (three ints for a colour,
    floats for the filter), so nothing that came out of a cfg is ever printed
    into the CSS verbatim."""
    decls = theme.theme_decls(album)
    if not decls:
        raise HTTPException(404, "not found")
    return Response(":root{%s}" % ";".join(decls), media_type="text/css",
                    headers=context.IMMUTABLE)


@router.get("/site-theme.css")
def site_theme_css():
    """gallery.cfg's `wallpaper_tint` / `wallpaper_dim` for every page that
    isn't an album's — the site's own default backdrop is dressed here."""
    return _theme_css_response(None)


@router.get("/album-theme.css/{album:path}")
def album_theme_css(album: str):
    """An album's own `accent` / `wallpaper_tint` / `wallpaper_dim` — the CSP
    drops inline styles, so this is how per-album colour reaches the page
    (see the section on it above)."""
    return _theme_css_response(album)


@router.get("/album-font.css/{album:path}")
def album_font_css(album: str):
    """The @font-face + --album-title-font binding for an album's
    `font = …` (plus --album-title-scale for its `font_scale = …`), as a
    real stylesheet — the CSP drops inline styles, so this is how a
    per-album face reaches the page (see the section on it above). The
    album path is percent-encoded into the url() so a folder name can
    never break out of the CSS string; the scale is re-serialised from a
    validated float, so it cannot carry anything but a number either."""
    font = theme.album_font_file(album)
    if font is None:
        raise HTTPException(404, "not found")
    fmt, _mime = theme.ALBUM_FONT_TYPES[font.suffix.lower()]
    src = f"/album-font/{quote(album)}?v={theme.album_font_version(album)}"
    scale = theme.album_font_scale(album)
    root = f"--album-title-font:'{theme.ALBUM_FONT_FAMILY}'"
    if scale is not None:
        root += f";--album-title-scale:{scale:g}"
    css = (
        "@font-face{"
        f"font-family:'{theme.ALBUM_FONT_FAMILY}';"
        f"src:url('{src}') format('{fmt}');"
        "font-weight:400;font-style:normal;font-display:swap}"
        f":root{{{root}}}"
    )
    return Response(css, media_type="text/css",
                    headers=context.IMMUTABLE)


@router.get("/site-font.css")
def site_font_css():
    """The @font-face + --display-font binding for gallery.cfg's `font = …`
    (plus --display-scale for its `font_scale = …`). The album sheet's twin,
    one tier down: same reason it is a real stylesheet rather than an inline
    <style> (the CSP drops those), and the same re-serialisation guarantee —
    the family name is a constant and the scale is a validated float, so
    nothing that came out of a cfg is printed into the CSS. The url() needs no
    escaping either: the path carries no filename, only the version."""
    font = theme.site_font_file()
    if font is None:
        raise HTTPException(404, "not found")
    fmt, _mime = theme.ALBUM_FONT_TYPES[font.suffix.lower()]
    src = f"/site-font?v={config.gallery_asset_stamp(font)}"
    scale = theme.site_font_scale()
    # the stock stack stays behind it: a display face that has no glyph for
    # what the chrome is currently saying falls through rather than tofus
    root = (f"--display-font:'{theme.SITE_FONT_FAMILY}','Ethnocentric',"
            "'Space Grotesk',sans-serif")
    if scale is not None:
        root += f";--display-scale:{scale:g}"
    css = (
        "@font-face{"
        f"font-family:'{theme.SITE_FONT_FAMILY}';"
        f"src:url('{src}') format('{fmt}');"
        "font-weight:400;font-style:normal;font-display:swap}"
        f":root{{{root}}}"
    )
    return Response(css, media_type="text/css", headers=context.IMMUTABLE)


@router.get("/site-font")
def serve_site_font():
    """The font file gallery.cfg names in `font = …`. Like every other route
    against photos/.gallery/, the filename is read back OUT of the cfg and
    never taken from the URL — there is nothing in the URL but a version."""
    font = theme.site_font_file()
    if font is None:
        raise HTTPException(404, "not found")
    _fmt, mime = theme.ALBUM_FONT_TYPES[font.suffix.lower()]
    return FileResponse(str(font), media_type=mime, headers=context.IMMUTABLE)


@router.get("/site-wallpaper/{variant}")
def serve_site_wallpaper(variant: str):
    """The backdrop gallery.cfg names in `wallpaper =` / `wallpaper_mobile =` —
    what every page shows that no album has dressed. The album route's twin,
    with the same guarantee: the variant picks the cfg key and the whitelist,
    and the filename comes back out of the cfg."""
    if variant not in theme.ALBUM_WALLPAPER_KEYS:
        raise HTTPException(404, "not found")
    path = theme.site_wallpaper_file(variant)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(path),
                        media_type=theme.ALBUM_WALLPAPER_TYPES[path.suffix.lower()],
                        headers=context.IMMUTABLE)


@router.get("/album-font/{album:path}")
def serve_album_font(album: str):
    """The font file an album's cfg names in `font = …`. The filename never
    comes from the URL — it is read back out of the album.cfg — so this
    route cannot be used to pull anything else out of an album."""
    font = theme.album_font_file(album)
    if font is None:
        raise HTTPException(404, "not found")
    _fmt, mime = theme.ALBUM_FONT_TYPES[font.suffix.lower()]
    return FileResponse(str(font), media_type=mime,
                        headers=context.IMMUTABLE)


@router.get("/album-icon/{album:path}")
def serve_album_icon(album: str):
    """The image an album's cfg names in `icon = …`. Like the font route,
    the filename never comes from the URL — it is read back out of the
    album.cfg — so this cannot be used to pull anything else out of an
    album's `.album/` folder."""
    icon = theme.album_icon_file(album)
    if icon is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(icon), media_type=theme.ALBUM_ICON_TYPES[icon.suffix.lower()],
                        headers=context.IMMUTABLE)


@router.get("/brand/badge/{index}")
def serve_brand_badge(index: int):
    """One badge of the footer row (gallery.cfg `badges = file | Label`).
    Indexed rather than named for the same reason the other brand routes
    exist at all: the filename is read back out of the cfg, so nothing but a
    file the cfg lists can come out of photos/.gallery/."""
    path = branding.brand_badge_file(index)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(path), media_type=branding.BRAND_ASSET_TYPES[path.suffix.lower()],
                        headers=context.IMMUTABLE)


@router.get("/brand/{slot}")
def serve_brand_asset(slot: str):
    """The gallery's own logo / favicon / operator portrait, as named in
    gallery.cfg and resolved inside photos/.gallery/. `slot` is matched
    against BRAND_SLOTS, and the filename never comes from the URL — same
    contract as /album-icon."""
    cfg = config.gallery_config()
    if slot not in branding.BRAND_SLOTS:
        raise HTTPException(404, "not found")
    path = branding.brand_file((cfgio.first(cfg, branding.BRAND_SLOTS[slot]) or "").strip())
    if path is None:
        raise HTTPException(404, "not found")
    small = branding.brand_render(slot, path)
    if small is not None:
        return FileResponse(str(small), media_type="image/webp", headers=context.IMMUTABLE)
    return FileResponse(str(path), media_type=branding.BRAND_ASSET_TYPES[path.suffix.lower()],
                        headers=context.IMMUTABLE)


@router.get("/album-wallpaper/{variant}/{album:path}")
def serve_album_wallpaper(variant: str, album: str):
    """The backdrop an album's cfg names in `wallpaper =` / `wallpaper_mobile =`.
    Like the icon and font routes the filename never comes from the URL — it is
    read back out of the album.cfg — so this cannot be used to pull anything
    else out of an album's `.album/` folder. The album in the path is the
    OWNING album (the one that set the key), which _album_wallpaper_url has
    already resolved, so a sub-album never serves through its parent's URL."""
    if variant not in theme.ALBUM_WALLPAPER_KEYS:
        raise HTTPException(404, "not found")
    path = theme.album_wallpaper_file(album, variant)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(path),
                        media_type=theme.ALBUM_WALLPAPER_TYPES[path.suffix.lower()],
                        headers=context.IMMUTABLE)


# What comes out of each derivative route. The URL never names a format —
# `/thumb/foo.png` is the address of "the grid tile for foo.png", whatever it
# is encoded in — so switching a tier's format is a server-side decision and
# no template, JSON link or bookmark changes with it. See scanner.THUMB_EXT
# for why the two tiers differ.
DERIVATIVE_MIME = {".jpg": "image/jpeg", ".webp": "image/webp"}


def _serve_derivative(album: str, filename: str, out_dir: Path, size: int, kind: str,
                      ext: str = scanner.PREVIEW_EXT):
    """A downscaled copy of the photo at album/filename, built on demand.

    The two sizes the gallery serves — the grid thumbnail and the stage
    preview — differ in nothing but their output directory, their long edge
    and their format, so they share this. A derivative that is missing or
    older than its source is (re)built here rather than being left to the next
    scan: a photo dropped in seconds ago is already linked from the page that
    asked for it."""
    rel = safe_rel(album, filename).as_posix()
    src = settings.photos_dir / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    dst = (out_dir / rel).with_suffix(ext)
    if scanner.needs_rebuild(dst, src):
        built = scanner.ensure_thumb(settings.photos_dir, out_dir, rel, size, ext)
        if not built:
            raise HTTPException(500, f"{kind} generation failed")
        dst = built
    return FileResponse(str(dst), media_type=DERIVATIVE_MIME[ext], headers=context.IMMUTABLE)


@router.get("/thumb/{album}/{filename:path}")
def serve_thumb(album: str, filename: str):
    return _serve_derivative(album, filename, settings.thumbs_dir, settings.thumb_size, "thumb",
                             scanner.THUMB_EXT)


@router.get("/preview/{album}/{filename:path}")
def serve_preview(album: str, filename: str):
    return _serve_derivative(album, filename, settings.previews_dir, settings.preview_size, "preview",
                             scanner.PREVIEW_EXT)


@router.get("/full/{album}/{filename:path}")
def serve_full(album: str, filename: str):
    rel = safe_rel(album, filename).as_posix()
    src = settings.photos_dir / rel
    if not src.exists():
        raise HTTPException(404, "not found")
    if scanner.needs_jpeg_conversion(src):
        dst = scanner.ensure_full_jpeg(settings.photos_dir, settings.fulls_dir, rel)
        if not dst:
            raise HTTPException(500, "full conversion failed")
        return FileResponse(str(dst), media_type="image/jpeg", headers=context.IMMUTABLE)
    return FileResponse(str(src), headers=context.IMMUTABLE)
