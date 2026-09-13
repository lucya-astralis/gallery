"""What album.cfg and gallery.cfg may say -- the one registry.

This used to be a hand-kept copy of the gallery's vocabulary, living in the
configurator because that shipped as its own image and could not import the
gallery. Its docstring said so: "when the gallery grows a key, add it here
too". The copy had already drifted in two places by the time the two became
one product -- the console refused `.gif`/`.jpg` icons the gallery serves, and
both validators flagged `reel = hide`, which the gallery honours.

So the direction is reversed. Everything below is the source, and the
gallery, the operations surface and the console all import it:

    aperture/config.py,   which keys the gallery reads, the effect whitelist,
    albums.py, theme.py,  the badge limit, every served file type and the
    branding.py           content type it goes out as
    aperture/scanner.py   what counts as a photograph
    aperture/ops.py       what `doctor` checks against
    aperture/console/     the form (KEY_SPEC, HELP) and its validation

Adding a key is one edit here: its name in ALBUM_KEYS or GALLERY_KEYS, its
write style in KEY_SPEC, its help line in HELP. `tests/test_schema.py` fails
if any of the three is missing, or if something starts keeping its own list
again.

The grammar of the files is not in here; that is `aperture/cfgio.py`.
"""

from __future__ import annotations

import os

from .cfgio import FALSE
from .i18n import LANGS as _I18N_LANGS

# ----- vocabularies the gallery whitelists ------------------------------
# What each file extension is served as. Every set below must be a subset of
# this map -- a file the gallery accepts but cannot name a type for would go
# out as application/octet-stream and render as a download.
MIME: dict[str, str] = {
    ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".avif": "image/avif",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# What counts as a photograph, for the scanner and for every route that
# serves one.
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff",
                        ".tif", ".heic", ".heif"})
def is_image(name) -> bool:
    """Whether a file name or path is a photograph, by extension. One function
    for the scanner, the watcher, the console and the path guards -- there
    used to be two, one taking a Path and one a string."""
    return os.path.splitext(os.fspath(name))[1].lower() in IMAGE_EXTS


# An album's `icon` and the gallery's marks. The console used to accept only
# .svg/.png/.webp here while the gallery served .gif/.jpg/.jpeg as well, so an
# operator whose icon was a JPEG could not manage it from the console.
ICON_EXTS = frozenset({".svg", ".png", ".webp", ".gif", ".jpg", ".jpeg"})
FONT_EXTS = frozenset({".otf", ".ttf", ".woff2", ".woff"})
# Per-album page backdrop. Desktop may be a clip; the mobile key is stills
# only, because the gallery never loads a backdrop video on a phone.
WALLPAPER_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".avif"})
WALLPAPER_VIDEO_EXTS = frozenset({".mp4", ".webm"})
WALLPAPER_EXTS = WALLPAPER_IMAGE_EXTS | WALLPAPER_VIDEO_EXTS

# Per-album metadata folder: everything that describes an album rather
# than being one of its photos -- album.cfg, the album_*.md descriptions,
# a custom title font. Never indexed (scanner.is_meta_path): a stray
# image in here is metadata -- a font specimen, a screenshot of the cfg --
# and not a gallery photo.
ALBUM_META_DIR = ".album"
ALBUM_CFG_NAME = "album.cfg"
GALLERY_CFG_NAME = "gallery.cfg"
# The gallery's own assets — its logo, the operator's portrait, the footer
# badges — live in photos/.gallery/, the gallery-wide mirror of
# an album's .album/ folder.
GALLERY_META_DIR = ".gallery"
# The pretty-link list, beside gallery.cfg — `name = album` or
# `name = album/photo.jpg`, one line per link. See aperture/links.py.
LINKS_CFG_NAME = "links.cfg"
# Both, for the guards that ask "is this path metadata?" -- the scanner
# walking the tree, and the write resolver in aperture/paths.py.
META_DIRS = (ALBUM_META_DIR, GALLERY_META_DIR)
BRAND_EXTS = ICON_EXTS
# Everything that folder may now hold. It started as marks only; the theme
# block one tier down (`font`, `wallpaper`, `wallpaper_mobile` in gallery.cfg)
# puts a face and a backdrop next to them, so the upload whitelist is the
# union rather than the marks alone.
GALLERY_EXTS = BRAND_EXTS | FONT_EXTS | WALLPAPER_EXTS
# how many footer badges the gallery renders
BADGE_MAX = 6
# http(s) or site-relative; anything else is dropped rather than put in an href
URL_KEYS = ("operator_url", "privacy_url", "imprint_url")

