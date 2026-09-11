"""The template setup both surfaces share.

The gallery and the console each built a Jinja2Templates and registered their
own `static_url` global -- the same function, written twice, differing only in
which folder it stamped. This is that function once.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from fastapi.templating import Jinja2Templates

# The gallery's own static/ and templates/. The console has its own pair
# under aperture/console/.
WEB_DIR = Path(__file__).resolve().parent / "gallery"


def static_url(base_dir: Path, path: str) -> str:
    """`/static/<path>` stamped with the file's mtime as `?v=`.

    Otherwise a browser keeps serving the stylesheet and script it cached
    before an update, and the page silently runs last week's code against
    this week's server. A missing file gets the bare path rather than an
    error: the 404 it produces is the more useful signal.
    """
    try:
        stamp = int((Path(base_dir) / "static" / path).stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={stamp}"


def make_templates(base_dir: Path, context_processors=()) -> Jinja2Templates:
    """`<base_dir>/templates`, with `static_url` bound to `<base_dir>/static`."""
    templates = Jinja2Templates(directory=str(Path(base_dir) / "templates"),
                                context_processors=list(context_processors))
    templates.env.globals["static_url"] = partial(static_url, base_dir)
    return templates
