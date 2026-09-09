"""Cross-process control channel between the CLI and the running server.

The gallery speaks HTTP strictly read-only — there is no endpoint that can be
poked to make the server *do* something, and that stays that way. Operational
control (pause the indexer, ask for a scan, read live state) therefore goes
through small JSON files under `DATA_DIR/control/`:

    paused.json         written by the CLI  -> read by the server
    scan.request.json   written by the CLI  -> consumed by the server
    status.json         written by the server -> read by the CLI

The server's control loop (main._control_loop) ticks every CONTROL_TICK
seconds: it publishes `status.json` as a heartbeat, picks up a pending scan
request, and skips periodic work while `paused.json` exists. So the whole
channel is one directory of tiny files — no socket, no port, no token, and
nothing that survives a `docker compose down -v` beyond the data volume.

Both sides go through this module, so the file names and payload shapes live
in exactly one place. Writes are atomic (tmp file + os.replace), because the
reader is a different process and must never see a half-written file.

State semantics:
  * `paused.json` is PERSISTENT — it survives a restart on purpose. A pause
    set before a maintenance restart is still in effect afterwards; only
    `resume` (or deleting the file) lifts it.
  * `status.json` is EPHEMERAL — it describes one server process. Its
    `heartbeat` tells a reader whether that process is still alive; see
    `status_is_live`.
"""

import json
import os
import time
from pathlib import Path

CONTROL_DIR_NAME = "control"
PAUSE_FILE = "paused.json"
SCAN_REQUEST_FILE = "scan.request.json"
STATUS_FILE = "status.json"

# How often the server's control loop looks at this directory. Also the upper
# bound on "how long until a manual scan actually starts".
CONTROL_TICK = 2.0
# A status file whose heartbeat is older than this is considered dead: the
# server was killed, so nothing is going to update it any more. Generous
# enough to survive a long blocking scan tick on a slow SMB share.
STATUS_STALE_AFTER = 90.0

_dir: Path | None = None


# ----- plumbing ---------------------------------------------------------
def configure(data_dir: Path) -> Path:
    """Point the channel at `DATA_DIR/control/` and create it. Called by both
    sides at import time; safe to call repeatedly."""
    global _dir
    _dir = Path(data_dir) / CONTROL_DIR_NAME
    try:
        _dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only data dir: reads still work, writes will just fail
    _sweep_tmp()
    return _dir


def control_dir() -> Path:
    if _dir is None:
        raise RuntimeError("control channel not configured")
    return _dir


def _path(name: str) -> Path:
    return control_dir() / name


def _read(name: str) -> dict | None:
    """Parsed control file, or None when it's absent, unreadable or corrupt.
    A corrupt file is treated as absent rather than raising: a half-written
    or hand-edited file must never take the server down."""
    try:
        text = _path(name).read_text(encoding="utf-8")
    except (OSError, RuntimeError):
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _write(name: str, payload: dict) -> bool:
    """Atomically replace a control file. The reader is another process, so a
    plain open+write could hand it a truncated file — write a sibling tmp and
    os.replace() it in, which is atomic on both POSIX and Windows.

    The tmp is always cleaned up. status.json is rewritten every CONTROL_TICK,
    so a replace that fails (on Windows an antivirus or the search indexer can
    hold the target open for a moment) used to leave one `.tmp<pid>` behind
    per attempt and nothing ever collected them. _sweep_tmp additionally
    clears the ones a killed process left mid-write."""
    try:
        target = _path(name)
    except RuntimeError:
        return False
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


TMP_STALE_AFTER = 60.0


def _sweep_tmp() -> None:
    """Drop `<file>.tmp<pid>` leftovers from a run that died mid-write.

    Age-gated rather than pid-gated: the CLI and the server both configure()
    the same directory, and a live _write holds its tmp for microseconds, so
    anything older than TMP_STALE_AFTER belongs to nobody. Called once at
    configure() time by both sides."""
    if _dir is None:
        return
    cutoff = time.time() - TMP_STALE_AFTER
    try:
        entries = list(_dir.glob("*.tmp*"))
    except OSError:
        return
    for f in entries:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _remove(name: str) -> bool:
    try:
        _path(name).unlink()
        return True
    except (OSError, RuntimeError):
        return False


def _now() -> float:
    return time.time()


# ----- pause / resume ---------------------------------------------------
def is_paused() -> bool:
    """True while indexing is suspended. Checked by the periodic scan loop
    and by the watcher's drain — a manual scan deliberately ignores it."""
    try:
        return _path(PAUSE_FILE).exists()
    except RuntimeError:
        return False


def pause_info() -> dict | None:
    """{since, reason, by} of the active pause, or None when running. Returns
    a stub for a pause file that exists but can't be parsed, so a hand-made
    empty `paused.json` still reads as paused."""
    if not is_paused():
        return None
    return _read(PAUSE_FILE) or {"since": None, "reason": None, "by": None}


def pause(reason: str | None = None, by: str = "cli") -> dict:
    info = {"since": _now(), "reason": reason or None, "by": by}
    _write(PAUSE_FILE, info)
    return info


def resume() -> bool:
    """Lift the pause. False when it wasn't paused in the first place."""
    if not is_paused():
        return False
    return _remove(PAUSE_FILE)


# ----- scan requests ----------------------------------------------------
def request_scan(album: str | None = None, force: bool = False,
                 by: str = "cli") -> dict:
    """Queue a scan for the server's control loop. One slot only — a second
    request before the first is picked up replaces it, which is what you want
    for "scan now" (two identical requests are one scan, not two)."""
    req = {
        "id": f"{int(_now() * 1000):x}-{os.getpid():x}",
        "album": album or None,
        "force": bool(force),
        "requested_at": _now(),
        "by": by,
    }
    _write(SCAN_REQUEST_FILE, req)
    return req


def pending_scan_request() -> dict | None:
    """Peek at a queued request without consuming it (for `status`)."""
    return _read(SCAN_REQUEST_FILE)


def take_scan_request() -> dict | None:
    """Consume a queued request, or None. Renames first, so a request written
    between the read and the delete isn't silently dropped."""
    try:
        src = _path(SCAN_REQUEST_FILE)
        if not src.exists():
            return None  # the common case, ticked several times a minute
        taken = src.with_suffix(src.suffix + ".taken")
        os.replace(src, taken)
    except (OSError, RuntimeError):
        return None
    try:
        payload = json.loads(taken.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    try:
        taken.unlink()
    except OSError:
        pass
    return payload if isinstance(payload, dict) else {"id": None, "album": None, "force": False}


# ----- status -----------------------------------------------------------
def publish_status(payload: dict) -> bool:
    """Server side: write the live snapshot, stamped with a fresh heartbeat."""
    payload = dict(payload)
    payload["heartbeat"] = _now()
    payload["pid"] = os.getpid()
    return _write(STATUS_FILE, payload)


def read_status() -> dict | None:
    return _read(STATUS_FILE)


def status_is_live(status: dict | None) -> bool:
    """Is the process that wrote this snapshot still around? A stale file
    means the server died without clearing it (kill -9, container OOM)."""
    if not status:
        return False
    hb = status.get("heartbeat")
    if not isinstance(hb, (int, float)):
        return False
    return (_now() - hb) < STATUS_STALE_AFTER


def clear_status() -> bool:
    """Server side: drop the snapshot on a clean shutdown, so the CLI reports
    "not running" immediately instead of waiting for the heartbeat to age out."""
    return _remove(STATUS_FILE)
