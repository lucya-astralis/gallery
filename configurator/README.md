# Gallery Configurator

A standalone web GUI for the gallery's config files — `photos/gallery.cfg` and
every `<album>/.album/album.cfg`, plus the per-language `album_*.md`
descriptions and the icon/font assets that sit beside them.

It is a separate app with its own compose file, its own image and its own port.
It does **not** import the gallery, read its database, or need it to be
running: the only thing the two share is the photo folder. Since the gallery
re-reads its cfg files per request, a save here shows up on its next page load
— no restart, no rescan.

```bash
cd configurator
cp .env.example .env      # optional; every value has a working default
docker compose up -d --build
```

Then open <http://localhost:8090>.

## What it edits

| File | Where | What the UI gives you |
| --- | --- | --- |
| `gallery.cfg` | photos root | welcome hero pickers (desktop / mobile / shared), a drag-sortable `album_order` with group headers, `album_sort` |
| `album.cfg` | `<album>/.album/` | every documented key as a real control — cover picker, drag-sortable `featured` / `order`, reel/sort/effect dropdowns, tags, custom attributes, icon & font pickers |
| `album_en/de/jp.md` | `<album>/.album/` | a markdown editor per language; saving an empty one deletes the file |
| `icon.svg`, `*.otf` … | `<album>/.album/` | upload, preview (the font is loaded and shown set in the album's name), delete |

Every file also has a **Raw file** tab if you would rather just type.

### Custom attributes

`loc` and `stat` are the album's editorial stats block — the bit above the
SPAN / DEVICE / FOCAL / APERTURE / DATA readouts the gallery derives from EXIF
(`paris_march_2026` uses both). `stat` is the freeform one: repeat the key for
as many `Label: Value` lines as you want.

The GUI edits those as **Label / Value pairs** rather than raw strings, which
removes the two ways to write one the gallery drops without a word — a line
with no colon, and a line with an empty value. Both are also reported by the
checks now, in this tool *and* in the gallery's own `debug.py cfg`, whose
known-key list was missing `loc`, `stat` and `stats` entirely.

`loc` is a single text field even though the parser comma-splits it: the
gallery rejoins the parts, so `Paris, France` is one line, not two.

## Design

Same skin as the gallery — palette, type ramp, 2px corners, grid backdrop,
accent hairline, HUD labels, logo and footer signature. The fonts
(Space Grotesk, JetBrains Mono, Ethnocentric) and `lucya_logo.svg` are
**copies** under this app's own `static/`, because the two apps deploy as
separate images and never share a mount. If the gallery's brand assets change,
re-copy them from `app/static/`.

Two deliberate departures:

- **No ambient video.** The gallery's `bg.mp4` backdrop is replaced by the
  grid + radial wash alone. A config tool has no business decoding 1080p
  behind a form.
- **The JP face is fenced off.** The gallery's `NotoSansJP-subset.woff2` is a
  469-glyph subset built for the gallery's own strings, so it is registered
  here under a private family name and used *only* on the 画像庫 brand mark.
  The description editor — where arbitrary Japanese actually gets typed — uses
  the reader's system JP font, which has the full range. Pointing the subset at
  an `album_jp.md` would render tofu.

## The part that matters: comments survive

The shipped cfg files are mostly documentation — the comments explain each key
in place. A GUI that parsed a file into a dict and wrote the dict back would
delete all of it.

So this tool never rewrites a whole file. It keeps the file as lines, and a
save touches only the lines belonging to the key you changed:

- changing a value rewrites that key's line(s) where they already sit,
- removing a key deletes its lines (and collapses the blank line the removal
  would otherwise leave behind, so repeated edits don't space the file out),
- a key that wasn't in the file yet is appended at the end.

Comments, blank lines, ordering and every key you didn't touch come through
untouched. The parser is a line-for-line mirror of the gallery's own
`_parse_cfg`, verified to produce identical output on all 24 shipped config
files — including the `#label` group markers inside `album_order`, which
survive a drag-reorder.

Before overwriting anything it also drops a timestamped copy into
`DATA_PATH/backups/` (20 versions per file by default), so a bad save is one
`cp` away from undone.

## Validation

The **Check all** button walks every album and reports what the gallery would
silently ignore: unknown keys, a `cover` or `featured` entry matching no photo,
a `sort = curated` with no `order` list behind it, an `effect` that isn't
whitelisted, a `font`/`icon` naming a file that isn't in that album's
`.album/`, an `album_order` entry with no matching folder, a `stat` line the gallery would
drop. Errors also show as a red dot next to the album in the tree; clicking an
issue jumps to it.

Checks run against the filesystem, not the gallery's index, so a photo added a
second ago already counts.

## Configuration

All optional — see `.env.example`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONFIGURATOR_PORT` | `8090` | host port |
| `PHOTOS_PATH` | `../photos` | the photo folder to manage; point it at the same folder or share the gallery serves |
| `DATA_PATH` | `./data` | thumbnail cache + backups. Disposable |
| `THUMB_SIZE` | `320` | preview size in the photo pickers |
| `BACKUPS` | `20` | versions kept per edited file |
| `MAX_UPLOAD_MB` | `8` | cap on icon/font uploads |
| `READ_ONLY` | `0` | `1` = browse and validate only; every write endpoint returns 403 and the UI disables its controls |

### Pointing at the same share as the gallery

The gallery's own compose takes `PHOTOS_PATH`; give this one the same value.
For an SMB/NFS mount that is usually an absolute host path:

```bash
PHOTOS_PATH=/mnt/photos
```

The configurator needs that mount **read-write** — editing the cfg files is the
whole job. It only ever writes inside `.album/` folders and the root
`gallery.cfg`; it never touches a photo.

## Running it without Docker

```bash
pip install -r requirements.txt
PHOTOS_DIR=/path/to/photos python -m uvicorn configurator.app.main:app --port 8090
```

Run from the repo root. With no `PHOTOS_DIR` set it falls back to the `photos/`
folder next to the checkout, which is what the local gallery uses.

## Layout

```
configurator/
  app/
    main.py       FastAPI routes: tree, cfg read/write, photos, thumbs, assets
    cfgio.py      the comment-preserving parser/writer
    schema.py     which keys exist, their allowed values and write style
    library.py    the photo tree, straight off the filesystem
    validate.py   the checks behind "Check all"
    static/       style.css, app.js, fonts/, logo/
    templates/    index.html
  Dockerfile
  docker-compose.yml
```

`schema.py` is the one file to touch when the gallery grows a config key: add
it to `KEY_SPEC`, to `ALBUM_KEYS`/`GALLERY_KEYS`, and to `HELP`. The form
builds itself from there.
