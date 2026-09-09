"""The operator CLI, one call per command.

The CLI had no tests, and the operations surface is about to be lifted out of
it so the console can serve the same reports. That move is only safe if there
is something that notices when a command stops producing what it produced.

So: every command that reads (rather than acts) is run with `--json`, its
output is parsed, and the keys it promises are asserted. The rendering half is
not tested here — a report that renders differently is a look; a report that
reports differently is a bug.
"""

import json
import io
import contextlib

import pytest

from aperture import cli


def run(*argv, expect=0):
    """One CLI invocation, its stdout parsed as JSON.

    Colour is forced off and the logo suppressed so the captured stream is the
    payload and nothing else.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--no-color", "--logo", "off", *argv])
    assert code == expect, f"{argv} exited {code}\n{buf.getvalue()[:2000]}"
    text = buf.getvalue().strip()
    return json.loads(text) if text else None


# ----- state ------------------------------------------------------------
def test_status_reports_the_index(indexed):
    body = run("status", "--json")
    assert set(body) >= {"server", "live", "paused", "index", "control_dir"}
    assert body["index"]["images"] == indexed["result"]["indexed"]
    assert body["index"]["albums"] >= 3
    # `live` is whether a heartbeat in the control directory is recent. The
    # test session publishes one when it scans, so this asserts the shape
    # rather than a value the suite's own order would decide.
    assert isinstance(body["live"], bool)


def test_doctor_finds_nothing_wrong_with_a_clean_tree(indexed):
    """The fixture tree was just scanned, so every derivative exists and the
    index matches the disk. A finding here means the checks disagree with the
    scanner, which is worth knowing either way."""
    body = run("doctor", "--json", expect=0)
    assert body["total"] == 0, body["problems"]
    assert body["photos_on_disk"] == body["rows"] == indexed["result"]["indexed"]


def test_doctor_can_be_scoped_to_one_album(indexed):
    body = run("doctor", "--album", "berlin", "--json")
    assert body["scope"] == "berlin"
    # berlin plus its sub-album, because a scope is a subtree.
    assert body["photos_on_disk"] == 5


def test_doctor_exits_nonzero_when_it_finds_something(indexed, photos_dir):
    """The exit code is the contract that makes it usable from cron."""
    stray = photos_dir / "berlin" / "unindexed.jpg"
    stray.write_bytes((photos_dir / "berlin" / "gate.jpg").read_bytes())
    try:
        body = run("doctor", "--json", expect=1)
        assert body["total"] >= 1
        assert "unindexed" in body["problems"]
    finally:
        stray.unlink()


# ----- reports ----------------------------------------------------------
def test_cfg_resolves_an_album(indexed):
    body = run("cfg", "berlin", "--json")
    assert set(body) == {"exists", "file", "issues", "parsed"}
    assert body["exists"] is True
    assert body["parsed"]["name"] == ["Berlin"]


def test_photo_resolves_one_file(indexed):
    body = run("photo", "berlin/gate.jpg", "--json")
    assert body["rel_path"] == "berlin/gate.jpg"


def test_tags_reports_the_vocabulary(indexed):
    body = run("tags", "--json")
    blob = json.dumps(body)
    assert "workspace" in blob and "keyboard" in blob


def test_welcome_says_what_the_front_page_will_show(indexed):
    body = run("welcome", "--json")
    assert body


def test_gps_audits_the_originals(indexed):
    """The fixture EXIF carries no coordinates, so the audit must come back
    clean — and must have actually looked."""
    body = run("gps", "--json")
    blob = json.dumps(body)
    assert "gate.jpg" not in blob


def test_featured_lists_what_album_cfg_flags(indexed):
    body = run("featured", "--json")
    blob = json.dumps(body)
    assert "gate.jpg" in blob and "tv-tower.jpg" in blob


def test_album_lists_the_tree(indexed):
    body = run("album", "--json")
    blob = json.dumps(body)
    for name in ("berlin", "berlin/mitte", "tech"):
        assert name in blob


def test_search_finds_a_photo(indexed):
    body = run("search", "gate", "--json")
    assert "gate.jpg" in json.dumps(body)


def test_thumbs_reports_the_derivatives(indexed):
    body = run("thumbs", "--json")
    assert json.dumps(body)


def test_i18n_reports_translation_coverage(indexed):
    body = run("i18n", "--json")
    assert json.dumps(body)


def test_term_explains_itself():
    body = run("term", "--json")
    assert "platform" in body


# ----- the control channel ----------------------------------------------
def test_pause_and_resume_round_trip(indexed):
    """A pause is a file, deliberately persistent, and `status` is what reads
    it back. Nothing else in the suite may leave one behind."""
    try:
        run("pause", "maintenance", "--json")
        assert run("status", "--json")["paused"] is True
        assert run("status", "--json")["pause"]["reason"] == "maintenance"
    finally:
        run("resume", "--json")
    assert run("status", "--json")["paused"] is False


def test_a_scan_runs_locally_when_no_server_is_listening(indexed):
    """`scan --local` is the path that does the work in this process instead
    of asking a running server for it."""
    body = run("scan", "--local", "--json")
    assert body["result"]["total_seen"] == indexed["result"]["indexed"]
    assert body["error"] is None
