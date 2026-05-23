# lucya.systems gallery

A lean, read-only web image gallery with folder-based albums, EXIF display, sidecar-file tags, and automatic thumbnail/preview generation. Deployed via Docker. Safe for public hosting behind Cloudflare.

## Features

- **Folder = album:** every subfolder in `photos/` is automatically an album. Drop an image in → it appears in the album.
- **Fully automatic indexing:** filesystem watcher (local) and/or periodic rescan (for SMB/NFS). No manual buttons in the web UI.
- **Two-tier images:** `/thumb/...` (480 px) for grids, `/preview/...` (1600 px) for the detail view stage. The original (`/full/...`) only loads when you click *Load original*.
- **EXIF:** camera, lens, exposure, ISO, focal length, … on the detail page. GPS coordinates are stripped by default (privacy).
- **Tags via sidecar files:** drop a `.tags` file next to an image (e.g. `IMG_0001.jpg.tags` containing `holiday, beach, sunset`). Click a tag in the album view to filter.
- **Search:** top bar searches album, file, and tag names.
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

- `GET /` — album overview
- `GET /album/{album}` — images in an album (optional `?tag=foo`)
- `GET /image/{album}/{file}` — detail view (stage shows preview by default)
- `GET /thumb/{album}/{file}` — grid thumbnail (lazy generated)
- `GET /preview/{album}/{file}` — stage preview (lazy generated)
- `GET /full/{album}/{file}` — original file
- `GET /search?q=…` — search

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
