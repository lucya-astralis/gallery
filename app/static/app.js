// ---------- UI LANGUAGE ----------------------------------------
// The server renders <html lang="en|de|ja"> from the language cookie
// (selector in the nav); client-side strings pick their translation here.
// Keep the wording in sync with app/i18n.py. Decorative HUD stamps
// (PREVIEW/ORIGINAL, IN TRANSIT, T-x DAYS, …) intentionally stay English.
// NB: new Japanese text may need a font-subset rebuild — see
// tools/build_jp_subset.py.
const UI_LANG = (document.documentElement.lang || 'en').toLowerCase().slice(0, 2);
const UI_STRINGS = {
  en: {
    loadOriginal: 'Load original',
    loading: 'Loading…',
    errRetry: 'Error — retry?',
    departsIn: 'Departs in',
    arrivingIn: (city) => 'Arriving in ' + city,
    leavingIn: (city) => 'Leaving ' + city + ' in',
    tripComplete: 'Trip complete',
    soon: 'soon',
    inDays: (d) => 'in ' + d + ' days',
    dayOf: (n, total) => 'Day ' + n + ' / ' + total,
    months: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    fmtDate: (d, M) => `${d.getDate()} ${M[d.getMonth()]} ${d.getFullYear()}`,
  },
  de: {
    loadOriginal: 'Original laden',
    loading: 'Lädt…',
    errRetry: 'Fehler — erneut?',
    departsIn: 'Abflug in',
    arrivingIn: (city) => 'Ankunft in ' + city,
    leavingIn: (city) => 'Abreise aus ' + city + ' in',
    tripComplete: 'Reise beendet',
    soon: 'bald',
    inDays: (d) => 'in ' + d + ' Tagen',
    dayOf: (n, total) => 'Tag ' + n + ' / ' + total,
    months: ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'],
    fmtDate: (d, M) => `${d.getDate()}. ${M[d.getMonth()]} ${d.getFullYear()}`,
  },
  ja: {
    loadOriginal: 'オリジナルを読み込む',
    loading: '読み込み中…',
    errRetry: 'エラー — 再試行？',
    departsIn: '出発まで',
    arrivingIn: (city) => city + 'に到着まで',
    leavingIn: (city) => city + 'を出発まで',
    tripComplete: '旅は終了しました',
    soon: 'まもなく',
    inDays: (d) => 'あと' + d + '日',
    dayOf: (n, total) => n + '日目 / ' + total + '日',
    months: null, // fmtDate below doesn't use month names
    fmtDate: (d) => `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`,
  },
};
const TXT = UI_STRINGS[UI_LANG] || UI_STRINGS.en;

// ---------- LANGUAGE SELECTOR ----------------------------------
// The server bakes ?next=<URL at render time> into the selector links, but
// the image page navigates between photos via pushState (SPA swaps) without
// re-rendering the nav — the baked next then points at the entry image.
// Rewrite it at interaction time so the /lang round-trip returns to the
// page actually on screen. pointerdown also covers middle-click/new-tab.
(function langSelector() {
  const sync = (a) => {
    try {
      const u = new URL(a.href, location.href);
      u.searchParams.set('next', location.pathname + location.search);
      a.href = u.toString();
    } catch (e) {}
  };
  document.querySelectorAll('.nav__lang-opt').forEach((a) => {
    a.addEventListener('pointerdown', () => sync(a));
    a.addEventListener('click', () => sync(a));
  });

  // A page restored from the back/forward cache may predate a language
  // switch and would show the old language (Safari bfcaches even with
  // Cache-Control: no-store). Compare the cookie against <html lang> and
  // reload only on a real mismatch, so bfcache stays fast otherwise.
  window.addEventListener('pageshow', (e) => {
    if (!e.persisted) return;
    const m = document.cookie.match(/(?:^|;\s*)lang=(en|de|jp)\b/);
    if (!m) return;
    const want = m[1] === 'jp' ? 'ja' : m[1];
    if (document.documentElement.lang !== want) location.reload();
  });
})();

// ---------- DEVICE CAPABILITY ----------------------------------
// Single gate for the heavy, decorative effects (background video, animated
// scanlines, text scramble, CRT auto-cycle). Small screens, data-saver,
// reduced-motion and low-end hardware all opt out and get the lightweight
// static experience instead.
const __mqReduceData = window.matchMedia('(prefers-reduced-data: reduce)');
function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
function isLowEndDevice() {
  try {
    const c = navigator.connection || {};
    if (c.saveData) return true;
    if (c.effectiveType && /(slow-)?2g/.test(c.effectiveType)) return true;
    if (typeof navigator.deviceMemory === 'number' && navigator.deviceMemory <= 4) return true;
    if (typeof navigator.hardwareConcurrency === 'number' && navigator.hardwareConcurrency <= 2) return true;
  } catch (e) {}
  return false;
}
function allowHeavyFx() {
  return !prefersReducedMotion() && !__mqReduceData.matches && !isLowEndDevice();
}
window.__allowHeavyFx = allowHeavyFx;
// Bridge capability detection to CSS: html.fx-lite kills the continuous
// full-screen scanline animation and other ambient motion on weak devices.
if (!allowHeavyFx()) document.documentElement.classList.add('fx-lite');
// Motion-layer gate (scroll reveals, thumbnail fades, hero build-up in
// style.css): only capable devices with IntersectionObserver opt in —
// everyone else keeps the fully static page. Runs synchronously before
// first paint, so gated content never flashes.
if (allowHeavyFx() && 'IntersectionObserver' in window) {
  document.documentElement.classList.add('fx-anim');
}

// ---------- BACKGROUND VIDEO (opt-in) --------------------------
// The <video> ships with no src and preload="none", so by default nothing is
// fetched. We only wire up the 6 MB clip on capable, desktop-sized screens.
(function bgVideo() {
  const v = document.querySelector('[data-bg-video]');
  if (!v || !v.dataset.src) return;
  const bigScreen = window.matchMedia('(min-width: 761px)').matches;
  if (!bigScreen || !allowHeavyFx()) return; // keep the static gradient backdrop
  v.src = v.dataset.src;
  v.load();
  const p = v.play();
  if (p && typeof p.catch === 'function') p.catch(() => {}); // autoplay blocked → ignore
})();

// ---------- SHARED HELPERS -------------------------------------
function readAlbumData() {
  const el = document.getElementById('album-data');
  if (!el) return null;
  try { return JSON.parse(el.textContent); }
  catch (e) { return null; }
}

// SPA-style navigation between images on the same detail page.
// Fetches the target page, swaps the per-image sections in place and
// keeps the URL in sync via pushState. Returns true on success.
async function spaLoadImage(href) {
  if (!document.querySelector('.detail')) return false;
  const oldStage = document.querySelector('.stage');
  if (oldStage) oldStage.classList.add('is-spa-loading');
  let success = false;
  try {
    const resp = await fetch(href, {
      credentials: 'same-origin',
      headers: { 'Accept': 'text/html' },
    });
    if (!resp.ok) throw new Error('bad status ' + resp.status);
    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const selectors = ['.crumb', '.section__doc', '.detail', '#album-data'];
    let swapped = 0;
    selectors.forEach(sel => {
      const newEl = doc.querySelector(sel);
      const oldEl = document.querySelector(sel);
      if (newEl && oldEl) { oldEl.replaceWith(newEl); swapped++; }
    });
    if (!swapped) throw new Error('nothing swapped');
    document.title = doc.title || document.title;
    try { history.pushState({ spa: true }, '', href); } catch (e) {}
    if (typeof window.__initImagePage === 'function') window.__initImagePage();
    if (typeof window.__lightboxReload === 'function') window.__lightboxReload();
    if (typeof window.__scrambleSwapped === 'function') window.__scrambleSwapped();
    window.scrollTo(0, 0);
    success = true;
  } catch (e) {
    success = false;
  } finally {
    if (!success) {
      // on success the old .stage is replaced so the loading class is gone
      const s = document.querySelector('.stage');
      if (s) s.classList.remove('is-spa-loading');
    }
  }
  return success;
}
window.__spaLoadImage = spaLoadImage;

