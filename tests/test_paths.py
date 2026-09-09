"""The path guard on the photo-serving routes.

These four routes (`/image`, `/thumb`, `/preview`, `/full`) read straight off
disk without asking the index, so they are the only place where a URL turns
into a filesystem path unmediated. This file is the executable version of that
promise, and it is the seed of the security suite the console will need: when
the two guards in the tree become one function, these cases move over with it
and gain the write-side ones.
"""

import pytest

ROUTES = ["/image", "/thumb", "/preview", "/full"]

# Every one of these must be refused, whatever the route.
HOSTILE = [
    "../../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "berlin/../../secret.jpg",
    "/etc/passwd",
    "berlin/./../../gate.jpg",
]

# Files that exist inside the tree but are not photographs. The meta folder is
# not a gallery of anything, and a sidecar is not an image.
NOT_PHOTOS = [
    "berlin/.album/album.cfg",
    "berlin/.album/album_en.md",
    "tech/desk.jpg.tags",
    ".gallery/gallery.cfg",
]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("rel", HOSTILE)
def test_traversal_is_refused(client, route, rel):
    r = client.get(f"{route}/{rel}")
    assert r.status_code in (400, 404), (route, rel, r.status_code)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("rel", NOT_PHOTOS)
def test_non_photos_are_not_served(client, route, rel):
    assert client.get(f"{route}/{rel}").status_code == 404, (route, rel)


@pytest.mark.parametrize("route", ROUTES)
def test_a_real_photo_still_works(client, route):
    """The guard is only worth something if it lets the legitimate case
    through — otherwise every test above passes on a broken route."""
    assert client.get(f"{route}/berlin/gate.jpg").status_code == 200, route


def test_the_meta_folder_has_no_listing(client):
    for path in ("/full/berlin/.album/", "/full/.gallery/", "/full/berlin/"):
        assert client.get(path).status_code in (404, 405), path


def test_an_unknown_album_is_a_404_not_a_500(client):
    assert client.get("/full/nope/gate.jpg").status_code == 404


def test_case_and_separator_variants_do_not_slip_through(client):
    """A backslash is a separator on Windows and a legal filename character
    on Linux; neither may become a way out of the album."""
    for rel in ("berlin\\..\\..\\gate.jpg", "berlin%5C..%5Cgate.jpg"):
        assert client.get(f"/full/{rel}").status_code in (400, 404), rel


def test_originals_are_served_byte_for_byte(client, photos_dir):
    """`/full` hands out the file as it lies on disk — that is the contract
    that makes `photos:ro` meaningful."""
    r = client.get("/full/berlin/gate.jpg")
    assert r.content == (photos_dir / "berlin" / "gate.jpg").read_bytes()
