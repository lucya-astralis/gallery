# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright © 2026 lucya.systems — https://lucya.systems
"""The rest of Aperture, described for Lucent (CONNECTOR.md section 5,
"pages", "sections", "actions").

Lucent already draws the photos, the tags and the links natively. Everything
else the console does lives here, as pages Lucent draws without learning what
an album.cfg is:

  * Übersicht — the to-do strip, the archive in figures, the indexer, what
    the check found, the last writes;
  * Alben — every folder as a row; one album's editor in tabs (Details,
    Fotos, Aussehen, Text, Dateien, Rohdatei, Verlauf), checks with their fix,
    one key set on many albums at once;
  * Website — gallery.cfg in the console's tabs (Identität, Aussehen,
    Startseite, Footer, Dateien, Rohdatei, Verlauf);
  * System — indexer, storage and its jobs, the health check, privacy, the
    backup, the activity log, tag hygiene, the password, about.

Every write goes through the functions the console's own routes use: the
same path resolver, the same backup, the same audit line (with the Lucent
person as `by`), the same control channel for anything the indexer owns. A
photograph is never written from here, and the index never is either.
"""

from __future__ import annotations

import base64
import difflib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import brand, cfgio, checks, control, db, health, links, ops, reports, schema, update_check
from ..runtime import settings
from . import lucent, security

router = APIRouter()

# `after` sets each page among Lucent's own parts (Fotos, Tags, Links) in the
# console's order: Übersicht, Fotos, Alben, Tags, Website, Links, System.
PAGES = [
    {"id": "uebersicht", "label": "Übersicht", "icon": "chart", "after": "start"},
    {"id": "alben", "label": "Alben", "icon": "folder", "after": "media"},
    {"id": "website", "label": "Website", "icon": "globe", "after": "tags"},
    {"id": "system", "label": "System", "icon": "server", "after": "links"},
]

# media detail and selection actions (manifest media.actions / media.bulk)
BULK = [{"id": "feature", "label": "Hervorheben"}, {"id": "unfeature", "label": "Nicht mehr hervorheben"}]

LANG_NAMES = {"en": "Englisch", "de": "Deutsch", "jp": "Japanisch"}

# what the console calls each key, in words
KEY_LABELS = {
    "name": "Name", "collection": "Sammlung", "showcase": "Schaufenster", "unlisted": "Nicht gelistet",
    "cover": "Titelbild", "featured": "Hervorgehoben", "order": "Reihenfolge", "reel": "Diashow",
    "sort": "Sortierung", "tags": "Album-Tags", "effect": "Effekt", "icon": "Icon", "font": "Schrift",
    "font_scale": "Schriftgröße", "accent": "Akzentfarbe", "wallpaper": "Hintergrund",
    "wallpaper_mobile": "Hintergrund mobil", "wallpaper_tint": "Hintergrund Farbe",
    "wallpaper_dim": "Hintergrund Helligkeit", "loc": "Ort", "stat": "Eigene Angaben",
    "stats": "Angabenblock", "welcome": "Startseite (beide)", "welcome_desktop": "Startseite Desktop",
    "welcome_mobile": "Startseite mobil", "album_order": "Album-Reihenfolge", "album_sort": "Album-Sortierung",
    "palette": "Palette", "faces": "Schriften der Oberfläche", "site_name": "Name der Seite",
    "site_sub": "Zweite Zeile", "site_hero": "Großes Wort", "site_desc": "Beschreibung",
    "site_desc_en": "Beschreibung EN", "site_desc_de": "Beschreibung DE", "site_desc_jp": "Beschreibung JP",
    "logo": "Logo", "favicon": "Favicon", "operator": "Betreiber", "operator_url": "Betreiber-Link",
    "operator_pfp": "Betreiber-Bild", "privacy_url": "Datenschutz-Link", "imprint_url": "Impressum-Link",
    "badges": "Buttons im Footer", "credit": "Urheber (EXIF)",
}

ALBUM_TABS = [
    ("details", "Details", schema.ALBUM_TOPICS["details"]),
    ("fotos", "Fotos", schema.ALBUM_TOPICS["fotos"]),
    ("aussehen", "Aussehen", schema.ALBUM_TOPICS["aussehen"]),
    ("text", "Text", []),
    ("dateien", "Dateien", []),
    ("roh", "Rohdatei", []),
    ("verlauf", "Verlauf", []),
]
SITE_TABS = [
    ("identitaet", "Identität", schema.GALLERY_TOPICS["identitaet"]),
    ("aussehen", "Aussehen", schema.GALLERY_TOPICS["aussehen"]),
    ("startseite", "Startseite", schema.GALLERY_TOPICS["startseite"]),
    ("footer", "Footer", schema.GALLERY_TOPICS["footer"]),
    ("dateien", "Dateien", []),
    ("roh", "Rohdatei", []),
    ("verlauf", "Verlauf", []),
]
SYSTEM_TABS = [("indexer", "Indexer"), ("speicher", "Speicher"), ("pruefung", "Prüfung"),
               ("datenschutz", "Datenschutz"), ("sicherung", "Sicherung"), ("aktivitaet", "Aktivität"),
               ("tags", "Tags"), ("passwort", "Passwort"), ("ueber", "Über")]
# the console's System side list: its groups and marks, for Lucent's side list
SYSTEM_GROUPS = {"indexer": ("Maschine", "server"), "speicher": ("Maschine", "folder"),
                 "pruefung": ("Maschine", "check"), "datenschutz": ("Daten", "info"),
                 "sicherung": ("Daten", "down"), "aktivitaet": ("Daten", "clock"), "tags": ("Daten", "tag"),
                 "passwort": ("Aperture", "user"), "ueber": ("Aperture", "info")}

DOCTOR_LABELS = {
    "missing_file": "Datei fehlt", "unindexed": "Nicht im Index", "stale_index": "Index veraltet",
    "orphan_derivative": "Übrige Vorschaubilder", "unreadable": "Nicht lesbar", "config": "Konfiguration",
    "featured_drift": "Hervorhebung abweichend", "database": "Datenbank",
}
# the job that fixes a kind of problem, as the console's buttons
DOCTOR_FIX = {"unindexed": "scan", "stale_index": "scan", "missing_file": "scan",
              "orphan_derivative": "prune", "featured_drift": "featured"}


class Bad(Exception):
    """A value the tool refuses: Lucent shows the sentence (422)."""


# ----- small helpers ---------------------------------------------------------
def _app():
    from . import app as console_app
    return console_app


def _bytes(n) -> str:
    return ops.bytes_h(int(n or 0))


def _when(ts) -> str:
    if not ts:
        return "–"
    try:
        if isinstance(ts, (int, float)):
            d = datetime.fromtimestamp(float(ts), timezone.utc)
        else:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.astimezone().strftime("%d.%m.%Y %H:%M")
    except (ValueError, OSError):
        return str(ts)


def _label(key: str) -> str:
    return KEY_LABELS.get(key, key)


def _issue_level(i: dict) -> str:
    return "err" if i.get("level") == "error" else "warn"


def _view(request: Request) -> dict[str, str]:
    return {k[2:]: v[:400] for k, v in request.query_params.items() if k.startswith("v_")}


def _go(page: str, **view) -> str:
    q = "&".join(f"v_{k}={quote(str(v), safe='')}" for k, v in view.items() if v not in (None, ""))
    return f"p/{page}" + (f"?{q}" if q else "")


def _ro() -> bool:
    return bool(settings.console_read_only)


def _act(id_: str, label: str, fields=None, **kw) -> dict:
    a = {"id": id_, "label": label, "fields": fields or []}
    a.update({k: v for k, v in kw.items() if v is not None})
    return a


def _hidden(name: str, value) -> dict:
    return {"name": name, "type": "hidden", "value": str(value)}


# ----- cfg keys as form fields -----------------------------------------------
def _spec(key: str, gallery: bool) -> dict:
    return (schema.GALLERY_SPEC.get(key) if gallery else None) or schema.KEY_SPEC.get(key, {})


def _help(key: str, gallery: bool) -> str:
    return (schema.GALLERY_HELP.get(key) if gallery else None) or schema.HELP.get(key, "")


def _is_lines(spec: dict) -> bool:
    return spec.get("type") in ("photo_list", "welcome", "album_list", "kv_list") or (
        spec.get("type") == "list" and spec.get("multiline"))


def _to_form(values: list[str] | None, spec: dict) -> str:
    if not values:
        return ""
    if _is_lines(spec):
        return "\n".join(values)
    if spec.get("type") == "list" or spec.get("joined"):
        return ", ".join(values)
    return values[0]


def _from_form(raw: str, spec: dict):
    raw = (raw or "").strip()
    if not raw:
        return None
    if _is_lines(spec):
        return [line.strip() for line in raw.splitlines() if line.strip()]
    if spec.get("type") == "list":
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def _field(key: str, values: dict, gallery: bool, assets: list[dict], photos: list[str]) -> dict:
    spec = _spec(key, gallery)
    t = spec.get("type")
    current = _to_form(values.get(key), spec)
    help_ = f"{key} · {_help(key, gallery)}"
    f = {"name": key, "label": _label(key), "value": current, "help": help_}
    if t == "bool":
        f.update(type="select", options=[{"value": "", "label": "nicht gesetzt"},
                                         {"value": "true", "label": "ja"}, {"value": "false", "label": "nein"}])
        if current and current.lower() in cfgio.TRUE:
            f["value"] = "true"
        elif current and current.lower() in cfgio.FALSE:
            f["value"] = "false"
    elif t == "bool_off":
        f.update(type="select", options=[{"value": "", "label": "zeigen"}, {"value": "off", "label": "ausblenden"}])
        f["value"] = "off" if current and current.lower() in cfgio.FALSE else ""
    elif t == "choice":
        opts = [{"value": "", "label": "nicht gesetzt"}] + [{"value": c, "label": c} for c in spec.get("choices", [])]
        if current and current not in spec.get("choices", []):
            opts.append({"value": current, "label": current})
        f.update(type="select", options=opts)
    elif t in ("asset", "brand_asset"):
        exts = set(spec.get("exts") or [])
        names = [a["name"] for a in assets if Path(a["name"]).suffix.lower() in exts]
        opts = [{"value": "", "label": "keins"}] + [{"value": n, "label": n} for n in names]
        if current and current not in names:
            opts.append({"value": current, "label": f"{current} (fehlt)"})
        f.update(type="select", options=opts)
    elif t == "photo":
        opts = [{"value": "", "label": "automatisch (neuestes Foto)"}] + [{"value": p, "label": p} for p in photos[:2000]]
        if current and current not in photos:
            opts.append({"value": current, "label": f"{current} (nicht gefunden)"})
        f.update(type="select", options=opts, span="full")
    elif _is_lines(spec):
        f.update(type="textarea", rows=6 if t != "kv_list" else 4, mono=True)
        if t == "kv_list":
            f["placeholder"] = "Kamera: Fuji X-T5"
        elif t == "album_list":
            f["placeholder"] = "#Gruppe\nalbum_name"
        else:
            f["placeholder"] = "eine Zeile je Eintrag"
    else:
        f.update(type="text")
        if t == "color":
            f["placeholder"] = "#8b5cf6"
        elif t in ("number", "ratio"):
            lo, hi = spec.get("range", [None, None])
            f["placeholder"] = f"{lo}–{hi}" + (" oder off" if spec.get("off") else "")
        if spec.get("joined") or key.endswith("_url"):
            f["span"] = "full"
    return f