// ---------- TEXT SCRAMBLE --------------------------------------
// decoder-style transition. Random glyphs flicker, then resolve to the
// target text one char at a time.
const SCRAMBLE_CHARS = '!<>-_\\/[]{}=+*^?#$&%01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿ';
const escScrambleChar = c => c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '&' ? '&amp;' : c;
class TextScrambler {
  constructor(el){ this.el = el; this.queue = []; this.frame = 0; }
  setText(newText){
    const oldText = this.el.textContent;
    const len = Math.max(oldText.length, newText.length);
    this.queue = [];
    for (let i = 0; i < len; i++){
      const start = Math.floor(Math.random() * 14);
      const end = start + 12 + Math.floor(Math.random() * 22);
      this.queue.push({ from: oldText[i] || '', to: newText[i] || '', start, end, char: '' });
    }
    cancelAnimationFrame(this.rafId);
    this.frame = 0;
    return new Promise(res => { this.resolve = res; this._tick(); });
  }
  _tick = () => {
    let out = '';
    let done = 0;
    for (let i = 0; i < this.queue.length; i++){
      const q = this.queue[i];
      if (this.frame >= q.end){ done++; out += escScrambleChar(q.to); }
      else if (this.frame >= q.start){
        if (!q.char || Math.random() < 0.28){
          q.char = SCRAMBLE_CHARS[Math.floor(Math.random() * SCRAMBLE_CHARS.length)];
        }
        out += `<span class="scramble-char">${escScrambleChar(q.char)}</span>`;
      } else {
        out += escScrambleChar(q.from);
      }
    }
    this.el.innerHTML = out;
    if (done === this.queue.length){ this.resolve && this.resolve(); }
    else { this.rafId = requestAnimationFrame(this._tick); this.frame++; }
  };
}
const __scramblers = new WeakMap();
window.__scrambleTo = (el, text) => {
  if (!el) return;
  // low-end / reduced-motion: skip the rAF decoder loop, set text instantly
  if (!allowHeavyFx()) { el.textContent = String(text); return Promise.resolve(); }
  let s = __scramblers.get(el);
  if (!s){ s = new TextScrambler(el); __scramblers.set(el, s); }
  return s.setText(String(text));
};
// re-scramble the changing texts in the image detail view after an SPA swap.
// the elements are fresh DOM nodes (replaceWith), so they already display the
// new target text — we blank them and scramble back in for the decoder effect.
window.__scrambleSwapped = () => {
  if (!window.__scrambleTo) return;
  const els = new Set();
  document.querySelectorAll('.crumb b, .stage__corner, .section__doc b').forEach(el => els.add(el));
  document.querySelectorAll('.section__doc span').forEach(span => {
    if (!span.querySelector('b') && !span.classList.contains('stamp')) els.add(span);
  });
  document.querySelectorAll('.sidebar .kv dd').forEach(dd => {
    const a = dd.querySelector('a');
    if (a && !a.querySelector('*')) els.add(a);
    else if (!dd.querySelector('*')) els.add(dd);
  });
  document.querySelectorAll('.sidebar .tag-list .tag').forEach(t => {
    if (!t.querySelector('*')) els.add(t);
  });
  const desc = document.querySelector('.sidebar .description');
  if (desc && !desc.querySelector('*')) els.add(desc);
  els.forEach(el => {
    const target = el.textContent;
    if (!target.trim()) return;
    el.dataset.scrambleSetup = '1';
    el.dataset.scrambleTarget = target;
    el.textContent = '';
    window.__scrambleTo(el, target);
  });
};

window.__scrambleOnView = (els) => {
  const list = els.filter(el => el && !el.dataset.scrambleSetup);
  list.forEach(el => {
    el.dataset.scrambleSetup = '1';
    if (!el.dataset.scrambleTarget) el.dataset.scrambleTarget = el.textContent.trim();
  });
  if (!('IntersectionObserver' in window)){
    list.forEach(el => window.__scrambleTo(el, el.dataset.scrambleTarget));
    return;
  }
  const io = new IntersectionObserver((ents, obs) => {
    ents.forEach(e => {
      if (e.isIntersecting){
        window.__scrambleTo(e.target, e.target.dataset.scrambleTarget);
        obs.unobserve(e.target);
      }
    });
  }, { threshold: 0.35, rootMargin: '0px 0px -10% 0px' });
  list.forEach(el => io.observe(el));
};

