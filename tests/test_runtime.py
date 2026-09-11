"""The process layer: roles, paths, and the separation between the surfaces.

What this pins is the shape of the merge itself — that one package holds two
apps, that a role decides which listeners open, and above all that the public
app cannot reach a console route. The last one is the whole reason the console
gets its own port instead of a URL prefix, so it is asserted rather than
assumed.
"""

import importlib

import pytest

from aperture import runtime
from aperture.console.app import app as console_app
from aperture.gallery.app import app as public_app


def _settings(**env):
    """A Settings built from an explicit environment, without touching the
    process-wide one the fixtures already resolved."""
    import os
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items()})
    try:
        return runtime.load()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ----- roles ------------------------------------------------------------
@pytest.mark.parametrize("role,public,console,indexer", [
    ("all", True, True, True),
    ("public", True, False, True),
    ("console", False, True, False),
])
def test_roles_decide_what_opens(role, public, console, indexer):
    s = _settings(APERTURE_ROLE=role)
    assert s.runs_public is public
    assert s.runs_console is console
    # The indexer follows the public role, so every shape has exactly one
    # writer on the database.
    assert s.owns_indexer is indexer


def test_console_can_be_switched_off_entirely():
    s = _settings(APERTURE_ROLE="all", CONSOLE_ENABLED="0")
    assert s.runs_public and not s.runs_console


def test_an_unknown_role_refuses_to_start():
    with pytest.raises(SystemExit):
        _settings(APERTURE_ROLE="admin")


def test_console_binds_loopback_unless_told_otherwise():
    import os
    saved = os.environ.pop("CONSOLE_BIND", None)
    try:
        assert runtime.load().console_bind == "127.0.0.1"
    finally:
        if saved is not None:
            os.environ["CONSOLE_BIND"] = saved


# ----- what belongs to whom --------------------------------------------
def test_our_state_never_lives_in_the_photo_tree():
    """The gallery serves files out of photos/.gallery/, so anything we are
    trusted with — the index, credentials, backups, the audit log — has to sit
    under DATA_DIR instead."""
    s = runtime.settings
    for ours in (s.gallery_db, s.console_dir, s.backup_dir):
        with pytest.raises(ValueError):
            ours.relative_to(s.photos_dir)


# ----- the separation ---------------------------------------------------
def _paths(app):
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_the_two_apps_are_not_the_same_object():
    assert public_app is not console_app


def test_no_console_route_is_reachable_on_the_public_app():
    """The console's API lives at /api/... too. Same prefixes, different
    apps — which only works because nothing routes between them."""
    console_only = {"/api/meta", "/api/tree", "/api/album/cfg", "/api/asset",
                    "/api/validate", "/api/health"}
    assert console_only.isdisjoint(_paths(public_app))


def test_the_console_serves_no_gallery_page(client):
    for path in ("/albums", "/stats", "/album/berlin", "/full/berlin/gate.jpg"):
        assert path not in _paths(console_app), path


def test_the_public_app_has_no_writing_route():
    """Phase 02 puts a lock on the console's writes. This asserts the other
    half: on the public app there is nothing to lock, because there is no
    method that could write."""
    methods = set()
    for r in public_app.routes:
        methods |= set(getattr(r, "methods", ()) or ())
    assert methods <= {"GET", "HEAD", "OPTIONS"}


def test_the_server_module_builds_both_configs(monkeypatch):
    server = importlib.import_module("aperture.server")
    monkeypatch.setattr(server, "settings", _settings(APERTURE_ROLE="all"))
    configs = server._configs()
    assert [c.port for c in configs] == [runtime.settings.port,
                                         runtime.settings.console_port]
    # The gallery answers on every interface; the console only where it was
    # pointed, which is loopback unless someone said otherwise.
    assert configs[0].host == "0.0.0.0"
    assert configs[1].host == runtime.settings.console_bind


# ----- the proxy in front ---------------------------------------------
def test_only_loopback_may_speak_for_a_client_by_default():
    import os
    saved = os.environ.pop("FORWARDED_ALLOW_IPS", None)
    try:
        assert runtime.load().forwarded_allow_ips == "127.0.0.1"
    finally:
        if saved is not None:
            os.environ["FORWARDED_ALLOW_IPS"] = saved


def test_both_listeners_trust_the_configured_proxy(monkeypatch):
    """The login throttle and the audit log key on the client address, so
    the console must trust exactly the proxy the gallery trusts."""
    server = importlib.import_module("aperture.server")
    monkeypatch.setattr(server, "settings",
                        _settings(APERTURE_ROLE="all", FORWARDED_ALLOW_IPS="10.0.0.2"))
    configs = server._configs()
    assert [c.forwarded_allow_ips for c in configs] == ["10.0.0.2", "10.0.0.2"]
    assert all(c.proxy_headers for c in configs)
