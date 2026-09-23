"""The looking half of the operations surface, as data.

`aperture/ops.py` holds what the indexer is doing and the reports that act on
the derivative trees. This module holds the rest of what the CLI could always
answer and the console could not: a cfg as the app parses it, one photo or
album in full, the trips, the welcome feed, the tag vocabulary, the search,
the translation table, the archive's statistics and the config export.

Every function returns a payload and prints nothing. `aperture/cli/reports.py`
renders them to a terminal, `aperture/console/opsapi.py` serves them, so a
report that changes changes in both. They read through the gallery's own
modules, so what they say is what the pages render rather than a second
opinion about it — and none of them writes anything but the export archive,
into a file object the caller hands in.
"""

from __future__ import annotations

import os
import re
import tarfile
from pathlib import Path

from . import (albums, cfgio, checks, config, db, i18n, ops, photos, scanner,
               schema, search, templating, theme, trips, welcome)
from .runtime import settings


class NotFound(Exception):
    """Something was named that the index or the config does not have — a
    photo, a trip. Carries near misses where there are any, because "did you
    mean" is the useful half of a miss."""

    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


# ----- cfg --------------------------------------------------------------
def cfg_report(album: str | None = None, gallery: bool = False) -> dict:
    """An album.cfg (or gallery.cfg) exactly as the app parses it, what it
    resolves to, and everything wrong with it."""
    db.conn()
    if gallery:
        return {"scope": None, "file": str(config.GALLERY_CFG_PATH),
                "exists": config.GALLERY_CFG_PATH.is_file(),
                "parsed": dict(config.gallery_config()),
                "issues": checks.gallery(), "resolved": None}
    album = ops.norm_album(album)
    if not album:
        raise ValueError("give an album path, or the gallery-wide config")
    cfg = config.album_config(album)
    meta = config.album_meta_dir(album)
    path = (meta / "album.cfg") if meta else (settings.photos_dir / album / ".album" / "album.cfg")
    mode, reel = albums.album_reel(album, cfg)
    return {
        "scope": album, "file": str(path), "exists": path.is_file(),
        "parsed": dict(cfg), "issues": checks.album(album),
        "resolved": {
            "showcase": albums.album_is_showcase(album),
            "collection": config.album_collection(album),
            "cover": albums.album_cover_rel(album),
            "reel_mode": mode,
            "reel": len(reel),
            "tags": config.album_tags(album, cfg),
            "descriptions": [f"album_{lang}.md" for lang in i18n.LANGS
                             if albums.album_description(album, lang)],
            # resolved, so an inherited backdrop names the album it came from
            "wallpaper": ops.wallpaper_line(album, "desktop"),
            "wallpaper_mobile": ops.wallpaper_line(album, "mobile"),
        },
    }


# ----- photo ------------------------------------------------------------
PHOTO_URLS = ("thumb", "preview", "full", "image", "api/photo")


def photo_report(rel_path: str) -> dict:
    """Everything the app knows about one photo."""
    import json

    c = db.conn()
    rel = (rel_path or "").replace("\\", "/").strip().strip("/")
    row = c.execute("SELECT * FROM images WHERE rel_path = ?", (rel,)).fetchone()
    if row is None:
        like = c.execute(
            "SELECT rel_path FROM images WHERE lower(rel_path) = lower(?) "
            "OR lower(filename) = lower(?) ORDER BY rel_path LIMIT 10",
            (rel, rel.rsplit("/", 1)[-1]),
        ).fetchall()
        raise NotFound(f"not indexed: {rel}", [r["rel_path"] for r in like])
    row = dict(row)
    exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
    tags = [r["name"] for r in c.execute(
        "SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id = t.id "
        "WHERE it.image_id = ? ORDER BY t.name", (row["id"],))]
    by_photo, _ = ops.featured_map()
    states = ops.derivative_state(rel)
    return {**{k: v for k, v in row.items() if k != "exif_json"},
            "tags": tags, "exif": exif,
            "exif_pretty": [[str(label), str(value)] for label, value
                            in photos.prettify_exif(exif, i18n.DEFAULT_LANG)],
            "featured_by": [{"album": a, "entry": e} for a, e in by_photo.get(rel, [])],
            "file_exists": (settings.photos_dir / rel).exists(),
            "file_mtime": ops.effective_mtime(rel),
            "derivatives": {k: {"path": str(p), "state": states.get(k)}
                            for k, p in ops.derivatives(rel).items()},
            # the file URLs as the pages hand them out, version stamp and all
            "urls": [scanner.media_url(name, rel) if name in ("thumb", "preview", "full")
                     else f"/{name}/{rel}" for name in PHOTO_URLS]}