document.addEventListener('DOMContentLoaded', () => {
  // mark shuffle card names with their target text so we can re-scramble later
  document.querySelectorAll('[data-scramble]').forEach(el => {
    if (!el.dataset.scrambleTarget) el.dataset.scrambleTarget = el.textContent.trim();
  });

  // scramble on view: section headers, hero stats, welcome title
  const onViewEls = [
    ...document.querySelectorAll('.section__slug .name'),
    // head + tail separately: scrambling flattens innerHTML, which would
    // destroy the title's dot/tail spans if the whole <h1> were the target
    ...document.querySelectorAll('.vf__title-head, .vf__title-tail'),
    ...document.querySelectorAll('.section__doc b'),
    ...document.querySelectorAll('.notfound__title'),
    ...document.querySelectorAll('.crumb b'),
    ...document.querySelectorAll('.trip__name'),
  ];
  window.__scrambleOnView(onViewEls);

  // ---------- WELCOME VIEWFINDER ---------------------------------
  // Full-bleed "live view" hero: crossfading frames, HUD readouts and a
  // segmented track. Auto-advance is driven by the CSS fill animation on
  // the active segment (animationend), so pause/resume and the visible
  // progress can never drift apart. fx-lite devices get manual controls.
  const vf = document.getElementById('vf');
  if (!vf) return;
  const frames = Array.from(vf.querySelectorAll('.vf__frame'));
  if (frames.length === 0) return;

  const segs = Array.from(vf.querySelectorAll('.vf__seg'));
  const stage = document.getElementById('vf-stage');
  const track = document.getElementById('vf-track');
  const fileEl = document.getElementById('vf-file');
  const idxEl = document.getElementById('vf-idx');
  const metaLink = document.getElementById('vf-meta');
  const openLink = document.querySelector('[data-vf-open]');
  const autoLabel = document.getElementById('vf-auto');

  const AUTO_MS = 6000;
  const FLASH_MS = 450;
  vf.style.setProperty('--vf-auto-ms', AUTO_MS + 'ms');

  let current = 0;
  const autoOk = allowHeavyFx() && frames.length > 1;

  function syncFrame(idx) {
    const f = frames[idx];
    if (fileEl) {
      const text = (f.dataset.album || '') + ' / ' + (f.dataset.filename || '');
      fileEl.dataset.scrambleTarget = text;
      if (window.__scrambleTo) window.__scrambleTo(fileEl, text);
      else fileEl.textContent = text;
    }
    if (idxEl) {
      const n = String(idx + 1).padStart(2, '0');
      if (window.__scrambleTo) window.__scrambleTo(idxEl, n);
      else idxEl.textContent = n;
    }
    if (metaLink) metaLink.href = f.href;
    if (openLink) openLink.href = f.href;
  }

  function paintSegs() {
    segs.forEach((s, i) => {
      s.classList.toggle('is-done', i < current);
      s.classList.remove('is-on');
      if (i === current) {
        void s.offsetWidth; // restart the fill animation from zero
        s.classList.add('is-on');
      }
    });
  }

  function goTo(idx) {
    const target = ((idx % frames.length) + frames.length) % frames.length;
    if (target !== current) {
      frames[current].classList.remove('is-on');
      frames[current].setAttribute('aria-hidden', 'true');
      current = target;
      frames[current].classList.add('is-on');
      frames[current].setAttribute('aria-hidden', 'false');
      vf.classList.add('is-switching'); // AF reticle flash
      setTimeout(() => vf.classList.remove('is-switching'), FLASH_MS);
      syncFrame(current);
    }
    paintSegs();
  }

  const advance = () => goTo(current + 1);
  const regress = () => goTo(current - 1);

  // init: make sure frame 0, counter and links agree
  frames.forEach((f, i) => {
    f.classList.toggle('is-on', i === current);
    f.setAttribute('aria-hidden', i === current ? 'false' : 'true');
  });
  syncFrame(current);

  if (autoOk) {
    vf.classList.add('vf--auto');
    if (track) track.addEventListener('animationend', (e) => {
      if (e.animationName === 'vf-seg-fill') advance();
    });
    // pause while the user inspects the meta block or the deck controls
    [metaLink, vf.querySelector('.vf__deck')].forEach(el => {
      if (!el) return;
      el.addEventListener('mouseenter', () => vf.classList.add('vf--paused'));
      el.addEventListener('mouseleave', () => vf.classList.remove('vf--paused'));
    });
  } else if (autoLabel) {
    autoLabel.textContent = frames.length > 1 ? 'MANUAL' : 'SINGLE FRAME';
  }

  const nextBtn = document.getElementById('vf-next');
  const prevBtn = document.getElementById('vf-prev');
  const tuneBtn = document.getElementById('vf-tune');
  if (nextBtn) nextBtn.addEventListener('click', advance);
  if (prevBtn) prevBtn.addEventListener('click', regress);
  segs.forEach(s => s.addEventListener('click', () => goTo(parseInt(s.dataset.goto, 10) || 0)));

  // swipe on the stage switches frames (and suppresses the anchor tap)
  if (stage) {
    let sx = 0, sy = 0, st = 0, swiping = false;
    stage.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) { swiping = false; return; }
      swiping = true;
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      st = Date.now();
    }, { passive: true });
    stage.addEventListener('touchend', (e) => {
      if (!swiping) return;
      swiping = false;
      const t = e.changedTouches[0];
      const dx = t.clientX - sx;
      const dy = t.clientY - sy;
      if (Date.now() - st > 700) return;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
      e.preventDefault();
      if (dx < 0) advance(); else regress();
    }, { passive: false });
  }

  // TUNE: pull a fresh shuffle from the API into the existing frames
  if (tuneBtn) {
    tuneBtn.addEventListener('click', async () => {
      tuneBtn.disabled = true;
      const prevText = tuneBtn.textContent;
      tuneBtn.textContent = '… TUNING';
      vf.classList.add('is-switching');
      try {
        const resp = await fetch('/api/shuffle?limit=' + frames.length);
        if (!resp.ok) throw new Error('bad status');
        const items = await resp.json();
        items.slice(0, frames.length).forEach((item, i) => {
          const f = frames[i];
          f.href = '/image/' + item.rel_path;
          f.dataset.rel = item.rel_path;
          f.dataset.album = item.album;
          f.dataset.filename = item.filename;
          const img = f.querySelector('img');
          if (img) {
            img.src = '/preview/' + item.rel_path;
            img.alt = item.filename;
          }
        });
        frames.forEach((f, i) => {
          f.classList.toggle('is-on', i === 0);
          f.setAttribute('aria-hidden', i === 0 ? 'false' : 'true');
        });
        current = 0;
        syncFrame(current);
        paintSegs();
      } catch (e) {
        // ignore — keep the current feed
      } finally {
        setTimeout(() => vf.classList.remove('is-switching'), FLASH_MS);
        tuneBtn.disabled = false;
        tuneBtn.textContent = prevText;
      }
    });
  }

  // keyboard: ← → switches frames on the welcome page
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') regress();
    else if (e.key === 'ArrowRight') advance();
  });
});

// ---------- ALBUM FEATURED HERO --------------------------------
// Compact "live view" slideshow of an album's showcased photos.
// Crossfade + segment track mirror the welcome viewfinder; auto-advance
// is CSS-animation driven (animationend on the filling segment, so the
// visible progress and the switch can't drift apart) and gated by
// allowHeavyFx(). fx-lite devices get manual controls only.
document.addEventListener('DOMContentLoaded', () => {
  const hero = document.getElementById('fhero');
  if (!hero) return;
  const slides = Array.from(hero.querySelectorAll('.fhero__slide'));
  if (!slides.length) return;

  const segs = Array.from(hero.querySelectorAll('.fhero__seg'));
  const stage = document.getElementById('fhero-stage');
  const track = document.getElementById('fhero-track');
  const fileLink = document.getElementById('fhero-file');
  const nameEl = document.getElementById('fhero-name');
  const idxEl = document.getElementById('fhero-idx');
  const autoLabel = document.getElementById('fhero-auto');

  const AUTO_MS = 5000;
  hero.style.setProperty('--fhero-auto-ms', AUTO_MS + 'ms');

  let current = 0;
  const autoOk = allowHeavyFx() && slides.length > 1;

  function sync(idx) {
    const s = slides[idx];
    if (nameEl) {
      const text = s.dataset.filename || '';
      nameEl.dataset.scrambleTarget = text;
      if (window.__scrambleTo) window.__scrambleTo(nameEl, text);
      else nameEl.textContent = text;
    }
    if (idxEl) idxEl.textContent = String(idx + 1).padStart(2, '0');
    if (fileLink) fileLink.href = s.href;
  }

  function paintSegs() {
    segs.forEach((s, i) => {
      s.classList.toggle('is-done', i < current);
      s.classList.remove('is-on');
      if (i === current) {
        void s.offsetWidth; // restart the fill animation from zero
        s.classList.add('is-on');
      }
    });
  }

  function goTo(idx) {
    const target = ((idx % slides.length) + slides.length) % slides.length;
    if (target !== current) {
      slides[current].classList.remove('is-on');
      slides[current].setAttribute('aria-hidden', 'true');
      current = target;
      slides[current].classList.add('is-on');
      slides[current].setAttribute('aria-hidden', 'false');
      sync(current);
    }
    paintSegs();
  }
  const advance = () => goTo(current + 1);
  const regress = () => goTo(current - 1);

  sync(current);

  if (autoOk) {
    hero.classList.add('fhero--auto');
    if (track) track.addEventListener('animationend', (e) => {
      if (e.animationName === 'fhero-seg-fill') advance();
    });
    // pause while the pointer is over the hero (the user is aiming/reading)
    const frame = hero.querySelector('.fhero__frame');
    if (frame) {
      frame.addEventListener('mouseenter', () => hero.classList.add('fhero--paused'));
      frame.addEventListener('mouseleave', () => hero.classList.remove('fhero--paused'));
    }
    // and while scrolled out of view — no point cycling off-screen
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(([entry]) => {
        hero.classList.toggle('fhero--idle', !entry.isIntersecting);
      }, { threshold: 0.15 }).observe(hero);
    }
  } else if (autoLabel) {
    autoLabel.textContent = slides.length > 1 ? 'MANUAL' : 'SINGLE';
  }

  const nextBtn = document.getElementById('fhero-next');
  const prevBtn = document.getElementById('fhero-prev');
  if (nextBtn) nextBtn.addEventListener('click', advance);
  if (prevBtn) prevBtn.addEventListener('click', regress);
  segs.forEach(s => s.addEventListener('click', () => goTo(parseInt(s.dataset.goto, 10) || 0)));

  // ← → only while focus is inside the hero — an in-page slideshow must
  // not hijack the page-level arrow keys (unlike the welcome viewfinder)
  hero.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); regress(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); advance(); }
  });

  // swipe on the stage switches slides (and suppresses the anchor tap)
  if (stage) {
    let sx = 0, sy = 0, st = 0, swiping = false;
    stage.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) { swiping = false; return; }
      swiping = true;
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      st = Date.now();
    }, { passive: true });
    stage.addEventListener('touchend', (e) => {
      if (!swiping) return;
      swiping = false;
      const t = e.changedTouches[0];
      const dx = t.clientX - sx;
      const dy = t.clientY - sy;
      if (Date.now() - st > 700) return;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
      e.preventDefault();
      if (dx < 0) advance(); else regress();
    }, { passive: false });
  }
});

