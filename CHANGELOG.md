# Changelog

Every release aperture has had, newest first. The number lives in exactly one
place in the code — `VERSION` in `aperture/brand.py` — and this file is the
only place that says what it means; the rules are in
[README → Versions](README.md#versions).

`MAJOR.MINOR.PATCH`, read as a promise to whoever runs it:

* **MAJOR** — the program is a different shape than it was. Read the entry
  before upgrading.
* **MINOR** — something new to see or to set. Drop-in unless the entry has a
  **Before you upgrade** line, which is where a required step is named.
* **PATCH** — fixes, refactors, docs, tests. Nothing new to learn.

An entry is written in the same commit that earns it, so the version and the
notes are never one commit apart.

---

## 2.0.0 — 2026-09-24

The console, rebuilt around what you came to do: a different shape, the same
files. It is now a set of places with addresses, a Library over every photo
with a real selection, and a tag manager.

**Before you upgrade:** nothing to do. No cfg key, file name, sidecar format
or database column changed, and the gallery itself is untouched — pull,
rebuild, restart. What changes is where things are in the console: albums
are the **Albums** place (no sidecar tree any more), photos and tagging are
the **Library**, and Operations is **System**. Old bookmarks to the console's
root still land on the Overview.

**New**

* **Seven places on a rail**: Overview, Library, Albums, Tags, Site, Links
  and System. They replace the header's five places, the album tree in the
  sidebar and Operations' eleven tabs.
* **Every place has an address**, so Back, a reload and a bookmark land
  where they were: `/library/japan_2026/kansai`, `/albums/berlin_dec_2025`,
  `/system/doctor`.
* **A command palette** (`Ctrl K` or `/`) reaches every place, album,
  setting, tag and action by typing. `Shift Enter` on an album opens its
  photos.
* **Keyboard**: `?` lists the keys, `g` and a letter jumps to a place.
* **Albums is a table**: cover, photos, config and text state per album,
  with a filter, "unwritten" and "with issues" views and three sort orders.
* **The Library** is every photo in one grid, with filters for album, tag
  (and "untagged"), year, camera and status, and the gallery's own search
  grammar (`camera:`, `lens:`, `date:` …) plus `tag:`, `album:` and `is:`.
  Every facet counts what ticking it would give.
* **Selection like a photo tool**: click, Ctrl-click, Shift-click, a lasso
  across the gaps, `Ctrl A` for everything that matches, `Ctrl I` to invert,
  arrow keys with Shift to extend.
* **Tags in one go**: the inspector shows each tag in the selection as
  "on all" or "on 2 of 6" — click the second to put it on the rest. `T`
  jumps to the field; commas add several; recently used tags are one click.
* **Undo for tags**: `Ctrl Z` gives every photo its own earlier tags back,
  `Ctrl Shift Z` redoes.
* **The loupe**: `Space` shows the photo large (the gallery's preview, never
  the original), `←`/`→` step through, `X` picks it.
* **Acting on a selection**: feature or unfeature, make a cover, give a photo
  a pretty link, copy the paths.
* **The Tags place manages the vocabulary**: rename a tag everywhere, merge
  two by renaming one onto the other, delete one from every photo — each
  sidecar backed up and audited. It also points out tags that are probably
  the same one (`tokio` / `tokyo`) with a Merge button.
* **An album is one page**: its settings groups, its text in each language
  and its files, down one page with a table of contents beside it, instead
  of four tabs. The Site's `gallery.cfg` the same. Raw file is a button in
  the header.
* **A setting is a row**: its name in words, the key as the file spells it,
  a line of prose, the control on the right.
* **Review before save**: Save (or `Ctrl S`) shows the exact lines that will
  change as a diff — rendered by the same parser and the same code path the
  save uses — and what the check would still say afterwards.
* **Unsaved edits wait per file**: moving to another album no longer asks
  you to throw them away. The bar's amber mark counts every file with
  something waiting and opens a tray to review, save or discard each.
* **Fixes for what the check finds**: most issues come with a one-click fix
  (remove the unknown line, drop the missing photo from `featured`, use the
  lightened accent), on the file's page and on the Overview. A fix is staged
  like any edit, so it goes through the same review.
* **History for every file the console writes**: the backups each save
  already kept are listed per file, with a diff of what restoring one would
  change and a Restore button. A restore is itself backed up, so it can be
  undone the same way.
* **Many albums at once**: tick albums in the Albums table and set (or
  remove) one key on all of them, after previewing every file's diff.
* **Saved views** in the Library: name the current filters and apply them
  again with one click (kept in this browser).
* **Activity** in System: every write this console has made, filterable,
  each a click from the file it touched.
* The bar shows the **indexer's lamp** on every screen.

**Changed**

* An album's editor no longer has a Photos tab; its **Photos** button opens
  the album in the Library.
* **System** lists its sections in a column (Machine, Reports, Data &
  access) instead of a tab strip that wrapped onto two rows. The release
  notes are its **About** section.
* Tags moved out of Operations into a place of their own.

---

## 1.11.3 — 2026-09-23

Both surfaces caught up with the rules Nebula wrote down since 1.11.2.

**Fixed**

* **`hidden` always hides.** One base rule in each sheet now beats every
  component's own `display`, instead of a `[hidden]` line per component —
  anything a script hides is gone from the screen and the tab order, not
  only the handful that had remembered their line.
* **The console's toast reports; it no longer wears the accent.** Its edge
  is grey, a warning finally shows amber instead of falling back to the
  accent, and every toast carries a glyph as well as its colour.
* **The touch target and the door's width are tokens** (`--tap`,
  `--door-w`), and the sign-in button is 44px tall on a desktop too.

