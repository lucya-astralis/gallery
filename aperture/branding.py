"""Who the archive belongs to.

The operator's wordmark, marks, links and footer badges, as gallery.cfg names
them. The software's own identity -- the attribution that ships with the
code -- is aperture/brand.py, and reads no config at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import cfgio, config, i18n, scanner, schema, templating, theme
from .runtime import settings


# ----- site branding (gallery.cfg owns it) ------------------------------
# The archive's own identity: its wordmark, its mark, the person behind it
# and where the legal pages live. Every bit of that describes the OPERATOR
# and none of it describes the software, so it lives in gallery.cfg instead
# of hard-coded in the templates, where it used to sit as one repeated
# string:
#
#   site_name    = lucya.systems      wordmark, first line
#   site_sub     = gallery            wordmark, second line
#   site_hero    = Gallery            the big word on the welcome screen
#   site_desc    = Personal photo …   meta description (+ _de / _jp)
#   logo         = logo.svg           the mark beside the wordmark
#   favicon      = logo.svg           tab icon; unset = the logo
#   operator     = lucya              who is behind the archive
#   operator_url = https://lucya.sh   where the "about" links point
#   operator_pfp = pfp.webp           the face on the footer's operator card
#   privacy_url  = https://…/privacy  footer link; unset = the link is gone
#   imprint_url  = https://…/privacy  footer link; unset = the link is gone
#   badges       = eu.gif | European Union
#
# Nothing here falls back to the vendor's own name (aperture/brand.py): an empty
# gallery.cfg leaves the site calling itself "Gallery" behind a neutral
# mark. Falling back to "lucya.systems" would quietly make the vendor the
# default operator again, which is the exact coupling this replaces. What
# stays vendor-side is the "powered by" line and the machine-readable
# generator marks — see aperture/brand.py for that half.
#
# The files sit in photos/.gallery/, the gallery-wide mirror of an album's
# `.album/` folder, and reach the page through /brand/{slot}: like the
# album icon and font routes, that reads the filename back OUT of the cfg
# rather than taking it from the URL, so it can only ever serve a file the
# cfg actually names.
# same set the per-album icon accepts — a mark is a mark
BRAND_ASSET_TYPES = theme.ALBUM_ICON_TYPES

# URL slot -> cfg key. Also the whitelist /brand/{slot} validates against,
# so the route has no notion of a key that isn't one of these three.
BRAND_SLOTS = {"logo": "logo", "favicon": "favicon", "pfp": "operator_pfp"}

# How large a raster mark may actually arrive at the browser, per slot: the
# longest edge the page ever draws it at, times three for a dense phone
# screen. A cfg names a FILE, and there is nothing in a folder to stop that
# file from being a 539x539 PNG worn as a 34px avatar — which is what
# `operator_pfp` was, 350 KB on every single page of the archive. Anything
# bigger is served as a capped WebP copy instead (see brand_render); the
# file on disk is never touched. The favicon is deliberately absent: the
# browser wants exactly what the cfg names there, at its own size.
BRAND_RASTER_CAP = {"logo": 256, "pfp": 128}

# where those copies live — beside the database, not in the photo derivative
# trees, so a `thumbs --prune` sweep never has an opinion about them
BRAND_CACHE_DIR = settings.data_dir / "brand"
BRAND_DEFAULT_NAME = "Gallery"

# the unbranded mark, shipped so a gallery that names no logo still has one
BRAND_DEFAULT_LOGO = "logo/gallery-mark.svg"


def brand_file(name: str) -> Path | None:
    """A branding filename resolved inside photos/.gallery/, or None: the rule
    every key against that folder shares (config.gallery_asset_file), held to the
    types a mark may be. These assets are versioned on the stamp shared with
    the rest of the folder — config.gallery_asset_stamp, in the same section."""
    return config.gallery_asset_file(name, BRAND_ASSET_TYPES)


def _brand_asset(slot: str, cfg: dict[str, list[str]]) -> dict | None:
    """{url, type} for one branding slot, or None when it is not configured
    (or names a file that isn't there)."""
    key = BRAND_SLOTS.get(slot)
    if key is None:
        return None
    path = brand_file((cfgio.first(cfg, key) or "").strip())
    if path is None:
        return None
    return {"url": f"/brand/{slot}?v={config.gallery_asset_stamp(path)}",
            "type": BRAND_ASSET_TYPES[path.suffix.lower()]}


def brand_render(slot: str, path: Path) -> Path | None:
    """A capped copy of an oversized raster mark, built once and cached, or
    None when the file the cfg names is already sensible and should be served
    as it is.

    Left alone on purpose: SVG (resolution-free by definition), GIF (the
    footer badges animate, and a resize would freeze them) and every slot
    with no cap in BRAND_RASTER_CAP. A cached copy older than its source is
    rebuilt, so editing the picture in .gallery/ is enough — the `?v=` stamp
    on the URL already changed with the mtime, so nobody is served the old
    one out of a cache either."""
    cap = BRAND_RASTER_CAP.get(slot)
    if cap is None or path.suffix.lower() in (".svg", ".gif"):
        return None
    dst = BRAND_CACHE_DIR / f"{slot}.webp"
    if not scanner.needs_rebuild(dst, path):
        return dst
    edge = scanner.max_edge(path)
    if edge is None or edge <= cap:
        # unreadable, or already small — either way the raw file speaks for itself
        return None
    return dst if scanner.make_brand_thumb(path, dst, cap) else None


def _brand_badge_names(cfg: dict[str, list[str]]) -> list[str]:
    return [raw.partition("|")[0].strip() for raw in cfg.get("badges", [])[:schema.BADGE_MAX]]


def _brand_badges(cfg: dict[str, list[str]]) -> list[dict]:
    """The footer's badge row. An entry naming a file that isn't in
    .gallery/ is dropped rather than rendered broken, and the label after
    the `|` is optional — without one the filename's stem stands in."""
    out = []
    for raw in cfg.get("badges", [])[:schema.BADGE_MAX]:
        name, _, label = raw.partition("|")
        path = brand_file(name.strip())
        if path is None:
            continue
        out.append({"url": f"/brand/badge/{len(out)}?v={config.gallery_asset_stamp(path)}",
                    "label": label.strip() or path.stem})
    return out


def brand_badge_file(index: int) -> Path | None:
    """The badge at position `index` of the RENDERED row, so the index means
    the same thing in the URL as it did in the page — dropped entries and
    all. Same guarantee as the other brand routes: the filename comes back
    out of the cfg, never out of the URL."""
    cfg = config.gallery_config()
    files = [f for f in (brand_file(n) for n in _brand_badge_names(cfg)) if f is not None]
    return files[index] if 0 <= index < len(files) else None


# http(s) and site-relative only: these values end up in an href, which is
# the reason a `javascript:` string from a cfg never gets that far.
_BRAND_URL = re.compile(r"^(?:https?://[^\s\"'<>]+|/[^\s\"'<>]*)$")


def brand_link(cfg: dict[str, list[str]], key: str) -> str | None:
    raw = cfgio.joined(cfg, key)
    return raw if raw and _BRAND_URL.match(raw) else None


def site_brand(lang: str = i18n.DEFAULT_LANG) -> dict:
    """Everything the chrome needs in order to name the archive, in one dict
    handed to every template as `brand` (see _i18n_context). Read per
    request, like every other gallery.cfg reader here, so an edit lands
    without a restart.

    `desc` is the one localized value, and it resolves through the same
    three tiers as an album description: `site_desc_de` / `site_desc_jp`
    win for their own language, `site_desc` is the shared fallback, and with
    neither set it lands on the translated built-in."""
    cfg = config.gallery_config()

    def txt(key: str) -> str:
        return cfgio.joined(cfg, key)

    name = txt("site_name") or BRAND_DEFAULT_NAME
    sub = txt("site_sub")
    logo = _brand_asset("logo", cfg)
    favicon = _brand_asset("favicon", cfg) or logo
    if logo is None:
        logo = {"url": templating.static_url(templating.WEB_DIR, BRAND_DEFAULT_LOGO), "type": "image/svg+xml"}
    if favicon is None:
        favicon = logo
    return {
        "name": name,
        "sub": sub,
        # "<name> <sub>" — the <title> suffix and og:site_name
        "title": f"{name} {sub}".strip(),
        # "<name> / <sub>" — the headline form the OG/Twitter cards use
        "og_title": f"{name} / {sub}" if sub else name,
        # the welcome screen's one big word, falling back through the wordmark
        "hero": txt("site_hero") or sub or name,
        "desc": (txt(f"site_desc_{lang}") or txt("site_desc")
                 or i18n.t(lang, "meta.site_desc")),
        "logo": logo["url"],
        "favicon": favicon["url"],
        "favicon_type": favicon["type"],
        # `operator` names the person, `operator_url` is where the two
        # "about" entries point — and having somewhere to point is what
        # gates both of them, so an archive that names nobody shows neither
        "operator": txt("operator") or name,
        "operator_url": brand_link(cfg, "operator_url"),
        "pfp": (_brand_asset("pfp", cfg) or {}).get("url"),
        "privacy_url": brand_link(cfg, "privacy_url"),
        "imprint_url": brand_link(cfg, "imprint_url"),
        "badges": _brand_badges(cfg),
    }
