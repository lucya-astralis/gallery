"""Operations, over HTTP.

The CLI could always run or pause the indexer and check the gallery for drift.
The console could not, because it shipped as a separate program that was not
allowed to assume the gallery existed. It ships in the same package now, so
that reason is gone and these routes are the other half of `aperture/ops.py`
and `aperture/reports.py`: the same functions the CLI renders to a terminal,
serialized instead. Every command the CLI has that means anything outside a
terminal has a route here — `menu`, `help` and `term` are about the terminal
itself and do not.

Three things this deliberately does NOT do.

It does not call the indexer. A scan request, and every other job that writes
what the indexer owns (rebuilding or pruning derivatives, recomputing featured
flags, stripping coordinates), is written to the control channel in
`data/control/` and picked up by the control loop, exactly as the CLI's scan
has always done it — even in the `all` role, where the console shares a
process with the thing it is asking. One code path, one writer, and the split
deployment (`APERTURE_ROLE=console` in its own container) keeps working with
no second implementation.

It does not write the index, the derivative trees or a photograph. Every
route here either reads, or writes a small JSON file into DATA_DIR — the one
exception being the console password, which is the door's own file.

And it takes no photo path for a job. A job is scoped to an album or to the
whole gallery; no request can name one file to be rewritten.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import control, db, i18n, ops, reports, vision
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


async def _body(request: Request) -> dict:
    """An optional JSON object: an empty body is `{}`, anything else that is
    not an object is a 400."""
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    return body


def _report(fn, *args, **kwargs):
    """Run one report and turn its misses into the HTTP answer they are."""
    try:
        return fn(*args, **kwargs)
    except UnknownAlbum as exc:
        raise HTTPException(404, "no such album: %r" % exc.args[0])
    except reports.NotFound as exc:
        hint = (" — did you mean: " + ", ".join(exc.suggestions[:5])) if exc.suggestions else ""
        raise HTTPException(404, str(exc) + hint)


# ----- state ------------------------------------------------------------
@router.get("/status")
def api_status():
    """What the CLI's `status` opens with: is the indexer alive, is it paused,
    what did the last scan do, what does the index hold, and where."""
    report = ops.status()
    report["paths"] = ops.paths()
    report["read_only"] = settings.console_read_only
    report["role"] = settings.role
    report["jobs"] = ops.JOBS
    report["auth"] = {"mode": "open" if security.open_access() else "password",
                      "may_run_open": security.may_run_open(),
                      "bind": settings.console_bind}
    return report


@router.get("/disk")
def api_disk():
    """What thumbnails, previews and conversions cost on disk. A walk of the
    generated trees, so a `def`: it runs in the threadpool."""
    return ops.disk_usage()


@router.get("/vision")
def api_vision():
    """Whether vision (aperture/vision.py) is on, installed and how far the
    scan has read the photos. Read-only: it is switched in the environment."""
    return vision.status(db.conn())


@router.get("/archive")
def api_archive():
    """The dashboard's statistics: span, largest albums, months, formats."""
    return reports.archive_report()


# ----- actions ----------------------------------------------------------
@router.post("/scan")
async def api_scan(request: Request):
    """Queue a scan. Returns the request id; poll /scan/{id} for the summary.

    Deliberately not a blocking call: a full pass over a large share on SMB
    takes minutes, and an HTTP request that waits for one is a request that
    times out somewhere between here and the browser.
    """
    _guard_actions()
    body = await _body(request)
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
            "scanning": bool((control.read_status() or {}).get("scanning"))}


@router.post("/pause")
async def api_pause(request: Request):
    """Suspend indexing. Persistent on purpose: it survives the restart it was
    very likely set for, and only resume lifts it."""
    _guard_actions()
    body = await _body(request)
    reason = str(body.get("reason") or "").strip()
    info = ops.pause(reason, by=BY)
    security.audit(request, "indexer paused", reason or "(no reason given)")
    return {"ok": True, "pause": info}


@router.post("/resume")
async def api_resume(request: Request):
    """Lift the pause; `scan: true` also queues a scan, like `resume --scan`."""
    _guard_actions()
    body = await _body(request)
    was_paused = ops.resume()
    req = ops.request_scan(by=BY) if body.get("scan") else None
    security.audit(request, "indexer resumed", "-",
                   extra={"was_paused": was_paused, "scan_requested": bool(req)})
    return {"ok": True, "was_paused": was_paused, "request": req}


@router.post("/jobs")
async def api_job(request: Request):
    """Queue one of ops.JOBS. Returns the job; poll /jobs/{id} for progress
    and, when it is done, its summary."""
    _guard_actions()
    body = await _body(request)
    kind = str(body.get("kind") or "")
    if kind not in ops.JOBS:
        raise HTTPException(400, "no such job: %r" % kind)
    try:
        job = ops.request_job(kind, album=body.get("album"),
                              rebuild_all=bool(body.get("all")), by=BY)
    except UnknownAlbum as exc:
        raise HTTPException(404, "no such album: %r" % exc.args[0])
    security.audit(request, "job requested (%s)" % kind,
                   job["params"].get("album") or "(whole gallery)",
                   extra={"job_id": job["id"], "params": job["params"]})
    status = ops.status()
    return {"ok": True, "job": job, "live": status["live"],
            "note": None if status["live"] else
                    "no indexer is listening — the job stays queued and "
                    "runs when one starts"}