// ---------- SCROLL REVEAL --------------------------------------
// Content blocks rise in as they enter the viewport. Elements that reveal
// in the same observer batch stagger by 45 ms (capped), so grids cascade
// instead of popping in as one wall. Gated on html.fx-anim (set above only
// when allowHeavyFx() + IntersectionObserver hold) — without it nothing is
// ever tagged .rv and the page stays fully static, including for crawlers
// and no-JS visitors.
document.addEventListener('DOMContentLoaded', () => {
  if (!document.documentElement.classList.contains('fx-anim')) return;

  // Stepping back OUT of a photo must not rebuild the album around the
  // user — photoAlbumContinuity() (runs at parse time, i.e. before this)
  // sets html.fx-return when this load is such a return, and the entrance
  // cascade is skipped once. Every real (re)visit — leaving an album
  // entirely and coming back later included — keeps the full entrance.
  if (document.documentElement.classList.contains('fx-return')) return;

  const targets = document.querySelectorAll([
    '.section__doc',
    '.section__head',
    '.trip',
    '.archive-head',
    '.album-group__head',
    '.sub-albums__head',
    '.feat__head',
    '.fhero__head',
    '.fhero__frame',
    '.showcase__head',
    '.trip-map',
    '.album-desc',
    '.album-grid > li',
    '.feat__rail > li',
    '.image-grid > li',
    '.vf-band__cell',
  ].join(','));
  if (!targets.length) return;

  const STEP_MS = 45;
  const MAX_DELAY_MS = 315;
  const io = new IntersectionObserver((entries, obs) => {
    let batch = 0;
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      const el = en.target;
      // stagger within this batch; CSSOM assignment is CSP-safe
      el.style.animationDelay = Math.min(batch * STEP_MS, MAX_DELAY_MS) + 'ms';
      batch++;
      el.classList.add('rv-in');
      obs.unobserve(el);
    });
  }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });

  targets.forEach((el) => {
    el.classList.add('rv');
    io.observe(el);
  });
});

// ---------- THUMBNAIL FADE-IN ----------------------------------
// Grid covers and tiles fade in when they finish loading instead of popping.
// Images already complete at wiring time (warm cache, bfcache restore) skip
// the fade entirely, so revisits stay instant. The helper classes are dropped
// after the fade so the cards' own hover transitions take back over.
document.addEventListener('DOMContentLoaded', () => {
  if (!document.documentElement.classList.contains('fx-anim')) return;
  document.querySelectorAll(
    '.album-card__img img, .image-tile img, .feat-card__img img'
  ).forEach((img) => {
    if (img.complete) return;
    img.classList.add('img-fade');
    const done = () => {
      img.classList.add('img-in');
      setTimeout(() => img.classList.remove('img-fade', 'img-in'), 600);
    };
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
  });
});

// ---------- STAGE PIXEL-IN (click-to-open decode) --------------
// Opening a photo decodes it in like a feed acquiring signal: a coarse
// mosaic (tiny canvas over the stage image, CSS-upscaled with
// image-rendering:pixelated) refines through a few steps, then snaps to
// the sharp image. Called by initImagePage(), i.e. on photo-page load AND
// after every SPA swap — this is the click-to-open animation, grid thumbs
// deliberately keep their plain fade. The stage <img> hides behind
// .px-wait until its mosaic finishes.
window.__stagePixelIn = () => {
  if (!document.documentElement.classList.contains('fx-anim')) return;
  const img = document.getElementById('stage-img');
  if (!img || img.classList.contains('px-wait')) return;

  const CELLS_PX = [40, 20, 10, 5]; // mosaic cell size on screen, coarse → fine
  const STEP_MS = 90;

  const reveal = () => img.classList.remove('px-wait');
  const start = () => {
    // the stage is the positioned ancestor; the img box IS the photo box
    // (auto-sized, aspect preserved), so overlay exactly that rectangle
    const stage = img.closest('.stage');
    if (!stage || !img.naturalWidth || !img.isConnected) { reveal(); return; }
    const w = img.offsetWidth;
    const h = img.offsetHeight;
    if (w < 48 || h < 48) { reveal(); return; }
    const canvas = document.createElement('canvas');
    canvas.className = 'px-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    const ctx = canvas.getContext('2d');
    if (!ctx) { reveal(); return; }
    canvas.style.left = img.offsetLeft + 'px';
    canvas.style.top = img.offsetTop + 'px';
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    const finish = () => { reveal(); canvas.remove(); };
    let step = 0;
    const paint = () => {
      const cell = CELLS_PX[step];
      canvas.width = Math.max(1, Math.round(w / cell));
      canvas.height = Math.max(1, Math.round(h / cell));
      ctx.imageSmoothingEnabled = true; // average down = clean mosaic cells
      try {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      } catch (e) { finish(); return; }
      step++;
      setTimeout(step < CELLS_PX.length ? paint : finish, STEP_MS);
    };
    stage.appendChild(canvas);
    paint();
  };

  img.classList.add('px-wait');
  if (img.complete && img.naturalWidth) start();
  else {
    img.addEventListener('load', start, { once: true });
    img.addEventListener('error', reveal, { once: true });
  }
};

// ---------- NAV SCROLL STATE -----------------------------------
// Deepen the sticky nav once the page scrolls so it reads as a bar floating
// over content instead of blending into the hero. Pure state toggle (colors,
// shadow) — cheap enough to run everywhere, no fx gate needed.
(function navScrollState() {
  const nav = document.querySelector('.nav');
  if (!nav) return;
  let ticking = false;
  const apply = () => {
    nav.classList.toggle('nav--scrolled', (window.scrollY || 0) > 8);
    ticking = false;
  };
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(apply); }
  }, { passive: true });
  document.addEventListener('DOMContentLoaded', apply);
})();

