import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def init(data_dir: Path) -> sqlite3.Connection:
    global _conn
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "gallery.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album TEXT NOT NULL,
            filename TEXT NOT NULL,
            rel_path TEXT NOT NULL UNIQUE,
            mtime REAL NOT NULL,
            size INTEGER,
            width INTEGER,
            height INTEGER,
            exif_json TEXT,
            taken_at TEXT,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_images_album ON images(album);
        CREATE INDEX IF NOT EXISTS idx_images_taken ON images(taken_at);

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL COLLATE NOCASE
        );

        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (image_id, tag_id),
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    _conn = conn
    return conn


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("DB not initialised")
    return _conn


def lock():
    return _lock
