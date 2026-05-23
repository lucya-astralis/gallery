import json
import logging
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image, ExifTags, UnidentifiedImageError

from . import db

log = logging.getLogger("scanner")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic"}


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


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


def extract_exif(img: Image.Image) -> tuple[dict, str | None]:
    exif_raw = img.getexif()
    out: dict = {}
    if not exif_raw:
        return out, None
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


def index_image(photos_dir: Path, file: Path) -> bool:
    rel = file.relative_to(photos_dir).as_posix()
    parts = file.relative_to(photos_dir).parts
    if len(parts) < 2:
        return False
    album = parts[0]
    filename = parts[-1]
    stat = file.stat()
    mtime = stat.st_mtime

    c = db.conn()
    with db.lock():
        row = c.execute("SELECT id, mtime FROM images WHERE rel_path = ?", (rel,)).fetchone()
        if row and abs(row["mtime"] - mtime) < 1.0:
            return False

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

    with db.lock():
        c.execute(
            """INSERT INTO images (album, filename, rel_path, mtime, size, width, height, exif_json, taken_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                 album=excluded.album, filename=excluded.filename, mtime=excluded.mtime,
                 size=excluded.size, width=excluded.width, height=excluded.height,
                 exif_json=excluded.exif_json, taken_at=excluded.taken_at""",
            (album, filename, rel, mtime, stat.st_size, width, height, json.dumps(exif), taken),
        )
        c.commit()
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


def full_scan(photos_dir: Path, thumbs_dir: Path, thumb_size: int) -> dict:
    added = 0
    thumbed = 0
    seen: set[str] = set()
    if not photos_dir.exists():
        photos_dir.mkdir(parents=True, exist_ok=True)
    for album_dir in sorted(p for p in photos_dir.iterdir() if p.is_dir()):
        for file in sorted(album_dir.iterdir()):
            if not file.is_file() or not is_image(file):
                continue
            rel = file.relative_to(photos_dir).as_posix()
            seen.add(rel)
            if index_image(photos_dir, file):
                added += 1
            thumb_path = thumbs_dir / rel
            thumb_path = thumb_path.with_suffix(".jpg")
            if not thumb_path.exists() or thumb_path.stat().st_mtime < file.stat().st_mtime:
                if make_thumbnail(file, thumb_path, thumb_size):
                    thumbed += 1

    c = db.conn()
    with db.lock():
        existing = [r["rel_path"] for r in c.execute("SELECT rel_path FROM images").fetchall()]
        removed = 0
        for rel in existing:
            if rel not in seen:
                c.execute("DELETE FROM images WHERE rel_path = ?", (rel,))
                removed += 1
        c.commit()
    return {"indexed": added, "thumbnails": thumbed, "removed": removed, "total_seen": len(seen)}


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
