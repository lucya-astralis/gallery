"""Vision: search a photo by what is in it. Optional, and OFF by default.

A local image model (CLIP ViT-B/32, with a multilingual text side) turns
every photo into 512 numbers once, when it is indexed, and a search into the
same 512 numbers when it is typed; the photos whose numbers point the same
way are the ones that look like what was typed -- "sunset over the sea",
"Tempel", "夕日" -- whether or not a tag or a file name says so.

Everything runs on this machine. Nothing is sent anywhere; the only network
this module ever touches is the one-time download of the model files, from
pinned revisions, each checked against its SHA-256 before it is used.

    VISION=1            switch it on (runtime.py); nothing is loaded without it
    VISION_THREADS=2    CPU threads the model may use

What it costs, measured on a desktop CPU with the quantized files below:
~20 ms to read a photo (800 photos: under half a minute, once), 2-4 ms to
read a search, ~225 MB on disk in DATA_DIR/models, and memory for the text
side (~300 MB) plus the image side while a scan runs -- that one is released
when the scan ends.

The model is behind a small interface (`Backend`) so the tests can stand in
a fake one: they check the plumbing, never the model's taste. And its taste
can be wrong -- a search for a cat in an archive without one finds the
nearest animals -- which is why the pages show these matches as a group of
their own, labelled as "looks like", and let every search switch them off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import threading
import urllib.request
from pathlib import Path

import numpy as np

from .runtime import settings

log = logging.getLogger("aperture.vision")

DIM = 512
# The model, by pinned revision: what is downloaded is exactly what was
# measured, and a file that does not hash to its entry is thrown away.
_TEXT = ("sentence-transformers/clip-ViT-B-32-multilingual-v1", "58edf8cada9e398793dca955574a48cbb7f18be2")
_IMAGE = ("Xenova/clip-vit-base-patch32", "d15189d7028b43f1d3e65039190477f6af591c2a")
FILES = [
    # (repo, revision), path in the repo, local name, bytes, sha256
    (_TEXT, "onnx/model_quint8_avx2.onnx", "text.onnx", 135377779,
     "fbc8fbeaa5237d96bd1bf430057c70d34de8334a463e933a5faa305f6caeed9c"),
    (_TEXT, "tokenizer.json", "tokenizer.json", 1961847,
     "5b4e1a8171c81dfd666ae40265b9530c6e0b3d53923fe8ac493dcc84229adf81"),
    (_TEXT, "2_Dense/model.safetensors", "dense.safetensors", 1572984,
     "d12568dc7300970a4d3dbb49068ad16cd89b99840b74b026f8e48071e9414f74"),
    (_IMAGE, "onnx/vision_model_quantized.onnx", "image.onnx", 89117001,
     "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
]
MODEL_NAME = "clip-ViT-B-32-multilingual"
_HF = "https://huggingface.co/%s/resolve/%s/%s"

# CLIP's own image preprocessing (preprocessor_config.json of the image repo)
_SIDE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

# How a search keeps what the model found. Scores between a text and a
# photo run low with this model (~0.2-0.4); below the floor it is guessing,
# and anything well behind the best match is a different subject.
FLOOR = 0.24
BEHIND_BEST = 0.05
LIMIT = 24


def models_dir() -> Path:
    return settings.data_dir / "models" / MODEL_NAME


# ----- the files -------------------------------------------------------------
_install_lock = threading.Lock()
_state = {"installing": False, "error": None, "done_bytes": 0}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def installed() -> bool:
    """Every file there at its size. (The hash is checked when it arrives.)"""
    d = models_dir()
    return all((d / name).is_file() and (d / name).stat().st_size == size
               for _, _, name, size, _ in FILES)


def install() -> bool:
    """Fetch whatever is missing, each file checked before it is kept. Safe
    to call again after a failure: what arrived stays, the rest is retried."""
    with _install_lock:
        if installed():
            return True
        d = models_dir()
        d.mkdir(parents=True, exist_ok=True)
        _state.update(installing=True, error=None, done_bytes=0)
        try:
            for (repo, rev), path, name, size, digest in FILES:
                target = d / name
                if target.is_file() and target.stat().st_size == size:
                    _state["done_bytes"] += size
                    continue
                part = target.with_suffix(target.suffix + ".part")
                log.info("vision: fetching %s (%d MB)", name, size // (1 << 20))
                with urllib.request.urlopen(_HF % (repo, rev, path), timeout=120) as res, \
                        part.open("wb") as out:
                    while True:
                        chunk = res.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        _state["done_bytes"] += len(chunk)
                if part.stat().st_size != size or _sha256(part) != digest:
                    part.unlink(missing_ok=True)
                    raise RuntimeError("%s did not match its checksum" % name)
                part.replace(target)
            log.info("vision: model installed in %s", d)
            return True
        except Exception as e:           # a failed download never stops a scan
            _state["error"] = "%s: %s" % (type(e).__name__, e)
            log.warning("vision: install failed -- %s", _state["error"])
            return False
        finally:
            _state["installing"] = False


# ----- the model ---------------------------------------------------------------
class Backend:
    """The real model, loaded half at a time: the text side on the first
    search and kept, the image side for a scan and released after it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._text = None
        self._image = None
        self._tok = None
        self._dense = None
        self._lock = threading.Lock()

    def _session(self, name):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, settings.vision_threads)
        opts.inter_op_num_threads = 1
        return ort.InferenceSession(str(self.folder / name), opts, providers=["CPUExecutionProvider"])

    def _load_text(self):
        if self._text is None:
            from tokenizers import Tokenizer
            self._tok = Tokenizer.from_file(str(self.folder / "tokenizer.json"))
            self._tok.enable_truncation(128)
            self._dense = _safetensor(self.folder / "dense.safetensors")
            self._text = self._session("text.onnx")

    def embed_text(self, text: str) -> np.ndarray:
        with self._lock:
            self._load_text()
            enc = self._tok.encode(text)
            ids = np.array([enc.ids], np.int64)
            mask = np.array([enc.attention_mask], np.int64)
            hidden = self._text.run(None, {"input_ids": ids, "attention_mask": mask})[0]
        # mean pooling over the real tokens, then the model's own projection
        # into the image side's 512 dimensions (1_Pooling / 2_Dense)
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return _unit(pooled @ self._dense.T)[0]

    def embed_images(self, images) -> np.ndarray:
        batch = np.stack([_pixels(img) for img in images])
        with self._lock:
            if self._image is None:
                self._image = self._session("image.onnx")
            out = self._image.run(None, {"pixel_values": batch})[0]
        return _unit(out)

    def release_images(self):
        with self._lock:
            self._image = None


