"""One registry for what a cfg file may say, and one grammar for how.

Before Aperture the gallery and the configurator each kept their own copy of
both. The copies were meant to be identical, and by the time they became one
product they were not: the console refused `.gif`/`.jpg` icons the gallery
serves, and both validators flagged `reel = hide`, which the gallery honours.

This file keeps that from happening again. It asserts three things:

  * every module that needs the vocabulary is holding THE registry, not a
    list that happens to be equal today;
  * no module grows its own list again -- checked on the source, because an
    equal copy is exactly the kind that passes an equality test and drifts
    next month;
  * every key is fully described, so adding one is the one edit the schema's
    docstring promises.
"""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aperture import albums, cfgio, checks, paths, schema, theme
from aperture.console import app as console_mod
from aperture.console import security
from aperture.runtime import settings

ALL_KEYS = frozenset(schema.ALBUM_KEYS) | frozenset(schema.GALLERY_KEYS)
PACKAGE = Path(schema.__file__).parent


# ============================================================
# one registry
# ============================================================
def test_main_py_is_gone_and_nothing_imports_it():
    """main.py held the gallery's grammar aliases, key sets and every helper in
    one 4 400-line file. It was split into aperture/{config,albums,photos,
    theme,branding,marks,trips,welcome,indexer}.py and aperture/gallery/; a
    reappearing import would mean something still reaches for the old home."""
    assert not (PACKAGE / "main.py").exists()
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                assert not (node.module in ("main", "aperture.main")
                            or (node.module in (None, "aperture") and "main" in names)), path


# A name a module gives a schema value BECAUSE IT MEANS SOMETHING ELSE there,
# and the CLI's four report verbs, which read as verbs in a command body and
# are bound once in aperture/cli/render.py, where that file says so.
ALIASES_ALLOWED = {("branding.py", "BRAND_ASSET_TYPES"),
                   ("render.py", "out"), ("render.py", "kv"),
                   ("render.py", "head"), ("render.py", "hint")}


def test_no_module_keeps_a_second_name_for_something():
    """`X = other.X` at module level is how a copy starts.

    Both kinds went wrong here already. A snapshot of a setting: the console
    bound READ_ONLY at import, so a read-only console refused the operations
    panel and wrote cfg files anyway. And a shortcut to another module: the
    CLI's sixteen `_x = ops.x`, bound to avoid a rename diff, which left the
    file talking about code that had moved out of it years of edits ago.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)):
                continue
            name = node.targets[0].id
            if (path.name, name) in ALIASES_ALLOWED:
                continue
            offenders.append("%s:%d  %s = %s.%s" % (
                path.relative_to(PACKAGE), node.lineno, name,
                node.value.value.id, node.value.attr))
    assert not offenders, "say it once, where it lives:\n" + "\n".join(offenders)


def test_no_function_shadows_a_module_the_file_imports():
    """A local named like an imported module turns every use of that module in
    the same function into an UnboundLocalError -- a crash the module's own
    line never sees coming.

    `dash` did exactly this: `albums = _top_albums(c)` sat twelve lines under
    `albums.all_album_nodes()`, so the dashboard raised before it drew
    anything, and every CLI test asked for `--json` and never ran it.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level and node.module is None:
                modules.update(a.asname or a.name for a in node.names)   # from . import x
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            bound = set()
            for sub in ast.walk(func):
                if isinstance(sub, ast.Assign):
                    bound.update(t.id for t in sub.targets if isinstance(t, ast.Name))
                elif isinstance(sub, (ast.For, ast.comprehension)):
                    if isinstance(sub.target, ast.Name):
                        bound.add(sub.target.id)
            for name in sorted(bound & modules):
                offenders.append("%s:%d  %s() binds %s, which is a module here"
                                 % (path.relative_to(PACKAGE), func.lineno, func.name, name))
    assert not offenders, "rename the local:\n" + "\n".join(offenders)


def test_every_served_file_type_is_the_schemas():
    """What the gallery serves and what the console accepts for upload are the
    same sets, with the same content type. The icon sets were not."""
    assert set(theme.ALBUM_ICON_TYPES) == schema.ICON_EXTS
    assert set(theme.ALBUM_FONT_TYPES) == schema.FONT_EXTS
    assert set(theme.ALBUM_WALLPAPER_TYPES) == schema.WALLPAPER_EXTS
    assert set(theme.ALBUM_WALLPAPER_IMAGE_TYPES) == schema.WALLPAPER_IMAGE_EXTS
    for ext, mime in theme.ALBUM_ICON_TYPES.items():
        assert mime == schema.MIME[ext]
    assert schema.ICON_EXTS <= console_mod._SCOPE_EXTS["album"]


def _string_collections(tree):
    """Every set/list/tuple literal made only of string constants."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)) and node.elts and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
            yield node, [e.value for e in node.elts]


def test_no_module_keeps_its_own_list_of_keys():
    """An equal copy passes every test above and drifts the month after. So
    the source is searched: outside the schema, nothing may spell out a run
    of cfg key names as a literal."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in ("schema.py", "cfgio.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, values in _string_collections(tree):
            known = [v for v in values if v in ALL_KEYS]
            if len(known) >= 5:
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno} {known[:6]}")
    assert not offenders, "keep key lists in aperture/schema.py:\n" + "\n".join(offenders)


