"""The album tree.

Which albums exist, their cards, the curated order and its sections,
breadcrumbs, descriptions and covers, the stats block an album page shows,
and the showcase/featured flags album.cfg owns.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter

from . import cfgio, config, db, i18n, photos, schema, stats, theme
from .runtime import settings


def showcase_album_rows(limit: int | None = None):
    """Top-level showcase albums for the ★ FEATURED rails (welcome screen
    and /albums). Newest-active first — unless gallery.cfg defines a curated
    `album_order`, which then fixes the rail order too. Whether an album is
    a showcase is decided by `album_is_showcase` (album.cfg
    `showcase = …`). Same card shape as `albums_index`
    (album, name, count, latest, cover, sub_count) so the showcase-album
    partial can be reused on both pages."""
    cards = [c for c in top_level_album_cards() if album_is_showcase(c["album"])]
    order = "curated" if curated_album_positions() else "latest_desc"
    cards = sorted_album_cards(cards, order)
    return cards[:limit] if limit is not None else cards


# ----- album tree -------------------------------------------------------
# Albums are directories and nest arbitrarily (e.g. "japan/tokyo"). The
# `images.album` column stores each photo's full parent-directory path, so
# the album *tree* is derived from those strings — intermediate folders that
# hold only sub-folders (no direct photos of their own) are still found.
def distinct_albums() -> list[str]:
    c = db.conn()
    return [r["album"] for r in c.execute("SELECT DISTINCT album FROM images").fetchall()]


def albums_with_ancestors() -> list[str]:
    """Every folder that can carry an album.cfg: the albums holding photos
    plus all their parents. A parent album whose photos all live in
    sub-folders has no rows in `images`, so walking distinct_albums() alone
    would skip its cfg entirely (its `featured = sub/pic.jpg` never applied,
    see recompute_featured)."""
    out: set[str] = set()
    for album in distinct_albums():
        parts = album.split("/")
        for i in range(1, len(parts) + 1):
            out.add("/".join(parts[:i]))
    return sorted(out)


def child_album_names(parent: str | None, all_albums: list[str] | None = None) -> list[str]:
    """Immediate sub-folder album-paths directly under `parent`
    (top-level albums when `parent` is None)."""
    albums = all_albums if all_albums is not None else distinct_albums()
    prefix = (parent + "/") if parent else ""
    plen = len(prefix)
    out: list[str] = []
    seen: set[str] = set()
    for a in albums:
        if prefix:
            if not a.startswith(prefix):
                continue
            rest = a[plen:]
        else:
            rest = a
        if not rest:
            continue
        full = prefix + rest.split("/", 1)[0]
        if full not in seen:
            seen.add(full)
            out.append(full)
    # an unlisted child stays off its parent's list -- unless the parent sits
    # in the same unlisted subtree, so an unlisted album still lists its own
    return [a for a in out if not hidden_from(a, parent)]


def album_cover_rel(album: str) -> str | None:
    """Cover for an album node: the album.cfg-pinned cover wins, otherwise
    the newest photo from anywhere in the subtree. `substr(...)` (not LIKE)
    so album names containing `_`/`%` don't act as wildcards."""
    cover_rel = config_cover_rel(album, cfgio.first(config.album_config(album), "cover"))
    if cover_rel:
        return cover_rel
    prefix = album + "/"
    # never a photo from an unlisted album further down (unlisted_clause)
    listed, listed_params = unlisted_clause(keep=album)
    row = db.conn().execute(
        "SELECT rel_path FROM images WHERE (album = ? OR substr(album, 1, ?) = ?) "
        f"AND {listed} "
        "ORDER BY taken_at IS NULL, taken_at DESC, mtime DESC LIMIT 1",
        (album, len(prefix), prefix, *listed_params),
    ).fetchone()
    return row["rel_path"] if row else None