# ----- trips ------------------------------------------------------------
def trips_report() -> dict:
    """Which trips aperture/trips.py configures, and whether their album is
    there to attach to."""
    db.conn()
    return {"trips": [{"key": key, "title": cfg.get("title"),
                       "stops": len(cfg.get("stops", [])),
                       "album_exists": albums.album_exists(key)}
                      for key, cfg in trips.TRIPS.items()]}


def trip_report(album: str, lang: str = i18n.DEFAULT_LANG) -> dict:
    """The resolved trip dashboard for one album, as the template gets it."""
    db.conn()
    album = ops.norm_album(album)
    if lang not in i18n.LANGS:
        raise ValueError(f"no such language: {lang!r}")
    trip = trips.trip_for_album(album, lang)
    if trip is None:
        raise NotFound(f"no trip configured for {album!r}", list(trips.TRIPS))
    return {"album": album, **trip}


# ----- tags -------------------------------------------------------------
def _indexed_tags(album: str | None) -> dict[str, list[str]]:
    """What the index believes: tag -> the photos carrying it."""
    where, params = "", []
    if album:
        where = "WHERE i.album = ? OR substr(i.album, 1, ?) = ?"
        params = [album, len(album) + 1, album + "/"]
    rows = db.conn().execute(
        f"""SELECT t.name AS tag, i.rel_path AS rel
            FROM tags t
            JOIN image_tags it ON it.tag_id = t.id
            JOIN images i ON i.id = it.image_id
            {where}
            ORDER BY t.name COLLATE NOCASE, i.rel_path""",
        params,
    ).fetchall()
    indexed: dict[str, list[str]] = {}
    for row in rows:
        indexed.setdefault(row["tag"], []).append(row["rel"])
    return indexed


def _sidecar_tags_on_disk(album: str | None = None) -> tuple[dict, list[str]]:
    """Every `.tags` sidecar under PHOTOS_DIR, parsed the way the scanner
    parses it: {rel_path: [tag, ...]}, plus the sidecars whose photo is gone.

    Read off the filesystem rather than the index on purpose — the whole point
    of the report is catching the two drifting apart.
    """
    found: dict[str, list[str]] = {}
    orphans: list[str] = []
    root = settings.photos_dir
    base = (root / album) if album else root
    if not base.is_dir():
        return found, orphans
    for path in scanner.walk_photo_tree(base):
        if path.suffix != ".tags" or scanner.is_meta_path(path.relative_to(root)):
            continue
        photo = path.with_suffix("")          # strip `.tags`, keep `.png`
        if not photo.is_file():
            orphans.append(path.relative_to(root).as_posix())
            continue
        found[photo.relative_to(root).as_posix()] = scanner._read_sidecar_tags(photo)
    return found, orphans


def tag_report(tag: str, album: str | None = None) -> dict:
    """The photos carrying one tag (case-insensitive)."""
    album = ops.norm_album(album)
    indexed = _indexed_tags(album)
    want = (tag or "").strip().lower()
    hits = {t: r for t, r in indexed.items() if t.lower() == want}
    name, rels = next(iter(hits.items()), (tag, []))
    return {"tag": name, "album": album, "found": bool(hits),
            "photos": sorted(rels), "known": sorted(indexed, key=str.lower)}


def tags_report(album: str | None = None) -> dict:
    """The vocabulary, and drift between the `.tags` sidecars and the index.
    The sidecar is the source of truth, the index is the copy."""
    album = ops.norm_album(album)
    indexed = _indexed_tags(album)
    disk, orphans = _sidecar_tags_on_disk(album)
    by_photo_indexed: dict[str, set[str]] = {}
    for tag, rels in indexed.items():
        for rel in rels:
            by_photo_indexed.setdefault(rel, set()).add(tag.lower())
    not_indexed: list[str] = []       # on disk, not in the index
    without_sidecar: list[str] = []   # in the index, not on disk
    for rel, tags in disk.items():
        have = by_photo_indexed.get(rel, set())
        not_indexed += [f"{rel} · {tag}" for tag in tags if tag.lower() not in have]
    for rel, tags in by_photo_indexed.items():
        on_disk = {t.lower() for t in disk.get(rel, [])}
        without_sidecar += [f"{rel} · {tag}" for tag in sorted(tags) if tag not in on_disk]
    return {"album": album,
            "vocabulary": {t: len(r) for t, r in indexed.items()},
            "photos_tagged": len(by_photo_indexed),
            "sidecars": len(disk),
            "drift": {"not_indexed": sorted(not_indexed),
                      "indexed_without_sidecar": sorted(without_sidecar)},
            "orphan_sidecars": orphans}


