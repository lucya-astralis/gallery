"""Who may talk to the console, and what a write leaves behind.

Until 1.0 the answer was "whoever reaches the port", which was defensible only
because the port belonged to a program that shipped separately and was
expected to live on a laptop. It is now the one write path into the photo
tree, so it gets a door.

Four things live here, in the order a request meets them:

    assert_safe_binding()   at startup, before a socket is opened
    guard (middleware)      is this session allowed to make this request
    login / logout          the credential check itself
    audit()                 what a write leaves in the record

**The binding rule.** A password is what protects the console; the port is
only where it is. So:

    password set                    -> login required, wherever it listens
    no password, loopback bind      -> open, with a warning on every start
    no password, anything else      -> the process REFUSES to start,
                                       unless CONSOLE_ALLOW_OPEN=1 says the
                                       network boundary is handled elsewhere

The refusal is the point of the whole module. Inside a container the console
must bind 0.0.0.0 — a container's own loopback is reachable from nowhere — so
"is the bind loopback" cannot be the test on its own, and the escape hatch is
explicit and loud rather than implied.

**No new dependencies.** scrypt is in hashlib, tokens are in secrets, and the
session table is a dict. A password store that needs a library is a password
store that will be out of date.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..runtime import settings

log = logging.getLogger("console.security")

# ----- knobs ------------------------------------------------------------
COOKIE = "aperture_console"
CSRF_HEADER = "x-aperture-csrf"
# Idle first, then a hard ceiling: a session left open in a tab all week is
# not a session, and re-typing a password once a day is not a hardship.
IDLE_TIMEOUT = 30 * 60
ABSOLUTE_TIMEOUT = 12 * 60 * 60
# scrypt at these parameters costs ~16 MB and ~100 ms. Stored WITH the hash,
# so raising them later does not invalidate an existing password.
SCRYPT = {"n": 16384, "r": 8, "p": 1, "dklen": 32}
# Three free tries, then the wait doubles. Capped so a locked-out operator is
# never locked out for longer than a coffee.
FREE_TRIES = 3
MAX_BACKOFF = 300.0

# Everything else needs a session.
OPEN_PATHS = frozenset({"/login", "/api/session", "/api/health",
                        # the look: the door wears the site's colour too
                        "/theme.css", "/bg/nova-wide-16-9.svg", "/bg/nova-square.svg"})
OPEN_PREFIXES = ("/static/",)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


# ----- the credential ---------------------------------------------------
def _cred_path() -> Path:
    return settings.console_dir / "credentials"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return {"algo": "scrypt", **{k: v for k, v in SCRYPT.items() if k != "dklen"},
            "dklen": SCRYPT["dklen"], "salt": _b64(salt), "hash": _b64(digest),
            "set_at": int(time.time())}


def set_password(password: str) -> Path:
    """Write the credential file, readable by its owner only.

    The mode matters on the Linux host this runs on; on Windows `chmod` is
    close to a no-op, which is one more reason the file lives under DATA_DIR
    and not in the photo tree — see runtime.py.
    """
    if len(password) < 8:
        raise ValueError("a console password needs at least 8 characters")
    path = _cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(hash_password(password), indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp, path)
    _sessions.clear()          # a new password ends every session on the old one
    return path


def clear_password() -> bool:
    path = _cred_path()
    existed = path.is_file()
    path.unlink(missing_ok=True)
    _sessions.clear()
    return existed


def _stored() -> dict | None:
    try:
        data = json.loads(_cred_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("hash") else None


def password_is_set() -> bool:
    return _stored() is not None


def verify_password(password: str) -> bool:
    rec = _stored()
    if rec is None:
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(rec["salt"]),
            n=int(rec["n"]), r=int(rec["r"]), p=int(rec["p"]),
            dklen=int(rec.get("dklen", 32)),
        )
    except (KeyError, ValueError, TypeError):
        return False
    return secrets.compare_digest(_b64(digest), str(rec["hash"]))


# ----- startup ----------------------------------------------------------
def _is_loopback(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_safe_binding(bind: str | None = None, port: int | None = None) -> None:
    """Called before the console's socket is opened. Raises SystemExit rather
    than logging, because a warning nobody reads is how an unauthenticated
    write API ends up on a network.

    The address is an argument (defaulting to the configured one) so the rule
    can be checked against a host without reconfiguring the process — which is
    what the tests do, and what a `--check` would do later.
    """
    if password_is_set():
        return
    bind = settings.console_bind if bind is None else bind
    port = settings.console_port if port is None else port

    if _is_loopback(bind):
        log.warning(
            "console: NO PASSWORD SET - open to anything that reaches "
            "%s:%d. Set one with `python -m aperture.cli passwd`.", bind, port)
        return
    if settings.console_allow_open:
        log.warning(
            "console: NO PASSWORD SET and bound to %s:%d, running open because "
            "CONSOLE_ALLOW_OPEN=1. Whatever can reach that port can rewrite "
            "this gallery's configuration.", bind, port)
        return

    raise SystemExit(
        "\n"
        "  The console has no password and is not bound to loopback.\n"
        "  It is the only write path into the photo tree, so Aperture will\n"
        "  not open that socket.\n"
        "\n"
        "  Set a password (recommended):\n"
        "      docker compose run --rm aperture python -m aperture.cli passwd\n"
        "      python -m aperture.cli passwd          # without Docker\n"
        "\n"
        "  Or, if the network boundary is somewhere else - the port is only\n"
        "  published on 127.0.0.1, or a firewall covers it - say so:\n"
        "      CONSOLE_ALLOW_OPEN=1\n"
        "\n"
        "  Or turn the console off entirely:\n"
        "      CONSOLE_ENABLED=0\n"
        f"\n  (bind={settings.console_bind}:{settings.console_port})\n"
    )


def may_run_open(bind: str | None = None) -> bool:
    """Whether this console would be allowed to start without a password --
    the rule assert_safe_binding enforces, as a question. Clearing the
    password from the browser is only offered where the answer is yes: on any
    other bind it would open the console to the network until the next start,
    and then keep it from starting at all."""
    bind = settings.console_bind if bind is None else bind
    return _is_loopback(bind) or bool(settings.console_allow_open)


def open_access() -> bool:
    """True when the console is running without a password. The UI says so;
    the guard below lets everything through."""
    return not password_is_set()


# ----- sessions ---------------------------------------------------------
@dataclass
class Session:
    csrf: str
    created: float
    seen: float
    ip: str = ""

    def alive(self, now: float) -> bool:
        return (now - self.seen) < IDLE_TIMEOUT and (now - self.created) < ABSOLUTE_TIMEOUT


_sessions: dict[str, Session] = {}
# ip -> [failures, blocked_until]
_failures: dict[str, list] = {}


def reset() -> None:
    """Forget every session and every recorded failure. FOR TESTS, which need
    a door nobody has knocked on yet; nothing in the app calls it."""
    _sessions.clear()
    _failures.clear()


def _sweep(now: float) -> None:
    for sid in [s for s, sess in _sessions.items() if not sess.alive(now)]:
        _sessions.pop(sid, None)


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "?"


def current(request: Request) -> tuple[str, Session] | None:
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    now = time.time()
    _sweep(now)
    sess = _sessions.get(sid)
    if sess is None:
        return None
    sess.seen = now
    return sid, sess


def still_signed_in(request: Request) -> bool:
    """Whether the request's session still stands -- WITHOUT counting as
    activity. current() refreshes `seen`; a live stream that called it every
    second would keep an idle session alive forever, so the stream asks this
    instead and ends when the idle timeout, a sign-out or a new password
    has ended the session."""
    if open_access():
        return True
    sid = request.cookies.get(COOKIE)
    if not sid:
        return False
    _sweep(time.time())
    return sid in _sessions


def _retry_after(ip: str, now: float) -> float:
    record = _failures.get(ip)
    return max(0.0, record[1] - now) if record else 0.0


def note_failure(ip: str) -> None:
    now = time.time()
    record = _failures.setdefault(ip, [0, 0.0])
    record[0] += 1
    if record[0] > FREE_TRIES:
        record[1] = now + min(2.0 ** (record[0] - FREE_TRIES), MAX_BACKOFF)


def check_password(request: Request, password: str, *, status: int = 401) -> None:
    """The password, asked for with the door's own throttle. Signing in uses
    it, and so does anything that asks for the password again from inside a
    session (changing it, clearing it) -- a stolen session cookie must not be
    a way to brute-force the password it is standing behind.

    `status` is 403 for the second case: a 401 in the console means "signed
    out", and the client would send the operator to the door for a typo."""
    ip = _client_ip(request)
    now = time.time()
    wait = _retry_after(ip, now)
    if wait > 0:
        raise HTTPException(429, "too many attempts — try again in %d s" % round(wait),
                            headers={"Retry-After": str(int(wait) + 1)})
    if not verify_password(password):
        note_failure(ip)
        log.warning("console: wrong password from %s", ip)
        raise HTTPException(status, "wrong password")
    _failures.pop(ip, None)


