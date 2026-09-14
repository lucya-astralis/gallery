"""How a page looks.

Title faces (per album and site-wide), the accent and the backdrop treatment,
album icons and wallpapers -- resolved from cfg to the files, URLs and CSS
declarations the pages and the theme routes hand out.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path
from urllib.parse import quote

from . import cfgio, config, schema, templating


# ----- per-album title font (album.cfg `font = ...`) --------------------
# An album can bring its own display face for its hero title: drop the font
# file into the album's `.album/` folder and name it in album.cfg
#   font = MusashiBrush.otf
# The face reaches the page as a generated stylesheet rather than an inline
# <style>, because the CSP (style-src 'self', see CSP below) drops inline
# styles: /album-font.css/{album} carries the @font-face plus the
# --album-title-font custom property that style.css's `.album-font
# .album-hero__title` rule reads, and /album-font/{album} serves the file.
# The family name is a constant — only one album's sheet ever loads on a
# page, so it cannot collide.
#
# The sheet also carries --album-title-scale from
#   font_scale = 1.25
# a multiplier on the hero title's size. Display faces differ a lot in how
# much of the em they actually ink — a brush face lands visibly smaller than
# a geometric one at the same px — so the album that ships the face is also
# where its size is tuned, rather than the shared clamp in style.css.
ALBUM_FONT_FAMILY = "album-title"

# Extension -> (CSS `format()` hint, response media type). Doubles as the
# whitelist of what may be served: a `font = …` naming anything else (an
# album_en.md, say) resolves to nothing.
# @font-face format() names are a CSS concern and stay here; which
# extensions are fonts, and their content type, are schema.py's.
_FONT_FORMAT = {".otf": "opentype", ".ttf": "truetype", ".woff2": "woff2", ".woff": "woff"}
ALBUM_FONT_TYPES = {e: (_FONT_FORMAT[e], schema.MIME[e]) for e in sorted(schema.FONT_EXTS)}


def album_font_file(album: str) -> Path | None:
    """The album's configured title font as a real file, or None. The cfg
    value is a bare filename resolved inside the album's `.album/` folder:
    anything carrying a path separator, or an extension outside
    ALBUM_FONT_TYPES, is rejected — so this only ever resolves to a font
    sitting next to the album.cfg that named it."""
    meta = config.album_meta_dir(album)
    if meta is None:
        return None
    name = (cfgio.first(config.album_config(album), "font") or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in ALBUM_FONT_TYPES:
        return None
    path = meta / name
    return path if path.is_file() else None


def album_font_scale(album: str) -> float | None:
    """The album's own `font_scale`. No inheritance and no gallery.cfg tier:
    it sizes the face the SAME cfg brought, so a scale without a `font` next
    to it means nothing (the doctor says so)."""
    return config.cfg_font_scale(cfgio.first(config.album_config(album), "font_scale"))


def album_font_version(album: str) -> int:
    """Cache-busting stamp for an album's generated font sheet: the newest
    mtime of the font file and of the album.cfg naming it (same idea as
    templating.static_url). The cfg has to count — the sheet carries `font_scale`
    too, and retuning that never touches the font file, so versioning on
    the font alone would leave the edit masked by a year-long cache."""
    meta = config.album_meta_dir(album)
    sources = [album_font_file(album), (meta / "album.cfg") if meta else None]
    stamps = []
    for path in filter(None, sources):
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return max(stamps, default=0)


def album_font_css_url(album: str) -> str | None:
    """Cache-busting URL of the album's generated font stylesheet, or None
    when the album configures no font — both this sheet and the font it
    points at are cached hard, so the version is what makes edits land."""
    if album_font_file(album) is None:
        return None
    return f"/album-font.css/{quote(album)}?v={album_font_version(album)}"


def album_font_preload(album: str) -> dict | None:
    """Preload descriptor (font url + media type) for the album's title face,
    or None when it configures none. Without it the browser can't even learn
    the font's URL until it has fetched AND parsed the generated
    /album-font.css sheet, and only then fetches the file — a serial waterfall
    that leaves the hero title in the fallback face for a visible beat before
    it swaps in. Preloading the file in the page head runs that download in
    parallel with the stylesheet instead, so the face is there by first paint
    and the swap never shows. Same url/version as the @font-face src, so both
    requests hit one cache entry."""
    font = album_font_file(album)
    if font is None:
        return None
    _fmt, mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    return {
        "href": f"/album-font/{quote(album)}?v={album_font_version(album)}",
        "type": mime,
    }


# ----- theme: accent + backdrop treatment (gallery.cfg / album.cfg) ------
# The site sets its colours in gallery.cfg and an album can repaint its own
# pages over the top — one key set, two tiers, resolved by config.cfg_tiered:
#   accent          = #7ad1ff   the accent for this album's pages
#   wallpaper_tint  = off       how much colour the backdrop keeps
#   wallpaper_dim   = .9        how bright it is
# All three reach the page the way the title face does — as a generated
# stylesheet (/album-theme.css/{album}), because the CSP drops inline styles
# (style-src 'self'; see CSP below). The sheet only redefines tokens style.css
# already reads, so no rule anywhere has to know that an album can carry an
# accent of its own.
#
# They inherit down the tree the way `wallpaper` does: a sub-album that sets
# nothing takes the nearest ancestor's, otherwise `japan_2026/kansai` would
# drop back to the site colours mid-browse while still wearing its parent's
# wallpaper. Below the whole album chain sits gallery.cfg with the same three
# keys, which is what a non-album page (welcome, /albums, search, 404) reads —
# so an operator paints the site once instead of repeating a colour in every
# album.cfg, and an album that wants to differ still says so itself.
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# The site treatment, restated here because the generated sheet has to emit a
# COMPLETE filter: it replaces --wallpaper-filter, it cannot patch one
# function out of it. Keep in step with the token in style.css.
WALLPAPER_TINT_DEFAULT = 0.92
WALLPAPER_DIM_DEFAULT = 0.72
WALLPAPER_CONTRAST = 1.04

# Contrast floor both accent readings are held to: --acc is small text on a
# PANEL and a face under black label text, so both want luminance.
ACCENT_MIN_CONTRAST = 4.5

# The ground --acc is measured against, and the one real bug this derivation
# had. It used to measure against #000000 and stop the moment it cleared it —
# but the accent is almost never ON #000. It is a link inside a panel, the
# open row of a sort menu, the glyph in front of a section label, every one of
# them on --surface or a step above. #5865F2 clears 4.56:1 on #000 and only
# 4.19:1 on --surface, so the whole 4.5 margin was spent before the colour was
# used. Measuring against the surface it actually sits on lifts the built-in
# accent exactly one step, #5865F2 -> #616EF3, and that step is the difference
# between a promise and a rounding error. Keep in step with --surface in
# style.css (and with nebulaAccent() in nebula/showcase.js, which is a
# byte-identical port of this function).
ACCENT_SURFACE = (0x0E, 0x0E, 0x10)


def _srgb_lum(rgb):
    """WCAG relative luminance of an 8-bit sRGB triple."""
    chan = []
    for v in rgb:
        v /= 255.0
        chan.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = chan
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = _srgb_lum(a), _srgb_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hls_rgb(h: float, l: float, s: float) -> tuple[int, int, int]:
    return tuple(round(c * 255)
                 for c in colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s))


def parse_hex_color(raw: str | None) -> tuple[int, int, int] | None:
    """`#abc` / `#aabbcc` -> (r, g, b). Anything else is None: the value ends
    up inside a generated stylesheet, so only three parsed integers ever get
    near it — never a string that came out of a cfg."""
    raw = (raw or "").strip()
    if not _HEX_COLOR.match(raw):
        return None
    body = raw[1:]
    if len(body) == 3:
        body = "".join(c * 2 for c in body)
    return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))


def accent_shades(rgb: tuple[int, int, int]) -> dict:
    """The three faces style.css needs, derived from one colour by moving
    only LIGHTNESS along its own hue. Each answers a legibility question the
    sheet cannot answer for itself:
      acc   small text on a PANEL and a face under black label text — one
            constraint either way, luminance, so a too-dark cfg colour is
            LIFTED rather than rendered unreadable (`lifted` says so, and the
            cfg checkers surface it)
      deep  the single face that carries WHITE text (the hero CTA), so it
            goes the other way until white reads on it
      soft  the hover step above acc, again under black text
    Saturation is capped on `deep` alone: at full chroma a mid-lightness hue
    turns electric, which none of the other shades of the same colour do.

    `acc` is measured against ACCENT_SURFACE, not against black — see the
    note there. This is the whole reason an accent knob can be exposed to a
    cfg file at all: a hand-typed hex lands with the same guarantees as the
    built-in colour rather than as a raw value nobody checked."""
    h, l, sat = colorsys.rgb_to_hls(*[v / 255 for v in rgb])
    acc_l = l
    while (acc_l < 0.97
           and _contrast(_hls_rgb(h, acc_l, sat), ACCENT_SURFACE) < ACCENT_MIN_CONTRAST):
        acc_l += 0.02
    deep_s, deep_l = min(sat, 0.78), min(acc_l, 0.58)
    while (deep_l > 0.12
           and _contrast(_hls_rgb(h, deep_l, deep_s), (255, 255, 255)) < ACCENT_MIN_CONTRAST):
        deep_l -= 0.02
    acc = _hls_rgb(h, acc_l, sat)
    return {
        "acc": "#%02x%02x%02x" % acc,
        "rgb": "%d,%d,%d" % acc,
        "deep": "#%02x%02x%02x" % _hls_rgb(h, deep_l, deep_s),
        "soft": "#%02x%02x%02x" % _hls_rgb(h, acc_l + (1 - acc_l) * 0.42, sat),
        "lifted": acc != tuple(rgb),
    }


def page_accent(album: str | None) -> dict | None:
    """The page's accent as the three derived faces, or None when neither the
    album chain nor gallery.cfg sets a usable `accent = #hex`. With none set
    anywhere, style.css's own --acc… tokens stand."""
    raw = config.cfg_tiered(album, "accent")
    if raw is None:
        return None
    rgb = parse_hex_color(raw)
    return None if rgb is None else accent_shades(rgb)


