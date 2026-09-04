"""Config checks, mirroring what the gallery's `python -m app.cli doctor` reports.

Runs against the filesystem rather than the gallery's index, so it catches the
same class of mistake (a cover pointing at a file that isn't there, a font the
album never got, a sort preset with no list behind it) without needing the
gallery to be running.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import cfgio, schema
from .library import Library


def _issue(level: str, key: str, detail: str) -> dict:
    return {"level": level, "key": key, "detail": detail}


def _check_wallpaper_knobs(cfg: dict[str, list[str]]) -> list[dict]:
    """`wallpaper_tint` / `wallpaper_dim`, which album.cfg and gallery.cfg both
    carry under identical rules -- gallery.cfg sets the site default, an album
    overrides it -- so the check is written once."""
    out: list[dict] = []
    for key, span in (("wallpaper_tint", schema.WALLPAPER_TINT_RANGE),
                      ("wallpaper_dim", schema.WALLPAPER_DIM_RANGE)):
        if key not in cfg:
            continue
        raw = (cfgio.first(cfg, key) or "").strip().lower()
        if not raw or raw in ("off", "none", "no", "0", "false",
                              "on", "yes", "true", "1"):
            continue
        try:
            ok = span[0] <= float(raw.replace(",", ".")) <= span[1]
        except ValueError:
            ok = False
        if not ok:
            out.append(_issue("warn", key,
                              "ignored -- not a number in %s-%s (or “off”)"
                              % span))
    return out


def check_album(lib: Library, album: str,
                cfg: dict[str, list[str]] | None = None) -> list[dict]:
    """Everything wrong with one album.cfg, as {level, key, detail}."""
    if cfg is None:
        cfg = cfgio.CfgFile.load(lib.cfg_path(album)).values()
    if not cfg:
        return []
    out: list[dict] = []
    known = ", ".join(sorted(schema.ALBUM_KEYS))

    for key in cfg:
        if key not in schema.ALBUM_KEYS:
            out.append(_issue("error", key,
                              "unknown key -- ignored by the gallery "
                              "(known: %s)" % known))

    if "cover" in cfg:
        raw = cfgio.first(cfg, "cover")
        if raw and not lib.resolve_photo(album, raw):
            out.append(_issue("error", "cover",
                              "%r does not resolve to a photo in this album" % raw))

    for key, level in (("featured", "error"), ("order", "warn")):
        for item in cfg.get(key, []):
            if not item or item.lower() in ("*", "all"):
                continue
            if not lib.resolve_photo(album, item):
                out.append(_issue(level, key, "%r matches no photo" % item))

    if "reel" in cfg:
        val = (cfgio.first(cfg, "reel") or "").strip().lower()
        allowed = set(schema.REEL_VALUES) | {"shuffle", "false", "0", "no", "none"}
        if val and val not in allowed:
            out.append(_issue("error", "reel", "%r is not featured/random/off" % val))

    if "sort" in cfg:
        val = (cfgio.first(cfg, "sort") or "").strip().lower()
        if val and val not in schema.PHOTO_SORTS:
            out.append(_issue("error", "sort", "%r is not one of %s"
                              % (val, ", ".join(sorted(schema.PHOTO_SORTS)))))
        elif val == "curated" and "order" not in cfg:
            out.append(_issue("warn", "sort",
                              "curated preset without an `order` list -- "
                              "the gallery falls back to date_desc"))

    if "effect" in cfg:
        val = (cfgio.first(cfg, "effect") or "").strip().lower()
        if val and val not in schema.EFFECTS:
            out.append(_issue("error", "effect", "%r is not whitelisted (%s)"
                              % (val, ", ".join(schema.EFFECTS))))

    for key, exts in (("icon", schema.ICON_EXTS), ("font", schema.FONT_EXTS),
                      ("wallpaper", schema.WALLPAPER_EXTS),
                      ("wallpaper_mobile", schema.WALLPAPER_IMAGE_EXTS)):
        if key not in cfg:
            continue
        raw = (cfgio.first(cfg, key) or "").strip()
        if not raw:
            continue
        if Path(raw).name != raw:
            out.append(_issue("error", key,
                              "%r must be a bare filename inside .album/" % raw))
        elif Path(raw).suffix.lower() not in exts:
            out.append(_issue("error", key, "%r is not one of %s"
                              % (raw, ", ".join(sorted(exts)))))
        elif not (lib.meta_dir(album) / raw).is_file():
            out.append(_issue("error", key, "%r is not in this album's .album/" % raw))

    if "font_scale" in cfg:
        lo, hi = schema.FONT_SCALE_RANGE
        raw = (cfgio.first(cfg, "font_scale") or "").strip()
        try:
            ok = lo <= float(raw) <= hi
        except ValueError:
            ok = False
        if not ok:
            out.append(_issue("warn", "font_scale",
                              "ignored -- not a number in %s-%s" % (lo, hi)))
        elif "font" not in cfg:
            out.append(_issue("warn", "font_scale", "ignored -- no `font` set"))

    if "accent" in cfg:
        raw = (cfgio.first(cfg, "accent") or "").strip()
        if raw and not re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", raw):
            out.append(_issue("error", "accent",
                              "%r is not a hex colour (#abc or #aabbcc) -- "
                              "the gallery ignores it" % raw))

    out += _check_wallpaper_knobs(cfg)

    # _album_stats splits on the FIRST colon and skips an entry whose value is
    # empty, so both shapes vanish from the page without a word.
    for item in cfg.get("stat", []):
        label, sep, val = item.partition(":")
        if not sep:
            out.append(_issue("warn", "stat",
                              "%r has no “Label: Value” colon — the line is dropped" % item))
        elif not val.strip():
            out.append(_issue("warn", "stat",
                              "%r has an empty value — the line is dropped" % item))

    return out


def check_gallery(lib: Library,
                  cfg: dict[str, list[str]] | None = None) -> list[dict]:
    if cfg is None:
        cfg = cfgio.CfgFile.load(lib.gallery_cfg_path(),
                                 cfgio.GROUP_KEYS).values()
    if not cfg:
        return []
    out: list[dict] = []
    known = ", ".join(sorted(schema.GALLERY_KEYS))

    for key in cfg:
        if key not in schema.GALLERY_KEYS:
            out.append(_issue("error", key,
                              "unknown key -- ignored (known: %s)" % known))

    for key in ("welcome", "welcome_desktop", "welcome_mobile"):
        spec = cfg.get(key, [])
        if len(spec) == 1 and spec[0].strip().lower() in schema.WELCOME_KEYWORDS:
            continue
        for raw in spec:
            if not lib.resolve_gallery_photo(raw):
                out.append(_issue("error", key,
                                  "%r does not resolve to a photo -- "
                                  "the entry is skipped" % raw))

    if "album_order" in cfg:
        known_albums = {schema.order_key(p) for p in lib.album_paths()}
        for raw in cfg["album_order"]:
            if raw.startswith("#"):
                continue  # group marker
            key = schema.order_key(raw).lstrip("_")
            if not any(key == a or a.replace("_", "", 1) == key or a == key
                       for a in known_albums):
                out.append(_issue("warn", "album_order",
                                  "%r matches no album folder" % raw))

    if "album_sort" in cfg:
        val = (cfgio.first(cfg, "album_sort") or "").strip().lower()
        if val and val not in schema.GALLERY_ALBUM_SORTS:
            out.append(_issue("error", "album_sort", "%r is not one of %s"
                              % (val, ", ".join(schema.GALLERY_ALBUM_SORTS))))
        elif val == "curated" and "album_order" not in cfg:
            out.append(_issue("warn", "album_sort",
                              "curated preset without an `album_order` list"))

    out += _check_wallpaper_knobs(cfg)
    out += _check_brand(lib, cfg)

    return out


# http(s) or site-relative — what the gallery is willing to put in an href
_URL_RE = re.compile(r"^(?:https?://[^\s\"'<>]+|/[^\s\"'<>]*)$")


def _check_brand(lib: Library, cfg: dict[str, list[str]]) -> list[dict]:
    """The branding keys. A mistake here never shows as an error on the site
    — a mistyped logo falls back to the built-in mark, a bad URL drops the
    link, a badge naming a missing file just vanishes — so this is the only
    place it surfaces."""
    out: list[dict] = []
    meta = lib.gallery_meta_dir()
    present = {a["name"] for a in lib.brand_assets()}

    def check_file(key: str, name: str, exts: set) -> None:
        if Path(name).name != name:
            out.append(_issue("error", key,
                              "%r must be a bare filename inside %s/"
                              % (name, schema.GALLERY_META_DIR)))
        elif not meta.is_dir():
            out.append(_issue("error", key, "%r -- there is no %s/ folder yet"
                              % (name, schema.GALLERY_META_DIR)))
        elif name not in present:
            out.append(_issue("error", key, "%r is not in %s/"
                              % (name, schema.GALLERY_META_DIR)))
        elif Path(name).suffix.lower() not in exts:
            out.append(_issue("error", key, "%r is not one of %s"
                              % (name, ", ".join(sorted(exts)))))

    for key, exts in schema.BRAND_ASSET_KEYS.items():
        name = (cfgio.first(cfg, key) or "").strip()
        if name:
            check_file(key, name, exts)

    for key in schema.URL_KEYS:
        raw = ", ".join(cfg.get(key) or []).strip()
        if raw and not _URL_RE.match(raw):
            out.append(_issue("error", key,
                              "%r is not an http(s) or site-relative URL -- "
                              "the link is dropped" % raw))

    badges = cfg.get("badges") or []
    for badge in badges[:schema.BADGE_MAX]:
        name = badge.partition("|")[0].strip()
        if name and name not in present:
            out.append(_issue("error", "badges",
                              "%r is not in %s/ -- the badge is skipped"
                              % (name, schema.GALLERY_META_DIR)))
    if len(badges) > schema.BADGE_MAX:
        out.append(_issue("warn", "badges",
                          "only the first %d are shown" % schema.BADGE_MAX))

    return out


def check_all(lib: Library) -> list[dict]:
    """Every issue across the gallery, each tagged with where it came from."""
    out: list[dict] = []
    for issue in check_gallery(lib):
        out.append(dict(issue, scope="gallery", album=""))
    for album in lib.album_paths():
        for issue in check_album(lib, album):
            out.append(dict(issue, scope="album", album=album))
    return out