# ----- welcome ----------------------------------------------------------
def _welcome_device(mobile: bool) -> dict:
    """What the welcome hero resolves to for one device class, and which
    gallery.cfg entries were dropped getting there."""
    cfg = config.gallery_config()
    key = "welcome_mobile" if mobile else "welcome_desktop"
    spec = cfg.get(key)
    source = key
    if not spec:
        spec = cfg.get("welcome", [])
        source = "welcome" if spec else "(unset)"
    skipped = []
    if not (len(spec) == 1 and spec[0].lower() in schema.WELCOME_KEYWORDS):
        skipped = [raw for raw in spec if welcome.lookup_welcome_image(raw) is None]
    feed, label, mode = welcome.welcome_feed(mobile=mobile)
    return {"device": "mobile" if mobile else "desktop",
            "source_key": source, "spec": list(spec), "skipped": skipped,
            "mode": mode, "label": label,
            "feed": [f["rel_path"] for f in feed]}


def welcome_report(desktop: bool = True, mobile: bool = True) -> dict:
    db.conn()
    devices = ([_welcome_device(mobile=False)] if desktop else []) + \
              ([_welcome_device(mobile=True)] if mobile else [])
    return {"devices": devices, "feed_max": welcome.WELCOME_FEED_MAX,
            "keywords": sorted(schema.WELCOME_KEYWORDS)}


# ----- albums -----------------------------------------------------------
def albums_report() -> dict:
    """Every album node with its counts and flags."""
    c = db.conn()
    listing = []
    for name in albums.all_album_nodes():
        row = c.execute(
            "SELECT COUNT(*) AS n FROM images WHERE album = ? OR substr(album, 1, ?) = ?",
            (name, len(name) + 1, name + "/")).fetchone()
        cfg = config.album_config(name)
        listing.append({"album": name, "photos": row["n"], "has_cfg": bool(cfg),
                        "showcase": albums.album_is_showcase(name),
                        "collection": config.album_collection(name, cfg)})
    return {"albums": listing}


def album_report(album: str) -> dict:
    """One album in full."""
    c = db.conn()
    album = ops.norm_album(album)
    if not album:
        raise ValueError("give an album path")
    cfg = config.album_config(album)
    rows = c.execute(
        "SELECT rel_path, taken_at, size FROM images "
        "WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY taken_at",
        (album, len(album) + 1, album + "/")).fetchall()
    dates = [r["taken_at"] for r in rows if r["taken_at"]]
    meta = config.album_meta_dir(album)
    icon = theme.album_icon_file(album)
    font = theme.album_font_file(album)
    return {
        "album": album,
        "photos": len(rows),
        "bytes": sum(r["size"] or 0 for r in rows),
        "span": [dates[0], dates[-1]] if dates else None,
        "undated": sum(1 for r in rows if not r["taken_at"]),
        "has_cfg": bool(cfg),
        "cfg": dict(cfg),
        "cover": albums.config_cover_rel(album, cfgio.first(cfg, "cover")),
        "featured": albums.resolve_photo_refs(album, cfg.get("featured", [])),
        "showcase": albums.album_is_showcase(album),
        "collection": config.album_collection(album, cfg),
        "tags": config.album_tags(album, cfg),
        "descriptions": sorted(p.name for p in meta.glob("album_*.md")) if meta else [],
        "icon": icon.name if icon else None,
        "font": font.name if font else None,
        "sub_albums": [n for n in albums.all_album_nodes() if n.startswith(album + "/")],
        "issues": checks.album(album),
    }


# ----- search -----------------------------------------------------------
def search_report(query: str, album: str | None = None, limit: int = 40) -> dict:
    """The query the /search page runs, in the same grammar (search.py), so
    what this lists is what the page would list."""
    query = (query or "").strip()
    if not query:
        raise ValueError("nothing to search for")
    parsed = search.parse(query)
    where, params = search.condition(parsed, "i")
    rows = db.conn().execute(
        f"""SELECT i.rel_path, i.album, i.filename, i.taken_at
           FROM images i WHERE {where}
           ORDER BY i.taken_at IS NULL, i.taken_at DESC, i.filename""",
        params).fetchall()
    album = ops.norm_album(album)
    if album:
        rows = [r for r in rows if r["album"] == album or r["album"].startswith(album + "/")]
    return {"query": query, "album": album, "matches": len(rows),
            "ignored": [f.label for f in parsed.filters if not f.ok],
            "photos": [dict(r) for r in rows[:max(0, limit)]]}


