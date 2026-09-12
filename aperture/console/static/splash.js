/* lucya.systems aperture — the console's boot screen.
 *
 * The mark assembles itself once while the console fetches what it needs.
 * The sequence is the one from the site's own splash, beat for beat and at
 * the same timings:
 *
 *   phase 1  the outline draws in symmetric PAIRS, lands       ~1.43s
 *   phase 2  CRT signal drop, then the pieces fill solid
 *   phase 3  the wordmark glitches in, then the baseline RGB split and
 *            the idle glitch
 *
 * Three things keep it out of the way:
 *   - ONCE per browser session. A reload in the middle of a job is not a
 *     three-second wall.
 *   - any click, any key, dismisses it immediately.
 *   - never shown at all under a reduced-motion request.
 *
 * WHY THIS FILE RUNS FROM THE TOP OF THE BODY, not the bottom with app.js:
 * the first thing it does is put `is-booting` on <html>, which takes the
 * nav, the layout and the footer to zero. Loaded at the top it lands before
 * any of those three have been parsed, so there is no frame in which the
 * console is on screen behind the mark. Nothing of the tool is ever visible
 * under the boot screen; the backdrop is the room's own artwork and stays
 * exactly where it was.
 *
 * It also waits for the app: the screen behind it is four "Loading..."
 * strings until /api/meta, /api/tree and /api/vocab answer, so this covers
 * a real wait rather than adding one. app.js fires `aperture:ready` when it
 * is done, and the hand-off is the later of that and the end of the build —
 * the mark fades out as the tool fades in, one cross-fade, no curtain
 * lifting off a lit stage.
 */