def album_card(album: str, all_albums: list[str] | None = None) -> dict:
    """Display info for one album node: recursive photo count, latest
    activity, a cover image from anywhere in its subtree, and how many
    immediate sub-albums it has. `substr(...)` (not LIKE) is used for the
    subtree prefix so album names containing `_`/`%` don't act as wildcards."""
    c = db.conn()
    prefix = album + "/"
    listed, listed_params = unlisted_clause(keep=album)
    cond = f"(album = ? OR substr(album, 1, ?) = ?) AND {listed}"
    params = (album, len(prefix), prefix, *listed_params)
    agg = c.execute(
        f"SELECT COUNT(*) AS count, MAX(taken_at) AS latest FROM images WHERE {cond}",
        params,
    ).fetchone()
    cover_rel = album_cover_rel(album)
    return {
        "album": album,
        # album.cfg `name = …` when set, else the folder name (config.album_display_name)
        "name": config.album_display_name(album),
        "count": agg["count"] if agg else 0,
        "latest": agg["latest"] if agg else None,
        "cover": cover_rel,
        # album.cfg `icon = …`, the album's own mark (None when it sets none)
        "icon": theme.album_icon_url(album),
        "sub_count": len(child_album_names(album, all_albums)),
    }


def top_level_album_cards(all_albums: list[str] | None = None) -> list[dict]:
    """One card per top-level album (unsorted)."""
    all_albums = all_albums if all_albums is not None else distinct_albums()
    return [album_card(n, all_albums) for n in child_album_names(None, all_albums)]


def album_order_key(path: str) -> str:
    """Normalize an album path for matching against gallery.cfg
    `album_order` entries: lower-cased, so an entry works regardless of the
    exact casing on disk."""
    return path.replace("\\", "/").strip().strip("/").lower()


def curated_album_positions() -> dict[str, int]:
    """gallery.cfg `album_order` as {normalized album path: position}.
    `#group` frame markers don't take part in the ordering and are skipped.
    Empty dict when no curated album order is configured."""
    pos: dict[str, int] = {}
    for item in config.gallery_config().get("album_order", []):
        if item.startswith("#"):
            continue
        key = album_order_key(item)
        if key and key not in pos:
            pos[key] = len(pos)
    return pos


def sorted_album_cards(cards: list[dict], sort_key: str) -> list[dict]:
    """Order album cards by one of the SORT_ALBUM keys or "curated"
    (gallery.cfg `album_order`). A leading stable name-ascending pass
    provides the tie-break for every other key.

    "By name" sorts on the name the reader SEES (album.cfg `name = …` via
    album_card), not the folder path — a grid of pretty names ordered by
    hidden folder names just looks broken. The folder path stays the
    tie-break so two albums sharing a display name keep a stable order."""
    def shown(a: dict) -> tuple[str, str]:
        return ((a.get("name") or a["album"]).lower(), a["album"].lower())
    cards = sorted(cards, key=shown)
    if sort_key == "curated":
        # listed albums first, in their configured order; everything not
        # listed follows newest-first (stable sorts keep both groups tidy)
        pos = curated_album_positions()
        cards.sort(key=lambda a: a["latest"] or "", reverse=True)
        cards.sort(key=lambda a: pos.get(album_order_key(a["album"]), len(pos)))
    elif sort_key == "name_desc":
        cards.sort(key=shown, reverse=True)
    elif sort_key == "count_desc":
        cards.sort(key=lambda a: a["count"], reverse=True)
    elif sort_key == "count_asc":
        cards.sort(key=lambda a: a["count"])
    elif sort_key == "latest_asc":
        cards.sort(key=lambda a: (a["latest"] is None, a["latest"] or ""))
    elif sort_key == "latest_desc":
        cards.sort(key=lambda a: a["latest"] or "", reverse=True)
    # name_asc: already sorted
    return cards


