# Gallery Configurator 3.0

A standalone web GUI for the gallery's config files — `photos/.gallery/gallery.cfg` and
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
| `gallery.cfg` | `photos/.gallery/` | welcome hero pickers (desktop / mobile / shared), a drag-sortable `album_order` with group headers, `album_sort`, the branding block, and the site's own **Look** and **Backdrop** — accent, display face and page backdrop |
| `album.cfg` | `<album>/.album/` | every documented key as a real control — cover picker, drag-sortable `featured` / `order`, reel/sort/effect dropdowns, tags, custom attributes, icon & font pickers |
| `album_en/de/jp.md` | `<album>/.album/` | a markdown editor per language; saving an empty one deletes the file |
| `icon.svg`, `*.otf` … | `<album>/.album/` | upload, preview (the font is loaded and shown set in the album's name), delete |
| `logo.svg`, `*.otf`, `bg.mp4` … | `photos/.gallery/` | the same, for the gallery's own marks, badges, display face and backdrop |

The theme keys — `accent`, `font`, `font_scale`, `wallpaper`,
`wallpaper_mobile` and the two backdrop knobs — appear on **both** tabs under
the same two group headings, because both files spell them identically: the
gallery tab dresses the whole site, an album's tab overrides it for that
album's pages. What changes between the tabs is only which folder the file
pickers read (`.gallery/` vs that album's `.album/`) and the help text under
each field.
| `<photo>.tags` | next to each photo | per-photo tags, one photo or forty at a time |

Every file also has a **Raw file** tab if you would rather just type.

### Getting around

The album tree is a column on a desktop and a **drawer** on anything under
900px — it slides in over the editor from the handle in the header and closes
the moment you pick an album. As a 38vh band above the pane it cost a third of
a phone screen on every page and still only listed four albums.

The pane's own header — file path, title, status marks and the tab strip —
**sticks** to the top while you scroll, and sheds the path and a few points of
title size once you are past the first line. A settings page runs three
screens deep; without this, scrolling into the middle of one left you with no
album name and no tabs.

The sidebar lists every album by its **cover**: whatever `album.cfg` pins, else
the first photo in the folder, else the first photo of its first sub-album — so
a folder that only holds sub-albums still shows a picture. The frame around it
carries the status the old dot did: violet means the album has an `album.cfg`,
red means that cfg has issues.

Settings are laid out as tiles rather than one full-width row per key. Simple
controls (a toggle, a dropdown, a filename) sit two or three across; only the
list-shaped keys — `featured`, `order`, the welcome reels, `album_order`,
`stat` — take the full width they actually need. A key written in the file
gets a violet edge and a filled tile; one left at its default recedes to an
outline, so what an album actually overrides is visible without reading.

Above the groups sits one row of view controls, because grouping alone stopped
being enough at `gallery.cfg`'s thirty-odd keys:

| Control | What it does |
| --- | --- |
| **Find a setting** | filters by key name **and** by help text — "colour" finds `accent` and `wallpaper_tint`, "phone" finds `wallpaper_mobile`. Nobody remembers a thirty-key vocabulary by name. |
| **Only set** | hides everything this file leaves at the gallery's default |
| **Help** | drops the description under each key and the blurb under each group, leaving the controls |
| **Fold all** | folds every group to its heading. Folded, the whole file is seven lines with an `n / m` count each — the fastest read of what an album actually overrides. |

Groups fold individually by clicking their heading, and each says how many of
its keys the file writes. A filter overrules a fold, so a hit can never hide
inside a shut group. The three switches persist across reloads; they are a
habit, not a property of the album.

A key you have changed but not yet saved is marked `edited` and switches its
edge to amber, and the save bar lists one chip per staged key: the name jumps
to that field — through a filter, through a fold — and the ✕ undoes that one
change rather than all of them.

### Photos & tags

The **Photos & tags** tab is a folder browser, not a flat wall: sub-folders
come up as tiles with a cover and a count, and only the photos actually in the
folder you are looking at are listed. A 391-photo trip is browsed the way it is
stored. The same browser backs every photo picker, so choosing a cover out of
`japan_2026/kansai/osaka` is a matter of clicking down to it. Typing in the
picker's filter switches to searching the whole subtree below where you stand.

Every ordered list — `featured`, `order`, `album_order`, the welcome reels —
is drag-to-reorder, since file order *is* display order for all of them. Each
row also has ↑/↓ buttons, so reordering never requires a mouse.

Click a photo to select it and open its metadata. To build a selection there
is a tick in each tile's corner — no modifier needed — plus ctrl-click to
toggle, shift-click to take a run, and **Select all here** for the whole
folder.

The selection panel and the metadata panel sit in a **column beside the grid**,
never on top of it. That is deliberate: as a sticky bar across the bottom, the
tagging controls covered most of the photos they existed to tag, which made
picking a second photo impossible without scrolling them out of the way. On a
phone there is no second column, so they move **above** the grid rather than
below it — stacked underneath, tagging forty photos meant scrolling past all
forty to reach the panel that tags them.

Whatever is selected can be tagged in one go. The panel lists the tags already
in the selection — with a `4/6` count when only some of them carry it — so
removing one is a click rather than a guess. The add field autocompletes
against every tag used anywhere in the gallery, so the same idea does not end
up spelled three ways.

Tags are written to a `<photo>.tags` sidecar, which is exactly what the
gallery's scanner already reads; because it folds the sidecar's mtime into the
photo's, the next scan picks the change up on its own.

Clicking a photo also opens its metadata panel: dimensions, file size, camera,
lens, exposure, aperture, ISO, focal length, capture date. That panel is
**read-only** — the configurator never rewrites a photo file. This library is
almost entirely PNG and BMP, where there is no dependable metadata container
to write into, so tags in a sidecar are the honest way to attach anything.

### Custom attributes

`loc` and `stat` are the album's editorial stats block — the bit above the
SPAN / DEVICE / FOCAL / APERTURE / DATA readouts the gallery derives from EXIF
(`paris_march_2026` uses both). `stat` is the freeform one: repeat the key for
as many `Label: Value` lines as you want.

The GUI edits those as **Label / Value pairs** rather than raw strings, which
removes the two ways to write one the gallery drops without a word — a line
with no colon, and a line with an empty value. Both are also reported by the
checks now, in this tool *and* in the gallery's own `app.cli doctor`, whose
known-key list was missing `loc`, `stat` and `stats` entirely.

`loc` is a single text field even though the parser comma-splits it: the
gallery rejoins the parts, so `Paris, France` is one line, not two.

## Design — Nebula

**Nebula** is the gallery's design language, and this tool is built in it
rather than in a lookalike of it — the four rules are written out in the
[gallery's README](../README.md#design--nebula). The sheet carries the same
token vocabulary — the ground and grey ramp, `--acc` / `--chrome` / `--label`,
`--glass` and its blur, `--title-a`/`--title-b`, and `--radius: 0`, square
everywhere — and the chrome is built out of the same objects:

| Object | Where it comes from |
| --- | --- |
| header | `.nav`: same ground, same accent hairline, and it brightens once the pane under it is scrolled, exactly as `.nav--scrolled` does |
| page heading | `.section__slug .name`: Space Grotesk 700 on the same clamp with the white-to-grey fall. It used to be the display face in caps — that is the **wordmark's** voice over there, not a heading's, and it also uppercased data (`japan_2026` is a folder name) |
| status marks | `.section__doc-mark`: a hairline in its own colour, no fill |
| tabs, toggles, the language switch | `.nav__lang`: one boxed segmented control, current option in the accent tint under an inset ring |
| group headings | `.search-group__label`: mono eyebrow at .24em in `--label`, hairline rule, bare count at the end |
| buttons | `.tag` / `.btn-link`: mono caps on glass, hover in `--chrome`. Only the button that writes the file keeps the accent |
| filter fields | `.nav__search`: glass pane, mono 12px, uppercase tracked hint — the album tree, the settings finder and the picker are the same control |
| tags | `.tag`, down to the tracking |
| footer | `.foot`: `#050505`, the page's own sans at 12px in ghost grey |

The split the gallery's palette pass established holds here too: purple marks
**meaning** — focus, the current selection, a key this file actually sets, the
primary action — and everything that is merely furniture takes `--chrome` or
`--label`. This sheet used to paint both groups purple.

The fonts (Space Grotesk, JetBrains Mono, Ethnocentric) and `lucya_logo.svg`
are **copies** under this app's own `static/`, because the two apps deploy as
separate images and never share a mount. If the gallery's brand assets change,
re-copy them from `app/static/`.

One deliberate departure:

- **No ambient video.** Where the gallery plays `bg.mp4`, this plays nothing:
  a config tool has no business decoding 1080p behind a form. It gets the
  **nova** artwork as a still instead — vector, so a full-viewport backdrop
  costs nothing to paint, which is the whole reason one is affordable here.
  A `<picture>` hands phones the square cut (`nova-square.svg`) and everything
  else the 16:9 one, so exactly one of the two is fetched; the wide cut turns
  to mush cropped into a portrait viewport.

  It sits under the same four overlay layers as the gallery's backdrop —
  grid, accent bloom, dim — but all three of the gallery's figures are
  retuned, because those defaults are set for a **photograph** and this is
  not one:

  | | gallery | here | why |
  | --- | --- | --- | --- |
  | tint | `grayscale(.92)` | **off** | The drain exists because a photograph behind the page introduces a *foreign* hue that every blurred pane then picks up. Nova has none to introduce — it is brand art drawn in the accent's own colour, so draining it removes the one thing it was made to say. The gallery spells this `wallpaper_tint = off`: a supported setting, not a departure. The photo thumbnails are still the most saturated thing on screen, which is the rule that actually matters. |
  | brightness | `.72` | `.95` | A photograph arrives bright and full-range and needs holding back. Nova arrives dark by construction — its own gradient falls to `#07070d`. |
  | dim | `.62 → .82` | flat `.62` | That ramp darkens a photo towards the footer of a scrolling page. Nova already falls off downward on its own, so the ramp landed on the artwork's own falloff and took the bottom half to black. |

  Nothing was ever cropped, incidentally: at any normal window the cover fit
  crops the **sides** and shows the full height.

  The source lives in [`designs/nova/`](../designs/nova/); what the app serves
  is a **copy** under `app/static/bg/`, for the same reason the fonts and the
  logo are copies — the two apps deploy as separate images and never share a
  mount.

The header carries the mark, the wordmark and the two actions and nothing
else — no mount path (it is the footer's `TARGET` stamp and never changes
during a session), no JP mark, and no status lamp: a green "READWRITE" light
on every normal session was a lamp reporting that nothing is wrong. A
read-only mount still says so, as a warn mark next to the buttons, because
that one changes what the tool can do.

### On a phone

Everything above 900px is the two-column desktop. Below it the album tree
becomes the drawer described under *Getting around*, the setting tiles go to
one column, and the photo browser's side panels move above the grid. Below
620px the type steps down, every control grows to a finger-sized box, the tab
strip wraps instead of scrolling sideways, text inputs go to 16px (under that
iOS zooms the page on focus and leaves it scrolled sideways with no way back),
the picker modal goes full-screen, and the save bar puts its buttons on their
own row. The body is sized in `dvh`, so a collapsing address bar cannot push
the save bar off the bottom.

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
| `THUMBS_PATH` | `../thumbnails` | the gallery's thumbnail tree, mounted read-only so grids reuse it. Optional |
| `DATA_PATH` | `./data` | thumbnail cache + backups. Disposable |
| `THUMB_SIZE` | `320` | fallback preview size, for photos the gallery has not thumbnailed |
| `BACKUPS` | `20` | versions kept per edited file |
| `MAX_UPLOAD_MB` | `8` | cap on icon/font uploads |
| `READ_ONLY` | `0` | `1` = browse and validate only; every write endpoint returns 403 and the UI disables its controls |

### Previews come from the gallery's thumbnails

Photo grids never load originals. `/api/thumb` hands back the gallery's own
thumbnail from `THUMBS_DIR` whenever that tree is mounted and the file is not
older than the photo — the response says which, in an `X-Thumb-Source` header.
Only a photo the gallery has not thumbnailed yet falls through to Pillow, and
that result is cached under `DATA_PATH` so it happens once.

Mount it read-only, pointing at the same folder as the gallery's
`THUMBS_PATH`. Without it nothing breaks; the first view of a folder is just
slower.

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
    main.py       FastAPI routes: tree, cfg read/write, photos, thumbs, tags, assets
    cfgio.py      the comment-preserving parser/writer
    schema.py     which keys exist, their allowed values and write style
    library.py    the photo tree and the .tags sidecars, off the filesystem
    imagemeta.py  read-only EXIF for the metadata panel
    validate.py   the checks behind "Check all"
    static/       style.css, app.js, fonts/, logo/
    templates/    index.html
  Dockerfile
  docker-compose.yml
```

`schema.py` is the one file to touch when the gallery grows a config key: add
it to `KEY_SPEC`, to `ALBUM_KEYS`/`GALLERY_KEYS`, and to `HELP`. The form
builds itself from there.
