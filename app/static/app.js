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

  const deck = document.getElementById('shuffle-deck');
  if (!deck) return;
  const cards = Array.from(deck.querySelectorAll('.shuffle-card'));
  if (cards.length === 0) return;

  const order = cards.map((_, i) => i);
  const AUTO_MS = 3000;

  function applyStack({ animate = true, snapIdx = null } = {}) {
    order.forEach((idx, pos) => {
      const card = cards[idx];
      const depth = pos;
      const ox = depth * 6;
      const oy = depth * -5;
      const rot = depth * -0.9;
      const sc = 1 - depth * 0.025;
      const op = depth > 4 ? 0 : 1;
      const snap = !animate || idx === snapIdx;
      if (snap) {
        const prev = card.style.transition;
        card.style.transition = 'none';
        card.style.setProperty('--ox', ox);
        card.style.setProperty('--oy', oy);
        card.style.setProperty('--rot', rot);
        card.style.setProperty('--sc', sc.toFixed(3));
        card.style.setProperty('--op', op);
        card.style.setProperty('--z', 100 - depth);
        card.classList.toggle('is-top', pos === 0);
        card.offsetWidth;
        card.style.transition = prev;
      } else {
        card.style.setProperty('--ox', ox);
        card.style.setProperty('--oy', oy);
        card.style.setProperty('--rot', rot);
        card.style.setProperty('--sc', sc.toFixed(3));
        card.style.setProperty('--op', op);
        card.style.setProperty('--z', 100 - depth);
        card.classList.toggle('is-top', pos === 0);
      }
    });
  }

  function advance() {
    if (order.length < 2) return;
    const topIdx = order[0];
    const topCard = cards[topIdx];
    const dir = Math.random() > 0.5 ? 1 : -1;
    topCard.classList.add('is-flying');
    topCard.style.setProperty('--ox', dir * 120);
    topCard.style.setProperty('--oy', -20);
    topCard.style.setProperty('--rot', dir * 6);
    topCard.style.setProperty('--sc', 0.96);
    topCard.style.setProperty('--op', 0);
    topCard.style.setProperty('--z', 200);
    setTimeout(() => {
      order.shift();
      order.push(topIdx);
      topCard.classList.remove('is-flying');
      applyStack({ animate: true, snapIdx: topIdx });
      onTopChanged();
    }, 380);
  }

  function onTopChanged() {
    const topIdx = order[0];
    const topCard = cards[topIdx];
    const idxEl = deck.querySelector('[data-shuffle-idx]');
    if (idxEl) {
      const next = String(topIdx + 1).padStart(2, '0');
      if (window.__scrambleTo) window.__scrambleTo(idxEl, next);
      else idxEl.textContent = next;
    }
    if (window.__scrambleTo) {
      const name = topCard.querySelector('[data-scramble]');
      if (name) {
        const target = name.dataset.scrambleTarget || name.textContent;
        window.__scrambleTo(name, target);
      }
    }
  }

  applyStack({ animate: false });

  let timer = setInterval(advance, AUTO_MS);
  const reset = () => { clearInterval(timer); timer = setInterval(advance, AUTO_MS); };

  const nextBtn = document.getElementById('shuffle-next');
  const refreshBtn = document.getElementById('shuffle-refresh');

  if (nextBtn) nextBtn.addEventListener('click', () => { advance(); reset(); });

  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      const prevText = refreshBtn.textContent;
      refreshBtn.textContent = '… LOADING';
      try {
        const resp = await fetch('/api/shuffle?limit=' + cards.length);
        if (!resp.ok) throw new Error('bad status');
        const items = await resp.json();
        items.slice(0, cards.length).forEach((item, i) => {
          const card = cards[i];
          card.href = '/image/' + item.rel_path;
          card.dataset.rel = item.rel_path;
          const img = card.querySelector('img');
          if (img) {
            img.src = '/preview/' + item.rel_path;
            img.alt = item.filename;
          }
          const name = card.querySelector('.shuffle-card__name');
          if (name) {
            const text = item.album + ' / ' + item.filename;
            name.dataset.scrambleTarget = text;
            name.textContent = text;
          }
        });
        // restore natural order after reshuffle
        order.length = 0;
        for (let i = 0; i < cards.length; i++) order.push(i);
        applyStack({ animate: true });
        onTopChanged();
      } catch (e) {
        // ignore
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = prevText;
        reset();
      }
    });
  }

  // pause auto-shuffle while user hovers the deck
  deck.addEventListener('mouseenter', () => clearInterval(timer));
  deck.addEventListener('mouseleave', reset);
});

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
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
});
