# lucya.systems gallery

A lean, read-only web image gallery with folder-based albums, EXIF display, sidecar-file tags, and automatic thumbnail/preview generation. Deployed via Docker. Safe for public hosting behind Cloudflare.

## Features

- **Folder = album:** every subfolder in `photos/` is automatically an album. Drop an image in → it appears in the album.
- **Fully automatic indexing:** filesystem watcher (local) and/or periodic rescan (for SMB/NFS). No manual buttons in the web UI.
- **Two-tier images:** `/thumb/...` (480 px) for grids, `/preview/...` (1600 px) for the detail view stage. The original (`/full/...`) only loads when you click *Load original*.
- **EXIF:** camera, lens, exposure, ISO, focal length, … on the detail page. GPS coordinates are stripped by default (privacy).
- **Tags:** per-album ones come from `album.cfg` and label the album in its hero; per-photo ones are sidecar files (e.g. `IMG_0001.jpg.tags` containing `holiday, beach, sunset`) — click one in the album view to filter.
- **Showcase:** flag photos (`featured = …`) or a whole album (`showcase = true`) in the album's `album.cfg` to surface them on the welcome screen, on the album overview, and via `/api/showcase` JSON for embedding on other sites.
- **Search & sort:** top bar searches album, file, and tag names; sort by date, name or size on every list view — plus a "Curated" order defined in `album.cfg` / `gallery.cfg`, which can also preselect the default sort.
- **By day:** an album whose photos span more than one day also offers a **By day** sort — newest day first, with the grid split into a framed section per capture day (day counter, weekday, photo count). On an album with a trip configured (`TRIPS` in `app/main.py`) the counter is the trip day, counted from the outbound flight, and each day carries a chip naming the leg it falls into — sub-albums of that trip inherit both.
- **Three languages (EN / DE / JP):** selector in the top-right corner, cookie-backed with an `Accept-Language` fallback. Album descriptions are per-language markdown files (`album_en.md` / `album_de.md` / `album_jp.md`); UI strings live in `app/i18n.py`. See [Languages](#languages--i18n).
- **Mobile-friendly:** responsive grid, large touch targets, keyboard navigation (← → ESC) on desktop.
- **Read-only:** no write endpoints, no uploads. The `photos/` mount is `:ro`. No attack surface for upload/tag-injection exploits.
- **Operations CLI:** `python -m app.cli` — run or pause the indexer, check index/config/derivative drift with `doctor`, audit tags and GPS, and inspect exactly how a photo, an album, the welcome hero or a trip resolves. See [Operations CLI](#operations-cli).
- **Security headers:** CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy — all set by built-in middleware.
- **Custom 404 page** with megacorp-terminal aesthetic.

## Quick start

```bash
cp .env.example .env   # adjust paths & options
docker compose up -d --build
```

Open: <http://localhost:8000>

Add images:

```
photos/
├── holiday-2025/
│   ├── DSC_0001.jpg
│   ├── DSC_0001.jpg.tags     # optional: "beach, italy"
│   └── DSC_0002.jpg
├── family/
│   └── …
└── rome-trip/
    └── …
```

Each subfolder is one album. Supported: JPG/JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC*.

(*HEIC may need extra Pillow plugins.)

For Linux server deployment with an SMB share see [DEPLOY-LINUX.md](DEPLOY-LINUX.md).

## Showcase

Featured photos and featured albums are configured in the album's
[`album.cfg`](#album-settings-albumcfg) — nothing is inferred from file or
folder names.

| Key in `album.cfg`      | Effect                                                                                |
|-------------------------|---------------------------------------------------------------------------------------|
| `featured = hero.jpg, …`| Those photos are featured: they appear in the welcome hero feed, in the featured hero slideshow of the album *and its parent albums*, and in the `/api/showcase` feed. They get a ★ in the album grid. `*` / `all` features every photo of the album. |
| `showcase = true`       | The album is featured: shown in a dedicated "Showcase Albums" section on the welcome screen and on `/albums`, with a `★ FEATURED` badge. Photos inside still need their own `featured` entry to be individually featured. |

The two flags are **independent** — marking an album as `showcase` does NOT
auto-feature its photos, and a photo can be featured in an album that isn't.

Example — `photos/best-of/.album/album.cfg`:

```
showcase = true
featured = portrait.jpg
```

```
photos/
├── best-of/
│   ├── .album/album.cfg       ← showcase = true, featured = portrait.jpg
│   ├── portrait.jpg           ← featured photo (in /api/showcase)
│   └── filler.jpg             ← in the album, but not featured
├── holiday-2025/
│   ├── .album/album.cfg       ← featured = favourite.jpg
│   ├── favourite.jpg          ← featured photo (album itself isn't a showcase)
│   └── DSC_0042.jpg
└── …
```

Featured flags are recomputed on every startup, after every scan and
whenever an `album.cfg` changes — an edit takes effect on the next reload,
without a re-scan.

## Languages / i18n

The site renders in **English, German and Japanese**. The nav selector
(top right) hits `GET /lang/{en|de|jp}?next=…`, which sets a `lang` cookie
and bounces back; first-time visitors get their `Accept-Language` match,
falling back to English. HTML responses carry `Vary: Cookie, Accept-Language`
so shared caches key correctly.

**What is translated:** real content — leads, buttons, counters, the sort
menu, EXIF labels, trip countdown, empty states, OG descriptions. The
decorative camera-HUD tokens (REC, FRM, SIG /, ONLINE, T-x DAYS, …)
intentionally stay English in every language, like the HUD of an actual
Japanese camera. UI strings live in `app/i18n.py` (server) and in the
`UI_STRINGS` table at the top of `app/static/app.js` (client) — keep both
in sync when adding text.

**Caching:** because the same URL serves different languages, all HTML is
sent with `Cache-Control: no-store` (browsers don't reliably key their
cache on `Vary: Cookie`, and back/forward-cache restores would resurface
stale-language pages — a `pageshow` guard in app.js reloads on mismatch
for Safari). Images, CSS and JS keep long-lived cache headers. If you put
a CDN cache rule in front, make sure it does NOT cache `text/html`.

**The `.album/` folder.** Everything that *describes* an album rather than
being one of its photos lives in a `.album/` folder inside it, so the photo
folder itself stays nothing but photos:

```
photos/japan_2026/
├── .album/
│   ├── album.cfg          ← settings (see Config files below)
│   ├── album_en.md        ← description, English (also the fallback)
│   ├── album_de.md        ← description, German
│   ├── album_jp.md        ← description, Japanese
│   ├── MusashiBrush.otf   ← the album's own title face (`font =`)
│   └── icon.svg           ← the album's own mark (`icon =`)
├── tokyo/                 ← sub-album (has its own .album/)
└── skyline.jpg
```

This is the **only** place looked at — a cfg or description left loose in
the photo folder is ignored. Nothing inside `.album/` is ever indexed,
thumbnailed or served as a photo, so a font specimen or reference image can
sit in there safely. `gallery.cfg` is not part of this: it configures the
gallery as a whole and stays at the root of `photos/` (see below).

**Album descriptions** are the per-language markdown files above. Missing
translations fall back to `album_en.md`, then to a plain `album.md`, then to
the first `*.md` in the folder — a partially translated gallery still shows
something everywhere.

**An album's own title font.** Drop a font into the album's `.album/` folder
and name it in `album.cfg`:

```ini
font = MusashiBrush.otf     # .otf / .ttf / .woff2 / .woff
font_scale = 1.3            # optional, 0.5–2.5 — size multiplier for it
```

The album's hero title then renders in that face (and is set larger, since a
custom face is a display treatment). `font_scale` tunes that size per album:
display faces disagree about how much of the em they ink, so a brush face
reads a size smaller than a geometric one set at the same px. Out-of-range
or unparseable values just mean no scaling. Because the CSP forbids inline
styles, the binding is served as a real stylesheet at
`/album-font.css/{album}`, which carries the `@font-face` plus the
`--album-title-font` / `--album-title-scale` properties that
`.album-font .album-hero__title` in `style.css` reads; the file itself comes
from `/album-font/{album}`. Only the file named in the cfg is ever served —
the filename never travels in the URL.

**An album's own icon.** Any album can carry a small mark — a civic emblem, a
crest, a logo. Drop the image into `.album/` and name it in `album.cfg`:

```ini
icon = icon.svg             # .svg / .png / .webp / .gif / .jpg
```

It then shows up wherever that album is named: its card in the grids and the
★ rail, the breadcrumb trail, the hero title, and — for a trip album — the
stops of the itinerary timeline, which read the mark off each stop's own
sub-album. Sizing is relative to whatever type it sits in, so a mark works at
every one of those places without per-page tuning. Served from
`/album-icon/{album}`; as with the font, only the file named in the cfg is
ever served and the filename never travels in the URL.

**Japanese font subset:** the site ships a glyph subset of Noto Sans JP
(`app/static/fonts/NotoSansJP-subset.woff2`, ~120 KB instead of the 8.8 MB
variable TTF). Every JP glyph the site can render must be baked in — after
changing/adding Japanese text anywhere (album_jp.md, i18n.py, app.js,
templates), rebuild it or new characters show as tofu:

```bash
python tools/build_jp_subset.py     # needs: pip install fonttools brotli
```

The subset always contains the full kana blocks plus every kanji currently
in use (the script scans the repo), so kana-only edits never need a rebuild.

**Logo raster:** the terminal CLI can draw the real logo as a picture (see
[Terminals](#terminals)), which needs a bitmap. `app/static/logo/lucya_logo.png`
is rasterised from the SVG on a developer machine, so the container needs no
SVG stack at all — Pillow and nothing else:

```bash
python tools/render_logo.py         # needs nothing beyond the app's own deps
```

Re-run it after changing `lucya_logo.svg`. The renderer covers exactly what
that file uses (nested `matrix(…)` groups, absolute `M`/`L`/`C`/`Z` paths,
solid fills) and refuses loudly on anything else, rather than quietly
producing a wrong picture.

## Config files (`gallery.cfg` / `album.cfg`)

Both files share one format: plain `key = value` lines, `#`/`;` start comments. List values accumulate — comma-separate them, repeat the key, or (easiest to read) put **one entry per line** below the key; any non-comment line without a `=` continues the key above it:

```ini
featured =
    osaka/hero.jpg
    tokyo/shibuya.jpg
    skyline.jpg
```

Both files are re-read on every page load, so edits apply immediately — no restart needed.

### Album settings (`album.cfg`)

Optional file in the album's **`.album/` folder** (see above):

| Key          | Values                          | Effect                                                                                     |
|--------------|---------------------------------|--------------------------------------------------------------------------------------------|
| `collection` | `true`                          | The album page shows every photo of its whole subtree (own + sub-folders) as one flat set. The [API](#api) scopes the album the same way. |
| `showcase`   | `true` / `false`                | Featured album: ★ rail on `/albums` and the welcome screen. |
| `featured`   | paths, or `*` / `all`           | Featured photos: welcome hero, `/api/showcase`, the album's reel. Paths are relative to the album (`osaka/IMG_4853.png`, a leading `/` is fine); bare filenames match anywhere in the subtree. Matching ignores casing, so `Osaka/…` also finds `osaka/…`. The album's reel shows them in exactly this order. |
| `cover`      | one path                        | Pin the album cover instead of auto-picking the newest photo.                              |
| `reel`       | `featured` / `random` / `off`   | What the hero slideshow at the top of the album shows: the featured photos (default), random photos from the album's subtree, or nothing (hidden). |
| `order`      | paths                           | Curated photo order — adds a **Curated** entry to the album's sort menu. Photos not listed follow, newest first. |
| `sort`       | `curated`, `days`, `date_desc`, `date_asc`, `name_asc`, `name_desc`, `size_desc`, `size_asc` | Preselect the sort option for this album's grid (visitors can still switch). `curated` needs an `order` list, `days` needs photos on more than one day — a preset that the album can't offer falls back to `date_desc`. |
| `tags`       | names, e.g. `paris, night`      | The album's tags, shown under its hero title and nowhere else. A leading `#` is optional. Album-level and display-only — see the note below. |
| `effect`     | `sakura`                        | Ambient effect layer on this album's page (petals drifting down). |
| `icon`       | a filename in `.album/`         | The album's own mark — `.svg` / `.png` / `.webp` / `.gif` / `.jpg` — shown wherever the album is named: cards, breadcrumb, hero title, trip stops (see above). |
| `font`       | a filename in `.album/`         | Display face for the album's hero title — `.otf` / `.ttf` / `.woff2` / `.woff` (see above). |
| `font_scale` | a number, `0.5`–`2.5`           | Size multiplier for that face, so a small-reading display face can be evened up. Only read when `font` is set; ignored when out of range. |

### Gallery settings (`gallery.cfg`)

Optional file in the **root of `photos/`**. By default the welcome hero cycles through a random selection of showcased photos (falling back to fully random when nothing is showcased). To pick the images yourself:

```ini
# photos/gallery.cfg — welcome hero feed
# one of:
#   welcome = showcase      ← random featured photos (default, same as no file)
#   welcome = random        ← random photos, ignore the featured flags
#   welcome = <paths>       ← hand-picked list, shown in exactly this order
welcome =
    berlin_dec_2025/IMG_0646.png
    paris_march_2026/IMG_2222.png
    frankfurt_feb_2026/IMG_1628.png

# separate feeds per device class (welcome = shared fallback):
welcome_desktop = showcase
welcome_mobile =
    paris_march_2026/IMG_2222.png

# curated album order: adds a "Curated" entry to the /albums sort menu and
# fixes the order of the ★ featured-album rails (welcome + /albums).
# A bare #label line (# glued to the label) frames the albums below it as a
# labeled group — the frames only show in the Curated view on /albums, every
# other sort/page uses the flat order. "# spaced" and ";" comments stay
# comments, so `# japan_2026` still just disables a line.
album_order =
    japan_2026
    paris_march_2026
    #trips
    berlin_dec_2025
    frankfurt_feb_2026
    #games
    elite_dangerous

# preselect the sort option on /albums (curated, latest_desc, latest_asc,
# name_asc, name_desc, count_desc, count_asc)
album_sort = curated
```

Rules for the hand-picked welcome list:

- Paths are relative to `photos/` (`album/file.jpg`, nested albums allowed); backslashes are tolerated.
- Entries accumulate in order (max 24, duplicates collapse).
- Entries that aren't indexed are skipped with a log warning; if nothing resolves, the feed falls back to showcase/random as if the file weren't there.
- With a hand-picked list the hero shows a `CURATED` label and hides the ⟳ TUNE (reshuffle) button.
- `welcome_mobile` / `welcome_desktop` accept the same syntax as `welcome` and win over it for their device class. Phones are detected via the User-Agent (`Mobi`); Android tablets and iPads in desktop mode get the desktop feed.

## API

A read-only JSON view of everything the pages render — albums, photos, EXIF, tags, stats — so you can embed the gallery elsewhere or build your own front end on it. CORS is open, responses are cached for 5 minutes, errors come back as JSON (`{"error": …, "status": …}`).

`GET /api` lists every endpoint with its parameters, so the API describes itself:

| Endpoint                    | Returns                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `GET /api`                  | Endpoint index, sort keys, languages                                    |
| `GET /api/stats`            | Gallery-wide counters: photos, albums, featured, tags, bytes, date span |
| `GET /api/albums`           | Album cards — top level, or the children of `?parent=`                  |
| `GET /api/album/{album}`    | One album in full: meta, description, stats, reel, sub-albums, photos    |
| `GET /api/photos`           | Photo query across the gallery or one album, paged                      |
| `GET /api/photo/{rel_path}` | One photo: EXIF, tags, prev/next neighbours                             |
| `GET /api/tags`             | Photo tags with counts                                                  |
| `GET /api/showcase`         | Featured photos (the original embed endpoint)                           |
| `GET /api/shuffle`          | Random photos (bare array — the welcome hero reads it)                  |

Three rules hold everywhere:

- **Collections are honoured.** An album with `collection = true` in its `album.cfg` answers with its *whole subtree*, exactly like its page does — `/api/album/…`, `/api/photos?album=…`, `/api/showcase?album=…` and `/api/tags?album=…` all agree. Every response carries a `scope` object saying what happened:

  ```json
  "scope": { "album": "japan_2026", "collection": true, "subtree": true }
  ```

  Pass `subtree=0` to force the plain folder scope on a collection album, or `subtree=1` to widen a normal album. Album paths tolerate different casing.
- **One shape per object.** Photos always look like the `items` entries below, albums always like the `album` object — in every endpoint, at every nesting level.
- **Language follows `lang=`** (`en`/`de`/`jp`), else the visitor's cookie / `Accept-Language`. Anything language-dependent (album descriptions, EXIF labels, the SPAN readout) echoes the language it used in `lang` and sends `Vary: Accept-Language, Cookie`.

### `GET /api/showcase`

| Query param | Default | Meaning                                                     |
|-------------|---------|-------------------------------------------------------------|
| `limit`     | `50`    | Max items, clamped to `1..200`                              |
| `album`     | —       | Only featured photos inside this album (collection-aware)   |
| `subtree`   | —       | `0`/`1` to override that album's collection scope           |
| `random`    | `0`     | `1` for random order; default is newest first (by EXIF date)|
| `tags`      | `0`     | `1` to include each photo's tags                            |

**Response shape:**

```json
{
  "count": 1,
  "total": 12,
  "scope": { "album": null, "collection": false, "subtree": false },
  "items": [
    {
      "rel_path": "holiday-2025/favourite.jpg",
      "album": "holiday-2025",
      "filename": "favourite.jpg",
      "width": 4032,
      "height": 3024,
      "size": 8123456,
      "taken_at": "2025-08-14T19:42:01",
      "mtime": 1755193321.0,
      "featured": true,
      "urls": {
        "thumb":       "/thumb/holiday-2025/favourite.jpg",
        "preview":     "/preview/holiday-2025/favourite.jpg",
        "full":        "/full/holiday-2025/favourite.jpg",
        "page":        "/image/holiday-2025/favourite.jpg",
        "api":         "/api/photo/holiday-2025/favourite.jpg",
        "thumb_abs":   "https://gallery.example.com/thumb/holiday-2025/favourite.jpg",
        "preview_abs": "https://gallery.example.com/preview/holiday-2025/favourite.jpg",
        "full_abs":    "https://gallery.example.com/full/holiday-2025/favourite.jpg",
        "page_abs":    "https://gallery.example.com/image/holiday-2025/favourite.jpg",
        "api_abs":     "https://gallery.example.com/api/photo/holiday-2025/favourite.jpg"
      }
    }
  ]
}
```

`count` is the items in this response, `total` the size of the whole match. The `*_abs` URLs use the `PUBLIC_BASE_URL` env if set (recommended when running behind a TLS-terminating reverse proxy), otherwise the request's own scheme + host. Every endpoint below returns photos in exactly this shape (plus `tags` when asked for).

### `GET /api/albums`

| Query param | Default | Meaning                                                                        |
|-------------|---------|--------------------------------------------------------------------------------|
| `parent`    | —       | List the children of this album; omit for the top level                        |
| `sort`      | cfg     | `curated` (when `gallery.cfg` sets `album_order`) or any album sort key        |
| `depth`     | `1`     | `1..4` — nest each card's own sub-albums under `children`                      |
| `showcase`  | —       | `1` for showcase albums only, `0` for the archive                              |
| `limit`     | `200`   | Max cards per level, `1..200`                                                  |

Each album card:

```json
{
  "album": "japan_2026",
  "name": "japan_2026",
  "count": 412,
  "latest": "2026-09-02T18:11:44",
  "sub_count": 3,
  "is_showcase": true,
  "collection": true,
  "tags": ["travel", "summer"],
  "cover": { "rel_path": "…", "urls": { "thumb": "…", "preview": "…", "thumb_abs": "…", "preview_abs": "…" } },
  "icon": { "url": "/album-icon/japan_2026?v=…", "url_abs": "…" },
  "urls": { "page": "/album/japan_2026", "api": "/api/album/japan_2026", "page_abs": "…", "api_abs": "…" }
}
```

`count` is recursive (the whole subtree), so it matches the number on the album grid. When `sort=curated` and no `parent` is given, the response also carries `sections` — the `#group` frames of the curated view.

### `GET /api/album/{album}`

Everything one album page knows.

| Query param | Default | Meaning                                                                 |
|-------------|---------|--------------------------------------------------------------------------|
| `images`    | `0`     | `1` to include the photo grid (otherwise only `images.total` comes back) |
| `sort`      | cfg     | `curated` (when `album.cfg` sets `order`), `days` (when the album spans more than one day) or any image sort key |
| `tag`       | —       | Filter the grid by photo tag                                            |
| `subtree`   | cfg     | `0`/`1` to override the album's collection scope                        |
| `limit`     | `200`   | Grid page size, `1..200`                                                |
| `offset`    | `0`     | Grid paging offset                                                      |
| `tags`      | `0`     | `1` to include each photo's tags                                        |
| `lang`      | request | `en` / `de` / `jp`                                                      |

```json
{
  "album": { … the card above … },
  "breadcrumbs": [{ "name": "japan_2026", "path": "japan_2026", "icon": "/album-icon/japan_2026?v=…" }],
  "scope": { "album": "japan_2026", "collection": true, "subtree": true },
  "description": { "html": "<p>…</p>", "lang": "de" },
  "stats": { "context": [{ "key": "LOC", "val": "Japan" }], "capture": [{ "key": "SPAN", "val": "…" }], "has": true },
  "effect": "sakura",
  "font": { "css": "/album-font.css/…?v=…", "scale": 1.25, "preload": { "href": "…", "type": "font/otf" } },
  "trip": { "key": "japan_2026", "stops": [ … ] },
  "reel": { "mode": "featured", "items": [ … photos … ] },
  "sub_albums": [ … cards … ],
  "photo_tags": ["night", "street"],
  "sort": { "current": "curated", "default": "curated", "options": [{ "key": "curated", "label": "Curated", "active": true }] },
  "images": { "total": 412, "count": 50, "limit": 50, "offset": 0, "tag": null, "items": [ … photos … ] },
  "lang": "de"
}
```

`reel.mode` is `featured` / `random` / `off` (album.cfg `reel =`), and its items come in the album's configured `featured` order. `photo_tags` are the `.tags` sidecar tags available inside the album's scope (what `?tag=` filters on) — the album's own display tags sit on `album.tags`. `font`, `effect` and `trip` are `null` when the album configures none, as is `album.icon` (and each breadcrumb's `icon`) for an album without a mark.

### `GET /api/photos`

| Query param | Default     | Meaning                                                             |
|-------------|-------------|----------------------------------------------------------------------|
| `album`     | —           | Scope to an album (collection-aware)                                |
| `subtree`   | cfg         | `0`/`1` to override that scope                                      |
| `tag`       | —           | Photo tag                                                           |
| `q`         | —           | Search album path, filename and tags                                |
| `featured`  | `0`         | `1` for featured photos only                                        |
| `sort`      | `date_desc` | Any image sort key                                                  |
| `random`    | `0`         | `1` for random order                                                |
| `tags`      | `0`         | `1` to include each photo's tags                                    |
| `limit`     | `50`        | `1..200`                                                            |
| `offset`    | `0`         | Paging offset                                                       |

Filters compose, so `?album=japan_2026&tag=night&featured=1` is a valid question. Returns `count`, `total`, `limit`, `offset`, `sort`, `scope`, `filters` and `items`.

### `GET /api/photo/{rel_path}`

One photo — the photo object above, plus:

```json
{
  "tags": ["night", "street"],
  "breadcrumbs": [ … ],
  "description": "text embedded in the file's XMP/EXIF",
  "exif": [{ "key": "Camera", "val": "X100V" }, { "key": "Aperture", "val": "f/2.0" }],
  "exif_raw": { "FNumber": 2.0 },
  "album_url": { "page": "/album/…", "api": "/api/album/…" },
  "neighbours": {
    "scope": { "album": "japan_2026", "collection_root": "japan_2026", "count": 412 },
    "sort": "curated", "index": 17, "prev": "…/a.jpg", "next": "…/b.jpg"
  }
}
```

`exif` is formatted and translated (`lang=`), `exif_raw` is what the file carried — GPS is dropped from both when `HIDE_GPS=1`. Neighbours walk the photo's own folder by default; pass `col=<album>` (a collection root above it) to walk the whole collection instead, exactly like the single-image view does. `neighbours=0` skips the walk.

### `GET /api/tags`

Photo tags with how many photos carry each, most-used first. `album=` scopes them (collection-aware), `subtree=0|1` overrides that, `limit` caps the list.

### `GET /api/stats`

```json
{
  "images": 4211, "featured": 63,
  "albums": { "top_level": 9, "total": 34, "showcase": 3 },
  "tags": 57, "bytes": 91234567890, "bytes_h": "85 GB",
  "span": { "from": "2019-04-02T…", "to": "2026-09-02T…", "label": "2019 – 2026" },
  "lang": "en"
}
```

**Embed example** — drop into any HTML page:

```html
<div id="lucya-feed"></div>
<script>
fetch('https://gallery.example.com/api/showcase?limit=8&random=1')
  .then(r => r.json())
  .then(({ items }) => {
    const root = document.getElementById('lucya-feed');
    for (const it of items) {
      const a = document.createElement('a');
      a.href = it.urls.page_abs;
      a.target = '_blank';
      a.rel = 'noopener';
      const img = document.createElement('img');
      img.src = it.urls.thumb_abs;
      img.alt = it.filename;
      img.loading = 'lazy';
      a.appendChild(img);
      root.appendChild(a);
    }
  });
</script>
```

## Tags

Two separate things share the name, so keep them apart:

**Album tags** describe the album and are set in its `album.cfg`:

```ini
tags = paris, night, street     # a leading # is optional
```

They render under the album's hero title and nowhere else. Labels only — they
don't filter and aren't indexed.

**Photo tags** describe one image and live as sidecar files in the filesystem —
same workflow as the rest of the gallery:

```bash
# Drop a .tags file next to the image
echo "holiday, italy, beach" > photos/holiday-2025/DSC_0001.jpg.tags
```

The scanner reads the file on the next indexing pass and links the tags. Empty or delete the file → tags disappear. The watcher reacts to changes live; the periodic scan picks them up at the next interval at the latest. These are the ones the album's tag bar filters on (`?tag=`), the image page lists, and search matches.

## Folder structure

| Path           | Purpose                                                  |
|----------------|----------------------------------------------------------|
| `photos/`      | Your originals + `.tags` sidecars (mounted read-only)    |
| `thumbnails/`  | Generated grid thumbnails (cache, can be wiped anytime)  |
| `previews/`    | Generated stage previews (cache, can be wiped anytime)   |
| `data/`        | SQLite DB with EXIF cache and tag index                  |
| `data/control/`| Flag files the CLI and the server talk through (see below) |

## Configuration

| Variable        | Default       | Meaning                                                    |
|-----------------|---------------|------------------------------------------------------------|
| `PHOTOS_DIR`    | `/photos`     | Where the original folders live                            |
| `THUMBS_DIR`    | `/thumbnails` | Where grid thumbnails are stored                           |
| `PREVIEWS_DIR`  | `/previews`   | Where stage previews are stored                            |
| `DATA_DIR`      | `/data`       | SQLite database                                            |
| `THUMB_SIZE`    | `480`         | Max edge of grid thumbnails (px)                           |
| `PREVIEW_SIZE`  | `1600`        | Max edge of stage previews (px)                            |
| `SCAN_INTERVAL` | `0`           | Periodic rescan in seconds (0 = off). For SMB use ~300.    |
| `ENABLE_WATCHER`| `1`           | inotify watcher (on SMB/NFS, prefer `0` and use interval)  |
| `HIDE_GPS`      | `1`           | Strip GPS from EXIF display                                |
| `STRIP_GPS`     | `1`           | Strip GPS from the original file on import (in-place)      |
| `PUBLIC_BASE_URL`| (auto)       | Absolute base URL used in OG tags + `/api/showcase` URLs   |

## Operations CLI

Everything operational is one command:

```bash
python -m app.cli
```

Without arguments it draws the dashboard — masthead, what the server is
doing, and what the archive holds — and then keeps an interactive menu
underneath it (only when it actually has a terminal; piped or in a cron job
it prints the dashboard and exits).

```
┌─ LUCYA.SYSTEMS GALLERY ──────────────────────────────────── OPS CONSOLE ─┐
│                                                                          │
│   ________       .__  .__                                                │
│  /  _____/_____  |  | |  |   ___________ ___.__.                         │
│ /   \  ___\__  \ |  | |  | _/ __ \_  __ <   |  |                         │
│ \    \_\  \/ __ \|  |_|  |_\  ___/|  | \/\___  |                         │
│  \______  (____  /____/____/\___  >__|   / ____|                         │
│         \/     \/               \/       \/                              │
│                                                                          │
│ LUCYA.SYSTEMS GALLERY  ·  OPS CONSOLE  ·  API v2                         │
│                                                                          │
├─ SYSTEM ─────────────────────────────────────────────────────────────────┤
│ SERVER      running · pid 4711 · up 2h 14m · heartbeat 6.0s ago           │
│ INDEXER     running                                                      │
│ SCAN        idle · last periodic 4m 12s ago in 1.8s → 12 indexed         │
│ WATCHER     on · running · 0 event(s) queued                             │
├─ ARCHIVE ────────────────────────────────────────────────────────────────┤
│ PHOTOS      328                                                          │
│ ALBUMS      10 with photos · 14 incl. parents                            │
│ FEATURED    5 photo(s) · 1 showcase album(s)                             │
│ …                                                                        │
├─ LARGEST ALBUMS ─────────────────────────────────────────────────────────┤
│   japan_2026/kansai/osaka       ██████████████████████    64 2.5 GB      │
│   japan_2026/hokkaido/sapporo   █████████████████████·    61 2.4 GB      │
│   …                                                                      │
├─ MENU ───────────────────────────────────────────────────────────────────┤
│   1   status   server, indexer, last scan, watcher queue                 │
│   2   scan     index now (optionally one album, --force)                 │
│   …                                                                      │
│   r redraw · w live dashboard · h help · q quit                          │
└──────────────────────────────────────────────────────────────────────────┘
  select ›
```

Every view is one frame — the dashboard, the menu, and each report — so the
CLI reads as a single interface rather than a stack of loose output. Long
values fold under their own column instead of being cut off.

Or go straight at a single command:

```bash
python -m app.cli <command> [options]
```

In Docker, run it inside the container:

```bash
docker compose exec gallery python -m app.cli status
```

| Command | What it does |
|---------|--------------|
| *(no command)* | Dashboard, then the menu — the same as `dash` followed by `menu` |
| `dash` | Masthead, live state and archive statistics on one screen: counters, date span, largest albums and capture-month activity as meters, format breakdown, cache size, and a quick index-vs-disk check. `--watch` repaints it on a timer (`--interval`, default 5s) until ctrl-c — a live view of what the indexer is doing |
| `menu` | Interactive console: pick a command by number or name, get prompted for its arguments, run it, come back. `↵` repeats the last one, `r` redraws, `w` opens the live dashboard, `q` quits |
| `help` | Command overview and the usage cheat sheet |
| `term` | What this terminal supports and why colour or the menu are off — see [Terminals](#terminals) |
| `status` | Live state: is the server up, is the indexer paused, is a scan running (or what the last one did), how many events sit in the watcher queue, index counters, paths, effective config |
| `scan [album] [--force]` | Run an indexing pass **now** instead of waiting for `SCAN_INTERVAL`. Optionally limited to one album subtree. `--force` re-indexes and re-derives even when mtimes say nothing changed |
| `pause [reason]` | Suspend indexing: no periodic scan, and the watcher stops processing events (it keeps queueing them) |
| `resume [--scan]` | Lift the pause; `--scan` also requests a scan right away |
| `doctor [--album X]` | Full integrity check — see below. **Exits 1** when it found something, so it works as a cron/CI check |
| `thumbs [--rebuild] [--all] [--prune]` | Report, rebuild or prune generated thumbnails and previews. Dry run by default: `--rebuild` builds missing/stale ones (`--all` rebuilds everything), `--prune` lists generated files with no source photo and only deletes them with `--apply` |
| `featured [album]` | Which `album.cfg` entry featured which photo, which entries match nothing, and whether the `is_showcase` flags in the DB still agree. `--recompute` rewrites the flags |
| `cfg <album>` / `cfg --gallery` | An `album.cfg` / `gallery.cfg` exactly as the app parses it, plus what it resolves to (cover, reel, description languages) and everything wrong with it |
| `photo <rel_path>` | Everything the app knows about one photo: row, mtime drift, tags, why it is (not) featured, derivative state, URLs, prettified EXIF (`--exif` for the raw block) |
| `album [name]` | One album in full: photo count and size, capture span, flags, cover, featured count, tags, icon/font/effect, which `album_*.md` exist, sub-albums, and any cfg issues. Without a name: every album with its counts and flags |
| `trip [album]` | The resolved trip dashboard — stops, dates, which sub-album each leg links to, photo counts. Without an album: which trips are configured and whether their album exists |
| `welcome [--desktop] [--mobile]` | What the welcome hero actually resolves to per device class: which `gallery.cfg` key won, the mode (manual / showcase / random), and which entries were skipped because they are not indexed. **Exits 1** when something was skipped |
| `tags [tag] [--album X]` | The tag vocabulary with photo counts, and drift between the `.tags` sidecars on disk and the index. With a tag name: the photos carrying it. **Exits 1** on drift or on a sidecar whose photo is gone |
| `search <query> [--album X]` | The same query the `/search` page runs — album name, file name and tag — from the terminal |
| `gps [album] [--strip]` | Which originals still carry GPS coordinates, alongside the effective `HIDE_GPS` / `STRIP_GPS` settings. **Exits 1** when any do. `--strip` **rewrites those originals in place** to remove the block |
| `export [--out F] [--list]` | Archive `gallery.cfg` and every `.album/` folder — config, descriptions, icons, title fonts — to a `.tar.gz`. Photos are left out; they are already the backup. `--list` shows what would go in without writing |
| `i18n` | EN/DE/JP completeness in `app/i18n.py`, keys used but undefined (they render as the key), and whether the `UI_STRINGS` mirror in `app.js` has the same keys in every language |

Every command also takes `--json` for a machine-readable dump, `--no-color`
for plain output, `--color` to force it on, and `-i` / `--interactive` to
force the prompts on.

### Terminals

Long operations show a spinner or a progress meter while they run (`scan`
waiting on the server, `doctor`, `thumbs --rebuild`), and the dashboard can
repaint itself with `dash --watch`. All of that needs to know whether a human
is actually watching, which `isatty()` alone does not reliably answer:

- **Git Bash / MSYS2 / Cygwin (mintty) on Windows** reach a native Python
  through a *named pipe*, so `isatty()` says "not a terminal" and the classic
  symptom is "no colours in Git Bash". The CLI therefore asks the handle for
  its pipe name and recognises an MSYS/Cygwin pty
  (`\msys-…-pty0-to-master`) — while a real redirect (`… > out.txt`) on the
  same machine still correctly counts as *not* a terminal.
- **stdin redirected, screen still attached** (`… < file`, some `docker exec`
  invocations): the menu falls back to reading `/dev/tty`.
- **No terminal at all** (cron, CI, `docker compose exec -T`): the menu
  refuses to prompt instead of hanging, `--watch` prints once instead of
  looping forever, and spinners stay silent so log files do not fill up with
  half-drawn frames.

Colour is on when stdout is a terminal, off when it is piped, off with
`NO_COLOR` or `--json`, and on regardless with `FORCE_COLOR=1` or `--color`.
Rules and meters follow the real terminal width; the logo collapses to a
single line below 50 columns.

When something looks wrong, ask:

```bash
python -m app.cli term
```

It prints what was detected (`stdout.isatty`, mintty, `/dev/tty`, `TERM`,
`COLORTERM`, `NO_COLOR`, width, encoding), the resulting verdict for colour /
menu / repainting / pictures, and what to do about it — `ssh -t`,
`docker compose exec` without `-T`, or simply `--color` / `--interactive`.

### Pictures in the terminal

The masthead is text by default — the letterforms are part of the interface,
and the logo itself is too fine-grained to survive being squeezed into
terminal cells. Terminals that can show a real bitmap can have one anyway;
`--logo` picks how:

| Mode | What it does | Where it works |
|------|--------------|----------------|
| `ascii` | the block letterforms — **the default** | any terminal |
| `auto` | the best picture protocol this terminal supports | everywhere |
| `kitty` | PNG through the kitty graphics protocol, pixel-perfect | kitty, ghostty |
| `iterm` | PNG through iTerm2 inline images, pixel-perfect | iTerm2, WezTerm |
| `blocks` | two pixels per cell as a half-block in 24-bit colour — coarse, and it shows on a detailed logo | anything with truecolor: Windows Terminal, mintty, VS Code, gnome-terminal, … |
| `off` | no masthead at all | any terminal |

Detection (for `auto`) is env-sniffing only (`KITTY_WINDOW_ID`, `TERM`,
`TERM_PROGRAM`, `COLORTERM`) — no escape-sequence queries, so a terminal that
never answers can never hang the CLI. Sixel terminals are not auto-detected
for the same reason; `--logo blocks` covers them. Into a pipe or a log file
`auto` falls back to the letterforms, so a redirect never collects binary
image data — and `kitty`/`iterm` are skipped inside a frame, because those
protocols move the cursor themselves and would tear the box apart.

The picture comes from `app/static/logo/lucya_logo.png`; if it is missing,
run `python tools/render_logo.py` (see above) — `term` says so too. Its
transparency is preserved in every mode: the logo sits on your terminal
background, not in a white box.

```bash
python -m app.cli --logo kitty
```

### How `pause` and `scan` reach the running server

### Filtering without a reload

The tag bar and the sort menu are plain links: they carry a real `href`, work
with JavaScript off, and are followed by crawlers. With JavaScript on, a click
that leads to the *same* page with a different query is intercepted — the new
HTML is fetched and only the regions marked `data-live` are swapped in.

The nav, the hero, the ambient background video, the fonts and the scroll
position are never touched, so filtering by tag or changing the sort no longer
rebuilds the whole document. `history.pushState` keeps the URL honest and Back
/ Forward work normally.

The swapped-in grid runs the same entrance cascade a fresh page load does:
tiles already on screen stagger in at 45 ms apart, the rest reveal as they
scroll into view. It is literally the same `scrollReveal()` — which means it
also inherits the motion gating, so `prefers-reduced-motion`, data-saver and
low-end devices get the new grid instantly and statically instead.

The rule is deliberately narrow: same pathname, different query, and the link
must sit inside a `data-live` region. Opening another album, a photo, or
switching language is a different page and still navigates for real — as does
a middle-click, a ctrl-click, or any fetch that fails, which falls straight
back to an ordinary navigation.

The HTTP surface stays **read-only** — there is no control endpoint that makes
the server do something, and no token to leak. The CLI and the server talk
through three small files in `data/control/` instead:

| File | Written by | Meaning |
|------|-----------|---------|
| `paused.json` | CLI | Indexing is suspended (holds the reason and since when) |
| `scan.request.json` | CLI | A scan is queued; the server consumes the file when it picks it up |
| `status.json` | server | The live snapshot `status` reads, re-stamped as a heartbeat |

The server's control loop looks at that directory every 2 seconds, so a
requested scan starts within ~2s, and `scan` waits for the result by default
(`--no-wait` to just queue it). The rules worth knowing:

- **A pause is persistent.** It survives a restart on purpose — a pause set
  before a maintenance restart is still in effect afterwards, including the
  startup scan. Only `resume` lifts it.
- **A pause never loses a change.** Watcher events keep accumulating while
  paused (keyed by path, so churn on one file collapses into one entry) and
  are processed on resume.
- **A manual `scan` ignores the pause.** That is the escape hatch for
  indexing one deliberate change without lifting a maintenance pause.
- **`pause` works with the server down** — the flag file is simply already
  there when it starts. `status` says so instead of pretending.

### What `doctor` checks

| Finding | Meaning |
|---------|---------|
| `unindexed` / `missing_file` | A photo on disk that no row knows about, or a row whose file is gone |
| `stale_index` | The file changed after it was indexed (EXIF/date/size in the DB are outdated) |
| `missing_thumb` / `stale_thumb` (same for previews) | A derivative was never built, or is older than its source |
| `orphan_derivative` | A generated file with no source photo — left over from a deleted or renamed original |
| `unreadable` | The source file cannot be opened at all (truncated upload, wrong extension). Those stay in the gallery without a thumbnail |
| `config` | Anything wrong in an `album.cfg` / `gallery.cfg`: unknown keys, a `cover`/`featured`/`order` entry that matches no photo, a missing `icon`/`font` file, an invalid `reel`/`sort`/`effect` value, a `welcome` entry that does not resolve |
| `featured_drift` | The `is_showcase` flags in the DB no longer match what the `album.cfg` files say |
| `database` | `PRAGMA integrity_check`, orphaned tag links, tags no photo uses any more |

### What writes what

Nothing in the CLI ever touches `photos/` — the originals stay untouched, as
everywhere else in this project.

| Writes | Commands |
|--------|----------|
| nothing | `status`, `doctor`, `cfg`, `photo`, `trip`, `i18n`, `thumbs` (without flags), `featured` (without `--recompute`) |
| the SQLite index | `scan`, `featured --recompute` |
| generated thumbnails/previews | `scan`, `thumbs --rebuild`, `thumbs --prune --apply` (deletes) |
| the control files | `pause`, `resume`, `scan` |

### Examples

```bash
# is anything running, and what did the last scan do?
python -m app.cli status

# freshly dropped an album on the share and do not want to wait 5 minutes
python -m app.cli scan japan_2026/kansai

# reorganising folders — stop the indexer from reacting to every move
python -m app.cli pause "resorting kansai"
python -m app.cli resume --scan

# why is this photo not in the reel?
python -m app.cli photo japan_2026/kansai/osaka/IMG_4853.png
python -m app.cli featured japan_2026

# after changing THUMB_SIZE
python -m app.cli thumbs --rebuild --all

# nightly health check (exits 1 when it finds something)
python -m app.cli doctor --json

# what the front page will actually show, and what it skipped
python -m app.cli welcome

# tag vocabulary, and whether the sidecars and the index still agree
python -m app.cli tags

# privacy audit: which originals still carry coordinates
python -m app.cli gps

# snapshot every hand-written file (config, text, icons, fonts)
python -m app.cli export --out backups/config.tar.gz
```

## Security / hosting

The app is fully **read-only** by design:

- No write API, no uploads, no tag editing via the web
- `photos/` is mounted `:ro` — even a hypothetical code bug can't touch the originals
- Tags and thumbnails live in `data/` and `thumbnails/` — none of it is security-critical
- Path traversal blocked (`_safe_rel`)
- GPS stripping on (`HIDE_GPS=1`)

**Built-in security headers** (set by middleware in `app/main.py`):

- `Content-Security-Policy` — strict `'self'`-only policy, no inline scripts/styles, no external resources. `frame-ancestors 'none'` (clickjacking protection)
- `X-Frame-Options: DENY` — same, for older browsers
- `X-Content-Type-Options: nosniff` — disables MIME sniffing
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: interest-cohort=(), browsing-topics=()` — opts out of FLoC/Topics
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`

**Recommended Cloudflare setup** for public hosting:

- **Bot Fight Mode** on
- **Rate Limiting** on `/full/*` if you want to cap bandwidth on originals
- **Cache Rules** for `/thumb/*`, `/preview/*`, `/static/*` (long TTL — those URLs are content-addressed and immutable)

## Endpoints

All GET, all public:

- `GET /` — welcome screen (live-view hero cycling through the `gallery.cfg` feed: curated list, showcase or random; plus a Showcase Albums section)
- `GET /albums` — album overview (showcase albums section + main grid; `?sort=`)
- `GET /album/{album}` — images in an album (`?tag=`, `?sort=`)
- `GET /image/{album}/{file}` — detail view (stage shows preview by default; `?sort=` preserved for prev/next ordering)
- `GET /thumb/{album}/{file}` — grid thumbnail (lazy generated)
- `GET /preview/{album}/{file}` — stage preview (lazy generated)
- `GET /full/{album}/{file}` — original file
- `GET /album-font.css/{album}` — generated stylesheet for an album's `font =` face (`@font-face` + `--album-title-font`, plus `--album-title-scale` when it sets `font_scale =`); 404 when the album sets none
- `GET /album-font/{album}` — the font file itself; only ever the one named in that album's `album.cfg`
- `GET /album-icon/{album}` — the album's `icon =` mark; only ever the file named in that album's `album.cfg`, 404 when it sets none
- `GET /search?q=…` — search (`?sort=`)
- `GET /lang/{en|de|jp}?next=…` — set the language cookie, 303 back to `next` (relative paths only)
- `GET /api` + `/api/stats` + `/api/albums` + `/api/album/{album}` + `/api/photos` + `/api/photo/{rel_path}` + `/api/tags` + `/api/showcase` + `/api/shuffle` — the JSON API, CORS-enabled (see [API](#api))
- `GET /api/trip-weather?trip=…` — current conditions per trip stop plus today's high/low, served as a same-origin proxy to [Open-Meteo](https://open-meteo.com/) (weather data CC BY 4.0). Server-side cache (15 min); the visitor's browser never contacts a third party, so no cookies and no consent banner are involved.

## Local development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p photos thumbnails previews data
uvicorn app.main:app --reload
```

## Notes

- First scan over a large library takes a while (EXIF + two thumbnail sizes). After that everything is cached.
- Delete an image: remove it from `photos/` — watcher/scan clean up DB entry, thumbnail, and preview.
- Rename a tag: edit the `.tags` file.
- Thumbnails, previews, and DB can be wiped any time — they are regenerated on the next scan.
- Something looks off? `python -m app.cli doctor` compares index, files, derivatives and config in one pass.
