import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image, ExifTags, UnidentifiedImageError

from . import db

log = logging.getLogger("scanner")

STRIP_GPS = os.environ.get("STRIP_GPS", "1") not in ("0", "false", "False", "")
GPS_IFD_TAG = 0x8825

# Showcase marker: prefix character used to flag featured items.
#   - A *photo* whose filename (without extension) starts with the marker
#     is a showcase photo (featured in the welcome CRT, in /api/showcase
#     and in its own album's "featured" strip).
#   - An *album* whose folder name starts with the marker is a showcase
#     album (surfaced in the dedicated section on /albums).
# The two flags are INDEPENDENT — putting a photo inside a showcase
# album does NOT auto-feature it. Default marker is an underscore;
# configurable via the SHOWCASE_MARKER env var. Set to empty to disable.
SHOWCASE_MARKER = os.environ.get("SHOWCASE_MARKER", "_")

# Per-album metadata folder. Everything that describes an album rather than
# being one of its photos — album.cfg, the album_*.md descriptions, a custom
# title font — lives in `<album>/.album/`, keeping the photo folder itself
# nothing but photos. Never indexed: a stray image in here is metadata (a
# font specimen, a screenshot of the cfg), not a gallery photo.
ALBUM_META_DIR = ".album"


def is_meta_path(relp: Path) -> bool:
    """True for a path (relative to photos_dir) inside an album's metadata
    folder — see ALBUM_META_DIR."""
    return ALBUM_META_DIR in relp.parts


def is_showcase_photo(filename: str) -> bool:
    """True if a photo's filename marks it as a showcase item."""
    if not SHOWCASE_MARKER:
        return False
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.startswith(SHOWCASE_MARKER)


def is_showcase_album(album: str) -> bool:
    """True if an album's folder name marks it as a showcase album."""
    if not SHOWCASE_MARKER:
        return False
    return album.startswith(SHOWCASE_MARKER)

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    log.warning("pillow-heif not installed; HEIC/HEIF support disabled")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
JPEG_CONVERT_EXTS = {".heic", ".heif"}


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def needs_jpeg_conversion(p: Path) -> bool:
    return p.suffix.lower() in JPEG_CONVERT_EXTS


def _coerce(v):
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace").strip("\x00")
        except Exception:
            return repr(v)
    if isinstance(v, Fraction):
        return float(v)
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        try:
            if v.denominator == 0:
                return None
            return float(v.numerator) / float(v.denominator)
        except Exception:
            return str(v)
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _coerce(val) for k, val in v.items()}
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


# XMP namespaces. dc:description (Dublin Core) is the standard "description"
# field written by Lightroom, digiKam, exiftool (-XMP-dc:Description), etc.
_XMP_DC_NS = "http://purl.org/dc/elements/1.1/"
_XMP_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
# key under which the extracted value is stored in exif_json (read back by
# main._extract_description).
XMP_DESCRIPTION_KEY = "XMP:dc:Description"


def _xmp_description(img: Image.Image) -> str | None:
    """Read dc:description out of an image's XMP packet (XMP-dc:Description).

    XMP lives in its own metadata packet, separate from EXIF, so PIL exposes it
    via img.info (key "xmp" for JPEG/HEIF, "XML:com.adobe.xmp" for PNG) rather
    than getexif(). Handles the three forms dc:description appears in:
      - rdf:Alt / rdf:li language alternatives (prefers xml:lang="x-default")
      - a plain element text value
      - the compact form where it's an attribute on rdf:Description
    Returns None when there's no XMP or no dc:description.
    """
    raw = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    # Trim the xpacket/BOM preamble and any trailing NUL padding so the parser
    # sees a clean XML document.
    lt = raw.find("<")
    if lt > 0:
        raw = raw[lt:]
    raw = raw.split("\x00", 1)[0].strip()
    if not raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    desc_tag = f"{{{_XMP_DC_NS}}}description"
    li_tag = f"{{{_XMP_RDF_NS}}}li"

    def _clean(s):
        return s.strip() if s and s.strip() else None

    # element form: <dc:description>(<rdf:Alt><rdf:li>…</rdf:li>) | plain text
    for el in root.iter(desc_tag):
        lis = list(el.iter(li_tag))
        if lis:
            xdef = next(
                (li.text for li in lis if li.get(_XML_LANG) == "x-default" and _clean(li.text)),
                None,
            )
            if _clean(xdef):
                return xdef.strip()
            for li in lis:
                if _clean(li.text):
                    return li.text.strip()
        if _clean(el.text):
            return el.text.strip()

    # compact form: dc:description carried as an attribute on rdf:Description
    for el in root.iter():
        v = el.get(desc_tag)
        if _clean(v):
            return v.strip()
    return None