**Changed**

* **The console reads less like a terminal.** It now speaks Nebula's two
  voices the way the gallery does: card, group and dialog headings, the
  names in a list of facts, empty states, hints, the save bar's line and
  the toast are sans and sentence case. Mono stays on what is chrome or an
  identifier.
* **Settings are one sheet, not a pile of boxes.** The keys share their
  hairlines, a set key no longer wears an accent edge (being written in the
  file is not a state), and a key is shown as the file writes it:
  `wallpaper_mobile`, never `WALLPAPER_MOBILE`. The same goes for the key
  in a config warning.
* **Reading copy is `--text-dim` at the faintest**, as Nebula's first rule
  asks. Help text, notes and empty states were a step fainter.
* **The toast is Nebula's**: the menu surface, a sentence, a coloured glyph,
  and it sits clear of the save bar.
* Every tracked label takes the tracking paired with its size
  (`--tr-chrome-*`), so there are no hand-typed `em` values left.

---

## 1.11.2 — 2026-09-23

The console's door, held to the Nebula rules written down for it.

**Fixed**

* **A refused sign-in says so without the red.** The error line carries a
  warning glyph in front of the words, so it still reads as a refusal in
  forced colours or to anyone who cannot tell red from grey.
* **Signing in no longer cuts the card off mid-fade.** The page used to move
  on after a fixed 300 ms while the card's fade ran for 450; it now waits for
  the fade to finish, with the fade's own duration as the fallback.

## 1.11.1 — 2026-09-23

The viewer's bar fits a phone.

**Fixed**

* **The lightbox bar on a phone.** It floated as a narrow box in the middle
  of the screen and wrapped into three rows, with Download alone on the last
  one and a dead cell beside the counter. It now spans the screen in two
  rows: the file name on top, the counter, Load original and Download
  side by side below — and once the original is in, Download takes the
  whole row. It also clears the home indicator.
* **Load original keeps its icon.** Opening a photo in the viewer replaced
  the button's whole content with its label and dropped the icon.

---

## 1.11.0 — 2026-09-23

Every gallery says which aperture it runs, and the update check asks
images.lucya.sh.

**Added**

* **`GET /api/version`.** The version this gallery runs, with the date, link
  and one-line summary of its changelog entry — public and CORS-open like the
  rest of the API, and listed in `/api`.

**Changed**

* **The update check asks a gallery, not a file.** The console now reads
  `https://images.lucya.sh/api/version`: the maker's own gallery always runs
  the newest release, so its answer is the latest one. `UPDATE_URL` can point
  at any other aperture's `/api/version` instead.

**Removed**