def _kv_values(keys: list[str], values: dict, gallery: bool) -> list[dict]:
    out = []
    for key in keys:
        spec = _spec(key, gallery)
        v = _to_form(values.get(key), spec)
        if _is_lines(spec) and v:
            n = len(values.get(key) or [])
            v = f"{n} Einträge" if n > 1 else v
        out.append({"label": _label(key), "value": v or "–", **({"tone": "acc"} if key in values else {})})
    return out


def _updates(keys: list[str], form: dict, gallery: bool) -> dict:
    """The form of one tab as the cfg patch the console's save would send."""
    patch = {}
    for key in keys:
        if key not in schema.KEY_SPEC:
            continue
        patch[key] = _from_form(form.get(key, ""), _spec(key, gallery))
    return _app()._updates_from(patch)


# ----- writes, through the console's own steps ---------------------------------
def _save_album(request: Request, album: str, updates: dict) -> list[dict]:
    a = _app()
    a._guard_write()
    path = a._cfg_target(album)
    before = security.sha256_of(path)
    cfg_file = cfgio.CfgFile.load(path)
    cfg_file.apply(updates, schema.KEY_SPEC)
    a._backup(path, "album")
    cfg_file.save(path)
    a._writes(request, "album.cfg", path, before)
    return checks.album(album, cfg_file.values())


def _save_gallery(request: Request, updates: dict) -> list[dict]:
    a = _app()
    a._guard_write()
    path = a._target(None, schema.GALLERY_CFG_NAME, scope="gallery")
    before = security.sha256_of(path)
    cfg_file = cfgio.CfgFile.load(path, cfgio.GROUP_KEYS)
    cfg_file.apply(updates, schema.KEY_SPEC)
    a._backup(path, "gallery")
    cfg_file.save(path)
    a._writes(request, "gallery.cfg", path, before)
    return checks.gallery(cfg_file.values())


def _save_raw(request: Request, album: str | None, text: str) -> list[dict]:
    a = _app()
    a._guard_write()
    if album is None:
        path = a._target(None, schema.GALLERY_CFG_NAME, scope="gallery")
        label, cfg_file = "gallery", cfgio.CfgFile(text, cfgio.GROUP_KEYS)
    else:
        path, label, cfg_file = a._cfg_target(album), "album", cfgio.CfgFile(text)
    before = security.sha256_of(path)
    a._backup(path, label)
    cfg_file.save(path)
    a._writes(request, f"{path.name} (raw)", path, before)
    return checks.album(album, cfg_file.values()) if album else checks.gallery(cfg_file.values())