LANGS = list(_I18N_LANGS)

EFFECTS = ["sakura"]
# The three the console offers as a choice ...
REEL_VALUES = ["featured", "random", "off"]
# ... and every spelling the gallery honours (albums.album_reel): `shuffle` is
# random, and anything in cfgio.FALSE -- `hide` and `none` included -- is off.
REEL_ACCEPTED = frozenset({"featured", "random", "shuffle"}) | FALSE
IMAGE_SORTS = ["date_desc", "date_asc", "name_asc", "name_desc",
               "size_desc", "size_asc"]
ALBUM_SORTS = ["latest_desc", "latest_asc", "name_asc", "name_desc",
               "count_desc", "count_asc"]
# Two pseudo sorts backed by a cfg list / EXIF rather than SQL.
PHOTO_SORTS = ["curated", "days"] + IMAGE_SORTS
GALLERY_ALBUM_SORTS = ["curated"] + ALBUM_SORTS

# A welcome key holding one of these words is a feed rather than a list of
# photos; the value is which feed. welcome.welcome_feed reads it, the
# console offers the words, aperture/checks.py knows not to resolve them.
WELCOME_KEYWORDS = {"showcase": "showcase", "auto": "showcase", "featured": "showcase",
                    "random": "random", "shuffle": "random"}

FONT_SCALE_RANGE = (0.5, 2.5)
# Per-album theme (album.cfg `accent` / `wallpaper_tint` / `wallpaper_dim`).
# The gallery derives three faces from the accent and rejects anything that
# isn't a hex colour; these two ranges are its guard rails on the backdrop
# treatment; aperture/theme.py and doctor both read them from here.
WALLPAPER_TINT_RANGE = (0.0, 1.0)
WALLPAPER_DIM_RANGE = (0.25, 1.0)

