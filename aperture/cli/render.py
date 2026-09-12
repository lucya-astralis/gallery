"""How every command writes: the short names, the frame, the system block.

The vocabulary itself lives in aperture/termui.py -- colour, rules, meters,
the masthead. These are the names a command body says, plus the two blocks
that more than one command renders the same way.
"""

import json
import sys
import time

from .. import brand, ops, termui as ui


# ----- output helpers ---------------------------------------------------
# The Windows console defaults to cp1252, which cannot print the JP strings
# or the box glyphs below — force UTF-8 rather than crash mid-report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# The report vocabulary lives in aperture/termui.py (colour, rules, meters, the
# masthead); these are just the short names the command bodies use.
out = ui.out


kv = ui.kv


head = ui.head


hint = ui.hint


def dump(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False))


def fail(msg: str) -> int:
    ui.error(f"error: {msg}")
    return 2


# ----- commands ---------------------------------------------------------
def _screen(right: str = ""):
    """Every full-page view gets the same frame: app name left, what you are
    looking at right."""
    return ui.screen(brand.PRODUCT, right)


def _render_system(st: dict | None, live: bool, pause: dict | None) -> None:
    """The server / indexer / scan / watcher block, shared by `status` and
    the dashboard."""
    if live:
        # The heartbeat age is shown even when it is fresh: a server that was
        # hard-killed (SIGKILL, OOM) leaves its status file behind, and a
        # growing age here is the first sign of that.
        kv("server", f"{ui.state('running')} · pid {st.get('pid')} · "
                     f"up {ops.dur(time.time() - (st.get('started_at') or time.time()))} · "
                     f"heartbeat {ops.ago(st.get('heartbeat'))}")
    elif st:
        kv("server", f"{ui.state('NOT running', 'bad')} · last heartbeat "
                     f"{ops.ago(st.get('heartbeat'))} (stale status file)")
    else:
        kv("server", f"{ui.state('NOT running', 'idle')} "
                     f"(no status file — never started, or a clean shutdown)")

    if pause:
        since = f" since {ops.stamp(pause.get('since'))}" if pause.get("since") else ""
        reason = f" · {pause['reason']}" if pause.get("reason") else ""
        kv("indexer", f"{ui.state('PAUSED', 'warn')}{since}{reason}")
    else:
        kv("indexer", ui.state("running"))

    if live:
        if st.get("scanning"):
            kv("scan", f"{ui.state('RUNNING', 'warn')} ({st.get('scan_trigger')}) · "
                       f"started {ops.ago(st.get('scan_started_at'))}")
        else:
            last = st.get("last_scan") or {}
            if last:
                res = last.get("result") or {}
                # only what actually happened — a row of zeroes says nothing
                # and pushes the interesting part off the line
                bits = ", ".join(f"{res[k]} {k}" for k in
                                 ("indexed", "thumbnails", "previews", "removed", "failed")
                                 if res.get(k)) or "no changes"
                scope = f" [{last['album']}]" if last.get("album") else ""
                err = f" · {ui.state('ERROR ' + str(last['error']), 'bad')}" if last.get("error") else ""
                kv("scan", f"idle · last {last.get('trigger')}{scope} {ops.ago(last.get('finished_at'))} "
                           f"in {ops.dur(last.get('seconds'))} → {bits}{err}")
            else:
                kv("scan", "idle · no scan in this process yet")
        w = st.get("watcher") or {}
        queued = w.get("pending", 0)
        kv("watcher", f"{'on' if w.get('enabled') else 'off'} · "
                      f"{ui.state('running') if w.get('running') else ui.state('not running', 'bad')} · "
                      f"{ui.state(f'{queued} event(s) queued', 'warn' if queued else 'idle')}")
        pend = st.get("pending_request")
        if pend:
            kv("queued", f"scan request {pend.get('id')} waiting ({ops.ago(pend.get('requested_at'))})")
