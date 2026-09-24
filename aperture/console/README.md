# Console

The web GUI for the gallery's config files — `photos/.gallery/gallery.cfg` and
every `<album>/.album/album.cfg`, plus the per-language `album_*.md`
descriptions and the icon/font assets that sit beside them.

It was the standalone *Configurator* until Aperture 1.0. It now ships in the
same package and the same image as the gallery, but it is still a **separate
ASGI app on a separate listener** — and that separation is the point. The
public port serves pages and cannot reach a route in here; this port is where
the one write path into the photo tree lives.

Since 1.0 it also drives the indexer: the live state, a scan button, pause /
resume and `doctor` — the same reports `python -m aperture.cli` prints, from
the same functions in `aperture/ops.py`. Actions are written to the flag-file
control channel exactly as the CLI writes them, so there is one place a scan
can begin whichever front end asked.

Two invariants hold whatever else changes:

- it writes only inside `<album>/.album/` and `photos/.gallery/` — a photograph
  is not addressable for writing by any route here;
- it never writes the index. Operational requests go through the flag-file
  control channel in `aperture/control.py`, the same one the CLI uses, so the
  indexer stays the single writer on the database. `READ_ONLY=1` disables the
  operations actions too: read-only means the console cannot change anything,
  not merely that it cannot change photos.

Since the gallery re-reads its cfg files per request, a save here shows up on
its next page load — no restart, no rescan.

```bash
cp .env.example .env      # in the repo root; every value has a working default
docker compose up -d --build
```

