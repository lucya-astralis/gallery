"""The photo whose pixels lie: EXIF Orientation 5-8.

`phone/portrait.jpg` in the fixture tree is a 900x600 buffer tagged
Orientation 6 — a landscape image that is meant to be shown as a 600x900
portrait, which is how every portrait from a phone arrives.

Nothing turns it at display time. The derivatives carry only Software and the
credit (scanner._derivative_exif), so they have no Orientation tag for a
browser to honour, and the tiles size themselves from the indexed width and
height. Both therefore have to be upright when they are WRITTEN — and, the
part that actually shows on the page, they have to agree with each other.
"""

import io

import pytest
from PIL import Image

from aperture import scanner
from aperture.runtime import settings

ROTATED = "phone/portrait.jpg"
UPRIGHT = "berlin/gate.jpg"


def dims(client, rel):
    item = client.get("/api/photo/" + rel).json()
    return item["width"], item["height"]


def served(client, route, rel):
    r = client.get("/%s/%s" % (route, rel))
    assert r.status_code == 200, r.text[:200]
    with Image.open(io.BytesIO(r.content)) as img:
        return img.size


def test_the_index_stores_the_photo_as_it_is_shown(client, indexed):
    assert dims(client, ROTATED) == (600, 900)


def test_a_photo_with_nothing_to_correct_is_untouched(client, indexed):
    assert dims(client, UPRIGHT) == (900, 600)


@pytest.mark.parametrize("route", ["thumb", "preview"])
def test_every_derivative_is_written_upright(client, indexed, route):
    width, height = served(client, route, ROTATED)
    assert height > width, "%s came out sideways: %dx%d" % (route, width, height)


@pytest.mark.parametrize("rel", [ROTATED, UPRIGHT])
@pytest.mark.parametrize("route", ["thumb", "preview"])
def test_the_tile_and_the_file_in_it_agree(client, indexed, route, rel):
    """The one that shows: a tile sized portrait from the index with a
    landscape image dropped into it is the letterboxed card nobody ordered."""
    w, h = dims(client, rel)
    dw, dh = served(client, route, rel)
    assert (w > h) == (dw > dh)


def test_the_derivative_carries_no_orientation_of_its_own(client, indexed):
    """Which is why the pixels have to be right: there is no tag left for a
    browser to act on, by design — see scanner._derivative_exif."""
    r = client.get("/preview/" + ROTATED)
    with Image.open(io.BytesIO(r.content)) as img:
        assert img.getexif().get(0x0112) in (None, 1)


def test_the_scanner_reads_the_tag_it_acts_on(indexed):
    assert scanner._orientation({"Orientation": 6}) == 6
    assert scanner._orientation({"Orientation": 6.0}) == 6      # EXIF rationals
    assert scanner._orientation({}) == 1                        # no tag at all
    assert scanner._orientation({"Orientation": "portrait"}) == 1
    assert 6 in scanner._SWAPPED_ORIENTATIONS and 1 not in scanner._SWAPPED_ORIENTATIONS


def test_a_rebuilt_thumbnail_stays_upright(indexed, photos_dir):
    """`thumbs --rebuild --all` is the migration path for derivatives written
    before this rule, so the rebuild must produce the same upright file."""
    src = photos_dir / ROTATED
    dst = (settings.thumbs_dir / ROTATED).with_suffix(scanner.THUMB_EXT)
    dst.unlink(missing_ok=True)
    assert scanner.make_thumbnail(src, dst, settings.thumb_size)
    with Image.open(dst) as img:
        assert img.height > img.width
