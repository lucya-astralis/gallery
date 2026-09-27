"""Is the ground still there? The process watching its own feet.

`doctor` answers when it is asked. This does not wait to be asked: a thread
in every serving process looks at the four directories the app stands on --
photos, data, thumbnails, previews -- every HEALTH_INTERVAL seconds, and a
little less often at the rest of what an operator would want to hear about
before a visitor does (a volume filling up, a share the watcher cannot see,
the indexer's heartbeat, the last scan's error).

The case it exists for: a NAS that switches itself off at night while the
app keeps running. Until 2.4 every request after that was a bare "Internal
Server Error", and the indexer took an unreachable share for an emptied one
as far as its guards let it. Now:

  * the storage checks are *critical*. When one fails twice in a row the
    process goes DOWN: the gallery answers every request with a 503 and a
    page that says what is going on (FAILSAFE, gallery/app.py), scans and the
    watcher hold still, and nothing is deleted from the index;
  * a request that trips over the storage before the next probe (an OSError,
    a disk I/O error from sqlite) calls `suspect()`, which probes at once --
    the visitor who found out first gets the failsafe page, not the 500;
  * it comes back UP after UP_AFTER good rounds in a row, not after the first
    one: a NAS that is booting answers, stops answering, and answers again;
  * the listeners (`on_change`) hear about both edges -- the indexer asks for
    a scan when the ground comes back.

Every probe runs on a thread of its own with a timeout. A hard NFS mount
whose server has gone does not fail, it HANGS, forever, in the kernel -- a
`stat` on the monitor's own thread would stop the monitor with it. A probe
that does not answer in PROBE_TIMEOUT counts as failed, and while it is still
stuck no second one is started for the same directory, so a dead mount costs
one thread, not one per round.

What the failsafe page needs lives in memory (`failsafe_page`): the HTML is
built from strings, the one stylesheet is a constant, the site's name is
remembered from the last healthy round. Nothing on the way to "the archive
is asleep" touches a disk, because the disk may be the thing that is gone.

Everything is read in this process and nothing is written but a log line, so
the monitor is safe to run in every role; the console runs its own.
"""

from __future__ import annotations

import errno
import html
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .runtime import settings

log = logging.getLogger("aperture.health")

# ----- tuning ----------------------------------------------------------------
PROBE_TIMEOUT = 5.0     # seconds a directory has to answer
DOWN_AFTER = 2          # failed rounds in a row before the process goes DOWN
UP_AFTER = 3            # good rounds in a row before it comes back
ENV_EVERY = 6           # the environment checks run every Nth round
RETRY_AFTER = 60        # what the 503 tells a client, in seconds
EVENTS_KEPT = 20        # transitions remembered for the console and the CLI

# How full a volume may get. Either condition is enough.
DISK_WARN = (0.05, 1 << 30)      # under 5 % or under 1 GiB free
DISK_ERROR = (0.01, 200 << 20)   # under 1 % or under 200 MiB free
WAL_WARN = 64 << 20              # a -wal file this large is not being checkpointed

# errno values that say "the storage went away", not "this file is missing"
STORAGE_ERRNOS = {
    errno.EIO, errno.ENOTCONN, errno.ETIMEDOUT, errno.ESTALE,
    errno.EHOSTDOWN, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ENETUNREACH,
    errno.ECONNABORTED, errno.ECONNRESET, errno.ENODEV, errno.ENXIO,
    getattr(errno, "ENOMEDIUM", -1), getattr(errno, "EREMOTEIO", -1),
}

# Filesystems a watcher gets no events from, as /proc/mounts names them.
NETWORK_FS = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "9p",
              "fuse.rclone", "davfs", "fuse.davfs2"}


# ----- state -----------------------------------------------------------------
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None
_listeners: list = []
_stuck: dict[str, tuple[threading.Thread, float]] = {}   # key -> (probe, since)

_state: dict = {
    "state": "ok",          # "ok" | "warn" | "down"
    "since": time.time(),
    "checked_at": None,     # None until the first round has run
    "checks": [],
    "reason": None,         # one line: why DOWN
    "events": [],
}
_bad_rounds = 0
_good_rounds = 0
_suspect: str | None = None
_round = 0
_env_checks: list[dict] = []
_site_name: str | None = None


