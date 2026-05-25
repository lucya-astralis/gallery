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
  let s = __scramblers.get(el);
  if (!s){ s = new TextScrambler(el); __scramblers.set(el, s); }
  return s.setText(String(text));
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
    ...document.querySelectorAll('.welcome__title'),
    ...document.querySelectorAll('.section__doc b'),
    ...document.querySelectorAll('.notfound__title'),
    ...document.querySelectorAll('.crumb b'),
  ];
  window.__scrambleOnView(onViewEls);

  const crt = document.getElementById('crt-deck');
  if (!crt) return;
  const frames = Array.from(crt.querySelectorAll('.crt__frame'));
  if (frames.length === 0) return;

  const screen = crt.querySelector('.crt__screen');
  const osdName = crt.querySelector('.crt__osd-name');
  const chEl = crt.querySelector('[data-shuffle-idx]');
  const openLink = document.querySelector('[data-shuffle-open]');
  const AUTO_MS = 3000;
  const SWITCH_MS = 280;

  let current = 0;
  let switching = false;

  function setOsdFor(frame) {
    if (!osdName) return;
    const album = frame.dataset.album || '';
    const filename = frame.dataset.filename || '';
    const text = album + ' / ' + filename;
    osdName.dataset.scrambleTarget = text;
    if (window.__scrambleTo) window.__scrambleTo(osdName, text);
    else osdName.textContent = text;
  }

  function setChannel(idx) {
    if (!chEl) return;
    const next = String(idx + 1).padStart(2, '0');
    if (window.__scrambleTo) window.__scrambleTo(chEl, next);
    else chEl.textContent = next;
  }

  function switchTo(idx, dir = 1) {
    if (switching) return;
    const target = ((idx % frames.length) + frames.length) % frames.length;
    if (target === current && frames.length > 1) return;
    switching = true;
    crt.classList.add('is-switching');
    setTimeout(() => {
      frames[current].classList.remove('is-on');
      current = target;
      frames[current].classList.add('is-on');
      if (screen) screen.dataset.href = frames[current].href;
      if (openLink) openLink.href = frames[current].href;
      setOsdFor(frames[current]);
      setChannel(current);
    }, Math.round(SWITCH_MS * 0.45));
    setTimeout(() => {
      crt.classList.remove('is-switching');
      switching = false;
    }, SWITCH_MS + 40);
  }

  function advance() { switchTo(current + 1); }
  function regress() { switchTo(current - 1, -1); }

  // init: ensure first frame is on and OSD/channel match
  frames.forEach((f, i) => f.classList.toggle('is-on', i === current));
  setChannel(current);
  if (screen) screen.dataset.href = frames[current].href;

  let timer = setInterval(advance, AUTO_MS);
  const reset = () => { clearInterval(timer); timer = setInterval(advance, AUTO_MS); };

  const nextBtn = document.getElementById('shuffle-next');
  const prevBtn = document.getElementById('shuffle-prev');
  const refreshBtn = document.getElementById('shuffle-refresh');

  if (nextBtn) nextBtn.addEventListener('click', () => { advance(); reset(); });
  if (prevBtn) prevBtn.addEventListener('click', () => { regress(); reset(); });

  // clicking the screen opens the currently-on frame
  if (screen) {
    screen.addEventListener('click', (e) => {
      const link = e.target.closest('.crt__frame');
      if (link) return; // anchor handles it
      const href = screen.dataset.href;
      if (href) window.location.href = href;
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      const prevText = refreshBtn.textContent;
      refreshBtn.textContent = '… TUNING';
      crt.classList.add('is-switching');
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
        frames.forEach((f, i) => f.classList.toggle('is-on', i === 0));
        current = 0;
        setChannel(current);
        setOsdFor(frames[current]);
        if (screen) screen.dataset.href = frames[current].href;
        if (openLink) openLink.href = frames[current].href;
      } catch (e) {
        // ignore
      } finally {
        setTimeout(() => crt.classList.remove('is-switching'), SWITCH_MS);
        refreshBtn.disabled = false;
        refreshBtn.textContent = prevText;
        reset();
      }
    });
  }

  // pause auto-cycle while user hovers the viewport
  crt.addEventListener('mouseenter', () => clearInterval(timer));
  crt.addEventListener('mouseleave', reset);

  // keyboard: ← → cycles channels when CRT is in viewport
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') { regress(); reset(); }
    else if (e.key === 'ArrowRight') { advance(); reset(); }
  });
});

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  // when the lightbox is open, its own handler takes over
  const lb = document.getElementById('lightbox');
  if (lb && !lb.hidden) return;
  if (e.key === 'ArrowLeft') {
    const prev = document.querySelector('.nav-arrow.prev');
    if (prev) window.location.href = prev.href;
  } else if (e.key === 'ArrowRight') {
    const next = document.querySelector('.nav-arrow.next');
    if (next) window.location.href = next.href;
  } else if (e.key === 'Escape') {
    const crumb = document.querySelector('.crumb a:last-of-type');
    if (crumb) window.location.href = crumb.href;
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('load-full-btn');
  const img = document.getElementById('stage-img');
  const loader = document.getElementById('stage-loader');
  const stamp = document.getElementById('quality-stamp');
  if (!btn || !img || !loader) return;

  btn.addEventListener('click', () => {
    const fullUrl = img.dataset.full;
    if (!fullUrl) return;
    loader.classList.add('is-loading');
    btn.textContent = 'Loading…';

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
      btn.textContent = 'Error — retry?';
    };
    full.src = fullUrl;
  });

  // swipe gestures on the stage → use existing prev/next nav-arrow hrefs
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
    stage.addEventListener('touchend', (e) => {
      if (!sTracking) return;
      sTracking = false;
      const t = e.changedTouches[0];
      const dx = t.clientX - sStartX;
      const dy = t.clientY - sStartY;
      const dt = Date.now() - sStartT;
      if (dt > 700) return;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
      // suppress the synthetic click so the lightbox doesn't open mid-navigation
      e.preventDefault();
      const link = dx < 0
        ? document.querySelector('.nav-arrow.next')
        : document.querySelector('.nav-arrow.prev');
      if (link) window.location.href = link.href;
    }, { passive: false });
  }
});

