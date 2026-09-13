"""Pretty links: a short address on the public site for one album or one photo.

    https://archive.example/s/tokyo   ->  /album/japan_2026/tokyo
    https://archive.example/s/fuji    ->  /image/japan_2026/hakone/fuji.jpg

The list is a file, `photos/.gallery/links.cfg`, in the grammar every other cfg
file uses — one `slug = target` line per link:

    tokyo = japan_2026/tokyo
    fuji  = japan_2026/hakone/fuji.jpg

That placement is the whole design. The console already writes exactly there
and nowhere else (aperture/paths.py), the gallery already re-reads cfg files per
request, and the index stays untouched: a link is configuration, not data, so
adding one needs no scan, no restart and no second writer on the database. It
is also what makes a link survive a rebuild of `data/` — it lives with the
photos it points at.

Every link lives under ONE prefix, `/s/`, and nowhere else. 1.4.0 served them
at the root (`/tokyo`), which meant a link and a page of the gallery shared one
namespace: every route the gallery gained later was a name some operator might
already have printed on a card. A prefix owned by links alone ends that for
good — no list of reserved names to keep, no route order to protect.

A target is an album path, or a photo's path inside the photo tree; which of
the two is decided by the extension, the same rule the scanner uses. Both are
resolved against the INDEX when a visitor follows the link, so a link to a
photo that was moved or deleted answers the ordinary 404 page instead of
redirecting into one.

The gallery answers a link with a 302 and `no-store`, never a 301: a permanent
redirect is cached by the browser for good, and the point of keeping the list
in a file an operator edits is that a link can be pointed somewhere else.

Three consumers, one module: the gallery's redirect route, the console's Links
screen, and `checks.pretty_links()` behind "Check all" and `doctor`.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import quote

from . import albums, cfgio, db, schema
from .runtime import settings

# The one path segment the gallery gives to links. Nothing else may ever be
# routed under it; tests/test_links.py checks the public app for that.
PREFIX = "/s/"

# Lower-case letters, digits and single inner hyphens, 1-64 long. Deliberately
# narrow: it is spoken, typed on a phone and printed on paper, so nothing in it
# may need escaping, and nothing may look like a file (`.`) or a path (`/`).
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
SLUG_MAX = 64

PATH = settings.photos_dir / schema.GALLERY_META_DIR / schema.LINKS_CFG_NAME

# What a freshly created links.cfg starts with. The shipped cfg files are their
# own documentation; a file the console creates should not be the exception.
HEADER = """\
# links.cfg — pretty links: https://<your site>/s/<name> -> an album or a photo
#
#   name = album/path                 an album
#   name = album/path/photo.jpg       one photo
#
# A name is lower-case letters, digits and hyphens. Edited by the console's
# Links screen; read by the gallery on every request, so a change is live on
# the next click.
"""

_cache: tuple[tuple[int, int], dict[str, list[str]]] | None = None


def load() -> dict[str, list[str]]:
    """Parse links.cfg, or {} when there is none. Cached on the file's
    (mtime, size), the contract config.gallery_config() keeps: an edit is
    picked up on the next call, an unchanged file is never re-read."""
    global _cache
    try:
        st = PATH.stat()
    except OSError:
        _cache = None
        return {}
    key = (st.st_mtime_ns, st.st_size)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    try:
        text = PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    cfg = cfgio.parse(text)
    _cache = (key, cfg)
    return cfg


# ----- the two halves of a link -------------------------------------------
def normalize_slug(raw) -> str:
    """What a slug is compared as: trimmed, lower-case, no outer slashes. The
    gallery lower-cases the request the same way, so `/s/Tokyo` finds `tokyo`."""
    return str(raw or "").strip().strip("/").lower()


def address(slug: str) -> str:
    """The path a visitor types for `slug` — what the audit log and every
    message call a link, so they all name it the way it is used."""
    return PREFIX + slug


def slug_problem(slug: str) -> str | None:
    """Why `slug` cannot be a link, in a sentence, or None when it can."""
    if not slug:
        return "a link needs a name"
    if len(slug) > SLUG_MAX:
        return f"a link name is at most {SLUG_MAX} characters"
    if not SLUG.fullmatch(slug) or "--" in slug:
        return (f"{slug!r} is not a link name — lower-case letters, digits and "
                "single hyphens, starting and ending with a letter or digit")
    return None


def normalize_target(raw) -> str:
    """A target as it is stored: posix separators, no outer slashes."""
    return str(raw or "").replace("\\", "/").strip().strip("/")


def target_kind(target: str) -> str:
    """"photo" or "album" — by extension, the scanner's own rule."""
    return "photo" if schema.is_image(target) else "album"


def target_problem(target: str) -> str | None:
    """Why `target` does not name something a visitor can see, or None.

    Checked against the index, like every other photo reference a cfg makes
    (checks.py): what counts as missing is what a visitor would not find."""
    if not target:
        return "a link needs a target — an album or a photo"
    if "," in target:
        # the grammar splits a value on commas, so no cfg line can hold one
        return f"{target!r} — a path with a comma cannot be written in a cfg file"
    parts = target.split("/")
    if any(p in ("", ".", "..") for p in parts) or any(p.startswith(".") for p in parts):
        return f"{target!r} is not a path inside the photo tree"
    if target_kind(target) == "photo":
        if len(parts) < 2:
            return f"{target!r} — a photo is linked by its path inside an album"
        if photo_row(target) is None:
            return f"{target!r} does not resolve to an indexed photo"
        return None
    if albums.resolve_album_path(target) is None:
        return f"{target!r} matches no album"
    return None


def photo_row(target: str):
    return db.conn().execute(
        "SELECT rel_path, album FROM images WHERE rel_path = ?", (target,)).fetchone()


def destination(target: str) -> str | None:
    """The gallery path a target redirects to, or None when it names nothing
    indexed. Albums resolve the way the album page does (casing tolerated), so
    a link and a typed URL agree about what exists."""
    if target_problem(target) is not None:
        return None
    if target_kind(target) == "photo":
        rel = PurePosixPath(target)
        return _quoted("/image/%s/%s" % (rel.parent.as_posix(), rel.name))
    return _quoted("/album/" + albums.resolve_album_path(target))


def _quoted(path: str) -> str:
    """Percent-encoded for a Location header, which is Latin-1 on the wire —
    and an archive of Japanese trips has folder names that are not."""
    return quote(path, safe="/")


def resolve(slug: str) -> str | None:
    """Where /s/<slug> goes, or None when there is no such link or its target
    is gone. Only a slug-shaped request is looked up at all."""
    slug = normalize_slug(slug)
    if slug_problem(slug) is not None:
        return None
    values = load().get(slug)
    if not values or len(values) != 1:
        return None
    return destination(normalize_target(values[0]))


def entries(cfg: dict[str, list[str]] | None = None) -> list[dict]:
    """Every link in the file, sorted by name, each with what it resolves to —
    the console's list. `cfg` as for checks: the parse a caller already holds."""
    cfg = load() if cfg is None else cfg
    out = []
    for slug in sorted(cfg):
        values = cfg[slug]
        target = normalize_target(cfgio.joined(cfg, slug))
        kind = target_kind(target)
        dest = destination(target) if len(values) == 1 else None
        if kind == "photo":
            thumb = target if dest else None
        else:
            album = albums.resolve_album_path(target) if target else None
            thumb = albums.album_cover_rel(album) if album else None
        out.append({"slug": slug, "target": target, "kind": kind,
                    "destination": dest, "thumb": thumb})
    return out
