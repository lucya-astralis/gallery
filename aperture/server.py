"""The process. One codebase, two surfaces, two sockets.

    python -m aperture                    # both, per APERTURE_ROLE
    APERTURE_ROLE=public python -m aperture
    APERTURE_ROLE=console python -m aperture

"Everything in one" and "the console on its own port" are not in tension: one
process serves both, but each surface is its own ASGI app on its own listener,
and the port is where the two trust levels part. Nothing routes between them —
a request that arrives on the public socket cannot reach a console route,
because the public app has never heard of them.

The same image therefore covers both deployment shapes without a code change:

    role=all      one container, both ports          (the default)
    role=public   the exposed instance, photos:ro    ┐ two containers,
    role=console  the operator instance, internal    ┘ separate blast radius

Two uvicorn details this file exists to get right:

  * `Server.serve()` installs its own signal handlers, and two servers doing
    that fight over SIGINT/SIGTERM. Both are muted here and one handler sets
    `should_exit` on the pair, so Ctrl-C and `docker stop` bring the whole
    process down cleanly.
  * `--reload` is a uvicorn CLI feature and does not apply to a programmatic
    run. In development, start one role per process and use the CLI reloader
    there (see .claude/launch.json).
  * uvicorn installs its own logging handlers when it configures logging, and
    two Servers doing that put two handlers on the root logger — every line,
    including every access log line, printed twice. `log_config=None` leaves
    logging to us; the app already set it up in main.py.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn

from .runtime import ensure_dirs, settings

log = logging.getLogger("server")


def create_public_app():
    """The gallery. Read-only, CORS-enabled, the thing the world sees."""
    from .main import app
    return app


def _configs() -> list[uvicorn.Config]:
    out: list[uvicorn.Config] = []
    if settings.runs_public:
        out.append(uvicorn.Config(
            create_public_app(), host="0.0.0.0", port=settings.port,
            proxy_headers=True, log_config=None,
        ))
    if settings.runs_console:
        from .console.app import create_console_app
        out.append(uvicorn.Config(
            create_console_app(), host=settings.console_bind,
            port=settings.console_port, proxy_headers=True, log_config=None,
        ))
    return out


def _install_shutdown(servers: list[uvicorn.Server]) -> None:
    """One handler for the whole process instead of one per server."""
    def stop(*_):
        for s in servers:
            s.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            # Windows: the proactor loop has no add_signal_handler. The plain
            # signal module still delivers, which is enough for Ctrl-C.
            signal.signal(sig, stop)


async def _serve() -> None:
    ensure_dirs()
    configs = _configs()
    if not configs:
        raise SystemExit(
            "APERTURE_ROLE=%s with CONSOLE_ENABLED=%s opens no listener at all"
            % (settings.role, "1" if settings.console_enabled else "0"))

    servers = [uvicorn.Server(c) for c in configs]
    for s in servers:
        s.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    _install_shutdown(servers)

    log.info("role=%s  gallery=%s  console=%s  photos=%s",
             settings.role,
             ":%d" % settings.port if settings.runs_public else "off",
             "%s:%d" % (settings.console_bind, settings.console_port)
             if settings.runs_console else "off",
             settings.photos_dir)

    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:  # pragma: no cover - the normal Ctrl-C path
        pass


if __name__ == "__main__":
    main()
