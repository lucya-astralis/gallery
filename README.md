# lucya.systems gallery

A lean, read-only web image gallery with folder-based albums, EXIF display, sidecar-file tags, and automatic thumbnail/preview generation. Deployed via Docker. Safe for public hosting behind Cloudflare.

## Features

- **Folder = album:** every subfolder in `photos/` is automatically an album. Drop an image in → it appears in the album.
- **Fully automatic indexing:** filesystem watcher (local) and/or periodic rescan (for SMB/NFS). No manual buttons in the web UI.
- **Two-tier images:** `/thumb/...` (480 px) for grids, `/preview/...` (1600 px) for the detail view stage. The original (`/full/...`) only loads when you click *Load original*.
- **EXIF:** camera, lens, exposure, ISO, focal length, … on the detail page. GPS coordinates are stripped by default (privacy).
- **Tags via sidecar files:** drop a `.tags` file next to an image (e.g. `IMG_0001.jpg.tags` containing `holiday, beach, sunset`). Click a tag in the album view to filter.
- **Showcase:** mark a photo (`_hero.jpg`) or a whole album (`_best-of/`) with an underscore prefix to surface it on the welcome screen, on the album overview, and via `/api/showcase` JSON for embedding on other sites.
- **Search & sort:** top bar searches album, file, and tag names; sort by date, name or size on every list view.
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
| **Filename** (`_hero.jpg`)    | Photo is featured: appears in the welcome hero feed, in its album's "featured" strip, and in the `/api/showcase` feed. Gets a ★ in the album grid. |
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

## Welcome feed (`gallery.cfg`)

By default the welcome hero cycles through a random selection of showcased photos (falling back to fully random when nothing is showcased). To pick the images yourself, drop a `gallery.cfg` into the **root of `photos/`** — same `key = value` format as `album.cfg`, `#`/`;` start comments:

```ini
# photos/gallery.cfg — welcome hero feed
# one of:
#   welcome = showcase      ← random featured photos (default, same as no file)
#   welcome = random        ← random photos, ignore the featured flags
#   welcome = <paths>       ← hand-picked list, shown in exactly this order
welcome = berlin_dec_2025/IMG_0646.png
welcome = paris_march_2026/IMG_2222.png, frankfurt_feb_2026/IMG_1628.png
```

Rules for the hand-picked list:

- Paths are relative to `photos/` (`album/file.jpg`, nested albums allowed). The showcase marker may be omitted (`best-of/hero.jpg` finds `_best-of/_hero.jpg`), backslashes are tolerated.
- Repeat the `welcome` key per line and/or comma-separate — entries accumulate in order (max 24, duplicates collapse).
- Entries that aren't indexed are skipped with a log warning; if nothing resolves, the feed falls back to showcase/random as if the file weren't there.
- The file is re-read on every page load, so edits apply immediately — no restart needed. With a hand-picked list the hero shows a `CURATED` label and hides the ⟳ TUNE (reshuffle) button.

## API

A small JSON endpoint exposes the showcased photos so you can embed them on your own site. CORS is open and responses are cached for 5 minutes.

### `GET /api/showcase`

| Query param | Default | Meaning                                                     |
|-------------|---------|-------------------------------------------------------------|
| `limit`     | `50`    | Max items, clamped to `1..200`                              |
| `album`     | —       | Only return showcase photos inside this album folder        |
| `random`    | `0`     | `1` for random order; default is newest first (by EXIF date)|

**Response shape:**

```json
{
  "count": 2,
  "marker": "_",
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
      "urls": {
        "thumb":       "/thumb/holiday-2025/_favourite.jpg",
        "preview":     "/preview/holiday-2025/_favourite.jpg",
        "full":        "/full/holiday-2025/_favourite.jpg",
        "page":        "/image/holiday-2025/_favourite.jpg",
        "thumb_abs":   "https://gallery.example.com/thumb/holiday-2025/_favourite.jpg",
        "preview_abs": "https://gallery.example.com/preview/holiday-2025/_favourite.jpg",
        "full_abs":    "https://gallery.example.com/full/holiday-2025/_favourite.jpg",
        "page_abs":    "https://gallery.example.com/image/holiday-2025/_favourite.jpg"
      }
    }
  ]
}
```

The `*_abs` URLs use the `PUBLIC_BASE_URL` env if set (recommended when running behind a TLS-terminating reverse proxy), otherwise the request's own scheme + host.

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

Tags live as sidecar files in the filesystem — same workflow as the rest of the gallery:

```bash
# Drop a .tags file next to the image
echo "holiday, italy, beach" > photos/holiday-2025/DSC_0001.jpg.tags
```

The scanner reads the file on the next indexing pass and links the tags. Empty or delete the file → tags disappear. The watcher reacts to changes live; the periodic scan picks them up at the next interval at the latest.

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
- `GET /search?q=…` — search (`?sort=`)
- `GET /api/showcase` — JSON list of showcased photos, CORS-enabled (see [API](#api))
- `GET /api/shuffle?limit=N` — JSON list of random photos (used internally by the welcome CRT)

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
