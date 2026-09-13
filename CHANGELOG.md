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
