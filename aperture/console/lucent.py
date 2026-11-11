"""Aperture as a Lucent tool (beta): pairing, the connector API, and
"Sign in with Lucent".

Lucent is the lucya.systems platform that ties the tools together, the way
vCenter ties ESXi hosts together. Aperture keeps working on its own: without a
pairing this module does nothing, and with one the local password still
opens the door. Lucent accounts and the local operator password are kept
apart: a session says which one opened it.

Three things live here:

  * PAIRING. `python -m aperture.cli lucent join <lucent-url> <code>` sends the
    one-time code from Lucent, together with a connector secret made here.
    Lucent answers with an OIDC client id and secret. Both land in
    DATA_DIR/console/lucent.json, mode 0600, next to the operator password.
  * THE CONNECTOR API, /lucent/v1/*, on the console's own port. Lucent calls it
    with the connector secret as a bearer token; only its SHA-256 is stored
    here. Status and the actions go through exactly the functions the console
    and the CLI use, and actions through the same control channel, so a scan
    asked for in Lucent is the same flag file `python -m aperture.cli scan`
    writes, with `by: lucent`.
  * SIGN-IN, OpenID Connect authorization code with PKCE. Aperture asked for
    HS256 ID tokens at pairing, signed with its own client secret, so checking
    them needs `hmac` and nothing else: no new dependency.

The contract both sides keep is CONNECTOR.md in the lucent repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import brand, health, ops
from ..runtime import settings
from . import security

log = logging.getLogger("console.lucent")

CONTRACT = 1
BY = "lucent"
STATE_COOKIE = "aperture_lucent"
PENDING_TTL = 600.0
CLOCK_SKEW = 60
HTTP_TIMEOUT = 8.0

router = APIRouter()

# state -> {nonce, verifier, browser, at}: sign-ins on their way through Lucent
_pending: dict[str, dict] = {}


# ----- the pairing file --------------------------------------------------
def _path() -> Path:
    return settings.console_dir / "lucent.json"


def pairing() -> dict | None:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_paired() -> bool:
    return pairing() is not None


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp, path)


def default_ui_url() -> str:
    host = settings.console_bind
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{settings.console_port}"


class LucentError(RuntimeError):
    pass


def _post(url: str, *, json_body: dict | None = None, form: dict | None = None,
          basic: tuple[str, str] | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": brand.GENERATOR}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    else:
        data = urllib.parse.urlencode(form or {}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if basic:
        raw = "%s:%s" % (urllib.parse.quote(basic[0], safe=""), urllib.parse.quote(basic[1], safe=""))
        headers["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            detail = body.get("detail") or body.get("error_description") or body.get("error")
        except ValueError:
            detail = e.reason
        raise LucentError(f"Lucent answered {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LucentError(f"Lucent is not reachable at {url}: {getattr(e, 'reason', e)}") from None


def join(lucent_url: str, code: str, *, ui_url: str | None = None,
         base_url: str | None = None) -> dict:
    """Pair this Aperture with a Lucent. `ui_url` is where a browser opens
    Aperture; `base_url` is where Lucent reaches it (the same, unless Lucent
    sits in another network than the browser)."""
    lucent_url = lucent_url.rstrip("/")
    ui_url = (ui_url or default_ui_url()).rstrip("/")
    base_url = (base_url or ui_url).rstrip("/")
    connector_secret = secrets.token_urlsafe(32)
    answer = _post(f"{lucent_url}/api/connect/pair", json_body={
        "code": code, "kind": "aperture", "name": "Aperture",
        "base_url": base_url, "ui_url": ui_url, "version": brand.VERSION,
        "redirect_uris": [f"{ui_url}/auth/lucent/callback"],
        "connector_secret": connector_secret, "id_token_alg": "HS256"})
    data = {"issuer": answer["issuer"].rstrip("/"), "tool_id": answer["tool_id"],
            "client_id": answer["client_id"], "client_secret": answer["client_secret"],
            "connector_secret_sha256": hashlib.sha256(connector_secret.encode()).hexdigest(),
            "ui_url": ui_url, "base_url": base_url, "paired_at": int(time.time())}
    _write(data)
    log.info("aperture: paired with Lucent at %s as %s", data["issuer"], data["client_id"])
    return data


def leave() -> bool:
    try:
        _path().unlink()
    except FileNotFoundError:
        return False
    return True


# ----- the connector API ---------------------------------------------------
def _require_lucent(request: Request) -> dict:
    p = pairing()
    if p is None:
        raise HTTPException(404, "this Aperture is not paired with Lucent")
    header = request.headers.get("authorization", "")
    sent = header[7:] if header.lower().startswith("bearer ") else ""
    digest = hashlib.sha256(sent.encode()).hexdigest()
    if not sent or not hmac.compare_digest(digest, p["connector_secret_sha256"]):
        raise HTTPException(401, "wrong connector secret")
    return p


ACTIONS = [
    {"id": "scan", "label": "Scan starten",
     "description": "Sucht neue und geänderte Fotos. Läuft im Indexer, nicht im Browser."},
    {"id": "pause", "label": "Indexer pausieren",
     "description": "Bleibt pausiert, auch über einen Neustart, bis du fortsetzt."},
    {"id": "resume", "label": "Indexer fortsetzen", "description": "Hebt eine Pause auf."},
]


@router.get("/lucent/v1/manifest")
def manifest(request: Request):
    p = _require_lucent(request)
    return {"contract": CONTRACT, "kind": "aperture", "name": "Aperture",
            "version": brand.VERSION, "ui_url": p["ui_url"],
            "description": "Galerie und Bildverwaltung", "icon": _icon(),
            "offers": ["media"], "media": {"browse": True, "writes": [] if settings.console_read_only else ["tags", "albums", "links"],
                      "langs": _langs()},
            "events": {"emits": ["media.tag_added", "media.tag_removed"]},
            "actions": [] if settings.console_read_only else ACTIONS}


def _icon() -> str | None:
    """Aperture's mark as SVG text; Lucent shows it beside the name (contract 1, optional)."""
    try:
        return (Path(__file__).parent / "static" / "logo" / "aperture.svg").read_text("utf-8")
    except OSError:
        return None