// ---------- LIGHTBOX -------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const lb = document.getElementById('lightbox');
  const dataEl = document.getElementById('album-data');
  if (!lb || !dataEl) return;

  let data;
  try { data = JSON.parse(dataEl.textContent); }
  catch (e) { return; }
  if (!data || !Array.isArray(data.rels) || data.rels.length === 0) return;

  // Reparent to body so the lightbox escapes <main>'s stacking context
  // (main has z-index:10, which would cap our z:1000 under body::after's
  // scanline layer at z:200). Living directly under body, z:1000 wins.
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
  const stageImg = document.getElementById('stage-img');
  const fsBtn = document.getElementById('open-fullscreen-btn');

  const rels = data.rels;
  const total = rels.length;
  let index = Math.max(0, Math.min(data.current | 0, total - 1));
  const initialIndex = index;
  let showingFull = false;

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
    fullBtn.textContent = 'Load original';
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

    // update URL bar to reflect the currently-viewed image
    try { history.replaceState(null, '', '/image/' + rel); } catch(e){}
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

  // open triggers
  if (stageImg) stageImg.addEventListener('click', (e) => {
    e.preventDefault();
    open();
  });
  if (fsBtn) fsBtn.addEventListener('click', open);

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
    fullBtn.textContent = 'Loading…';
    const full = new Image();
    full.onload = () => {
      imgEl.src = fullUrl;
      showingFull = true;
      bar.classList.remove('is-loading-full');
      bar.classList.add('is-full');
    };
    full.onerror = () => {
      bar.classList.remove('is-loading-full');
      fullBtn.textContent = 'Error — retry?';
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

  // swipe gestures
  let tStartX = 0, tStartY = 0, tStartT = 0, tracking = false;
  stage.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) return;
    tracking = true;
    tStartX = e.touches[0].clientX;
    tStartY = e.touches[0].clientY;
    tStartT = Date.now();
  }, { passive: true });
  stage.addEventListener('touchend', (e) => {
    if (!tracking) return;
    tracking = false;
    const t = e.changedTouches[0];
    const dx = t.clientX - tStartX;
    const dy = t.clientY - tStartY;
    const dt = Date.now() - tStartT;
    if (dt > 700) return;
    if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) navigate(1); else navigate(-1);
  }, { passive: true });
});
