"""gzip for the responses that benefit from it, and only those.

The gallery is served by uvicorn directly — a reverse proxy in front of it is
optional (see docker-compose.yml / DEPLOY-LINUX.md), so compression cannot be
assumed to happen anywhere else. Without it the first visit ships every byte
raw, and the text this site sends is extremely compressible:

    style.css           234 KB  ->  64 KB   (27%)
    app.js              103 KB  ->  32 KB   (31%)
    /album/japan_2026   249 KB  ->  20 KB   ( 8%)
    /stats               49 KB  ->   6 KB   (12%)

Why not `starlette.middleware.gzip.GZipMiddleware`: it compresses every
response over `minimum_size` regardless of type, and the dominant traffic here
is JPEG. Re-compressing a 480 KB photo costs real CPU for ~0% gain, on every
single tile of every grid. So the one thing this adds over Starlette's version
is a content-type gate — everything else follows the same ASGI shape.

A response that already carries `Content-Encoding` is passed through
untouched, so putting a compressing proxy in front of this stays safe.
"""

import gzip
import io

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Worth compressing. Matched against the bare media type, lower-cased.
# Everything else — JPEG, PNG, WebP, woff2, mp4 — is already compressed and
# only loses CPU here.
COMPRESSIBLE_PREFIXES = ("text/",)
COMPRESSIBLE_TYPES = frozenset({
    "application/json",
    "application/javascript",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/manifest+json",
    "image/svg+xml",
})

# Below this a gzip frame's own overhead eats the saving, and the extra
# round of work is not worth it for a 200-byte JSON error. (An 87-byte theme
# sheet gzips to 102 bytes — bigger than it started.)
MINIMUM_SIZE = 600
# 6 is zlib's default and the knee of the curve: level 9 costs noticeably more
# CPU for ~1% smaller output on this kind of text.
COMPRESS_LEVEL = 6
# How much of a response body may be held in memory to compress it in one
# piece. Every compressible thing this app sends is well under it (the biggest
# page is ~250 KB); anything past it falls back to chunk-by-chunk streaming
# rather than growing without bound.
MAX_BUFFER = 2 * 1024 * 1024


def _compressible(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    if not media:
        return False
    return media in COMPRESSIBLE_TYPES or media.startswith(COMPRESSIBLE_PREFIXES)


class CompressMiddleware:
    """gzip the text responses of an ASGI app when the client asks for it."""

    def __init__(self, app: ASGIApp, minimum_size: int = MINIMUM_SIZE,
                 compresslevel: int = COMPRESS_LEVEL) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or "gzip" not in Headers(scope=scope).get("accept-encoding", ""):
            await self.app(scope, receive, send)
            return
        await _Responder(self.app, self.minimum_size, self.compresslevel)(scope, receive, send)


class _Responder:
    """One response.

    `http.response.start` is held back until the body is known, because
    whether the body gets compressed decides what the headers have to say.
    The body itself is accumulated rather than compressed chunk by chunk: the
    security-headers middleware in front of the routes turns EVERY response
    into a streaming one, so a per-chunk decision would never see a size and
    would gzip an 87-byte stylesheet into 102 bytes. Collecting first gives
    the real length back — which is also what lets Content-Length survive, so
    the client still gets a progress bar. Only a body past MAX_BUFFER (nothing
    this app sends) falls back to streaming.
    """

    def __init__(self, app: ASGIApp, minimum_size: int, compresslevel: int) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.send: Send = None  # type: ignore[assignment]
        self.start: Message = {}
        self.passthrough = False   # decided at http.response.start
        self.started = False       # has the start message gone out yet
        self.chunks: list[bytes] = []
        self.buffered = 0
        self.buffer: io.BytesIO | None = None   # streaming mode only
        self.gz: gzip.GzipFile | None = None    # streaming mode only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        try:
            await self.app(scope, receive, self._send)
        finally:
            if self.gz is not None:
                self.gz.close()
            if self.buffer is not None:
                self.buffer.close()

    # ----- helpers ------------------------------------------------------
    def _headers(self) -> MutableHeaders:
        return MutableHeaders(raw=self.start["headers"])

    async def _flush_plain(self, message: Message) -> None:
        """Give up on compressing and emit what was held back, unchanged."""
        if not self.started:
            self.started = True
            await self.send(self.start)
            for chunk in self.chunks:
                await self.send({"type": "http.response.body", "body": chunk,
                                 "more_body": True})
            self.chunks.clear()
        await self.send(message)

    def _begin_stream(self) -> None:
        headers = self._headers()
        headers["Content-Encoding"] = "gzip"
        headers.add_vary_header("Accept-Encoding")
        # The compressed length is not knowable up front, and a Content-Length
        # left over from the uncompressed body is a lie the client would cut
        # the response off at.
        del headers["Content-Length"]
        self.buffer = io.BytesIO()
        self.gz = gzip.GzipFile(mode="wb", fileobj=self.buffer,
                                compresslevel=self.compresslevel)

    async def _stream(self, body: bytes, more: bool) -> None:
        assert self.gz is not None and self.buffer is not None
        self.gz.write(body)
        if more:
            self.gz.flush()
        else:
            self.gz.close()
            self.gz = None
        chunk = self.buffer.getvalue()
        self.buffer.seek(0)
        self.buffer.truncate()
        await self.send({"type": "http.response.body", "body": chunk, "more_body": more})

    # ----- the ASGI send hook -------------------------------------------
    async def _send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = Headers(raw=message["headers"])
            self.passthrough = (
                "content-encoding" in headers
                or not _compressible(headers.get("content-type", ""))
            )
            self.start = message
            return  # held back until the body decides

        if message["type"] != "http.response.body":
            await self.send(message)
            return

        if self.passthrough:
            await self._flush_plain(message)
            return

        body = message.get("body", b"")
        more = message.get("more_body", False)

        if self.started:  # already streaming compressed
            await self._stream(body, more)
            return

        self.chunks.append(body)
        self.buffered += len(body)

        if more:
            if self.buffered <= MAX_BUFFER:
                return  # keep collecting
            # too big to hold: switch to chunk-by-chunk from here on
            self.started = True
            self._begin_stream()
            await self.send(self.start)
            collected, self.chunks = b"".join(self.chunks), []
            await self._stream(collected, True)
            return

        # end of the body: the whole thing is in hand
        self.started = True
        collected = b"".join(self.chunks)
        self.chunks.clear()
        if len(collected) < self.minimum_size:
            await self.send(self.start)
            await self.send({"type": "http.response.body", "body": collected,
                             "more_body": False})
            return
        self._begin_stream()
        assert self.gz is not None and self.buffer is not None
        self.gz.write(collected)
        self.gz.close()
        self.gz = None
        out = self.buffer.getvalue()
        headers = self._headers()
        headers["Content-Length"] = str(len(out))
        await self.send(self.start)
        await self.send({"type": "http.response.body", "body": out, "more_body": False})