// ---------- PHOTO ↔ ALBUM CONTINUITY ---------------------------
// Opening a photo pixels in via __stagePixelIn; no shared-element morphs
// in either direction. What this module does: the album remembers WHICH
// photo was open — on return it scrolls that tile back into view
// pre-paint (no dump at page top), blinks its corner brackets and skips
// the entrance-reveal replay (html.fx-return). Runs at parse time — the
// rel=expect link in <head> holds back first render until this script has
// executed, so the restored scroll position never flashes.
(function photoAlbumContinuity() {
  const KEY = 'vt:last-photo';
  const store = {
    read() { try { return sessionStorage.getItem(KEY); } catch (e) { return null; } },
    write(v) { try { sessionStorage.setItem(KEY, v); } catch (e) {} },
    clear() { try { sessionStorage.removeItem(KEY); } catch (e) {} },
  };
  // image page + lightbox keep the key pointing at the photo on screen
  window.__vtRememberPhoto = (rel) => store.write(rel);

  if (document.querySelector('.detail')) {
    // photo page: remember the photo being viewed (SPA swaps and lightbox
    // navigation refresh this via __vtRememberPhoto)
    const m = location.pathname.match(/^\/image\/(.+)$/);
    if (m) {
      let rel = m[1];
      try { rel = decodeURIComponent(rel); } catch (e) {}
      store.write(rel);
    }
    return;
  }

  // every other page consumes the key exactly once, so a stale entry can't
  // scroll some later album visit around unexpectedly
  const rel = store.read();
  store.clear();
  if (!rel) return;
  // collection albums append ?col= (and sorted views ?sort=) to tile links —
  // compare the path part only, the query never changes which photo it is
  let link = null;
  document.querySelectorAll('.image-tile a').forEach((a) => {
    if (link) return;
    const path = (a.getAttribute('href') || '').split('?')[0];
    if (path === '/image/' + rel) link = a;
  });
  if (!link) return;
  // this load is a return out of one of this page's own photos: flag it so
  // the scroll-reveal module skips the entrance cascade this one time
  // (replaying the page build-up around the user read as unnatural)
  document.documentElement.classList.add('fx-return');
  // land mid-viewport: the user keeps their place in the grid
  link.scrollIntoView({ block: 'center', behavior: 'instant' });
  const tile = link.closest('.image-tile');
  if (tile) tile.classList.add('is-returned');
})();

// ---------- PREVIEW PRE-WARM (tile hover) ----------------------
// Aiming at a tile warms the /preview/ file its photo page will need, so
// entering the photo doesn't stall on the hero request. Only fires after
// 65 ms of hover intent (not while sweeping across the grid); pointerdown
// warms immediately. allowHeavyFx() keeps data-saver and low-end out.
document.addEventListener('DOMContentLoaded', () => {
  if (!allowHeavyFx()) return;
  const warmed = new Set();
  const warm = (a) => {
    // strip ?col=/?sort= so the warmed URL matches the stage's cache key
    const m = (a.getAttribute('href') || '').split('?')[0].match(/^\/image\/(.+)$/);
    if (!m || warmed.has(m[1])) return;
    warmed.add(m[1]);
    const img = new Image();
    img.src = '/preview/' + m[1];
  };
  document.querySelectorAll('.image-tile a').forEach((a) => {
    let t = null;
    a.addEventListener('mouseenter', () => { t = setTimeout(() => warm(a), 65); }, { passive: true });
    a.addEventListener('mouseleave', () => { if (t) clearTimeout(t); }, { passive: true });
    a.addEventListener('pointerdown', () => warm(a), { passive: true });
  });
});

// ---------- COUNT-UP DIGITS ------------------------------------
// [data-count-to] elements render their final value server-side; capable
// devices re-count from 0 when the element scrolls into view. fx-lite
// keeps the static number (no rAF loop, no zero flash).
document.addEventListener('DOMContentLoaded', () => {
  const els = Array.from(document.querySelectorAll('[data-count-to]'));
  if (!els.length) return;
  if (!allowHeavyFx() || !('IntersectionObserver' in window)) return;
  const run = (el) => {
    const target = parseInt(el.dataset.countTo, 10);
    if (!isFinite(target)) return;
    const DUR = 1100;
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / DUR);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const io = new IntersectionObserver((ents, obs) => {
    ents.forEach(en => {
      if (en.isIntersecting) { run(en.target); obs.unobserve(en.target); }
    });
  }, { threshold: 0.5 });
  els.forEach(el => { el.textContent = '0'; io.observe(el); });
});

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  // when the lightbox is open, its own handler takes over
  const lb = document.getElementById('lightbox');
  if (lb && !lb.hidden) return;
  if (e.key === 'ArrowLeft') {
    const prev = document.querySelector('.nav-arrow.prev');
    if (prev) prev.click(); // triggers the SPA-aware click handler
  } else if (e.key === 'ArrowRight') {
    const next = document.querySelector('.nav-arrow.next');
    if (next) next.click();
  } else if (e.key === 'Escape') {
    // in a collection, Esc returns to the collection root (same target as the
    // "back" button); otherwise fall back to the last breadcrumb (the folder).
    const data = readAlbumData();
    if (data && data.collection_root) {
      window.location.href = '/album/' + data.collection_root;
      return;
    }
    const crumb = document.querySelector('.crumb a:last-of-type');
    if (crumb) window.location.href = crumb.href;
  }
});

// ---------- IMAGE-PAGE INIT (re-callable after SPA swap) -------
function initImagePage() {
  const btn = document.getElementById('load-full-btn');
  const img = document.getElementById('stage-img');
  const loader = document.getElementById('stage-loader');
  const stamp = document.getElementById('quality-stamp');
  if (!btn || !img || !loader) return;

  // reset stage state — DOM was just (re)rendered
  loader.classList.remove('is-loading', 'is-done');
  img.classList.add('is-preview');
  btn.textContent = TXT.loadOriginal;
  // click-to-open decode: the photo pixels in on load and after SPA swaps
  if (window.__stagePixelIn) window.__stagePixelIn();
  if (stamp) {
    stamp.textContent = 'PREVIEW';
    stamp.classList.add('stamp--cy');
    stamp.classList.remove('stamp--ok');
  }

  // load-original swaps preview → full quality inside the stage
  btn.addEventListener('click', () => {
    const fullUrl = img.dataset.full;
    if (!fullUrl) return;
    loader.classList.add('is-loading');
    btn.textContent = TXT.loading;
    const full = new Image();
    full.onload = () => {
      img.src = fullUrl;
      img.classList.remove('is-preview');
      loader.classList.add('is-done');
      if (stamp) {
        stamp.textContent = 'ORIGINAL';
        stamp.classList.remove('stamp--cy');
        stamp.classList.add('stamp--ok');
      }
    };
    full.onerror = () => {
      loader.classList.remove('is-loading');
      btn.textContent = TXT.errRetry;
    };
    full.src = fullUrl;
  });

  // fullscreen / lightbox triggers
  const fsBtn = document.getElementById('open-fullscreen-btn');
  if (fsBtn) fsBtn.addEventListener('click', () => {
    if (typeof window.__lightboxOpen === 'function') window.__lightboxOpen();
  });
  img.addEventListener('click', (e) => {
    e.preventDefault();
    if (typeof window.__lightboxOpen === 'function') window.__lightboxOpen();
  });

  // swipe gestures on the stage — SPA-load next/prev
  const stage = img.closest('.stage');
  if (stage) {
    let sStartX = 0, sStartY = 0, sStartT = 0, sTracking = false;
    stage.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) { sTracking = false; return; }
      sTracking = true;
      sStartX = e.touches[0].clientX;
      sStartY = e.touches[0].clientY;
      sStartT = Date.now();
    }, { passive: true });
    stage.addEventListener('touchend', async (e) => {
      if (!sTracking) return;
      sTracking = false;
      if (window.visualViewport && window.visualViewport.scale > 1.05) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - sStartX;
      const dy = t.clientY - sStartY;
      const dt = Date.now() - sStartT;
      if (dt > 700) return;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
      e.preventDefault();
      const link = dx < 0
        ? document.querySelector('.nav-arrow.next')
        : document.querySelector('.nav-arrow.prev');
      if (!link) return;
      const ok = await spaLoadImage(link.href);
      if (!ok) location.replace(link.href);
    }, { passive: false });
  }

  // nav-arrow clicks → SPA load instead of full page navigation
  document.querySelectorAll('.nav-arrow.prev, .nav-arrow.next').forEach(a => {
    a.addEventListener('click', async (ev) => {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1) return;
      ev.preventDefault();
      const ok = await spaLoadImage(a.href);
      if (!ok) location.replace(a.href);
    });
  });

  // warm the cache for neighbours so SPA nav feels instant
  document.querySelectorAll('.nav-arrow.prev, .nav-arrow.next').forEach(a => {
    try {
      const u = new URL(a.href, location.href);
      const m = u.pathname.match(/^\/image\/(.+)$/);
      if (m) { const p = new Image(); p.src = '/preview/' + m[1]; }
    } catch (e) {}
  });

  // keep the return-to-album anchor pointing at the photo on screen
  // (this runs again after every SPA swap)
  if (window.__vtRememberPhoto) {
    const relM = location.pathname.match(/^\/image\/(.+)$/);
    if (relM) {
      let rel = relM[1];
      try { rel = decodeURIComponent(rel); } catch (e) {}
      window.__vtRememberPhoto(rel);
    }
  }
}
window.__initImagePage = initImagePage;
document.addEventListener('DOMContentLoaded', initImagePage);