@router.get("/jobs/{job_id}")
def api_job_state(job_id: str):
    """Queued, running (with its progress) or done (with its summary). An id
    that is not the shape of one is a 404: it never reaches the filesystem."""
    if not control.JOB_ID_RE.match(job_id):
        raise HTTPException(404, "no such job")
    return ops.job(job_id)


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


@router.get("/derivatives")
def api_derivatives(album: str | None = None, all: bool = False):
    """`thumbs`: what is missing, stale or left over. Reads only — the
    rebuild and the prune are jobs."""
    return _report(ops.derivatives_report, album, rebuild_all=all)


@router.get("/featured")
def api_featured(album: str | None = None):
    return _report(ops.featured_report, album)


@router.get("/cfg")
def api_cfg(album: str | None = None, gallery: bool = False):
    return _report(reports.cfg_report, album, gallery=gallery)


@router.get("/photo")
def api_photo(path: str):
    return _report(reports.photo_report, path)


@router.get("/albums")
def api_albums(album: str | None = None):
    """Every album with its counts, or — with `album` — that one in full."""
    if album:
        return _report(reports.album_report, album)
    return reports.albums_report()


@router.get("/trips")
def api_trips(album: str | None = None, lang: str = i18n.DEFAULT_LANG):
    """The configured trips, or — with `album` — its resolved dashboard."""
    if album:
        return _report(reports.trip_report, album, lang)
    return reports.trips_report()


@router.get("/welcome")
def api_welcome():
    return reports.welcome_report()


@router.get("/tags")
def api_tags(album: str | None = None, tag: str | None = None):
    """The vocabulary and the sidecar drift, or — with `tag` — its photos."""
    if tag:
        return _report(reports.tag_report, tag, album)
    return _report(reports.tags_report, album)


@router.get("/gps")
def api_gps(album: str | None = None):
    """Which originals still carry coordinates. Opens every original, so it
    is a `def` in the threadpool; the strip is a job."""
    return _report(ops.gps_audit, album)


@router.get("/search")
def api_search(q: str = "", album: str | None = None, limit: int = 200):
    return _report(reports.search_report, q, album, limit=max(1, min(limit, 1000)))


@router.get("/i18n")
def api_i18n():
    return reports.i18n_report()


# ----- export -----------------------------------------------------------
@router.get("/export/contents")
def api_export_contents():
    """`export --list`: what the archive would hold."""
    return reports.export_report()


@router.get("/export")
def api_export(request: Request):
    """The archive itself, as a download. Built into a spooled temp file —
    memory for a small gallery's config, /tmp (a tmpfs in the container) past
    that — and never written anywhere a later request could find it."""
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    result = reports.write_export(spool)
    size = spool.tell()
    spool.seek(0)
    name = "gallery-config-%s.tar.gz" % datetime.now().strftime("%Y%m%dT%H%M%S")
    security.audit(request, "config exported", "(every .album/ and .gallery/)",
                   extra={"files": result["files"], "bytes": size})

    def chunks():
        try:
            while True:
                block = spool.read(64 * 1024)
                if not block:
                    break
                yield block
        finally:
            spool.close()

    return StreamingResponse(chunks(), media_type="application/gzip", headers={
        "Content-Disposition": 'attachment; filename="%s"' % name,
        "Content-Length": str(size),
    })


# ----- the password -----------------------------------------------------
@router.post("/password")
async def api_password_set(request: Request):
    """`passwd`: set or change the console password. Where one is set it has
    to be given again — a session is not the password. Changing it ends every
    session, this one included, so the client goes to the door next."""
    _guard_actions()
    body = await _body(request)
    new = body.get("password")
    if not isinstance(new, str) or not new:
        raise HTTPException(400, "expected a `password`")
    if security.password_is_set():
        security.check_password(request, str(body.get("current") or ""), status=403)
    security.audit(request, "console password set", "data/console/credentials",
                   extra={"had_one": security.password_is_set()})
    security.set_password(new)        # ValueError (too short) -> 400
    return {"ok": True, "signed_out": True}


@router.delete("/password")
async def api_password_clear(request: Request):
    """`passwd --clear`, where it is safe: only on a bind the console is
    allowed to run open on. Anywhere else it would stand open to the network
    until the next start and then refuse to start at all."""
    _guard_actions()
    body = await _body(request)
    if not security.password_is_set():
        raise HTTPException(409, "no password is set")
    if not security.may_run_open():
        raise HTTPException(409, "this console listens on %s — without a password it "
                                 "would not be allowed to start; set a new one instead"
                                 % settings.console_bind)
    security.check_password(request, str(body.get("current") or ""), status=403)
    security.audit(request, "console password cleared", "data/console/credentials")
    security.clear_password()
    return {"ok": True, "open": True}
