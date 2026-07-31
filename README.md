# lucya.systems gallery

A lean, read-only web image gallery with folder-based albums, EXIF display, sidecar-file tags, and automatic thumbnail/preview generation. Deployed via Docker. Safe for public hosting behind Cloudflare.

## Features

- **Folder = album:** every subfolder in `photos/` is automatically an album. Drop an image in → it appears in the album.
- **Fully automatic indexing:** filesystem watcher (local) and/or periodic rescan (for SMB/NFS). No manual buttons in the web UI.
- **Two-tier images:** `/thumb/...` (480 px) for grids, `/preview/...` (1600 px) for the detail view stage. The original (`/full/...`) only loads when you click *Load original*.
- **EXIF:** camera, lens, exposure, ISO, focal length, … on the detail page. GPS coordinates are stripped by default (privacy).
- **Tags:** per-album ones come from `album.cfg` and label the album in its hero; per-photo ones are sidecar files (e.g. `IMG_0001.jpg.tags` containing `holiday, beach, sunset`) — click one in the album view to filter.
- **Showcase:** mark a photo (`_hero.jpg`) or a whole album (`_best-of/`) with an underscore prefix to surface it on the welcome screen, on the album overview, and via `/api/showcase` JSON for embedding on other sites.
- **Search & sort:** top bar searches album, file, and tag names; sort by date, name or size on every list view — plus a "Curated" order defined in `album.cfg` / `gallery.cfg`, which can also preselect the default sort.
- **Three languages (EN / DE / JP):** selector in the top-right corner, cookie-backed with an `Accept-Language` fallback. Album descriptions are per-language markdown files (`album_en.md` / `album_de.md` / `album_jp.md`); UI strings live in `app/i18n.py`. See [Languages](#languages--i18n).
- **Mobile-friendly:** responsive grid, large touch targets, keyboard navigation (← → ESC) on desktop.
- **Read-only:** no write endpoints, no uploads. The `photos/` mount is `:ro`. No attack surface for upload/tag-injection exploits.
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

Mark individual photos and/or whole albums as **showcased** with a single character at the start of the filename or folder name (default: `_`).

| Where the marker sits         | Effect                                                                                |
|-------------------------------|---------------------------------------------------------------------------------------|
| **Filename** (`_hero.jpg`)    | Photo is featured: appears in the welcome hero feed, in the featured hero slideshow of its album *and its parent albums*, and in the `/api/showcase` feed. Gets a ★ in the album grid. |
| **Album folder** (`_best-of/`)| Album is featured: shown in a dedicated "Showcase Albums" section on the welcome screen and on `/albums`, with a `★ FEATURED` badge. Photos inside still need their own `_` to be individually featured. |

The two flags are **independent** — putting a photo into a `_showcase` album does NOT auto-feature it. Each photo opts in with its own filename prefix. Display strips the leading marker, so `_best-of/` shows up as "best-of" and `_hero.jpg` as "hero.jpg"; URLs keep the raw name on disk.

Examples:

```
photos/
├── _best-of/                  ← showcase album
│   ├── _portrait.jpg          ← also a showcase photo (in /api/showcase)
│   └── filler.jpg             ← in the album, but not featured
├── holiday-2025/              ← regular album
│   ├── _favourite.jpg         ← showcase photo (featured even though album isn't)
│   └── DSC_0042.jpg
└── …
```

Change the marker globally via `SHOWCASE_MARKER` (set it to an empty string to disable the whole feature). Showcase flags are re-evaluated on every startup, so toggling files / changing the marker takes effect after a restart without needing a full re-scan.

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
city stops of the itinerary timeline, which read the mark off each stop's own
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
| `showcase`   | `true` / `false`                | Featured album: ★ rail on `/albums` and the welcome screen (replaces the `_` folder prefix). |
| `featured`   | paths, or `*` / `all`           | Featured photos: welcome hero, `/api/showcase`, the album's reel (replaces the `_` filename prefix). Paths are relative to the album; bare filenames match anywhere in the subtree. The album's reel shows them in exactly this order. |
| `cover`      | one path                        | Pin the album cover instead of auto-picking the newest photo.                              |
| `reel`       | `featured` / `random` / `off`   | What the hero slideshow at the top of the album shows: the featured photos (default), random photos from the album's subtree, or nothing (hidden). |
| `order`      | paths                           | Curated photo order — adds a **Curated** entry to the album's sort menu. Photos not listed follow, newest first. |
| `sort`       | `curated`, `date_desc`, `date_asc`, `name_asc`, `name_desc`, `size_desc`, `size_asc` | Preselect the sort option for this album's grid (visitors can still switch). |
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

- Paths are relative to `photos/` (`album/file.jpg`, nested albums allowed). The showcase marker may be omitted (`best-of/hero.jpg` finds `_best-of/_hero.jpg`), backslashes are tolerated.
- Entries accumulate in order (max 24, duplicates collapse).
- Entries that aren't indexed are skipped with a log warning; if nothing resolves, the feed falls back to showcase/random as if the file weren't there.
- With a hand-picked list the hero shows a `CURATED` label and hides the ⟳ TUNE (reshuffle) button.
- `welcome_mobile` / `welcome_desktop` accept the same syntax as `welcome` and win over it for their device class. Phones are detected via the User-Agent (`Mobi`); Android tablets and iPads in desktop mode get the desktop feed.

## API

A read-only JSON view of everything the pages render — albums, photos, EXIF, tags, stats — so you can embed the gallery elsewhere or build your own front end on it. CORS is open, responses are cached for 5 minutes, errors come back as JSON (`{"error": …, "status": …}`).

`GET /api` lists every endpoint with its parameters, so the API describes itself:

| Endpoint                    | Returns                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `GET /api`                  | Endpoint index, sort keys, languages, marker                            |
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
  "scope": { "album": "_japan_2026", "collection": true, "subtree": true }
  ```

  Pass `subtree=0` to force the plain folder scope on a collection album, or `subtree=1` to widen a normal album. Album paths tolerate a stripped showcase marker (`japan_2026` finds `_japan_2026`).
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
  "marker": "_",
  "scope": { "album": null, "collection": false, "subtree": false },
  "items": [
    {
      "rel_path": "holiday-2025/_favourite.jpg",
      "album": "holiday-2025",
      "filename": "_favourite.jpg",
      "display_album": "holiday-2025",
      "display_filename": "favourite.jpg",
      "width": 4032,
      "height": 3024,
      "size": 8123456,
      "taken_at": "2025-08-14T19:42:01",
      "mtime": 1755193321.0,
      "featured": true,
      "urls": {
        "thumb":       "/thumb/holiday-2025/_favourite.jpg",
        "preview":     "/preview/holiday-2025/_favourite.jpg",
        "full":        "/full/holiday-2025/_favourite.jpg",
        "page":        "/image/holiday-2025/_favourite.jpg",
        "api":         "/api/photo/holiday-2025/_favourite.jpg",
        "thumb_abs":   "https://gallery.example.com/thumb/holiday-2025/_favourite.jpg",
        "preview_abs": "https://gallery.example.com/preview/holiday-2025/_favourite.jpg",
        "full_abs":    "https://gallery.example.com/full/holiday-2025/_favourite.jpg",
        "page_abs":    "https://gallery.example.com/image/holiday-2025/_favourite.jpg",
        "api_abs":     "https://gallery.example.com/api/photo/holiday-2025/_favourite.jpg"
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
  "album": "_japan_2026",
  "name": "japan_2026",
  "display_path": "japan_2026",
  "count": 412,
  "latest": "2026-09-02T18:11:44",
  "sub_count": 3,
  "is_showcase": true,
  "collection": true,
  "tags": ["travel", "summer"],
  "cover": { "rel_path": "…", "urls": { "thumb": "…", "preview": "…", "thumb_abs": "…", "preview_abs": "…" } },
  "icon": { "url": "/album-icon/_japan_2026?v=…", "url_abs": "…" },
  "urls": { "page": "/album/_japan_2026", "api": "/api/album/_japan_2026", "page_abs": "…", "api_abs": "…" }
}
```

`count` is recursive (the whole subtree), so it matches the number on the album grid. When `sort=curated` and no `parent` is given, the response also carries `sections` — the `#group` frames of the curated view.

### `GET /api/album/{album}`

Everything one album page knows.

| Query param | Default | Meaning                                                                 |
|-------------|---------|--------------------------------------------------------------------------|
| `images`    | `0`     | `1` to include the photo grid (otherwise only `images.total` comes back) |
| `sort`      | cfg     | `curated` (when `album.cfg` sets `order`) or any image sort key          |
| `tag`       | —       | Filter the grid by photo tag                                            |
| `subtree`   | cfg     | `0`/`1` to override the album's collection scope                        |
| `limit`     | `200`   | Grid page size, `1..200`                                                |
| `offset`    | `0`     | Grid paging offset                                                      |
| `tags`      | `0`     | `1` to include each photo's tags                                        |
| `lang`      | request | `en` / `de` / `jp`                                                      |

```json
{
  "album": { … the card above … },
  "breadcrumbs": [{ "name": "japan_2026", "path": "_japan_2026", "icon": "/album-icon/_japan_2026?v=…" }],
  "scope": { "album": "_japan_2026", "collection": true, "subtree": true },
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

Filters compose, so `?album=_japan_2026&tag=night&featured=1` is a valid question. Returns `count`, `total`, `limit`, `offset`, `sort`, `scope`, `filters` and `items`.

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
    "scope": { "album": "_japan_2026", "collection_root": "_japan_2026", "count": 412 },
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
  "marker": "_", "lang": "en"
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
      img.alt = it.display_filename;
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
| `SHOWCASE_MARKER`| `_`          | Filename / folder prefix marking showcase items (empty = off) |
| `PUBLIC_BASE_URL`| (auto)       | Absolute base URL used in OG tags + `/api/showcase` URLs   |

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
