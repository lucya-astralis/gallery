"""What is wrong with a cfg file -- one implementation, for every front end.

`doctor` and the `cfg` / `album` commands in the CLI, and "Check all" and the
issue list under every form in the console, all ask here.

There used to be two implementations. The console's resolved photos and
files against the filesystem, doctor's against the gallery's index and
resolvers; their messages differed and so, now and then, did their verdicts.
The gallery's own resolution is what decides what a visitor sees, so that is
what these checks use. The precise messages about a key that names a file --
not a bare name, an extension that key does not take, no folder yet, no such
file -- came along from the console, because they tell an operator what to
fix and not only that something is wrong. They describe the gallery's rule
exactly: theme.album_icon_file and config.gallery_asset_file check those
four things and nothing else.

Every issue is a dict:

    scope   "album" | "gallery" | "links"
    album   the album path, or None for gallery.cfg and links.cfg
    level   "error"  the gallery ignores or drops what the file says
            "warn"   it works, but not the way the file suggests
    key     the cfg key
    detail  one sentence an operator can act on
"""

from __future__ import annotations

from pathlib import Path

from . import albums, branding, cfgio, config, db, links, schema, theme, welcome

# Said after an extension error where the whitelist alone would not explain
# itself.
_EXTENSION_HINTS = {"wallpaper_mobile": "stills only: a phone never loads a backdrop video"}


# ----- fixes ---------------------------------------------------------------
# An issue may carry the one edit that makes it go away: which key, and the
# value it should have (None removes the key). The console stages it as an
# ordinary unsaved change -- reviewed in its diff and saved like any other
# edit -- so a fix is never a write of its own. `doctor` ignores it.
def _issue(entry: dict, fix: dict | None) -> dict:
    if fix:
        entry["fix"] = fix
    return entry


def _drop(key: str, label: str = "Remove the line") -> dict:
    return {"label": label, "key": key, "value": None}


def _without(cfg: dict, key: str, item: str) -> dict:
    """The list with one entry taken out -- the key gone if that was the last."""
    rest = [i for i in cfg.get(key, []) if i.strip() != item.strip()]
    return {"label": f"Remove {item.strip()!r}", "key": key, "value": rest or None}


def album(name: str, cfg: dict[str, list[str]] | None = None) -> list[dict]:
    """Everything wrong with one album.cfg. Empty when the file is fine or absent.

    `cfg` is the parsed file when the caller already holds it. The console
    passes what it has just written: the gallery's cfg cache is keyed on the
    file's mtime and size, and a same-size edit inside one timestamp tick
    would otherwise be checked as the version before it."""
    cfg = config.album_config(name) if cfg is None else cfg
    if not cfg:
        return []
    out: list[dict] = []

    def add(level: str, key: str, detail: str, fix: dict | None = None) -> None:
        out.append(_issue({"scope": "album", "album": name, "level": level, "key": key,
                           "detail": detail}, fix))

    for key in cfg:
        if key not in schema.ALBUM_KEYS:
            add("error", key, "unknown key — ignored by the gallery (known: %s)"
                % ", ".join(sorted(schema.ALBUM_KEYS)), _drop(key))

    if "cover" in cfg:
        raw = cfgio.first(cfg, "cover")
        if raw and not albums.config_cover_rel(name, raw):
            add("error", "cover", f"{raw!r} does not resolve to an indexed photo",
                _drop("cover", "Pick automatically again"))

    featured = [i.strip() for i in cfg.get("featured", []) if i.strip()]
    if any(i.lower() in ("*", "all") for i in featured):
        # The gallery then features the album's own photos and reads no
        # other entry, so there is nothing else to resolve.
        if not db.conn().execute("SELECT 1 FROM images WHERE album = ? LIMIT 1", (name,)).fetchone():
            add("error", "featured", "`*` features nothing — the album has no photos of its own")
    else:
        for item in featured:
            if not albums.resolve_photo_refs(name, [item]):
                add("error", "featured", f"{item!r} matches no indexed photo",
                    _without(cfg, "featured", item))
    for item in cfg.get("order", []):
        item = item.strip()
        if item and not albums.resolve_photo_refs(name, [item]):
            add("warn", "order", f"{item!r} matches no indexed photo",
                _without(cfg, "order", item))

    reel = (cfgio.first(cfg, "reel") or "").strip().lower()
    if reel and reel not in schema.REEL_ACCEPTED:
        add("error", "reel", f"{reel!r} is not featured, random or off", _drop("reel", "Use the default"))

    sort = (cfgio.first(cfg, "sort") or "").strip().lower()
    if sort and sort not in schema.PHOTO_SORTS:
        add("error", "sort", f"{sort!r} is not one of {', '.join(sorted(schema.PHOTO_SORTS))}",
            _drop("sort", "Use the default"))
    elif sort == "curated" and "order" not in cfg:
        add("warn", "sort", "curated preset without an `order` list — the gallery falls back to date_desc")

    effect = (cfgio.first(cfg, "effect") or "").strip().lower()
    if effect and effect not in schema.EFFECTS:
        add("error", "effect", f"{effect!r} is not whitelisted ({', '.join(sorted(schema.EFFECTS))})",
            _drop("effect", "Use no effect"))

    _files(cfg, schema.ALBUM_ASSET_KEYS, config.album_meta_dir(name), schema.ALBUM_META_DIR + "/", add)
    _theme(cfg, add)

    # A custom stat renders as LABEL / VALUE, so it needs the colon to split
    # on; without one, or with nothing after it, the line is dropped silently.
    for item in cfg.get("stat", []):
        _label, sep, value = item.partition(":")
        if not sep:
            add("warn", "stat", f"{item!r} has no `Label: Value` colon — the line is dropped",
                _without(cfg, "stat", item))
        elif not value.strip():
            add("warn", "stat", f"{item!r} has an empty value — the line is dropped",
                _without(cfg, "stat", item))
    stats = (cfgio.first(cfg, "stats") or "").strip().lower()
    if stats and stats not in cfgio.FALSE:
        add("warn", "stats", f"{stats!r} does nothing — only an off/false/no value hides the block",
            _drop("stats"))
    return out