def extract_exif(img: Image.Image) -> tuple[dict, str | None]:
    out: dict = {}
    exif_raw = img.getexif()
    if exif_raw:
        for tag_id, value in exif_raw.items():
            name = ExifTags.TAGS.get(tag_id, str(tag_id))
            out[name] = _coerce(value)

        ifd = exif_raw.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else {}
        for tag_id, value in (ifd or {}).items():
            name = ExifTags.TAGS.get(tag_id, str(tag_id))
            out[name] = _coerce(value)

        gps_ifd = exif_raw.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(ExifTags, "IFD") else {}
        if gps_ifd:
            gps_out = {}
            for tag_id, value in gps_ifd.items():
                name = ExifTags.GPSTAGS.get(tag_id, str(tag_id))
                gps_out[name] = _coerce(value)
            out["GPSInfo"] = gps_out

    # dc:description from the XMP packet — read independently of EXIF, since a
    # file can carry XMP without any EXIF block at all.
    xmp_desc = _xmp_description(img)
    if xmp_desc:
        out[XMP_DESCRIPTION_KEY] = xmp_desc

    taken = None
    for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
        if key in out and isinstance(out[key], str):
            try:
                dt = datetime.strptime(out[key], "%Y:%m:%d %H:%M:%S")
                taken = dt.isoformat()
                break
            except ValueError:
                continue
    return out, taken


def _has_gps(exif) -> bool:
    if not exif:
        return False
    if GPS_IFD_TAG in exif:
        return True
    try:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        return bool(gps)
    except Exception:
        return False


