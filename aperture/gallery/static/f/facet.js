/* SPDX-License-Identifier: MPL-2.0 · Copyright © 2026 lucya.systems — https://lucya.systems */
/* FACET — behaviour that CSS cannot do alone.
   Every effect here is a progressive enhancement: without this file the
   components still render and work; they just move less. */
(() => {
  const root = document.documentElement;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');

  /* --i for every child of [data-stagger] */
  const stagger = (scope = document) => scope.querySelectorAll('[data-stagger]').forEach(g =>
    [...g.children].forEach((c, i) => c.style.setProperty('--i', i)));

  /* Glass sheen follows the pointer (rule 3). Writes --mx/--my in px. */
  const sheen = e => {
    const el = e.target.closest?.('.f-glass');
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', `${e.clientX - r.left}px`);
    el.style.setProperty('--my', `${e.clientY - r.top}px`);
  };

  /* Ripple from the press point on any .f-btn */
  const ripple = e => {
    const b = e.target.closest?.('.f-btn');
    if (!b || reduce.matches) return;
    const r = b.getBoundingClientRect(), d = Math.hypot(r.width, r.height) * 2;
    const s = document.createElement('span');
    s.className = 'f-ripple';
    Object.assign(s.style, { width: `${d}px`, height: `${d}px`, left: `${e.clientX - r.left}px`, top: `${e.clientY - r.top}px` });
    b.append(s);
    s.addEventListener('animationend', () => s.remove(), { once: true });
  };

  /* Sliding indicators: segmented controls and tabs */
  const place = (thumb, target, host) => {
    if (!thumb || !target) return;
    const a = host.getBoundingClientRect(), b = target.getBoundingClientRect();
    thumb.style.setProperty('--x', `${b.left - a.left}px`);
    thumb.style.setProperty('--w', `${b.width}px`);
  };
  const seg = s => place(s.querySelector('.f-seg__thumb'), s.querySelector('input:checked + label'), s);
  const tabs = t => place(t.querySelector('.f-tabs__ink'), t.querySelector('[aria-selected="true"]'), t);
  const layout = () => { document.querySelectorAll('.f-seg').forEach(seg); document.querySelectorAll('.f-tabs').forEach(tabs); };

  /* Count-up: <span data-count="1.84" data-decimals="2">0</span> */
  const fmt = (v, d) => v.toLocaleString(root.lang || 'de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
  const countUp = el => {
    const to = parseFloat(el.dataset.count), d = +(el.dataset.decimals || 0);
    if (reduce.matches) { el.textContent = fmt(to, d); return; }
    const t0 = performance.now(), dur = 1400;
    const step = t => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      el.textContent = fmt(to * e, d);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  /* Theme: light / dark / system, with a circular reveal from the toggle */
  const THEMES = ['system', 'light', 'dark'];
  const currentTheme = (storedOnly = false) => {
    let s = null;
    try { s = localStorage.getItem('facet-theme'); } catch {}
    return storedOnly ? s : (s || root.dataset.theme || 'system');
  };
  const labelTheme = t => document.querySelectorAll('[data-theme-label]').forEach(n =>
    n.textContent = { system: 'System', light: 'Hell', dark: 'Dunkel' }[t] || 'System');
  const applyTheme = t => {
    if (t === 'system') delete root.dataset.theme; else root.dataset.theme = t;
    try { localStorage.setItem('facet-theme', t); } catch {}
    labelTheme(t);
  };
  const setTheme = (t, from) => {
    if (!document.startViewTransition || reduce.matches || !from) return applyTheme(t);
    const r = from.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    document.startViewTransition(() => applyTheme(t)).ready.then(() =>
      root.animate({ clipPath: [`circle(0 at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
        { duration: 650, easing: 'cubic-bezier(.16,1,.3,1)', pseudoElement: '::view-transition-new(root)' }));
  };

  /* Toasts: Facet.toast('Gespeichert') */
  const toast = (msg, { tone = 'ok', ms = 3200 } = {}) => {
    let host = document.querySelector('.f-toasts');
    if (!host) { host = document.createElement('div'); host.className = 'f-toasts'; host.setAttribute('role', 'status'); document.body.append(host); }
    const t = document.createElement('div');
    t.className = 'f-toast f-glass f-glass--hi';
    t.innerHTML = `<span class="f-status f-status--${tone}"></span><span></span><button class="f-btn f-btn--ghost f-btn--sm" aria-label="Schließen">✕</button>`;
    t.children[1].textContent = msg;
    const close = () => { t.classList.add('is-out'); t.addEventListener('animationend', () => t.remove(), { once: true }); };
    t.querySelector('button').onclick = close;
    host.append(t);
    setTimeout(close, ms);
  };

  /* Replay an entrance: Facet.replay(el) restarts every animation inside */
  const replay = el => el.getAnimations({ subtree: true }).forEach(a => { a.cancel(); a.play(); });

  const init = () => {
    /* Only a stored choice touches data-theme; otherwise whatever the host
       page (or the OS) decided stays in charge. */
    const stored = currentTheme(true);
    if (stored) applyTheme(stored); else labelTheme(currentTheme());
    stagger();
    layout();
    document.querySelectorAll('[data-count]').forEach(countUp);
    document.addEventListener('pointermove', sheen, { passive: true });
    document.addEventListener('pointerdown', ripple);
    document.addEventListener('change', e => { const s = e.target.closest('.f-seg'); if (s) seg(s); });
    document.addEventListener('click', e => {
      const tab = e.target.closest('.f-tabs [role="tab"]');
      if (tab) { tab.parentElement.querySelectorAll('[role="tab"]').forEach(b => b.setAttribute('aria-selected', b === tab)); tabs(tab.parentElement); }
      const tg = e.target.closest('[data-theme-toggle]');
      if (tg) setTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % 3], tg);
    });
    addEventListener('resize', layout, { passive: true });
    document.fonts?.ready.then(layout);
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
  window.Facet = { toast, setTheme, replay, countUp, layout, stagger };
})();
