"""Test fixtures: a throwaway photo tree, indexed, served by the real app.

The app reads its paths from the environment AT IMPORT TIME, so everything
here happens in a deliberate order: build the tree, point the environment at
it, and only then import the package. A conftest is imported before any test
module, which is what makes that order reliable.

The tree is small but not trivial — a nested album, a curated cover, per
-language descriptions, a tag sidecar, a photo with real EXIF — because the
routes under test resolve all of those. Nothing here touches the operator's
own `photos/`.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

# ----- the tree ---------------------------------------------------------
ROOT = Path(tempfile.mkdtemp(prefix="aperture-tests-"))
PHOTOS = ROOT / "photos"
THUMBS = ROOT / "thumbnails"
PREVIEWS = ROOT / "previews"
DATA = ROOT / "data"

# album -> (filename, taken_at, size). The dates are spread over two years so
# the timeline chart on /stats has more than one bucket to draw.
TREE = {
    "berlin": [
        ("gate.jpg", "2025:12:04 09:12:00", (900, 600)),
        ("wall.jpg", "2025:12:04 17:40:00", (600, 900)),
        ("tv-tower.jpg", "2025:12:05 11:05:00", (800, 800)),
    ],
    "berlin/mitte": [
        ("dome.jpg", "2025:12:05 14:20:00", (1200, 800)),
        ("river.jpg", "2025:12:05 15:00:00", (800, 1200)),
    ],
    "tech": [
        ("desk.jpg", "2026:02:11 20:15:00", (1000, 750)),
        ("rack.jpg", "2026:02:12 08:00:00", (750, 1000)),
    ],
    # The phone case: a landscape buffer that EXIF says is a quarter turn off,
    # which is how every portrait from a phone arrives. See ORIENTED below and
    # tests/test_orientation.py.
    "phone": [
        ("portrait.jpg", "2026:03:02 18:30:00", (900, 600)),
    ],
}

# album -> EXIF Orientation for its photos. 1 means "as it lies"; 6 is the
# quarter turn a phone writes when it is held upright.
ORIENTED = {"phone": 6}

GALLERY_CFG = """\
# fixture gallery.cfg
site_name = Fixture Archive
welcome = showcase
album_order = berlin, tech
album_sort = curated
accent = #616ef3
"""

BERLIN_CFG = """\
name = Berlin
tags = city, winter
cover = gate.jpg
featured = gate.jpg, tv-tower.jpg
accent = #d2694a
"""

MITTE_CFG = """\
name = Mitte
collection = 0
"""

TECH_CFG = """\
name = Tech
featured = desk.jpg
"""


def _jpeg(path: Path, size, taken_at: str, orientation: int = 1) -> None:
    """A real JPEG with a real DateTimeOriginal — the scanner reads both."""
    w, h = size
    img = Image.new("RGB", (w, h))
    # A cheap gradient: enough pixel variance that thumbnailing is meaningful
    # and the file is not a single flat DC block.
    px = img.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            c = ((x * 255) // w, (y * 255) // h, ((x + y) * 255) // (w + h))
            for dy in range(min(4, h - y)):
                for dx in range(min(4, w - x)):
                    px[x + dx, y + dy] = c
    exif = img.getexif()
    exif[0x0112] = orientation          # Orientation
    exif[0x0110] = "Fixture Cam"        # Model
    exif[0x010F] = "Fixture Optics"     # Make
    # The capture facts live in the Exif sub-IFD. Pillow only writes that IFD
    # when it is assigned as a dict (edits to a get_ifd() copy are dropped on
    # save), and a rational tag needs an IFDRational, not a tuple. The fixture
    # used to do both the other way, so its photos carried neither a date nor
    # an aperture.
    exif[0x8769] = {                    # ExifIFD
        0x9003: taken_at,               # DateTimeOriginal
        0x829A: IFDRational(1, 250),    # ExposureTime
        0x829D: IFDRational(28, 10),    # FNumber
        0x8827: 400,                    # ISO
        0x920A: IFDRational(35, 1),     # FocalLength
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=70, exif=exif)


def _build_tree() -> None:
    for album, files in TREE.items():
        for name, taken, size in files:
            _jpeg(PHOTOS / album / name, size, taken, ORIENTED.get(album, 1))

    meta = PHOTOS / ".gallery"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "gallery.cfg").write_text(GALLERY_CFG, encoding="utf-8")

    for album, cfg in (("berlin", BERLIN_CFG), ("berlin/mitte", MITTE_CFG),
                       ("tech", TECH_CFG)):
        d = PHOTOS / album / ".album"
        d.mkdir(parents=True, exist_ok=True)
        (d / "album.cfg").write_text(cfg, encoding="utf-8")

    berlin = PHOTOS / "berlin" / ".album"
    (berlin / "album_en.md").write_text(
        "Three days in **Berlin**, mostly in the cold.\n", encoding="utf-8")
    (berlin / "album_de.md").write_text(
        "Drei Tage in **Berlin**, überwiegend in der Kälte.\n", encoding="utf-8")

    # A tag sidecar, the format the scanner picks up next to an image.
    (PHOTOS / "tech" / "desk.jpg.tags").write_text("workspace\nkeyboard\n", encoding="utf-8")


_build_tree()

os.environ.update(
    PHOTOS_DIR=str(PHOTOS),
    THUMBS_DIR=str(THUMBS),
    PREVIEWS_DIR=str(PREVIEWS),
    DATA_DIR=str(DATA),
    THUMB_SIZE="240",
    PREVIEW_SIZE="640",
    SCAN_INTERVAL="0",
    ENABLE_WATCHER="0",
    HIDE_GPS="1",
    STRIP_GPS="1",
    APERTURE_ROLE="all",
    CONSOLE_BIND="127.0.0.1",
)

# Import order matters — see the module docstring.
from aperture import db, indexer  # noqa: E402
from aperture.gallery.app import app as gallery_app  # noqa: E402
from aperture.runtime import settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def indexed():
    """The fixture tree, walked once, exactly like a startup scan does it —
    but synchronously, so a test never races the indexer."""
    db.init(settings.data_dir)
    summary = indexer.run_scan(trigger="test")
    assert summary is not None and summary["error"] is None, summary
    assert summary["result"]["indexed"] == sum(len(v) for v in TREE.values())
    return summary


@pytest.fixture(scope="session")
def client(indexed):
    """The public app. Deliberately NOT used as a context manager: entering it
    would run the lifespan, which starts the scanner thread and the control
    loop — background work a route test has no use for."""
    return TestClient(gallery_app)


@pytest.fixture
def console(indexed):
    """The console app, with its door open — a loopback bind and no password,
    which is what every test that is not about the door wants. Function-scoped
    and reset each time, because the tests that DO set a password share this
    process with the ones that do not."""
    from aperture.console import security
    from aperture.console.app import app as console_app
    security.clear_password()
    security.reset()
    return TestClient(console_app)


@pytest.fixture(scope="session")
def photos_dir():
    return PHOTOS