// ---------- SORT DROPDOWN --------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const sorts = document.querySelectorAll('[data-sort]');
  if (!sorts.length) return;

  const isMobile = () => window.matchMedia('(max-width: 760px)').matches;
  let backdrop = null;
  function ensureBackdrop(){
    if (backdrop) return backdrop;
    backdrop = document.createElement('div');
    backdrop.className = 'sort__backdrop';
    document.body.appendChild(backdrop);
    return backdrop;
  }

  sorts.forEach(sort => {
    const btn = sort.querySelector('.sort__btn');
    const menu = sort.querySelector('.sort__menu');
    if (!btn || !menu) return;

    function close(){
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      if (backdrop) backdrop.classList.remove('is-open');
      document.body.classList.remove('sort-open');
      // restore menu to its original parent if we lifted it
      if (menu._origParent && menu.parentNode === document.body) {
        if (menu._origNext && menu._origNext.parentNode === menu._origParent) {
          menu._origParent.insertBefore(menu, menu._origNext);
        } else {
          menu._origParent.appendChild(menu);
        }
        menu._origParent = null;
        menu._origNext = null;
      }
    }
    function open(){
      // close any other open menu
      sorts.forEach(s => {
        if (s !== sort) {
          const m = s.querySelector('.sort__menu');
          const b = s.querySelector('.sort__btn');
          if (m) m.hidden = true;
          if (b) b.setAttribute('aria-expanded', 'false');
        }
      });
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      if (isMobile()) {
        ensureBackdrop().classList.add('is-open');
        // Lift the menu out of <main>'s stacking context (z-index:10),
        // otherwise the backdrop at body level (z:200) sits *above* it
        // and intercepts every tap.
        if (menu.parentNode !== document.body) {
          menu._origParent = menu.parentNode;
          menu._origNext = menu.nextSibling;
          document.body.appendChild(menu);
        }
      }
      document.body.classList.add('sort-open');
    }

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (menu.hidden) open(); else close();
    });

    // outside click closes
    document.addEventListener('click', (e) => {
      if (menu.hidden) return;
      if (!sort.contains(e.target) && (!backdrop || e.target === backdrop || !backdrop.contains(e.target))) {
        close();
      }
    });

    if (backdrop || true) {
      // backdrop click also closes (once it exists)
      document.addEventListener('click', (e) => {
        if (backdrop && e.target === backdrop) close();
      });
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    sorts.forEach(s => {
      const m = s.querySelector('.sort__menu');
      const b = s.querySelector('.sort__btn');
      if (m && !m.hidden) {
        m.hidden = true;
        if (b) b.setAttribute('aria-expanded', 'false');
        if (backdrop) backdrop.classList.remove('is-open');
        document.body.classList.remove('sort-open');
      }
    });
  });
});

// ---------- BACK-BUTTON GUARD (image view → album) -------------
// On any /image/ page, ensure the browser back button takes the user
// straight to the corresponding /album/... view, regardless of how they
// navigated between images. Achieved by pushing a duplicate history
// entry and redirecting to the album on popstate. Also intercepts the
// stage nav-arrows so prev/next within the same album replace the
// current entry instead of stacking up.
(function setupBackGuard(){
  document.addEventListener('DOMContentLoaded', () => {
    const dataEl = document.getElementById('album-data');
    if (!dataEl) return;
    let data;
    try { data = JSON.parse(dataEl.textContent); }
    catch (e) { return; }
    if (!data || !data.album) return;
    // when browsing a collection, "back" returns to the collection root the
    // user came from rather than the sub-folder this photo lives in.
    const backAlbum = data.collection_root || data.album;
    const albumBase = '/album/' + encodeURIComponent(backAlbum).replace(/%2F/g, '/');

    try { history.pushState({ albumGuard: true }, '', location.pathname + location.search); }
    catch (e) { return; }

    window.addEventListener('popstate', (e) => {
      // any back from this view shortcuts to the album.
      // preserve ?sort= so the user lands on the same ordering.
      const params = new URLSearchParams(location.search);
      const passthrough = new URLSearchParams();
      const sort = params.get('sort');
      if (sort) passthrough.set('sort', sort);
      const qs = passthrough.toString();
      location.replace(qs ? albumBase + '?' + qs : albumBase);
    });
    // nav-arrow click interception is now handled by initImagePage()
    // (which uses SPA navigation instead of full reload).
  });
})();

