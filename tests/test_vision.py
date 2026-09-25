"""Vision's plumbing, with a stand-in model: the scan fills the vectors, the
search shows what looks like the words as a group of its own, one switch
leaves it out, the filters still hold, and a download that does not match
its checksum is never kept. The real model's taste is not tested here."""

import hashlib

import numpy as np
import pytest

from aperture import db, scanner, vision
from aperture.runtime import settings


class FakeModel:
    """Each picture's vector is a hash of its pixels; a text is whatever
    vector the test pins to it (or a random one)."""

    def __init__(self):
        self.texts = {}

    def embed_images(self, images):
        out = []
        for img in images:
            seed = int(hashlib.sha256(img.convert("RGB").tobytes()).hexdigest()[:8], 16)
            out.append(np.random.default_rng(seed).standard_normal(vision.DIM))
        m = np.array(out, np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def embed_text(self, text):
        v = self.texts.get(text)
        if v is None:
            v = np.random.default_rng(1).standard_normal(vision.DIM).astype(np.float32)
            v /= np.linalg.norm(v)
        return v


@pytest.fixture
def model(indexed):
    fake = FakeModel()
    vision.set_backend(fake)
    c = db.conn()
    for (rel,) in c.execute("SELECT rel_path FROM images").fetchall():
        thumb = (settings.thumbs_dir / rel).with_suffix(scanner.THUMB_EXT)
        assert scanner.read_vision(rel, thumb), rel
    yield fake
    vision.set_backend(None)
    with db.lock():
        c.execute("UPDATE images SET vision = NULL")
        c.commit()


def _pin(fake, text, rel):
    """Make `text` look exactly like the photo at `rel`."""
    blob = db.conn().execute("SELECT vision FROM images WHERE rel_path = ?", (rel,)).fetchone()[0]
    fake.texts[text] = vision.unpack(blob)


def _rels():
    return [r[0] for r in db.conn().execute("SELECT rel_path FROM images ORDER BY rel_path")]


def test_off_by_default_and_nothing_on_the_page(indexed, client):
    assert settings.vision is False
    assert vision.backend() is None
    body = client.get("/search", params={"q": "anything"}).text
    assert "search-vision" not in body and "search-group--look" not in body


def test_the_scan_gives_every_photo_a_vector(model):
    rows = db.conn().execute("SELECT vision FROM images").fetchall()
    assert rows and all(r[0] and len(r[0]) == vision.DIM * 2 for r in rows)


def test_a_search_finds_what_looks_like_the_words(model, client):
    target = _rels()[0]
    _pin(model, "zzlooks", target)
    body = client.get("/search", params={"q": "zzlooks"}).text
    assert "search-group--look" in body
    assert target.rsplit("/", 1)[-1] in body
    assert 'aria-pressed="true"' in body            # the switch is on


def test_one_switch_leaves_it_out_and_the_links_keep_it(model, client):
    target = _rels()[0]
    _pin(model, "zzlooks", target)
    body = client.get("/search", params={"q": "zzlooks", "vision": "off"}).text
    assert "search-group--look" not in body
    assert 'aria-pressed="false"' in body
    # a search with results: its facets and its sort carry the switch along
    listing = client.get("/search", params={"q": "tech", "vision": "off"}).text
    assert "vision=off" in listing.split("search-facets", 1)[1]
    assert "&amp;vision=off" in listing              # the sort menu (escaped in the href)


def test_the_filters_still_hold(model, client):
    rels = _rels()
    outside = next(r for r in rels if not r.startswith("tech/"))
    _pin(model, "zzlooks", outside)
    ranked = vision.rank(db.conn(), "zzlooks", allowed={
        r[0] for r in db.conn().execute("SELECT id FROM images WHERE album = 'tech'")})
    ids = {r[0] for r in db.conn().execute("SELECT id FROM images WHERE album = 'tech'")}
    assert all(i in ids for i, _ in ranked)


def test_what_the_words_found_is_not_shown_twice(model, client):
    rel = _rels()[0]
    name = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    _pin(model, name, rel)                           # the name finds it AND it looks like it
    body = client.get("/search", params={"q": name}).text
    look = body.split("search-group--look", 1)[1] if "search-group--look" in body else ""
    assert rel not in look


def test_a_download_that_does_not_match_is_thrown_away(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "models_dir", lambda: tmp_path / "m")
    monkeypatch.setattr(vision, "FILES", [(("r", "v"), "p", "x.onnx", 5, "0" * 64)])

    class Res:
        def __init__(self):
            self.data = [b"hello", b""]

        def read(self, n):
            return self.data.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(vision.urllib.request, "urlopen", lambda *a, **k: Res())
    assert vision.install() is False
    assert "checksum" in vision._state["error"]
    assert not (tmp_path / "m" / "x.onnx").exists()
    assert not (tmp_path / "m" / "x.onnx.part").exists()
    vision._state["error"] = None


def test_the_console_reports_it(indexed, console):
    st = console.get("/api/ops/vision").json()
    assert st["enabled"] is False and st["installed"] is False
    assert st["size_mb"] > 200 and st["total"] > 0