def _ago(ts) -> str:
    if not ts:
        return "noch nie"
    s = max(0, int(time.time() - float(ts)))
    if s < 90:
        return "gerade eben"
    if s < 5400:
        return f"vor {round(s / 60)} min"
    if s < 172800:
        return f"vor {round(s / 3600)} h"
    return f"vor {round(s / 86400)} Tagen"


def _num(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


# ---- exchange: media for a reference (contract 1, "offers": ["media"]) ------
# A reference is another tool's id for a thing, e.g. arca's inventory code. A
# photo belongs to it when it carries that id as a tag. Lucent asks; Aperture
# never learns who else is paired.
MEDIA_LIMIT = 48


def _item(r, ui_url: str) -> dict:
    return {"id": str(r["id"]), "title": r["filename"], "album": r["album"],
            "width": r["width"], "height": r["height"], "taken_at": r["taken_at"],
            "url": f"{ui_url}/albums/{urllib.parse.quote(r['album'])}"}


@router.get("/lucent/v1/media")
def media(request: Request, ref: str = "", album: str = "", q: str = "", offset: int = 0, limit: int = MEDIA_LIMIT):
    """Photos for a reference (a tag, exactly), an album (with its sub-albums)
    or a search in names and tags. One of the three is required."""
    p = _require_lucent(request)
    ref, album, q = ref.strip(), album.strip().strip("/"), q.strip()
    if not (ref or album or q) or max(len(ref), len(album), len(q)) > 200:
        raise HTTPException(400, "ref, album or q is required")
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    from .. import db
    base = "SELECT DISTINCT i.id, i.album, i.filename, i.width, i.height, i.taken_at FROM images i"
    if ref:
        sql = base + (" JOIN image_tags it ON it.image_id = i.id JOIN tags t ON t.id = it.tag_id"
                      " WHERE t.name = ? COLLATE NOCASE")
        args = [ref]
    elif album:
        sql = base + " WHERE i.album = ? OR substr(i.album, 1, ?) = ?"
        args = [album, len(album) + 1, album + "/"]
    else:
        like = f"%{q}%"
        sql = base + (" LEFT JOIN image_tags it ON it.image_id = i.id LEFT JOIN tags t ON t.id = it.tag_id"
                      " WHERE i.filename LIKE ? OR i.album LIKE ? OR t.name LIKE ?")
        args = [like, like, like]
    c = db.conn()
    total = c.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
    rows = c.execute(sql + " ORDER BY i.taken_at DESC, i.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return {"ref": ref, "album": album, "q": q, "total": total, "items": [_item(r, p["ui_url"]) for r in rows]}


@router.get("/lucent/v1/media/albums")
def media_albums(request: Request):
    _require_lucent(request)
    from .. import albums, db
    c = db.conn()
    out = []
    for name in sorted(albums.all_album_nodes(), key=str.lower):
        row = c.execute("SELECT COUNT(*) AS n, MAX(taken_at) AS last FROM images WHERE album = ? OR substr(album, 1, ?) = ?",
                        (name, len(name) + 1, name + "/")).fetchone()
        cover = c.execute("SELECT id FROM images WHERE album = ? OR substr(album, 1, ?) = ? ORDER BY taken_at DESC LIMIT 1",
                          (name, len(name) + 1, name + "/")).fetchone()
        out.append({"id": name, "title": name.rsplit("/", 1)[-1], "parent": name.rpartition("/")[0],
                    "count": row["n"], "last": row["last"], "cover": str(cover["id"]) if cover else None})
    return {"albums": out}


_EXIF = (("Make", "Kamera"), ("Model", "Modell"), ("LensModel", "Objektiv"), ("FNumber", "Blende"),
         ("ExposureTime", "Belichtung"), ("ISOSpeedRatings", "ISO"), ("FocalLength", "Brennweite"))


def _detail(image_id: int) -> dict:
    from .. import db
    from .app import lib
    r = db.conn().execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "no such photo")
    p = pairing()
    try:
        exif = json.loads(r["exif_json"] or "{}")
    except ValueError:
        exif = {}
    facts = [{"label": "Album", "value": r["album"]}, {"label": "Datei", "value": r["filename"]}]
    if r["width"] and r["height"]:
        facts.append({"label": "Größe", "value": f"{r['width']} × {r['height']}"})
    if r["taken_at"]:
        facts.append({"label": "Aufgenommen", "value": str(r["taken_at"])})
    if r["size"]:
        size = r["size"]
        facts.append({"label": "Datei-Größe", "value": f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{max(1, round(size / 1e3))} KB"})
    facts += [{"label": label, "value": str(exif[k])} for k, label in _EXIF if exif.get(k) not in (None, "")]
    return {**_item(r, p["ui_url"]), "rel": r["rel_path"], "tags": lib.read_tags(r["rel_path"]), "facts": facts}


@router.get("/lucent/v1/media/{image_id:int}")
def media_detail(request: Request, image_id: int):
    _require_lucent(request)
    return _detail(image_id)


@router.put("/lucent/v1/media/{image_id:int}/tags")
async def media_tags(request: Request, image_id: int):
    """Replace a photo's tags on behalf of a Lucent person (`by`). The same
    write as the console's: the sidecar, an audit line, an event, a rescan of
    its album so lookups by tag see it."""
    _require_lucent(request)
    if settings.console_read_only:
        raise HTTPException(403, "Aperture is mounted read-only")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "expected JSON")
    tags = body.get("tags")
    if not isinstance(tags, list) or len(tags) > 100:
        raise HTTPException(400, "tags must be a list")
    d = _detail(image_id)
    from ..paths import PathRefused, sidecar_target
    from .. import schema
    from .app import lib
    try:
        sidecar_target(settings.photos_dir, d["rel"], is_image=schema.is_image)
    except PathRefused as exc:
        raise HTTPException(400, str(exc))
    lib.write_tags(d["rel"], [str(t)[:80] for t in tags])
    by = body.get("by") or {}
    security.audit(request, "tags set", d["rel"], extra={"by": f"lucent:{str(by.get('email') or '')[:120]}"})
    try:
        ops.request_scan(album=d["album"], by=BY)
    except Exception:          # no indexer running: the next scan picks it up
        pass
    return _detail(image_id)


def _langs() -> list[str]:
    from .. import schema
    return list(schema.LANGS)


async def _as_person(request: Request) -> dict:
    """A write's body, with the Lucent person noted for Aperture's audit log."""
    _require_lucent(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "expected JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    by = body.get("by") or {}
    request.state.lucent_by = f"lucent:{str(by.get('email') or '?')[:120]}"
    return body


def _rels(ids) -> list[str]:
    from .. import db
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise HTTPException(400, "ids must be a list of 1 to 500 photo ids")
    try:
        nums = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "ids must be photo ids")
    rows = db.conn().execute(f"SELECT rel_path, album FROM images WHERE id IN ({','.join('?' * len(nums))})", nums).fetchall()
    return rows


def _rescan(albums_: set[str]) -> None:
    for a in albums_:
        try:
            ops.request_scan(album=a, by=BY)
        except Exception:
            pass


@router.get("/lucent/v1/media/tags")
def media_tags_all(request: Request):
    _require_lucent(request)
    from .app import lib
    counts = lib.all_tags()
    return {"tags": [{"name": n, "count": c} for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]}


@router.post("/lucent/v1/media/tags/bulk")
async def media_tags_bulk(request: Request):
    """Add and remove tags on many photos at once; each keeps its other tags."""
    body = await _as_person(request)
    from .app import tags_write
    rows = _rels(body.get("ids"))
    out = tags_write(request, {"photos": [r["rel_path"] for r in rows],
                               "add": body.get("add") or [], "remove": body.get("remove") or []})
    _rescan({r["album"] for r in rows})
    return {"ok": True, "changed": out.get("changed", 0)}


@router.post("/lucent/v1/media/tags/rename")
async def media_tags_rename(request: Request):
    body = await _as_person(request)
    from .app import tags_rename
    out = tags_rename(request, {"from": body.get("from"), "to": body.get("to")})
    _rescan({p.rpartition("/")[0] for p in out.get("photos", [])})
    return {"ok": True, "changed": out.get("changed", 0)}


@router.post("/lucent/v1/media/tags/delete")
async def media_tags_delete(request: Request):
    body = await _as_person(request)
    from .app import tags_delete
    out = tags_delete(request, {"tag": body.get("tag")})
    _rescan({p.rpartition("/")[0] for p in out.get("photos", [])})
    return {"ok": True, "changed": out.get("changed", 0)}


@router.get("/lucent/v1/media/album")
def media_album(request: Request, album: str = ""):
    """One album for Lucent's album page: its descriptions and the links that lead to it."""
    _require_lucent(request)
    from .. import links
    from .app import _album_or_400, lib
    name = _album_or_400(album)
    descs = {lang: lib.read_desc(name, lang) for lang in _langs()}
    to_it = [e for e in links.entries() if e["kind"] != "photo" and (e["target"] or "").strip("/") == name]
    return {"id": name, "title": name.rsplit("/", 1)[-1], "descriptions": descs,
            "links": [{"slug": e["slug"], "address": links.address(e["slug"])} for e in to_it]}


@router.put("/lucent/v1/media/album/description")
async def media_album_description(request: Request):
    body = await _as_person(request)
    from .app import album_description
    return album_description(request, {"album": body.get("album"), "lang": body.get("lang"), "text": body.get("text")})


@router.get("/lucent/v1/media/links")
def media_links(request: Request):
    _require_lucent(request)
    from .. import links
    return {"links": [{**e, "address": links.address(e["slug"])} for e in links.entries()]}


@router.put("/lucent/v1/media/links")
async def media_links_write(request: Request):
    body = await _as_person(request)
    from .app import links_write
    links_write(request, {"slug": body.get("slug"), "target": body.get("target"), "was": body.get("was")})
    return media_links(request)


@router.delete("/lucent/v1/media/links")
async def media_links_delete(request: Request, slug: str = ""):
    body = await _as_person(request) if request.headers.get("content-length", "0") != "0" else {}
    _require_lucent(request)
    from .app import api_links_delete
    api_links_delete(request, slug or str(body.get("slug") or ""))
    return media_links(request)


@router.get("/lucent/v1/media/changes")
def media_changes(request: Request, limit: int = 20):
    """Aperture's own log of writes, newest first (who, what, where)."""
    _require_lucent(request)
    rows = security.recent(max(1, min(limit, 100)))
    return {"changes": [{"at": r.get("ts"), "action": r.get("action"), "target": r.get("target"),
                         "by": r.get("by") or ("Konsole" if r.get("session") else r.get("ip") or "")} for r in rows]}


@router.get("/lucent/v1/media/{image_id:int}/preview")
def media_preview(request: Request, image_id: int):
    """The bigger picture for Lucent's photo page: the gallery's preview when
    it exists, the thumbnail otherwise."""
    _require_lucent(request)
    from .. import db, scanner
    row = db.conn().execute("SELECT rel_path FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such photo")
    prev = ops.derivatives(row["rel_path"])["preview"]
    if prev.is_file():
        return FileResponse(prev, media_type="image/webp" if prev.suffix == ".webp" else "image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})
    thumb = scanner.ensure_thumb(settings.photos_dir, settings.thumbs_dir, row["rel_path"], settings.thumb_size)
    if thumb is None:
        raise HTTPException(415, "cannot decode this photo")
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/lucent/v1/media/{image_id:int}/thumb")
def media_thumb(request: Request, image_id: int):
    _require_lucent(request)
    from .. import db, scanner
    row = db.conn().execute("SELECT rel_path FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such photo")
    thumb = scanner.ensure_thumb(settings.photos_dir, settings.thumbs_dir, row["rel_path"], settings.thumb_size)
    if thumb is None:
        raise HTTPException(415, "cannot decode this photo")
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})


# ---- events (contract 1, "events": {"emits": [...]}) ------------------------
# The outbox: numbered events in a small sqlite file beside the pairing file,
# kept 30 days. Lucent takes them by number; a poke only says "look now".
# Nothing here waits on Lucent: a poke runs in a thread and may fail.
OUTBOX_DAYS = 30


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _outbox():
    """The outbox, opened for one use and closed after; commits on success."""
    import sqlite3
    c = sqlite3.connect(settings.console_dir / "lucent_outbox.sqlite", timeout=5)
    c.row_factory = sqlite3.Row
    try:
        c.execute("CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,"
                  " type TEXT NOT NULL, subject TEXT, data TEXT NOT NULL DEFAULT '{}')")
        with c:
            yield c
    finally:
        c.close()


def emit(type_: str, subject: str, data: dict) -> None:
    """Put an event in the outbox and poke Lucent. Silent when unpaired."""
    if not is_paired():
        return
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    try:
        with _outbox() as c:
            c.execute("INSERT INTO outbox (at, type, subject, data) VALUES (?,?,?,?)",
                      (now.isoformat(timespec="seconds"), type_, subject, json.dumps(data)))
            c.execute("DELETE FROM outbox WHERE at < ?", ((now - timedelta(days=OUTBOX_DAYS)).isoformat(),))
    except Exception:              # an event is never worth failing the write it reports
        log.exception("aperture: could not write the Lucent outbox")
        return
    _poke_soon()


_poke_lock = __import__("threading").Lock()
_poke_due = False


def _poke_soon() -> None:
    """One poke for a burst of events: a rename across four hundred photos
    is eight hundred events and still one poke."""
    import threading
    global _poke_due
    with _poke_lock:
        if _poke_due:
            return
        _poke_due = True

    def poke():
        global _poke_due
        with _poke_lock:
            _poke_due = False
        p = pairing()
        if not p:
            return
        token = base64.b64encode(f"{p['client_id']}:{p['client_secret']}".encode()).decode()
        req = urllib.request.Request(f"{p['issuer']}/api/connect/poke", data=b"{}", method="POST",
                                     headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:          # Lucent down or slow: it looks once a minute anyway
            log.info("aperture: poke to Lucent failed; it will catch up")
    t = threading.Timer(0.5, poke)
    t.daemon = True
    t.start()


def tags_changed(rel: str, before: list[str], after: list[str]) -> None:
    """Called by the library on every tag write: one event per tag that came or went."""
    old, new = {t.lower(): t for t in before}, {t.lower(): t for t in after}
    album, _, name = rel.rpartition("/")
    for key, tag in new.items():
        if key not in old:
            emit("media.tag_added", tag, {"rel": rel, "title": name, "album": album})
    for key, tag in old.items():
        if key not in new:
            emit("media.tag_removed", tag, {"rel": rel, "title": name, "album": album})


@router.get("/lucent/v1/events")
def events(request: Request, after: int = 0, limit: int = 200):
    _require_lucent(request)
    limit = max(1, min(limit, 500))
    with _outbox() as c:
        rows = c.execute("SELECT * FROM outbox WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
    return {"events": [{"seq": r["seq"], "at": r["at"], "type": r["type"], "subject": r["subject"],
                        "data": json.loads(r["data"] or "{}")} for r in rows]}


def status_report() -> dict:
    st = ops.status()
    h = health.snapshot()
    index = st["index"]
    server = st.get("server") or {}
    last = server.get("last_scan") or {}
    if h["state"] == "down":
        state, summary = "down", f"Speicher nicht erreichbar: {h.get('reason') or 'unbekannt'}"
    elif not st["live"]:
        state, summary = "warn", "Kein Indexer läuft. Scans bleiben in der Warteschlange."
    elif st["paused"]:
        state, summary = "warn", "Der Indexer ist pausiert."
    else:
        state = "warn" if h["state"] == "warn" else "ok"
        summary = f"{_num(index['images'])} Fotos in {_num(index['albums'])} Alben"
    if st["live"] and server.get("progress"):
        indexer = ("Scannt", "ok")
    elif st["live"]:
        indexer = ("Pausiert", "warn") if st["paused"] else ("Bereit", "ok")
    else:
        indexer = ("Aus", "warn")
    warn_checks = [c for c in h["checks"] if c.get("level") != "ok"]
    return {
        "state": state, "summary": summary, "checked_at": h.get("checked_at"),
        "cards": [
            {"label": "Fotos", "value": _num(index["images"]), "note": f"{_num(index['albums'])} Alben"},
            {"label": "Archiv", "value": ops.bytes_h(index["bytes"])},
            {"label": "Indexer", "value": indexer[0], "state": indexer[1],
             "note": ("Letzter Scan " + _ago(last.get("finished_at") or last.get("started_at"))) if last else None},
            {"label": "Speicher", "value": "Ok" if not warn_checks else f"{len(warn_checks)} Hinweis(e)",
             "state": None if not warn_checks else "warn",
             "note": "; ".join(c.get("detail", "") for c in warn_checks)[:120] or "erreichbar, genug Platz"},
        ],
        "facts": [
            {"label": "Rolle", "value": settings.role},
            {"label": "Fotoordner", "value": str(settings.photos_dir)},
            {"label": "Scan-Intervall", "value": f"{settings.scan_interval} s"},
            {"label": "Tags", "value": _num(index["tags"])},
            {"label": "Hervorgehoben", "value": _num(index["featured"])},
            {"label": "Index", "value": ops.bytes_h(index["db_bytes"])},
        ],
    }


@router.get("/lucent/v1/status")
def status(request: Request):
    _require_lucent(request)
    return status_report()


@router.post("/lucent/v1/actions/{action}")
async def action(request: Request, action: str):
    _require_lucent(request)
    if settings.console_read_only:
        raise HTTPException(403, "Aperture is mounted read-only")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    by = BY + (f" ({body['by']})" if isinstance(body, dict) and isinstance(body.get("by"), str) else "")
    if action == "scan":
        req = ops.request_scan(by=BY)
        security.audit(request, "scan requested", "(whole gallery)",
                       extra={"by": by[:120], "request_id": req["id"]})
        live = ops.status()["live"]
        return {"ok": True, "message": "Scan angefordert." if live else
                "Scan angefordert. Es läuft gerade kein Indexer, er startet mit dem nächsten.",
                "request_id": req["id"]}
    if action == "pause":
        ops.pause("paused from Lucent", by=BY)
        security.audit(request, "indexer paused", "(indexer)", extra={"by": by[:120]})
        return {"ok": True, "message": "Indexer pausiert."}
    if action == "resume":
        lifted = ops.resume()
        security.audit(request, "indexer resumed", "(indexer)", extra={"by": by[:120]})
        return {"ok": True, "message": "Indexer läuft weiter." if lifted else "Es gab keine Pause."}
    raise HTTPException(404, f"no such action: {action!r}")


# ----- sign in with Lucent -------------------------------------------------
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_id_token(token: str, p: dict, nonce: str) -> dict:
    """An HS256 ID token, checked the way OIDC Core 3.1.3.7 says: signature with
    the client secret, then iss, aud, exp, iat and the nonce."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64u(head_b64))
        claims = json.loads(_unb64u(body_b64))
        sig = _unb64u(sig_b64)
    except (ValueError, TypeError):
        raise LucentError("the ID token is not a JWT") from None
    if header.get("alg") != "HS256":
        raise LucentError("unexpected ID token algorithm")
    expected = hmac.new(p["client_secret"].encode(), f"{head_b64}.{body_b64}".encode(),
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise LucentError("the ID token signature does not match")
    now = time.time()
    aud = claims.get("aud")
    if claims.get("iss") != p["issuer"]:
        raise LucentError("the ID token comes from another issuer")
    if aud != p["client_id"] and not (isinstance(aud, list) and p["client_id"] in aud):
        raise LucentError("the ID token is for another client")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - CLOCK_SKEW:
        raise LucentError("the ID token has expired")
    if not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + CLOCK_SKEW:
        raise LucentError("the ID token was issued in the future")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise LucentError("the ID token nonce does not match")
    return claims


def _sweep_pending(now: float) -> None:
    for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL]:
        _pending.pop(k, None)


@router.get("/auth/lucent")
def sign_in(request: Request):
    p = pairing()
    if p is None:
        raise HTTPException(404, "this Aperture is not paired with Lucent")
    now = time.time()
    _sweep_pending(now)
    state, nonce, verifier, browser = (secrets.token_urlsafe(24), secrets.token_urlsafe(24),
                                       secrets.token_urlsafe(48), secrets.token_urlsafe(24))
    _pending[state] = {"nonce": nonce, "verifier": verifier, "browser": browser, "at": now}
    challenge = _b64u(hashlib.sha256(verifier.encode()).digest())
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": p["client_id"],
        "redirect_uri": f"{p['ui_url']}/auth/lucent/callback", "scope": "openid email profile",
        "state": state, "nonce": nonce, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    r = RedirectResponse(f"{p['issuer']}/oidc/authorize?{q}", status_code=303)
    # Lax, not Strict: it has to come back on the top-level GET from Lucent.
    # It only ties the callback to this browser; the session cookie stays Strict.
    r.set_cookie(STATE_COOKIE, browser, max_age=int(PENDING_TTL), httponly=True, samesite="lax",
                 secure=request.url.scheme == "https", path="/auth/lucent")
    return r


_HOP = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url=/"><title>Aperture</title></head>
<body><p>Angemeldet. <a href="/">Weiter zu Aperture</a></p></body></html>"""


@router.get("/auth/lucent/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", iss: str = ""):
    p = pairing()
    pending = _pending.pop(state, None) if state else None
    fail = RedirectResponse("/login?reason=lucent", status_code=303)
    fail.delete_cookie(STATE_COOKIE, path="/auth/lucent")
    if p is None or pending is None or time.time() - pending["at"] > PENDING_TTL:
        log.warning("aperture: Lucent sign-in with an unknown or stale state")
        return fail
    if not hmac.compare_digest(request.cookies.get(STATE_COOKIE, ""), pending["browser"]):
        log.warning("aperture: Lucent sign-in finished in another browser than it began")
        return fail
    if error:
        log.warning("aperture: Lucent refused the sign-in: %s", error)
        return fail
    if iss and iss.rstrip("/") != p["issuer"]:
        log.warning("aperture: Lucent sign-in answered by another issuer: %s", iss)
        return fail
    try:
        answer = _post(f"{p['issuer']}/oidc/token", form={
            "grant_type": "authorization_code", "code": code, "code_verifier": pending["verifier"],
            "redirect_uri": f"{p['ui_url']}/auth/lucent/callback"},
            basic=(p["client_id"], p["client_secret"]))
        claims = verify_id_token(answer.get("id_token", ""), p, pending["nonce"])
    except LucentError as e:
        log.warning("aperture: Lucent sign-in failed: %s", e)
        return fail
    who = claims.get("email") or f"lucent:{claims.get('sub')}"
    sid, _sess = security.login_external(request, via="lucent", who=who)
    security.audit(request, "signed in", "(console)", extra={"via": "lucent", "who": who})
    # A page, not a redirect: the session cookie is SameSite=Strict, and a
    # redirect chain that began on Lucent would arrive without it.
    r = HTMLResponse(_HOP)
    security.issue_cookie(r, request, sid)
    r.delete_cookie(STATE_COOKIE, path="/auth/lucent")
    return r
