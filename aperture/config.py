"""Reading album.cfg and gallery.cfg.

Where the files live, their cached parses, and the answers that are about the
file rather than about the page: an album's display name, tags and collection
flag, values inherited from a parent album or tiered down from gallery.cfg,
and the files a cfg names inside photos/.gallery/.

The grammar is aperture/cfgio.py; what a key may say is aperture/schema.py.
"""

from __future__ import annotations

from pathlib import Path

from . import cfgio, schema
from .runtime import settings


def album_meta_dir(album: str) -> Path | None:
    """The album's `.album/` metadata folder (see the format notes above), or
    None when the album path is bogus or the folder doesn't exist."""
    folder = (settings.photos_dir / album / schema.ALBUM_META_DIR).resolve()
    try:
        folder.relative_to(settings.photos_dir)  # guard against path traversal
    except ValueError:
        return None
    return folder if folder.is_dir() else None


# album path -> ((mtime_ns, size), parsed cfg). Same contract as
# _gallery_cfg_cache further down: the parse is keyed on what the file looks
# like on disk, so an edit is picked up on the very next call and an unchanged
# file is never re-read. One entry per album, so the whole thing is a few
# dozen small dicts even on a large tree.
_album_cfg_cache: dict[str, tuple[tuple[int, int], dict[str, list[str]]]] = {}


def album_config(album: str) -> dict[str, list[str]]:
    """Parse the album's `album.cfg` (see cfgio.parse), or {} when there's no
    such file.

    Cached on the file's (mtime, size). It reads like a per-card cost, but
    nearly every helper on this page takes an album and looks its cfg up
    again — name, cover, icon, tags, font, reel, showcase, effect, plus the
    ancestor walks for theme and wallpaper — so rendering one album page
    asked for the same handful of files ~74 times, 20 of them the identical
    one. The returned dict is shared and never mutated by callers."""
    meta = album_meta_dir(album)
    if meta is None:
        _album_cfg_cache.pop(album, None)
        return {}
    cfg_path = meta / "album.cfg"
    try:
        st = cfg_path.stat()
    except OSError:
        _album_cfg_cache.pop(album, None)
        return {}
    key = (st.st_mtime_ns, st.st_size)
    cached = _album_cfg_cache.get(album)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    cfg = cfgio.parse(text)
    _album_cfg_cache[album] = (key, cfg)
    return cfg


def album_display_name(album: str, cfg: dict[str, list[str]] | None = None) -> str:
    """What an album is CALLED, as opposed to where it lives. album.cfg
    `name = Japan 2026` wins; without one the folder's own last segment is
    used with underscores relaxed into spaces (`japan_2026` -> `japan 2026`),
    which is what the hero title always did on its own.

    The folder name stays the identity everywhere it matters — URLs, cfg
    lookups, the `album` column, cover paths — so renaming here is free and
    never breaks a link. cfgio.parse comma-splits values, so the parts are
    rejoined the way `loc` is (albums.album_stats): a name may contain commas."""
    cfg = album_config(album) if cfg is None else cfg
    pretty = ", ".join(v.strip() for v in (cfg.get("name") or []) if v.strip())
    if pretty:
        return pretty
    return (album or "").rsplit("/", 1)[-1].replace("_", " ")


def album_tags(album: str, cfg: dict[str, list[str]] | None = None) -> list[str]:
    """The album's `tags = a, b, c`, de-duplicated, order kept. A leading `#`
    is optional in the cfg — the hero renders one either way, so accept both
    spellings rather than printing `##night`.

    These describe the ALBUM and are display-only. The per-image tags that a
    `.tags` sidecar feeds into the tags/image_tags tables are a separate
    thing (scanner._read_sidecar_tags) and still own the ?tag= grid filter."""
    cfg = album_config(album) if cfg is None else cfg
    out: list[str] = []
    seen: set[str] = set()
    for raw in cfg.get("tags") or []:
        name = raw.strip().lstrip("#").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def album_collection(album: str, cfg: dict[str, list[str]] | None = None) -> bool:
    """album.cfg `collection = true`: the album stands for its whole subtree
    (its own photos + every sub-folder's) rather than just the photos sitting
    directly in it. One definition for the album page, the single-image
    neighbour scroll and the JSON API — see photos.photo_scope."""
    cfg = album_config(album) if cfg is None else cfg
    return cfgio.as_bool(cfgio.first(cfg, "collection"))