def strip_gps_inplace(path: Path) -> bool:
    """Remove GPS EXIF from the original file in place. Returns True if modified."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not _has_gps(exif):
                return False
            if GPS_IFD_TAG in exif:
                del exif[GPS_IFD_TAG]
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
                if gps_ifd:
                    gps_ifd.clear()
            except Exception:
                pass
            fmt = img.format
            save_kwargs = {"exif": exif.tobytes()}
            if fmt == "JPEG":
                save_kwargs["quality"] = "keep"
            img.save(path, format=fmt, **save_kwargs)
        log.info("stripped GPS from %s", path.name)
        return True
    except (UnidentifiedImageError, OSError, ValueError) as e:
        log.warning("gps strip failed for %s: %s", path, e)
        return False


def make_thumbnail(src: Path, dst: Path, size: int) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            img.thumbnail((size, size), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (20, 20, 20))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=82, optimize=True, progressive=True)
        return True
    except (UnidentifiedImageError, OSError) as e:
        log.warning("thumb failed for %s: %s", src, e)
        return False


def make_full_jpeg(src: Path, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (20, 20, 20))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=92, optimize=True, progressive=True)
        return True
    except (UnidentifiedImageError, OSError) as e:
        log.warning("full jpeg conversion failed for %s: %s", src, e)
        return False


def ensure_full_jpeg(photos_dir: Path, fulls_dir: Path, rel_path: str) -> Path | None:
    src = photos_dir / rel_path
    if not src.exists() or not is_image(src):
        return None
    dst = (fulls_dir / rel_path).with_suffix(".jpg")
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    if make_full_jpeg(src, dst):
        return dst
    return None


def _read_sidecar_tags(image_path: Path) -> list[str]:
    sidecar = image_path.with_suffix(image_path.suffix + ".tags")
    if not sidecar.exists():
        return []
    try:
        raw = sidecar.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for chunk in raw.replace("\n", ",").split(","):
        t = chunk.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(t)
    return tags


def _sync_tags(image_id: int, tag_names: list[str]):
    c = db.conn()
    with db.lock():
        c.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
        for name in tag_names:
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            c.execute(
                "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
                (image_id, tag_id),
            )
        c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)")
        c.commit()


def index_image(photos_dir: Path, file: Path) -> bool:
    relp = file.relative_to(photos_dir)
    rel = relp.as_posix()
    parts = relp.parts
    if len(parts) < 2:
        return False
    if is_meta_path(relp):
        return False
    # `album` is the full relative directory path of the folder holding the
    # image (POSIX, e.g. "japan/tokyo"). This keeps the invariant
    # rel_path == album + "/" + filename and lets albums nest arbitrarily —
    # the album *tree* is derived from these paths (see main.py helpers).
    album = "/".join(parts[:-1])
    filename = parts[-1]
    stat = file.stat()
    mtime = stat.st_mtime
    sidecar = file.with_suffix(file.suffix + ".tags")
    sidecar_mtime = sidecar.stat().st_mtime if sidecar.exists() else 0.0
    effective_mtime = max(mtime, sidecar_mtime)

    c = db.conn()
    with db.lock():
        row = c.execute("SELECT id, mtime FROM images WHERE rel_path = ?", (rel,)).fetchone()
        if row and abs(row["mtime"] - effective_mtime) < 1.0:
            return False

    if STRIP_GPS and strip_gps_inplace(file):
        stat = file.stat()
        mtime = stat.st_mtime
        effective_mtime = max(mtime, sidecar_mtime)

    width = height = None
    exif: dict = {}
    taken = None
    try:
        with Image.open(file) as img:
            width, height = img.size
            try:
                exif, taken = extract_exif(img)
            except Exception as e:
                log.warning("exif failed for %s: %s", file, e)
    except (UnidentifiedImageError, OSError) as e:
        log.warning("open failed for %s: %s", file, e)
        return False

    # `is_showcase` (featured flag) is owned by main._recompute_featured(),
    # which derives it from each album's album.cfg (`featured = …`) with the
    # legacy filename-marker as a fallback. We deliberately leave the column
    # untouched here so a re-index never clobbers a computed flag: new rows
    # default to 0, existing rows keep their value.
    with db.lock():
        c.execute(
            """INSERT INTO images (album, filename, rel_path, mtime, size, width, height, exif_json, taken_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                 album=excluded.album, filename=excluded.filename, mtime=excluded.mtime,
                 size=excluded.size, width=excluded.width, height=excluded.height,
                 exif_json=excluded.exif_json, taken_at=excluded.taken_at""",
            (album, filename, rel, effective_mtime, stat.st_size, width, height, json.dumps(exif), taken),
        )
        image_id = c.execute("SELECT id FROM images WHERE rel_path = ?", (rel,)).fetchone()["id"]
        c.commit()
    _sync_tags(image_id, _read_sidecar_tags(file))
    return True


def remove_image(photos_dir: Path, file: Path):
    try:
        rel = file.relative_to(photos_dir).as_posix()
    except ValueError:
        return
    c = db.conn()
    with db.lock():
        c.execute("DELETE FROM images WHERE rel_path = ?", (rel,))
        c.commit()


def full_scan(photos_dir: Path, thumbs_dir: Path, thumb_size: int,
              previews_dir: Path | None = None, preview_size: int = 1600) -> dict:
    added = 0
    thumbed = 0
    previewed = 0
    seen: set[str] = set()
    if not photos_dir.exists():
        try:
            photos_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            log.warning("photos dir does not exist and is not writable: %s", photos_dir)
            return {"indexed": 0, "thumbnails": 0, "previews": 0, "removed": 0, "total_seen": 0}
    # Walk the whole tree so albums can nest (photos/japan/tokyo/img.jpg).
    # Files sitting directly in photos_dir (no album folder) are skipped.
    for file in sorted(photos_dir.rglob("*")):
        if not file.is_file() or not is_image(file):
            continue
        relp = file.relative_to(photos_dir)
        if len(relp.parts) < 2:
            continue
        if is_meta_path(relp):
            continue  # album metadata, not a photo — no index, no thumbs
        rel = relp.as_posix()
        seen.add(rel)
        if index_image(photos_dir, file):
            added += 1
        mtime = file.stat().st_mtime
        thumb_path = (thumbs_dir / rel).with_suffix(".jpg")
        if not thumb_path.exists() or thumb_path.stat().st_mtime < mtime:
            if make_thumbnail(file, thumb_path, thumb_size):
                thumbed += 1
        if previews_dir is not None:
            preview_path = (previews_dir / rel).with_suffix(".jpg")
            if not preview_path.exists() or preview_path.stat().st_mtime < mtime:
                if make_thumbnail(file, preview_path, preview_size):
                    previewed += 1

    c = db.conn()
    with db.lock():
        existing = [r["rel_path"] for r in c.execute("SELECT rel_path FROM images").fetchall()]
        removed = 0
        for rel in existing:
            if rel not in seen:
                c.execute("DELETE FROM images WHERE rel_path = ?", (rel,))
                removed += 1
        c.commit()
    return {
        "indexed": added,
        "thumbnails": thumbed,
        "previews": previewed,
        "removed": removed,
        "total_seen": len(seen),
    }


def ensure_thumb(photos_dir: Path, thumbs_dir: Path, rel_path: str, size: int) -> Path | None:
    src = photos_dir / rel_path
    if not src.exists() or not is_image(src):
        return None
    dst = (thumbs_dir / rel_path).with_suffix(".jpg")
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    if make_thumbnail(src, dst, size):
        return dst
    return None