* **`tools/build_update_manifest.py`** and the uploaded `latest.json` it wrote
  — there is nothing to publish by hand after a release any more.

## 1.10.0 — 2026-09-23

A photo replaced under the same name is picked up as a new picture.

**Added**

* **Every photo is hashed.** The index keeps the SHA-256 of each file. When a
  photo is edited and uploaded again under its old name, the scan (or the
  watcher) sees different bytes, deletes the old thumbnail, preview and full
  JPEG, and builds them again from the new file — also when the upload kept
  the old timestamp, which used to leave the old picture in the gallery for
  good. The same file copied over again is recognised as unchanged and
  nothing is re-encoded.
* **Photo URLs carry a version stamp.** `/thumb/`, `/preview/` and `/full/`
  are asked for with `?v=` plus the start of the hash, on every page, in the
  lightbox and in the API's `urls`. Only a stamped URL is cached for a year,
  so a replaced photo gets a new address and no browser keeps the old one. A
  plain URL still answers, cached for an hour.

**Worth knowing:** nothing to do, but the first scan after the upgrade reads
every photo once to hash it — on a large share over SMB that scan takes as
long as reading the whole library. Later scans only read files whose mtime
or size changed.

## 1.9.0 — 2026-09-20

The location bar reads as a path again.

**Changed**

* **Every step of the trail carries a mark.** *Home* a house, *Albums* a stack
  of photos, a folder its folder — open on the one you are standing in — a
  photo a picture, a search its magnifier. An album with its own emblem
  (`album.cfg` `icon =`) still shows that instead, so the trail is scanned
  rather than read.
* **The trail is grey again.** It used to be accent-purple end to end, which
  said *state* on a line where nothing had any. The steps above you are dim,
  the step you are on is bright, and the accent appears exactly once on the
  row: on the mark of where you are. Tappable is carried by the hairline
  under each label, which lights up on hover.
* **The back button follows it.** Quiet grey on a desktop with the accent on
  the hover border; on a phone it stays a thumb-sized button but is filled
  with glass rather than an accent tint, and the accent comes back on the
  press.
* **On a phone both rows hold one height.** The back button and the trail
  pane share the bar's floor, the way they already matched on a desktop. Every
  step keeps its name here too: a trail of marks alone fits a deep path on one
  line and halves the bar's height, but the path is the one thing on the page
  that has to be unambiguous, so it is allowed to wrap instead.
* **Every measure in the bar comes off a scale.** The route line had picked
  up a hand-typed 14px gap, an 18px and a 20px margin, a 38px and a 40px
  height and two different icon sizes — the exact drift the design language's
  fifth rule exists to stop. It now reads two named measures (the gap, and
  the thumb target both panes share), and the marks sit in one icon box: an
  album's own emblem and a generic glyph measured 12.8px and 14.3px in the
  same slot before, which is what happens when a size has no name.
* **`--fs-ico`.** That icon step is now a token, in all three copies of the
  sheet. It was the value `.92em`, spelled out once in the gallery and once
  in the console — two apps, one number, no way to keep them together.
* **One definition for all five pages.** The bar is a macro now
  (`_pathbar.html`) instead of the same markup copied into the album, photo,
  index, search and statistics templates.

---

## 1.8.0 — 2026-09-15

The console says when there is a newer aperture.

**New**

* **Update notice.** The console asks lucya.sh, at most twice a day, whether a
  newer release exists. When one does, the *Changelog* place gets a dot and
  *Home* a card with the version, its one-line summary and a link to the
  notes. The *Changelog* place always shows the check — up to date, newer
  release, could not ask — with a *Check again* button. Nothing is downloaded
  or installed, and the request carries no version and no identifier. The
  server asks, not the browser, so the console's CSP is unchanged.
* **`UPDATE_CHECK` and `UPDATE_URL`.** `UPDATE_CHECK=0` never asks;
  `UPDATE_URL` reads the answer from somewhere else.
* **`tools/build_update_manifest.py`** writes the file that is published, from
  `VERSION` and the newest entry here.

---

## 1.7.1 — 2026-09-14

The trip timeline shows the trip, not the albums' marks.

**Fixed**

