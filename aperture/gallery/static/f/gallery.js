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
    let cur = 0, timer = null, paused = false;
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
    const tick = () => { if (!paused && !document.hidden) show(cur + 1); };
    const play = () => { if (!reduce) { clearInterval(timer); timer = setInterval(tick, 6000); } };
    $('[data-reel-next]', reel)?.addEventListener('click', () => { show(cur + 1); play(); });
    $('[data-reel-prev]', reel)?.addEventListener('click', () => { show(cur - 1); play(); });
    dots.forEach((d) => d.addEventListener('click', () => { show(+d.dataset.goto); play(); }));
    reel.addEventListener('pointerenter', () => { paused = true; });
    reel.addEventListener('pointerleave', () => { paused = false; });
    reel.addEventListener('focusin', () => { paused = true; });
    reel.addEventListener('focusout', () => { paused = false; });
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

  /* ---- a configured backdrop clip: only on a large, willing screen ---- */
  const vid = $('video[data-bg-video]');
  if (vid && !reduce && matchMedia('(min-width: 761px)').matches && !(navigator.connection && navigator.connection.saveData)) {
    vid.src = vid.dataset.src;
    vid.addEventListener('canplay', () => { vid.classList.add('is-on'); vid.play().catch(() => {}); }, { once: true });
  }

  /* ---- the photo page: original, keyboard, lightbox ---- */
  const stage = $('#stage-img');
  if (!stage) return;
  const stamp = $('#quality-stamp');
  const loadFull = $('#load-full-btn');
  const original = () => {
    if (stage.dataset.loaded) return;
    stage.classList.add('is-loading');
    const pic = new Image();
    pic.onload = () => {
      stage.src = pic.src; stage.dataset.loaded = '1'; stage.classList.remove('is-loading');
      if (stamp) stamp.textContent = stamp.dataset.qOriginal;
      if (loadFull) loadFull.disabled = true;
    };
    pic.onerror = () => stage.classList.remove('is-loading');
    pic.src = stage.dataset.full;
  };
  loadFull?.addEventListener('click', original);

  let data = null;
  try { data = JSON.parse($('#album-data')?.textContent || 'null'); } catch { data = null; }
  const lb = $('#lightbox');
  const url = (kind, rel) => `/${kind}/${rel.split('/').map(encodeURIComponent).join('/')}` +
    (data && data.stamps && data.stamps[rel] ? `?v=${data.stamps[rel]}` : '');
  let at = data ? data.current : -1, full = false;
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
    // warm the neighbours
    [at - 1, at + 1].forEach((k) => { if (data.rels[k]) (new Image()).src = url('preview', data.rels[k]); });
  };
  const openLb = () => {
    if (!lb || !data || at < 0) return;
    full = !!stage.dataset.loaded; paint();
    lb.showModal();
    if (lb.requestFullscreen && matchMedia('(pointer: fine)').matches) lb.requestFullscreen().catch(() => {});
  };
  const step = (d) => { if (!data) return; at = (at + d + data.rels.length) % data.rels.length; paint(); };
  $('#open-fullscreen-btn')?.addEventListener('click', openLb);
  stage.addEventListener('click', openLb);
  $('[data-lb-prev]')?.addEventListener('click', () => step(-1));
  $('[data-lb-next]')?.addEventListener('click', () => step(1));
  $('[data-lb-close]')?.addEventListener('click', () => lb.close());
  $('#lb-full')?.addEventListener('click', () => { full = true; paint(); });
  lb?.addEventListener('close', () => { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); });
  lb?.addEventListener('click', (e) => { if (e.target === lb || e.target.classList.contains('g-lb__stage')) lb.close(); });

  // swipe in the lightbox
  let x0 = null;
  lb?.addEventListener('pointerdown', (e) => { x0 = e.clientX; });
  lb?.addEventListener('pointerup', (e) => {
    if (x0 === null) return; const dx = e.clientX - x0; x0 = null;
    if (Math.abs(dx) > 60) step(dx < 0 ? 1 : -1);
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select')) return;
    if (lb?.open) {
      if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
      return;
    }
    if (e.key === 'ArrowRight') $('[data-next]')?.click();
    else if (e.key === 'ArrowLeft') $('[data-prev]')?.click();
    else if (e.key === 'f') openLb();
  });
})();