def cfg_font_scale(raw: str | None) -> float | None:
    """A `font_scale = …` as a float, or None when it is unset, not a number,
    or outside ALBUM_FONT_SCALE_RANGE. None means "don't emit the property" —
    style.css then falls back to its own default of 1. Shared by the album's
    face and the site's (gallery.cfg), which are tuned on the same terms."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        scale = float(raw.replace(",", "."))
    except ValueError:
        return None
    lo, hi = schema.FONT_SCALE_RANGE
    return scale if lo <= scale <= hi else None


def cfg_ratio(raw: str, fallback: float, span: tuple[float, float],
               off: float) -> float:
    """A 0-1 cfg number, with off/none/no as a word for one end of it. Out of
    range or unparseable falls back to the SITE value rather than to an
    extreme — a typo should not black out a page."""
    val = (raw or "").strip().lower()
    if val in cfgio.FALSE:
        return off
    if val in cfgio.TRUE:
        return 1.0
    try:
        num = float(val.replace(",", "."))
    except ValueError:
        return fallback
    lo, hi = span
    return num if lo <= num <= hi else fallback


def _cfg_inherited(album: str | None, key: str) -> tuple[str, str] | None:
    """(owning album, raw value) for the nearest album from `album` upwards
    whose cfg sets `key` to something non-empty. The generic form of the walk
    theme.album_wallpaper_source does for its two file keys."""
    if not album:
        return None
    parts = album.replace("\\", "/").strip("/").split("/")
    for depth in range(len(parts), 0, -1):
        owner = "/".join(parts[:depth])
        raw = (cfgio.first(album_config(owner), key) or "").strip()
        if raw:
            return owner, raw
    return None


def cfg_tiered(album: str | None, key: str) -> str | None:
    """The raw value of `key` for a page, through the two tiers every themable
    key has: the album and its ancestors (_cfg_inherited), then gallery.cfg.
    None when nobody sets it and the built-in default stands.

    This is the whole inheritance story in one function, and every themable
    key goes through it — accent, both backdrop knobs, the title face — so
    "gallery.cfg paints the site, an album overrides it for its own pages"
    is one rule rather than one per key."""
    found = _cfg_inherited(album, key)
    if found is not None:
        return found[1]
    return (cfgio.first(gallery_config(), key) or "").strip() or None


def gallery_meta_dir() -> Path | None:
    """photos/.gallery/, or None when the gallery keeps no assets of its own."""
    folder = settings.photos_dir / schema.GALLERY_META_DIR
    return folder if folder.is_dir() else None


def gallery_asset_file(name: str, allowed) -> Path | None:
    """A gallery.cfg filename resolved inside photos/.gallery/, or None.

    The rule every key naming a file in that folder shares — the mark, the
    favicon, the portrait, the badges, the site face, the site backdrop: a
    BARE filename only (anything carrying a path separator is refused) whose
    extension is one that key is willing to serve. Same guarantee
    theme.album_icon_file gives one tier up, so a cfg edit can never reach outside
    the one folder, whichever key made it."""
    folder = gallery_meta_dir()
    if folder is None or not name:
        return None
    if Path(name).name != name or Path(name).suffix.lower() not in allowed:
        return None
    path = folder / name
    return path if path.is_file() else None


def gallery_asset_stamp(path: Path) -> int:
    """Cache-busting stamp for a file in photos/.gallery/: the newer of its own
    mtime and gallery.cfg's. The filename never travels in the URL, so
    repointing `logo =` — or `font =`, or `wallpaper =` — at a different file
    has to invalidate through the cfg's mtime, or the browser keeps serving
    the old one. The cfg counts for a second reason on the face: `font_scale`
    rides on the same generated sheet, and retuning it never touches the font
    file (theme.album_font_version versions on its own cfg for exactly this)."""
    stamps = []
    for p in (path, GALLERY_CFG_PATH):
        try:
            stamps.append(int(p.stat().st_mtime))
        except OSError:
            pass
    return max(stamps, default=0)


def gallery_cfg_asset(key: str, allowed) -> Path | None:
    """The file gallery.cfg names in `key`, or None when it names none — or
    one that isn't there, or isn't a type this key serves."""
    return gallery_asset_file((cfgio.first(gallery_config(), key) or "").strip(),
                               allowed)


# Next to the assets it names, the way an album.cfg sits in `.album/`.
GALLERY_CFG_PATH = settings.photos_dir / schema.GALLERY_META_DIR / schema.GALLERY_CFG_NAME
_gallery_cfg_cache: tuple[tuple[int, int], dict[str, list[str]]] | None = None


def gallery_config() -> dict[str, list[str]]:
    """Parse gallery.cfg (see cfgio.parse), or {} when there's no such file. Edits still show up without a restart — the parse is
    keyed on the file's (mtime, size), so a changed file is re-read and an
    unchanged one is not. It used to re-parse on every call, which was fine
    while only page renders asked; the branding and credit readers ask far
    more often than that, a full scan once per derivative written."""
    global _gallery_cfg_cache
    cfg_path = GALLERY_CFG_PATH
    try:
        st = cfg_path.stat()
    except OSError:
        _gallery_cfg_cache = None
        return {}
    key = (st.st_mtime_ns, st.st_size)
    cached = _gallery_cfg_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    cfg = cfgio.parse(text, group_keys=cfgio.GROUP_KEYS)
    _gallery_cfg_cache = (key, cfg)
    return cfg