def curated_album_sections(cards: list[dict]) -> list[dict]:
    """Split album cards (already in curated order) into the framed groups
    of the Curated /albums view: a gallery.cfg `album_order` line like
    `#trips` opens a named group that frames every album listed below it.
    Returns [{label, cards}, ...] in cfg order — label None for the
    frameless chunks (albums listed above the first marker, plus a trailing
    chunk for albums that aren't listed at all) — or [] when the order
    defines no groups, so callers keep the flat grid."""
    entries = config.gallery_config().get("album_order", [])
    if not any(e.startswith("#") for e in entries):
        return []
    labels = [""]  # section labels in cfg order; "" = the frameless lead
    key_label: dict[str, str] = {}
    label = ""
    for e in entries:
        if e.startswith("#"):
            label = e[1:].strip()
            if label not in labels:
                labels.append(label)
        else:
            k = album_order_key(e)
            if k and k not in key_label:
                key_label[k] = label
    buckets: dict[str, list[dict]] = {lab: [] for lab in labels}
    unlisted: list[dict] = []
    for card in cards:
        lab = key_label.get(album_order_key(card["album"]))
        (unlisted if lab is None else buckets[lab]).append(card)
    sections = [{"label": lab or None, "cards": buckets[lab]}
                for lab in labels if buckets[lab]]
    if unlisted:
        sections.append({"label": None, "cards": unlisted})
    return sections


def album_breadcrumbs(album: str) -> list[dict]:
    """[{name, path, icon}, ...] for each ancestor segment of an album path,
    so templates can render HOME / ALBUMS / japan / tokyo with linkable
    parts. `icon` is that segment's album.cfg mark, or None."""
    acc: list[str] = []
    out: list[dict] = []
    for seg in album.split("/"):
        if not seg:
            continue
        acc.append(seg)
        path = "/".join(acc)
        out.append({"name": config.album_display_name(path), "path": path,
                    "icon": theme.album_icon_url(path)})
    return out


