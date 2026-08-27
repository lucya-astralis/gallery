"""Read-only image metadata.

The configurator never writes into a photo. Two reasons: this library is
almost entirely PNG and BMP, where there is no dependable metadata container
to write into (BMP has none at all), and rewriting an original to store a
caption is a bad trade for a config tool. So EXIF is presented as an
inspector, and the one thing that *is* editable per photo -- tags -- lives in
the `.tags` sidecar the gallery's scanner already reads.
"""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import ExifTags, Image

# What to surface, in the order a photographer reads it. Everything else in
# the EXIF block is still returned under `raw`.
INTERESTING = [
    ("Make", "Camera make"),
    ("Model", "Camera"),
    ("LensModel", "Lens"),
    ("DateTimeOriginal", "Taken"),
    ("ExposureTime", "Exposure"),
    ("FNumber", "Aperture"),
    ("ISOSpeedRatings", "ISO"),
    ("FocalLength", "Focal length"),
    ("FocalLengthIn35mmFilm", "Focal length (35mm)"),
    ("Software", "Software"),
    ("Artist", "Artist"),
    ("Copyright", "Copyright"),
    ("ImageDescription", "Description"),
]


def _coerce(value):
    """Flatten an EXIF value into something JSON can carry."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").replace("\x00", "").strip()
    if isinstance(value, tuple) and len(value) == 2 and all(
            isinstance(v, int) for v in value):
        return list(value)
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    if isinstance(value, (int, float, str)) or value is None:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _pretty(key: str, value) -> str:
    """Format one EXIF value the way a camera would print it."""
    if value is None or value == "":
        return ""
    if key == "ExposureTime":
        try:
            secs = float(value[0]) / float(value[1]) if isinstance(value, list) else float(value)
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            return str(value)
        if secs >= 1:
            return "%gs" % round(secs, 2)
        return "1/%d s" % round(1 / secs) if secs else str(value)
    if key == "FNumber":
        try:
            num = float(value[0]) / float(value[1]) if isinstance(value, list) else float(value)
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            return str(value)
        return "f/%g" % round(num, 1)
    if key in ("FocalLength", "FocalLengthIn35mmFilm"):
        try:
            num = float(value[0]) / float(value[1]) if isinstance(value, list) else float(value)
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            return str(value)
        return "%g mm" % round(num, 1)
    if key == "DateTimeOriginal" and isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value
    if isinstance(value, list):
        try:
            return str(Fraction(int(value[0]), int(value[1])))
        except (ValueError, ZeroDivisionError, IndexError):
            return ", ".join(str(v) for v in value)
    return str(value)


def read(path: Path) -> dict:
    """Everything worth showing about one photo file.

    Never raises on a broken or exotic file -- an unreadable image still gets
    its size and name back, so the UI can list it.
    """
    out: dict = {
        "width": None,
        "height": None,
        "format": None,
        "mode": None,
        "taken_at": None,
        "fields": [],   # [{key, label, value}] -- the readable summary
        "raw": {},      # every EXIF tag, for the full dump
        "gps": False,
        "error": None,
    }
    try:
        st = path.stat()
        out["size"] = st.st_size
        out["mtime"] = int(st.st_mtime)
    except OSError:
        out["size"] = None
        out["mtime"] = None

    try:
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            out["format"] = img.format
            out["mode"] = img.mode
            exif_raw = img.getexif()
            flat: dict = {}
            if exif_raw:
                for tag_id, value in exif_raw.items():
                    flat[ExifTags.TAGS.get(tag_id, str(tag_id))] = _coerce(value)
                try:
                    ifd = exif_raw.get_ifd(ExifTags.IFD.Exif)
                    for tag_id, value in (ifd or {}).items():
                        flat[ExifTags.TAGS.get(tag_id, str(tag_id))] = _coerce(value)
                except (AttributeError, KeyError, ValueError):
                    pass
                try:
                    gps = exif_raw.get_ifd(ExifTags.IFD.GPSInfo)
                    out["gps"] = bool(gps)
                except (AttributeError, KeyError, ValueError):
                    pass
            out["raw"] = flat
    except Exception as exc:  # a corrupt or unsupported file is data, not a crash
        out["error"] = str(exc)
        return out

    for key, label in INTERESTING:
        if key in out["raw"]:
            text = _pretty(key, out["raw"][key])
            if text:
                out["fields"].append({"key": key, "label": label, "value": text})

    for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        value = out["raw"].get(key)
        if isinstance(value, str):
            try:
                out["taken_at"] = datetime.strptime(
                    value, "%Y:%m:%d %H:%M:%S").isoformat()
                break
            except ValueError:
                continue
    return out
