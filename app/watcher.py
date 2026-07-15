import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import scanner

log = logging.getLogger("watcher")


class _Handler(FileSystemEventHandler):
    def __init__(self, photos_dir: Path, thumbs_dir: Path, thumb_size: int,
                 previews_dir: Path | None = None, preview_size: int = 1600,
                 fulls_dir: Path | None = None, on_config=None):
        self.photos_dir = photos_dir
        self.thumbs_dir = thumbs_dir
        self.thumb_size = thumb_size
        self.previews_dir = previews_dir
        self.preview_size = preview_size
        self.fulls_dir = fulls_dir
        # called (no args) whenever an album.cfg changes, so featured/showcase
        # edits take effect without waiting for the next periodic scan.
        self.on_config = on_config
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()

    def _enqueue(self, path: str):
        with self._lock:
            self._pending[path] = time.time()

    def _is_meta(self, path: Path) -> bool:
        """True for anything in an album's `.album/` folder — descriptions,
        fonts, and any image that happens to live there (a font specimen,
        say). Checked *after* album.cfg, which lives there too but has its
        own handling."""
        try:
            return scanner.is_meta_path(path.relative_to(self.photos_dir))
        except ValueError:
            return False

    def _fire_config(self):
        if not self.on_config:
            return
        try:
            self.on_config()
            log.info("album.cfg changed -> recomputed featured")
        except Exception as e:
            log.warning("on_config callback failed: %s", e)

    def _drain(self):
        while True:
            time.sleep(1.5)
            now = time.time()
            ready: list[str] = []
            with self._lock:
                for p, t in list(self._pending.items()):
                    if now - t > 1.0:
                        ready.append(p)
                        del self._pending[p]
            for p in ready:
                fp = Path(p)
                if fp.name == "album.cfg":
                    self._fire_config()
                    continue
                if self._is_meta(fp):
                    continue  # album metadata — never a photo, never thumbed
                if fp.suffix == ".tags":
                    image = fp.with_suffix("")
                    if image.exists() and scanner.is_image(image):
                        try:
                            scanner.index_image(self.photos_dir, image)
                            log.info("re-indexed tags for %s", image.name)
                        except Exception as e:
                            log.warning("tag reindex failed for %s: %s", image, e)
                    continue
                if not fp.exists():
                    scanner.remove_image(self.photos_dir, fp)
                    continue
                if not scanner.is_image(fp):
                    continue
                try:
                    scanner.index_image(self.photos_dir, fp)
                    rel = fp.relative_to(self.photos_dir).as_posix()
                    thumb_dst = (self.thumbs_dir / rel).with_suffix(".jpg")
                    scanner.make_thumbnail(fp, thumb_dst, self.thumb_size)
                    if self.previews_dir is not None:
                        prev_dst = (self.previews_dir / rel).with_suffix(".jpg")
                        scanner.make_thumbnail(fp, prev_dst, self.preview_size)
                    log.info("indexed %s", rel)
                except Exception as e:
                    log.warning("process failed for %s: %s", p, e)

    def on_created(self, event):
        if event.is_directory:
            return
        self._enqueue(event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._enqueue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            scanner.remove_image(self.photos_dir, Path(event.src_path))
            self._enqueue(event.dest_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        fp = Path(event.src_path)
        if fp.name == "album.cfg":
            self._fire_config()
            return
        if self._is_meta(fp):
            return  # album metadata — nothing indexed, nothing to clean up
        if fp.suffix == ".tags":
            image = fp.with_suffix("")
            if image.exists() and scanner.is_image(image):
                try:
                    scanner.index_image(self.photos_dir, image)
                except Exception as e:
                    log.warning("tag-removal reindex failed for %s: %s", image, e)
            return
        scanner.remove_image(self.photos_dir, fp)
        try:
            rel = fp.relative_to(self.photos_dir).as_posix()
            for d in (self.thumbs_dir, self.previews_dir, self.fulls_dir):
                if d is None:
                    continue
                f = (d / rel).with_suffix(".jpg")
                if f.exists():
                    f.unlink()
        except Exception:
            pass


def start(photos_dir: Path, thumbs_dir: Path, thumb_size: int,
          previews_dir: Path | None = None, preview_size: int = 1600,
          fulls_dir: Path | None = None, on_config=None) -> Observer:
    try:
        photos_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        pass
    handler = _Handler(photos_dir, thumbs_dir, thumb_size, previews_dir, preview_size, fulls_dir, on_config)
    obs = Observer()
    obs.schedule(handler, str(photos_dir), recursive=True)
    obs.daemon = True
    obs.start()
    log.info("watching %s", photos_dir)
    return obs
