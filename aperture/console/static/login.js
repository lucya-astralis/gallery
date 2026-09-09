/* lucya.systems aperture — the console's door.
 *
 * A real <form> with a real submit button, progressively enhanced: without
 * this file the browser still posts it, and the API answers JSON rather than
 * a redirect, which is a worse experience but not a broken one.
 *
 * The one thing it does that the form cannot: post JSON, keep the page, and
 * put the server's reason next to the field. A wrong password should not cost
 * a navigation.
 */
(function () {
  const form = document.getElementById('login-form');
  const field = document.getElementById('password');
  const error = document.getElementById('login-error');
  const submit = document.getElementById('login-submit');
  if (!form || !field) return;

  function fail(message) {
    error.textContent = message;
    error.hidden = false;
    field.select();
    field.focus();
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
    error.hidden = true;
    submit.disabled = true;
    try {
      const res = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: field.value }),
      });
      if (res.ok) {
        /* replace(), not assign(): the login page has no business in the
         * history stack that Back walks through. */
        window.location.replace('/');
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
      submit.disabled = false;
    }
  });
})();