// ---------- LIGHTBOX -------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const lb = document.getElementById('lightbox');
  if (!lb) return;

  // Reparent to body so the lightbox escapes <main>'s stacking context,
  // which would otherwise confine our z:1000 to main's local layer. Living
  // directly under body, z:1000 stacks at the top level as intended.
  if (lb.parentNode !== document.body) document.body.appendChild(lb);

  const stage = document.getElementById('lb-stage');
  const imgEl = document.getElementById('lb-img');
  const titleEl = document.getElementById('lb-title');
  const countEl = document.getElementById('lb-count');
  const prevBtn = document.getElementById('lb-prev');
  const nextBtn = document.getElementById('lb-next');
  const closeBtn = document.getElementById('lb-close');
  const fullBtn = document.getElementById('lb-full');
  const dlBtn = document.getElementById('lb-dl');
  const bar = lb.querySelector('.lightbox__bar');

  // mutable state — refreshed whenever the underlying #album-data changes
  // (initial page load + after every SPA swap).
  let rels = [];
  let total = 0;
  let index = 0;
  let initialIndex = 0;
  let initialSearch = '';
  let showingFull = false;

  function reload() {
    const data = readAlbumData();
    if (!data || !Array.isArray(data.rels) || data.rels.length === 0) {
      rels = []; total = 0; index = 0; initialIndex = 0;
      return;
    }
    rels = data.rels;
    total = rels.length;
    index = Math.max(0, Math.min(data.current | 0, total - 1));
    initialIndex = index;
    initialSearch = location.search;
  }
  reload();
  window.__lightboxReload = reload;
  if (rels.length === 0) return;

  const IDLE_MS = 2500;
  let idleTimer = null;
  function bumpIdle(){
    if (lb.hidden) return;
    lb.classList.remove('is-idle');
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (!lb.hidden) lb.classList.add('is-idle');
    }, IDLE_MS);
  }
  function cancelIdle(){
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
    lb.classList.remove('is-idle');
  }

  function relToPreview(rel){ return '/preview/' + rel; }
  function relToFull(rel){ return '/full/' + rel; }
  function relToFilename(rel){
    const parts = rel.split('/');
    return parts[parts.length - 1];
  }
  function relToTitle(rel){
    const parts = rel.split('/');
    if (parts.length >= 2) return parts[0] + ' / ' + parts.slice(1).join('/');
    return rel;
  }

  function preload(rel){
    if (!rel) return;
    const p = new Image();
    p.src = relToPreview(rel);
  }

  function setLoading(on){ lb.classList.toggle('is-loading', !!on); }

  function render(){
    const rel = rels[index];
    const filename = relToFilename(rel);
    titleEl.textContent = relToTitle(rel);
    countEl.textContent = String(index + 1).padStart(2, '0') + ' / ' + String(total).padStart(2, '0');
    dlBtn.href = relToFull(rel);
    dlBtn.setAttribute('download', filename);
    fullBtn.textContent = TXT.loadOriginal;
    bar.classList.remove('is-loading-full', 'is-full');
    showingFull = false;

    setLoading(true);
    const next = new Image();
    next.onload = () => {
      imgEl.src = next.src;
      imgEl.alt = filename;
      setLoading(false);
    };
    next.onerror = () => setLoading(false);
    next.src = relToPreview(rel);

    if (prevBtn) prevBtn.disabled = (index <= 0);
    if (nextBtn) nextBtn.disabled = (index >= total - 1);

    // preload neighbours
    if (index + 1 < total) preload(rels[index + 1]);
    if (index - 1 >= 0) preload(rels[index - 1]);

    // update URL bar to reflect the currently-viewed image (keep sort etc.)
    try { history.replaceState(null, '', '/image/' + rel + initialSearch); } catch(e){}
    // keep the return-to-album anchor in sync while flipping in the viewer
    if (window.__vtRememberPhoto) window.__vtRememberPhoto(rel);
  }

  function navigate(delta){
    const target = index + delta;
    if (target < 0 || target >= total) return;
    index = target;
    render();
    bumpIdle();
  }

  function open(){
    if (!lb.hidden) return;
    lb.hidden = false;
    document.body.classList.add('lightbox-open');
    render();
    bumpIdle();
  }

  function close(){
    if (lb.hidden) return;
    cancelIdle();
    // if user navigated to a different image, reload so the
    // page metadata (EXIF, tags, breadcrumbs) matches the URL.
    if (index !== initialIndex){
      window.location.reload();
      return;
    }
    lb.hidden = true;
    document.body.classList.remove('lightbox-open');
    setLoading(false);
  }

  // any interaction inside the lightbox keeps the UI alive.
  // wasIdle flag lets the immediate click after a wake-up tap be
  // swallowed — so tapping a hidden UI brings it back instead of
  // closing the viewer.
  let wasIdle = false;
  lb.addEventListener('mousemove', bumpIdle);
  lb.addEventListener('pointerdown', (e) => {
    wasIdle = lb.classList.contains('is-idle');
    bumpIdle();
  }, true);
  lb.addEventListener('wheel', bumpIdle, { passive: true });

  // open triggers — wired by initImagePage() via window.__lightboxOpen
  window.__lightboxOpen = open;

  // nav / close
  if (prevBtn) prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(-1); });
  if (nextBtn) nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(1); });
  if (closeBtn) closeBtn.addEventListener('click', (e) => { e.stopPropagation(); close(); });

  // click outside image / on stage padding closes
  lb.addEventListener('click', (e) => {
    if (wasIdle) { wasIdle = false; return; }
    if (e.target === lb || e.target === stage) close();
  });

  // click on image itself zooms out / closes too (cursor:zoom-out vibe)
  imgEl.addEventListener('click', (e) => {
    e.stopPropagation();
    if (wasIdle) { wasIdle = false; return; }
    close();
  });

  // bar shouldn't close
  if (bar) bar.addEventListener('click', (e) => e.stopPropagation());

  // load original / download buttons
  if (fullBtn) fullBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (showingFull) return;
    const rel = rels[index];
    const fullUrl = relToFull(rel);
    bar.classList.add('is-loading-full');
    fullBtn.textContent = TXT.loading;
    const full = new Image();
    full.onload = () => {
      imgEl.src = fullUrl;
      showingFull = true;
      bar.classList.remove('is-loading-full');
      bar.classList.add('is-full');
    };
    full.onerror = () => {
      bar.classList.remove('is-loading-full');
      fullBtn.textContent = TXT.errRetry;
    };
    full.src = fullUrl;
  });

  // keyboard (capture so it beats the global arrow-nav handler)
  document.addEventListener('keydown', (e) => {
    if (lb.hidden) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    bumpIdle();
    if (e.key === 'Escape')      { e.stopPropagation(); close(); }
    else if (e.key === 'ArrowLeft')  { e.stopPropagation(); navigate(-1); }
    else if (e.key === 'ArrowRight') { e.stopPropagation(); navigate(1); }
  }, true);

  // swipe gestures (lightbox)
  let tStartX = 0, tStartY = 0, tStartT = 0, tracking = false;
  stage.addEventListener('touchstart', (e) => {
    // multi-touch = pinch in progress, let the browser handle it
    if (e.touches.length !== 1) { tracking = false; return; }
    tracking = true;
    tStartX = e.touches[0].clientX;
    tStartY = e.touches[0].clientY;
    tStartT = Date.now();
  }, { passive: true });
  stage.addEventListener('touchend', (e) => {
    if (!tracking) return;
    tracking = false;
    // while user is zoomed in, treat one-finger drags as panning, not nav
    if (window.visualViewport && window.visualViewport.scale > 1.05) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - tStartX;
    const dy = t.clientY - tStartY;
    const dt = Date.now() - tStartT;
    if (dt > 700) return;
    if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) navigate(1); else navigate(-1);
  }, { passive: true });
});