# ----- how each key is written back -------------------------------------
# multiline: one entry per line under a bare `key =` header.
# repeated:  one `key = value` line per entry (values may contain commas that
#            must not be re-split).
KEY_SPEC: dict[str, dict] = {
    # album.cfg
    # The album's display name. Like loc, the gallery rejoins the parser's
    # comma-split parts with ", " (config.album_display_name), so it stays one line.
    "name": {"type": "text", "joined": True},
    "collection": {"type": "bool"},
    "showcase": {"type": "bool"},
    "unlisted": {"type": "bool"},
    "cover": {"type": "photo"},
    "featured": {"type": "photo_list", "multiline": True},
    "order": {"type": "photo_list", "multiline": True},
    "reel": {"type": "choice", "choices": REEL_VALUES},
    "sort": {"type": "choice", "choices": PHOTO_SORTS},
    "tags": {"type": "list"},
    "effect": {"type": "choice", "choices": EFFECTS},
    "icon": {"type": "asset", "exts": sorted(ICON_EXTS)},
    "font": {"type": "asset", "exts": sorted(FONT_EXTS)},
    "wallpaper": {"type": "asset", "exts": sorted(WALLPAPER_EXTS)},
    "wallpaper_mobile": {"type": "asset", "exts": sorted(WALLPAPER_IMAGE_EXTS)},
    "font_scale": {"type": "number", "range": list(FONT_SCALE_RANGE), "step": 0.05},
    # The album's own accent colour, and how the gallery treats the backdrop
    # behind its pages. `off` is a real value for both wallpaper knobs, so
    # they are text-with-a-slider rather than plain numbers.
    "accent": {"type": "color"},
    "wallpaper_tint": {"type": "ratio", "range": list(WALLPAPER_TINT_RANGE),
                       "step": 0.02, "off": "off"},
    "wallpaper_dim": {"type": "ratio", "range": list(WALLPAPER_DIM_RANGE),
                      "step": 0.02, "off": "off"},
    # The gallery re-joins loc's comma-split parts with ", " (albums.album_stats),
    # so it reads as one line even though the parser sees a list.
    "loc": {"type": "text", "joined": True},
    # The album's custom attributes: freeform "Label: Value" lines the
    # gallery renders above the auto EXIF readouts. Edited as pairs.
    "stat": {"type": "kv_list", "repeated": True},
    "stats": {"type": "bool_off"},
    # gallery.cfg
    "welcome": {"type": "welcome", "multiline": True},
    "welcome_desktop": {"type": "welcome", "multiline": True},
    "welcome_mobile": {"type": "welcome", "multiline": True},
    "album_order": {"type": "album_list", "multiline": True},
    "album_sort": {"type": "choice", "choices": GALLERY_ALBUM_SORTS},
    # gallery.cfg — branding. Who the archive belongs to; the software's own
    # attribution is not configurable and stays in the gallery's footer line.
    # The text keys are prose, so they are joined the way `name` and `loc`
    # are: the parser comma-splits every value and a sentence must survive it.
    "site_name": {"type": "text", "joined": True},
    "site_sub": {"type": "text", "joined": True},
    "site_hero": {"type": "text", "joined": True},
    "site_desc": {"type": "text", "joined": True},
    "site_desc_en": {"type": "text", "joined": True},
    "site_desc_de": {"type": "text", "joined": True},
    "site_desc_jp": {"type": "text", "joined": True},
    "logo": {"type": "brand_asset", "exts": sorted(BRAND_EXTS)},
    "favicon": {"type": "brand_asset", "exts": sorted(BRAND_EXTS)},
    "operator": {"type": "text", "joined": True},
    "operator_url": {"type": "text", "joined": True},
    "operator_pfp": {"type": "brand_asset", "exts": sorted(BRAND_EXTS)},
    "privacy_url": {"type": "text", "joined": True},
    "imprint_url": {"type": "text", "joined": True},
    "badges": {"type": "list", "multiline": True},
    # gallery.cfg — whose photographs these are, in the derived images' EXIF
    "credit": {"type": "text", "joined": True},
}

ALBUM_KEYS = ["name", "collection", "showcase", "unlisted", "cover", "featured", "order",
              "reel", "sort", "tags", "effect", "icon", "font", "font_scale",
              "accent", "wallpaper", "wallpaper_mobile", "wallpaper_tint",
              "wallpaper_dim", "loc", "stat", "stats"]
GALLERY_KEYS = ["welcome", "welcome_desktop", "welcome_mobile", "album_order",
                "album_sort",
                # the theme block, spelled exactly as album.cfg spells it —
                # gallery.cfg dresses the site, an album overrides its pages
                "accent", "font", "font_scale",
                "wallpaper", "wallpaper_mobile",
                "wallpaper_tint", "wallpaper_dim",
                "site_name", "site_sub", "site_hero", "site_desc",
                "site_desc_en", "site_desc_de", "site_desc_jp",
                "logo", "favicon", "operator", "operator_url", "operator_pfp",
                "privacy_url", "imprint_url", "badges", "credit"]
# Which gallery.cfg keys name a file in .gallery/, and what each accepts.
# Marks first, then the theme block's three — checked identically, because
# the folder rule is the same one whatever the file is for.
GALLERY_ASSET_KEYS = {"logo": BRAND_EXTS, "favicon": BRAND_EXTS,
                      "operator_pfp": BRAND_EXTS,
                      "font": FONT_EXTS,
                      "wallpaper": WALLPAPER_EXTS,
                      "wallpaper_mobile": WALLPAPER_IMAGE_EXTS}
