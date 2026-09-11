"""Operations, over HTTP.

The CLI could always run or pause the indexer and check the gallery for drift.
The console could not, because it shipped as a separate program that was not
allowed to assume the gallery existed. It ships in the same package now, so
that reason is gone and these routes are the other half of `aperture/ops.py`:
the same functions the CLI renders to a terminal, serialized instead.

Two things this deliberately does NOT do.

It does not call the indexer. A scan request is written to the control channel
in `data/control/` and picked up by the control loop, exactly as the CLI has
always done it — even in the `all` role, where the console shares a process
with the thing it is asking. One code path, one place a scan can begin, and
the split deployment (`APERTURE_ROLE=console` in its own container) keeps
working with no second implementation.

And it does not write the index. Every route here either reads the database or
writes a small JSON file into DATA_DIR. The indexer stays the single writer.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from .. import ops
from ..ops import UnknownAlbum
from ..runtime import settings
from . import security

log = logging.getLogger("console.ops")

router = APIRouter(prefix="/api/ops")

# The console asks as "console", the CLI as "cli". The value rides into
# status.json and the audit log, so "who started this scan" is answerable
# afterwards — and only one of the two leaves a shell history behind.
BY = "console"


def _guard_actions() -> None:
    """READ_ONLY means the console cannot change anything, not merely that it
    cannot change photos. Pausing the indexer is a change."""
    if settings.console_read_only:
        raise HTTPException(403, "the console is mounted read-only")


def _album(raw: str | None) -> str | None:
    try:
        return ops.norm_album(raw)
    except UnknownAlbum as exc:
        raise HTTPException(404, "no such album: %r" % exc.args[0])


# ----- state ------------------------------------------------------------
@router.get("/status")
def api_status():
    """What the CLI's `status` opens with: is the indexer alive, is it paused,
    what did the last scan do, what does the index hold, and where."""
    report = ops.status()
    report["paths"] = ops.paths()
    report["read_only"] = settings.console_read_only
    report["role"] = settings.role
    return report


# ----- actions ----------------------------------------------------------
@router.post("/scan")
async def api_scan(request: Request):
    """Queue a scan. Returns the request id; poll /scan/{id} for the summary.

    Deliberately not a blocking call: a full pass over a large share on SMB
    takes minutes, and an HTTP request that waits for one is a request that
    times out somewhere between here and the browser.
    """
    _guard_actions()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")

    album = _album(body.get("album"))
    force = bool(body.get("force"))
    if album and not (settings.photos_dir / album).is_dir():
        raise HTTPException(404, "no such album: %r" % album)

    req = ops.request_scan(album=album, force=force, by=BY)
    security.audit(request, "scan requested", album or "(whole gallery)",
                   extra={"force": force, "request_id": req["id"]})
    status = ops.status()
    return {"ok": True, "request": req, "live": status["live"],
            "note": None if status["live"] else
                    "no indexer is listening — the request stays queued and "
                    "runs when one starts"}


@router.get("/scan/{request_id}")
def api_scan_result(request_id: str):
    """The summary of one requested scan, or `null` while it is still
    running. A poller's "not yet" is a 200 with no result, not a 404 — the
    request exists, its answer does not."""
    return {"request_id": request_id, "result": ops.scan_result(request_id),
            "scanning": bool((ops.control.read_status() or {}).get("scanning"))}


@router.post("/pause")
async def api_pause(request: Request):
    """Suspend indexing. Persistent on purpose: it survives the restart it was
    very likely set for, and only resume lifts it."""
    _guard_actions()
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = str((body or {}).get("reason") or "").strip()
    info = ops.pause(reason, by=BY)
    security.audit(request, "indexer paused", reason or "(no reason given)")
    return {"ok": True, "pause": info}


@router.post("/resume")
def api_resume(request: Request):
    _guard_actions()
    was_paused = ops.resume()
    security.audit(request, "indexer resumed", "-",
                   extra={"was_paused": was_paused})
    return {"ok": True, "was_paused": was_paused}


# ----- reports ----------------------------------------------------------
@router.get("/doctor")
def api_doctor(album: str | None = None, limit_slow: int = 50):
    """Index, files, derivatives and config checked against each other.

    Runs in the threadpool (this is a `def`, not an `async def`) because it
    walks the whole tree — on a large share that is seconds, and it must not
    be seconds during which the event loop cannot answer anything else.
    """
    report = ops.doctor(_album(album), limit_slow=max(0, min(limit_slow, 500)))
    return report
