"""Pretty links: /s/<name> -> an album or a photo. What a link is, where the
list lives and why it redirects the way it does: aperture/links.py.

Everything under `/s/` belongs to links and nothing else is ever routed there,
so this router can sit anywhere in gallery/app.py and a page the gallery gains
later can never collide with a link somebody has already printed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from .. import links

router = APIRouter()


@router.get(links.PREFIX + "{slug}", include_in_schema=False)
def follow_link(slug: str):
    destination = links.resolve(slug)
    if destination is None:
        raise HTTPException(404, "not found")
    # 302 and no-store, never 301: a permanent redirect is kept by the browser
    # for good, and a link is meant to be re-pointed from the console.
    return RedirectResponse(destination, status_code=302,
                            headers={"Cache-Control": "no-store"})
