import hashlib
import json
import logging
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image, ExifTags, ImageOps

from . import brand, capture, db, marks
from . import schema
from .runtime import settings

log = logging.getLogger("scanner")

GPS_IFD_TAG = 0x8825


def is_meta_path(relp: Path) -> bool:
    """True for a path (relative to photos_dir) inside a metadata folder —
    an album's `.album/`, the gallery's own `.gallery/`, or a folder a NAS
    keeps for itself (`@eaDir`, `#recycle`, … — schema.SYSTEM_DIRS) at any
    depth. Never indexed: an image in any of them is metadata (a mark, a font
    specimen, the NAS's own thumbnail), not a gallery photo."""
    return any(d in relp.parts for d in schema.META_DIRS) or \
        any(schema.is_system_dir(part) for part in relp.parts)


def walk_photo_tree(base: Path) -> list[Path]:
    """Every file under `base`, sorted, without ever descending into a folder
    in schema.SYSTEM_DIRS.

    Pruned rather than filtered afterwards: a Synology keeps several
    thumbnails of its own per photo in `@eaDir`, and over SMB every one of
    them is a round trip the scan has no use for. The metadata folders are
    still walked — the callers skip their files with is_meta_path."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not schema.is_system_dir(d)]
        found.extend(Path(dirpath) / name for name in filenames)
    return sorted(found)


# iPhone photos are HEIC; without the plugin they cannot be opened at all,
# so the flag is worth reporting rather than only logging once at import
# (see `python -m aperture.cli status`).
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False
    log.warning("pillow-heif not installed; HEIC/HEIF support disabled")

JPEG_CONVERT_EXTS = {".heic", ".heif"}

# EXIF Orientation 5-8 mean the stored pixel buffer is a quarter turn away
# from how the photo is meant to be shown -- how every portrait from a
# phone arrives. Everything written below is written UPRIGHT, and the
# indexed dimensions are stored upright with it: a derivative carries no
# Orientation tag of its own (see _derivative_exif), so a browser cannot
# put a sideways one right, and the tile sizes itself from those numbers.
_SWAPPED_ORIENTATIONS = {5, 6, 7, 8}


def _orientation(exif: dict) -> int:
    """The EXIF Orientation as a plain int; 1 ("as it lies") when the tag
    is absent or unreadable."""
    try:
        return int(float(exif.get("Orientation")))
    except (TypeError, ValueError):
        return 1


# The format each derivative tier is written in. The grid thumbnail is WebP:
# an album page asks for hundreds of them and they are the one tier a visitor
# on a slow line waits for, and WebP is a third to a half smaller than the
# JPEG at the same quality (measured over this library: 42 KB -> 26 KB
# average, 480 px tiles at q82 vs q76). The preview stays
# JPEG on purpose — it is what `og:image` hands to link unfurlers, and not all
# of those decode WebP.
THUMB_EXT = ".webp"
PREVIEW_EXT = ".jpg"
DERIVATIVE_EXTS = (".jpg", ".webp")


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
# photos.extract_description).
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
    except Exception as e:
        # Broad on purpose — see make_thumbnail.
        log.warning("gps strip failed for %s: %s", path, e)
        return False


# ----- marks on the derived images --------------------------------------
# Every JPEG this module writes is a DERIVATIVE — a thumbnail, a preview, or
# a converted full — generated here, and so carrying no metadata of its own
# unless it is given some. Two names belong in it:
#
#   the vendor's     the EXIF `Software` tag. Unconditional, and the one
#                    attribution that rides in the served bytes rather than
#                    in the page around them (aperture/brand.py).
#
#   the operator's   EXIF Artist / Copyright, from gallery.cfg `credit` —
#                    who took the picture. Never the vendor's name there:
#                    resizing an image is not authorship.
#
# Metadata only. Nothing is drawn onto a photograph, and the originals under
# photos/ are never rewritten — a JPEG original is still handed out as the
# bytes on disk (the X-Powered-By header is what names the software on that
# response).
#
# The operator half comes from aperture/marks.py, which reads gallery.cfg;
# every writer below asks it through marks.credit(). Nothing had to grow a
# parameter, which matters: thumbs are written from five places (here, the
# watcher, the CLI, and both serve routes) and metadata that reached only
# some of them would be worse than none.


def _credit() -> str | None:
    """gallery.cfg `credit` (see aperture/marks.py). A cfg problem must never
    cost a thumbnail, so it is reported and treated as unset."""
    try:
        return marks.credit()
    except Exception as e:
        log.warning("credit unreadable: %s: %s", type(e).__name__, e)
        return None


def marks_stamp() -> float:
    """When `credit` last changed (see aperture/marks.py), or 0."""
    try:
        return float(marks.stamp() or 0.0)
    except Exception:
        return 0.0


def needs_rebuild(dst: Path, src: Path) -> bool:
    """Does `dst` need rebuilding? Newer than its source is not quite enough
    on its own: a `credit` added or changed in gallery.cfg has to invalidate
    the derivatives written before the edit, and that does not touch the
    photo's own mtime."""
    try:
        if not dst.exists():
            return True
        return dst.stat().st_mtime < max(src.stat().st_mtime, marks_stamp())
    except OSError:
        return True


