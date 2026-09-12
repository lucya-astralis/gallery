/* lucya.systems aperture — the console's door.
 *
 * A real <form> with a real submit button, progressively enhanced: without
 * this file the browser still posts it, and the API answers JSON rather than
 * a redirect, which is a worse experience but not a broken one. Everything
 * below is an improvement on a page that already works.
 *
 * The one thing it does that the form cannot: post JSON, keep the page, and
 * put the server's reason next to the field. A wrong password should not cost
 * a navigation.
 */
(function () {
  const form = document.getElementById('login-form');
  const field = document.getElementById('password');
  const error = document.getElementById('login-error');
  const caps = document.getElementById('login-caps');
  const reveal = document.getElementById('login-reveal');
  const submit = document.getElementById('login-submit');
  const card = document.querySelector('.login__card');
  if (!form || !field) return;

  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  function fail(message) {
    error.textContent = message;
    error.hidden = false;
    field.select();
    field.focus();
  }

  /* ----- Caps Lock -------------------------------------------------------
   * The commonest reason a password that is definitely right is definitely
   * wrong. getModifierState is answered on both key events, which is what
   * makes the hint appear on the keystroke that turns Caps Lock ON rather
   * than on the next letter. It is also read on focus, for the case where it
   * was already on before this page existed — there is no event for that, so
   * a keystroke is the earliest anything can know. */
  function checkCaps(event) {
    if (!caps || typeof event.getModifierState !== 'function') return;
    caps.hidden = !event.getModifierState('CapsLock');
  }
  field.addEventListener('keydown', checkCaps);
  field.addEventListener('keyup', checkCaps);
  field.addEventListener('blur', () => { if (caps) caps.hidden = true; });

  /* ----- the reveal ------------------------------------------------------
   * Focus is put back on the field with the caret where it was: a reveal
   * that dumps you at the start of what you typed is worse than no reveal. */
  if (reveal) {
    reveal.addEventListener('click', () => {
      const shown = field.type === 'text';
      const at = field.selectionStart;
      field.type = shown ? 'password' : 'text';
      reveal.textContent = shown ? 'Show' : 'Hide';
      reveal.setAttribute('aria-pressed', String(!shown));
      field.focus();
      try { field.setSelectionRange(at, at); } catch (_) { /* not all types allow it */ }
    });
  }

  /* ----- the lockout -----------------------------------------------------
   * Three tries are free, then the server backs off exponentially to five
   * minutes (console/security.py) and answers 429 with a Retry-After. Without
   * this the door printed that number once and then stood there lying about
   * it, with the button still inviting another go. A count that runs down is
   * the difference between "locked out" and "broken". */
  let ticking = null;

  function lockFor(seconds) {
    clearInterval(ticking);
    let left = Math.max(1, Math.ceil(seconds));
    submit.disabled = true;
    field.disabled = true;
    const paint = () => {
      submit.textContent = 'Locked — ' + left + 's';
      error.textContent = 'Too many attempts. The door is shut for a moment.';
      error.hidden = false;
    };
    paint();
    ticking = setInterval(() => {
      left -= 1;
      if (left > 0) { paint(); return; }
      clearInterval(ticking);
      ticking = null;
      submit.disabled = false;
      field.disabled = false;
      submit.textContent = 'Sign in';
      error.hidden = true;
      field.focus();
    }, 1000);
  }

  /* Implicit submission — Enter in the only field of a form — is the
   * browser's job, and it is the browser's job unevenly: it does not fire
   * under some automation and some embedded webviews. On a sign-in form that
   * failure mode is a password typed and nothing happening, so the key is
   * handled outright rather than relied upon. */
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(
        new Event('submit', { cancelable: true }));
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (ticking) return;                 // shut is shut
    error.hidden = true;
    submit.disabled = true;
    try {
      const res = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: field.value }),
      });
      if (res.ok) {
        /* The card leaves under its own power before the navigation, so the
         * hand-off to the boot screen is a fade and not the white flash of a
         * document swap. replace(), not assign(): the login page has no
         * business in the history stack that Back walks through. */
        if (card && !REDUCED) {
          card.classList.add('is-out');
          setTimeout(() => window.location.replace('/'), 300);
        } else {
          window.location.replace('/');
        }
        return;
      }
      if (res.status === 429) {
        /* Same origin, so the header is readable without any CORS exposure.
         * The body's own "try again in N s" is the fallback for a proxy that
         * has eaten the header. */
        const after = parseInt(res.headers.get('Retry-After') || '', 10);
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch (_) { /* empty */ }
        const fromBody = /(\d+)\s*s/.exec(detail);
        lockFor(Number.isFinite(after) ? after
          : fromBody ? parseInt(fromBody[1], 10) : 30);
        return;
      }
      let detail = res.statusText;
      try {
        const payload = await res.json();
        if (payload && typeof payload.detail === 'string') detail = payload.detail;
      } catch (_) { /* an empty body is still an error */ }
      fail(detail);
    } catch (_) {
      fail('The console did not answer. Is it still running?');
    } finally {
      if (!ticking) submit.disabled = false;
    }
  });

  /* The mark in the card does NOT play the boot sequence. It is the same
   * drawing the header wears and it behaves the same way: solid, and it
   * splits into its two colour flanks on hover (see THE MARK in style.css).
   * The full build belongs to the boot screen, which covers a real wait;
   * this card covers nothing and the field under it is meant to be typed
   * into on the first frame. */
})();