def test_no_module_keeps_its_own_parser():
    """The grammar in two places is how this started."""
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "cfgio.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert 'line[0] in "#;"' not in source, f"{path.name} parses cfg text itself"


# ============================================================
# every key fully described
# ============================================================
def test_every_key_has_a_write_style_and_a_help_line():
    assert ALL_KEYS <= set(schema.KEY_SPEC), sorted(ALL_KEYS - set(schema.KEY_SPEC))
    assert ALL_KEYS <= set(schema.HELP), sorted(ALL_KEYS - set(schema.HELP))
    for key in ALL_KEYS:
        assert schema.KEY_SPEC[key].get("type"), key
        assert schema.HELP[key].strip(), key


def test_the_gallery_overrides_only_override_gallery_keys():
    assert set(schema.GALLERY_SPEC) <= set(schema.GALLERY_KEYS)
    assert set(schema.GALLERY_HELP) <= set(schema.GALLERY_KEYS)


def test_no_key_is_listed_twice():
    assert len(schema.ALBUM_KEYS) == len(set(schema.ALBUM_KEYS))
    assert len(schema.GALLERY_KEYS) == len(set(schema.GALLERY_KEYS))


def test_every_accepted_file_type_can_be_served():
    accepted = (schema.ICON_EXTS | schema.FONT_EXTS | schema.WALLPAPER_EXTS
                | schema.BRAND_EXTS | schema.GALLERY_EXTS)
    assert accepted <= set(schema.MIME), sorted(accepted - set(schema.MIME))


def test_the_console_offers_every_key(indexed):
    security.clear_password()
    meta = TestClient(console_mod.app).get("/api/meta").json()
    assert ALL_KEYS <= set(meta["spec"])
    assert ALL_KEYS <= set(meta["help"])


# ============================================================
# the grammar
# ============================================================
SAMPLE = """\
# the archive
site_name = Fixture, the archive

album_order =
    berlin
#trips
    tech
# just a comment
stat = Camera: X100
stat = Lens: 23mm
empty =
"""


def test_parse_keeps_what_the_gallery_relies_on():
    cfg = cfgio.parse(SAMPLE, cfgio.GROUP_KEYS)
    assert cfgio.joined(cfg, "site_name") == "Fixture, the archive"
    assert cfg["album_order"] == ["berlin", "#trips", "tech"]
    assert cfg["stat"] == ["Camera: X100", "Lens: 23mm"]
    assert cfg["empty"] == []                    # present but empty != absent
    assert "just" not in str(cfg)


def test_off_has_every_spelling_the_gallery_honours():
    for word in ("off", "false", "0", "no", "none", "hide"):
        assert word in cfgio.FALSE
    assert {"featured", "random", "shuffle"} <= schema.REEL_ACCEPTED


def test_editing_leaves_every_comment_where_it_was():
    f = cfgio.CfgFile(SAMPLE, cfgio.GROUP_KEYS)
    f.apply({"site_name": ["Renamed"], "stat": ["Camera: GR III"], "empty": None},
            schema.KEY_SPEC)
    text = f.text()
    assert "# the archive" in text and "# just a comment" in text and "#trips" in text
    values = f.values()
    assert values["site_name"] == ["Renamed"]
    assert values["stat"] == ["Camera: GR III"]
    assert "empty" not in values
    assert values["album_order"] == ["berlin", "#trips", "tech"]


def test_repeated_edits_do_not_spread_the_file_out():
    f = cfgio.CfgFile(SAMPLE, cfgio.GROUP_KEYS)
    for i in range(10):
        f.apply({"site_name": [f"Name {i}"]}, schema.KEY_SPEC)
        f.apply({"stat": None}, schema.KEY_SPEC)
        f.apply({"stat": ["Camera: X"]}, schema.KEY_SPEC)
    assert "\n\n\n" not in f.text()


# ============================================================
# the two drifts, as regressions
# ============================================================
@pytest.fixture
def berlin_cfg(photos_dir):
    """berlin's album.cfg, restored byte for byte afterwards — other test
    files assert on its content."""
    path = photos_dir / "berlin" / ".album" / "album.cfg"
    original = path.read_bytes()
    yield path
    path.write_bytes(original)


def test_reel_hide_is_not_an_error_anywhere(berlin_cfg):
    berlin_cfg.write_text(berlin_cfg.read_text(encoding="utf-8") + "reel = hide\n",
                          encoding="utf-8")
    cfg = cfgio.parse(berlin_cfg.read_text(encoding="utf-8"))

    issues = checks.album("berlin")
    assert not [i for i in issues if i["key"] == "reel"], issues

    # ... and the gallery really does treat it as off.
    assert albums.album_reel("berlin", cfg)[0] == "off"


def test_a_jpeg_icon_can_be_uploaded_and_is_served_as_one():
    target = paths.writable_target(settings.photos_dir, "berlin", "mark.jpg",
                                   allowed_exts=console_mod._SCOPE_EXTS["album"])
    assert target.name == "mark.jpg"
    assert theme.ALBUM_ICON_TYPES[".jpg"] == "image/jpeg"