def login(request: Request, password: str) -> tuple[str, Session]:
    check_password(request, password)
    ip = _client_ip(request)
    now = time.time()
    sid = secrets.token_urlsafe(32)
    sess = Session(csrf=secrets.token_urlsafe(32), created=now, seen=now, ip=ip)
    _sessions[sid] = sess
    log.info("console: session opened from %s", ip)
    return sid, sess


def logout(request: Request) -> None:
    sid = request.cookies.get(COOKIE)
    if sid:
        _sessions.pop(sid, None)


def issue_cookie(response, request: Request, sid: str) -> None:
    """Set the session cookie, `Secure` only where the browser would accept it.

    A `Secure` cookie is dropped on plain http to a LAN address, so forcing it
    would lock the operator out of exactly the deployment this is written for.
    Instead it follows the scheme, and a plaintext non-loopback session gets a
    warning in the log — the honest answer being a tunnel, not a cookie flag.
    """
    https = request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    host = request.url.hostname or ""
    if not https and not _is_loopback(host):
        log.warning("console: session for %s over plain http - the cookie "
                    "travels in clear; prefer a VPN or an SSH tunnel", host)
    response.set_cookie(COOKIE, sid, httponly=True, samesite="strict",
                        secure=https, path="/", max_age=ABSOLUTE_TIMEOUT)


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ----- the guard --------------------------------------------------------
def _is_open_path(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def _same_origin(request: Request) -> bool:
    """Origin is sent by every browser on a state-changing request. When it is
    there it must be ours; when it is absent the request did not come from a
    browser's cross-site path, and the CSRF token still has to match."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") == str(request.base_url).rstrip("/")


async def guard(request: Request, call_next):
    """Authentication and CSRF, in one place, for every route on this app."""
    path = request.url.path

    if open_access():
        # No password configured. assert_safe_binding() has already decided
        # this is an acceptable place to run open, so the door stands open —
        # but a browser is still not allowed to be steered here from another
        # site, which is what the origin check below is for.
        if request.method not in SAFE_METHODS and not _same_origin(request):
            return _refuse(request, 403, "cross-site request refused")
        return await call_next(request)

    if _is_open_path(path):
        return await call_next(request)

    found = current(request)
    if found is None:
        return _refuse(request, 401, "sign in to the console")

    if request.method not in SAFE_METHODS:
        if not _same_origin(request):
            return _refuse(request, 403, "cross-site request refused")
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, found[1].csrf):
            return _refuse(request, 403, "missing or stale CSRF token — reload the page")

    return await call_next(request)


def _refuse(request: Request, status: int, detail: str):
    """HTML routes get the login page, API routes get JSON. A fetch() that
    receives a login form instead of an error is how a UI ends up rendering a
    stylesheet into a table."""
    wants_html = "text/html" in request.headers.get("accept", "") \
        and not request.url.path.startswith("/api/")
    if wants_html and status == 401:
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": detail}, status_code=status)


# ----- the record -------------------------------------------------------
def _audit_path() -> Path:
    return settings.console_dir / "audit.log"


def sha256_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# How much of the tail recent() reads. 64 KB is some 400 writes -- more than
# any dashboard shows, and a bounded read whatever the log grew to.
AUDIT_TAIL_BYTES = 64 * 1024


def audit(request: Request, action: str, target: str, *,
          before: str | None = None, after: str | None = None,
          extra: dict | None = None) -> None:
    """One JSON line per write: who, when, what file, and what its content
    hashed to before and after. Never the content itself — the backups next to
    it are for that, and an audit log that quotes a config file is a config
    file with a second, unmanaged copy.
    """
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ip": _client_ip(request),
        "action": action,
        "target": target,
        "before": before,
        "after": after,
    }
    found = current(request)
    if found is not None:
        record["session"] = found[0][:8]
    if extra:
        record.update(extra)
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:                     # pragma: no cover - disk trouble
        log.warning("console: could not write the audit log: %s", exc)


def recent(limit: int = 40) -> list[dict]:
    """The last writes, newest first. The console shows them as "what changed
    here"; until now the log was written on every save and read by nobody.

    Only the tail is read -- the file grows for the life of the deployment and
    a dashboard has no business parsing all of it. A line that is not JSON is
    skipped rather than raised on: this is a report, and half a log is worth
    more than an error page.
    """
    path = _audit_path()
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - AUDIT_TAIL_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines()[-limit * 2:]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    out.reverse()
    return out[:limit]


def state(request: Request) -> dict:
    """What the UI needs to know about its own session."""
    found = current(request)
    return {
        "auth": "open" if open_access() else "password",
        "signed_in": bool(found) or open_access(),
        "csrf": found[1].csrf if found else "",
        "idle_timeout": IDLE_TIMEOUT,
    }