(function () {
  const boot = document.getElementById('boot');
  if (!boot) return;

  const mark = boot.querySelector('.mark--splash');
  const svg = mark && mark.querySelector('.mark__svg');
  if (!mark || !svg) return;

  const label = document.getElementById('boot-label');
  const name = document.getElementById('boot-name');
  const sub = boot.querySelector('.boot__sub');
  const ghostR = boot.querySelector('.mark__ghost--r');
  const ghostB = boot.querySelector('.mark__ghost--b');

  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* sessionStorage can throw outright — a private window, a browser set to
   * block site data. A boot screen is not worth an exception, and playing it
   * one time too many is the harmless side of that coin. */
  const SEEN = 'aperture.boot';
  function seen() {
    try { return sessionStorage.getItem(SEEN) === '1'; } catch (_) { return false; }
  }
  function remember() {
    try { sessionStorage.setItem(SEEN, '1'); } catch (_) { /* fine */ }
  }

  if (REDUCED || seen()) return;      // stays hidden, and the console is not dimmed
  remember();

  /* ----- the beats -------------------------------------------------------
   * The mark is mirror-symmetric, so it builds in PAIRS rather than one
   * piece at a time — drawing a left feather and then its right twin a beat
   * later reads as a stutter, not as assembly. Durations come from each
   * path's own length so the stroke travels at roughly one speed everywhere
   * (~1250-1450 user units/sec); the tail is the deliberate exception,
   * slower, because it is the opening beat with nothing else on screen to
   * race. */
  const order = [
    { pieces: ['tail'], dur: 0.66 },                    // 579u  -> ~880 u/s
    { pieces: ['head'], dur: 0.46 },                    // 574u  -> ~1250
    { pieces: ['wing-l', 'wing-r'], dur: 0.86 },        // 1245u -> ~1450
    { pieces: ['mid-l', 'mid-r'], dur: 0.46 },          // 622u  -> ~1350
    { pieces: ['small-l', 'small-r'], dur: 0.36 },      // 481u  -> ~1340
  ];
  const piecesOf = (group) => group.pieces
    .map((id) => svg.querySelector('[data-piece="' + id + '"]'))
    .filter(Boolean);

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  /* Each stroke is measured, not guessed: --len is the path's own length, so
   * one dash covers it exactly whatever the geometry. */
  function measure(el, seconds) {
    try {
      el.style.setProperty('--len', el.getTotalLength());
      el.style.setProperty('--dur', seconds + 's');
    } catch (_) { /* a browser that cannot measure it just shows it */ }
  }

  const split = (dx) => {
    if (ghostR) { ghostR.style.opacity = '0.4'; ghostR.style.translate = (-dx) + 'px ' + (dx * 0.3) + 'px'; }
    if (ghostB) { ghostB.style.opacity = '0.4'; ghostB.style.translate = dx + 'px ' + (-dx * 0.3) + 'px'; }
  };
  const unsplit = () => {
    if (ghostR) { ghostR.style.opacity = '0'; ghostR.style.translate = ''; }
    if (ghostB) { ghostB.style.opacity = '0'; ghostB.style.translate = ''; }
  };
  /* What it settles on and keeps: a two-unit fringe, permanently. */
  const baseline = () => {
    if (ghostR) { ghostR.style.opacity = '0.55'; ghostR.style.translate = '-2px 0.5px'; }
    if (ghostB) { ghostB.style.opacity = '0.55'; ghostB.style.translate = '2px -0.5px'; }
  };

  /* `translate`, not `transform`: the house rule is that a scripted move
   * uses the independent property, so it can never overwrite a `transform`
   * something else put on the same element. The two below that DO write
   * `transform` need skew, which has no property of its own. */
  const shake = (el, count, intensity) => new Promise((res) => {
    let i = 0;
    const iv = setInterval(() => {
      el.style.translate = ((Math.random() - .5) * intensity) + 'px '
        + ((Math.random() - .5) * intensity * .6) + 'px';
      if (++i >= count) { clearInterval(iv); el.style.translate = ''; res(); }
    }, 35);
  });
  const burst = (el, rounds, intensity) => new Promise((res) => {
    let i = 0;
    const iv = setInterval(() => {
      el.style.transform = 'translate(' + ((Math.random() - .5) * intensity) + 'px, '
        + ((Math.random() - .5) * intensity * .5) + 'px) skewX('
        + ((Math.random() - .5) * 3) + 'deg)';
      split(3 + Math.random() * 5);
      if (++i >= rounds) { clearInterval(iv); el.style.transform = ''; unsplit(); res(); }
    }, 45);
  });

  /* CRT signal drop: the tube blooms, cuts to black, comes back weak, then
   * settles. Used once, as the hinge between the outline and the fill — the
   * mark does not just start filling, the signal drops and it comes back
   * solid. */
  const signalLoss = (el, duration) => new Promise((res) => {
    el.style.filter = 'brightness(2.4) contrast(.55)';
    el.style.transform = 'skewX(' + ((Math.random() - .5) * 4) + 'deg)';
    split(8 + Math.random() * 6);
    setTimeout(() => {
      el.style.filter = 'brightness(0)';
      el.style.transform = '';
      unsplit();
    }, duration * 0.3);
    setTimeout(() => { el.style.filter = 'brightness(.6) contrast(1.2)'; }, duration * 0.6);
    setTimeout(() => {
      el.style.filter = '';
      el.style.transform = '';
      res();
    }, duration);
  });

  /* hard on/off flicker — the last unstable moment before the wordmark */
  const flicker = (el, times) => new Promise((res) => {
    let i = 0;
    const iv = setInterval(() => {
      el.style.opacity = i % 2 === 0 ? '0' : '1';
      if (++i >= times) { clearInterval(iv); el.style.opacity = ''; res(); }
    }, 42 + Math.random() * 26);
  });

  /* The wordmark resolves instead of fading: it lands wide and overshoots its
   * tracking a few times before settling on the stylesheet's value. */
  const resolveName = async (el, aborted) => {
    if (!el) return;
    const frames = [
      { ls: '1.05em', x: 9, skew: 3 },
      { ls: '.10em', x: -6, skew: -2.4 },
      { ls: '.46em', x: 4, skew: 1.6 },
      { ls: '.15em', x: -2, skew: -0.8 },
      { ls: '.22em', x: 0, skew: 0 },
    ];
    for (let i = 0; i < frames.length; i++) {
      if (aborted()) return;
      const f = frames[i];
      el.style.letterSpacing = f.ls;
      el.style.transform = 'translateX(' + f.x + 'px) skewX(' + f.skew + 'deg)';
      if (i < frames.length - 1) split(2 + Math.abs(f.x) * 0.5); else unsplit();
      await wait(100 + Math.random() * 45);
    }
    /* hand tracking back to the stylesheet, so the phone tier still wins */
    el.style.transform = 'none';
    el.style.letterSpacing = '';
  };

  /* ----- the hand-off ----------------------------------------------------
   * Two fades on the same clock: the boot screen out, the console in. The
   * class comes off FIRST so the tool is already fading up underneath while
   * the mark fades down over it, which is what makes it read as one move
   * rather than as a curtain. */
  const FADE = 450;
  let idle = null;
  let gone = false;
  let ready = false;
  let built = false;

  function reveal() {
    document.documentElement.classList.remove('is-booting');
  }
  function dismiss() {
    if (gone) return;
    gone = true;
    clearInterval(idle);
    reveal();
    boot.classList.add('is-done');
    /* hidden only after the fade, or the fade has nothing to fade */
    setTimeout(() => { boot.hidden = true; }, FADE + 50);
  }
  /* The later of the two, so the screen neither cuts off its own build nor
   * hangs on a backend that is not answering. */
  function maybeDismiss() { if (ready && built) dismiss(); }

  document.addEventListener('aperture:ready', () => { ready = true; maybeDismiss(); });
  /* A backend that never answers must not leave the operator looking at a
   * logo: the console has its own way of saying it cannot reach itself. And
   * whatever else happens below, the tool is never left invisible. */
  setTimeout(() => { ready = true; maybeDismiss(); }, 8000);
  setTimeout(dismiss, 12000);

  boot.addEventListener('click', dismiss);
  document.addEventListener('keydown', dismiss, { once: true });

  /* ----- the sequence --------------------------------------------------- */
  async function play() {
    const aborted = () => gone;

    /* Armed, then shown: .is-armed is what hollows the wing out, and it has
     * to be on the element before the screen is visible or the first frame
     * is the finished logo. */
    mark.classList.add('is-armed');
    order.forEach((g) => piecesOf(g).forEach((el) => measure(el, g.dur)));
    boot.hidden = false;

    /* --- phase 1: draw the outline --- */
    await wait(200);
    for (let i = 0; i < order.length; i++) {
      if (aborted()) return;
      piecesOf(order[i]).forEach((el) => el.classList.add('drawn'));
      if (i === 1 || i === 3) await shake(svg, 3, 3);
      await wait(130);
    }
    /* The loop only STARTS each stroke and hands off to a CSS transition, so
     * it returns (~1.06s) before the wings land (~1.43s). The dropout has to
     * fall on a finished outline to read as the hinge and not an
     * interruption. */
    await wait(380);
    if (aborted()) return;

    /* --- phase 2: signal drops, the mark comes back solid --- */
    await signalLoss(mark, 260);
    if (aborted()) return;
    await burst(svg, 6, 15);
    for (let i = 0; i < order.length; i++) {
      if (aborted()) return;
      piecesOf(order[i]).forEach((el) => el.classList.add('filled'));
      if (i % 2 === 0) { split(2 + Math.random() * 3); await wait(22); unsplit(); }
      await wait(36);
    }
    await burst(svg, 3, 12);
    if (aborted()) return;

    /* --- phase 3: the wordmark locks in --- */
    await flicker(mark, 3);
    if (aborted()) return;
    if (label) label.style.opacity = '1';
    await resolveName(name, aborted);
    if (aborted()) return;
    if (sub) sub.style.opacity = '1';

    baseline();
    mark.classList.add('is-built');
    built = true;
    maybeDismiss();

    /* The idle glitch. It exists for the case the boot screen is still on
     * screen — a backend taking its time — because a frozen splash reads as
     * a hang. It stops the moment the screen leaves. */
    idle = setInterval(async () => {
      if (aborted()) { clearInterval(idle); return; }
      const r = Math.random();
      if (r > 0.82) {
        const rounds = 3 + Math.floor(Math.random() * 4);
        for (let i = 0; i < rounds; i++) {
          if (aborted()) return;
          svg.style.translate = ((Math.random() - .5) * 10) + 'px 0';
          svg.style.clipPath = 'inset(' + Math.floor(Math.random() * 70) + '% 0 '
            + Math.floor(Math.random() * 70) + '% 0)';
          svg.style.filter = 'hue-rotate(' + ((Math.random() - .5) * 70) + 'deg)';
          split(5 + Math.random() * 7);
          await wait(45);
        }
        svg.style.translate = ''; svg.style.clipPath = ''; svg.style.filter = '';
        baseline();
      } else if (r > 0.5) {
        split(4 + Math.random() * 4);
        svg.style.translate = ((Math.random() - .5) * 2) + 'px 0';
        setTimeout(() => { if (aborted()) return; svg.style.translate = ''; baseline(); },
          80 + Math.random() * 90);
      } else {
        svg.style.opacity = '0.7';
        setTimeout(() => { if (aborted()) return; svg.style.opacity = ''; }, 40);
      }
    }, 520);
  }

  /* The console goes dark BEFORE anything else happens, and before the rest
   * of the body has been parsed — see the note at the top. */
  document.documentElement.classList.add('is-booting');

  /* Fonts first: the wordmark's tracking overshoot is measured against the
   * display face, and a mid-sequence swap would jump it. */
  (document.fonts ? document.fonts.ready.catch(() => {}) : Promise.resolve())
    .then(play)
    .catch(() => dismiss());        // never leave the tool hidden
})();
