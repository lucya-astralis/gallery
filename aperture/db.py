"""The sqlite index, one connection PER THREAD.

The gallery serves every route from a `def` handler, so FastAPI runs them on
the threadpool while the scanner, the watcher's drain and the control loop each
have a thread of their own. All of them go through `conn()`.

They used to get the SAME connection (opened with `check_same_thread=False`),
and that is a connection-level object being driven from a dozen threads at
once. Two things came out of it:

  * A reader's SELECT ran *inside* whatever transaction a writer had open, so
    any write done as more than one statement was observable half-finished — a
    photo mid-retag read as untagged, an album mid-recompute as unfeatured. On
    a 4-second probe (writer re-tagging one photo in a loop, three readers
    counting its tag rows) 44% of reads returned the wrong count.
  * The same probe also raised `sqlite3.InterfaceError: bad parameter or other
    API misuse` and handed back `None` from `fetchone()`, because concurrent
    statements on one connection walk over each other's cursor state. In a
    request that is a 500, not a stale number.

A connection per thread ends both. WAL (below) is what makes it the right
answer rather than a trade: readers get a consistent snapshot of the last
COMMIT and never block the writer, and the writer never blocks them. Writers
are still serialised by `lock()`, because sqlite allows one at a time and the
CLI writes from a second process.
"""

import json
import sqlite3
import threading
from pathlib import Path

from . import capture

# Writers only. Readers hold no lock — with a connection each they don't need
# one, which is the whole point of the split.
_lock = threading.Lock()
# Held while the database is opened for the first time, so two threads
# arriving together do not both run the schema migrations.
_init_lock = threading.Lock()
# Where init() put the database. Doubles as "has init() run yet".
_db_path: Path | None = None
# The per-thread connection. A thread that ends drops its reference and the
# connection is closed by the GC, so nothing has to be handed back.
_local = threading.local()

# Seconds a connection waits for another one's write lock before giving up.
# In-process writers queue on _lock instead, so this covers the CLI writing
# from its own process while the server is running.
BUSY_TIMEOUT = 10.0

SCHEMA = """
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


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open one connection with the settings every one of them needs."""
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    # PER CONNECTION and not remembered in the file: a connection that skips
    # this one silently loses ON DELETE CASCADE, so deleting an image would
    # leave its image_tags rows behind.
    conn.execute("PRAGMA foreign_keys = ON")
    # A property of the DATABASE, so in practice only the first connection
    # after the file is created changes anything. Asked for every time anyway,
    # because everything above assumes it.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# The capture facts (capture.py) and their column types. Their own columns so
# a search never parses exif_json; scanner.index_image writes them.
CAPTURE_COLUMNS = (("camera", "TEXT"), ("lens", "TEXT"), ("focal", "INTEGER"),
                   ("aperture", "REAL"), ("iso", "INTEGER"))


def migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations: the columns a newer version reads, added to a
    database an older one created. Safe to run on every start; the caller
    commits."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(images)").fetchall()}
    if "is_showcase" not in existing_cols:
        conn.execute("ALTER TABLE images ADD COLUMN is_showcase INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_images_showcase ON images(is_showcase)")

    added = [col for col, kind in CAPTURE_COLUMNS if col not in existing_cols]
    for col, kind in CAPTURE_COLUMNS:
        if col in added:
            conn.execute(f"ALTER TABLE images ADD COLUMN {col} {kind}")
    if added:
        # Rows indexed before the columns existed would otherwise stay
        # unsearchable until their file changed -- the scan skips a photo
        # whose mtime says nothing happened. The EXIF is already stored, so
        # the facts are read out of it here, once.
        for row in conn.execute("SELECT id, exif_json FROM images").fetchall():
            try:
                exif = json.loads(row["exif_json"]) if row["exif_json"] else {}
            except (ValueError, TypeError):
                exif = {}
            fact = capture.facts(exif)
            conn.execute(
                "UPDATE images SET camera = ?, lens = ?, focal = ?, aperture = ?, iso = ? "
                "WHERE id = ?",
                (fact["camera"], fact["lens"], fact["focal"], fact["aperture"], fact["iso"],
                 row["id"]))


def init(data_dir: Path) -> sqlite3.Connection:
    """Create the database if it isn't there, bring the schema up to date, and
    hand back this thread's connection. conn() calls it on first use; the
    indexer also calls it at startup, so a migration runs before the first
    request rather than inside one."""
    global _db_path
    data_dir.mkdir(parents=True, exist_ok=True)
    _db_path = data_dir / "gallery.db"
    conn = _connect(_db_path)

    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    _local.conn = conn
    return conn


def conn() -> sqlite3.Connection:
    """This thread's connection, opened on first use. Never share the object
    across threads — sqlite3 refuses it, which is deliberate: the guarantees
    at the top of this file are exactly what that refusal protects."""
    if _db_path is None:
        # Nothing has to remember to open it first. A console-only process
        # never runs the indexer's startup, and every read it made of the
        # index used to fail here.
        with _init_lock:
            if _db_path is None:
                from .runtime import settings
                return init(settings.data_dir)
    existing = getattr(_local, "conn", None)
    if existing is not None:
        return existing
    fresh = _connect(_db_path)
    _local.conn = fresh
    return fresh


def lock():
    """Held around WRITES. sqlite takes one writer at a time; queueing them
    here turns a lost race into a short wait instead of a `database is locked`.
    Readers must NOT take it."""
    return _lock
