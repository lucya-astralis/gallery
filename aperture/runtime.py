"""One place that reads the environment.

Before the merge there were two: the gallery read `os.environ` at the top of
main.py, the configurator had its own `_default()` helper with its own
fallbacks, and the same variable could mean two different directories
depending on which process you asked. That is fine for two programs and wrong
for one.

Everything below is resolved once, at import, and handed out as `settings`.
The paths are absolute from that moment on, so nothing downstream has to care
what the working directory was.

The one rule worth stating: `photos/` is the operator's input and `data/` is
ours. Anything this software generates or is trusted with — the index, the
control channel, the console's credentials, its backups — lives under
DATA_DIR, never in the photo tree. The gallery serves files out of
`photos/.gallery/`, so a secret placed there would be a secret published.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path

# The roles a process can run in. See server.py — this is what decides which
# listeners open, and it is also what decides whether this process owns the
# indexer.
ROLES = ("all", "public", "console")


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, "").strip() or default).resolve()


@dataclass(frozen=True)
class Settings:
    # ----- what this process is ----------------------------------------
    role: str

    # ----- where things live -------------------------------------------
    photos_dir: Path
    thumbs_dir: Path
    previews_dir: Path
    fulls_dir: Path
    data_dir: Path

    # ----- the public surface ------------------------------------------
    port: int
    thumb_size: int
    preview_size: int
    scan_interval: int
    enable_watcher: bool
    hide_gps: bool
    strip_gps: bool
    public_base_url: str

    # ----- the console surface -----------------------------------------
    console_enabled: bool
    console_bind: str
    console_port: int
    console_read_only: bool
    console_allow_open: bool
    console_backups: int
    console_max_upload: int

    # ----- the network in front of both --------------------------------
    # Which peers may set X-Forwarded-For / X-Forwarded-Proto. Only those
    # addresses are believed about the client behind them; everyone else's
    # headers are ignored. It matters since 1.0 because the console's login
    # throttle and audit log key on the client address: with the proxy
    # untrusted, every visitor looks like the proxy, and one wrong password
    # rate-limits everybody.
    forwarded_allow_ips: str

    @property
    def runs_public(self) -> bool:
        return self.role in ("all", "public")

    @property
    def runs_console(self) -> bool:
        return self.console_enabled and self.role in ("all", "console")

    @property
    def owns_indexer(self) -> bool:
        """Whether this process runs the scanner, the watcher and the control
        loop. Tied to the public role rather than to an app's lifespan: in
        every deployment shape there is then exactly one writer on the SQLite
        file, and a console-only process is a reader that asks for scans
        through the control channel like the CLI does."""
        return self.runs_public

    # ----- derived directories, ours and never the operator's -----------
    @property
    def console_dir(self) -> Path:
        """Credentials, sessions, the audit log and the rolling backups of
        every file the console overwrites."""
        return self.data_dir / "console"

    @property
    def backup_dir(self) -> Path:
        return self.console_dir / "backups"

    @property
    def gallery_db(self) -> Path:
        return self.data_dir / "gallery.db"


def load() -> Settings:
    role = (os.environ.get("APERTURE_ROLE") or "all").strip().lower()
    if role not in ROLES:
        raise SystemExit(
            "APERTURE_ROLE=%r is not one of %s" % (role, ", ".join(ROLES)))

    previews = _path("PREVIEWS_DIR", "./previews")
    return Settings(
        role=role,
        photos_dir=_path("PHOTOS_DIR", "./photos"),
        thumbs_dir=_path("THUMBS_DIR", "./thumbnails"),
        previews_dir=previews,
        fulls_dir=_path("FULLS_DIR", str(previews / "_full")),
        data_dir=_path("DATA_DIR", "./data"),

        port=_int("PORT", 8000),
        thumb_size=_int("THUMB_SIZE", 480),
        preview_size=_int("PREVIEW_SIZE", 1600),
        # Default 300s (5 min): the file watcher gets no events over SMB/CIFS
        # or NFS, so a periodic full scan is what picks up newly added albums
        # there. 0 disables it.
        scan_interval=_int("SCAN_INTERVAL", 300),
        enable_watcher=_flag("ENABLE_WATCHER"),
        hide_gps=_flag("HIDE_GPS"),
        strip_gps=_flag("STRIP_GPS"),
        public_base_url=(os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/"),

        console_enabled=_flag("CONSOLE_ENABLED"),
        # Loopback by default. A different address is a deliberate act and
        # carries a condition — see console.security.assert_safe_binding().
        console_bind=(os.environ.get("CONSOLE_BIND") or "127.0.0.1").strip(),
        console_port=_int("CONSOLE_PORT", 8090),
        console_read_only=_flag("READ_ONLY", "0"),
        # The one startup rule an operator can wave away, so it belongs
        # with the rest of the configuration rather than being read out
        # of the environment where it is enforced -- see
        # console.security.assert_safe_binding().
        console_allow_open=_flag("CONSOLE_ALLOW_OPEN", "0"),
        console_backups=_int("BACKUPS", 20),
        console_max_upload=_int("MAX_UPLOAD_MB", 8) * 1024 * 1024,

        # uvicorn's own default, made explicit and configurable. Set it to the
        # reverse proxy's address (or its network, comma-separated) — never
        # `*` on a port something else can reach directly, or any client can
        # claim any address.
        forwarded_allow_ips=(os.environ.get("FORWARDED_ALLOW_IPS") or "127.0.0.1").strip(),
    )


settings = load()


def _owner(path: Path) -> str:
    """Who owns a directory, as a person reading a log needs it."""
    try:
        st = path.stat()
    except OSError:
        return "it does not exist"
    return "uid %s, gid %s, mode %s" % (
        getattr(st, "st_uid", "?"), getattr(st, "st_gid", "?"), oct(st.st_mode & 0o777))


def _refuse_dir(directory: Path, exc: OSError) -> SystemExit:
    """The one message this failure is worth.

    A bind mount carries the HOST's ownership into the container, so the
    chown in the Dockerfile does nothing for a mounted path -- which is the
    trap, and the reason the useful facts (who this process is, who owns the
    directory) belong in the refusal rather than in a traceback.
    """
    me = getattr(os, "getuid", None)
    who = ("uid %d, gid %d" % (os.getuid(), os.getgid())) if me else "this user"
    return SystemExit(
        "\n"
        "  Aperture cannot create %s\n"
        "      %s\n"
        "\n"
        "  This process runs as %s.\n"
        "  Its parent %s belongs to %s.\n"
        "\n"
        "  It writes into the data, thumbnail and preview directories. On a\n"
        "  bind mount the HOST's ownership is what counts -- the image's own\n"
        "  chown does not reach a mounted path -- so on the host:\n"
        "\n"
        "      sudo chown -R %s data thumbnails previews\n"
        "\n"
        "  On a CIFS/SMB mount there are no real owners: set uid= and gid= in\n"
        "  the mount options instead. See DEPLOY-LINUX.md.\n"
        % (directory, exc, who, directory.parent, _owner(directory.parent),
           ("%d:%d" % (os.getuid(), os.getgid())) if me else "<uid>:<gid>"))


def ensure_dirs() -> None:
    """Create what we own. `photos/` is only attempted, never required: in the
    public role it is mounted read-only on purpose, and a share that is not
    there yet is a reason to serve an empty gallery, not to refuse to start.

    The other four ARE required -- without them there is no index, no control
    channel and nowhere to put a thumbnail -- so a failure there stops the
    process. It stops with an instruction rather than a stack trace: this is
    the first thing a fresh deployment gets wrong, and it gets it wrong on a
    machine whose logs are the only thing anyone can see."""
    try:
        settings.photos_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        pass
    for d in (settings.thumbs_dir, settings.previews_dir, settings.fulls_dir,
              settings.data_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _refuse_dir(d, exc) from None


@contextmanager
def override(**changes):
    """Settings, changed for the length of a `with` block. FOR TESTS.

    They are frozen because configuration does not change while a process
    runs, and every module holds this one instance rather than a copy, so a
    test that needs the other value of a flag cannot simply build a second
    Settings. It says so here instead of reaching past the freeze itself,
    which is what several tests used to do one object.__setattr__ at a time --
    and which left the value changed for the rest of the session whenever the
    test failed before its cleanup ran.
    """
    unknown = set(changes) - {f.name for f in fields(Settings)}
    if unknown:
        raise TypeError("no such setting: %s" % ", ".join(sorted(unknown)))
    before = {name: getattr(settings, name) for name in changes}
    for name, value in changes.items():
        object.__setattr__(settings, name, value)
    try:
        yield settings
    finally:
        for name, value in before.items():
            object.__setattr__(settings, name, value)