def _upload(request: Request, scope: str, album: str | None, files: list[dict]) -> list[str]:
    """Files from Lucent into .album/ or .gallery/; album texts (album_de.md,
    tech-ja.md …) through the description route, as the console's Files tab."""
    a = _app()
    a._guard_write()
    done = []
    for f in files or []:
        name = Path(str(f.get("name") or "")).name
        try:
            payload = base64.b64decode(str(f.get("data_b64") or ""), validate=True)
        except ValueError:
            raise Bad(f"{name}: kaputt angekommen")
        if len(payload) > settings.console_max_upload:
            raise Bad(f"{name} ist größer als {settings.console_max_upload // 1048576} MB")
        if scope == "album" and name.lower().endswith(".md"):
            lang = _md_lang(name)
            if not lang:
                raise Bad(f"{name}: welche Sprache? album_de.md, album_en.md oder album_jp.md")
            a.album_description(request, {"album": album, "lang": lang,
                                          "text": payload.decode("utf-8", errors="replace")})
            done.append(name)
            continue
        target = a._target(album if scope == "album" else None, name, scope=scope,
                           allowed_exts=a._SCOPE_EXTS[scope])
        a._sniff(name, payload)
        before = security.sha256_of(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        a._backup(target, "asset")
        target.write_bytes(payload)
        a._writes(request, "asset uploaded", target, before)
        done.append(name)
    return done


def _md_lang(name: str) -> str | None:
    stem = Path(name).stem.lower()
    for lang in schema.LANGS:
        alias = {"jp": ("jp", "ja")}.get(lang, (lang,))
        if any(stem == f"album_{x}" or stem.endswith(f"-{x}") or stem.endswith(f"_{x}") for x in alias):
            return lang
    return None


def _delete_asset(request: Request, scope: str, album: str | None, name: str) -> None:
    a = _app()
    a._guard_write()
    target = a._target(album if scope == "album" else None, name, scope=scope, allowed_exts=a._SCOPE_EXTS[scope])
    if not target.is_file():
        raise Bad("Diese Datei gibt es nicht mehr.")
    before = security.sha256_of(target)
    a._backup(target, "asset")
    target.unlink()
    a._writes(request, "asset deleted", target, before)


def _restore(request: Request, file: str, album: str | None, lang: str | None, version: str) -> None:
    a = _app()
    a._guard_write()
    path, label = a._history_file(file, album, lang)
    found = a._version(a._backup_folder(path, label), version)
    data = found.read_bytes()
    before = security.sha256_of(path)
    a._backup(path, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    a._writes(request, f"{path.name} restored", path, before)


def _scan(request: Request, album: str | None = None, force: bool = False) -> str:
    req = ops.request_scan(album=album or None, force=force, by=lucent.BY)
    security.audit(request, "scan requested", album or "(whole gallery)",
                   extra={"force": force, "request_id": req["id"]})
    return "Scan angefordert." if ops.status()["live"] else \
        "Scan angefordert. Es läuft gerade kein Indexer, er startet mit dem nächsten."


def _job(request: Request, kind: str, album: str | None = None, rebuild_all: bool = False) -> str:
    try:
        job = ops.request_job(kind, album=album or None, rebuild_all=rebuild_all, by=lucent.BY)
    except ops.UnknownAlbum as exc:
        raise Bad(f"Das Album {exc.args[0]!r} gibt es nicht.")
    security.audit(request, f"job requested ({kind})", job["params"].get("album") or "(whole gallery)",
                   extra={"job_id": job["id"], "params": job["params"]})
    return f"Auftrag „{JOB_LABELS.get(kind, kind)}“ angefordert." + (
        "" if ops.status()["live"] else " Es läuft gerade kein Indexer, er startet mit dem nächsten.")


JOB_LABELS = {"rebuild": "Vorschaubilder bauen", "prune": "Übrige Vorschaubilder löschen",
              "featured": "Hervorhebungen neu schreiben", "gps_strip": "GPS aus Originalen entfernen",
              "scan": "Scan"}


# ----- what a page looks like --------------------------------------------------
def _albums_rows(filter_: str, find: str) -> tuple[list[dict], dict]:
    a = _app()
    lib = a.lib
    rows, counts = [], {"alle": 0, "ohne_text": 0, "probleme": 0}
    try:
        index = {r["album"]: r["photos"] for r in reports.albums_report()["albums"]}
    except Exception:
        index = {}
    for album in lib.album_paths():
        has_cfg = lib.cfg_path(album).is_file()
        langs = [lang for lang in schema.LANGS if lib.read_desc(album, lang).strip()]
        issues = checks.album(album) if has_cfg else []
        counts["alle"] += 1
        counts["ohne_text"] += 0 if (langs and has_cfg) else 1
        counts["probleme"] += 1 if issues else 0
        if filter_ == "ohne_text" and langs and has_cfg:
            continue
        if filter_ == "probleme" and not issues:
            continue
        if find and find.lower() not in album.lower():
            continue
        badges = []
        if has_cfg:
            badges.append({"label": "album.cfg"})
        if langs:
            badges.append({"label": "Text " + " ".join(l.upper() for l in langs), "tone": "ok"})
        else:
            badges.append({"label": "kein Text", "tone": "warn"})
        if issues:
            badges.append({"label": f"{len(issues)} Hinweis(e)", "tone": _issue_level(issues[0])})
        depth = album.count("/")
        rows.append({"title": album.rsplit("/", 1)[-1], "sub": album if depth else None, "depth": depth,
                     "badges": badges, "meta": f"{index.get(album, 0)} Fotos",
                     "go": _go("alben", album=album)})
    return rows, counts


def page_alben(view: dict) -> dict:
    album = view.get("album", "").strip().strip("/")
    if album:
        return _album_page(album, view)
    filter_ = view.get("filter") if view.get("filter") in ("ohne_text", "probleme") else ""
    find = view.get("find", "")[:80]
    rows, counts = _albums_rows(filter_, find)
    keys = [{"value": k, "label": f"{_label(k)} ({k})"} for k in schema.ALBUM_KEYS]
    actions = [] if _ro() else [
        _act("bulk", "Einen Schlüssel auf vielen Alben setzen", icon="list", fields=[
            {"name": "key", "label": "Schlüssel", "type": "select", "options": keys, "value": "unlisted"},
            {"name": "value", "label": "Wert", "type": "text", "value": "",
             "help": "Leer entfernt den Schlüssel. Listen mit Kommas oder einer Zeile je Eintrag."},
            {"name": "albums", "label": "Alben", "type": "textarea", "rows": 8, "mono": True,
             "value": "\n".join(r["sub"] or r["title"] for r in rows),
             "help": "Ein Album je Zeile. Vorgefüllt mit der Liste, die du gerade siehst."},
        ], submit="Auf allen speichern", help="Jede album.cfg wird wie beim einzelnen Speichern geschrieben: "
                                               "Kommentare bleiben, Sicherung und Protokoll inklusive."),
    ]
    return {
        "title": "Alben", "meta": f"{counts['alle']} Ordner",
        "lead": "Jeder Ordner ist ein Album. Ein Klick öffnet seine Einstellungen.",
        "choose": [{"name": "filter", "label": "Zeigen", "value": filter_, "options": [
            {"value": "", "label": "Alle", "count": counts["alle"]},
            {"value": "ohne_text", "label": "Ohne Text", "count": counts["ohne_text"]},
            {"value": "probleme", "label": "Mit Hinweisen", "count": counts["probleme"]}]}],
        "actions": actions,
        "sections": [{"id": "albums", "title": "Alben", "kind": "tree", "items": rows,
                      "empty": "Hier ist nichts."}],
    }


def _assets_rows(scope: str, album: str | None, assets: list[dict], page: str) -> list[dict]:
    rows = []
    for asset in assets:
        name = asset["name"]
        ext = Path(name).suffix.lower()
        row = {"title": name, "sub": ", ".join(asset.get("kinds") or []), "meta": _bytes(asset.get("size"))}
        if ext in (".svg", ".png", ".webp", ".jpg", ".jpeg", ".gif"):
            row["thumb"] = f"a/{album}/{name}" if scope == "album" else f"g/{name}"
        if not _ro():
            row["actions"] = [_act("asset_delete", "Löschen", danger=True, values={"name": name},
                                   confirm=f"{name} löschen? Die Sicherung behält eine Kopie.")]
        rows.append(row)
    return rows


def _issues_section(issues: list[dict], album: str | None) -> dict | None:
    if not issues:
        return None
    rows = []
    for i in issues:
        row = {"title": f"{_label(i.get('key') or '')} ({i.get('key')})", "text": i.get("detail"),
               "badges": [{"label": "Fehler" if i.get("level") == "error" else "Hinweis", "tone": _issue_level(i)}]}
        fix = i.get("fix")
        if fix and not _ro():
            value = fix.get("value")
            row["actions"] = [_act("fix", fix.get("label") or "Beheben", icon="check", values={
                "key": fix.get("key"), "value": json.dumps(value), **({"album": album} if album else {})})]
        rows.append(row)
    return {"id": "issues", "title": "Was die Prüfung findet", "kind": "rows", "items": rows,
            "meta": str(len(issues)), "warn": True}


def _history_section(file: str, album: str | None, view: dict, page: str) -> list[dict]:
    a = _app()
    lang = view.get("lang") if view.get("lang") in schema.LANGS else None
    if file == "desc" and not lang:
        lang = schema.LANGS[0]
    path, label = a._history_file(file, album, lang)
    folder = a._backup_folder(path, label)
    current = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    versions = []
    if folder.is_dir():
        for entry in sorted(folder.iterdir(), reverse=True):
            if a._HISTORY_ID.fullmatch(entry.name):
                versions.append(entry)
    base = {"album": album} if album else {}
    rows = []
    for entry in versions:
        stamp = datetime.strptime(entry.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        row = {"title": _when(stamp.isoformat()), "sub": entry.name, "meta": _bytes(entry.stat().st_size),
               "go": _go(page, **base, tab="verlauf", file=view.get("file", ""), lang=lang or "", version=entry.name)}
        if not _ro():
            row["actions"] = [_act("restore", "Diese Fassung zurückholen", danger=True,
                                   values={"file": file, "lang": lang or "", "version": entry.name, **base},
                                   confirm="Die jetzige Fassung kommt dabei selbst in den Verlauf.")]
        rows.append(row)
    sections = [{"id": "versions", "title": f"Frühere Fassungen von {path.name}", "kind": "rows", "items": rows,
                 "empty": "Noch keine. Jede Änderung legt die vorige Fassung hier ab.",
                 "meta": str(len(rows))}]
    version = view.get("version", "")
    if version:
        try:
            old = a._version(folder, version).read_text(encoding="utf-8", errors="replace")
            diff = "\n".join(difflib.unified_diff(current.splitlines(), old.splitlines(), "jetzt", version,
                                                  lineterm="")) or "Gleich wie jetzt."
            sections.insert(0, {"id": "diff", "title": "Was das Zurückholen ändern würde", "kind": "text",
                                "text": diff})
        except HTTPException:
            pass
    return sections


def _history_choose(album: str | None, view: dict) -> list[dict]:
    opts = [{"value": "", "label": "gallery.cfg" if album is None else "album.cfg"}]
    if album is not None:
        opts += [{"value": f"desc-{l}", "label": f"Text {l.upper()}"} for l in schema.LANGS]
    return [{"name": "file", "label": "Datei", "value": view.get("file", ""), "options": opts}]


def _album_page(album: str, view: dict) -> dict:
    a = _app()
    album = a._album_or_400(album)
    lib = a.lib
    cfg_path = lib.cfg_path(album)
    cfg_file = cfgio.CfgFile.load(cfg_path)
    values = cfg_file.values()
    issues = checks.album(album, values) if cfg_path.is_file() else []
    tab = view.get("tab") if view.get("tab") in {t[0] for t in ALBUM_TABS} else "details"
    assets = lib.assets(album)
    own = lib.photos(album, recursive=True)
    subs = [p["sub"] for p in own]
    langs = [l for l in schema.LANGS if lib.read_desc(album, l).strip()]
    facts = ["album.cfg" if cfg_path.is_file() else "ohne album.cfg", f"{len(own)} Fotos",
             ("Text " + " ".join(l.upper() for l in langs)) if langs else "kein Text"]
    if issues:
        facts.append(f"{len(issues)} Hinweis(e)")
    sections, ro = [], _ro()
    if issues and tab in ("details", "fotos", "aussehen"):
        sections.append(_issues_section(issues, album))
    keys = next(k for t, _, k in ALBUM_TABS if t == tab)
    if keys:
        fields = [_field(k, values, False, assets, subs) for k in keys]
        sections.append({"id": tab, "title": next(l for t, l, _ in ALBUM_TABS if t == tab), "kind": "kv",
                         "items": _kv_values(keys, values, False),
                         "actions": [] if ro else [_act("cfg", "Bearbeiten", icon="edit", submit="Speichern",
                                                         fields=[_hidden("album", album), _hidden("_keys", ",".join(keys))] + fields)]})
    if tab == "fotos":
        sections += _album_photo_sections(album, values, own)
    elif tab == "text":
        for lang in schema.LANGS:
            text = lib.read_desc(album, lang)
            sections.append({"id": f"text-{lang}", "title": LANG_NAMES.get(lang, lang), "kind": "text",
                             "text": text or "(leer)", "meta": f"album_{lang}.md",
                             "actions": [] if ro else [_act("desc", "Bearbeiten", icon="edit", submit="Speichern",
                                                             values={"album": album, "lang": lang},
                                                             fields=[{"name": "text", "label": f"Text ({lang})",
                                                                      "type": "textarea", "rows": 14, "value": text,
                                                                      "help": "Markdown. Leer gespeichert löscht die Datei."}])]})
    elif tab == "dateien":
        sections.append({"id": "files", "title": "Dateien in .album/", "kind": "rows",
                         "items": _assets_rows("album", album, assets, "alben"),
                         "empty": "Noch keine. Icons, Schriften, Hintergründe und Texte (album_de.md) kommen hier hinein.",
                         "actions": [] if ro else [_act("upload", "Hochladen", icon="upload", values={"album": album},
                                                         fields=[{"name": "files", "label": "Dateien", "type": "file",
                                                                  "accept": ",".join(sorted(a._SCOPE_EXTS["album"] | {".md"}))}],
                                                         help="Icons (svg, png, webp, gif, jpg), Schriften (otf, ttf, woff), "
                                                              "Hintergründe (Bilder, mp4, webm) und album_xx.md.")]})
    elif tab == "roh":
        text = cfg_file.text()
        acts = [] if ro else [_act("raw", "Rohdatei bearbeiten", icon="edit", submit="Speichern",
                                   values={"album": album},
                                   fields=[{"name": "raw", "label": "album.cfg", "type": "textarea", "rows": 22,
                                            "mono": True, "value": text}])]
        if cfg_path.is_file() and not ro:
            acts.append(_act("cfg_delete", "album.cfg löschen", danger=True, values={"album": album},
                             confirm="Das Album fällt auf die Voreinstellungen der Galerie zurück. Der Verlauf behält die Datei."))
        sections.append({"id": "raw", "title": "album.cfg", "kind": "text", "text": text or "(gibt es noch nicht)",
                         "actions": acts})
    elif tab == "verlauf":
        f = view.get("file", "")
        lang = f[5:] if f.startswith("desc-") else None
        v = dict(view, lang=lang or "")
        sections += _history_section("desc" if lang else "album", album, v, "alben")
    actions = [] if ro else [
        _act("scan_album", "Album scannen", icon="clock", values={"album": album}),
        _act("rebuild_album", "Vorschaubilder bauen", icon="image", values={"album": album},
             fields=[{"name": "all", "label": "Alle neu bauen, nicht nur fehlende", "type": "checkbox"}]),
    ]
    choose = [{"name": "tab", "label": "Teil", "value": tab,
               "options": [{"value": t, "label": l} for t, l, _ in ALBUM_TABS]}]
    if tab == "verlauf":
        choose += _history_choose(album, view)
    return {"title": album.rsplit("/", 1)[-1], "meta": " · ".join(facts),
            "lead": album,
            "choose": choose,
            "keep": {"album": album},
            "links": [{"label": "Fotos ansehen", "go": f"media?album={quote(album, safe='')}"},
                      {"label": "Alle Alben", "go": "p/alben"}],
            "actions": actions, "sections": [s for s in sections if s]}


def _album_photo_sections(album: str, values: dict, own: list[dict]) -> list[dict]:
    """The photos the album leans on: its featured list, in order, each with
    up, down and out; the cover; the curated order."""
    ro = _ro()
    by_sub = {p["sub"]: p for p in own}
    featured = [i.strip() for i in values.get("featured", []) if i.strip()]
    items = []
    for n, entry in enumerate(featured):
        p = by_sub.get(entry)
        item = {"id": entry, "src": f"tr/{p['rel']}" if p else "", "caption": entry, "n": n + 1,
                "cover": entry == cfgio.first(values, "cover")}
        if not p:
            item["badges"] = [{"label": "nicht gefunden", "tone": "err"}]
        if not ro:
            acts = []
            if n > 0:
                acts.append(_act("featured_move", "Nach vorn", icon="up", values={"album": album, "entry": entry, "dir": "up"}))
            if n < len(featured) - 1:
                acts.append(_act("featured_move", "Nach hinten", icon="down", values={"album": album, "entry": entry, "dir": "down"}))
            acts.append(_act("cover_set", "Als Titelbild", icon="image", values={"album": album, "entry": entry}))
            acts.append(_act("featured_remove", "Nicht mehr hervorheben", icon="x", values={"album": album, "entry": entry}))
            item["actions"] = acts
        if p:
            items.append(item)
        else:
            items.append({**item, "src": None})
    shown = [i for i in items if i.get("src")]
    missing = [i for i in items if not i.get("src")]
    add = [] if ro else [_act("featured_add", "Foto hervorheben", icon="plus", values={"album": album}, fields=[
        {"name": "entry", "label": "Foto", "type": "select", "span": "full",
         "options": [{"value": p["sub"], "label": p["sub"]} for p in own if p["sub"] not in featured][:2000]}])]
    out = [{"id": "featured", "title": "Hervorgehoben", "kind": "photos", "items": shown,
            "meta": str(len(featured)), "lead": "In dieser Reihenfolge auf der Startseite und in der Diashow des Albums.",
            "empty": "Noch keine. Die Galerie zeigt dann die neuesten.", "actions": add}]
    if missing:
        out.append({"id": "featured-missing", "title": "Einträge ohne Foto", "kind": "rows", "warn": True,
                    "meta": str(len(missing)),
                    "items": [{"title": i["caption"], "actions": [a for a in i.get("actions", []) if a["id"] == "featured_remove"]}
                              for i in missing]})
    cover = cfgio.first(values, "cover")
    cp = by_sub.get(cover or "")
    if cp:
        out.append({"id": "cover", "title": "Titelbild", "kind": "photos", "side": True,
                    "items": [{"id": cover, "src": f"tr/{cp['rel']}", "caption": cover, "cover": True}]})
    return out


def page_website(view: dict) -> dict:
    a = _app()
    lib = a.lib
    path = lib.gallery_cfg_path()
    cfg_file = cfgio.CfgFile.load(path, cfgio.GROUP_KEYS)
    values = cfg_file.values()
    issues = checks.gallery(values)
    tab = view.get("tab") if view.get("tab") in {t[0] for t in SITE_TABS} else "identitaet"
    assets = lib.brand_assets()
    ro = _ro()
    sections = []
    keys = next(k for t, _, k in SITE_TABS if t == tab)
    if issues and keys:
        sections.append(_issues_section(issues, None))
    if keys:
        photos = [p["rel"] for p in lib.photos("", recursive=True)] if tab == "startseite" else []
        fields = [_field(k, values, True, assets, photos) for k in keys]
        sections.append({"id": tab, "title": next(l for t, l, _ in SITE_TABS if t == tab), "kind": "kv",
                         "items": _kv_values(keys, values, True),
                         "actions": [] if ro else [_act("cfg", "Bearbeiten", icon="edit", submit="Speichern",
                                                         fields=[_hidden("_keys", ",".join(keys))] + fields)]})
        if tab == "startseite":
            sections.append({"id": "albums", "title": "Alben, die es gibt", "kind": "text",
                             "text": "\n".join(lib.album_paths()) or "(keine)",
                             "lead": "Für die Album-Reihenfolge: ein Album je Zeile, #Gruppe beginnt eine Überschrift. "
                                     "Startseite: Pfade von Fotos, eine Zeile je Foto, oder showcase / random."})
    elif tab == "dateien":
        sections.append({"id": "files", "title": "Dateien in .gallery/", "kind": "rows",
                         "items": _assets_rows("gallery", None, assets, "website"),
                         "empty": "Noch keine. Logo, Favicon, Porträt, Buttons, Schrift und Hintergrund kommen hier hinein.",
                         "actions": [] if ro else [_act("upload", "Hochladen", icon="upload",
                                                         fields=[{"name": "files", "label": "Dateien", "type": "file",
                                                                  "accept": ",".join(sorted(a._SCOPE_EXTS["gallery"]))}])]})
    elif tab == "roh":
        text = cfg_file.text()
        sections.append({"id": "raw", "title": "gallery.cfg", "kind": "text", "text": text or "(gibt es noch nicht)",
                         "actions": [] if ro else [_act("raw", "Rohdatei bearbeiten", icon="edit", submit="Speichern",
                                                         fields=[{"name": "raw", "label": "gallery.cfg",
                                                                  "type": "textarea", "rows": 24, "mono": True,
                                                                  "value": text}])]})
    elif tab == "verlauf":
        sections += _history_section("gallery", None, view, "website")
    meta = ["gallery.cfg" if path.is_file() else "ohne gallery.cfg", f"{len(values)} Schlüssel gesetzt"]
    if issues:
        meta.append(f"{len(issues)} Hinweis(e)")
    return {"title": "Website", "meta": " · ".join(meta),
            "lead": "Was die ganze Galerie trägt: Name, Aussehen, Startseite, Footer.",
            "choose": [{"name": "tab", "label": "Teil", "value": tab,
                        "options": [{"value": t, "label": l} for t, l, _ in SITE_TABS]}],
            "sections": [s for s in sections if s]}


def _indexer_kv() -> list[dict]:
    st = ops.status()
    server = st.get("server") or {}
    last = server.get("last_scan") or {}
    state = ("läuft" if st["live"] else "aus") + (" · pausiert" if st["paused"] else "")
    items = [{"label": "Indexer", "value": state, "tone": "ok" if st["live"] and not st["paused"] else "warn"}]
    if st["paused"] and st.get("pause"):
        p = st["pause"]
        items.append({"label": "Pause", "value": f"{p.get('reason') or 'ohne Grund'} · {p.get('by') or ''} · {_when(p.get('at'))}"})
    prog = server.get("progress")
    if prog:
        items.append({"label": "Gerade", "value": " · ".join(str(x) for x in (prog if isinstance(prog, list) else [prog]))})
    job = server.get("job")
    if job:
        items.append({"label": "Auftrag", "value": f"{JOB_LABELS.get(job.get('kind'), job.get('kind'))} · {job.get('label') or ''}"})
    if last:
        items.append({"label": "Letzter Scan", "value": f"{_when(last.get('finished_at') or last.get('started_at'))}"})
        for k in ("added", "updated", "removed", "seconds"):
            if last.get(k) is not None:
                items.append({"label": {"added": "Neu", "updated": "Geändert", "removed": "Entfernt",
                                        "seconds": "Dauer"}[k], "value": str(last[k])})
    pending = control.pending_jobs() if hasattr(control, "pending_jobs") else []
    if pending:
        items.append({"label": "Warteschlange", "value": ", ".join(JOB_LABELS.get(j.get("kind"), j.get("kind")) for j in pending)})
    return items


def _album_options(empty: str = "ganze Galerie") -> list[dict]:
    return [{"value": "", "label": empty}] + [{"value": p, "label": p} for p in _app().lib.album_paths()]


def page_system(view: dict) -> dict:
    tab = view.get("tab") if view.get("tab") in {t for t, _ in SYSTEM_TABS} else "indexer"
    ro = _ro()
    sections, actions = [], []
    if tab == "indexer":
        st = ops.status()
        idx = st["index"]
        sections.append({"id": "stats", "kind": "stats", "items": [
            {"label": "Fotos", "value": f"{idx['images']:,}".replace(",", " ")},
            {"label": "Alben", "value": str(idx["albums"])},
            {"label": "Tags", "value": str(idx["tags"])},
            {"label": "Archiv", "value": _bytes(idx["bytes"])}]})
        sections.append({"id": "state", "title": "Indexer", "kind": "kv", "items": _indexer_kv()})
        paths = ops.paths()
        sections.append({"id": "paths", "title": "Wo alles liegt", "kind": "kv", "side": True, "items": [
            {"label": "Fotos", "value": paths["photos"]}, {"label": "Vorschaubilder", "value": paths["thumbs"]},
            {"label": "Vorschauen", "value": paths["previews"]}, {"label": "Daten", "value": paths["data"]},
            {"label": "Scan-Intervall", "value": f"{paths['scan_interval']} s"},
            {"label": "Ordner beobachten", "value": "ja" if paths["watcher"] else "nein"},
            {"label": "Rolle", "value": settings.role}]})
        if not ro:
            actions = [
                _act("scan", "Scan starten", icon="clock", fields=[
                    {"name": "album", "label": "Bereich", "type": "select", "options": _album_options()},
                    {"name": "force", "label": "Auch unveränderte Fotos neu lesen", "type": "checkbox"}]),
                _act("pause", "Pausieren", icon="x", fields=[
                    {"name": "reason", "label": "Grund", "type": "text", "placeholder": "Umzug der Fotos"}],
                    help="Bleibt pausiert, auch über einen Neustart, bis du fortsetzt."),
                _act("resume", "Fortsetzen", icon="check", fields=[
                    {"name": "scan", "label": "Danach gleich scannen", "type": "checkbox"}]),
            ]
    elif tab == "speicher":
        d = ops.disk_usage()
        sections.append({"id": "stats", "kind": "stats", "items": [
            {"label": "Originale", "value": _bytes(d["originals"]["bytes"]), "note": f"{d['originals']['files']} Fotos"},
            {"label": "Erzeugt", "value": _bytes(d["derivatives"]["bytes"]), "note": f"{d['derivatives']['files']} Dateien"},
            {"label": "Anteil", "value": f"{round((d['ratio'] or 0) * 100)} %", "note": "auf die Originale"},
            {"label": "Index", "value": _bytes(d["database"]["bytes"])}]})
        names = {"thumbnails": "Vorschaubilder", "previews": "Vorschauen", "fulls": "Umwandlungen"}
        sections.append({"id": "share", "title": "Was Platz braucht", "kind": "ring", "size": "half", "ranked": False,
                         "center": _bytes(d["originals"]["bytes"] + d["derivatives"]["bytes"] + d["database"]["bytes"]),
                         "sub": "zusammen", "items": [{"label": "Originale", "value": d["originals"]["bytes"],
                                                       "display": _bytes(d["originals"]["bytes"])}]
                         + [{"label": names.get(t["key"], t["key"]), "value": t["bytes"], "display": _bytes(t["bytes"])}
                            for t in d["tiers"]]
                         + [{"label": "Index", "value": d["database"]["bytes"], "display": _bytes(d["database"]["bytes"])}]})
        sections.append({"id": "disks", "title": "Laufwerke", "kind": "bars", "size": "half", "scale": 100, "items": [
            {"label": ", ".join(v["holds"]), "value": round(v["used"] / v["total"] * 100) if v["total"] else 0,
             "display": f"{round(v['used'] / v['total'] * 100) if v['total'] else 0} %",
             "note": f"{_bytes(v['free'])} frei", "tone": "warn" if v["total"] and v["free"] / v["total"] < 0.1 else None}
            for v in d["volumes"]]})
        sections.append({"id": "tiers", "title": "Was erzeugt wurde", "kind": "rows", "items": [
            {"title": {"thumbnails": "Vorschaubilder", "previews": "Vorschauen", "fulls": "Umwandlungen"}.get(t["key"], t["key"]),
             "sub": t["path"], "meta": f"{_bytes(t['bytes'])} · {t['files']} Dateien",
             "badges": ([{"label": "alte Formate: " + ", ".join(t["stale_formats"]), "tone": "warn"}] if t["stale_formats"] else [])
             + ([] if t["exists"] else [{"label": "fehlt", "tone": "warn"}])} for t in d["tiers"]]})
        try:
            r = ops.derivatives_report(None)
            n_todo, n_orph = r["to_build"], len(r["orphans"])
            sections.append({"id": "todo", "title": "Was fehlt oder übrig ist", "kind": "kv", "items": [
                {"label": "Fehlend oder veraltet", "value": str(n_todo), "tone": "warn" if n_todo else "ok"},
                *[{"label": k.replace("_", " "), "value": str(n)} for k, n in sorted(r["by_state"].items())],
                {"label": "Übrig", "value": f"{n_orph} · {_bytes(r['orphan_bytes'])}", "tone": "warn" if n_orph else "ok"}]})
        except Exception:
            pass
        if not ro:
            actions = [
                _act("job_rebuild", "Vorschaubilder bauen", icon="image", fields=[
                    {"name": "album", "label": "Bereich", "type": "select", "options": _album_options()},
                    {"name": "all", "label": "Alle neu bauen, nicht nur fehlende und veraltete", "type": "checkbox"}]),
                _act("job_featured", "Hervorhebungen neu schreiben", icon="check"),
                _act("job_prune", "Übrige löschen", danger=True,
                     confirm="Löscht erzeugte Dateien, zu denen es kein Foto mehr gibt. Die Originale bleiben."),
            ]
    elif tab == "pruefung":
        if view.get("run") != "1":
            sections.append({"id": "start", "title": "Gesundheitsprüfung", "kind": "text",
                             "text": "Index, Dateien, Vorschaubilder, Hervorhebungen und Konfiguration gegeneinander "
                                     "geprüft. Das liest den ganzen Fotoordner und dauert bei vielen Fotos etwas.",
                             "link": None})
        else:
            album = view.get("album") or None
            rep = ops.doctor(album, limit_slow=50)
            sections.append({"id": "stats", "kind": "stats", "items": [
                {"label": "Fotos", "value": str(rep["photos_on_disk"])},
                {"label": "Im Index", "value": str(rep["rows"])},
                {"label": "Probleme", "value": str(rep["total"]), "tone": "warn" if rep["total"] else "ok"}]})
            if rep["problems"]:
                sections.append({"id": "kinds", "title": "Was gefunden wurde", "kind": "bars", "items": [
                    {"label": DOCTOR_LABELS.get(k, k.replace("_", " ")), "value": len(v), "tone": "warn"}
                    for k, v in sorted(rep["problems"].items(), key=lambda kv: -len(kv[1]))]})
            for check, items in sorted(rep["problems"].items()):
                label = DOCTOR_LABELS.get(check, check.replace("_", " "))
                fix = DOCTOR_FIX.get(check) or ("rebuild" if check.split("_", 1)[0] in ("missing", "stale") else None)
                acts = []
                if fix and not ro:
                    acts.append(_act(f"job_{fix}" if fix != "scan" else "scan", f"Beheben: {JOB_LABELS.get(fix, fix)}",
                                     icon="check", values={"album": album or ""}, danger=fix == "prune" or None,
                                     confirm="Löscht erzeugte Dateien, zu denen es kein Foto mehr gibt." if fix == "prune" else None))
                rows = []
                for it in items[:200]:
                    rel = it.get("rel_path") or it.get("album") or ""
                    rows.append({"title": rel if isinstance(rel, str) else str(rel),
                                 "text": it.get("detail") or "", "meta": it.get("key") or None})
                sections.append({"id": f"d-{check}", "title": label, "kind": "rows", "items": rows,
                                 "meta": str(len(items)), "warn": True, "actions": acts})
            if not rep["total"]:
                sections.append({"id": "ok", "title": "Alles in Ordnung", "kind": "text",
                                 "text": "Index, Dateien, Vorschaubilder und Konfiguration passen zusammen."})
        actions = [_act("doctor", "Prüfung starten", icon="check", fields=[
            {"name": "album", "label": "Bereich", "type": "select", "options": _album_options(),
             "value": view.get("album", "")}], help="Liest nur, ändert nichts.")]
    elif tab == "datenschutz":
        paths = ops.paths()
        sections.append({"id": "gps", "title": "Ortsdaten", "kind": "kv", "items": [
            {"label": "GPS verbergen (HIDE_GPS)", "value": "an" if paths["hide_gps"] else "aus",
             "tone": "ok" if paths["hide_gps"] else "warn"},
            {"label": "GPS aus Vorschauen entfernen (STRIP_GPS)", "value": "an" if paths["strip_gps"] else "aus",
             "tone": "ok" if paths["strip_gps"] else "warn"}],
            "lead": "Beides wird in der Umgebung eingestellt, nicht hier."})
        if view.get("run") == "1":
            rep = ops.gps_audit(view.get("album") or None)
            sections.append({"id": "carrying", "title": "Originale mit Koordinaten", "kind": "rows",
                             "meta": f"{len(rep['with_gps'])} von {rep['checked']}", "warn": bool(rep["with_gps"]),
                             "items": [{"title": r} for r in rep["with_gps"][:500]],
                             "empty": "Keins der geprüften Originale trägt Koordinaten."})
            if rep["unreadable"]:
                sections.append({"id": "unreadable", "title": "Nicht lesbar", "kind": "rows",
                                 "items": [{"title": r} for r in rep["unreadable"][:200]]})
        actions = [_act("gps_check", "Originale prüfen", icon="pin", fields=[
            {"name": "album", "label": "Bereich", "type": "select", "options": _album_options()}],
            help="Öffnet jedes Original und liest seine EXIF. Ändert nichts.")]
        if not ro:
            actions.append(_act("job_gps_strip", "GPS aus Originalen entfernen", danger=True, fields=[
                {"name": "album", "label": "Bereich", "type": "select", "options": _album_options()}],
                confirm="Schreibt die Originale neu. Das ist das Einzige in Aperture, das ein Foto verändert."))
    elif tab == "sicherung":
        rep = reports.export_report()
        sections.append({"id": "export", "title": "Alles Handgeschriebene als .tar.gz", "kind": "rows",
                         "meta": f"{len(rep['files'])} Dateien · {_bytes(rep['bytes'])}",
                         "lead": "Jede album.cfg, jeder Text, jede Datei aus .album/ und .gallery/. Keine Fotos.",
                         "downloads": [{"label": "Sicherung herunterladen", "path": "config.tar.gz"}],
                         "items": [{"title": f} for f in rep["files"][:500]]})
    elif tab == "aktivitaet":
        rows = []
        for r in security.recent(100):
            who = r.get("by") or ("Konsole" if r.get("session") else r.get("ip") or "")
            rows.append({"title": r.get("action") or "", "sub": r.get("target") or "",
                         "meta": _when(r.get("ts")), "facts": [{"icon": "user", "text": who}] if who else None})
        sections.append({"id": "audit", "title": "Jede Änderung in Aperture", "kind": "rows", "items": rows,
                         "empty": "Noch nichts geändert."})
    elif tab == "tags":
        rep = reports.tags_report(None)
        names = sorted(_app().lib.all_tags())
        pairs = _similar_tags(names)
        sections.append({"id": "similar", "title": "Wahrscheinlich derselbe Tag", "kind": "rows",
                         "meta": str(len(pairs)), "empty": "Keine Paare gefunden.",
                         "items": [{"title": f"{x}  ·  {y}", "actions": [] if ro else [
                             _act("tag_merge", f"„{x}“ in „{y}“", icon="check", values={"from": x, "to": y}),
                             _act("tag_merge", f"„{y}“ in „{x}“", icon="check", values={"from": y, "to": x})]}
                                   for x, y in pairs]})
        drift = rep["drift"]
        sections.append({"id": "drift", "title": "Sidecars und Index", "kind": "rows",
                         "lead": "Wo die .tags-Dateien und der Index bis zum nächsten Scan auseinanderliegen.",
                         "items": [{"title": d, "sub": "noch nicht im Index"} for d in drift["not_indexed"][:200]]
                         + [{"title": d, "sub": "im Index, ohne Sidecar"} for d in drift["indexed_without_sidecar"][:200]],
                         "empty": "Alles im Gleichen.",
                         "actions": [] if ro else [_act("scan", "Scan starten", icon="clock")]})
        if rep.get("orphan_sidecars"):
            sections.append({"id": "orphans", "title": "Sidecars ohne Foto", "kind": "rows",
                             "items": [{"title": o} for o in rep["orphan_sidecars"][:200]]})
    elif tab == "passwort":
        is_set = security.password_is_set()
        sections.append({"id": "door", "title": "Die Tür von Aperture", "kind": "kv", "items": [
            {"label": "Passwort", "value": "gesetzt" if is_set else "keins", "tone": "ok" if is_set else "warn"},
            {"label": "Lauscht auf", "value": f"{settings.console_bind}:{settings.console_port}"},
            {"label": "Ohne Passwort erlaubt", "value": "ja" if security.may_run_open() else "nein"}],
            "lead": "Das Passwort von Aperture selbst. Über Lucent meldest du dich mit deinem Lucent-Konto an; "
                    "eine Änderung hier beendet alle Sitzungen in Aperture."})
        if not ro:
            fields = ([{"name": "current", "label": "Jetziges Passwort", "type": "password", "required": True}] if is_set else []) + [
                {"name": "password", "label": "Neues Passwort", "type": "password", "required": True},
                {"name": "again", "label": "Noch einmal", "type": "password", "required": True}]
            actions = [_act("password", "Passwort ändern" if is_set else "Passwort setzen", icon="edit", fields=fields)]
            if is_set and security.may_run_open():
                actions.append(_act("password_clear", "Passwort entfernen", danger=True, fields=[
                    {"name": "current", "label": "Jetziges Passwort", "type": "password", "required": True}],
                    confirm="Aperture ist danach ohne Passwort offen, nur auf diesem Rechner."))
    elif tab == "ueber":
        up = update_check.status(fresh=view.get("fresh") == "1")
        state = {"off": "Prüfung aus", "current": "aktuell", "available": f"{up.get('latest')} verfügbar",
                 "unreachable": "Server nicht erreichbar"}.get(up.get("state"), up.get("state"))
        sections.append({"id": "about", "title": brand.PRODUCT, "kind": "kv", "items": [
            {"label": "Version", "value": brand.VERSION},
            {"label": "Aktualisierung", "value": state, "tone": "warn" if up.get("state") == "available" else "ok"},
            {"label": "Quelle", "value": brand.REPO_URL},
            {"label": "Von", "value": f"{brand.NAME} · {brand.URL}"},
            {"label": "Design", "value": brand.DESIGN_NAME}]})
        try:
            text = update_check.CHANGELOG_PATH.read_text(encoding="utf-8")
        except OSError:
            text = ""
        notes = _app()._release_notes(text)[:8]
        sections.append({"id": "notes", "title": "Was neu ist", "kind": "rows", "items": [
            {"title": n["version"], "sub": n["date"], "text": _plain(n["html"])[:600]} for n in notes],
            "empty": "Keine Versionshinweise im Paket."})
        actions = [_act("update_check", "Erneut prüfen", icon="clock")]
    return {"title": "System", "meta": f"Aperture {brand.VERSION}" + (" · nur lesen" if ro else ""),
            "choose": [{"name": "tab", "label": "Teil", "value": tab,
                        "options": [{"value": t, "label": l, "group": SYSTEM_GROUPS[t][0], "icon": SYSTEM_GROUPS[t][1]}
                                    for t, l in SYSTEM_TABS]}],
            "actions": actions, "sections": sections}


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _similar_tags(names: list[str]) -> list[tuple[str, str]]:
    """Tags one typo apart, or differing only in case, spaces, hyphens or
    underscores — the console's "Probably the same tag"."""
    def norm(t: str) -> str:
        return re.sub(r"[\s_\-]+", "", t.lower())
    out, seen = [], set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            na, nb = norm(a), norm(b)
            same = na == nb or (min(len(na), len(nb)) >= 4 and abs(len(na) - len(nb)) <= 1
                                and difflib.SequenceMatcher(None, na, nb).ratio() >= 0.88)
            if same and (a, b) not in seen:
                seen.add((a, b))
                out.append((a, b))
        if len(out) >= 60:
            break
    return out


MONTHS = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")


def _over_time(dated: list[str]) -> tuple[list[dict], bool]:
    """Photos per month they were taken, as the console's chart: gaps kept as
    zeros, the earliest 2% (a camera that thinks it is 2001) folded into one
    column so the axis starts where the photos do, per quarter past six years."""
    dated = sorted(d[:7] for d in dated if d and len(d) >= 7 and d[:4].isdigit())
    if not dated:
        return [], False
    cut = dated[int(len(dated) * 0.02)]
    earlier = sum(1 for d in dated if d < cut)
    dated = [d for d in dated if d >= cut]
    fy, fm = int(dated[0][:4]), int(dated[0][5:7])
    ly, lm = int(dated[-1][:4]), int(dated[-1][5:7])
    quarterly = (ly - fy) * 12 + (lm - fm) + 1 > 72
    counts: dict[str, int] = {}
    for d in dated:
        y, m = int(d[:4]), int(d[5:7])
        key = f"{y}-Q{(m - 1) // 3 + 1}" if quarterly else f"{y}-{m:02d}"
        counts[key] = counts.get(key, 0) + 1
    out = []
    if earlier:
        out.append({"label": f"vor {MONTHS[fm - 1]} {fy}", "value": earlier, "axis": "…"})
    y, m = fy, ((fm - 1) // 3 * 3 + 1 if quarterly else fm)
    while (y, m) <= (ly, lm):
        q = (m - 1) // 3 + 1
        key = f"{y}-Q{q}" if quarterly else f"{y}-{m:02d}"
        first = len(out) == (1 if earlier else 0) or m == 1
        go = "media?q=" + quote(f"date:{y}-{m:02d}" if not quarterly else f"date:{y}")
        out.append({"label": f"Q{q} {y}" if quarterly else f"{MONTHS[m - 1]} {y}", "value": counts.get(key, 0),
                    "axis": str(y) if first else "", "go": go})
        m += 3 if quarterly else 1
        if m > 12:
            m, y = m - 12, y + 1
    return out, quarterly


def page_uebersicht(view: dict) -> dict:
    a = _app()
    lib = a.lib
    st = ops.status()
    arch = reports.archive_report(top=12, months=1)
    photos, tags = lib.catalog()
    untagged = sum(1 for p in photos if not tags.get(p["rel"]))
    c = db.conn()
    try:
        indexed = {r[0] for r in c.execute("SELECT rel_path FROM images")}
        dated = [r[0] for r in c.execute("SELECT taken_at FROM images WHERE taken_at IS NOT NULL")]
    except Exception:
        indexed, dated = set(), []
    not_indexed = sum(1 for p in photos if p["rel"] not in indexed)
    albums_ = lib.album_paths()
    unwritten = sum(1 for al in albums_ if not (lib.cfg_path(al).is_file()
                                                 and any(lib.read_desc(al, l).strip() for l in schema.LANGS)))
    issues = checks.everything()
    idx = arch["index"]
    span = arch.get("span") or {}
    n = len(photos) or 1

    buckets, quarterly = _over_time(dated)
    per_year: dict[str, int] = {}
    for d in dated:
        per_year[d[:4]] = per_year.get(d[:4], 0) + 1
    years = [per_year[y] for y in sorted(per_year)]

    def todo(label, count, go, note_done, note_open):
        return {"label": label, "value": str(count), "go": go, "tone": "ok" if not count else "warn",
                "note": note_done if not count else note_open}

    figures = [
        {"label": "Fotos", "value": f"{idx['images']:,}".replace(",", " "), "note": _bytes(idx["bytes"]),
         "go": "media", "spark": years if len(years) > 1 else None},
        {"label": "Alben", "value": str(arch.get("album_nodes", idx["albums"])),
         "note": f"{arch.get('showcase_albums', 0)} im Schaufenster", "go": _go("alben")},
        {"label": "Getaggt", "value": f"{round((n - untagged) / n * 100)} %", "note": f"{idx['tags']} Tags",
         "go": "tags"},
        {"label": "Zeitraum", "value": f"{(span.get('from') or '–')[:4]}–{(span.get('to') or '–')[:4]}",
         "note": f"{len(per_year)} Jahre"},
    ]
    to_do = [
        todo("Ohne Tag", untagged, "media?q=" + quote("is:untagged"), "alle getaggt", "Fotos taggen"),
        todo("Ohne Text", unwritten, _go("alben", filter="ohne_text"), "alle beschrieben", "Alben beschreiben"),
        todo("Prüfung", len(issues), _go("alben", filter="probleme"), "nichts gefunden", "Hinweise ansehen"),
        todo("Nicht im Index", not_indexed, _go("system", tab="indexer"), "alles gelesen", "Scan läuft oder fehlt"),
    ]

    album_items = [{"label": r["album"], "value": r["n"], "note": _bytes(r["bytes"]),
                    "go": "media?album=" + quote(r["album"], safe="")} for r in arch.get("albums") or []]
    top_tags = sorted(lib.all_tags().items(), key=lambda kv: -kv[1])
    tag_ring = [{"label": "getaggt", "value": n - untagged},
                {"label": "ohne Tag", "value": untagged, "go": "media?q=" + quote("is:untagged"), "tone": "warn"}]
    cams = []
    try:
        cams = [{"label": r["camera"], "value": r["n"], "go": "media?q=" + quote(f'camera:"{r["camera"]}"')}
                for r in c.execute("SELECT camera, COUNT(*) AS n FROM images WHERE camera IS NOT NULL "
                                   "AND camera != '' GROUP BY camera ORDER BY n DESC LIMIT 20")]
    except Exception:
        pass
    sizes: dict[str, int] = {}
    try:
        for r in c.execute("SELECT filename, size FROM images"):
            ext = r["filename"].rsplit(".", 1)[-1].lower() if "." in r["filename"] else "?"
            sizes[ext] = sizes.get(ext, 0) + int(r["size"] or 0)
    except Exception:
        pass
    formats = [{"label": f".{ext}", "value": k, "display": f"{k} · {_bytes(sizes.get(ext))}"}
               for ext, k in (arch.get("formats") or [])]
    issue_rows = [{"title": f"{i.get('album') or 'gallery.cfg'} · {_label(i.get('key') or '')}", "text": i.get("detail"),
                   "badges": [{"label": "Fehler" if i.get("level") == "error" else "Hinweis", "tone": _issue_level(i)}],
                   "go": _go("alben", album=i["album"]) if i.get("album") else _go("website")} for i in issues[:20]]
    changes = [{"title": r.get("action") or "", "sub": r.get("target") or "", "meta": _when(r.get("ts")),
                "facts": [{"icon": "user", "text": r.get("by") or ("Konsole" if r.get("session") else r.get("ip") or "")}]}
               for r in security.recent(10)]
    ix_actions = [] if _ro() else [
        _act("scan", "Scan starten", icon="clock"),
        _act("resume", "Fortsetzen", icon="check") if st["paused"] else _act("pause", "Pausieren", icon="x"),
    ]
    lamp = "läuft" if st["live"] and not st["paused"] else ("pausiert" if st["paused"] else "aus")
    return {"title": "Übersicht", "meta": f"Aperture {brand.VERSION} · Indexer {lamp}", "sections": [
        {"id": "figures", "kind": "stats", "items": figures},
        {"id": "todo", "kind": "stats", "title": "Zu tun", "items": to_do},
        {"id": "time", "title": "Fotos über die Zeit", "kind": "columns", "items": buckets, "unit": "Fotos",
         "meta": ("je Quartal" if quarterly else "je Monat") + " · ein Klick zeigt die Fotos",
         "empty": "Noch keine Aufnahmedaten."},
        {"id": "albums", "title": "Alben", "kind": "bars", "items": album_items, "unit": "Fotos", "limit": 8,
         "size": "half", "empty": "Noch keine Alben."},
        {"id": "toptags", "title": "Häufigste Tags", "kind": "bars", "unit": "Fotos", "limit": 8, "size": "half",
         "items": [{"label": t, "value": k, "go": "media?ref=" + quote(t)} for t, k in top_tags],
         "empty": "Noch keine Tags."},
        {"id": "tags", "title": "Getaggt", "kind": "ring", "items": tag_ring, "ranked": False, "size": "third",
         "center": f"{round((n - untagged) / n * 100)} %", "sub": "getaggt", "unit": "Fotos"},
        {"id": "cameras", "title": "Kameras", "kind": "ring", "items": cams, "size": "third", "unit": "Fotos",
         "center": str(len(cams)), "sub": "Kameras", "empty": "Keine Kamera in den EXIF-Daten."},
        {"id": "formats", "title": "Formate", "kind": "ring", "items": formats, "size": "third",
         "center": _bytes(idx["bytes"]), "sub": "auf der Platte"},
        {"id": "indexer", "title": "Indexer", "kind": "kv", "items": _indexer_kv(), "size": "third",
         "actions": ix_actions},
        {"id": "issues", "title": "Braucht Aufmerksamkeit", "kind": "rows", "items": issue_rows[:8], "size": "third",
         "meta": str(len(issues)) if issues else None, "warn": bool(issues), "empty": "Nichts. Die Prüfung findet nichts."},
        {"id": "changes", "title": "Letzte Änderungen", "kind": "rows", "items": changes[:6], "size": "third",
         "empty": "Noch keine."},
    ]}


PAGE_BUILDERS = {"uebersicht": page_uebersicht, "alben": page_alben, "website": page_website, "system": page_system}


# ----- the connector routes ----------------------------------------------------
@router.get("/lucent/v1/pages/{page_id}")
def get_page(request: Request, page_id: str):
    lucent._require_lucent(request)
    build = PAGE_BUILDERS.get(page_id)
    if build is None:
        raise HTTPException(404, "no such page")
    return build(_view(request))


async def _body(request: Request) -> dict:
    body = await lucent._as_person(request)
    if not isinstance(body.get("values", {}), dict):
        raise HTTPException(400, "values must be an object")
    return body


@router.post("/lucent/v1/pages/{page_id}/do/{action}")
async def do_page(request: Request, page_id: str, action: str):
    body = await _body(request)
    if page_id not in PAGE_BUILDERS:
        raise HTTPException(404, "no such page")
    v = {k: str(x) for k, x in (body.get("values") or {}).items()}
    files = body.get("files") or []
    try:
        notice = _do(request, page_id, action, v, files)
    except Bad as e:
        raise HTTPException(422, str(e))
    except HTTPException as e:
        if e.status_code in (400, 404, 409, 413, 415):
            raise HTTPException(422, str(e.detail))
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"notice": notice}


def _do(request: Request, page_id: str, action: str, v: dict, files: list) -> str:
    a = _app()
    album = (v.get("album") or "").strip().strip("/") or None
    if action in ("doctor", "gps_check"):          # reads only: the page shows the answer
        return "Ergebnis unten." if action == "doctor" else "Ergebnis unten."
    if action == "update_check":
        update_check.status(fresh=True)
        return "Nachgesehen."
    if _ro():
        raise Bad("Aperture ist nur zum Lesen eingebunden.")
    if action == "scan":
        return _scan(request, album, v.get("force") == "1")
    if action == "scan_album":
        return _scan(request, album)
    if action == "rebuild_album":
        return _job(request, "rebuild", album, v.get("all") == "1")
    if action == "pause":
        ops.pause(v.get("reason", "") or "paused from Lucent", by=lucent.BY)
        security.audit(request, "indexer paused", v.get("reason") or "(no reason given)")
        return "Indexer pausiert."
    if action == "resume":
        lifted = ops.resume()
        security.audit(request, "indexer resumed", "-")
        msg = "Indexer läuft weiter." if lifted else "Es gab keine Pause."
        if v.get("scan") == "1":
            msg += " " + _scan(request)
        return msg
    if action.startswith("job_"):
        kind = action[4:]
        if kind == "scan":
            return _scan(request, album)
        if kind not in ops.JOBS:
            raise Bad("Diesen Auftrag gibt es nicht.")
        return _job(request, kind, album, v.get("all") == "1")
    if action == "cfg":
        keys = [k for k in (v.get("_keys") or "").split(",") if k in schema.KEY_SPEC]
        if page_id == "website":
            issues = _save_gallery(request, _updates(keys, v, True))
        else:
            if not album:
                raise Bad("Welches Album?")
            album = a._album_or_400(album)
            issues = _save_album(request, album, _updates(keys, v, False))
        return "Gespeichert." + (f" Die Prüfung findet noch {len(issues)} Hinweis(e)." if issues else "")
    if action == "fix":
        key = v.get("key", "")
        if key not in schema.KEY_SPEC:
            raise Bad("Diesen Schlüssel kennt Aperture nicht.")
        try:
            value = json.loads(v.get("value") or "null")
        except ValueError:
            value = None
        updates = a._updates_from({key: value})
        if album:
            _save_album(request, a._album_or_400(album), updates)
        else:
            _save_gallery(request, updates)
        return "Behoben."
    if action == "raw":
        text = v.get("raw", "")
        if page_id == "website":
            issues = _save_raw(request, None, text)
        else:
            issues = _save_raw(request, a._album_or_400(album or ""), text)
        return "Gespeichert." + (f" Die Prüfung findet {len(issues)} Hinweis(e)." if issues else "")
    if action == "cfg_delete":
        album = a._album_or_400(album or "")
        path = a._cfg_target(album)
        before = security.sha256_of(path)
        a._backup(path, "album")
        path.unlink(missing_ok=True)
        a._writes(request, "album.cfg deleted", path, before)
        return "album.cfg gelöscht."
    if action == "desc":
        a.album_description(request, {"album": album, "lang": v.get("lang"), "text": v.get("text", "")})
        return "Text gespeichert."
    if action == "upload":
        scope = "gallery" if page_id == "website" else "album"
        if scope == "album":
            album = a._album_or_400(album or "")
        done = _upload(request, scope, album, files)
        if not done:
            raise Bad("Keine Datei angekommen.")
        return f"Hochgeladen: {', '.join(done)}."
    if action == "asset_delete":
        scope = "gallery" if page_id == "website" else "album"
        _delete_asset(request, scope, a._album_or_400(album or "") if scope == "album" else None, v.get("name", ""))
        return f"{v.get('name')} gelöscht."
    if action == "restore":
        file = v.get("file") or ("gallery" if page_id == "website" else "album")
        _restore(request, file, album, v.get("lang") or None, v.get("version", ""))
        return "Diese Fassung ist wieder da."
    if action in ("featured_add", "featured_remove", "featured_move", "cover_set"):
        album = a._album_or_400(album or "")
        return _featured_edit(request, album, action, v.get("entry", ""), v.get("dir", ""))
    if action == "bulk":
        return _bulk_key(request, v)
    if action == "tag_merge":
        out = a.tags_rename(request, {"from": v.get("from"), "to": v.get("to")})
        lucent._rescan({p.rpartition("/")[0] for p in out.get("photos", [])})
        return f"„{v.get('from')}“ heißt jetzt „{v.get('to')}“ ({out.get('changed', 0)} Fotos)."
    if action == "password":
        if v.get("password") != v.get("again"):
            raise Bad("Die beiden neuen Passwörter sind nicht gleich.")
        if security.password_is_set():
            if not security.verify_password(v.get("current", "")):
                raise Bad("Das jetzige Passwort stimmt nicht.")
        security.audit(request, "Aperture password set", "data/console/credentials",
                       extra={"had_one": security.password_is_set()})
        security.set_password(v.get("password", ""))
        return "Passwort gesetzt. Alle Sitzungen in Aperture sind beendet."
    if action == "password_clear":
        if not security.may_run_open():
            raise Bad("Ohne Passwort dürfte Aperture an dieser Adresse nicht starten. Setze ein neues.")
        if not security.verify_password(v.get("current", "")):
            raise Bad("Das jetzige Passwort stimmt nicht.")
        security.audit(request, "Aperture password cleared", "data/console/credentials")
        security.clear_password()
        return "Passwort entfernt."
    raise HTTPException(404, f"no such action: {action!r}")


def _featured_edit(request: Request, album: str, action: str, entry: str, direction: str) -> str:
    entry = entry.strip()
    if not entry:
        raise Bad("Welches Foto?")
    values = cfgio.CfgFile.load(_app().lib.cfg_path(album)).values()
    featured = [i.strip() for i in values.get("featured", []) if i.strip()]
    if action == "cover_set":
        _save_album(request, album, {"cover": [entry]})
        return "Titelbild gesetzt."
    if any(i.lower() in ("*", "all") for i in featured) and action == "featured_add":
        raise Bad("Dieses Album hebt schon alle eigenen Fotos hervor (featured = *).")
    if action == "featured_add":
        if entry in featured:
            return "War schon hervorgehoben."
        featured.append(entry)
        msg = "Hervorgehoben."
    elif action == "featured_remove":
        featured = [i for i in featured if i != entry]
        msg = "Nicht mehr hervorgehoben."
    else:
        if entry not in featured:
            raise Bad("Dieses Foto ist nicht hervorgehoben.")
        i = featured.index(entry)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(featured):
            featured[i], featured[j] = featured[j], featured[i]
        msg = "Verschoben."
    _save_album(request, album, {"featured": featured or None})
    return msg


def _bulk_key(request: Request, v: dict) -> str:
    a = _app()
    key = v.get("key", "")
    if key not in schema.ALBUM_KEYS:
        raise Bad("Diesen Schlüssel gibt es in album.cfg nicht.")
    names = [n.strip().strip("/") for n in (v.get("albums") or "").splitlines() if n.strip()]
    if not names:
        raise Bad("Keine Alben genannt.")
    if len(names) > 500:
        raise Bad("Höchstens 500 Alben auf einmal.")
    spec = schema.KEY_SPEC[key]
    raw = v.get("value", "")
    if spec.get("type") == "list" and not spec.get("multiline") and "\n" in raw:
        raw = ", ".join(x.strip() for x in raw.splitlines() if x.strip())
    updates = a._updates_from({key: _from_form(raw, spec)})
    resolved = [a._album_or_400(n) for n in names]       # all or nothing: a typo stops before any write
    for album in resolved:
        _save_album(request, album, updates)
    return f"{_label(key)} auf {len(resolved)} Alben {'gesetzt' if updates.get(key) else 'entfernt'}."


# ----- pictures and the download ---------------------------------------------
@router.get("/lucent/v1/pages/{page_id}/file/{path:path}")
def page_file(request: Request, page_id: str, path: str):
    lucent._require_lucent(request)
    a = _app()
    from .. import scanner
    kind, _, rest = path.partition("/")
    if kind == "tr":          # a photo's thumbnail by its path
        rel = rest.strip("/")
        try:
            target = a.lib.safe(rel)
        except ValueError:
            raise HTTPException(400, "bad path")
        if not (target.is_file() and schema.is_image(target.name)):
            raise HTTPException(404, "no such photo")
        thumb = scanner.ensure_thumb(settings.photos_dir, settings.thumbs_dir, rel, settings.thumb_size)
        if thumb is None:
            raise HTTPException(415, "cannot decode this photo")
        return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600"})
    if kind in ("a", "g"):    # an asset in .album/ or .gallery/
        album, _, name = rest.rpartition("/") if kind == "a" else ("", "", rest)
        if Path(name).name != name or not name:
            raise HTTPException(400, "bad name")
        folder = a._asset_dir("album" if kind == "a" else "gallery", album)
        target = folder / name
        ext = target.suffix.lower()
        if not target.is_file() or ext not in schema.MIME or not schema.MIME[ext].startswith("image/"):
            raise HTTPException(404, "no such picture")
        return FileResponse(target, media_type=schema.MIME[ext], headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "no such file")


@router.get("/lucent/v1/pages/{page_id}/download/{name}")
def page_download(request: Request, page_id: str, name: str):
    """The backup, the console's Data → Backup: every hand-written file as one archive."""
    lucent._require_lucent(request)
    if page_id != "system" or name != "config.tar.gz":
        raise HTTPException(404, "no such download")
    import tempfile
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    result = reports.write_export(spool)
    size = spool.tell()
    spool.seek(0)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    request.state.lucent_by = "lucent"
    security.audit(request, "config exported", "(every .album/ and .gallery/)",
                   extra={"files": result["files"], "bytes": size, "via": "lucent"})
    data = spool.read()
    spool.close()
    return Response(data, media_type="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="gallery-config-{stamp}.tar.gz"'})


# ----- one photo: what the Library's inspector does beyond tags ---------------------
def media_extras(r) -> dict:
    """Sections and actions for one photo on Lucent's photo page: featured,
    cover, a pretty link, and the diagnostics the console's inspector shows."""
    rel, album, name = r["rel_path"], r["album"], r["filename"]
    values = cfgio.CfgFile.load(_app().lib.cfg_path(album)).values() if album else {}
    featured = [i.strip() for i in values.get("featured", []) if i.strip()]
    is_featured = name in featured or bool(r["is_showcase"])
    is_cover = cfgio.first(values, "cover") == name
    sections = []
    try:
        rep = reports.photo_report(rel)
        sections.append({"id": "diag", "title": "Diagnose", "kind": "kv", "items": [
            {"label": "Pfad", "value": rel},
            {"label": "Datei", "value": "da" if rep["file_exists"] else "fehlt", "tone": "ok" if rep["file_exists"] else "err"},
            {"label": "Hervorgehoben von", "value": ", ".join(f"{f['album']} ({f['entry']})" for f in rep["featured_by"]) or "–"},
            *[{"label": {"thumb": "Vorschaubild", "preview": "Vorschau", "full": "Umwandlung"}.get(k, k),
               "value": d["state"] or "–", "tone": "ok" if d["state"] == "ok" else "warn"}
              for k, d in rep["derivatives"].items()],
            {"label": "Im Index seit", "value": _when(rep.get("indexed_at")) if rep.get("indexed_at") else "–"}]})
    except Exception:
        pass
    to_it = [e for e in links.entries() if e["kind"] == "photo" and (e["target"] or "").strip("/") == rel]
    if to_it:
        sections.append({"id": "links", "title": "Kurzlinks", "kind": "rows",
                         "items": [{"title": links.address(e["slug"])} for e in to_it]})
    actions = []
    if not _ro() and album:
        actions.append(_act("unfeature" if is_featured else "feature",
                            "Nicht mehr hervorheben" if is_featured else "Hervorheben", icon="check"))
        if not is_cover:
            actions.append(_act("cover", "Als Titelbild des Albums", icon="image"))
        actions.append(_act("link", "Kurzlink anlegen", icon="link", fields=[
            {"name": "slug", "label": "Name", "type": "text", "required": True,
             "help": f"Erreichbar als {links.PREFIX}<name> auf der Galerie."}]))
        actions.append(_act("scan", "Album neu scannen", icon="clock"))
    badges = ([{"label": "hervorgehoben", "tone": "acc"}] if is_featured else []) + (
        [{"label": "Titelbild", "tone": "acc"}] if is_cover else [])
    return {"sections": sections, "actions": actions, "badges": badges}


@router.post("/lucent/v1/media/{image_id:int}/do/{action}")
async def media_do(request: Request, image_id: int, action: str):
    body = await _body(request)
    v = {k: str(x) for k, x in (body.get("values") or {}).items()}
    r = db.conn().execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "no such photo")
    try:
        notice = _media_do(request, [r], action, v)
    except Bad as e:
        raise HTTPException(422, str(e))
    except HTTPException as e:
        if e.status_code in (400, 404, 409):
            raise HTTPException(422, str(e.detail))
        raise
    return {"notice": notice}


@router.post("/lucent/v1/media/bulk/do/{action}")
async def media_bulk_do(request: Request, action: str):
    body = await _body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise HTTPException(400, "ids must be a list of 1 to 500 photo ids")
    try:
        nums = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "ids must be photo ids")
    rows = db.conn().execute(f"SELECT * FROM images WHERE id IN ({','.join('?' * len(nums))})", nums).fetchall()
    try:
        notice = _media_do(request, rows, action, {})
    except Bad as e:
        raise HTTPException(422, str(e))
    return {"notice": notice}


def _media_do(request: Request, rows, action: str, v: dict) -> str:
    a = _app()
    if _ro():
        raise Bad("Aperture ist nur zum Lesen eingebunden.")
    if action in ("feature", "unfeature"):
        by_album: dict[str, list[str]] = {}
        for r in rows:
            if r["album"]:
                by_album.setdefault(r["album"], []).append(r["filename"])
        n = 0
        for album, names in by_album.items():
            values = cfgio.CfgFile.load(a.lib.cfg_path(album)).values()
            featured = [i.strip() for i in values.get("featured", []) if i.strip()]
            if any(i.lower() in ("*", "all") for i in featured):
                continue
            before = list(featured)
            if action == "feature":
                featured += [x for x in names if x not in featured]
            else:
                featured = [x for x in featured if x not in names]
            if featured != before:
                _save_album(request, album, {"featured": featured or None})
                n += abs(len(featured) - len(before))
        if n:
            _job(request, "featured")
        return (f"{n} Foto(s) hervorgehoben." if action == "feature" else f"{n} Foto(s) nicht mehr hervorgehoben.") \
            if n else "Nichts zu ändern."
    r = rows[0]
    if action == "cover":
        if not r["album"]:
            raise Bad("Ein Foto im Hauptordner gehört zu keinem Album.")
        _save_album(request, r["album"], {"cover": [r["filename"]]})
        return "Titelbild gesetzt."
    if action == "link":
        a.links_write(request, {"slug": v.get("slug"), "target": r["rel_path"]})
        return f"Kurzlink {links.address(links.normalize_slug(v.get('slug')))} angelegt."
    if action == "scan":
        return _scan(request, r["album"] or None)
    raise HTTPException(404, f"no such action: {action!r}")
