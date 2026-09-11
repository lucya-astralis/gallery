"""The indexer's lifecycle, in the process that owns it.

Scans, the control loop that picks up requests from data/control/, and the
status heartbeat the CLI and the console read. Started by the gallery app's
lifespan -- which only runs where APERTURE_ROLE includes the public surface,
so there is exactly one writer on the index.
"""

from __future__ import annotations

import logging
import threading
import time

from . import albums, control, db, scanner, watcher
from .runtime import settings

log = logging.getLogger("aperture.indexer")
_scan_lock = threading.Lock()
_STARTED_AT = time.time()

# ----- indexer control --------------------------------------------------
# The gallery takes no orders over HTTP — there is no endpoint that makes the
# server do work, by design. Operations (pause, resume, "scan now") arrive as
# flag files in DATA_DIR/control, written by the CLI and picked up by
# _control_loop; the same loop publishes status.json, which is the only window
# the CLI has into this process. The channel itself lives in aperture/control.py.
_HEARTBEAT_INTERVAL = 15.0
_scan_state: dict = {
    "scanning": False,
    "started_at": None,
    "trigger": None,
    "last_scan": None,
}
_scan_state_lock = threading.Lock()


def _publish_status() -> None:
    """Snapshot this process for `python -m aperture.cli status`. Cheap enough to
    call on every scan edge plus a slow heartbeat."""
    with _scan_state_lock:
        state = dict(_scan_state)
    pause = control.pause_info()
    control.publish_status({
        "started_at": _STARTED_AT,
        "paused": pause is not None,
        "pause": pause,
        "scanning": state["scanning"],
        "scan_started_at": state["started_at"],
        "scan_trigger": state["trigger"],
        "last_scan": state["last_scan"],
        "pending_request": control.pending_scan_request(),
        "watcher": {
            "enabled": settings.enable_watcher,
            "running": watcher.is_running(),
            "pending": watcher.pending_count(),
        },
        "config": {
            "photos_dir": str(settings.photos_dir),
            "thumbs_dir": str(settings.thumbs_dir),
            "previews_dir": str(settings.previews_dir),
            "data_dir": str(settings.data_dir),
            "thumb_size": settings.thumb_size,
            "preview_size": settings.preview_size,
            "scan_interval": settings.scan_interval,
            "hide_gps": settings.hide_gps,
            "strip_gps": settings.strip_gps,
        },
    })


def run_scan(trigger: str = "periodic", album: str | None = None,
              force: bool = False, request_id: str | None = None) -> dict | None:
    """One indexing pass, then a featured recompute. Returns the run summary,
    or None when a scan was already in flight — the lock is never waited on,
    two overlapping scans would only fight over the same rows."""
    if not _scan_lock.acquire(blocking=False):
        log.info("scan (%s) skipped: a scan is already running", trigger)
        return None
    started = time.time()
    error = None
    result = None
    with _scan_state_lock:
        _scan_state.update(scanning=True, started_at=started, trigger=trigger)
    _publish_status()
    try:
        try:
            result = scanner.full_scan(
                settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                previews_dir=settings.previews_dir, preview_size=settings.preview_size,
                root=album, force=force,
            )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.exception("scan failed: %s", e)
        # Re-derive featured flags from album.cfg.
        # Runs even when the walk blew up: the index is then partial, but
        # leaving is_showcase stale on top of it hides featured photos too.
        albums.recompute_featured()
        if result and any(result[k] for k in ("indexed", "thumbnails", "previews", "removed", "failed")):
            log.info("scan: %s", result)
        if result and result["failed"]:
            log.warning("scan: %d file(s) unreadable — see the 'thumb failed' / "
                        "'skipped' warnings above; they stay in the gallery "
                        "without a thumbnail until fixed or removed",
                        result["failed"])
    except Exception as e:
        error = error or f"{type(e).__name__}: {e}"
        log.exception("scan bookkeeping failed: %s", e)
    finally:
        finished = time.time()
        summary = {
            "trigger": trigger,
            "request_id": request_id,
            "album": album,
            "force": force,
            "started_at": started,
            "finished_at": finished,
            "seconds": round(finished - started, 3),
            "error": error,
            "result": result,
        }
        with _scan_state_lock:
            _scan_state.update(scanning=False, started_at=None, trigger=None,
                               last_scan=summary)
        _scan_lock.release()
        _publish_status()
    return summary


def _control_loop():
    """Heartbeat, control channel and periodic rescan in one thread.

    Ticks every control.CONTROL_TICK seconds, so a manual scan starts within
    ~2s instead of after a whole SCAN_INTERVAL. Runs even with
    SCAN_INTERVAL=0: the periodic pass is off then, but pause/resume, manual
    scans and the status heartbeat still work."""
    last_periodic = time.monotonic()
    last_beat = 0.0
    while True:
        time.sleep(control.CONTROL_TICK)
        try:
            req = control.take_scan_request()
            if req is not None:
                # A requested scan ignores the pause on purpose: it was asked
                # for explicitly, and it is how you index a one-off change
                # without lifting a maintenance pause.
                run_scan(trigger="manual", album=req.get("album"),
                          force=bool(req.get("force")), request_id=req.get("id"))
                last_periodic = last_beat = time.monotonic()
                continue
            now = time.monotonic()
            if (settings.scan_interval > 0 and not control.is_paused()
                    and now - last_periodic >= settings.scan_interval):
                run_scan(trigger="periodic")
                last_periodic = last_beat = time.monotonic()
                continue
            if now - last_beat >= _HEARTBEAT_INTERVAL:
                last_beat = now
                _publish_status()
        except Exception as e:
            log.warning("control tick failed: %s: %s", type(e).__name__, e)


def startup():
    """Everything this process has to have running: the index, the featured
    flags, the watcher and the control loop. Driven by _lifespan (top of the
    file) — FastAPI's on_event hooks are deprecated."""
    db.init(settings.data_dir)
    albums.recompute_featured()
    log.info(
        "photos=%s thumbs=%s data=%s thumb_size=%d watcher=%s scan_interval=%ds hide_gps=%s strip_gps=%s",
        settings.photos_dir, settings.thumbs_dir, settings.data_dir, settings.thumb_size, settings.enable_watcher, settings.scan_interval, settings.hide_gps, settings.strip_gps,
    )
    if control.is_paused():
        info = control.pause_info() or {}
        # A pause is deliberately persistent: it survives the restart it was
        # very likely set for. No startup scan, no periodic scan; the watcher
        # still starts, but only queues events (see watcher._drain).
        log.warning("indexer PAUSED (%s) — resume with `python -m aperture.cli resume`",
                    info.get("reason") or "no reason given")
    else:
        threading.Thread(target=run_scan, kwargs={"trigger": "startup"}, daemon=True).start()
    if settings.enable_watcher:
        try:
            watcher.start(settings.photos_dir, settings.thumbs_dir, settings.thumb_size,
                          previews_dir=settings.previews_dir, preview_size=settings.preview_size,
                          fulls_dir=settings.fulls_dir, on_config=albums.recompute_featured)
        except Exception as e:
            log.warning("watcher failed to start: %s", e)
    threading.Thread(target=_control_loop, daemon=True).start()
    if settings.scan_interval > 0:
        log.info("periodic rescan every %d seconds", settings.scan_interval)
    _publish_status()


def shutdown():
    # Drop the snapshot so the CLI reports "not running" right away instead
    # of waiting for the heartbeat to age out.
    control.clear_status()
