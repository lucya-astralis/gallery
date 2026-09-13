# lucya.systems aperture

One app, two surfaces, two ports.

**Gallery** (`:8000`) is the public half: a lean, read-only image gallery with
folder-based albums, EXIF display, sidecar-file tags and automatic
thumbnail/preview generation. Safe for public hosting behind Cloudflare.

**Console** (`:8090`) is the operator half: the editor for the config files the
gallery reads, on its own listener, reachable only where you publish its port.
It was a separate program (the *Configurator*) until 1.0.

They ship as one package and one image. Which listeners a process opens is
decided by `APERTURE_ROLE` — `all` for one container with both, or `public` and
`console` for two containers with separate blast radii. See
[One app, two surfaces](#one-app-two-surfaces).

## Features

- **Folder = album:** every subfolder in `photos/` is automatically an album. Drop an image in → it appears in the album.
- **Your name on it:** the wordmark, logo, favicon, operator card, legal links and footer badges all come out of `gallery.cfg`; the assets live in `photos/.gallery/`. Nothing is hard-coded, and an unconfigured gallery calls itself “Gallery” behind a neutral mark.
- **Fully automatic indexing:** filesystem watcher (local) and/or periodic rescan (for SMB/NFS). No manual buttons in the web UI.
- **Two-tier images:** `/thumb/...` (480 px WebP) for grids, `/preview/...` (1600 px JPEG) for the detail view stage. The original (`/full/...`) only loads when you click *Load original*. The grid tier is WebP because an album page asks for hundreds of tiles and they are what a visitor on a slow line actually waits for (a third to a half smaller than the same JPEG: 42 KB → 26 KB average here); the preview tier stays JPEG because it is what `og:image` hands to link unfurlers. The URL never names the format, so nothing outside the server changes when a tier does.
- **EXIF:** camera, lens, exposure, ISO, focal length, … on the detail page. GPS coordinates are stripped by default (privacy).
- **Tags:** per-album ones come from `album.cfg` and label the album in its hero; per-photo ones are sidecar files (e.g. `IMG_0001.jpg.tags` containing `holiday, beach, sunset`) — click one in the album view to filter.
- **Showcase:** flag photos (`featured = …`) or a whole album (`showcase = true`) in the album's `album.cfg` to surface them on the welcome screen, on the album overview, and via `/api/showcase` JSON for embedding on other sites.
- **Public statistics (`/stats`):** what the archive holds, charted — a monthly timeline, the largest albums (each bar links into its album), cameras, focal lengths, apertures, ISO, tags, a 24-hour **polar dial** for time of day (the hours are cyclical, so they are drawn round), a weekday column chart, and a **stacked proportion bar** for orientation (the one series whose parts add up to every photo). Everything is measured from the photos' own capture dates and EXIF; no visitor is counted and nothing is logged, so the page is safe to share. Server-rendered SVG geometry — no JavaScript, no chart library. Linked from the archive readout under the welcome hero and from the footer.
- **Search & sort:** top bar searches album, file, and tag names; sort by date, name or size on every list view — plus a "Curated" order defined in `album.cfg` / `gallery.cfg`, which can also preselect the default sort.
- **By day:** an album whose photos span more than one day also offers a **By day** sort — newest day first, with the grid split into a framed section per capture day (day counter, weekday, photo count). On an album with a trip configured (`TRIPS` in `aperture/trips.py`) the counter is the trip day, counted from the outbound flight, and each day carries a chip naming the leg it falls into — sub-albums of that trip inherit both.
- **Three languages (EN / DE / JP):** selector in the top-right corner, cookie-backed with an `Accept-Language` fallback. Album descriptions are per-language markdown files (`album_en.md` / `album_de.md` / `album_jp.md`); UI strings live in `aperture/i18n.py`. See [Languages](#languages--i18n).
- **Mobile-friendly:** responsive grid, large touch targets, keyboard navigation (← → ESC) on desktop.
- **Read-only where it faces the public:** the gallery app has no route that is not a `GET` — no write endpoints, no uploads, no tag editing. The one write path in the product belongs to the console, on the other port, and reaches only the `.album/` and `.gallery/` metadata folders. See [Security / hosting](#security--hosting).
- **Console:** the config editor *and the operations panel*, built in. Album and gallery `cfg` files, per-language descriptions, icons, title fonts, wallpapers and brand assets, with validation — plus the indexer's live state, scan / pause / resume, and `doctor`, on its own port, never on the public one.
- **Operations CLI:** `python -m aperture.cli` — run or pause the indexer, check index/config/derivative drift with `doctor`, audit tags and GPS, and inspect exactly how a photo, an album, the welcome hero or a trip resolves. See [Operations CLI](#operations-cli).
- **Security headers:** CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy — all set by built-in middleware.
- **Custom 404 page** with megacorp-terminal aesthetic.

## Quick start

```bash
cp .env.example .env   # adjust paths & options
docker compose up -d --build
```

- Gallery: <http://localhost:8000>
- Console: <http://127.0.0.1:8090> — published to `127.0.0.1` by default.
  `CONSOLE_HOST` in `.env` decides which host address it appears on; anything
  other than loopback is a security decision, not a convenience one.

## One app, two surfaces

| `APERTURE_ROLE` | Listeners | Indexer | `photos` mount | Use |
|---|---|---|---|---|
| `all` | `:8000` + `:8090` | yes | `rw` | one container, the default |
| `public` | `:8000` | yes | `ro` | the exposed instance |
| `console` | `:8090` | no | `rw` | the operator instance, internal |

The indexer follows the *public* role rather than an app lifespan, so in every
shape there is exactly one writer on the SQLite file. A console-only process
is a reader: it asks for scans through the same flag-file control channel the
CLI uses (`data/control/`, see [Operations CLI](#operations-cli)).

Nothing routes between the two apps. The console's API lives under `/api/` as
well — it can, because a request that arrives on the public socket has no way
to reach a handler that was never mounted on it.

### Operations from either front end

`aperture/ops.py` is the operational surface as data — the live state of the
indexer, what `doctor` checked, what a scan did. Two front ends render it:
`aperture/cli/` to a terminal, `aperture/console/opsapi.py` over HTTP. There
is one implementation of each check, and a report that changes changes in both
places at once.

Actions go through the flag-file control channel in `data/control/`, never
through a function call — **even in the `all` role, where the console shares a
process with the indexer.** A scan requested in the browser is the same file
`python -m aperture.cli scan` writes, picked up by the same control loop. That
keeps one place where a scan can begin, one place to look when one did not,
and it means `APERTURE_ROLE=console` in its own container works with no second
implementation. The request carries `by: console` or `by: cli`, so `status`
says afterwards which one asked.

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
Japanese camera. UI strings live in `aperture/i18n.py` (server) and in the
`UI_STRINGS` table at the top of `aperture/gallery/static/app.js` (client) — keep both
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
sit in there safely.

**The `.gallery/` folder** is the same idea one tier up — the gallery's own
settings and assets rather than one album's:

```
photos/
├── .gallery/
│   ├── gallery.cfg        ← gallery-wide settings
│   ├── logo.svg           ← the wordmark's mark (`logo =`, `favicon =`)
│   ├── pfp.webp           ← the operator's portrait (`operator_pfp =`)
│   ├── eu.gif             ← a footer badge (`badges =`)
│   ├── Display.otf        ← the site's display face (`font =`)
│   └── bg.mp4             ← the site's backdrop (`wallpaper =`)
└── japan_2026/            ← an album
```

It is excluded from indexing exactly like `.album/`, so a logo never turns
up as a photo, and `python -m aperture.cli export` archives it along with every
`.album/`.

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

The **site** has the same two keys one tier down, in `gallery.cfg` against
`photos/.gallery/` — see [Look](#look). They do not collide: `album.cfg`'s
`font` sets that album's hero title, `gallery.cfg`'s sets the chrome (the
wordmark, the welcome screen's big word, the 404), and an album's hero title
is never restyled by the site face.

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
(`aperture/gallery/static/fonts/NotoSansJP-subset.woff2`, ~120 KB instead of the 8.8 MB
variable TTF). Every JP glyph the site can render must be baked in — after
changing/adding Japanese text anywhere (album_jp.md, i18n.py, app.js,
templates), rebuild it or new characters show as tofu:

```bash
python tools/build_jp_subset.py     # needs: pip install fonttools brotli
```

The subset always contains the full kana blocks plus every kanji currently
in use (the script scans the repo), so kana-only edits never need a rebuild.

**Static font instances:** the pages do not load the variable fonts
directly. Space Grotesk, JetBrains Mono and the JP subset are served as
static per-weight woff2 files (`SpaceGrotesk-400.woff2`,
`JetBrainsMono-600.woff2`, `NotoSansJP-subset-700.woff2`, …), because
instantiating a variable font is the most expensive part of a page's first
layout — on phones it stalled the album entrance animations. The JP script
above rebuilds its instances itself; after swapping a Latin variable font
or adding a weight to the stylesheet, run:

```bash
python tools/build_font_instances.py
```

**Display faces:** Ethnocentric and Chakra Petch have no variable source to
instantiate — they ship as the foundry's `.otf`/`.ttf` — but they are still
served as woff2, which is the same outlines in a smaller container (Ethnocentric
draws the wordmark on every page: 67 KB → 31 KB). After replacing one of those
source files, run:

```bash
python tools/build_display_faces.py   # needs: pip install fonttools brotli
```

**Logo raster:** the terminal CLI can draw the real logo as a picture (see
[Terminals](#terminals)), which needs a bitmap. `aperture/gallery/static/logo/lucya_logo.png`
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
| `name`       | free text, e.g. `Japan 2026`    | The album's display name, used everywhere the album is named: cards, breadcrumbs, hero title, `/api`. The folder name stays the URL, so renaming here never breaks a link. Unset, the folder name is used with its underscores relaxed into spaces (`japan_2026` → `japan 2026`). Commas are fine — they are rejoined. |
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
| `accent`     | a hex colour, `#7ad1ff`         | The accent of this album's pages — links, focus, the active state, the featured mark, the hero button. Sub-albums inherit it. Unset falls through to `gallery.cfg`'s `accent`, then to the built-in colour. A colour too dark to read on the black page is lightened (`doctor` says when). |
| `wallpaper`  | a filename in `.album/`         | This album's page backdrop on desktop — `.mp4` / `.webm` / `.jpg` / `.png` / `.webp` / `.avif`. Sub-albums inherit it. Unset falls through to `gallery.cfg`, then to the one shipped with the gallery. |
| `wallpaper_mobile` | a filename in `.album/`   | The same on phones. **Stills only** — the gallery never loads a backdrop video there. It also stands in as the poster frame behind a desktop clip while that buffers. |
| `wallpaper_tint` | `off`, or `0`–`1`           | How much colour that backdrop keeps. Unset inherits (ancestor album → `gallery.cfg` → the built-in near-greyscale). `off` is full colour, and also drops the accent wash lying over the picture. |
| `wallpaper_dim`  | `off`, or `0.25`–`1`        | How bright it is; `1` and `off` both leave it untouched. Unset inherits the same way, ending at the built-in `0.72`. |

### Pretty links (`links.cfg`)

A short address on the public site for one album or one photo —
`https://archive.example/s/tokyo` instead of `/album/japan_2026/tokyo`. The
list is one more file in **`photos/.gallery/`**, next to `gallery.cfg`:

```ini
# photos/.gallery/links.cfg
tokyo = japan_2026/tokyo          # an album
fuji  = japan_2026/hakone/fuji.jpg  # one photo
```

- A **name** is lower-case letters, digits and single hyphens, up to 64
  characters. The request is lower-cased, so `/s/Tokyo` works too.
- A **target** is an album path, or a photo's path inside its album; the
  extension decides which. Both are resolved against the index when someone
  follows the link.
- The gallery answers with a **302 and `no-store`**, never a 301 — a browser
  keeps a permanent redirect for good, and a link is meant to be re-pointable.
- A name that is not a link, or a link whose target has gone, gets the
  ordinary 404 page.
- Every link lives under **`/s/`**, and nothing else of the gallery ever will.
  So any name is free — `/s/albums` is a link, `/albums` stays the page — and
  a page the gallery gains in a later release can never take over an address
  that is already printed somewhere.
- `Check all` in the console and `doctor` report every link that would
  answer 404.

The console's **Links** screen edits this file — see
[the console's README](aperture/console/README.md#what-it-edits). Set
`PUBLIC_BASE_URL` for it to show and open the full address.

### Gallery settings (`gallery.cfg`)

Optional file in **`photos/.gallery/`**, next to the assets it names. By default the welcome hero cycles through a random selection of showcased photos (falling back to fully random when nothing is showcased). To pick the images yourself:

```ini
# photos/.gallery/gallery.cfg — welcome hero feed
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

#### Look

The same file carries how the archive looks. Every key here is one an
`album.cfg` also has, spelled identically — this is simply the tier below it:
**`gallery.cfg` dresses the whole site, and an album still overrides it for
its own pages.** Nothing is required; with none of it set the gallery wears
its built-in colours, face and backdrop.

```ini
# photos/.gallery/gallery.cfg — how the site looks

# the accent every page wears that no album has repainted: links, focus,
# the active state, the featured mark, the hero button
accent = #7ad1ff

# the display face of the CHROME — the wordmark, the welcome screen's one
# big word, the 404. Lives in photos/.gallery/ next to the logo.
font = Display.otf          # .otf / .ttf / .woff2 / .woff
font_scale = 1.1            # optional, 0.5–2.5 — size multiplier for it

# the backdrop behind every page an album has not dressed
wallpaper = bg.mp4          # desktop: a clip or a still
wallpaper_mobile = bg.jpg   # phones: stills only

# how that backdrop is treated (an album that sets these still wins)
wallpaper_tint = off        # off | 0–1   — how much colour it keeps
wallpaper_dim  = .72        # off | .25–1 — how bright it is
```

Three things worth knowing:

* **`font` is the chrome's face, not an album's title.** An album's hero
  title has its own `font` in `album.cfg` and is never restyled by this one —
  otherwise the same key would mean two different treatments depending on
  which file you wrote it in. `font_scale` here scales every text the site
  face sets at once, so a face that inks small comes back up everywhere
  rather than one heading at a time.
* **`accent` is checked, not trusted.** It is parsed into three derived faces
  (small text on black, a fill under black label text, and the one face that
  carries white text), each held to a 4.5:1 contrast floor — so a colour too
  dark to read is lightened rather than shipped unreadable. `doctor` reports
  when that happened.
* **The backdrop falls through in tiers.** An album's own wins, then its
  nearest ancestor's, then `gallery.cfg`'s, then the clip shipped under
  `/static`. When a configured clip is playing, the configured
  `wallpaper_mobile` is the poster frame behind it — two crops of one
  backdrop rather than a stock photo flashing for a beat.

Because the CSP forbids inline styles, all of it reaches the page as real
stylesheets: `/site-theme.css` (or `/album-theme.css/{album}`) redefines the
`--acc…` and `--wallpaper-filter` tokens `style.css` already reads, and
`/site-font.css` carries the `@font-face` plus `--display-font` /
`--display-scale`. The files themselves come from `/site-font` and
`/site-wallpaper/{desktop|mobile}`. As everywhere else, only the file the cfg
names is ever served — the filename never travels in the URL.

#### Branding

The same file carries everything that says whose archive this is. All of it
is optional; with none of it set the gallery calls itself **Gallery** behind
a neutral built-in mark, so a fresh deployment never wears someone else's
name. Files named here live in `photos/.gallery/` and are bare filenames —
nothing with a slash in it.

```ini
site_name = lucya.systems          # wordmark, first line, and og:site_name
site_sub  = gallery                # wordmark, second line
site_hero = Gallery                # the one big word on the welcome screen
site_desc = Personal photo archive.   # meta description
site_desc_de = Persönliches Fotoarchiv.  # (also _en / _jp) wins for its language

logo    = logo.svg                 # the mark beside the wordmark
favicon = logo.svg                 # unset = the logo doubles as the tab icon

operator     = lucya               # who is behind the archive
operator_url = https://lucya.sh    # the operator card needs this to appear
operator_pfp = pfp.webp

privacy_url = https://lucya.sh/privacy   # unset = no such footer link
imprint_url = https://lucya.sh/privacy   # may point at the same page

badges =                           # classic 88x31 web buttons, `file | label`
    eu.gif | European Union
    pride.png | Progress Pride
```

Notes:

- `operator_url` gates both the footer's operator card and the welcome
  screen's *about me* button — neither is rendered without somewhere to
  point. Same for the two legal links: unset means the link is gone, which
  beats a dead one.
- URLs must be `http(s)` or site-relative; anything else is dropped rather
  than written into an `href`.
- Text values may contain commas (the parser splits on them and the gallery
  rejoins the parts), but the two halves of a badge line may not.
- `python -m aperture.cli doctor` reports a key naming a missing file, a bad URL
  or a badge that will silently vanish — none of which look like errors in
  the browser.
- Japanese branding text is a special case: the shipped Noto Sans JP is a
  glyph **subset**, and `tools/build_jp_subset.py` scans the cfg files at
  build time. New kanji in `site_desc_jp` need that re-run and the font
  redeployed, or they render as tofu.

#### Credit

Who took the photographs, written into the **derived** images' EXIF:

```ini
credit = lucya      # EXIF Artist/Copyright on thumbnails, previews, fulls
```

Metadata only — nothing is drawn onto a photograph, and the files under
`photos/` are never rewritten. It sits alongside the `Software` tag naming
the gallery software itself, and the two answer different questions: one
says what made the file, the other whose picture it is. Changing `credit`
re-derives what it affects on the next request (unrelated edits to
`gallery.cfg` do not), so it needs no manual purge.

#### What stays with the software

Everything above is the operator's to set. What is *not* configurable is the
software's own origin: however a deployment is branded, it still says what
it runs on. That set lives in [`aperture/brand.py`](aperture/brand.py), reads no
config, and reaches a visitor through eight independent channels:

| Where | What it says |
|---|---|
| `/humans.txt` | the full colophon — software, version, vendor, and the archive beside it. Linked from every page as `rel="author"` |
| Footer | *lucya.systems gallery · 7.0*, linked, on every page including 404 |
| `<meta name="generator">` | `lucya.systems gallery 7.0`, outside the overridable `meta` block |
| `X-Powered-By` | on **every** response — pages, JSON, stylesheets, and image bytes, including originals served untouched |
| `/api` | `product`, `product_version`, `vendor`, `vendor_url`, kept separate from the archive's own `name` |
| EXIF `Software` | written into every derivative — thumbnails (WebP), previews and converted fulls (JPEG) |
| `style.css` / `app.js` | a banner at the top of both files, which an operator serves verbatim |
| CLI masthead | `python -m aperture.cli` is the vendor's tool, not the operator's site |

The one place it is deliberately *absent* is the photographs themselves.
Nothing is ever drawn onto a picture, and the EXIF `Artist`/`Copyright` of a
derived image carry the operator's `credit`, never the vendor's name —
resizing an image is not authorship. Attribution lives in the chrome, the
headers and the file metadata, which is where all eight channels above sit.

None of this is enforcement — anyone holding the source can delete a line.
It is the set of defaults that makes attribution the path of least
resistance; a licence is what would actually ask for it.

Rules for the hand-picked welcome list:

- Paths are relative to `photos/` (`album/file.jpg`, nested albums allowed); backslashes are tolerated.
- Entries accumulate in order (max 24, duplicates collapse).
- Entries that aren't indexed are skipped with a log warning; if nothing resolves, the feed falls back to showcase/random as if the file weren't there.
- With a hand-picked list the hero shows a `CURATED` label and hides the ⟳ TUNE (reshuffle) button.
- `welcome_mobile` / `welcome_desktop` accept the same syntax as `welcome` and win over it for their device class. Phones are detected via the User-Agent (`Mobi`); Android tablets and iPads in desktop mode get the desktop feed.

## Design — Nebula

The look has a name, because two surfaces wear it: the gallery, and the
[console](aperture/console/) that edits its config files. **Nebula**, after
the accent it rations — `#5865F2`, "Nebula Blue". Six rules, and everything
in `aperture/gallery/static/style.css` is one of them:

1. **Black ground, grey furniture, one purple accent.** `--acc` marks *state*
   and nothing else — links, focus, active/selected/open, the featured mark.
   Furniture (labels, counts, hovers, HUD strokes) is `--chrome` or `--label`.
   Before colouring something purple, ask whether it carries state. The accent
   never sits on its own tint, and reading copy bottoms out at `--text-dim` —
   `--text-ghost` is a ghost, not a text colour.
2. **Square corners, always.** `--radius: 0`. Only genuinely round things —
   the status dot — opt out with their own `border-radius`.
3. **Depth comes from blur, not from darkness.** Six surfaces float over the
   drained wallpaper — `--glass`/`-hi` (panes), `--scrim`/`-hi` (chips on a
   picture), `--pane` (the app bar), `--overlay` (menus) — each with a step off
   the `--blur-1..3` ladder. A surface that needs to read better gets more blur
   or the `-hi` step, never a darker fill. A pane is a fill, a blur and a
   hairline: no bevel, no gloss. `--pane` is the one exception to the ladder:
   **deep black**, no hue in the fill and `--blur-1` behind it, so the bar
   framing every screen never picks up an album's colour.
4. **The mono, tracked, uppercase voice marks the chrome *around* a photo
   grid** — section labels, counts, measured values, the meta line. Everything
   a person actually reads is Space Grotesk in sentence case. The display face
   (Ethnocentric) is the wordmark's voice and is not a heading's. The token
   name carries the voice: `--fs-chrome-*` against `--fs-text-*`.
5. **Measure is rationed like colour.** Every size, space, duration and blur
   comes off a named scale — `--s-0..10`, `--fs-*`, `--tr-*`, `--dur-*`,
   `--blur-*`. A raw `px` is the same mistake as a raw hex, and the check is a
   grep: `grep -oE '(padding|gap|margin|font-size):[^;]*[0-9]+px' style.css`.
6. **The environment can overrule the look.** Blur, translucency, motion and
   hover are capabilities, not guarantees. Every fallback — `html.fx-lite`,
   `prefers-reduced-transparency`, `prefers-contrast`, `forced-colors`,
   `prefers-reduced-motion`, `hover: none` — is reached by *redefining tokens*,
   never by overriding component rules.

Rules 1–4 say what a thing looks like. Rules 5 and 6 are what make it a
*language* rather than a look: a look is copied by eye and drifts the first
time someone eyeballs a padding; a scale and a set of declared fallbacks can
be inherited.

An album can repaint the accent and dress the backdrop
(`accent` / `wallpaper*`), and the operator can replace the display face —
those are the knobs Nebula exposes, and they run through the same derivation
so a hand-typed hex still lands with the contrast guarantees. Everything else
is the software's.

The console carries the same tokens and the same controls rather than a
lookalike of them; its README lists which of its objects comes from which of
the gallery's. They ship in one image now, but still as **two stylesheets** —
`aperture/gallery/static/style.css` and `aperture/console/static/style.css` — so a
change to the language still has to be made in both. Collapsing them onto one
shared `nebula.css` is a later milestone.

[`nebula/`](nebula/) is the language on its own, away from either app: an
interactive specimen book (`index.html` — the component set, plus four
switches that break one rule each so the page shows what a rule is *for*), a
portable base sheet a third app can start from (`nebula.css`), and
[`NEBULA.md`](nebula/NEBULA.md), the same content written as instructions to
hand to Claude. Serve it with `python -m http.server 8123 --directory nebula`.

It is **its own repository**
([lucya-astralis/nebula](https://github.com/lucya-astralis/nebula)) checked out
in that folder, not a part of this one — cloning the gallery does not bring it,
and this repo ignores it rather than keeping a second copy in step:

```bash
git clone git@github.com:lucya-astralis/nebula.git nebula
```

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

| Path            | Purpose                                                   |
|-----------------|-----------------------------------------------------------|
| `photos/`       | Your originals + `.tags` sidecars + the `.album/` and `.gallery/` metadata folders |
| `thumbnails/`   | Generated grid thumbnails (cache, can be wiped anytime)   |
| `previews/`     | Generated stage previews (cache, can be wiped anytime)    |
| `data/`         | SQLite DB with EXIF cache and tag index                   |
| `data/control/` | Flag files the CLI and the server talk through (see below)|
| `data/console/` | The console's own state: its password hash, the audit log, rolling backups of every file it overwrites |

Everything this software is trusted with lives under `data/`, never in the
photo tree — the gallery *serves* files out of `photos/.gallery/`, so a secret
placed there would be a secret published.

Inside the package:

| Path                | Purpose                                             |
|---------------------|-----------------------------------------------------|
| `aperture/gallery/` | the public app: pages, the JSON API, the media routes, its static files and templates |
| `aperture/console/` | the console app, its static files and templates     |
| `aperture/albums.py`, `photos.py`, `theme.py`, `config.py`, `branding.py`, … | what an album, a photo, a theme and a cfg ARE. No route and no template reaches in from here, and nothing here imports the web layer — a test asserts that direction |
| `aperture/schema.py` + `cfgio.py` | which keys a cfg file may hold, and the grammar that reads and rewrites it |
| `aperture/checks.py`| what is wrong with a cfg file — one implementation, asked by `doctor`, the CLI and the console |
| `aperture/scanner.py` + `indexer.py` | the walk that fills the index and writes the derivatives, and the loop that schedules it |
| `aperture/ops.py`   | the operations surface the CLI and the console's `/api/ops/*` share |
| `aperture/server.py`| the process: which listeners open, and shutdown     |
| `aperture/runtime.py`| the one place that reads the environment            |
| `aperture/cli/` + `termui.py` | the operator CLI (`render` how it writes, `operate` the commands that act, `reports` the ones that only look, `screens` the full-screen surfaces, `entry` the parser) and its terminal vocabulary |
| `tests/`            | the characterization net (`python -m pytest`)       |

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
| `APERTURE_ROLE` | `all`         | `all` / `public` / `console` — which listeners open         |

Console only:

| Variable          | Default     | Meaning                                            |
|-------------------|-------------|----------------------------------------------------|
| `CONSOLE_ENABLED` | `1`         | `0` leaves the second listener closed entirely      |
| `CONSOLE_PORT`    | `8090`      | Port inside the container                           |
| `CONSOLE_BIND`    | `127.0.0.1` | Address the console binds. In Docker this is `0.0.0.0` and `CONSOLE_HOST` in `.env` decides which host address the port is published on — a container's own loopback would be unreachable from anywhere |
| `READ_ONLY`       | `0`         | `1` = browse and validate, write nothing            |
| `BACKUPS`         | `20`        | Versions kept per edited file under `data/console/backups` |
| `MAX_UPLOAD_MB`   | `8`         | Cap on icon / font / wallpaper uploads              |

## Operations CLI

Everything operational is one command — or, since 1.0, the **Operations** entry
at the top of the [console](aperture/console/), which serves the same reports
from the same functions (`aperture/ops.py`). The console covers what you need
while the gallery is running: live indexer state, scan now, pause / resume, and
`doctor`. The CLI covers those plus everything that is about authoring and
shipping — `cfg`, `photo`, `trip`, `tags`, `welcome`, `gps`, `export`, `i18n` —
and it is the only one of the two that works with nothing running at all.

Everything operational is one command:

```bash
python -m aperture.cli
```

Without arguments it draws the dashboard — masthead, what the server is
doing, and what the archive holds — and then keeps an interactive menu
underneath it (only when it actually has a terminal; piped or in a cron job
it prints the dashboard and exits).

```
┌─ LUCYA.SYSTEMS APERTURE ──────────────────────────────────── OPS CONSOLE ─┐
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
python -m aperture.cli <command> [options]
```

In Docker, run it inside the container:

```bash
docker compose exec gallery python -m aperture.cli status
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
| `i18n` | EN/DE/JP completeness in `aperture/i18n.py`, keys used but undefined (they render as the key), and whether the `UI_STRINGS` mirror in `app.js` has the same keys in every language |

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
python -m aperture.cli term
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

The picture comes from `aperture/gallery/static/logo/lucya_logo.png`; if it is missing,
run `python tools/render_logo.py` (see above) — `term` says so too. Its
transparency is preserved in every mode: the logo sits on your terminal
background, not in a white box.

```bash
python -m aperture.cli --logo kitty
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

### Derivatives are written upright

A phone writes a portrait photo as a landscape buffer plus an EXIF
`Orientation` tag saying "turn this". The generated thumbnails and previews
carry no such tag of their own — they get `Software` and your `credit` and
nothing else — so a browser cannot turn them, and the grid tile is sized from
the width and height in the index. Both are therefore corrected when they are
written: the pixels by the scanner, the stored dimensions with them.

An archive indexed before this rule only needs catching up if it holds such
photos (`doctor` will not flag them — they are not broken, only sideways):

```bash
python -m aperture.cli scan --force        # re-read the dimensions
python -m aperture.cli thumbs --rebuild --all   # rewrite the files
```

### Examples

```bash
# is anything running, and what did the last scan do?
python -m aperture.cli status

# freshly dropped an album on the share and do not want to wait 5 minutes
python -m aperture.cli scan japan_2026/kansai

# reorganising folders — stop the indexer from reacting to every move
python -m aperture.cli pause "resorting kansai"
python -m aperture.cli resume --scan

# why is this photo not in the reel?
python -m aperture.cli photo japan_2026/kansai/osaka/IMG_4853.png
python -m aperture.cli featured japan_2026

# after changing THUMB_SIZE
python -m aperture.cli thumbs --rebuild --all

# after an upgrade that changes a tier's FORMAT (the grid tier became WebP):
# build what is missing, then clear the files of the old format, which the
# orphan sweep reports because no photo maps to them any more
python -m aperture.cli thumbs --rebuild
python -m aperture.cli thumbs --prune            # look first
python -m aperture.cli thumbs --prune --apply    # then delete

# nightly health check (exits 1 when it finds something)
python -m aperture.cli doctor --json

# what the front page will actually show, and what it skipped
python -m aperture.cli welcome

# tag vocabulary, and whether the sidecars and the index still agree
python -m aperture.cli tags

# privacy audit: which originals still carry coordinates
python -m aperture.cli gps

# snapshot every hand-written file (config, text, icons, fonts)
python -m aperture.cli export --out backups/config.tar.gz
```

## Performance

The gallery is fast to *render* — pages come out of the server in 2–5 ms warm —
so everything that makes a visit feel slow is on the wire. Two things decide
that: how many bytes a page asks for, and how far away the visitor is.

**What a page is allowed to fetch before it is needed.** Nothing the visitor
has not asked for is downloaded on a link that cannot afford it. `app.js` asks
`hasFastLink()`, which reads Save-Data and `prefers-reduced-data`, then the
browser's `effectiveType` where there is one (it is derived from throughput
*and* round-trip time, so it also catches a fat line to a server on another
continent), and then — because a declared `downlink` cannot tell "fast" from
"not measured yet", and because Safari and Firefox declare nothing at all —
the page's own resource timings: the best throughput actually observed on
something that came over the network. On a "no", the following do not happen:

| Deferred on a slow link | What it costs otherwise |
|---|---|
| The ambient backdrop clip | 6.3 MB, decoration only (already desktop-only) |
| Reel / viewfinder auto-advance | ~320 KB per turn, for frames nobody scrolled to |
| Reading ahead in the reel | 2 previews (~640 KB) before the first interaction |
| Hover-prefetch of a grid photo | ~320 KB per guess; pressing still prefetches |
| Lightbox + prev/next neighbour warming | ~640 KB per photo viewed |

On a fast connection every one of those still happens exactly as before. The
clip additionally waits for `load` + idle, so it never competes with the first
screenful of tiles.

**Sized for where it is shown.** A file the config names is served at the size
the page draws it at, not at whatever size it happens to be: the operator
portrait is a 34 px avatar and was a 539x539 PNG, 350 KB on every page of the
archive, now a 128 px WebP of 4 KB (`branding.brand_render`). Showcase covers carry a
`srcset` across the two photo tiers, so a card that is a third of the page wide
takes the grid tile rather than the 1600 px preview.

**Tiers and formats.** `/thumb/` (480 px WebP) is what an album page asks for
hundreds of times; `/preview/` (1600 px JPEG) is one image on a photo page and
the reel's current frame. The reel ships only its first slide with a `src` —
crossfade slides all stack at one spot, so `loading="lazy"` defers nothing
there — and the grid relies on real lazy loading plus `content-visibility`,
which on a 439-photo album means 12 tiles fetched instead of 452.

**Measured** (headless Chrome over CDP, Fast-3G profile, cold cache, 30 s
window, `/album/japan_2026` — 439 photos):

| | before | after |
|---|---|---|
| desktop 1440 px, total | 5.65 MB | **1.14 MB** |
| … reel previews | 2.9 MB (8 files) | 0.27 MB (1 file) |
| … backdrop clip | 1.8 MB, still going | none |
| … grid thumbnails | 0.39 MB (12 tiles) | 0.28 MB (12 tiles) |
| … `load` event | 21.2 s | 6.3 s |
| phone 390 px, total | 3.57 MB | **0.87 MB** |
| `/albums`, total | 3.74 MB | **0.86 MB** |

On an unthrottled connection the same page still fetches all of it — 8.8 MB,
clip and full reel included — which is the point: nothing was removed, it was
made conditional.

**Distance.** HTML is `no-store` (it is per-visitor: language cookie, live
counts) so a page render always comes from the origin, but *every* asset URL
is immutable and stamped — put them all in the edge cache, including the
generated ones, or a visitor far from the server pays the round trip for the
wordmark and the album's title face too. See the Cloudflare notes under
[Security / hosting](#security--hosting).

## Security / hosting

### The public surface is read-only

- No write API, no uploads, no tag editing — the gallery app answers `GET`,
  `HEAD` and `OPTIONS` and has no other method on any route. That is asserted
  by a test (`tests/test_runtime.py`), not just intended.
- Path traversal blocked (`media.safe_rel`); the four routes that turn a URL into a
  filesystem path have their own test file (`tests/test_paths.py`)
- GPS stripping on (`HIDE_GPS=1`)
- Tags, thumbnails and the index live in `data/` and `thumbnails/` — none of it
  is security-critical

### The console is the write path, and it is on the other port

Until 1.0 the editor was a separate program, and this section could say
"`photos/` is mounted `:ro`, so even a code bug cannot touch the originals".
With the two merged that sentence needs qualifying: in the `all` role one
process serves both surfaces and the photo mount is writable.

What replaces it:

- **Where a write can land.** One function decides — `writable_target()` in
  `aperture/paths.py` — and it returns paths inside an album's `.album/`
  folder or `photos/.gallery/`, and nowhere else. Per-photo tags are the third
  and last case: a `.tags` sidecar sits *beside* its photo by the convention
  the scanner reads, so `sidecar_target()` derives the name from a file that
  must already exist and must be an image. A photograph is never a writable
  target, and there is no delete outside those places. The resolver also
  refuses traversal, absolute paths, reserved Windows device names, alternate
  data streams, trailing dots and spaces, and any path reached through a
  symlink.
- **Where the console can be reached from.** Its port is published to a host
  address, not to every interface. `127.0.0.1` is the default; a LAN or VPN
  address puts it on exactly that network. It is not meant for the open
  internet — reach it over WireGuard, Tailscale or an SSH tunnel.
- **What it never touches.** The console does not write the index. Scans and
  pauses go through the flag-file control channel, so the indexer stays the
  single writer on the database.
- **Two containers if you want the old guarantee literally.** Run
  `APERTURE_ROLE=public` with `photos:ro` and `APERTURE_ROLE=console`
  separately; it costs one environment variable.
- **Not root.** The image runs as uid 10001; the compose service adds
  `read_only`, `cap_drop: ALL` and `no-new-privileges`.

### The console's door

- **A password, not a port.** One operator password, hashed with `scrypt`
  (stdlib — no new dependency), stored in `data/console/credentials` with mode
  0600. Set it with `python -m aperture.cli passwd`, which prompts without
  echoing or reads `--stdin`; it is never an argument, because argv lands in
  shell history, `ps` output and a container's inspect JSON.
- **Fail-closed at startup.** No password and a non-loopback bind ⇒ the
  process refuses to open that socket and prints how to fix it. Inside a
  container the bind is always `0.0.0.0`, so if your boundary is elsewhere
  (the port is only published on `127.0.0.1`, or a firewall covers it) say so
  with `CONSOLE_ALLOW_OPEN=1`. On loopback, no password runs open with a
  warning on every start — and a red bar across the console saying so.
- **Sessions.** Server-side, `HttpOnly` + `SameSite=Strict` cookie, `Secure`
  where the browser would accept it, 30 minutes idle and 12 hours absolute.
  Changing the password ends every open session.
- **CSRF, three layers.** `SameSite=Strict`, an `Origin` check on every
  mutating request, and a session-bound token in `X-Aperture-CSRF`. The
  cross-site check applies even when the console runs open.
- **Throttling.** Three free attempts per address, then exponential backoff
  capped at five minutes; the answer becomes `429` with a `Retry-After`.
- **Uploads.** Extension allowlist *and* a magic-byte check, so an HTML page
  named `icon.png` is refused; 8 MB cap; a 64 MP ceiling on anything Pillow
  decodes.
- **An audit log.** `data/console/audit.log`, one JSON line per write: time,
  address, session, the file relative to `photos/`, and its SHA-256 before and
  after. Never the content — the rolling backups beside it are for that.

Every bullet above has a test in `tests/test_security.py`, which is the point
of writing them as bullets.

**Still open** — TLS is yours to provide. A session over plain HTTP to a LAN
address travels in clear, and the console says so in the log. Put it behind
WireGuard, Tailscale or an SSH tunnel rather than exposing it.

**Built-in security headers** (set by middleware in `aperture/gallery/app.py`):

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
- **A Cache Rule for the generated-asset routes too** — `/brand/*`,
  `/album-icon/*`, `/album-font/*`, `/album-font.css/*`, `/site-font*`,
  `/album-theme.css/*`, `/site-theme.css`, `/album-wallpaper/*`,
  `/site-wallpaper/*`. They all send `Cache-Control: public, max-age=31536000`
  and all carry a `?v=` stamp, but Cloudflare decides what to cache by *file
  extension* by default and none of those paths has one, so without a rule
  marking them "eligible for cache" every visitor fetches the wordmark, the
  operator portrait, the album's title face and its theme sheet from the
  origin. That is the difference between a nearby edge and a server on
  another continent on the first page view — the case this matters in is
  exactly the one where it hurts (see [Performance](#performance))

## Endpoints

All GET, all public:

- `GET /` — welcome screen (live-view hero cycling through the `gallery.cfg` feed: curated list, showcase or random; plus a Showcase Albums section)
- `GET /albums` — album overview (showcase albums section + main grid; `?sort=`)
- `GET /stats` — public statistics: headline figures plus ten charts over the whole index (timeline, albums, cameras, focal length, aperture, ISO, orientation, weekday, hour of day, tags). Computed in `aperture/stats.py`, drawn by the Jinja macros in `aperture/templates/_charts.html`. Bars, columns and the stacked bar are plain HTML with one `<rect>` per mark in a stretched (`preserveAspectRatio="none"`) SVG; the hour dial is a real uniformly-scaled SVG whose polar geometry is computed server-side. In every case the sizes are SVG *geometry attributes*, never inline CSS, because `style-src 'self'` drops inline styles (see [Security](#security--hosting)) — and never JS, so the page is complete without scripts. Colour is one hue on a four-step ramp keyed to each mark's value against its series maximum.
- `GET /album/{album}` — images in an album (`?tag=`, `?sort=`)
- `GET /image/{album}/{file}` — detail view (stage shows preview by default; `?sort=` preserved for prev/next ordering)
- `GET /thumb/{album}/{file}` — grid thumbnail (lazy generated)
- `GET /preview/{album}/{file}` — stage preview (lazy generated)
- `GET /full/{album}/{file}` — original file
- `GET /album-font.css/{album}` — generated stylesheet for an album's `font =` face (`@font-face` + `--album-title-font`, plus `--album-title-scale` when it sets `font_scale =`); 404 when the album sets none
- `GET /album-font/{album}` — the font file itself; only ever the one named in that album's `album.cfg`
- `GET /album-icon/{album}` — the album's `icon =` mark; only ever the file named in that album's `album.cfg`, 404 when it sets none
- `GET /site-font.css` — the same thing one tier down for `gallery.cfg`'s `font =` (`@font-face` + `--display-font`, plus `--display-scale` for `font_scale =`); 404 when the gallery sets none
- `GET /site-font` — that face's file; only ever the one named in `gallery.cfg`
- `GET /site-theme.css` — the theme of every page that is not an album's: `--acc…` from `gallery.cfg`'s `accent =`, `--wallpaper-filter` from its backdrop knobs. 404 when nothing is themed and `style.css`'s own tokens stand
- `GET /album-theme.css/{album}` — the same for one album, resolved through its ancestors and then `gallery.cfg`
- `GET /album-wallpaper/{desktop|mobile}/{album}` — the backdrop an `album.cfg` names; the album in the path is the one that *set* the key, so a sub-album never serves through its parent's URL
- `GET /site-wallpaper/{desktop|mobile}` — the backdrop `gallery.cfg` names, for every page no album has dressed
- `GET /brand/{logo|favicon|pfp}` + `GET /brand/badge/{index}` — the operator's marks and footer badges from `photos/.gallery/`; the index is the position in the *rendered* row
- `GET /search?q=…` — search (`?sort=`)
- `GET /s/{name}` — a pretty link from `photos/.gallery/links.cfg`: a 302 to the album or photo it names, 404 otherwise (see [Pretty links](#pretty-links-linkscfg)). `/s/` holds links and nothing else
- `GET /lang/{en|de|jp}?next=…` — set the language cookie, 303 back to `next` (relative paths only)
- `GET /api` + `/api/stats` + `/api/albums` + `/api/album/{album}` + `/api/photos` + `/api/photo/{rel_path}` + `/api/tags` + `/api/showcase` + `/api/shuffle` — the JSON API, CORS-enabled (see [API](#api))
- `GET /api/trip-weather?trip=…` — current conditions per trip stop plus today's high/low, served as a same-origin proxy to [Open-Meteo](https://open-meteo.com/) (weather data CC BY 4.0). Server-side cache (15 min); the visitor's browser never contacts a third party, so no cookies and no consent banner are involved.

## Versions

The version is one constant: `VERSION` in `aperture/brand.py`. The nav, the
footer, `/humans.txt`, `X-Powered-By`, `<meta name="generator">`, the EXIF
`Software` tag written into derived JPEGs and the CLI masthead all derive from
it, so a release is one edit and cannot half-happen. What each release
actually changed is in [CHANGELOG.md](CHANGELOG.md).

`MAJOR.MINOR.PATCH`, and each part is a promise to whoever runs the thing
rather than a category of code change:

| | what it says | what the operator does |
|---|---|---|
| **MAJOR** | the program is a different shape than it was — surfaces, ports, where the data lives, what the container is | read the entry before pulling |
| **MINOR** | something new to see or to set: a page, a config key, a command, a visible behaviour | pull; the entry names any required step under **Before you upgrade** |
| **PATCH** | fixes, refactors, docs, tests — nothing new to learn | pull |

A required step (the container's uid, a new mandatory setting) does **not**
force a MAJOR. It goes in the entry, in bold, where an operator reads it
before upgrading; reserving the big number for "this is a different program"
keeps it meaningful, and 1.0 was the last time it happened.

### The other numbers, which are not this one

* **`API_VERSION`** (`aperture/gallery/api.py`) versions the JSON contract and
  moves only when that contract breaks for a consumer. It did not reset when
  the product did.
* **Static assets** are cache-busted per file by mtime (`static_url`), so a
  release is not a cache key and a stylesheet fix ships without one.
* **Nebula** (the design language) is its own repository with its own
  version. A restyle here is a change in aperture, not in Nebula.
* **The config grammar** has no version: `aperture/schema.py` is the registry,
  and a key is added or removed in a release entry like anything else.

### Making a release

1. Bump `VERSION` and write the `CHANGELOG.md` entry **in the commit that
   earns it** — never as a separate "release" commit, so the number and the
   notes can never be one commit apart.
2. `python -m pytest` — `tests/test_version.py` checks the constant against the
   changelog's newest heading, the format, and that the entries descend.
3. `git tag v$(python -c "from aperture import brand; print(brand.VERSION)")`
   if the release is worth pointing at later. The deployment itself tracks
   `main`, so the tag is a bookmark, not a trigger.


## Local development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
mkdir -p photos thumbnails previews data

python -m aperture                 # both surfaces, one process
```

`--reload` is a uvicorn CLI feature and does not apply to the two-listener
run, so while working on one surface start that one on its own:

```bash
uvicorn aperture.gallery.app:app --reload --port 8000          # gallery
uvicorn aperture.console.app:app --reload --port 8090   # console
```

Tests:

```bash
python -m pytest
```

They build their own photo tree in a temp folder and index it synchronously,
so they never touch `photos/` and never race the scanner.


## Notes

- First scan over a large library takes a while (EXIF + two thumbnail sizes). After that everything is cached.
- Delete an image: remove it from `photos/` — watcher/scan clean up DB entry, thumbnail, and preview.
- Rename a tag: edit the `.tags` file.
- Thumbnails, previews, and DB can be wiped any time — they are regenerated on the next scan.
- Something looks off? `python -m aperture.cli doctor` compares index, files, derivatives and config in one pass.
