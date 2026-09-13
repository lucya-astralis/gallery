"""Pretty links: /<name> -> an album or a photo. What a link is, where the
list lives and why it redirects the way it does: aperture/links.py.

A router of its own because of where it has to sit. `/{slug}` matches ANY
one-segment path, `/api` and `/stats` included, so it is included last in
gallery/app.py: every real route gets the request first, and this only ever
sees a path nothing else claimed — which, before it existed, was the 404 page.
It still is, for a name that is not a link.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from .. import links

router = APIRouter()


@router.get("/{slug}", include_in_schema=False)
def follow_link(slug: str):
    destination = links.resolve(slug)
    if destination is None:
        raise HTTPException(404, "not found")
    # 302 and no-store, never 301: a permanent redirect is kept by the browser
    # for good, and a link is meant to be re-pointed from the console.
    return RedirectResponse(destination, status_code=302,
                            headers={"Cache-Control": "no-store"})