* **No album icons on the trip's leg cards.** A region album's `icon = …`
  (Kansai, Hokkaido, Kanto) was also drawn in front of its name on the trip
  timeline. It still shows on the album's card, hero title and breadcrumb.

---

## 1.7.0 — 2026-09-14

Everything the CLI can do, in the console — and what the thumbnails cost on
disk.

**New**

* **Operations has a tab for every CLI command.** *Overview*, *Doctor*,
  *Derivatives*, *Featured*, *Tags*, *GPS*, *Front page* (the welcome feed
  and the trips), *Lookup* (an album, a photo, a cfg as the app parses it,
  the search), *Translations*, *Export* and *Password*. Each serves the same
  function the CLI renders, so the two cannot disagree. Only `menu`, `help`
  and `term`, which are about the terminal itself, stay in the terminal.
* **What the generated trees cost.** A *Disk* card on the overview, and
  `python -m aperture.cli disk`, show the size and file count of the
  thumbnails, the previews and the HEIC conversions, what that is per photo
  and as a share of the originals, how full each volume involved is, and how
  much of a tier is still in a format it no longer writes (the JPEG
  thumbnails from before WebP, say) — with a way to delete those.
* **The doctor's fixes are buttons.** Rebuilding missing and stale
  derivatives, deleting orphaned files, recomputing the featured flags and
  stripping GPS from the originals are *jobs*: queued on the control channel
  like a scan, run by the indexer, followed in the console with a progress
  bar and a summary. After a doctor run, *What now* offers exactly the jobs
  and scans its findings call for.
* **The archive's statistics on the overview** — date span, largest albums,
  shots per capture month, formats, and whether the index and the disk agree
  — the dashboard `dash` draws, as bars.
* **The config export is a download**, and *resume* can ask for a scan right
  away, like `resume --scan`.
* **The console password can be set and changed in the console.** Changing
  it asks for the current one and ends every session. Removing it is only
  offered where the console may run without one (a loopback bind, or
  `CONSOLE_ALLOW_OPEN=1`) — anywhere else it would stand open until the next
  start and then refuse to start.

**Fixed**

* **`export` works again.** It looked for the metadata folder names in the
  wrong module and failed on every run.

---

## 1.6.1 — 2026-09-14

The folders a NAS keeps for itself stay out of the gallery.

**Fixed**

* **`@eaDir` is not an album.** A Synology writes its own thumbnails of every
  photo into `@eaDir/<photo>/SYNOPHOTO_THUMB_*.jpg`, and because those are
  JPEGs the scan indexed each one as a photo of an album called
  `<album>/@eaDir/<photo>` — in the album list, the search and `/stats`. Such
  folders are now ignored at any depth and in any case: Synology's `@eaDir`,
  `@tmp`, `@sharebin`, `#recycle` and `#snapshot`, QNAP's `@Recycle`,
  `@Recently-Snapshot` and `.@__thumb`, the ones macOS and Windows leave on a
  share, and `lost+found`. The scan no longer walks into them at all, which
  also spares it a round trip per NAS thumbnail on an SMB share; the watcher
  drops their events, `doctor` does not count them, the media routes do not
  serve them, the console does not list them and refuses to write into one.
  The first scan after the upgrade takes the rows that got in out of the
  index; `thumbs --prune --apply` then clears the thumbnails that were built
  for them.

---

## 1.6.0 — 2026-09-13

Search by what a photo was shot with, long lists that open up, unlisted
albums, and the release notes inside the console.

**New**

* **The search reads EXIF.** A word now matches the camera and the lens as
  well as the album, file name and tag, and filters narrow what the words
  found: `camera:x100v`, `lens:"35mm f/2"`, `iso:3200` or a range like
  `iso:800-3200` and `iso:1600-`, `f:2.8` (also typed as `f/2.8` or `ƒ2.8`),
  `mm:35` or `mm:24-70`, and `date:2026`, `date:2026-08`, `date:2026-08-15`
  or `date:2026-08-01..2026-08-20`. A bare date is a date too. Each filter
  shows as a chip under the search header, and a chip leads to the same
  search without it; a filter the search cannot read is struck through and
  ignored instead of emptying the results.
