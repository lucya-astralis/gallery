"""The console's own face: the mark, the boot screen, and the version stamp.

None of this is about photographs. It is about the three things that broke
when the logo stopped being an <img> and started being markup the stylesheet
and a script can reach into.
"""

import re

from aperture import brand


def test_the_mark_is_markup_now_and_not_a_picture(console):
    """An <img> is a closed document: nothing outside it can reach a path, so
    it can be neither drawn in nor split into colour. The nav, the footer and
    the boot screen all carry the wing as inline SVG."""
    page = console.get("/").text
    assert page.count('class="mark ') == 3          # nav, footer, boot
    assert 'mark--nav' in page and 'mark--foot' in page and 'mark--splash' in page
    # the eight pieces, in each of the three, each addressable by name
    assert page.count('data-piece="tail"') >= 3
    # and the logo file is still the favicon -- a 16px tab icon wants the flat
    # file, not eight paths
    assert 'logo/lucya_logo.svg' in page


def test_the_boot_screen_is_hidden_until_a_script_shows_it(console):
    """The no-JS story, and the once-per-session one. Both depend on the same
    two facts: the div ships `hidden`, and the sheet says what `hidden` means
    for it -- a class that sets `display` otherwise beats the browser's own
    rule, and the screen would then sit on top of the console on every load
    after the first, with the script returning early and nothing left to take
    it away."""
    page = console.get("/").text
    assert re.search(r'<div class="boot" id="boot" hidden>', page)

    sheet = console.get("/static/style.css").text
    assert ".boot[hidden] { display: none; }" in sheet
    # every other overlay in this sheet earns the same line
    for cls in (".modal", ".toast", ".scrim"):
        assert cls + "[hidden] { display: none; }" in sheet


def test_nothing_of_the_tool_shows_behind_the_boot_screen(console):
    """The boot screen does not cover the console, it UNDRESSES it: the class
    the script sets takes the three chrome blocks to zero, so the backdrop
    stays the room the console already lives in. The script tag has to be at
    the top of the body for that to land before they are parsed."""
    page = console.get("/").text
    assert page.index("splash.js") < page.index('class="nav"')
    assert page.index('id="boot"') < page.index('class="nav"')

    sheet = console.get("/static/style.css").text
    assert "html.is-booting .nav" in sheet
    assert "html.is-booting .layout" in sheet
    assert "html.is-booting .foot" in sheet

    script = console.get("/static/splash.js").text
    assert "classList.add('is-booting')" in script
    assert "classList.remove('is-booting')" in script


def test_the_boot_screen_never_leaves_the_console_invisible(console):
    """Everything that dims the tool has to have something that un-dims it,
    on every path: the sequence finishing, a click, a key, a backend that
    never answers, and the sequence throwing."""
    script = console.get("/static/splash.js").text
    assert "reduced-motion" in script or "prefers-reduced-motion" in script
    assert "setTimeout(dismiss, 12000)" in script       # the last resort
    assert ".catch(() => dismiss())" in script          # a sequence that threw
    assert "boot.addEventListener('click', dismiss)" in script
    assert "addEventListener('keydown', dismiss" in script


def test_the_console_says_when_it_is_actually_up(console):
    """The boot screen waits on this rather than on a clock: it covers the
    four "Loading..." strings, so it has to know when there is something
    behind it."""
    app = console.get("/static/app.js").text
    assert "aperture:ready" in app
    assert "document.addEventListener('aperture:ready'" in \
        console.get("/static/splash.js").text


def test_the_door_stamps_the_version(console):
    """Behind a password the footer is not reachable, and the version is the
    first thing to check when the tool behaves unlike the notes for it."""
    from aperture.console import security
    security.set_password("a-test-password")
    try:
        page = console.get("/login").text
        assert brand.VERSION in page
        # in the lockup, not loose on the page
        assert re.search(r'brand-sub">console <b>' + re.escape(brand.VERSION),
                         page)
    finally:
        security.clear_password()
        security.reset()


def test_the_door_keeps_its_wordmark_on_a_phone(console):
    """The rule that drops the wordmark is about the APP BAR -- two lines of
    brand text plus a menu handle plus two buttons across 375px. The door has
    a whole card to itself and was losing its lockup for a reason that never
    applied to it."""
    sheet = console.get("/static/style.css").text
    assert ".nav__brand .brand-text { display: none; }" in sheet
    # the unscoped form is what took the door's wordmark with it
    assert re.search(r"^\s*\.brand-text \{ display: none; \}", sheet, re.M) is None


# ----- the door ---------------------------------------------------------
def door(console):
    """The sign-in page, which only exists when a password does."""
    from aperture.console import security
    security.set_password("a-test-password")
    try:
        return console.get("/login").text
    finally:
        security.clear_password()
        security.reset()


