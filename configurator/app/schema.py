"""What the gallery accepts in album.cfg / gallery.cfg.

Mirrored by hand from the gallery app (its cfg format block in main.py and the
key checks in debug.py) so this tool stays a standalone editor: it never
imports the gallery, and it can point at a photos/ folder served by any
version of it. When the gallery grows a key, add it here.
"""

from __future__ import annotations

# ----- vocabularies the gallery whitelists ------------------------------
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff",
              ".tif", ".heic", ".heif"}
ICON_EXTS = {".svg", ".png", ".webp"}
FONT_EXTS = {".otf", ".ttf", ".woff2", ".woff"}
# Per-album page backdrop. Desktop may be a clip; the mobile key is stills
# only, because the gallery never loads a backdrop video on a phone.
WALLPAPER_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
WALLPAPER_VIDEO_EXTS = {".mp4", ".webm"}
WALLPAPER_EXTS = WALLPAPER_IMAGE_EXTS | WALLPAPER_VIDEO_EXTS

ALBUM_META_DIR = ".album"
ALBUM_CFG_NAME = "album.cfg"
GALLERY_CFG_NAME = "gallery.cfg"

LANGS = ["en", "de", "jp"]

EFFECTS = ["sakura"]
REEL_VALUES = ["featured", "random", "off"]
IMAGE_SORTS = ["date_desc", "date_asc", "name_asc", "name_desc",
               "size_desc", "size_asc"]
ALBUM_SORTS = ["latest_desc", "latest_asc", "name_asc", "name_desc",
               "count_desc", "count_asc"]
# Two pseudo sorts backed by a cfg list / EXIF rather than SQL.
PHOTO_SORTS = ["curated", "days"] + IMAGE_SORTS
GALLERY_ALBUM_SORTS = ["curated"] + ALBUM_SORTS

WELCOME_KEYWORDS = ["showcase", "auto", "featured", "random", "shuffle"]

FONT_SCALE_RANGE = (0.5, 2.5)

# ----- how each key is written back -------------------------------------
# multiline: one entry per line under a bare `key =` header.
# repeated:  one `key = value` line per entry (values may contain commas that
#            must not be re-split).
KEY_SPEC: dict[str, dict] = {
    # album.cfg
    "collection": {"type": "bool"},
    "showcase": {"type": "bool"},
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
    "font_scale": {"type": "number"},
    # The gallery re-joins loc's comma-split parts with ", " (_album_stats),
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
}

ALBUM_KEYS = ["collection", "showcase", "cover", "featured", "order", "reel",
              "sort", "tags", "effect", "icon", "font", "font_scale",
              "wallpaper", "wallpaper_mobile", "loc", "stat", "stats"]
GALLERY_KEYS = ["welcome", "welcome_desktop", "welcome_mobile", "album_order",
                "album_sort"]

# One-line help shown next to each field in the UI.
HELP: dict[str, str] = {
    "collection": "Show every photo in the subtree (own + sub-folders) as one flat collection.",
    "showcase": "Showcase album -- gets the star rail on /albums and the welcome page.",
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
    "wallpaper": "Page backdrop on desktop — a clip or a still. Sub-albums inherit it. Empty means the gallery's default video.",
    "wallpaper_mobile": "Page backdrop on phones. Stills only; the gallery never loads a backdrop video there. Empty means the gallery's default still.",
    "loc": "Location, shown as the LOC line at the top of the stats block. Commas are fine here — the gallery rejoins them.",
    "stat": "Custom attributes for this album, one per line. They sit above the auto SPAN / DEVICE / FOCAL / APERTURE / DATA readouts the gallery derives from EXIF. No commas in a value — the cfg parser splits on them.",
    "stats": "Hide the whole stats block — both the custom attributes and the EXIF readouts.",
    "welcome": "Shared fallback for the welcome hero when a device key is missing or empty.",
    "welcome_desktop": "Welcome hero images on desktop.",
    "welcome_mobile": "Welcome hero images on phones (detected via User-Agent).",
    "album_order": "Curated album order. Adds “Curated” to the /albums sort menu and fixes the featured rails. A group header frames the albums under it.",
    "album_sort": "Preselect the sort option on /albums.",
}


def order_key(path: str) -> str:
    """Normalize an album path or photo ref the way the gallery does when
    matching cfg entries: slashes forward, trimmed, lower-cased."""
    return path.replace("\\", "/").strip().strip("/").lower()