def gallery(cfg: dict[str, list[str]] | None = None) -> list[dict]:
    """Everything wrong with gallery.cfg. Empty when the file is fine or
    absent. `cfg` as for album()."""
    cfg = config.gallery_config() if cfg is None else cfg
    if not cfg:
        return []
    out: list[dict] = []

    def add(level: str, key: str, detail: str, fix: dict | None = None) -> None:
        out.append(_issue({"scope": "gallery", "album": None, "level": level, "key": key,
                           "detail": detail}, fix))

    for key in cfg:
        if key not in schema.GALLERY_KEYS:
            add("error", key, "unknown key — ignored by the gallery (known: %s)"
                % ", ".join(sorted(schema.GALLERY_KEYS)), _drop(key))

    for key in ("welcome", "welcome_desktop", "welcome_mobile"):
        spec = cfg.get(key, [])
        if len(spec) == 1 and spec[0].strip().lower() in schema.WELCOME_KEYWORDS:
            continue
        for raw in spec:
            if not welcome.lookup_welcome_image(raw):
                add("error", key, f"{raw!r} does not resolve to an indexed photo — the entry is skipped",
                    _without(cfg, key, raw))

    if "album_order" in cfg:
        known = {albums.album_order_key(n) for n in albums.all_album_nodes()}
        for item in cfg["album_order"]:
            if item.startswith("#"):
                continue          # a group label, not an album
            if albums.album_order_key(item) not in known:
                add("warn", "album_order", f"{item!r} matches no album",
                    _without(cfg, "album_order", item))

    album_sort = (cfgio.first(cfg, "album_sort") or "").strip().lower()
    if album_sort and album_sort not in schema.GALLERY_ALBUM_SORTS:
        add("error", "album_sort", "%r is not one of %s"
            % (album_sort, ", ".join(sorted(schema.GALLERY_ALBUM_SORTS))),
            _drop("album_sort", "Use the default"))
    elif album_sort == "curated" and "album_order" not in cfg:
        add("warn", "album_sort", "curated preset without an `album_order` list")

    _files(cfg, schema.GALLERY_ASSET_KEYS, config.gallery_meta_dir(), schema.GALLERY_META_DIR + "/", add)
    _theme(cfg, add)

    # A bad link or a badge naming a missing file is silent in the browser --
    # the link is dropped, the badge vanishes -- so this is the only place it
    # surfaces.
    for key in schema.URL_KEYS:
        raw = cfgio.joined(cfg, key)
        if raw and branding.brand_link(cfg, key) is None:
            add("error", key, f"{raw!r} is not an http(s) or site-relative URL — the link is dropped",
                _drop(key))
    badges = cfg.get("badges", [])
    for badge in badges[:schema.BADGE_MAX]:
        file = badge.partition("|")[0].strip()
        if file and branding.brand_file(file) is None:
            add("error", "badges", f"{file!r} is not an image in {schema.GALLERY_META_DIR}/ — the badge is skipped",
                _without(cfg, "badges", badge))
    if len(badges) > schema.BADGE_MAX:
        add("warn", "badges", f"only the first {schema.BADGE_MAX} are shown")
    return out