// ---------- TRIP DASHBOARD (album addon) -----------------------
// Live flight countdown + itinerary "you are here" marker. Every date is
// read as the viewer's LOCAL wall-clock, so the widget reads correctly both
// from home before departure and on the ground once the trip is underway.
// State is driven purely by class toggles + element.style (CSP-safe — no
// inline <script>/<style>); all styling lives in style.css.
document.addEventListener('DOMContentLoaded', () => {
  const root = document.querySelector('[data-trip]');
  if (!root) return;

  const DAY = 86400000;
  const pad2 = (n) => String(n).padStart(2, '0');

  // "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS" -> local Date (NOT UTC).
  const parseLocal = (s) => {
    const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  };
  const fmtDate = (d) => d ? TXT.fmtDate(d, TXT.months) : '';
  const ceilDays = (from, to) => Math.max(0, Math.ceil((to - from) / DAY));

  const depart = parseLocal(root.dataset.depart);
  // `city` stays the English key (matches _trip_map.html's data-map-city and
  // the HUD status stamp); `cityLabel` is what countdown sentences display —
  // the Japanese name on the JP page when the template provides one.
  const stops = Array.from(root.querySelectorAll('[data-stop]')).map((el) => ({
    el,
    city: el.dataset.city || '',
    cityLabel: (UI_LANG === 'ja' && el.dataset.cityJp) ? el.dataset.cityJp : (el.dataset.city || ''),
    start: parseLocal(el.dataset.start),
    end: parseLocal(el.dataset.end),
    fill: el.querySelector('[data-stop-fill]'),
    meta: el.querySelector('[data-stop-meta]'),
  })).filter((s) => s.start && s.end);

  // route map (_trip_map.html): dots + visited prefectures mirror the stop
  // states; segments (keyed by the stop they END at) get is-done / is-next.
  // The map lives in the album sidebar, outside [data-trip], so look it up
  // document-wide.
  const mapCity = {};
  const mapPref = {};
  document.querySelectorAll('[data-map-city]').forEach((g) => { mapCity[g.dataset.mapCity] = g; });
  document.querySelectorAll('[data-map-pref]').forEach((p) => { mapPref[p.dataset.mapPref] = p; });
  const mapSegs = Array.from(document.querySelectorAll('[data-map-seg]'));

  // JST wall clock in the top bar (Japan has a single, DST-free zone).
  const clockEl = root.querySelector('[data-trip-clock]');
  let jstFmt = null;
  try {
    jstFmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Tokyo', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
  } catch (e) {
    if (clockEl) clockEl.hidden = true;
  }

  const cd = {
    d: root.querySelector('[data-cd="d"]'),
    h: root.querySelector('[data-cd="h"]'),
    m: root.querySelector('[data-cd="m"]'),
    s: root.querySelector('[data-cd="s"]'),
  };
  const labelEl = root.querySelector('[data-trip-cd-label]');
  const targetEl = root.querySelector('[data-trip-cd-target]');
  const statusEl = root.querySelector('[data-trip-status]');

  const firstStart = stops.length ? stops[0].start : null;
  const lastEnd = stops.length ? stops[stops.length - 1].end : null;

  const setPhase = (p) => ['pre', 'transit', 'active', 'done']
    .forEach((x) => root.classList.toggle('trip--' + x, x === p));

  const setState = (el, state) => {
    if (!el) return;
    el.classList.toggle('is-upcoming', state === 'upcoming');
    el.classList.toggle('is-active', state === 'active');
    el.classList.toggle('is-done', state === 'done');
  };

  const setClock = (ms) => {
    const t = Math.max(0, ms);
    if (cd.d) cd.d.textContent = String(Math.floor(t / DAY));
    if (cd.h) cd.h.textContent = pad2(Math.floor((t % DAY) / 3600000));
    if (cd.m) cd.m.textContent = pad2(Math.floor((t % 3600000) / 60000));
    if (cd.s) cd.s.textContent = pad2(Math.floor((t % 60000) / 1000));
  };

  function tick() {
    const now = new Date();

    // per-stop state + progress fill. Boundaries are start-inclusive /
    // end-exclusive (the last stop includes its end) so a shared travel-day
    // date belongs to the city you're arriving in — only one stop is ever
    // "active".
    let activeIdx = -1;
    const states = [];
    stops.forEach((s, i) => {
      const isLast = i === stops.length - 1;
      let state;
      if (now < s.start) state = 'upcoming';
      else if (isLast ? now > s.end : now >= s.end) state = 'done';
      else { state = 'active'; activeIdx = i; }
      states.push(state);

      setState(s.el, state);
      setState(mapCity[s.city], state);
      setState(mapPref[s.city], state);

      const span = s.end - s.start;
      let pct = state === 'done' ? 100
        : state === 'upcoming' ? 0
        : span > 0 ? ((now - s.start) / span) * 100 : 0;
      pct = Math.max(0, Math.min(100, pct));
      if (s.fill) s.fill.style.width = pct + '%';

      if (s.meta) {
        if (state === 'upcoming') {
          const dleft = ceilDays(now, s.start);
          s.meta.textContent = dleft <= 1 ? TXT.soon : TXT.inDays(dleft);
        } else if (state === 'active') {
          const total = Math.max(1, Math.round(span / DAY));
          const dayNum = Math.min(total, Math.floor((now - s.start) / DAY) + 1);
          s.meta.textContent = TXT.dayOf(dayNum, total);
        } else {
          s.meta.textContent = '✓';
        }
      }
    });

    // map segments: seg i is the leg INTO stop i (there is no leg into stop
    // 0). Done once that stop is reached; "next" (animated dashes) while its
    // origin stop is underway/passed but the destination hasn't started.
    mapSegs.forEach((seg) => {
      const i = +seg.dataset.mapSeg;
      const reached = states[i] && states[i] !== 'upcoming';
      const next = states[i] === 'upcoming' && !!states[i - 1] && states[i - 1] !== 'upcoming';
      seg.classList.toggle('is-done', !!reached);
      seg.classList.toggle('is-next', next);
    });

    if (jstFmt && clockEl) clockEl.textContent = 'JST ' + jstFmt.format(now);

    // phase + headline countdown (label is localized; the status stamp is
    // HUD chrome and stays English in every language)
    let phase, target, label, status;
    if (depart && now < depart) {
      phase = 'pre'; target = depart;
      label = TXT.departsIn; status = 'T-' + Math.floor((depart - now) / DAY) + ' DAYS';
    } else if (firstStart && now < firstStart) {
      phase = 'transit'; target = firstStart;
      label = TXT.arrivingIn(stops[0].cityLabel); status = 'IN TRANSIT';
    } else if (activeIdx >= 0) {
      phase = 'active'; target = stops[activeIdx].end;
      label = TXT.leavingIn(stops[activeIdx].cityLabel); status = 'IN ' + stops[activeIdx].city.toUpperCase();
    } else {
      phase = 'done'; target = null;
      label = TXT.tripComplete; status = 'COMPLETE';
    }

    setPhase(phase);
    setClock(target ? target - now : 0);
    if (labelEl) labelEl.textContent = label;
    if (statusEl) statusEl.textContent = status;
    if (targetEl) targetEl.textContent = fmtDate(target || lastEnd);
  }

  tick();
  setInterval(tick, 1000);

  // ---- current weather per stop (same-origin proxy) ----
  // One fetch per page view against /api/trip-weather (server-cached proxy
  // to Open-Meteo — the browser never talks to a third party, so no consent
  // UI is needed and connect-src 'self' holds). Chips stay hidden unless
  // real data arrives; glyphs use text-presentation symbols so they render
  // in the mono HUD style instead of as emoji. Condition names are HUD
  // chrome (English, tooltip only), like the status stamps.
  const wxByCity = {};
  stops.forEach((s) => {
    const el = s.el.querySelector('[data-stop-wx]');
    if (el) wxByCity[s.city] = el;
  });
  const tripKey = root.dataset.tripKey;
  if (tripKey && window.fetch && Object.keys(wxByCity).length) {
    // WMO weather_code buckets -> [glyph, label]; ☀/☾ pick by is_day
    const wmo = (code, isDay) => {
      if (code <= 1) return [isDay ? '☀︎' : '☾︎', code === 0 ? 'Clear' : 'Mostly clear'];
      if (code <= 3) return ['☁︎', code === 2 ? 'Partly cloudy' : 'Overcast'];
      if (code <= 48) return ['≡', 'Fog'];
      if (code <= 57) return ['☂︎', 'Drizzle'];
      if (code <= 67) return ['☂︎', 'Rain'];
      if (code <= 77) return ['❄︎', 'Snow'];
      if (code <= 82) return ['☂︎', 'Rain showers'];
      if (code <= 86) return ['❄︎', 'Snow showers'];
      return ['⚡︎', 'Thunderstorm'];
    };
    fetch('/api/trip-weather?trip=' + encodeURIComponent(tripKey), { credentials: 'omit' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !Array.isArray(data.stops)) return;
        data.stops.forEach((w) => {
          const el = wxByCity[w.city];
          if (!el || typeof w.temp !== 'number') return;
          const [glyph, label] = wmo(w.code, w.is_day);
          el.textContent = glyph + ' ' + Math.round(w.temp) + '°';
          el.title = label + ' · weather: open-meteo.com';
          el.hidden = false;
        });
      })
      .catch(() => {}); // no weather is a fine state — chips just stay hidden
  }
});
