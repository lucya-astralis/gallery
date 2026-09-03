"""Who built this gallery — as opposed to who runs it.

The two used to be one string. Every page said "lucya.systems gallery",
which was simultaneously the name of the archive's operator and the name of
the software serving it. Those are different facts about a deployment and
only one of them belongs to the person running it, so they were split:

  operator branding   gallery.cfg (`site_name`, `logo`, `operator_url`, …)
                      resolved in main._brand(); assets live in
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
  * the CLI masthead (that is the vendor's tool, not the operator's site)

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
PRODUCT = f"{NAME} gallery"

# The gallery's own release version, shown in the nav and the footer.
# Distinct from API_VERSION (main.py), which versions the JSON API contract.
VERSION = "6.0"

# One-line form, shared by <meta name="generator">, the X-Powered-By header
# and the EXIF `Software` tag so all three can never drift apart.
GENERATOR = f"{PRODUCT} {VERSION}"
# outbound requests identify themselves as the product, not as a browser
USER_AGENT = "lucya.systems-gallery"

# what templates get as `vendor`
CONTEXT = {
    "name": NAME,
    "url": URL,
    "product": PRODUCT,
    "version": VERSION,
    "generator": GENERATOR,
}