def _safetensor(path: Path) -> np.ndarray:
    """The one tensor of a .safetensors file (the 768 -> 512 projection),
    read without the safetensors package: an 8-byte length, a JSON header,
    then the raw little-endian floats."""
    raw = path.read_bytes()
    n = struct.unpack("<Q", raw[:8])[0]
    head = {k: v for k, v in json.loads(raw[8:8 + n]).items() if k != "__metadata__"}
    (spec,) = head.values()
    a, b = spec["data_offsets"]
    return np.frombuffer(raw[8 + n + a:8 + n + b], dtype=np.float32).reshape(spec["shape"])


def _pixels(img) -> np.ndarray:
    """CLIP's preprocessing: shortest side to 224 (bicubic), centre crop,
    scale to 0-1, normalise per channel, channels first."""
    from PIL import Image
    img = img.convert("RGB")
    w, h = img.size
    s = _SIDE / min(w, h)
    img = img.resize((max(_SIDE, round(w * s)), max(_SIDE, round(h * s))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - _SIDE) // 2, (h - _SIDE) // 2
    a = np.asarray(img.crop((left, top, left + _SIDE, top + _SIDE)), np.float32) / 255.0
    return ((a - _MEAN) / _STD).transpose(2, 0, 1)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


_backend: Backend | None = None
_backend_lock = threading.Lock()


def set_backend(backend) -> None:
    """Stand another model in (the tests' fake), or None to go back."""
    global _backend
    with _backend_lock:
        _backend = backend
    _matrix_cache.clear()


def backend():
    """The model, or None when vision is off or not installed yet."""
    global _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        if not settings.vision or not installed():
            return None
        _backend = Backend(models_dir())
        return _backend


def enabled() -> bool:
    return backend() is not None


def release_images() -> None:
    b = _backend
    if b is not None and hasattr(b, "release_images"):
        b.release_images()


# ----- vectors in the index ------------------------------------------------------
def pack(v: np.ndarray) -> bytes:
    return np.asarray(v, np.float16).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, np.float16).astype(np.float32)


_matrix_cache: dict = {}


def matrix(conn) -> tuple[list[int], np.ndarray]:
    """(image ids, their vectors as rows) for every photo that has one. Kept
    between searches and rebuilt when the index's vectors change -- one
    COUNT and one MAX tell."""
    stamp = tuple(conn.execute(
        "SELECT COUNT(vision), MAX(CASE WHEN vision IS NOT NULL THEN id END) FROM images").fetchone())
    if _matrix_cache.get("stamp") == stamp:
        return _matrix_cache["ids"], _matrix_cache["m"]
    rows = conn.execute("SELECT id, vision FROM images WHERE vision IS NOT NULL").fetchall()
    ids = [r[0] for r in rows]
    m = np.stack([unpack(r[1]) for r in rows]) if rows else np.zeros((0, DIM), np.float32)
    _matrix_cache.update(stamp=stamp, ids=ids, m=m)
    return ids, m


def forget() -> None:
    """Drop the cached matrix (the scanner calls this after it writes)."""
    _matrix_cache.clear()


def rank(conn, text: str, allowed: set[int] | None = None, exclude: set[int] = frozenset(),
         limit: int = LIMIT) -> list[tuple[int, float]]:
    """The photos that look like `text`, best first, as (image id, score):
    only those in `allowed` (when given), never those in `exclude`, above
    FLOOR and within BEHIND_BEST of the best one."""
    b = backend()
    if b is None or not text.strip():
        return []
    ids, m = matrix(conn)
    if not ids:
        return []
    scores = m @ b.embed_text(text.strip())
    order = np.argsort(-scores)
    out: list[tuple[int, float]] = []
    best = None
    for i in order:
        s = float(scores[i])
        if s < FLOOR or (best is not None and s < best - BEHIND_BEST):
            break
        image_id = ids[i]
        if (allowed is not None and image_id not in allowed) or image_id in exclude:
            continue
        if best is None:
            best = s
        out.append((image_id, s))
        if len(out) >= limit:
            break
    return out


def status(conn) -> dict:
    """What the console's System place shows."""
    read, total = conn.execute("SELECT COUNT(vision), COUNT(*) FROM images").fetchone()
    return {
        "enabled": settings.vision,
        "installed": installed(),
        "installing": _state["installing"],
        "error": _state["error"],
        "downloaded_mb": _state["done_bytes"] // (1 << 20),
        "size_mb": sum(f[3] for f in FILES) // (1 << 20),
        "model": MODEL_NAME,
        "folder": str(models_dir()),
        "read": read,
        "total": total,
        "threads": settings.vision_threads,
    }
