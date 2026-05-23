import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import scanner

log = logging.getLogger("watcher")


class _Handler(FileSystemEventHandler):
    def __init__(self, photos_dir: Path, thumbs_dir: Path, thumb_size: int):
        self.photos_dir = photos_dir
        self.thumbs_dir = thumbs_dir
        self.thumb_size = thumb_size
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._worker.start()

    def _enqueue(self, path: str):
        with self._lock:
            self._pending[path] = time.time()

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
                if not fp.exists():
                    scanner.remove_image(self.photos_dir, fp)
                    continue
                if not scanner.is_image(fp):
                    continue
                try:
                    scanner.index_image(self.photos_dir, fp)
                    rel = fp.relative_to(self.photos_dir).as_posix()
                    dst = (self.thumbs_dir / rel).with_suffix(".jpg")
                    scanner.make_thumbnail(fp, dst, self.thumb_size)
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
        scanner.remove_image(self.photos_dir, Path(event.src_path))
        try:
            rel = Path(event.src_path).relative_to(self.photos_dir).as_posix()
            dst = (self.thumbs_dir / rel).with_suffix(".jpg")
            if dst.exists():
                dst.unlink()
        except Exception:
            pass


def start(photos_dir: Path, thumbs_dir: Path, thumb_size: int) -> Observer:
    try:
        photos_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        pass
    handler = _Handler(photos_dir, thumbs_dir, thumb_size)
    obs = Observer()
    obs.schedule(handler, str(photos_dir), recursive=True)
    obs.daemon = True
    obs.start()
    log.info("watching %s", photos_dir)
    return obs