def test_the_door_says_why_you_are_standing_there(console):
    """A session that idles out drops you at the door with no explanation,
    which reads as the tool having thrown you out for no reason."""
    from aperture.console import security
    from aperture.console.app import LOGIN_REASONS
    security.set_password("a-test-password")
    try:
        page = console.get("/login?reason=timeout").text
        assert LOGIN_REASONS["timeout"] in page
        assert str(security.IDLE_TIMEOUT // 60) in LOGIN_REASONS["timeout"]
        assert 'class="login__why"' in page

        page = console.get("/login?reason=signout").text
        assert LOGIN_REASONS["signout"] in page
    finally:
        security.clear_password()
        security.reset()


def test_the_reason_is_chosen_and_never_echoed(console):
    """It arrives on a query string, on an UNAUTHENTICATED page. The parameter
    picks a key out of an allowlist; nothing from it is ever rendered."""
    from aperture.console import security
    security.set_password("a-test-password")
    try:
        page = console.get("/login?reason=<script>alert(1)</script>").text
        assert "alert(1)" not in page
        assert "login__why" not in page          # an unknown key says nothing
    finally:
        security.clear_password()
        security.reset()


def test_the_door_has_no_redirect_parameter_left(console):
    """`next` could only ever carry "/" — the console has no router, no
    pushState, no hash — so it was an open-redirect sink standing open for no
    benefit. What is genuinely lost across the door is the SCREEN you were
    on, and that is remembered on the console's side."""
    import inspect
    from aperture.console import app as console_app
    assert "next" not in inspect.signature(console_app.login_page).parameters

    js = console.get("/static/app.js").text
    assert "cfgtool.return" in js
    assert "takeReturnNote() || { kind: 'home' }" in js
    # a deliberate sign-out is a decision to stop, and leaves no note
    assert "if (reason === 'timeout' && state.sel)" in js
    assert "toLogin('signout')" in js


def test_the_door_helps_a_password_manager_file_it(console):
    """One account with no name is the shape a manager cannot file: with
    nothing carrying autocomplete="username" most will not offer to save."""
    page = door(console)
    assert 'autocomplete="username"' in page
    assert 'autocomplete="current-password"' in page
    # offscreen, NOT display:none — that is what makes managers ignore it
    assert 'class="u-offscreen"' in page
    sheet = console.get("/static/style.css").text
    assert ".u-offscreen" in sheet and "clip-path: inset(50%)" in sheet


def test_the_door_has_a_reveal_and_a_caps_lock_hint(console):
    page = door(console)
    # type="button", or it submits the form
    assert '<button type="button" class="login__reveal"' in page
    assert 'aria-pressed="false"' in page
    assert 'id="login-caps"' in page and 'role="status"' in page

    js = console.get("/static/login.js").text
    assert "getModifierState('CapsLock')" in js
    assert "setSelectionRange" in js          # the caret survives a reveal


def test_the_door_counts_the_lockout_down(console):
    """Three tries are free, then the server backs off and answers 429 with a
    Retry-After. The door used to print that number once and then stand there
    lying about it with the button still inviting another go."""
    js = console.get("/static/login.js").text
    assert "res.status === 429" in js
    assert "Retry-After" in js
    assert "submit.disabled = true" in js
    assert "if (ticking) return;" in js       # shut is shut


def test_the_door_hands_over_to_the_boot_screen(console):
    """Signing in is a full page load. The card leaves under its own power
    first, so the hand-off is a fade and not a document swap's white flash."""
    js = console.get("/static/login.js").text
    assert "card.classList.add('is-out')" in js
    sheet = console.get("/static/style.css").text
    assert ".login__card.is-out" in sheet


def test_the_door_wears_the_mark_but_does_not_build_it(console):
    """The card gets the header's behaviour, not the boot screen's: solid,
    splitting into colour on hover. The full build belongs to the screen that
    covers a real wait — this card covers nothing, and the field under it is
    meant to be typed into on the first frame."""
    from aperture.console import security
    security.set_password("a-test-password")
    try:
        page = console.get("/login").text
    finally:
        security.clear_password()
        security.reset()
    assert "mark--door" in page
    # the two ghost copies, which are what the hover splits apart
    assert page.count("mark__ghost") >= 2

    js = console.get("/static/login.js").text
    assert "is-armed" not in js          # no build on the door
    assert "getTotalLength" not in js

    sheet = console.get("/static/style.css").text
    assert ".mark--door:hover .mark__ghost--r" in sheet
    # and the build states stay the splash's own, scoped off the ghosts
    assert ".mark--splash.is-armed > .mark__svg .piece {" in sheet