def _wallpaper_knob(album: str | None, key: str, default: float,
                    span: tuple[float, float], off: float) -> float | None:
    """One backdrop knob, resolved through the tiers of config.cfg_tiered and then
    the built-in default. Returns None when nobody set it at all — the caller
    needs to tell "nothing configured" from "configured to the same value the
    site uses", because that is what decides whether a stylesheet is emitted
    for this page at all."""
    raw = config.cfg_tiered(album, key)
    if raw is None:
        return None
    return config.cfg_ratio(raw, default, span, off=off)


def wallpaper_decls(album: str | None = None) -> list[str]:
    """The backdrop half of a theme sheet: `--wallpaper-filter`, plus
    `--wallpaper-bloom` when the tint is off. Empty when neither the album
    chain nor gallery.cfg touches either knob — style.css's own tokens then
    stand and, if nothing else is themed either, no sheet is emitted at all.

    Two things `tint = off` has to do, and the second one is easy to miss:
    drop the greyscale, AND drop the accent wash `.site-bg::after` lays over
    the picture. A backdrop asked for in full colour that still had a coloured
    bloom on it was, correctly, reported as still tinted (user, 2026-09-01).
    What `tint = off` does NOT drop is the dimming: full colour, but still a
    backdrop you can put text on. Only turning BOTH knobs off yields
    `filter: none`."""
    tint = _wallpaper_knob(album, "wallpaper_tint", WALLPAPER_TINT_DEFAULT,
                           (0.0, 1.0), off=0.0)
    dim = _wallpaper_knob(album, "wallpaper_dim", WALLPAPER_DIM_DEFAULT,
                          schema.WALLPAPER_DIM_RANGE, off=1.0)
    if tint is None and dim is None:
        return []
    if tint is None:
        tint = WALLPAPER_TINT_DEFAULT
    if dim is None:
        dim = WALLPAPER_DIM_DEFAULT
    if tint <= 0 and dim >= 1:
        filt = "none"
    else:
        parts = []
        if tint > 0:
            parts.append("grayscale(%g)" % tint)
        if dim < 1:
            parts.append("brightness(%g)" % dim)
        parts.append("contrast(%g)" % WALLPAPER_CONTRAST)
        filt = " ".join(parts)
    decls = ["--wallpaper-filter:%s" % filt]
    if tint <= 0:
        decls.append("--wallpaper-bloom:transparent")
    return decls


