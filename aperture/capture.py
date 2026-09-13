"""Capture facts: what a photo was shot with, read once when it is indexed.

The EXIF blob is a few KB of JSON per photo, and a search that parsed it per
row would re-read megabytes on every query. So the handful of facts a visitor
can search for -- camera, lens, focal length, aperture, ISO -- are pulled out
at index time and kept in columns of their own (see db.py and
scanner.index_image).

Each fact is read the way the pages already print it: the camera name is
stats.clean_device, which the album pages and the /stats chart use, and the
focal length prefers the 35 mm equivalent like the /stats focal chart. A name
or a number read off either page therefore finds its photos.
"""

from __future__ import annotations

from . import stats


def facts(exif: dict | None) -> dict:
    """{camera, lens, focal, aperture, iso} for one photo's EXIF; a fact the
    file does not carry is None."""
    exif = exif or {}
    lens = str(exif.get("LensModel") or "").replace("\x00", "").strip() or None
    focal = (stats.exif_number(exif.get("FocalLengthIn35mmFilm"))
             or stats.exif_number(exif.get("FocalLength")))
    fnum = stats.exif_number(exif.get("FNumber"))
    iso = exif.get("ISOSpeedRatings")
    # some writers store ISO as a list of one
    if isinstance(iso, (list, tuple)):
        iso = iso[0] if iso else None
    iso = stats.exif_number(iso)
    return {
        "camera": stats.clean_device(exif.get("Make"), exif.get("Model")),
        "lens": lens,
        "focal": round(focal) if focal else None,
        "aperture": round(fnum, 1) if fnum else None,
        "iso": round(iso) if iso else None,
    }