def _derivative_exif(credit: str | None) -> bytes:
    """The EXIF block written into every derivative: which software made it,
    and — when gallery.cfg names one — who the photo belongs to. The
    originals keep their own EXIF; these files are generated here and would
    otherwise carry none at all, so nothing is being overwritten."""
    exif = Image.Exif()
    exif[0x0131] = brand.GENERATOR            # Software
    if credit:
        exif[0x013B] = credit                 # Artist
        exif[0x8298] = credit                 # Copyright
    return exif.tobytes()


# Decoding a damaged file throws far more than OSError: PIL's plugins raise
# SyntaxError ("broken PNG file"), ValueError, struct.error, zlib.error and
# Image.DecompressionBombError straight out of load(). Every decode path below
# therefore catches Exception and turns the failure into a False/None return —
# a single half-written upload on the share must never escape as an exception,
# because in full_scan() it would abort the whole walk (see there) and in the
# /thumb route it surfaces as a 500 with a traceback instead of a skipped file.
def make_thumbnail(src: Path, dst: Path, size: int) -> bool:
    tmp = None
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)   # upright, see the note above
            img.thumbnail((size, size), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (20, 20, 20))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            # The output format follows the destination's own suffix, so the
            # tier decides (THUMB_EXT / PREVIEW_EXT) and every caller — scan,
            # CLI rebuild, on-demand route — writes the same thing. `method`
            # is the encoder's effort knob: 4 is the point where more time
            # stops buying meaningful size on 480 px tiles.
            #
            # Encoded beside the destination and renamed into place: the scan,
            # the gallery's /thumb route and the console's /api/thumb all build
            # into this tree, and none of them may hand out a half-written file.
            tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            if dst.suffix.lower() == ".webp":
                img.save(tmp, "WEBP", quality=76, method=4,
                         exif=_derivative_exif(_credit()))
            else:
                img.save(tmp, "JPEG", quality=82, optimize=True, progressive=True,
                         exif=_derivative_exif(_credit()))
        tmp.replace(dst)
        return True
    except Exception as e:
        log.warning("thumb failed for %s: %s: %s", src, type(e).__name__, e)
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        return False


def max_edge(p: Path) -> int | None:
    """The longest edge of an image file, or None when it cannot be read.
    Opening does not decode the pixels — PIL reads the header — so this is
    cheap enough to ask on a request path."""
    try:
        with Image.open(p) as img:
            return max(img.size)
    except Exception:
        return None