def _theme_version(album: str | None) -> int:
    """Cache-busting stamp: the newest mtime of every cfg that can own a value
    on this page — each album.cfg from the album up to the root, plus
    gallery.cfg. Editing a VALUE never touches a file whose name travels in
    the URL, so the cfg mtimes are all there is to version on."""
    paths = [config.GALLERY_CFG_PATH]
    if album:
        parts = album.replace("\\", "/").strip("/").split("/")
        for depth in range(len(parts), 0, -1):
            meta = config.album_meta_dir("/".join(parts[:depth]))
            if meta is not None:
                paths.append(meta / "album.cfg")
    stamps = []
    for path in paths:
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return max(stamps, default=0)


def theme_decls(album: str | None) -> list[str]:
    """The custom-property declarations this page's theme sheet carries.
    Every value is re-serialised from parsed numbers, never printed straight
    out of a cfg."""
    decls = []
    accent = page_accent(album)
    if accent is not None:
        decls += ["--acc:%s" % accent["acc"], "--acc-rgb:%s" % accent["rgb"],
                  "--acc-deep:%s" % accent["deep"], "--acc-soft:%s" % accent["soft"]]
    decls += wallpaper_decls(album)
    return decls


def theme_css_url(album: str | None = None) -> str | None:
    """Cache-busting URL of the generated theme sheet for a page, or None when
    nothing is themed and style.css's own tokens stand. Two routes behind it:
    an album path when there is one, the site-wide sheet otherwise — a page
    with no album still needs gallery.cfg's backdrop treatment."""
    if not theme_decls(album):
        return None
    stamp = _theme_version(album)
    if not album:
        return "/site-theme.css?v=%d" % stamp
    return "/album-theme.css/%s?v=%d" % (quote(album), stamp)