# ----- probing ---------------------------------------------------------------
def _with_timeout(key: str, fn, timeout: float | None = None):
    """fn() on a thread of its own. Returns (ok, value_or_detail).

    A probe still stuck from an earlier round is not joined by a new one:
    the answer is "still not answering" until it comes back."""
    if timeout is None:
        timeout = PROBE_TIMEOUT
    stuck = _stuck.get(key)
    if stuck is not None:
        probe, since = stuck
        if probe.is_alive():
            return False, "not answering (a check has been stuck for %ds)" % (time.time() - since)
        _stuck.pop(key, None)

    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:   # noqa: BLE001 -- reported, not raised
            box["error"] = e

    probe = threading.Thread(target=run, name="health-probe-" + key, daemon=True)
    probe.start()
    probe.join(timeout)
    if probe.is_alive():
        _stuck[key] = (probe, time.time())
        return False, "not answering (no reply within %ds)" % timeout
    if "error" in box:
        return False, describe(box["error"])
    return True, box.get("value")


def describe(e: BaseException) -> str:
    if isinstance(e, _Empty):
        return str(e)
    if isinstance(e, OSError) and e.errno is not None:
        return "%s (%s)" % (os.strerror(e.errno), errno.errorcode.get(e.errno, e.errno))
    return "%s: %s" % (type(e).__name__, e)


def _index_has_rows() -> bool:
    """Whether the index holds photos -- read on a connection of its own, so a
    probe thread never leaves a thread-local one behind."""
    db_file = settings.gallery_db
    if not db_file.exists():
        return False
    c = sqlite3.connect("file:%s?mode=ro" % db_file.as_posix(), uri=True, timeout=2)
    try:
        return c.execute("SELECT EXISTS(SELECT 1 FROM images)").fetchone()[0] == 1
    except sqlite3.OperationalError:
        return False      # no table yet: a fresh install
    finally:
        c.close()


def _probe_photos() -> str:
    root = settings.photos_dir
    try:
        entries = [e for e in os.listdir(root) if not e.startswith(".")]
    except FileNotFoundError:
        # a fresh install without a photo tree yet serves an empty gallery;
        # one whose index remembers photos has lost its share
        if _index_has_rows():
            raise _Empty("gone, but the index holds photos -- the share is not mounted") from None
        return "does not exist yet"
    # The empty mount point a `nofail` share leaves behind lists fine -- it
    # is only wrong because the index remembers photos under it.
    if not entries and _index_has_rows():
        raise _Empty("empty, but the index holds photos -- the share is not mounted")
    return "%d entries" % len(entries)


def _probe_dir(path: Path):
    def probe() -> str:
        os.listdir(path)
        return "reachable"
    return probe