def make_brand_thumb(src: Path, dst: Path, size: int) -> bool:
    """A capped WebP copy of a branding image (the operator portrait, a logo).

    Not make_thumbnail(): a mark may be transparent and must stay that way,
    so this keeps the alpha channel instead of flattening it onto the tile
    background, and it writes no credit EXIF — a logo is the operator's, not
    a photo of theirs. Only ever called for raster marks; SVG has no business
    here and is served untouched."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size), Image.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            img.save(dst, "WEBP", quality=82, method=4)
        return True
    except Exception as e:
        log.warning("brand thumb failed for %s: %s: %s", src, type(e).__name__, e)
        return False


def make_full_jpeg(src: Path, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            # The HEIC path, which is exactly where rotated phone photos arrive.
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (20, 20, 20))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=92, optimize=True, progressive=True,
                     exif=_derivative_exif(_credit()))
        return True
    except Exception as e:
        log.warning("full jpeg conversion failed for %s: %s: %s", src, type(e).__name__, e)
        return False


def ensure_full_jpeg(photos_dir: Path, fulls_dir: Path, rel_path: str) -> Path | None:
    src = photos_dir / rel_path
    if not src.exists() or not schema.is_image(src):
        return None
    dst = (fulls_dir / rel_path).with_suffix(".jpg")
    if not needs_rebuild(dst, src):
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
    """Point an image's `image_tags` rows at exactly `tag_names`.

    Compares against what is stored and returns without writing when the two
    already agree, which is the common case: a re-index is usually triggered
    by the photo, not by its sidecar. Readers get a proper snapshot now that
    every thread has its own connection (app/db.py), so the DELETE-then-INSERT
    below is no longer observable half-done — but the cheapest write is still
    the one that doesn't happen, and a scan re-reads every sidecar it walks.

    The unused-tag sweep deliberately does NOT live here: it scans the whole
    `tags` table and used to run once per indexed photo. prune_tags() does it
    once per scan instead.
    """
    c = db.conn()
    wanted = list(dict.fromkeys(tag_names))
    with db.lock():
        current = [r["name"] for r in c.execute(
            "SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id = t.id "
            "WHERE it.image_id = ?", (image_id,)).fetchall()]
        # tags.name is COLLATE NOCASE, so compare the way the table does
        if {n.casefold() for n in current} == {n.casefold() for n in wanted}:
            return
        c.execute("DELETE FROM image_tags WHERE image_id = ?", (image_id,))
        for name in wanted:
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            c.execute(
                "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)",
                (image_id, tag_id),
            )
        c.commit()


def prune_tags() -> int:
    """Drop `tags` rows no photo carries any more; returns how many went.

    A whole-table sweep — cheap once, wasteful per photo, which is where it
    used to sit (inside _sync_tags, so a 5000-photo scan ran it 5000 times).
    Orphans are invisible in the meantime: every query that reads tags joins
    through image_tags, so this is housekeeping, not correctness."""
    c = db.conn()
    with db.lock():
        cur = c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)")
        c.commit()
        return cur.rowcount or 0


# ----- content hash -----------------------------------------------------
# The index used to know a photo only by its mtime, and the derivatives were
# rebuilt only when their source was NEWER than them. A photo edited and
# uploaded again under the same name is exactly the case that slipped
# through: plenty of copy tools and NAS clients keep the original timestamp,
# so the gallery went on serving the thumbnail and preview of the old
# picture. Every photo now carries the SHA-256 of its bytes, and a different
# hash under the same name drops the old derivatives so they are built from
# the new file.
HASH_CHUNK = 1 << 20

# How much of the hash rides photo URLs as `?v=` (see photos.media_url): the
# derivative routes are cached for a year, so a replaced photo needs a new
# address, not just new files on disk.
STAMP_LEN = 12


def content_hash(path: Path) -> str:
    """SHA-256 of the file's bytes, read in chunks so a 100 MB original never
    sits in memory whole."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def photo_stamp(rel: str) -> str | None:
    """The version stamp of one indexed photo, or None when it has no hash
    yet (indexed before hashing, not scanned since) or is not indexed."""
    row = db.conn().execute("SELECT content_hash FROM images WHERE rel_path = ?",
                            (rel,)).fetchone()
    return row["content_hash"][:STAMP_LEN] if row and row["content_hash"] else None