* **EXIF values link to their photos.** On a photo page the camera, lens,
  capture date, aperture, ISO and focal length each open the search for
  every photo that shares them, and so do the bars of the camera, focal
  length, aperture and ISO charts on `/stats`. The same grammar answers `q`
  on `/api/photos` and `python -m aperture.cli search`, which also lists the
  filters it ignored.
* **Long lists open up instead of stopping at "and N more".** The album,
  camera and tag charts on `/stats` show their top rows and put the rest
  behind *+N more*, which opens in place and names every one — the tail used
  to be a single row that added them up. It needs no JavaScript, and a folded
  bar is still as long as its count says against the whole chart. In the
  console, the Doctor report, *Needs attention* and *Unwritten* show their
  first rows and a button for the rest, and a list you opened stays open
  while the home screen refreshes during a scan.
* **Unlisted albums.** `unlisted = true` in an album.cfg takes the album, and
  every album under it, out of every list the gallery draws: `/albums`, the
  sub-album cards of its parent, the search, `/stats`, the welcome screen's
  counts and random feed, and the lists of the JSON API (`/api/albums`,
  `/api/photos`, `/api/showcase`, `/api/shuffle`, `/api/tags`, `/api/stats`).
  A parent shown as a whole collection stops at it, and none of its photos
  becomes a parent's cover. Its page, its photos and a `/s/` link to it keep
  working for whoever has the address, and those pages ask search engines
  not to index them. It is a way to share an album, not a lock. The console
  offers the switch with the album's other basics.