def _probe_data() -> str:
    os.listdir(settings.data_dir)
    db_file = settings.gallery_db
    if db_file.exists():
        with open(db_file, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise _Empty("gallery.db does not read as a database")
    return "reachable"


class _Empty(Exception):
    """A directory that answers but is not what it should be."""

    def __str__(self) -> str:
        return self.args[0]


def storage_checks() -> list[dict]:
    """The critical half: can this process reach what it stands on?"""
    specs = [("photos", "photos", settings.photos_dir, _probe_photos),
             ("data", "data", settings.data_dir, _probe_data)]
    # the derivative trees only matter where the gallery serves them
    if settings.runs_public:
        specs += [("thumbs", "thumbnails", settings.thumbs_dir, _probe_dir(settings.thumbs_dir)),
                  ("previews", "previews", settings.previews_dir, _probe_dir(settings.previews_dir))]
    out = []
    for key, label, path, fn in specs:
        started = time.monotonic()
        ok, detail = _with_timeout(key, fn)
        out.append({"key": key, "label": label, "path": str(path), "critical": True,
                    "level": "ok" if ok else "error",
                    "detail": detail if isinstance(detail, str) else "reachable",
                    "ms": int((time.monotonic() - started) * 1000)})
    return out


# ----- the environment (not critical: said, never acted on) -------------------
def _mount_of(path: Path) -> tuple[str, str] | None:
    """(mount point, fstype) of the longest /proc/mounts entry above path."""
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    best = None
    target = str(path)
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/") or point == "/":
            if best is None or len(point) > len(best[0]):
                best = (point, parts[2])
    return best


def _disk(label: str, path: Path) -> dict | None:
    try:
        total, _used, free = shutil.disk_usage(path)
    except OSError:
        return None             # the storage check says it better
    share = free / total if total else 1.0
    gib = free / (1 << 30)
    detail = "%.1f GiB free (%d %%)" % (gib, round(share * 100))
    level = "ok"
    if share < DISK_ERROR[0] or free < DISK_ERROR[1]:
        level = "error"
    elif share < DISK_WARN[0] or free < DISK_WARN[1]:
        level = "warn"
    return {"key": "disk_" + label, "label": label + " volume", "path": str(path),
            "critical": False, "level": level, "detail": detail}


def environment_checks() -> list[dict]:
    """What an operator should hear about before a visitor notices it."""
    from . import control

    out: list[dict] = []

    def add(key, label, level, detail, fix=None):
        entry = {"key": key, "label": label, "critical": False, "level": level, "detail": detail}
        if fix:
            entry["hint"] = fix
        out.append(entry)

    # --- space, once per volume ---
    seen_dev: set[int] = set()
    for label, path in (("data", settings.data_dir), ("thumbnails", settings.thumbs_dir),
                        ("previews", settings.previews_dir)):
        try:
            dev = path.stat().st_dev
        except OSError:
            continue
        if dev in seen_dev:
            continue
        seen_dev.add(dev)
        row = _disk(label, path)
        if row is not None:
            if row["level"] != "ok":
                row["hint"] = "`python -m aperture.cli thumbs --prune` finds leftovers of old formats"
            out.append(row)

    # --- a share the watcher cannot see ---
    mount = _mount_of(settings.photos_dir)
    if mount and mount[1] in NETWORK_FS:
        if settings.scan_interval <= 0:
            add("network_share", "photo share", "warn",
                "photos are on a %s share and SCAN_INTERVAL=0: a network share delivers no "
                "file events, so new photos are only found by a manual scan" % mount[1],
                "set SCAN_INTERVAL (300 is the default)")
        else:
            add("network_share", "photo share", "ok",
                "%s share, rescanned every %ds" % (mount[1], settings.scan_interval))

    # --- the public side should not be able to write the photos ---
    if settings.role == "public" and settings.photos_dir.is_dir() and os.access(settings.photos_dir, os.W_OK):
        add("photos_rw", "photo mount", "warn",
            "the public instance can write to the photo tree; mount it read-only (:ro)")

    # --- the index file ---
    wal = Path(str(settings.gallery_db) + "-wal")
    try:
        size = wal.stat().st_size
    except OSError:
        size = 0
    if size > WAL_WARN:
        add("wal", "index journal", "warn",
            "gallery.db-wal is %d MiB -- something holds a read open for a long time" % (size >> 20),
            "a restart checkpoints it")

    # --- the indexer: alive, and what its last scan said ---
    st = control.read_status()
    if not control.status_is_live(st):
        if settings.owns_indexer:
            pass            # this process IS the indexer; its heartbeat may simply be starting
        else:
            add("indexer", "indexer", "warn",
                "no heartbeat from the indexer -- is the public instance running?")
    else:
        last = (st or {}).get("last_scan") or {}
        if last.get("error"):
            add("last_scan", "last scan", "warn", "failed: %s" % last["error"])
        elif (last.get("result") or {}).get("held"):
            add("last_scan", "last scan", "warn",
                "found no photos and kept the index as it was -- is the share mounted?")
        elif (last.get("result") or {}).get("walk_errors"):
            add("last_scan", "last scan", "warn",
                "could not read %d folder(s); nothing was removed from the index"
                % last["result"]["walk_errors"])
    return out


# ----- one round -------------------------------------------------------------
def _refresh_site_name() -> None:
    """Remember the archive's name while the storage is there to read it --
    gallery.cfg lives in the photo tree, which is what the failsafe page is
    about to be missing."""
    global _site_name
    try:
        from . import branding
        _site_name = branding.site_brand()["name"]
    except Exception:
        pass


def check_now(force_env: bool = False) -> dict:
    """One round, on the caller's thread (the probes still time out on their
    own). What the monitor runs, and what the CLI runs to look for itself."""
    global _bad_rounds, _good_rounds, _suspect, _round, _env_checks
    storage = storage_checks()
    failing = [c for c in storage if c["level"] != "ok"]
    _round += 1
    # Not while the storage is gone: they would only restate it, slower.
    if not failing and (force_env or (_round - 1) % ENV_EVERY == 0):
        try:
            _env_checks = environment_checks()
        except Exception as e:
            log.warning("environment checks failed: %s", describe(e))
    if not failing:
        _refresh_site_name()

    events = []
    with _lock:
        was = _state["state"]
        suspect, _suspect = _suspect, None
        if failing:
            _good_rounds = 0
            _bad_rounds += 1
            # a request that already tripped over it counts as the first round
            confirmed = _bad_rounds >= DOWN_AFTER or (suspect is not None) or was == "down"
        else:
            _bad_rounds = 0
            _good_rounds += 1
            confirmed = False
        if confirmed:
            new = "down"
        elif was == "down" and (failing or _good_rounds < UP_AFTER):
            new = "down"        # not back until it has stayed back
        else:
            new = "warn" if any(c["level"] != "ok" for c in _env_checks) else "ok"
        reason = None
        if new == "down":
            src = failing or [c for c in _state["checks"] if c.get("critical") and c["level"] != "ok"]
            reason = "; ".join("%s: %s" % (c["label"], c["detail"]) for c in src) or _state["reason"] \
                or "storage unreachable"
        _state["checks"] = storage + _env_checks
        _state["checked_at"] = time.time()
        if new != was:
            _state["since"] = time.time()
            ev = {"at": _state["since"], "from": was, "to": new,
                  "detail": reason if new == "down" else _summary(new)}
            _state["events"] = (_state["events"] + [ev])[-EVENTS_KEPT:]
            events.append(ev)
        _state["state"] = new
        _state["reason"] = reason
    for ev in events:
        _announce(ev)
    return snapshot()


def _summary(state: str) -> str:
    if state == "ok":
        return "everything answers"
    bad = [c for c in _env_checks if c["level"] != "ok"]
    return "; ".join("%s: %s" % (c["label"], c["detail"]) for c in bad)


def _announce(ev: dict) -> None:
    if ev["to"] == "down":
        log.error("FAILSAFE: storage unreachable -- %s. The gallery answers 503 and the "
                  "indexer holds still until it is back.", ev["detail"])
    elif ev["from"] == "down":
        log.warning("storage is back after %s -- leaving failsafe", _ago(ev["at"], _prev_since(ev)))
    elif ev["to"] == "warn":
        log.warning("health: %s", ev["detail"])
    for fn in list(_listeners):
        try:
            fn(ev)
        except Exception as e:
            log.warning("health listener failed: %s", describe(e))


def _prev_since(ev: dict) -> float:
    for prior in reversed(_state["events"]):
        if prior is not ev and prior["to"] == "down":
            return prior["at"]
    return ev["at"]


def _ago(now: float, then: float) -> str:
    s = int(max(0, now - then))
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dmin" % (s // 60)
    return "%dh %02dmin" % (s // 3600, s % 3600 // 60)


# ----- the monitor -----------------------------------------------------------
def _loop() -> None:
    while True:
        try:
            check_now()
        except Exception as e:      # the watcher must outlive its own bugs
            log.warning("health round failed: %s", describe(e))
        _wake.wait(settings.health_interval)
        _wake.clear()


def start() -> None:
    """Run the monitor in this process. Idempotent: the gallery and the
    console both ask for it, and in role=all they are one process."""
    global _thread
    if settings.health_interval <= 0:
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="health", daemon=True)
        _thread.start()


def running() -> bool:
    return _thread is not None and _thread.is_alive()


def on_change(fn) -> None:
    """fn(event) on every transition: {"at", "from", "to", "detail"}."""
    if fn not in _listeners:
        _listeners.append(fn)


def suspect(exc: BaseException | None = None) -> None:
    """A request tripped over something that may be the storage. Probe now
    rather than at the next tick; if the probe fails too, that is enough to
    go DOWN."""
    global _suspect
    with _lock:
        _suspect = describe(exc) if exc is not None else "a request failed"
    if running():
        _wake.set()
    else:
        check_now()


def is_storage_error(exc: BaseException) -> bool:
    """Whether an exception a request raised says the ground is gone."""
    if isinstance(exc, sqlite3.DatabaseError):
        msg = str(exc).lower()
        return "disk i/o" in msg or "unable to open" in msg or "readonly" in msg \
            or "malformed" in msg or "not a database" in msg
    if isinstance(exc, OSError):
        return exc.errno in STORAGE_ERRNOS
    return False


def is_down() -> bool:
    """Whether the gallery answers from failsafe: DOWN, and FAILSAFE on."""
    return _state["state"] == "down" and settings.failsafe


def storage_down() -> bool:
    """DOWN, whatever FAILSAFE says: what writers ask before they write. The
    switch decides what visitors see, never whether the index is touched."""
    return _state["state"] == "down"


def reachable(path: Path) -> bool:
    """Whether a directory lists, and lists something, within the timeout."""
    ok, _detail = _with_timeout("reach-" + str(path), lambda: bool(os.listdir(path)))
    return ok and _detail is True


def storage_ok() -> bool:
    """For writers (the scan's cleanup, the watcher's forget): the monitor's
    verdict, and -- because a verdict can be up to one interval old -- a
    fresh look at the photo root. Never removes anything on a maybe."""
    if storage_down():
        return False
    ok, _detail = _with_timeout("photos-now", _probe_photos)
    if not ok:
        suspect()
    return ok


def snapshot(volatile: bool = True) -> dict:
    """The state, for the console, the CLI and /healthz. `volatile=False`
    leaves out what changes every round (timings, the clock), so a live
    stream that only sends on change stays quiet while nothing does."""
    with _lock:
        snap = {
            "state": _state["state"],
            "failsafe": _state["state"] == "down" and settings.failsafe,
            "since": _state["since"],
            "reason": _state["reason"],
            "checks": [dict(c) for c in _state["checks"]],
            "events": list(_state["events"]),
            "monitor": running(),
            "interval": settings.health_interval,
        }
        if volatile:
            snap["checked_at"] = _state["checked_at"]
    if not volatile:
        for c in snap["checks"]:
            c.pop("ms", None)
    return snap


def _reset_for_tests() -> None:
    """Back to a process that has not looked yet. FOR TESTS."""
    global _bad_rounds, _good_rounds, _suspect, _round, _env_checks
    with _lock:
        _state.update(state="ok", since=time.time(), checked_at=None, checks=[],
                      reason=None, events=[])
        _bad_rounds = _good_rounds = _round = 0
        _suspect = None
        _env_checks = []
    _stuck.clear()


# ----- the failsafe page, from memory ----------------------------------------
# Held as strings: no template loader, no static file, no font. The stylesheet
# is served from /_failsafe.css out of this constant, because the CSP allows
# no inline style; the refresh is a meta tag, because it allows no inline
# script -- and a meta refresh is not a script.
FAILSAFE_CSS = """\
:root{color-scheme:dark;--bg:#07070c;--fg:#e8e6f0;--dim:#8d8aa3;--acc:#b9a7ff;--red:#ff6b81}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--fg);
font:16px/1.55 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
main{min-height:100vh;display:grid;place-items:center;padding:24px 16px}
.box{max-width:34rem;width:100%}
.hud{display:flex;gap:.75rem;align-items:center;font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin-bottom:2rem}
.dot{width:.6rem;height:.6rem;border-radius:50%;background:var(--red);box-shadow:0 0 12px var(--red);
animation:blink 2s steps(2,start) infinite}
@keyframes blink{to{visibility:hidden}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.name{font-size:.8rem;letter-spacing:.2em;text-transform:uppercase;color:var(--acc);margin:0 0 .5rem}
h1{font-size:clamp(1.6rem,5vw,2.4rem);line-height:1.15;margin:0 0 1rem;font-weight:650}
p{margin:0 0 1rem;color:var(--fg)}
.note{color:var(--dim);font-size:.9rem}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim)}
"""

_FAILSAFE_HTML = """\
<!doctype html>
<html lang="{html_lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{retry}">
<meta name="robots" content="noindex">
<title>{title} — {name}</title>
<link rel="stylesheet" href="/_failsafe.css">
</head>
<body>
<main><div class="box">
<div class="hud"><span class="dot" aria-hidden="true"></span><span>SIG / LOST</span><span>·</span><span>{since_hud}</span></div>
<p class="name">{name}</p>
<h1>{title}</h1>
<p>{lead}</p>
<p class="note">{note}</p>
</div></main>
</body>
</html>
"""


def failsafe_page(lang: str) -> str:
    from . import i18n

    def t(key: str, **fmt) -> str:
        return html.escape(i18n.t(lang, key, **fmt))

    since = _state["since"]
    return _FAILSAFE_HTML.format(
        html_lang=i18n.HTML_LANG.get(lang, "en"),
        retry=RETRY_AFTER,
        name=html.escape(_site_name or "Gallery"),
        title=t("failsafe.title"),
        lead=t("failsafe.lead"),
        note=t("failsafe.note", seconds=RETRY_AFTER),
        since_hud=html.escape("T+" + _ago(time.time(), since)),
    )