def media_url(kind: str, photo, base: str = "") -> str:
    """The address of one photo's `thumb`, `preview` or `full`, carrying
    `?v=<stamp>` so a photo replaced under the same name gets a new URL and
    the year-long cache (gallery/context.IMMUTABLE) never serves the old
    picture. `photo` is an index row (a dict with rel_path + content_hash —
    no query) or a bare rel_path (looked up: album covers, trip stops).
    Without a hash yet the URL is plain, and the route caches it briefly."""
    if isinstance(photo, str):
        rel, stamp = photo, photo_stamp(photo)
    else:
        rel = photo["rel_path"]
        # a dict or a sqlite3.Row; a row selected without the column is
        # just a photo without a stamp
        digest = photo["content_hash"] if "content_hash" in photo.keys() else None
        stamp = digest[:STAMP_LEN] if digest else None
    url = f"{base}/{kind}/{rel}"
    return f"{url}?v={stamp}" if stamp else url


def derivative_paths(rel: str) -> list[Path]:
    """Every generated file one photo may have on disk: each tier, in every
    format that tier has ever been written in (DERIVATIVE_EXTS) — a gallery
    that served JPEG thumbs before the WebP switch still has those."""
    out = []
    for d in (settings.thumbs_dir, settings.previews_dir, settings.fulls_dir):
        if d is None:
            continue
        out.extend((d / rel).with_suffix(ext) for ext in DERIVATIVE_EXTS)
    return out


def drop_derivatives(rel: str) -> int:
    """Delete one photo's derivatives; returns how many files went. Whoever
    asks next (the scan, the watcher, a visitor) builds them fresh."""
    dropped = 0
    for f in derivative_paths(rel):
        try:
            if f.exists():
                f.unlink()
                dropped += 1
        except OSError:
            pass
    return dropped


def _keep_derivatives(rel: str) -> None:
    """The bytes are the same but the file's mtime moved (a re-copy, a
    `touch`, the same export uploaded twice): bring the derivatives' mtimes
    along, so needs_rebuild() does not rebuild files that would come out
    identical."""
    for f in derivative_paths(rel):
        try:
            if f.exists():
                os.utime(f)
        except OSError:
            pass