* **A Changelog place in the console**, next to Operations: every release
  this build knows about, the newest open and each older one a click away,
  and which version this build is. It says who makes the software —
  [lucya.sh](https://lucya.sh), with a picture — and links to the code on
  [GitHub](https://github.com/lucya-astralis/gallery).

**Fixed**

* `q` on `/api/photos` no longer treats `%` and `_` as wildcards.

**Before you upgrade**

* The image now carries `CHANGELOG.md`. Rebuild it rather than restarting
  the old one, or the console's Changelog place reports that the notes are
  not part of the build.
* The first start adds five columns to the index and fills them from the
  EXIF it already holds, once; on a library of a few thousand photos that
  takes seconds.

**For maintainers**

* Camera, lens, focal length, aperture and ISO are index columns now
  (`capture.facts`, `db.migrate`), and `aperture/search.py` is the one search
  grammar.
* `stats.collect()` no longer takes `more_label` and `stats.album_rows()` no
  longer takes `other_label`. Rows past a chart's limit are marked `extra`
  (`stats.fold`), and the `bars` / `album_bars` macros take the label of the
  disclosure instead.
* `albums.unlisted_clause()` is the one SQL condition for unlisted albums and
  `albums.is_unlisted()` the one question; a new listing that counts or shows
  photos across albums should use them. A saved `unlisted` shows within two
  seconds, which is how long the answer is kept.
* `brand.REPO_URL`, `brand.MAKER_NAME` and `brand.MAKER_URL` hold the links of
  the Changelog place, which renders the notes on the server at `/api/about`;
  only a `## X.Y.Z — date` heading counts as a release there.
* The test fixture's photos now really carry their capture date, aperture,
  focal length and ISO: Pillow dropped the Exif sub-IFD the fixture wrote,
  so none of those had ever reached the index in a test.

---

## 1.5.1 — 2026-09-13

A scan that finds nothing no longer empties the gallery, and the photo viewer
can be used from the keyboard alone.

**Fixed**

* **An unmounted share no longer wipes the index.** When the photo share is
  not there, a `nofail` mount leaves an empty folder, and the periodic scan
  used to read that as every photo having been deleted — the site went empty
  until the share came back and a scan re-indexed it. A whole-gallery scan
  that finds no photo at all while the index still holds some now changes
  nothing and says so: a warning in the log, `held` in the scan result, and a
  mark in `status`, `scan` and the console's scan readout. `scan --force` is
  the way to clear the index when the gallery really is empty.
* **The fullscreen viewer keeps focus.** Opening it moves focus to its close
  button and makes the page behind it inert, so Tab no longer walks out into
  links that cannot be seen; closing it hands focus back to what opened it.
* **A skip link.** The first Tab on every page offers *Skip to content*, which
  jumps past the header straight into the page.

---

## 1.5.0 — 2026-09-13

The console wears the gallery's icons, and its header folds instead of jumping.

**New**

* **Icons across the console**, from the same Font Awesome subset the gallery
  draws on: the places and actions in the header, every tab, status mark, card
  heading and button, a magnifier in each filter field, and the sign-in door.
  The ✕ ▶ ↑ ↓ ⠿ ↗ → characters that stood in for icons are icons now.

**Fixed**

* **The pane header no longer jumps on scroll.** Past 18px it used to swap to
  a layout 40px shorter, so the page under it lurched, and near that point it
  could flicker between the two. It keeps its height now: the file path slides
  up under the pane's edge as you scroll and comes back down as you return,
  and only the bar's shade and shadow change once content runs under it. The
  title no longer steps down in size.

**For maintainers**

* `tools/build_fa_subset.py` scans the console's templates and scripts too,
  and the console serves the gallery's generated `fa-icons.css`.

---

## 1.4.1 — 2026-09-13

Pretty links move under `/s/`.

**Changed**

* A link is now **`/s/<name>`** instead of `/<name>`. At the root, links and
  the gallery's own pages shared one namespace: every page a later release
  added was a name some link might already use, which 1.4.0 held off with a
  list of reserved names. `/s/` belongs to links alone, so the list is gone —
  `albums`, `stats` and `api` are ordinary link names now — and no future
  page can take over an address that is already printed somewhere.

**Before you upgrade**

* A link handed out as `/<name>` under 1.4.0 is `/s/<name>` now; the old
  address answers 404. `links.cfg` itself does not change.

---

## 1.4.0 — 2026-09-13

Pretty links.

**New**

* **A short address for an album or a photo.** `https://<site>/tokyo` instead
  of `/album/japan_2026/tokyo`, and `/fuji` for one picture. The list is a new
  file, `photos/.gallery/links.cfg` — one `name = target` line per link, in the
  grammar every other cfg file uses — so it lives with the photos, survives a
  rebuild of `data/`, and needs no scan and no restart.
* The console has a fourth place, **Links**: name a link, point it at an album
  (the field completes album paths) or pick a photo, then copy or open the
  address. An album's header and a photo's metadata panel each carry a
  **Link…** button that opens the form already pointing at them. Renaming,
  re-pointing and deleting are there too; every write gets a backup and an
  audit line like any other.
* The gallery answers `/<name>` with a **302 and `no-store`** — never a 301,
  which a browser keeps for good — so a link can be re-pointed later. A name
  that is not a link, or a link whose target has gone, is the ordinary 404.
* **Check all** and `doctor` report a link that would answer 404: a target
  that no longer resolves in the index, a name that is not a link name, a
  name the gallery already uses for a page of its own.

**Before you upgrade**

* Set `PUBLIC_BASE_URL` if you want the console to show and open full
  addresses. Without it the Links screen still works and shows `/name`: the
  console is on its own port and cannot know the address visitors use.

---

## 1.3.0 — 2026-09-12

The door stops being a bare form.

**New**

* The door says **why** you are looking at it. A session that idles out drops
  you there with no explanation, which reads as the tool having thrown you
  out; it now names the reason — timed out, signed out, or twelve hours up.
  The text is chosen server-side from a fixed list and nothing from the query
  string is ever rendered.
* **You land back where you were.** A timeout writes down which screen was
  open and the console restores it once, on the way back in. Signing out on
  purpose deliberately leaves no note — that is a decision to stop.
* A **Caps Lock** warning, and a **Show / Hide** for the password that keeps
  the caret where it was.
* The lockout **counts down**. After three tries the server backs off to five
  minutes; the door printed that number once and then stood there lying about
  it with the button still inviting another go. The button now says how long
  is left and comes back on its own.
* The card carries a hidden `username` field, because one account with no name
  is the shape a password manager cannot file — most will not offer to save
  the credential at all without it.
* The card fades out on the way to the console, instead of the browser
  flashing between two documents. The mark in the card splits on hover the
  way the header's does; it deliberately does not play the boot build.

**Fixed**

* `/login` had a `next` parameter that could only ever carry `/` — the console
  has no router — so it was an open-redirect sink standing open for no
  benefit. Removed.

---

## 1.2.0 — 2026-09-12

The console gets a face of its own.

**New**

* A boot screen. The mark draws itself in one piece at a time, the signal
  drops, and it comes back solid — the same sequence the site's own splash
  plays, at the same timings. It covers the seconds the console spends
  fetching what it needs, it plays **once per browser session**, any click or
  key takes it away, and it is never shown at all to someone whose system
  asks for less motion. Nothing of the tool is visible behind it: the
  backdrop is the room the console already lives in, with the furniture not
  yet in it.
* The logo is markup now, not a picture — inline SVG in the nav, the footer,
  the sign-in card and the boot screen. An `<img>` is a closed document and
  nothing outside it can reach a path; that is what made the mark unable to
  draw itself or to split into colour.
* The mark in the header splits into its two colour flanks on hover and
  snaps back — the animation's own language, in half a second.
* The sign-in card carries the version. Behind a password the footer is not
  reachable, and the version is the first thing to check when the tool
  behaves unlike the notes for it.

**Fixed**

* The sign-in card kept its wordmark on a phone. The rule that drops it is
  about the app bar — two lines of brand text plus a menu handle plus two
  buttons do not fit across 375px — and it was never scoped to it.

---

## 1.1.0 — 2026-09-12

The console stops being a form you land in the middle of.

**New**

* A front door: `/` is a dashboard built out of routes that already existed —
  index state, what the last scan did, the albums, what still needs writing
  about — rather than the first album's editor.
* **Operations** in the console speaks the same language as the home screen:
  the same words for the same states, the same colours, the same readouts.
* The console keeps a log of what it wrote, and tells you which albums have a
  description in which language.
* The sidebar holds one kind of thing: albums. Everything else moved into the
  three entries in the header (Home / Gallery / Operations).

**Changed**

* The CLI is five modules split by what a command does (`aperture/cli/`:
  render, operate, reports, screens, entry) instead of one file, and the
  interactive dashboard draws again.
* `main.py` became layered modules; `settings`, the cfg checks, `static_url`,
  `is_image` and the camera names each have one implementation and one name.
* The console wears the gallery's own fonts and one name per status colour.
* Derivatives are written upright — a photo is indexed the way it is shown.
* An album is required on the console's read routes too, not only the writes.
* `nebula/` is its own repository; this one links it instead of copying it.

**Fixed**

* The runtime's refusal reads correctly when the photo directory is missing.

## 1.0.0 — 2026-09-11

Two programs became one. The gallery and the *Configurator* shipped as one
package, one image and one version, and the number restarted at 1.0 because a
version is a promise about a thing and this was a new thing. (The gallery's
own line had reached 7.2; see *Before 1.0* below.)

**New**

* One `aperture/` package with two surfaces: gallery on `:8000`, console on
  `:8090`. `APERTURE_ROLE` (`all` / `public` / `console`) decides which
  listeners a process opens, and the indexer follows the *public* role, so
  every shape has exactly one writer on the SQLite file.
* The console has a door: a password, a session, and one function that decides
  where a write may land.
* The CLI's operational surface — live state, scan, pause / resume, `doctor` —
  is in the console too, over the same control channel.
* A characterization test net over the public surface.

**Changed**

* One grammar and one registry for what a cfg file may say, which closed two
  drifts the two old validators had been hiding.
* The container runs as uid 10001 and trusts the proxy it is actually behind.

**Before you upgrade**

* The container no longer runs as root: the host directories and any CIFS
  share must be owned by uid 10001 (the image's `chown` never reaches a bind
  mount).
* The console needs a password, or its listener stays closed. That is the
  intended behaviour, not a failure to start.
* Set `FORWARDED_ALLOW_IPS` to the proxy, or forwarded headers are ignored.

---

## Before 1.0

There was a *gallery* (1.0 → 7.2) and a *Configurator*, two programs with two
version lines, neither of them recorded here. They ended at the commit above.
The JSON API did **not** restart with them: its contract is versioned on its
own by `API_VERSION`, and the endpoints kept their shape.
