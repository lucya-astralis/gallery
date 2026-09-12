"""What the console's home screen is made of.

The dashboard invents nothing: it asks the same four routes the rest of the
console does, and shows what they already knew. These pin the parts that had
no reader before it — the audit log, and whether an album has been written
about at all.
"""

import json

from aperture.runtime import settings


def node(tree, path):
    """One album out of the /api/tree payload, by its path."""
    stack = [tree]
    while stack:
        item = stack.pop()
        if item.get("path") == path:
            return item
        stack.extend(item.get("children") or [])
    raise AssertionError("no album %r in the tree" % path)


def test_the_tree_says_which_albums_are_unwritten(console):
    """The other half of "what still needs doing": berlin carries album_en.md
    and album_de.md, tech carries none."""
    tree = console.get("/api/tree").json()["root"]
    assert node(tree, "berlin")["has_desc"] is True
    assert node(tree, "tech")["has_desc"] is False
    assert node(tree, "tech")["has_cfg"] is True        # a cfg, but nothing written


def test_a_write_comes_back_out_of_the_audit_log(console, photos_dir):
    """Every save has written a line since the door went in. Nothing read it
    back, so the console could not say what had just happened in it."""
    before = console.get("/api/audit").json()["entries"]
    res = console.put("/api/album/cfg",
                      json={"album": "tech", "values": {"name": "Tech"}})
    assert res.status_code == 200, res.text

    entries = console.get("/api/audit").json()["entries"]
    assert len(entries) == len(before) + 1
    newest = entries[0]                                  # newest first
    assert newest["action"] == "album.cfg"
    assert newest["target"] == "tech/.album/album.cfg"
    assert newest["ts"] and newest["after"]
    # It says what changed, never the content that changed.
    assert "Tech" not in json.dumps(newest)


def test_the_log_is_read_from_the_tail_and_survives_a_bad_line(console):
    """A report, not a parser: a line that is not JSON is skipped rather than
    raised on, or one bad write would take the whole screen down."""
    log = settings.console_dir / "audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
    entries = console.get("/api/audit").json()["entries"]
    assert isinstance(entries, list)
    assert all("action" in e for e in entries)


def test_the_limit_is_bounded(console):
    assert len(console.get("/api/audit?limit=1").json()["entries"]) <= 1
    # A caller asking for a million gets the cap, not a million.
    assert len(console.get("/api/audit?limit=100000").json()["entries"]) <= 200


def test_home_needs_no_route_of_its_own(console):
    """Everything on the screen comes from routes that already existed for
    something else — which is why it cannot drift from what the rest of the
    console shows."""
    for path in ("/api/ops/status", "/api/validate", "/api/tree", "/api/audit"):
        assert console.get(path).status_code == 200, path