# ----- per-album icon (album.cfg `icon = ...`) --------------------------
# Any album can carry a small mark of its own — a civic emblem, a crest, a
# logo — rendered wherever the album is named: its card in the grids, the
# hero title and the breadcrumb. It started as three hard-coded SVGs under
# /static/emblems on the trip timeline's stops; the mark now belongs to the
# album instead, so every album gets one for free — and the timeline, which
# is the trip's rather than the albums', no longer shows it.
#
# Same shape as the title font: drop the file into the album's `.album/`
# folder, name it in album.cfg
#   icon = kansai.svg
# and /album-icon/{album} serves it back. Nothing is looked at without that
# key, so an album without one just renders without a mark.
ALBUM_ICON_TYPES = {e: schema.MIME[e] for e in sorted(schema.ICON_EXTS)}


def album_icon_file(album: str) -> Path | None:
    """The album's configured icon as a real file, or None. Mirrors
    album_font_file: the cfg value is a bare filename resolved inside the
    album's `.album/` folder, and anything carrying a path separator or an
    extension outside ALBUM_ICON_TYPES is rejected — so this only ever
    resolves to an image sitting next to the album.cfg that named it."""
    meta = config.album_meta_dir(album)
    if meta is None:
        return None
    name = (cfgio.first(config.album_config(album), "icon") or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in ALBUM_ICON_TYPES:
        return None
    path = meta / name
    return path if path.is_file() else None


def album_icon_url(album: str | None) -> str | None:
    """Cache-busting URL of an album's icon, or None when it configures
    none — the file is served with a year-long cache, so the version stamp
    is what makes an edit land. Newest mtime of the icon AND of the cfg
    naming it, because pointing `icon =` at a different file doesn't change
    this URL's path (the filename never travels in it)."""
    if not album:
        return None
    icon = album_icon_file(album)
    if icon is None:
        return None
    meta = config.album_meta_dir(album)
    stamps = []
    for path in (icon, (meta / "album.cfg") if meta else None):
        if path is None:
            continue
        try:
            stamps.append(int(path.stat().st_mtime))
        except OSError:
            pass
    return f"/album-icon/{quote(album)}?v={max(stamps, default=0)}"


# ----- per-album wallpaper (album.cfg owns it) --------------------------
# Same shape as the icon and the title font: drop the file into the album's
# `.album/` folder and name it in album.cfg
#   wallpaper        = kyoto-night.mp4     -> desktop backdrop
#   wallpaper_mobile = kyoto-night.jpg     -> phones
# Two keys, because the two are genuinely different assets: desktop can carry
# a video, phones must not (app.js never loads one there, and a 6 MB clip on
# a phone plan is the reason it doesn't). Either key on its own is fine; the
# missing side falls back to the site default (bg.mp4 / bg-poster.jpg).
#
# A sub-album with no wallpaper of its own inherits the nearest ancestor's,
# so `japan_2026` dresses `japan_2026/kansai/osaka` too — otherwise every
# nested folder would drop back to the site default mid-browse.
ALBUM_WALLPAPER_VIDEO_TYPES = {e: schema.MIME[e] for e in sorted(schema.WALLPAPER_VIDEO_EXTS)}
ALBUM_WALLPAPER_IMAGE_TYPES = {e: schema.MIME[e] for e in sorted(schema.WALLPAPER_IMAGE_EXTS)}
ALBUM_WALLPAPER_TYPES = {**ALBUM_WALLPAPER_VIDEO_TYPES, **ALBUM_WALLPAPER_IMAGE_TYPES}

# phones get a still frame, never a clip — see the note above
ALBUM_WALLPAPER_KEYS = {"desktop": ("wallpaper", ALBUM_WALLPAPER_TYPES),
                        "mobile": ("wallpaper_mobile", ALBUM_WALLPAPER_IMAGE_TYPES)}


def album_wallpaper_file(album: str, variant: str) -> Path | None:
    """The album's own configured wallpaper as a real file, or None. Mirrors
    album_icon_file exactly: the cfg value is a bare filename resolved inside
    the album's `.album/` folder, and anything with a path separator or an
    extension outside the whitelist is rejected — so this can only ever
    resolve to a file sitting next to the album.cfg that named it. No
    inheritance here; album_wallpaper_source walks the tree."""
    key, allowed = ALBUM_WALLPAPER_KEYS[variant]
    meta = config.album_meta_dir(album)
    if meta is None:
        return None
    name = (cfgio.first(config.album_config(album), key) or "").strip()
    if not name or Path(name).name != name:
        return None
    if Path(name).suffix.lower() not in allowed:
        return None
    path = meta / name
    return path if path.is_file() else None


def album_wallpaper_source(album: str | None, variant: str) -> tuple[str, Path] | None:
    """(owning album, file) for the wallpaper an album shows — its own, else
    the nearest ancestor's. None when nothing up the tree configures one."""
    if not album:
        return None
    parts = album.replace("\\", "/").strip("/").split("/")
    for depth in range(len(parts), 0, -1):
        owner = "/".join(parts[:depth])
        found = album_wallpaper_file(owner, variant)
        if found is not None:
            return owner, found
    return None


def _album_wallpaper_url(album: str | None, variant: str) -> str | None:
    """Cache-busting URL, or None when neither the album nor any ancestor
    configures this variant. Version stamp is the newest mtime of the file
    AND of the cfg naming it — repointing the key at a different file does
    not change the path, since the filename never travels in the URL."""
    src = album_wallpaper_source(album, variant)
    if src is None:
        return None
    owner, path = src
    meta = config.album_meta_dir(owner)
    stamps = []
    for p in (path, (meta / "album.cfg") if meta else None):
        if p is None:
            continue
        try:
            stamps.append(int(p.stat().st_mtime))
        except OSError:
            pass
    return f"/album-wallpaper/{variant}/{quote(owner)}?v={max(stamps, default=0)}"


# A constant, for the reason ALBUM_FONT_FAMILY is one: at most one site sheet
# ever loads on a page, so the name cannot collide — not even with an album's.
SITE_FONT_FAMILY = "site-display"


def site_font_file() -> Path | None:
    """The site's display face as a real file, or None. album_font_file one
    tier up, read out of gallery.cfg against photos/.gallery/."""
    return config.gallery_cfg_asset("font", ALBUM_FONT_TYPES)


def site_font_scale() -> float | None:
    """gallery.cfg's `font_scale`, on the same terms as an album's — a
    multiplier on every text the site face sets, held to the same range."""
    return config.cfg_font_scale(cfgio.first(config.gallery_config(), "font_scale"))


def site_font() -> dict | None:
    """What base.html needs to dress the chrome in the site's own face, or
    None when gallery.cfg names none and style.css's stock stack stands:
      css      the generated @font-face + --display-font sheet
      preload  {href, type} for the file itself, so it downloads ALONGSIDE
               that sheet rather than only after the browser has fetched and
               parsed it — the same serial waterfall album_font_preload
               exists to break, and more visible here: the face is on the
               welcome hero at 72px.
    Both carry the same version, so the two requests hit one cache entry."""
    font = site_font_file()
    if font is None:
        return None
    _fmt, mime = ALBUM_FONT_TYPES[font.suffix.lower()]
    stamp = config.gallery_asset_stamp(font)
    return {
        "css": f"/site-font.css?v={stamp}",
        "preload": {"href": f"/site-font?v={stamp}", "type": mime},
    }


def site_wallpaper_file(variant: str) -> Path | None:
    """The site's own backdrop for one variant, or None. The same two keys and
    the same per-variant whitelist an album.cfg carries — phones never get a
    clip — read from gallery.cfg against photos/.gallery/."""
    key, allowed = ALBUM_WALLPAPER_KEYS[variant]
    return config.gallery_cfg_asset(key, allowed)


def _site_wallpaper_url(variant: str) -> str | None:
    path = site_wallpaper_file(variant)
    if path is None:
        return None
    return f"/site-wallpaper/{variant}?v={config.gallery_asset_stamp(path)}"


def _bg_layer(album: str | None, variant: str) -> tuple[str, Path] | None:
    """(url, file) for one backdrop slot, through the tiers a wallpaper has:
    the album and its ancestors, then gallery.cfg's own. None when neither
    configures one and the shipped /static default is what shows."""
    src = album_wallpaper_source(album, variant)
    if src is not None:
        return _album_wallpaper_url(album, variant), src[1]
    path = site_wallpaper_file(variant)
    if path is not None:
        return _site_wallpaper_url(variant), path
    return None


def site_bg(album: str | None = None) -> dict:
    """What base.html paints behind the page. Three slots so the template
    stays dumb and CSP-safe (no inline styles anywhere):
      still_mobile / still_desktop — <picture> sources; the browser fetches
        exactly one, and the desktop still doubles as the poster frame behind
        a loading video
      video — desktop clip, or None when the desktop wallpaper is a still
        image and there is nothing to play
    Every slot goes through _bg_layer, so an album's own backdrop wins, then
    gallery.cfg's, then the one shipped under /static — the same fall-through
    the rest of the theme has.

    The poster behind a CONFIGURED clip is the configured mobile still rather
    than the shipped bg-poster.jpg: the two are crops of one backdrop, and
    flashing a stock photograph for the beat before the clip buffers had the
    site briefly wearing someone else's picture. With nothing configured at
    all it is the shipped clip that plays, and the shipped poster is the frame
    that belongs to it."""
    desktop = _bg_layer(album, "desktop")
    mobile = _bg_layer(album, "mobile")
    default_still = templating.static_url(templating.WEB_DIR, "bg-poster.jpg")
    desktop_is_video = (desktop is not None
                        and desktop[1].suffix.lower() in ALBUM_WALLPAPER_VIDEO_TYPES)
    if desktop is None:
        still_desktop = default_still
    elif desktop_is_video:
        still_desktop = mobile[0] if mobile else default_still
    else:
        still_desktop = desktop[0]
    return {
        "still_mobile": mobile[0] if mobile else default_still,
        "still_desktop": still_desktop,
        "video": (desktop[0] if desktop_is_video else
                  (None if desktop else templating.static_url(templating.WEB_DIR, "bg.mp4"))),
    }