def _render_markdown(text: str) -> str:
    """Render an album-description markdown string to HTML. python-markdown is
    a pure-Python dependency (requirements.txt); if it's ever missing we still
    produce readable paragraphs rather than crashing the album page."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        import markdown as _md
        return _md.markdown(text, extensions=["extra", "sane_lists"], output_format="html5")
    except Exception:
        import html as _html
        blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
        return "".join("<p>" + _html.escape(b).replace("\n", "<br>") + "</p>" for b in blocks)


def album_description(album: str, lang: str = i18n.DEFAULT_LANG) -> str | None:
    """An album's description is a per-language markdown file in its `.album/`
    folder: album_en.md / album_de.md / album_jp.md. The active language wins;
    a missing translation falls back to English, then to a plain album.md,
    then to the first *.md in the folder — so a partially translated gallery
    still shows something everywhere. Rendered to HTML; None when the folder
    has no markdown at all."""
    meta = config.album_meta_dir(album)
    if meta is None:
        return None
    candidates = [meta / f"album_{lang}.md",
                  meta / f"album_{i18n.DEFAULT_LANG}.md",
                  meta / "album.md"]
    md_file = next((p for p in candidates if p.is_file()), None)
    if md_file is None:
        md_file = next(iter(sorted(p for p in meta.glob("*.md") if p.is_file())), None)
    if md_file is None:
        return None
    try:
        raw = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _render_markdown(raw) or None


def album_stats(images: list[dict], cfg: dict[str, list[str]], lang: str) -> dict:
    """Stats block for the description card: {'context': [...], 'capture': [...],
    'has': bool}. Each entry is {'key': LABEL, 'val': text}. `images` is the
    album's whole photo set (unfiltered by any ?tag=), so the readouts describe
    the album, not the current grid view."""
    if (cfgio.first(cfg, "stats") or "").strip().lower() in cfgio.FALSE:
        return {"context": [], "capture": [], "has": False}

    # --- context: editorial, from album.cfg -----------------------------
    context: list[dict] = []
    loc = ", ".join(v.strip() for v in (cfg.get("loc") or []) if v.strip())
    if loc:
        context.append({"key": i18n.t(lang, "stat.location"), "val": loc})
    # freeform `stat = Label: Value` (one fact per line; avoid commas in the
    # value — the cfg parser comma-splits list values, see cfgio.parse).
    for raw in cfg.get("stat") or []:
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            k, _, v = raw.partition(":")
            entry = {"key": k.strip(), "val": v.strip()}
        else:
            entry = {"key": "", "val": raw}
        if entry["val"]:
            context.append(entry)

    # --- capture: auto, from the photos' EXIF + size --------------------
    total = 0
    tmin = tmax = None
    devices: Counter = Counter()
    fnums: list[float] = []
    focals: list[int] = []
    for im in images:
        total += im.get("size") or 0
        t = im.get("taken_at")
        if t:
            tmin = t if tmin is None or t < tmin else tmin
            tmax = t if tmax is None or t > tmax else tmax
        try:
            exif = json.loads(im["exif_json"]) if im.get("exif_json") else {}
        except (ValueError, TypeError):
            exif = {}
        dev = stats.clean_device(exif.get("Make"), exif.get("Model"))
        if dev:
            devices[dev] += 1
        fn = exif.get("FNumber")
        if isinstance(fn, (int, float)) and fn > 0:
            fnums.append(float(fn))
        fl = exif.get("FocalLengthIn35mmFilm") or exif.get("FocalLength")
        if isinstance(fl, (int, float)) and fl > 0:
            focals.append(round(float(fl)))

    capture: list[dict] = []
    span = i18n.date_span(lang, tmin, tmax)
    if span:
        capture.append({"key": i18n.t(lang, "stat.span"), "val": span})
    if devices:
        dev = devices.most_common(1)[0][0]
        # a couple of stray cameras shouldn't hide the dominant one, but note them
        if len(devices) > 1:
            dev = f"{dev} +{len(devices) - 1}"
        capture.append({"key": i18n.t(lang, "stat.device"), "val": dev})
    if focals:
        lo, hi = min(focals), max(focals)
        capture.append({"key": i18n.t(lang, "stat.focal"),
                        "val": f"{lo} mm" if lo == hi else f"{lo}–{hi} mm"})
    if fnums:
        lo, hi = min(fnums), max(fnums)
        val = f"ƒ{stats.fmt_num(lo)}" if lo == hi else f"ƒ{stats.fmt_num(lo)}–{stats.fmt_num(hi)}"
        capture.append({"key": i18n.t(lang, "stat.aperture"), "val": val})
    data = photos.humanize_bytes(total)
    if data:
        capture.append({"key": i18n.t(lang, "stat.data"), "val": data})

    return {"context": context, "capture": capture, "has": bool(context or capture)}


def config_cover_rel(album: str, manual: str | None) -> str | None:
    """Resolve an album.cfg `cover` value (path relative to the album, with
    or without the album prefix) to a real indexed rel_path, or None."""
    if not manual:
        return None
    rel = manual.strip().strip("/")
    if not rel:
        return None
    if not (rel == album or rel.startswith(album + "/")):
        rel = f"{album}/{rel}"
    row = db.conn().execute(
        "SELECT rel_path FROM images WHERE rel_path = ?", (rel,)
    ).fetchone()
    return row["rel_path"] if row else None


# ----- showcase / featured (album.cfg owns it) --------------------------
# album.cfg is the only source of truth for:
#   showcase = true|false   -> is this a showcase album? (★ on /albums)
#   featured = a.jpg, b.jpg -> which photos are featured (welcome hero,
#                              /api/showcase, the featured hero slideshow of
#                              the album and its parents); paths may point
#                              into sub-folders, bare filenames also match
#                              anywhere in the subtree, and `*`/`all`
#                              features every photo directly in the album.
# A missing key simply means "not featured" — nothing is ever inferred from
# a file or folder name.
def album_is_showcase(album: str, cfg: dict[str, list[str]] | None = None) -> bool:
    cfg = config.album_config(album) if cfg is None else cfg
    return "showcase" in cfg and cfgio.as_bool(cfgio.first(cfg, "showcase"))


def resolve_photo_refs(album: str, items: list[str]) -> list[str]:
    """Resolve photo references from an album.cfg list value (`featured`,
    `order`) to indexed rel_paths, keeping the given order (deduped). Each
    item is a path relative to the album (sub-folders allowed); an item that
    isn't found at that exact path falls back to a fuzzy match inside the
    album's subtree, so a parent cfg can reference sub-folder photos:
      * a bare filename matches that filename anywhere in the subtree
        (every same-named file),
      * a path matches any photo whose rel_path ends with it.
    The fallback normalizes like `album_order` does — case-insensitive, so
    `Osaka/PIC.jpg` also finds `osaka/pic.jpg`."""
    c = db.conn()
    prefix = album + "/"
    subtree = None  # fallback pool, loaded once and only if something misses
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip().strip("/")
        if not item:
            continue
        rel = item if (item == album or item.startswith(prefix)) else f"{album}/{item}"
        row = c.execute("SELECT rel_path FROM images WHERE rel_path = ?", (rel,)).fetchone()
        if row:
            matches = [row["rel_path"]]
        else:
            if subtree is None:
                # substr() (not LIKE) keeps `_`/`%` in album names literal.
                subtree = c.execute(
                    "SELECT rel_path, filename FROM images "
                    "WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY rel_path",
                    (album, len(prefix), prefix),
                ).fetchall()
            key = album_order_key(item)
            if "/" in key:
                matches = [r["rel_path"] for r in subtree
                           if (k := album_order_key(r["rel_path"])) == key
                           or k.endswith("/" + key)]
            else:
                matches = [r["rel_path"] for r in subtree
                           if album_order_key(r["filename"]) == key]
        for rel_path in matches:
            if rel_path not in seen:
                seen.add(rel_path)
                out.append(rel_path)
    return out


def _resolve_featured(album: str, items: list[str]) -> set[str]:
    """Resolve an album.cfg `featured` list to a set of indexed rel_paths.
    `*`/`all` features every photo directly in the album; everything else
    resolves like resolve_photo_refs."""
    if any(i.strip().lower() in ("*", "all") for i in items):
        c = db.conn()
        return {r["rel_path"] for r in c.execute("SELECT rel_path FROM images WHERE album = ?", (album,))}
    return set(resolve_photo_refs(album, items))


def recompute_featured() -> None:
    """Recompute the `is_showcase` flag for every photo from the album.cfg
    `featured` lists. The single owner of the column — runs at startup and
    after every scan / album.cfg change."""
    c = db.conn()
    featured: set[str] = set()
    for album in albums_with_ancestors():
        cfg = config.album_config(album)
        if "featured" in cfg:
            featured |= _resolve_featured(album, cfg["featured"])
    # Apply the whole set in ONE statement. Clearing the column and adding the
    # flags back row by row would be visible to anyone reading mid-flight: the
    # app shares a single sqlite connection (db.py), so concurrent SELECTs run
    # inside this very transaction and saw the intermediate state — a reel that
    # rendered empty or half-filled while a recompute was in progress. A single
    # UPDATE has no intermediate state to observe. The set is passed as one JSON
    # array rather than N placeholders so a `featured = *` album can't run into
    # the host-parameter limit; `WHERE is_showcase <> …` keeps it to the rows
    # that actually change.
    want = "(rel_path IN (SELECT value FROM json_each(?)))"
    payload = json.dumps(sorted(featured))
    with db.lock():
        c.execute(f"UPDATE images SET is_showcase = {want} WHERE is_showcase <> {want}",
                  (payload, payload))
        c.commit()


# The watcher recomputes featured flags when an album.cfg changes, but its
# debounce means a save + immediate reload can still render stale flags (and
# a recompute racing a mid-write read may even drop them until the next
# scan). Album pages therefore stat their own album.cfg per request and run
# the recompute inline the moment the file's mtime differs from the last
# look — a cfg edit + reload then always shows the new featured state.
_cfg_seen_mtimes: dict[str, float] = {}
_cfg_seen_lock = threading.Lock()


def refresh_featured_on_cfg_change(album: str) -> None:
    # Called with the raw path straight off the URL, before the album is
    # known to exist — so the traversal guard stays here rather than leaning
    # on config.album_meta_dir, which cannot tell "bogus path" from "no folder"
    # and would let junk paths seed the mtime map and trigger a recompute.
    folder = (settings.photos_dir / album).resolve()
    try:
        folder.relative_to(settings.photos_dir)  # guard against path traversal
    except ValueError:
        return
    try:
        mtime = (folder / schema.ALBUM_META_DIR / "album.cfg").stat().st_mtime
    except OSError:
        mtime = 0.0  # missing file is a state too (cfg deleted -> refresh)
    with _cfg_seen_lock:
        stale = _cfg_seen_mtimes.get(album) != mtime
        if stale:
            _cfg_seen_mtimes[album] = mtime
    if stale:
        recompute_featured()


# ----- unlisted albums ---------------------------------------------------
# album.cfg `unlisted = true` keeps an album -- and every album under it -- out
# of each list the gallery draws: /albums, a parent's sub-album cards, the
# search, /stats, the welcome counters and feed, and the API's lists. Its own
# page, its photos and a /s/ link still answer, so it can be shared with
# whoever has the address. It is not a lock; the pages only say noindex.
#
# The question is asked many times per page (every album card counts its
# sub-albums), and each answer stats every album's cfg -- on an SMB share
# that is the slow part. So the answer is kept for a moment.
UNLISTED_TTL = 2.0
_unlisted_lock = threading.Lock()
_unlisted_cache: tuple[float, list[str]] | None = None


def forget_unlisted() -> None:
    """Drop the kept answer, so the next question reads the cfgs again."""
    global _unlisted_cache
    with _unlisted_lock:
        _unlisted_cache = None


def unlisted_roots() -> list[str]:
    """The topmost albums whose own album.cfg sets `unlisted`, sorted. The
    albums under them are unlisted with them and are not repeated here."""
    global _unlisted_cache
    now = time.monotonic()
    with _unlisted_lock:
        if _unlisted_cache is not None and now - _unlisted_cache[0] < UNLISTED_TTL:
            return _unlisted_cache[1]
    flagged = [a for a in all_album_nodes()
               if cfgio.as_bool(cfgio.first(config.album_config(a), "unlisted"))]
    roots = [a for a in flagged if not any(a.startswith(other + "/") for other in flagged)]
    with _unlisted_lock:
        _unlisted_cache = (now, roots)
    return roots


def _within(album: str, root: str) -> bool:
    return album == root or album.startswith(root + "/")


def is_unlisted(album: str | None) -> bool:
    """Whether `album` is unlisted, by its own cfg or an ancestor's."""
    return bool(album) and any(_within(album, root) for root in unlisted_roots())


