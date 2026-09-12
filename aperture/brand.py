"""Who built this — as opposed to who runs it.

The two used to be one string. Every page said "lucya.systems gallery",
which was simultaneously the name of the archive's operator and the name of
the software serving it. Those are different facts about a deployment and
only one of them belongs to the person running it, so they were split:

  operator branding   gallery.cfg (`site_name`, `logo`, `operator_url`, …)
                      resolved in branding.site_brand(); assets live in
                      photos/.gallery/. Theirs to set, neutral by default.

  vendor identity     this module. Fixed, reads no config, ships with the
                      code — the attribution that travels with the software.

However a deployment is branded, it still says what it runs on. Eight
independent channels, so that no single edit to a template quietly removes
the lot:
  * /humans.txt — the full colophon, and the only one a person can be
    pointed at rather than having to view source for. Every page links it
    as rel="author"
  * the footer's "powered by" line, on every page including the 404
  * <meta name="generator">, deliberately outside the overridable meta block
  * the X-Powered-By response header — the broadest of the set, since it
    rides on EVERY response: pages, JSON, stylesheets, and image bytes,
    including originals handed out untouched
  * /api's `product` / `vendor` / `vendor_url`, separate from the archive's
    own `name`
  * the EXIF `Software` tag written into every derived JPEG (scanner.py)
  * a banner at the top of style.css and app.js, which an operator serves
    verbatim
  * the CLI masthead and the console's own chrome (those are the
    vendor's tools, not the operator's site)

None of it is enforcement: anyone holding the source can delete a line. The
licence is what asks for attribution — these are the defaults that make
giving it the path of least resistance.

What none of them do is touch a photograph. The EXIF Artist/Copyright of a
derived image carry the operator's `credit`, never the name above, because
resizing an image is not authorship; and nothing is ever drawn onto a
picture. Attribution belongs in the chrome, the headers and the metadata,
which is where all eight of these live.
"""

NAME = "lucya.systems"
URL = "https://lucya.systems"
PRODUCT = f"{NAME} aperture"

# Aperture's own release version — THE one place it is written. The nav, the
# footer, /humans.txt, X-Powered-By, <meta name="generator">, the EXIF
# Software tag and the CLI masthead all derive from this line, so a release is
# one edit and cannot half-happen.
#
# MAJOR.MINOR.PATCH, read as a promise to whoever runs it: MAJOR = the program
# is a different shape (read the changelog first), MINOR = something new to
# see or to set (drop-in unless the entry names a step), PATCH = fixes and
# internals. Bump it in the same commit as the change that earns it, and write
# that commit's entry in CHANGELOG.md; the rules are in README → Versions and
# tests/test_version.py holds the two to each other.
#
# It started at 1.0 rather than continuing the gallery's 7.2: the gallery and
# the configurator were two programs, this is one, and a version is a promise
# about a thing. Distinct from API_VERSION (gallery/api.py), which versions the
# JSON API contract and did NOT reset — the endpoints kept their shape.
VERSION = "1.1.0"

# One-line form, shared by <meta name="generator">, the X-Powered-By header
# and the EXIF `Software` tag so all three can never drift apart.
GENERATOR = f"{PRODUCT} {VERSION}"
# outbound requests identify themselves as the product, not as a browser
USER_AGENT = "lucya.systems-aperture"

# what templates get as `vendor`
CONTEXT = {
    "name": NAME,
    "url": URL,
    "product": PRODUCT,
    "version": VERSION,
    "generator": GENERATOR,
}