def pretty_links(cfg: dict[str, list[str]] | None = None) -> list[dict]:
    """Everything wrong with links.cfg — every entry the gallery would answer
    with a 404 instead of a redirect. Empty when the file is fine or absent.
    `cfg` as for album(). Issues carry scope "links", and the slug as `key`.

    Worth a check of its own because a dead link is the quietest failure
    there is: nothing on the site shows it, only the person who follows it
    finds out — usually from a link printed somewhere it cannot be fixed."""
    cfg = links.load() if cfg is None else cfg
    out: list[dict] = []

    def add(level: str, key: str, detail: str) -> None:
        out.append({"scope": "links", "album": None, "level": level, "key": key, "detail": detail})

    for slug, values in cfg.items():
        problem = links.slug_problem(slug)
        if problem:
            add("error", slug, problem)
            continue
        if len(values) > 1:
            add("error", slug, "more than one target (a comma, or the name written twice) "
                "— the link answers 404")
            continue
        problem = links.target_problem(links.normalize_target(cfgio.joined(cfg, slug)))
        if problem:
            add("error", slug, problem + " — the link answers 404")
    return out


def everything() -> list[dict]:
    """gallery.cfg, the pretty links, then every album that can carry an
    album.cfg."""
    out = gallery() + pretty_links()
    for name in albums.albums_with_ancestors():
        out += album(name)
    return out


# ----- rules both files share ---------------------------------------------
def _files(cfg: dict, keys: dict, folder: Path | None, where: str, add) -> None:
    """The four things the gallery checks before it serves a file a cfg
    names, in the order an operator would fix them."""
    for key, allowed in keys.items():
        raw = (cfgio.first(cfg, key) or "").strip()
        if not raw:
            continue
        if Path(raw).name != raw:
            add("error", key, f"{raw!r} must be a bare file name inside {where}", _drop(key))
        elif Path(raw).suffix.lower() not in allowed:
            hint = _EXTENSION_HINTS.get(key)
            add("error", key, f"{raw!r} is not one of {', '.join(sorted(allowed))}"
                + (f" — {hint}" if hint else ""), _drop(key))
        elif folder is None:
            add("error", key, f"{raw!r} — there is no {where} folder yet", _drop(key))
        elif not (folder / raw).is_file():
            add("error", key, f"{raw!r} is not in {where}", _drop(key))


def _theme(cfg: dict, add) -> None:
    """The value half of the theme block album.cfg and gallery.cfg both carry
    under identical rules: the face's scale, the accent, the backdrop knobs.
    (The file half -- `font`, both wallpapers -- goes through _files.)"""
    if "font_scale" in cfg:
        raw = (cfgio.first(cfg, "font_scale") or "").strip()
        lo, hi = schema.FONT_SCALE_RANGE
        if config.cfg_font_scale(raw) is None:
            add("warn", "font_scale", f"{raw!r} is ignored — not a number in {lo:g}–{hi:g}",
                _drop("font_scale"))
        elif "font" not in cfg:
            add("warn", "font_scale", "ignored — there is no `font` for it to scale",
                _drop("font_scale"))

    accent = (cfgio.first(cfg, "accent") or "").strip()
    if accent:
        rgb = theme.parse_hex_color(accent)
        if rgb is None:
            add("error", "accent", f"{accent!r} is not a hex colour (#abc or #aabbcc) — the gallery ignores it",
                _drop("accent"))
        elif theme.accent_shades(rgb)["lifted"]:
            # Not an error: the gallery lightens it rather than ship an
            # unreadable page. But the colour on screen is then not the one in
            # the file, and that is worth saying out loud.
            lifted = theme.accent_shades(rgb)["acc"]
            add("warn", "accent", f"{accent!r} is too dark to read on the black page — "
                f"the gallery lightens it to {lifted}",
                {"label": f"Use {lifted}", "key": "accent", "value": lifted})

    for key, span in (("wallpaper_tint", schema.WALLPAPER_TINT_RANGE),
                      ("wallpaper_dim", schema.WALLPAPER_DIM_RANGE)):
        raw = (cfgio.first(cfg, key) or "").strip()
        if not raw or raw.lower() in cfgio.TRUE | cfgio.FALSE:
            continue
        unit = f"{span[0]:g}–{span[1]:g} or off"
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            add("error", key, f"{raw!r} is not a number ({unit}) — ignored", _drop(key))
            continue
        if not span[0] <= value <= span[1]:
            add("warn", key, f"{raw!r} is outside {unit} — ignored, the default stands", _drop(key))