def index_image(photos_dir: Path, file: Path, force: bool = False) -> bool:
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
    # the album *tree* is derived from these paths (see aperture/albums.py).
    album = "/".join(parts[:-1])
    filename = parts[-1]
    stat = file.stat()
    mtime = stat.st_mtime
    sidecar = file.with_suffix(file.suffix + ".tags")
    sidecar_mtime = sidecar.stat().st_mtime if sidecar.exists() else 0.0
    effective_mtime = max(mtime, sidecar_mtime)

    c = db.conn()
    row = c.execute("SELECT id, mtime, size, content_hash FROM images WHERE rel_path = ?",
                    (rel,)).fetchone()
    # Same mtime AND same size: unchanged, without reading a byte. The size
    # is what catches most edits uploaded with their old timestamp kept; the
    # hash below settles everything that gets past this line.
    # `force` re-reads a file whose stamp says "unchanged" — the escape
    # hatch for a row that went bad (bogus EXIF, a restored backup that
    # kept its old timestamps). See `scan --force`.
    if not force and row and abs(row["mtime"] - effective_mtime) < 1.0 \
            and row["size"] == stat.st_size:
        if row["content_hash"] is None:
            # indexed before hashing existed: hashed once, on the first scan
            # after the upgrade, so its URLs can carry a version stamp too
            digest = content_hash(file)
            with db.lock():
                c.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, row["id"]))
                c.commit()
        return False

    if settings.strip_gps and strip_gps_inplace(file):
        stat = file.stat()
        mtime = stat.st_mtime
        effective_mtime = max(mtime, sidecar_mtime)

    # After the GPS strip, so the stored hash is of the file as it now lies.
    digest = content_hash(file)
    if row and row["content_hash"] == digest and not force:
        # Only the timestamps moved (or the .tags sidecar did): the EXIF and
        # the derivatives would come out the same, so neither is redone.
        _keep_derivatives(rel)
        with db.lock():
            c.execute("UPDATE images SET mtime = ?, size = ? WHERE id = ?",
                      (effective_mtime, stat.st_size, row["id"]))
            c.commit()
        _sync_tags(row["id"], _read_sidecar_tags(file))
        return True
    if row and row["content_hash"] != digest:
        # A different picture under the same name: its old thumbnail, preview
        # and full JPEG go, and are rebuilt from the new bytes.
        if drop_derivatives(rel):
            log.info("%s changed on disk -> derivatives dropped", rel)

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
            if _orientation(exif) in _SWAPPED_ORIENTATIONS:
                # Stored as the photo is SHOWN, which is what the derivatives
                # are written as and what the tile is sized from.
                width, height = height, width
    except Exception as e:
        log.warning("open failed for %s: %s: %s", file, type(e).__name__, e)
        return False

    # `is_showcase` (featured flag) is owned by albums.recompute_featured(),
    # which derives it from each album's album.cfg (`featured = …`). We
    # deliberately leave the column untouched here so a re-index never
    # clobbers a computed flag: new rows default to 0, existing rows keep
    # their value.
    # the searchable facts, in columns of their own (see capture.py)
    fact = capture.facts(exif)
    with db.lock():
        c.execute(
            """INSERT INTO images (album, filename, rel_path, mtime, size, width, height, exif_json, taken_at,
                                   camera, lens, focal, aperture, iso, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                 album=excluded.album, filename=excluded.filename, mtime=excluded.mtime,
                 size=excluded.size, width=excluded.width, height=excluded.height,
                 exif_json=excluded.exif_json, taken_at=excluded.taken_at,
                 camera=excluded.camera, lens=excluded.lens, focal=excluded.focal,
                 aperture=excluded.aperture, iso=excluded.iso,
                 content_hash=excluded.content_hash""",
            (album, filename, rel, effective_mtime, stat.st_size, width, height, json.dumps(exif), taken,
             fact["camera"], fact["lens"], fact["focal"], fact["aperture"], fact["iso"], digest),
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


def _empty_scan(root: str | None = None) -> dict:
    return {"indexed": 0, "thumbnails": 0, "previews": 0, "removed": 0,
            "failed": 0, "total_seen": 0, "root": root}


def full_scan(photos_dir: Path, thumbs_dir: Path, thumb_size: int,
              previews_dir: Path | None = None, preview_size: int = 1600,
              root: str | None = None, force: bool = False) -> dict:
    """Walk the photo tree, index what changed, build missing derivatives and
    drop rows whose file is gone.

    `root` narrows all of that to one album subtree (path relative to
    photos_dir) — including the stale-row cleanup, which is then scoped to
    rows inside that subtree. Without the scoping a partial walk would read
    as "everything else disappeared" and wipe the rest of the index.

    `force` re-indexes and re-derives even when mtimes say nothing changed.
    """
    added = 0
    thumbed = 0
    previewed = 0
    failed = 0
    seen: set[str] = set()
    if not photos_dir.exists():
        try:
            photos_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            log.warning("photos dir does not exist and is not writable: %s", photos_dir)
            return _empty_scan(root)
    base = photos_dir
    if root:
        base = (photos_dir / root).resolve()
        try:
            base.relative_to(photos_dir)
        except ValueError:
            log.warning("scan root outside the photo tree: %s", root)
            return _empty_scan(root)
        if not base.is_dir():
            log.warning("scan root does not exist: %s", root)
            return _empty_scan(root)
    # Walk the whole tree so albums can nest (photos/japan/tokyo/img.jpg).
    # Files sitting directly in photos_dir (no album folder) are skipped.
    for file in walk_photo_tree(base):
        if not file.is_file() or not schema.is_image(file):
            continue
        relp = file.relative_to(photos_dir)
        if len(relp.parts) < 2:
            continue
        if is_meta_path(relp):
            continue  # album metadata, not a photo — no index, no thumbs
        rel = relp.as_posix()
        # Marked seen before any processing: whatever goes wrong below, the
        # file is on disk, so the cleanup pass at the end must not drop its
        # (possibly still valid) row from the index.
        seen.add(rel)
        # One damaged or half-uploaded file must never take the scan down with
        # it. Without this guard a single unreadable photo aborts the whole
        # walk — every album sorting after it is left unindexed and un-thumbed,
        # the stale-row cleanup never runs, and albums.recompute_featured() is
        # never reached, so the gallery silently loses everything past that
        # file until it is removed.
        broken = False  # counts the FILE once, however many derivatives failed
        try:
            if index_image(photos_dir, file, force=force):
                added += 1
            thumb_path = (thumbs_dir / rel).with_suffix(THUMB_EXT)
            if force or needs_rebuild(thumb_path, file):
                if make_thumbnail(file, thumb_path, thumb_size):
                    thumbed += 1
                else:
                    broken = True
            if previews_dir is not None:
                preview_path = (previews_dir / rel).with_suffix(PREVIEW_EXT)
                if force or needs_rebuild(preview_path, file):
                    if make_thumbnail(file, preview_path, preview_size):
                        previewed += 1
                    else:
                        broken = True
        except Exception as e:
            broken = True
            log.warning("skipped %s: %s: %s", rel, type(e).__name__, e)
        if broken:
            failed += 1

    c = db.conn()
    with db.lock():
        if root:
            # Scoped cleanup: only rows inside the walked subtree may be
            # dropped. substr() (not LIKE) keeps `_`/`%` in album names from
            # acting as wildcards.
            prefix = root + "/"
            existing = [r["rel_path"] for r in c.execute(
                "SELECT rel_path FROM images WHERE album = ? OR substr(album, 1, ?) = ?",
                (root, len(prefix), prefix)).fetchall()]
        else:
            existing = [r["rel_path"] for r in c.execute("SELECT rel_path FROM images").fetchall()]
        removed = 0
        # A whole-tree walk that found no photo at all while the index still
        # holds rows is an unmounted share far more often than an emptied
        # gallery: a `nofail` CIFS mount that did not come up leaves an empty
        # directory, and dropping every row would read as an empty site until
        # the share is back and a scan re-indexes. So the rows stay and the
        # scan says why. A scoped walk needs no such guard — a missing album
        # folder already returns above — and `force` is how an operator says
        # the gallery really is empty now.
        held = not root and not seen and bool(existing) and not force
        if held:
            log.warning("scan found no photos under %s but the index holds %d; "
                        "left the index as it is (share not mounted?) -- "
                        "`scan --force` clears it if the gallery really is empty",
                        photos_dir, len(existing))
        else:
            for rel in existing:
                if rel not in seen:
                    c.execute("DELETE FROM images WHERE rel_path = ?", (rel,))
                    removed += 1
        c.commit()
    # once per scan, not once per photo — see prune_tags()
    prune_tags()
    return {
        "indexed": added,
        "thumbnails": thumbed,
        "previews": previewed,
        "removed": removed,
        "failed": failed,
        "total_seen": len(seen),
        # True when the empty-walk guard above kept the index as it was
        "held": held,
        "root": root,
    }


def ensure_thumb(photos_dir: Path, thumbs_dir: Path, rel_path: str, size: int,
                 ext: str = THUMB_EXT) -> Path | None:
    src = photos_dir / rel_path
    if not src.exists() or not schema.is_image(src):
        return None
    dst = (thumbs_dir / rel_path).with_suffix(ext)
    if not needs_rebuild(dst, src):
        return dst
    if make_thumbnail(src, dst, size):
        return dst
    return None
