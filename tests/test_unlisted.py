"""Unlisted albums: reachable by their address, absent from every listing.

`unlisted = true` in an album.cfg takes the album and everything under it out
of /albums, a parent's sub-album cards, the search, /stats, the welcome
counters and the API's lists -- while its page, its photos and a /s/ link to
it keep working, marked noindex. It is a way to share an album with the people
who have the link, not a lock.

The fixture tree is left as it is: each test here switches `phone` (top
level) and `berlin/mitte` (a sub-album) to unlisted for itself and puts both
files back when it is done.
"""

import pytest

from aperture import albums, db


@pytest.fixture
def unlisted(indexed, photos_dir):
    phone = photos_dir / "phone" / ".album" / "album.cfg"
    mitte = photos_dir / "berlin" / "mitte" / ".album" / "album.cfg"
    mitte_before = mitte.read_text(encoding="utf-8")
    phone.parent.mkdir(parents=True, exist_ok=True)
    phone.write_text("unlisted = true\n", encoding="utf-8")
    mitte.write_text(mitte_before + "unlisted = yes\n", encoding="utf-8")
    albums.forget_unlisted()
    yield
    phone.unlink()
    phone.parent.rmdir()
    mitte.write_text(mitte_before, encoding="utf-8")
    albums.forget_unlisted()


def _total() -> int:
    return db.conn().execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]


HIDDEN = 3          # phone/portrait.jpg + berlin/mitte/dome.jpg, river.jpg


def test_the_flag_covers_the_subtree(unlisted):
    assert albums.unlisted_roots() == ["berlin/mitte", "phone"]
    assert albums.is_unlisted("phone") and albums.is_unlisted("berlin/mitte")
    assert not albums.is_unlisted("berlin") and not albums.is_unlisted("tech")


def test_an_unlisted_album_is_not_listed(client, unlisted):
    index = client.get("/albums").text
    assert 'href="/album/phone"' not in index
    assert 'href="/album/tech"' in index
    berlin = client.get("/album/berlin").text
    assert 'href="/album/berlin/mitte"' not in berlin
    top = {a["album"] for a in client.get("/api/albums").json()["albums"]}
    assert "phone" not in top and "berlin" in top
    assert client.get("/api/albums", params={"parent": "berlin"}).json()["albums"] == []


def test_its_address_still_answers_and_asks_not_to_be_indexed(client, unlisted):
    for path in ("/album/phone", "/image/phone/portrait.jpg",
                 "/album/berlin/mitte", "/image/berlin/mitte/dome.jpg"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert '<meta name="robots" content="noindex">' in page.text, path
    assert '<meta name="robots"' not in client.get("/album/berlin").text
    # asked for by name, the API answers as the page does
    assert client.get("/api/photos", params={"album": "phone"}).json()["total"] == 1
    inside = client.get("/api/album/berlin/mitte", params={"images": 1}).json()
    assert inside["images"]["total"] == 2


def test_counts_and_covers_leave_it_out(client, unlisted):
    assert client.get("/api/stats").json()["images"] == _total() - HIDDEN
    assert client.get("/api/photos", params={"limit": 200}).json()["total"] == _total() - HIDDEN
    berlin = next(a for a in client.get("/api/albums").json()["albums"] if a["album"] == "berlin")
    assert berlin["count"] == 3 and berlin["sub_count"] == 0
    # a parent walked as its whole subtree does not reach into it either
    wide = client.get("/api/album/berlin", params={"images": 1, "subtree": 1}).json()
    assert {i["rel_path"] for i in wide["images"]["items"]} == {
        "berlin/gate.jpg", "berlin/wall.jpg", "berlin/tv-tower.jpg"}


def test_search_and_stats_leave_it_out(client, unlisted):
    assert client.get("/api/photos", params={"q": "portrait"}).json()["total"] == 0
    found = client.get("/search", params={"q": "mitte"}).text
    assert "dome.jpg" not in found and 'href="/album/berlin/mitte"' not in found
    stats = client.get("/stats").text
    assert 'href="/album/phone"' not in stats


def test_listed_again_once_the_flag_is_gone(client, indexed, photos_dir):
    # runs after the module fixture has put the files back (it is not used here)
    assert not albums.is_unlisted("phone")
    assert client.get("/api/stats").json()["images"] == _total()