# The same for album.cfg, against the album's own .album/.
ALBUM_ASSET_KEYS = {"icon": ICON_EXTS, "font": FONT_EXTS,
                    "wallpaper": WALLPAPER_EXTS,
                    "wallpaper_mobile": WALLPAPER_IMAGE_EXTS}

# Where a key means something different on the gallery tab than on an album's.
# Both files spell the theme block identically, but one tier down the files
# live in .gallery/ rather than in an .album/, and what they dress is the whole
# site rather than one album. Only the DIFFERENCE is written here — every
# other key, and every other half of these ones, falls through to KEY_SPEC
# and HELP.
GALLERY_SPEC: dict[str, dict] = {
    "font": {"type": "brand_asset", "exts": sorted(FONT_EXTS)},
    "wallpaper": {"type": "brand_asset", "exts": sorted(WALLPAPER_EXTS)},
    "wallpaper_mobile": {"type": "brand_asset",
                         "exts": sorted(WALLPAPER_IMAGE_EXTS)},
}

# One-line help shown next to each field in the UI.
HELP: dict[str, str] = {
    "name": "Display name, used everywhere the album is named -- cards, breadcrumbs, hero title. The folder name stays the URL. Empty means the folder name with underscores as spaces. Commas are fine here -- the gallery rejoins them.",
    "collection": "Show every photo in the subtree (own + sub-folders) as one flat collection.",
    "showcase": "Showcase album -- gets the star rail on /albums and the welcome page.",
    "unlisted": "Keep this album and every album under it out of all listings -- /albums, the search, /stats, the welcome page and the lists of the API. Its page, its photos and a /s/ link still open for whoever has the address, marked noindex. Not a lock.",
    "cover": "Pin the album cover instead of auto-picking the newest photo.",
    "featured": "Featured photos: welcome hero, /api/showcase, and this album's reel.",
    "order": "Curated photo order. Adds the “Curated” entry to this album's sort menu.",
    "reel": "What the hero slideshow at the top of the album shows.",
    "sort": "Preselect this album's sort option. “curated” needs an order list.",
    "tags": "Album tags shown under the hero title. Display-only — unrelated to the per-image .tags sidecar the ?tag= filter reads.",
    "effect": "Ambient effect layer on this album's page. Whitelisted by the gallery.",
    "icon": "The album's mark, shown wherever the album is named. Lives in .album/.",
    "font": "Display face for the album's hero title. Lives in .album/.",
    "font_scale": "Size multiplier for that face (%s–%s). Only read when a font is set."
                  % FONT_SCALE_RANGE,
    "wallpaper": "Page backdrop on desktop — a clip or a still. Sub-albums inherit it. Empty means the gallery's own (gallery.cfg), and failing that the one shipped with it.",
    "wallpaper_mobile": "Page backdrop on phones. Stills only; the gallery never loads a backdrop video there. Empty means the gallery's own, then the shipped one.",
    "accent": "This album's accent colour -- links, focus, active state, the featured mark, the hero button. Sub-albums inherit it. Empty means the gallery's own accent (gallery.cfg). The gallery lightens a colour too dark to read on the black page.",
    "wallpaper_tint": "How much colour the backdrop keeps. Empty = inherit (an album takes its parent's, then gallery.cfg; gallery.cfg takes the built-in near-greyscale treatment). “off” = the picture in full colour, a number = partial.",
    "wallpaper_dim": "How bright that backdrop is -- 1 and “off” both leave it untouched. Empty = inherit, ending at the built-in 0.72. In gallery.cfg this dresses the site's own default wallpaper.",
    "loc": "Location, shown as the LOC line at the top of the stats block. Commas are fine here — the gallery rejoins them.",
    "stat": "Custom attributes for this album, one per line. They sit above the auto SPAN / DEVICE / FOCAL / APERTURE / DATA readouts the gallery derives from EXIF. No commas in a value — the cfg parser splits on them.",
    "stats": "Hide the whole stats block — both the custom attributes and the EXIF readouts.",
    "welcome": "Shared fallback for the welcome hero when a device key is missing or empty.",
    "welcome_desktop": "Welcome hero images on desktop.",
    "welcome_mobile": "Welcome hero images on phones (detected via User-Agent).",
    "album_order": "Curated album order. Adds “Curated” to the /albums sort menu and fixes the featured rails. A group header frames the albums under it.",
    "album_sort": "Preselect the sort option on /albums.",
    "site_name": "The archive's name — first line of the wordmark, and the site name on link previews. Empty leaves the gallery calling itself “Gallery”. Commas are fine here.",
    "site_sub": "Second line of the wordmark, under the name. Usually what the site is rather than who runs it (“gallery”, “archive”). Empty shows the name alone.",
    "site_hero": "The one big word on the welcome screen. Empty falls back to the sub-line, then to the name.",
    "site_desc": "Meta description used on link previews. The per-language keys below win for their own language; with none of them set the gallery's own translated line stands.",
    "site_desc_en": "English meta description. Overrides the shared one.",
    "site_desc_de": "German meta description. Overrides the shared one.",
    "site_desc_jp": "Japanese meta description. Overrides the shared one. Note the gallery ships a glyph SUBSET of its Japanese font — new kanji here need tools/build_jp_subset.py re-run and the font redeployed.",
    "logo": "The mark beside the wordmark, in the nav and the footer. Lives in .gallery/. Empty means the gallery's own neutral mark.",
    "favicon": "Browser tab icon. Lives in .gallery/. Empty means the logo doubles as one.",
    "operator": "Who is behind the archive — the name in the footer's operator card and its “about” link. Empty falls back to the site name.",
    "operator_url": "Where the operator card and the welcome screen's “about me” button point. Both only appear when this is set. http(s) or site-relative.",
    "operator_pfp": "The portrait on the operator card. Lives in .gallery/.",
    "privacy_url": "Footer privacy link. Empty means no such link — better than a dead one. May point at the same page as the imprint.",
    "imprint_url": "Footer imprint link. Empty means no such link.",
    "badges": "Classic 88x31 web buttons in the footer, `file | label` per line. The label is the alt text and the tooltip. Files live in .gallery/. No commas in either half — the cfg parser splits on them.",
    "credit": "Who took the photographs — written as EXIF Artist/Copyright into every derived image (thumbnails, previews, converted fulls). Metadata only: nothing is drawn on a photo and the originals are never rewritten.",
}


