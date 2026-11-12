/* SPDX-License-Identifier: AGPL-3.0-or-later · Copyright © 2026 lucya.systems — https://lucya.systems */
/* Aperture 3.2 · the gallery's script. Every page works without it: the
   reels show their first picture, the sort menu is a <details>, a photo's
   arrows are links. This file adds motion and the lightbox, nothing else. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches ||
    document.documentElement.dataset.motion === 'reduced';

  /* ---- reels: the welcome hero and an album's featured strip ---- */
  const hydrate = (slide) => {
    const img = slide && $('img[data-src]', slide);
    if (img) { img.src = img.dataset.src; img.removeAttribute('data-src'); }
  };
  $$('[data-reel]').forEach((reel) => {
    const slides = $$('.g-reel__slide', reel);
    if (slides.length < 2) return;
    const dots = $$('.g-reel__dot', reel);
    const name = $('[data-reel-name]', reel), file = $('[data-reel-file]', reel), open = $('[data-reel-open]', reel);
    let cur = 0, timer = null, paused = false, seen = true;
    const show = (i) => {
      i = (i + slides.length) % slides.length;
      hydrate(slides[i]); hydrate(slides[(i + 1) % slides.length]);
      slides.forEach((s, k) => { s.classList.toggle('is-on', k === i); s.setAttribute('aria-hidden', k === i ? 'false' : 'true'); });
      dots.forEach((d, k) => d.classList.toggle('is-on', k === i));
      const href = slides[i].getAttribute('href');
      if (name) name.textContent = slides[i].dataset.name || '';
      if (file) file.setAttribute('href', href);
      if (open) open.setAttribute('href', href);
      cur = i;
    };
    const tick = () => { if (!paused && seen && !document.hidden) show(cur + 1); };
    const play = () => { if (!reduce) { clearInterval(timer); timer = setInterval(tick, 6000); } };
    $('[data-reel-next]', reel)?.addEventListener('click', () => { show(cur + 1); play(); });
    $('[data-reel-prev]', reel)?.addEventListener('click', () => { show(cur - 1); play(); });
    dots.forEach((d) => d.addEventListener('click', () => { show(+d.dataset.goto); play(); }));
    reel.addEventListener('pointerenter', () => { paused = true; });
    reel.addEventListener('pointerleave', () => { paused = false; });
    reel.addEventListener('focusin', () => { paused = true; });
    reel.addEventListener('focusout', () => { paused = false; });
    // a reel scrolled out of sight stands still: it loads no further
    // previews and changes nothing while the page below is being looked at
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(([e]) => { seen = e.isIntersecting; }, { threshold: 0.15 }).observe(reel);
    }
    hydrate(slides[1]);
    play();
  });

  /* ---- the sort menu: close on a click elsewhere or Escape ---- */
  document.addEventListener('click', (e) => {
    $$('details[data-menu][open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $$('details[data-menu][open]').forEach((d) => { d.open = false; d.querySelector('summary')?.focus(); });
  });

  /* ---- the search field: a phone's bar has room for one word ---- */
  const q = $('.g-search input[data-ph-short]');
  if (q) {
    const long = q.placeholder, narrow = matchMedia('(max-width: 639px)');
    const fit = () => { q.placeholder = narrow.matches ? q.dataset.phShort : long; };
    fit(); narrow.addEventListener?.('change', fit);
  }

  /* ---- a configured backdrop clip: only on a large, willing screen ---- */
  const vid = $('video[data-bg-video]');
  if (vid && !reduce && matchMedia('(min-width: 761px)').matches && !(navigator.connection && navigator.connection.saveData)) {
    vid.src = vid.dataset.src;
    vid.addEventListener('canplay', () => { vid.classList.add('is-on'); vid.play().catch(() => {}); }, { once: true });
  }

  /* ---- a tile waits in its photo's colours, not in grey ----
     The scan keeps each photo's palette; the tile carries its two strongest
     colours (data-tint) and shows them as a soft gradient until the
     thumbnail is decoded. Set through the CSSOM: the CSP allows no style
     attribute in the markup. */
  $$('.g-photo > a[data-tint]').forEach((a) => {
    const [c1, c2] = a.dataset.tint.split(' ');
    a.style.setProperty('--tint-1', c1);
    a.style.setProperty('--tint-2', c2 || c1);
  });

  /* ---- the photo page: original, keyboard, lightbox, in-place steps ----
     The arrows are links to the next photo's own page. With this script a
     step fetches that page and swaps its .g-page in (the photo, title, meta,
     panels) and the album data, pushes the address, and back/forward walk
     the same way. Anything that goes wrong falls back to the plain link. */
  if (!$('#stage-img')) return;
  const lb = $('#lightbox');
  let stage, data, at = -1, full = false;
  const url = (kind, rel) => `/${kind}/${rel.split('/').map(encodeURIComponent).join('/')}` +
    (data && data.stamps && data.stamps[rel] ? `?v=${data.stamps[rel]}` : '');
  const warm = (src) => { if (src) (new Image()).src = src; };

  const original = () => {
    if (!stage || stage.dataset.loaded) return;
    const stamp = $('#quality-stamp'), btn = $('#load-full-btn'), el = stage;
    el.classList.add('is-loading');
    const pic = new Image();
    pic.onload = () => {
      el.src = pic.src; el.dataset.loaded = '1'; el.classList.remove('is-loading');
      if (stamp) stamp.textContent = stamp.dataset.qOriginal;
      if (btn) btn.disabled = true;
    };
    pic.onerror = () => el.classList.remove('is-loading');
    pic.src = el.dataset.full;
  };

  // what belongs to the page that is showing; run again after every swap
  const bind = () => {
    stage = $('#stage-img');
    try { data = JSON.parse($('#album-data')?.textContent || 'null'); } catch { data = null; }
    at = data ? data.current : -1;
    $('#load-full-btn')?.addEventListener('click', original);
    $('#open-fullscreen-btn')?.addEventListener('click', openLb);
    // the photos either side, so the next step shows at once
    if (data && at >= 0) [at - 1, at + 1].forEach((k) => { if (data.rels[k]) warm(url('preview', data.rels[k])); });
    const box = stage.closest('.g-stage') || stage;
    // a swipe on the photo goes to the one before or after; a tap opens it big
    let sx = null, swiped = false;
    box.addEventListener('pointerdown', (e) => { sx = e.pointerType === 'mouse' ? null : e.clientX; swiped = false; });
    box.addEventListener('pointerup', (e) => {
      if (sx === null) return; const dx = e.clientX - sx; sx = null;
      if (Math.abs(dx) > 60) { swiped = true; $(dx < 0 ? '[data-next]' : '[data-prev]')?.click(); }
    });
    box.addEventListener('pointercancel', () => { sx = null; });
    stage.addEventListener('click', () => { if (!swiped) openLb(); swiped = false; });
  };

  let ticket = 0;
  const go = async (href, push) => {
    const mine = ++ticket;
    const page = $('.g-page');
    page?.setAttribute('aria-busy', 'true');
    try {
      const res = await fetch(href, { headers: { Accept: 'text/html' } });
      if (!res.ok) throw new Error(res.status);
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const next = $('.g-page', doc), nextData = $('#album-data', doc), img = $('#stage-img', next || doc);
      if (!next || !nextData || !img) throw new Error('not a photo page');
      // the new photo decoded before it is shown, so the stage never blanks
      const pre = new Image(); pre.src = img.getAttribute('src');
      await Promise.race([pre.decode().catch(() => {}), new Promise((r) => setTimeout(r, 2500))]);
      if (mine !== ticket) return;
      page.replaceWith(document.adoptNode(next));
      $('#album-data').textContent = nextData.textContent;
      document.title = doc.title;
      ['og:title', 'og:description', 'og:url', 'og:image'].forEach((k) => {
        const from = $(`meta[property="${k}"]`, doc), to = $(`meta[property="${k}"]`);
        if (from && to) to.setAttribute('content', from.getAttribute('content'));
      });
      if (push) history.pushState({ photo: true }, '', href);
      bind();
    } catch {
      if (mine === ticket) location.assign(href);
    }
  };
  history.replaceState({ photo: true }, '', location.href);
  addEventListener('popstate', (e) => { if (e.state && e.state.photo) go(location.href, false); });
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[data-prev], a[data-next]');
    if (!a || e.defaultPrevented || e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault(); go(a.href, true);
  });

  const paint = () => {
    if (!data || at < 0) return;
    const rel = data.rels[at], img = $('#lb-img');
    img.classList.add('is-loading');
    img.onload = () => img.classList.remove('is-loading');
    img.src = url(full ? 'full' : 'preview', rel);
    img.alt = rel.split('/').pop();
    $('#lb-title').textContent = rel;
    $('#lb-count').textContent = `${String(at + 1).padStart(2, '0')} / ${String(data.rels.length).padStart(2, '0')}`;
    const dl = $('#lb-dl'); dl.href = url('full', rel); dl.setAttribute('download', rel.split('/').pop());
    [at - 1, at + 1].forEach((k) => { if (data.rels[k]) warm(url('preview', data.rels[k])); });
  };
  function openLb() {
    if (!lb || !data || data.current < 0) return;
    at = data.current; full = !!stage.dataset.loaded; paint();
    lb.showModal();
    if (lb.requestFullscreen && matchMedia('(pointer: fine)').matches) lb.requestFullscreen().catch(() => {});
  }
  const step = (d) => { if (!data) return; at = (at + d + data.rels.length) % data.rels.length; paint(); };
  $('[data-lb-prev]')?.addEventListener('click', () => step(-1));
  $('[data-lb-next]')?.addEventListener('click', () => step(1));
  // closed on another photo than the page shows: the page follows. Run from
  // every way out (button, backdrop, Escape's close event), once.
  const follow = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (!data || at < 0 || at === data.current) return;
    const rel = data.rels[at]; at = data.current;
    go(`/image/${rel.split('/').map(encodeURIComponent).join('/')}${location.search}`, true);
  };
  const closeLb = () => { if (lb.open) lb.close(); follow(); };
  $('[data-lb-close]')?.addEventListener('click', closeLb);
  $('#lb-full')?.addEventListener('click', () => { full = true; paint(); });
  lb?.addEventListener('close', follow);
  lb?.addEventListener('click', (e) => { if (e.target === lb || e.target.classList.contains('g-lb__stage')) closeLb(); });

  // swipe in the lightbox
  let x0 = null;
  lb?.addEventListener('pointerdown', (e) => { x0 = e.clientX; });
  lb?.addEventListener('pointerup', (e) => {
    if (x0 === null) return; const dx = e.clientX - x0; x0 = null;
    if (Math.abs(dx) > 60) step(dx < 0 ? 1 : -1);
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.closest?.('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (lb?.open) {
      if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
      return;
    }
    if (e.key === 'ArrowRight') $('[data-next]')?.click();
    else if (e.key === 'ArrowLeft') $('[data-prev]')?.click();
    else if (e.key === 'f') openLb();
  });
  bind();
})();