# ----- export -----------------------------------------------------------
def export_members() -> list[tuple]:
    """Every hand-written file: `.gallery/` (gallery.cfg with it) and each
    `.album/`, as (path, name in the archive).

    Photos are deliberately left out — they are the one thing that already is
    the backup. What this captures is the part that cannot be regenerated: the
    config, the descriptions, the icons and title fonts, and the gallery's own
    branding assets.
    """
    root = settings.photos_dir
    members: list[tuple] = []
    # schema's names, not scanner's: `export` read them off the scanner, which
    # has never had them, and failed on every run until the console needed it.
    # Walked with the NAS's own folders pruned, like every walk of the share.
    metas: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if not schema.is_system_dir(d)]
        if schema.ALBUM_META_DIR in dirnames:
            metas.append(Path(dirpath) / schema.ALBUM_META_DIR)
    metas.sort()
    brand_dir = root / schema.GALLERY_META_DIR
    if brand_dir.is_dir():
        metas.insert(0, brand_dir)
    for meta in metas:
        if not meta.is_dir():
            continue
        for path in sorted(meta.rglob("*")):
            if path.is_file() and path.name not in ("Thumbs.db", ".DS_Store"):
                members.append((path, path.relative_to(root).as_posix()))
    return members


def export_report() -> dict:
    """`export --list`: what an archive would hold."""
    members = export_members()
    return {"files": [name for _, name in members],
            "bytes": sum(p.stat().st_size for p, _ in members)}


def write_export(fileobj) -> dict:
    """Write the archive as .tar.gz into an open binary file object. Restore
    with `tar -xzf <archive> -C <photos dir>`."""
    members = export_members()
    with tarfile.open(fileobj=fileobj, mode="w:gz") as tar:
        for path, name in members:
            tar.add(path, arcname=name)
    return {"files": len(members)}


# ----- i18n -------------------------------------------------------------
_JS_LANG_RE = re.compile(r"^  (\w+): \{$")
_JS_KEY_RE = re.compile(r"^    (\w+):")
# `t('album.count')` in a template, `i18n.t(lang, "sort.curated")` in Python
_T_CALL_RE = re.compile(r"""\bt\(\s*['"]([a-z0-9_]+(?:\.[a-z0-9_]+)+)['"]""", re.I)
_PY_T_CALL_RE = re.compile(r"""i18n\.t\(\s*[^,]+,\s*['"]([a-z0-9_]+(?:\.[a-z0-9_]+)+)['"]""", re.I)
# a key built at runtime: t(lang, f"exif.{tag}") — the literal prefix is what
# we can see, so every key in that family counts as used
_KEY_FAMILY_RE = re.compile(r"""['"]([a-z0-9_]+(?:\.[a-z0-9_]+)*\.)\{""", re.I)
# What actually breaks a page, as opposed to what is only worth knowing.
I18N_HARD = ("shape", "empty", "missing", "js")
I18N_ORDER = ("shape", "empty", "missing", "js", "blank", "untranslated", "unused")


def _i18n_sources() -> str:
    """Everything that can reference a translation key: the templates and the
    app modules (minus i18n.py itself, whose table would match everything,
    and the operator surfaces, whose own reports quote keys)."""
    parts = []
    for path in sorted((templating.WEB_DIR / "templates").glob("*.html")):
        parts.append(path.read_text(encoding="utf-8"))
    for path in sorted(p for p in templating.WEB_DIR.parent.rglob("*.py")
                       if "console" not in p.parts and "cli" not in p.parts):
        if path.name in ("i18n.py", "reports.py"):
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _js_ui_strings() -> dict[str, set[str]]:
    """Key sets of the UI_STRINGS blocks in app.js, per language. Parsed by
    indentation rather than by evaluating JS — the block is hand-formatted
    and stays that way."""
    text = (templating.WEB_DIR / "static" / "app.js").read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("const UI_STRINGS"))
    except StopIteration:
        return {}
    blocks: dict[str, set[str]] = {}
    current = None
    for line in lines[start + 1:]:
        if line.startswith("};"):
            break
        m = _JS_LANG_RE.match(line)
        if m:
            current = m.group(1)
            blocks[current] = set()
            continue
        m = _JS_KEY_RE.match(line)
        if m and current:
            blocks[current].add(m.group(1))
    return blocks