# What the shared theme keys mean on the gallery tab. Same idea as
# GALLERY_SPEC: only the entries that genuinely differ, everything else falls
# through to HELP.
GALLERY_HELP: dict[str, str] = {
    "accent": "The gallery's accent colour -- links, focus, active state, the featured mark, the hero button, on every page no album has repainted. An album's own `accent` still wins for its own pages. Empty means the gallery's built-in colour. A colour too dark to read on the black page is lightened.",
    "font": "The archive's display face: the wordmark, the welcome screen's one big word, the 404. Lives in .gallery/. NOT an album's hero title -- that has its own `font` in album.cfg and is untouched by this. Empty means the gallery's stock face.",
    "font_scale": "Size multiplier for that face (%s-%s). It scales every text the face sets at once, so a face that inks small comes back up everywhere rather than one heading at a time. Only read when a `font` is set."
                  % FONT_SCALE_RANGE,
    "wallpaper": "The site's backdrop on desktop -- a clip or a still, in .gallery/. Every page that no album has dressed shows it. Empty means the one shipped with the gallery.",
    "wallpaper_mobile": "The site's backdrop on phones. Stills only; the gallery never loads a backdrop video there. It also stands in as the poster frame behind a desktop clip while that buffers.",
    "wallpaper_tint": "How much colour the site backdrop keeps -- the gallery's own and any an album brings that says nothing itself. Empty = the built-in near-greyscale treatment. \u201coff\u201d = full colour, a number = partial.",
    "wallpaper_dim": "How bright that backdrop is -- 1 and \u201coff\u201d both leave it untouched. Empty = the built-in 0.72. An album that sets it wins for its own pages.",
}
