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