Then open <http://127.0.0.1:8090>. `CONSOLE_HOST` decides which host address
that port appears on; see [Security / hosting](../../README.md#security--hosting).

**Signing in.** The console asks for one operator password. Set it with
`python -m aperture.cli passwd`. Without one it starts only on a loopback bind
(with a red bar saying so) or with `CONSOLE_ALLOW_OPEN=1`; anywhere else it
refuses to open the socket. Details in
[Security / hosting](../../README.md#security--hosting).

## What it edits

| File | Where | What the UI gives you |
| --- | --- | --- |
| `gallery.cfg` | `photos/.gallery/` | welcome hero pickers (desktop / mobile / shared), a drag-sortable `album_order` with group headers, `album_sort`, the branding block, and the site's own **Look** and **Backdrop** — accent, display face and page backdrop |
| `album.cfg` | `<album>/.album/` | every documented key as a real control — cover picker, drag-sortable `featured` / `order`, reel/sort/effect dropdowns, tags, custom attributes, icon & font pickers |
| `album_en/de/jp.md` | `<album>/.album/` | a markdown editor per language; saving an empty one deletes the file |
| `links.cfg` | `photos/.gallery/` | the **Links** screen: pretty links — `/s/<name>` on the public site for one album or one photo. A name, a target (album paths complete as you type, photos come from the picker), copy and open. An album's header and a photo's metadata panel carry a **Link…** button that opens the form pointing at them |
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

Every file also has a **Raw file** mode, one button in its header, if you would rather just type.

### Getting around

The console has **seven places**, and they are the **rail** down the left:
**Overview**, **Library**, **Albums**, **Tags**, **Site** (`gallery.cfg`),
**Links** and **System**. A place is a job, not a file. There is no album tree
in a sidebar any more: albums are a place of their own, a table, and in the
Library an album is simply where you are browsing.

Every place is an **address** — `/library/japan_2026/kansai`,
`/albums/berlin_dec_2025`, `/system/doctor` — so Back, a reload and a
bookmark land where they were. The server lists each prefix explicitly and
serves the one page for all of them; anything else is still a 404.

The bar above holds the **command palette** (`Ctrl K`, or `/`): one field that
reaches every place, every album (`Shift Enter` opens its photos instead),
every setting of either file, every tag, and the actions — scan, pause, check,
reload, sign out. `?` lists the keys; `g` then a letter jumps to a place
(`g l` Library, `g a` Albums, `g y` System …). The bar also carries the
indexer's lamp, and an amber mark while the page you are on has unsaved
edits.

**Home** is what it opens on, and it is a reading rather than a form:

| | |
| --- | --- |
| is the machine working | one lamp, the last scan and what it did, with Scan and Pause right there |
| what is in it | six figures out of the index |
| is anything broken | what the config check found, each line a click from the key that caused it |
| what is still unwritten | albums that have photos but no cfg, or no text |
| what happened here | the audit log — every write this console has made |

None of it is computed on that screen. The lamp is the same `/api/ops/status`
Operations reads, the issues are the same check the tree's red dots come from,
and the log is the one every save has written since the door went in. A
dashboard with figures of its own would be a second opinion to keep in step.

**Albums** is every folder as a row: cover, name, photos (in the folder / with
sub-albums), whether it has an `album.cfg` or issues, whether it has a text.
Filter it, show only the unwritten ones or only those with issues, sort it by
tree, name or size. A row opens the album's editor; its **Photos** button
opens it in the Library.

An album's editor is its settings, description, files and raw file. Its
header says what the album is — its cover, the name it calls itself, and
whether anyone has written about it yet — and carries **Photos** into the
Library.

**System** is the machine and the reports, with its sections in a column
beside them rather than eleven tabs across the top: *Machine* (Indexer,
Derivatives, Doctor), *Reports* (Featured, GPS, Front page, Translations,
Lookup) and *Data & access* (Activity — every write this console has made,
filterable — Export, Password, About — the release notes).

The pane's own header — file path, title, status marks and its buttons —
**sticks** to the top while you scroll, and sheds the path once you are past
the first line.

Under 900px the rail keeps its glyphs and drops its words; on a phone it turns
into a strip under the bar, the current place marked on its bottom edge.

### An album, or the site, as one page

An album's editor — and the Site's, for `gallery.cfg` — is **one page of
sections** with a table of contents beside it: the settings groups (*The
album*, *Photos it leans on*, *Look*, *Backdrop*, *Text & stats*), then the
album's **Text** in each language, then its **Files**. It used to be four tabs
that hid three quarters of an album behind clicks. **Raw file** is a mode of
the page, one button in its header.

A setting is a **row**: its name in words ("Wallpaper mobile"), the key
beside it as the file spells it (`wallpaper_mobile`), a line of prose, and
the control on the right. List-shaped keys — `featured`, `order`, the welcome
reels, `album_order`, `stat` — take the whole row, their control under the
prose. The rows share their hairlines instead of each sitting in a box; a
key the file does not set reads a step quieter.

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

A key you have changed but not yet saved is marked `edited` with an amber
edge, and the save bar lists one chip per staged key: the name jumps to that
field — through a filter, through a fold — and the ✕ undoes that one change.

**Nothing is written blind.** **Review & save** (or `Ctrl S`) shows the save
as a **line diff** before it happens — the dry run comes from
`/api/album/cfg/preview` (and `/api/gallery/cfg/preview`), which runs the same
parser and the same apply() the save does, so the diff is exactly what lands.
Underneath it says what the check would still find afterwards.

**Edits belong to their file, not to the screen.** Moving to another album
keeps them: the bar's amber **unsaved** mark counts every file with something
waiting and opens the tray, where each can be opened, reviewed and saved, or
discarded. Leaving the page altogether still asks first.

### History

Every overwrite already left the previous version in `data/console/backups/`
(20 per file by default). **History** — a button in an album's and the
Site's header, and beside each description's Save — lists them: pick one and
the diff says what putting it back would change, then **Restore this
version**. A restore is an ordinary write through the same gate, backed up
and audited, so the version it replaced is in the history too and a restore
can be undone the same way. `GET /api/history`, `GET /api/history/version`,
`POST /api/history/restore`; a version id is checked against the backup
folder's own naming before anything is read.

### Many albums at once

Tick albums in the **Albums** table and **Set a key…** sets one key — or
removes it — on all of them: `unlisted`, `showcase`, an accent, a sort, a
reel, tags. **Preview the diffs** shows every file's change before anything
is written; then each album.cfg is saved by the same route a single album
uses, comments kept, backed up and audited.

### The Library

The **Library** is every photo in the archive in one grid, in three columns:
the **filters** on the left, the **grid**, and the **inspector** on the right,
each scrolling on its own so the tag field stays where your hand is while the
grid runs on under it.

**What it lists.** `/api/library` walks the tree once for the files and their
`.tags` sidecars, and joins the capture facts (date, camera, lens, exposure,
featured) out of the index read-only. Tags come off disk rather than out of
the index, so a tag written a second ago is there on the next read; a photo no
scan has seen yet is listed and marked *not indexed yet*.

**Narrowing it down.**

| | |
| --- | --- |
| Albums | the folder you are in and the folders under it; the address follows (`/library/japan_2026/kansai`) |
| Tags | any of the ticked ones, plus **Untagged** |
| Year, Camera | what a scan read from the EXIF |
| Status | Featured, Not indexed yet |
| Search | the gallery's own grammar (`camera:`, `lens:`, `iso:`, `f:`, `mm:`, `date:` and words), answered by `/api/library/search` through `aperture/search.py` — plus the console's `tag:`, `album:` and `is:featured / is:untagged / is:new`, which it applies itself |

Every facet counts what you would get by ticking it, and every active filter
is a chip above the grid that one click removes.

**Selecting.** Click picks one. Ctrl-click or the corner tick adds or takes
one away, Shift-click takes a run, and dragging across the gaps draws a
**lasso** (Shift or Ctrl adds to what was picked). `Ctrl A` takes everything
that matches — not only the tiles drawn so far — and `Ctrl I` inverts. The
arrow keys move, Shift-arrows extend, `Esc` lets go. A filter that hides a
picked photo also unpicks it, so nothing is ever tagged behind your back.

**Tagging.** The inspector lists the tags in the selection, each with how
many of the picked photos carry it: a full chip is on all of them, a dashed
one on some — click it to put it on the rest. `T` jumps to the field, which
autocompletes against every tag in use and takes several at once with commas;
the tags you used this session sit under it as one-click chips.

**Undo.** Every tag change can be taken back with `Ctrl Z` (and put back with
`Ctrl Shift Z`): each photo gets its own earlier list back, photos that shared
one in a single request.

**Looking closer.** `Space`, `Enter` or a double click opens the **loupe** on
the gallery's large preview — never the original. `←`/`→` step through what
matches, `X` picks or drops the photo you are looking at.

**Acting on the selection.** Feature or unfeature (the `featured` list of each
photo's own album), make a photo its album's cover, give it a pretty link, copy
the paths. Each is an ordinary `album.cfg` save, with the same backup and audit
line as any other.

Tags are written to a `<photo>.tags` sidecar, which is exactly what the
gallery's scanner already reads; because it folds the sidecar's mtime into the
photo's, the next scan picks the change up on its own. The console never
rewrites a photo file — this library is almost entirely PNG and BMP, where
there is no dependable metadata container to write into.

**Views.** Filters you come back to — "untagged, this year", "featured
without a tag" — can be named at the top of the filter column and applied
with one click later. They are kept in this browser: a way of working, not a
property of the gallery.

### Tags

The **Tags** place is the vocabulary: every tag with how many photos carry it
and where, a click from the Library narrowed to it. **Rename** rewrites the
tag in every sidecar; renaming onto a tag that exists **merges** the two (a
photo with both keeps one). **Delete** takes it off every photo. Both go
through `/api/tags/rename` and `/api/tags/delete`, which write each sidecar
the way a single tag write does — through `paths.sidecar_target`, with a
backup and an audit line each.

Above the list, **Probably the same tag** pairs tags that are one typo apart
or differ only in case, spaces, hyphens or underscores (`tokio` / `tokyo`,
`Night` / `night`), with a Merge button. Below it, **Sidecars and the index**
lists where the two disagree until the next scan.

### Pickers and ordered lists

The photo pickers (a cover, the featured list, the welcome reels) are a folder
browser, not a flat wall: sub-folders come up as tiles with a cover and a
count. Typing in the picker's filter searches the whole subtree below where
you stand.

Every ordered list — `featured`, `order`, `album_order`, the welcome reels —
is drag-to-reorder, since file order *is* display order for all of them. Each
row also has ↑/↓ buttons, so reordering never requires a mouse.

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

The fonts (Space Grotesk, JetBrains Mono, Ethnocentric) are the gallery's own
files, mounted at `/static/fonts` out of `aperture/gallery/static/fonts/` —
one package, one copy of each face, and nothing to re-copy when one changes.
`lucya_logo.svg` is this app's own: it is a different drawing, not a stale
duplicate of the gallery's.

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

  The source lives in [`designs/nova/`](../../designs/nova/); what the app
  serves is a copy under `static/bg/`, because `designs/` is where artwork is
  drawn and not a folder any listener serves from.

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
untouched. There is one parser for both surfaces — `aperture/cfgio.py`, which
the gallery reads with too — so what the console shows and what the gallery
renders cannot disagree about a file. That includes the `#label` group markers
inside `album_order`, which survive a drag-reorder.

Before overwriting anything it also drops a timestamped copy into
`DATA_PATH/console/backups/` (20 versions per file by default), so a bad save is one
`cp` away from undone.

## Validation

**Check all** (in the palette, and on the Overview) walks every album and
reports what the gallery would silently ignore: unknown keys, a `cover` or
`featured` entry matching no photo, a `sort = curated` with no `order` list
behind it, an `effect` that isn't whitelisted, a `font`/`icon` naming a file
that isn't in that album's `.album/`, an `album_order` entry with no matching
folder, a `stat` line the gallery would drop. The result lands on the
**Overview**, under *Needs attention*, where each line is a click from the key
that caused it, and the rail's Overview mark turns red.

Most issues come with their **fix**: remove the unknown line, take the
missing photo out of `featured`, use the lightened accent the gallery would
show anyway. `aperture/checks.py` attaches it as `{label, key, value}`; the
console stages it as an ordinary edit — on the file's page or straight from
the Overview — so it goes through the same diff and the same save as
anything you typed. `doctor` ignores it.

Checks resolve photos the way the gallery does, against its index, so what they
call missing is what a visitor would not see; a photo added a second ago counts
once the indexer has picked it up. `doctor` runs the same checks —
`aperture/checks.py`.

## Configuration

All optional — see the repo root's `.env.example`. Paths are no longer set
here: `PHOTOS_DIR`, `THUMBS_DIR` and `DATA_DIR` are the app's, read once in
`aperture/runtime.py`, and the console gets the same values the gallery does.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONSOLE_ENABLED` | `1` | `0` leaves this listener closed entirely |
| `CONSOLE_PORT` | `8090` | port inside the container |
| `CONSOLE_HOST` | `127.0.0.1` | which host address that port is published on. The boundary — see the root README |
| `BACKUPS` | `20` | versions kept per edited file, under `data/console/backups` |
| `MAX_UPLOAD_MB` | `8` | cap on icon/font/wallpaper uploads |
| `READ_ONLY` | `0` | `1` = browse and validate only; every write endpoint returns 403 and the UI disables its controls |
| `CONSOLE_ALLOW_OPEN` | `0` | `1` = run without a password on a non-loopback bind. Say this only when the network boundary is somewhere else |

### Previews come from the gallery's thumbnails

Photo grids never load originals. `/api/thumb` hands back the gallery's own
grid thumbnail — the file `/thumb` serves visitors, out of `THUMBS_DIR` — and
builds it first when the photo has none yet or a stale one. There is one
derivative tree and the console builds into it exactly as the gallery does, so
`THUMBS_DIR` has to be writable for this listener too.

### Pointing at the same share as the gallery

The gallery's own compose takes `PHOTOS_PATH`; give this one the same value.
For an SMB/NFS mount that is usually an absolute host path:

```bash
PHOTOS_PATH=/mnt/photos
```

The console needs that mount **read-write** — editing the cfg files is the
whole job. It only ever writes inside `.album/` folders and `.gallery/`; it
never touches a photo.

## Running it without Docker

```bash
pip install -r requirements-dev.txt
python -m aperture                                        # both surfaces
APERTURE_ROLE=console python -m aperture                  # this one alone
uvicorn aperture.console.app:app --reload --port 8090     # with a reloader
```

Run from the repo root. Paths come from `aperture/runtime.py`, which reads the
same environment the gallery does — one `PHOTOS_DIR`, one meaning.

## Layout

```
aperture/console/
  app.py        FastAPI routes: tree, cfg read/write, photos, thumbs, tags, assets
  opsapi.py     the operations panel: status, scan, pause/resume, doctor
  security.py   the door: password, sessions, CSRF, throttle, audit log
  library.py    the photo tree and the .tags sidecars, off the filesystem
  imagemeta.py  read-only EXIF for the metadata panel
  static/       style.css, shell.js, app.js, bg/, logo/ (the fonts are the gallery's)
                shell.js is the frame: the places, the addresses, the palette,
                the keys and the Albums table; library.js the Library and the
                Tags place; app.js draws what is in the other places
  templates/    index.html
```

State lives in `data/console/` — the password hash, the audit log and rolling
backups of every file the console overwrites. Never in the photo tree: the
gallery serves files out of `photos/.gallery/`.

The format and the vocabulary live one level up, shared with the gallery:

- `aperture/cfgio.py` — the grammar, and the comment-preserving writer
- `aperture/schema.py` — which keys exist, their allowed values, write style
  and help text
- `aperture/checks.py` — what is wrong with a cfg: the checks behind "Check
  all" and every form's issue list, the same ones `doctor` runs

`aperture/schema.py` is the one file to touch when the gallery grows a config
key: its name in `ALBUM_KEYS`/`GALLERY_KEYS`, its write style in `KEY_SPEC`, its
help line in `HELP`. The form builds itself from there, the gallery accepts it,
and `doctor` stops calling it unknown — all from that one edit.
`tests/test_schema.py` fails if a key is missing any of the three, or if a key
list or a cfg-reading loop turns up anywhere else.