def hidden_from(album: str, viewer: str | None) -> bool:
    """Whether `album` stays off what `viewer` lists (an album, or None for the
    gallery at large): it lies in an unlisted subtree `viewer` is not inside."""
    return any(_within(album, root) and not (viewer and _within(viewer, root))
               for root in unlisted_roots())


def unlisted_clause(column: str = "album", keep: str | None = None) -> tuple[str, list]:
    """(sql, params) that leaves the photos of unlisted subtrees out, over
    `column`. `keep` is the album being looked at: the unlisted subtree it lives
    in stays in, so an unlisted album's own page still shows its photos. "1"
    when nothing is unlisted. substr() rather than LIKE, as everywhere here, so
    `_` and `%` in a folder name stay literal."""
    parts: list[str] = []
    params: list = []
    for root in unlisted_roots():
        if keep and _within(keep, root):
            continue
        prefix = root + "/"
        parts.append(f"NOT ({column} = ? OR substr({column}, 1, ?) = ?)")
        params += [root, len(prefix), prefix]
    return (" AND ".join(parts) or "1"), params


def all_album_nodes() -> list[str]:
    """Every album node including the intermediate folders that hold only
    sub-folders — `images.album` alone lists just the ones with photos."""
    nodes: set[str] = set()
    for a in distinct_albums():
        parts = a.split("/")
        for i in range(1, len(parts) + 1):
            nodes.add("/".join(parts[:i]))
    return sorted(nodes)