def i18n_report() -> dict:
    """EN/DE/JP completeness of i18n.STRINGS, keys used but undefined, and
    whether app.js's UI_STRINGS mirror has the same keys in every language."""
    problems: dict[str, list[str]] = {}

    def note(kind, detail):
        problems.setdefault(kind, []).append(detail)

    for key, value in i18n.STRINGS.items():
        if not isinstance(value, tuple) or len(value) != len(i18n.LANGS):
            note("shape", f"{key}: expected {len(i18n.LANGS)} values, got {len(value)}")
            continue
        for lang, text in zip(i18n.LANGS, value):
            if str(text).strip():
                continue
            if lang == i18n.DEFAULT_LANG:
                note("empty", f"{key} [{lang}] is empty — there is nothing left to fall back to")
            elif key.endswith(("_prefix", "_suffix")):
                # deliberate: a sentence split around a value puts all of the
                # text in one half for a language whose word order differs
                continue
            else:
                note("blank", f"{key} [{lang}] is empty — renders the English text")
        if value[1] == value[0] and value[2] == value[0]:
            note("untranslated", f"{key}: DE and JP are identical to EN")

    blob = _i18n_sources()
    referenced = set(_T_CALL_RE.findall(blob)) | set(_PY_T_CALL_RE.findall(blob))
    for key in sorted(referenced - set(i18n.STRINGS)):
        note("missing", f"{key} is used but not defined in i18n.STRINGS (renders as the key itself)")
    # A key counts as used when it appears as a literal anywhere (a t() call,
    # or a constant table like SORT_IMAGE_OPTIONS), or when its family prefix
    # is built into an f-string.
    used = {key for key in i18n.STRINGS if f'"{key}"' in blob or f"'{key}'" in blob}
    for prefix in set(_KEY_FAMILY_RE.findall(blob)):
        used |= {key for key in i18n.STRINGS if key.startswith(prefix)}
    for key in sorted(set(i18n.STRINGS) - used):
        note("unused", f"{key} is defined but never referenced")

    js = _js_ui_strings()
    if not js:
        note("js", "could not find the UI_STRINGS block in app.js")
    else:
        base = js.get("en", set())
        for lang, keys in js.items():
            for key in sorted(base - keys):
                note("js", f"UI_STRINGS.{lang} is missing {key!r}")
            for key in sorted(keys - base):
                note("js", f"UI_STRINGS.{lang} has {key!r}, which en does not")

    return {"languages": list(i18n.LANGS), "keys": len(i18n.STRINGS),
            "js_languages": {k: len(v) for k, v in js.items()},
            "used": len(used), "problems": problems,
            "total": sum(len(v) for v in problems.values()),
            "hard": sum(len(problems.get(k, [])) for k in I18N_HARD)}


# ----- archive ----------------------------------------------------------
# Above this many rows the quick index-vs-disk count is skipped instead of
# making whoever asked wait for a walk of the whole share.
QUICK_CHECK_MAX_ROWS = 20000


def archive_report(top: int = 12, months: int = 12) -> dict:
    """What the archive holds, as the dashboard draws it: counters, date span,
    largest albums, shots per capture month, formats, and a quick check that
    the index and the disk agree on how many photos there are."""
    c = db.conn()
    counts = ops.index_counts(c)
    span = c.execute("SELECT MIN(taken_at) AS a, MAX(taken_at) AS b FROM images "
                     "WHERE taken_at IS NOT NULL").fetchone()
    largest = c.execute(
        "SELECT album, COUNT(*) AS n, SUM(size) AS bytes FROM images "
        "GROUP BY album ORDER BY n DESC, album ASC LIMIT ?", (top,)).fetchall()
    by_month = c.execute(
        "SELECT substr(taken_at, 1, 7) AS ym, COUNT(*) AS n FROM images "
        "WHERE taken_at IS NOT NULL GROUP BY ym ORDER BY ym DESC LIMIT ?",
        (months,)).fetchall()
    formats: dict[str, int] = {}
    for r in c.execute("SELECT filename FROM images"):
        ext = r["filename"].rsplit(".", 1)[-1].lower() if "." in r["filename"] else "?"
        formats[ext] = formats.get(ext, 0) + 1
    quick = counts["images"] <= QUICK_CHECK_MAX_ROWS
    return {
        "index": counts,
        "span": {"from": span["a"], "to": span["b"]},
        "albums": [dict(r) for r in largest],
        # oldest first — the archive's pulse reads left to right
        "months": [dict(r) for r in reversed(by_month)],
        "formats": sorted(formats.items(), key=lambda kv: (-kv[1], kv[0])),
        "album_nodes": len(albums.all_album_nodes()),
        "showcase_albums": sum(1 for a in albums.albums_with_ancestors()
                               if albums.album_is_showcase(a)),
        "heic": bool(scanner.HEIF_SUPPORTED),
        "photos_on_disk": len(ops.photo_files()) if quick else None,
    }
