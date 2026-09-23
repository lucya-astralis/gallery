"""The JSON API's shape, pinned.

The API is the one surface with consumers that are not this repo, so it is
held tighter than the pages: every response's key set is asserted in full, not
as a subset. A refactor that renames a field or drops one fails here, which is
the point — the pages can be rearranged freely, the contract cannot.

Values are only asserted where they are derived rather than stored (URLs,
counts, the scope block), because that is where a moved helper would show up.
"""

import pytest

from aperture import scanner

# route -> the exact top-level key set it answers with
SHAPES = {
    "/api": {"base_url", "endpoints", "languages", "limits", "name", "product",
             "product_version", "sorts", "vendor", "vendor_url", "version"},
    "/api/stats": {"albums", "bytes", "bytes_h", "featured", "images", "lang",
                   "span", "tags"},
    "/api/albums": {"albums", "count", "default_sort", "depth", "parent",
                    "sections", "sort"},
    "/api/album/berlin": {"album", "breadcrumbs", "description", "effect", "font",
                          "images", "lang", "photo_tags", "reel", "scope", "sort",
                          "stats", "sub_albums", "theme", "trip"},
    "/api/photos": {"count", "filters", "items", "limit", "offset", "scope",
                    "sort", "total"},
    "/api/photo/berlin/gate.jpg": {"album", "album_url", "breadcrumbs",
                                   "description", "exif", "exif_raw", "featured",
                                   "filename", "height", "lang", "mtime",
                                   "neighbours", "rel_path", "size", "tags",
                                   "taken_at", "urls", "width"},
    "/api/tags": {"count", "items", "scope"},
    "/api/showcase": {"count", "items", "scope", "total"},
}

ALBUM_CARD = {"album", "collection", "count", "cover", "icon", "is_showcase",
              "latest", "name", "sub_count", "tags", "urls"}

PHOTO_ITEM = {"album", "featured", "filename", "height", "mtime", "rel_path",
              "size", "taken_at", "urls", "width"}

PHOTO_URLS = {"api", "api_abs", "full", "full_abs", "page", "page_abs",
              "preview", "preview_abs", "thumb", "thumb_abs"}


@pytest.mark.parametrize("path,keys", sorted(SHAPES.items()))
def test_response_shape(client, path, keys):
    assert set(client.get(path).json()) == keys, path


def test_album_cards_have_one_shape(client):
    for card in client.get("/api/albums").json()["albums"]:
        assert set(card) == ALBUM_CARD, card.get("album")


def test_photo_items_have_one_shape(client):
    items = client.get("/api/photos").json()["items"]
    assert items
    for item in items:
        assert set(item) == PHOTO_ITEM, item["rel_path"]
        assert set(item["urls"]) == PHOTO_URLS, item["rel_path"]


def test_photo_urls_point_at_the_serving_routes(client):
    item = client.get("/api/photos?album=berlin&sort=name_asc").json()["items"][0]
    rel = item["rel_path"]
    # the file URLs carry the photo's version stamp (scanner.media_url)
    stamp = "?v=" + scanner.photo_stamp(rel)
    assert item["urls"]["thumb"] == "/thumb/" + rel + stamp
    assert item["urls"]["preview"] == "/preview/" + rel + stamp
    assert item["urls"]["full"] == "/full/" + rel + stamp
    assert item["urls"]["page"] == "/image/" + rel
    for key in ("thumb", "preview", "full", "page", "api"):
        assert item["urls"][key + "_abs"].endswith(item["urls"][key])


def test_counts_agree_with_the_index(client, indexed):
    total = indexed["result"]["indexed"]
    assert client.get("/api/photos").json()["total"] == total
    assert client.get("/api/stats").json()["images"] == total


def test_collection_scope_widens_the_album(client):
    """berlin holds three photos and a sub-album with two more. `subtree`
    is the switch between "this folder" and "this folder and below"."""
    own = client.get("/api/photos?album=berlin").json()
    sub = client.get("/api/photos?album=berlin&subtree=true").json()
    assert own["total"] == 3
    assert sub["total"] == 5


def test_tags_come_from_the_sidecar(client):
    names = {t["name"] for t in client.get("/api/tags").json()["items"]}
    assert {"workspace", "keyboard"} <= names


def test_gps_never_reaches_the_api(client):
    """HIDE_GPS is on for the fixture, as it is by default. Coordinates must
    not appear under any key of the photo payload."""
    body = client.get("/api/photo/berlin/gate.jpg").text.lower()
    for needle in ("gpslatitude", "gpslongitude", "gpsinfo"):
        assert needle not in body


def test_the_api_names_the_product(client):
    meta = client.get("/api").json()
    assert meta["vendor"] == "lucya.systems"
    assert meta["product"] and meta["product_version"]


def test_limits_are_enforced(client):
    limit = client.get("/api").json()["limits"]["max_limit"]
    assert client.get(f"/api/photos?limit={limit * 10}").json()["limit"] == limit


def test_preflight_is_answered(client):
    r = client.options("/api/photos")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