def album_exists(album: str) -> bool:
    prefix = album + "/"
    return db.conn().execute(
        "SELECT 1 FROM images WHERE album = ? OR substr(album, 1, ?) = ? LIMIT 1",
        (album, len(prefix), prefix),
    ).fetchone() is not None


def resolve_album_path(album: str) -> str | None:
    """Map an album path off the URL to a real indexed album, tolerating
    different casing on any segment. None when nothing matches."""
    album = album.strip("/").replace("\\", "/")
    if not album or ".." in album.split("/"):
        return None
    if album_exists(album):
        return album
    key = album_order_key(album)
    return next((n for n in all_album_nodes() if album_order_key(n) == key), None)


def serialize_album(card: dict, base: str) -> dict:
    """One album card as the API returns it — the same numbers the /albums
    grid shows (recursive photo count, latest activity, cover from anywhere
    in the subtree) plus the cfg flags a client needs to render it: whether
    it is a showcase album and whether it is a collection."""
    album = card["album"]
    cfg = config.album_config(album)
    cover = card.get("cover")
    icon = card.get("icon") or theme.album_icon_url(album)
    return {
        "album": album,
        "name": card["name"],
        "count": card["count"],
        "latest": card["latest"],
        "sub_count": card["sub_count"],
        "is_showcase": album_is_showcase(album, cfg),
        "collection": config.album_collection(album, cfg),
        "tags": config.album_tags(album, cfg),
        "cover": {
            "rel_path": cover,
            "urls": {
                "thumb": f"/thumb/{cover}",
                "preview": f"/preview/{cover}",
                "thumb_abs": f"{base}/thumb/{cover}",
                "preview_abs": f"{base}/preview/{cover}",
            },
        } if cover else None,
        # album.cfg `icon = …` — the album's own mark, null when it sets none
        "icon": {"url": icon, "url_abs": f"{base}{icon}"} if icon else None,
        "urls": {
            "page": f"/album/{album}",
            "api": f"/api/album/{album}",
            "page_abs": f"{base}/album/{album}",
            "api_abs": f"{base}/api/album/{album}",
        },
    }


def album_reel(album: str, cfg: dict[str, list[str]], limit: int = 8) -> tuple[str, list[dict]]:
    """The album's hero slideshow (album.cfg `reel`), as (mode, rows).

    `featured` (the default) shows featured photos from this album AND its
    sub-albums, so a photo featured inside e.g. japan_2026/kansai surfaces on
    the japan_2026 page too — a showcase ALBUM doesn't auto-promote its
    contents, each photo opts in via album.cfg `featured`. The album's own
    `featured` list sets the order, exactly as written; anything featured by
    sub-album cfgs follows newest-first. `random` fills it from the subtree instead, `off` empties
    it. Shared by the album page and /api/album."""
    mode = (cfgio.first(cfg, "reel") or "").strip().lower()
    if mode in cfgio.FALSE:
        return "off", []
    if mode in ("random", "shuffle"):
        return "random", photos.random_subtree_rows(album, limit=limit)
    # fetch wide before trimming so a date-based LIMIT can't cut off early
    # entries of the configured list
    rows = photos.showcase_rows(album=album, limit=100, random_order=False, subtree=True)
    order_items = [i for i in cfg.get("featured", []) if i.strip().lower() not in ("*", "all")]
    if order_items:
        rows = photos.apply_curated_order(rows, resolve_photo_refs(album, order_items))
    return "featured", rows[:limit]
