/* lucya.systems aperture — console client.
 *
 * Three moving parts: the album tree on the left, a tabbed editor on the
 * right, and one photo browser reused by both the Photos tab and the picker
 * modal. Config edits collect into `state.edits` and only reach disk on Save,
 * so a half-finished list never lands in a cfg the gallery is reading live.
 * Tag edits are the exception — they apply straight away, because bulk
 * tagging forty photos is not something to stage and forget.
 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  meta: null,
  tree: null,
  issuesByAlbum: {},
  sel: null,          // {kind: 'home'|'gallery'|'links'|'ops'|'album', album}
  data: null,         // payload for the current selection
  edits: {},          // config key -> value staged for the next save
  tab: 'settings',
  descLang: 'en',
  vocab: [],          // every tag in use, for autocomplete
  browse: null,       // Photos-tab browser state
  openPaths: new Set(),
  // Settings view: a filter over the key list, "only what this file sets",
  // whether the per-key help is drawn, and which groups are folded shut.
  query: '',
  setOnly: false,
  showHelp: true,
  collapsed: new Set(),
};

const READ_ONLY = document.documentElement.dataset.readOnly === '1';

/* The three view switches are preferences, not data — they belong to the
 * person, not the album, so they outlive a reload. Everything else here is
 * derived from the server on boot. */
const PREFS_KEY = 'cfgtool.view';

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
    if (typeof saved.setOnly === 'boolean') state.setOnly = saved.setOnly;
    if (typeof saved.showHelp === 'boolean') state.showHelp = saved.showHelp;
    if (Array.isArray(saved.collapsed)) state.collapsed = new Set(saved.collapsed);
  } catch (_) { /* a broken or blocked store just means defaults */ }
  syncHelpClass();
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      setOnly: state.setOnly,
      showHelp: state.showHelp,
      collapsed: [...state.collapsed],
    }));
  } catch (_) { /* private mode: the switches just don't persist */ }
}

function syncHelpClass() {
  document.documentElement.classList.toggle('no-help', !state.showHelp);
}

/* ----- helpers ---------------------------------------------------------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

function toast(message, kind = 'ok') {
  const box = $('#toast');
  box.textContent = message;
  box.className = 'toast is-' + kind;
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, kind === 'err' ? 6000 : 2600);
}

/* ----- the session ------------------------------------------------------
 * The console is cookie-authenticated, so a cross-site form could aim a write
 * at it with the operator's own cookie attached. The server therefore wants a
 * token that only a page which can READ this origin could know, and every
 * mutating request carries it in a header. It is fetched once at start-up and
 * refreshed whenever the server says it is stale.
 *
 * When there is no password configured the token is an empty string and the
 * server does not ask for one — the origin check still applies. */
let CSRF = '';

async function refreshSession() {
  try {
    const res = await fetch('/api/session', { headers: { Accept: 'application/json' } });
    if (!res.ok) return false;
    const state = await res.json();
    CSRF = state.csrf || '';
    return true;
  } catch (_) {
    return false;
  }
}

/* A session that has timed out is not an error to report in a toast — it is a
 * different page. Sending the operator back to the door beats a form that
 * silently stops saving.
 *
 * Two things travel with the bounce. The REASON, so the door can say why it
 * is there rather than presenting a blank form to someone who was working
 * thirty seconds ago; it is a key the server looks up in an allowlist, never
 * text, because it rides in on a query string. And, for a timeout only, a
 * RETURN NOTE: the console has no router, so the screen you were on is not
 * in the URL and is simply lost across a sign-in unless something writes it
 * down. Signing out on purpose does NOT leave one — that is a decision to
 * stop, and dropping the next person back into the same album would be
 * ignoring it. */
const RETURN_KEY = 'cfgtool.return';

function toLogin(reason = 'timeout') {
  if (reason === 'timeout' && state.sel) {
    /* sessionStorage, not local: the note belongs to THIS tab and to this
     * one trip through the door. One-shot — boot() deletes it on the way
     * back out, so a later plain reload still lands on home. */
    try { sessionStorage.setItem(RETURN_KEY, JSON.stringify(state.sel)); } catch (_) { /* fine */ }
  }
  window.location.replace('/login?reason=' + encodeURIComponent(reason));
}

/* The note, consumed. Validated against the tree that has just loaded rather
 * than trusted: an album can have been renamed or deleted while the door was
 * open, and select() on a path that is no longer there is a broken screen. */
function takeReturnNote() {
  let raw = null;
  try {
    raw = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
  } catch (_) { return null; }
  if (!raw) return null;
  let sel = null;
  try { sel = JSON.parse(raw); } catch (_) { return null; }
  if (!sel || typeof sel !== 'object') return null;
  if (['gallery', 'ops', 'home', 'links'].includes(sel.kind)) {
    return { kind: sel.kind };
  }
  if (sel.kind === 'album' && typeof sel.album === 'string' && albumExists(sel.album)) {
    return { kind: 'album', album: sel.album };
  }
  return null;
}

function albumExists(path) {
  const stack = state.tree ? [state.tree] : [];
  while (stack.length) {
    const node = stack.pop();
    if (node.path === path) return true;
    for (const child of node.children || []) stack.push(child);
  }
  return false;
}

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  if (method !== 'GET' && method !== 'HEAD' && CSRF) {
    headers['X-Aperture-CSRF'] = CSRF;
  }
  const res = await fetch(path, { ...options, method, headers });
  if (res.status === 401) { toLogin('timeout'); throw new Error('signed out'); }
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* empty body */ }
  if (res.status === 403 && payload && /csrf/i.test(payload.detail || '')) {
    /* The token rotated under us (the server restarted, or the password was
     * changed). Fetch a fresh one and let the caller retry once. */
    await refreshSession();
  }
  if (!res.ok) {
    const detail = (payload && payload.detail) || res.statusText;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

/* Up the whole ladder: this stopped at MB, so a 31 GB archive read
 * "31985.1 MB" on every screen that showed its size. */
const bytes = (n) => {
  if (!n) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let step = 0;
  while (n >= 1024 && step < units.length - 1) { n /= 1024; step += 1; }
  return (step === 0 ? n : step === 1 ? Math.round(n) : n.toFixed(1)) + ' ' + units[step];
};

/* Previews always come from /api/thumb: the gallery's own grid thumbnail,
 * the file /thumb serves visitors. A grid of tiles must never pull the
 * full-size originals, and every tile here is drawn smaller than THUMB_SIZE. */
const thumbUrl = (rel) => '/api/thumb?path=' + encodeURIComponent(rel);

function splitPath(value) {
  const i = String(value).lastIndexOf('/');
  return i < 0 ? ['', String(value)] : [String(value).slice(0, i + 1), String(value).slice(i + 1)];
}

const strip = (v) => String(v).replace(/^\/+/, '');

/* ----- drawer ----------------------------------------------------------- */
/* Below 900px the album tree is an overlay rather than a column: as a 38vh
 * band above the editor it ate a third of a phone screen on every page and
 * still only showed four albums. It slides in over the pane instead, and
 * closes the moment an album is picked — the choice is the whole errand. */
function drawerOpen() {
  return document.body.classList.contains('drawer-open');
}

function setDrawer(open) {
  document.body.classList.toggle('drawer-open', open);
  $('#scrim').hidden = !open;
  $('#btn-menu').setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) $('#tree-filter').focus();
}

const isNarrow = () => window.matchMedia('(max-width: 900px)').matches;

/* ----- boot ------------------------------------------------------------- */
async function boot() {
  loadPrefs();
  // Before anything else: the CSRF token every later write has to carry.
  await refreshSession();
  try {
    state.meta = await api('/api/meta');
    await Promise.all([loadTree(), loadVocab()]);
    /* Home is the landing screen on purpose — the console stopped being a
     * form you land in the middle of. The one exception is coming back
     * through the door after a timeout, which nobody chose. */
    select(takeReturnNote() || { kind: 'home' });
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: 'Cannot reach the backend: ' + err.message }));
  }
  /* The boot screen is waiting on this: it covers the four "Loading..."
   * strings above, so it has to know when there is something behind it. Sent
   * on the failure path too — an error message is also something to read,
   * and a splash that hangs over one is worse than no splash. */
  document.dispatchEvent(new Event('aperture:ready'));
  $('#btn-reload').addEventListener('click', async () => {
    photoCache.clear();
    await Promise.all([loadTree(), loadVocab()]);
    if (state.sel) select(state.sel, true);
    toast('Reloaded');
  });
  $('#btn-check').addEventListener('click', () => { setDrawer(false); checkAll(); });
  $$('.place').forEach((button) => button.addEventListener('click', () => {
    setDrawer(false);
    select({ kind: button.dataset.place });
  }));
  const signout = $('#btn-signout');
  if (signout) {
    signout.addEventListener('click', async () => {
      try { await api('/api/session', { method: 'DELETE' }); } catch (_) { /* going anyway */ }
      toLogin('signout');
    });
  }
  $('#tree-filter').addEventListener('input', renderTree);
  $('#btn-menu').addEventListener('click', () => setDrawer(!drawerOpen()));
  $('#btn-menu-close').addEventListener('click', () => setDrawer(false));
  $('#scrim').addEventListener('click', () => setDrawer(false));
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && drawerOpen()) setDrawer(false);
  });
  // The pane's own header sticks; past the first line of scroll it drops the
  // file path and shrinks, so a long settings page keeps its tabs without
  // spending 90px on them.
  wirePaneShrink();
  wireModal();
}

function wirePaneShrink() {
  const pane = $('#pane');
  let pending = false;
  pane.addEventListener('scroll', () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const scrolled = pane.scrollTop > 18;
      pane.classList.toggle('is-scrolled', scrolled);
      // the gallery brightens its nav hairline the moment the bar floats
      // over content; here the pane is the thing that scrolls under it
      $('.nav').classList.toggle('nav--scrolled', scrolled);
    });
  }, { passive: true });
}

async function loadTree() {
  state.tree = (await api('/api/tree')).root;
  renderTree();
}

async function loadVocab() {
  try {
    state.vocab = (await api('/api/tags')).tags;
  } catch (_) { state.vocab = []; }
}

/* ----- album tree ------------------------------------------------------- */
function renderTree() {
  const list = $('#tree');
  const filter = $('#tree-filter').value.trim().toLowerCase();
  list.innerHTML = '';

  /* Albums, and nothing else. Operations and gallery.cfg used to sit at the
   * top of this list, where a tool and a file read as two odd albums; they
   * are places now, in the nav. */
  if (!state.tree) return;
  for (const child of state.tree.children) {
    const item = renderNode(child, 1, filter);
    if (item) list.append(item);
  }
  if (!list.children.length) {
    list.append(el('li', { class: 'tree__empty',
                          text: filter ? 'No album matches.' : 'No albums yet.' }));
  }
  /* With a filter typed it reads "3 / 31": how much of the tree is in front
   * of you, and how much there is. */
  const count = $('#tree-count');
  if (count) {
    const total = countAlbums(state.tree);
    const shown = list.querySelectorAll('.tree__row').length;
    count.textContent = filter ? shown + ' / ' + total : String(total);
  }
}

/* Every folder below the root that can carry an album.cfg. */
function countAlbums(node) {
  return (node.children || []).reduce((n, child) => n + 1 + countAlbums(child), 0);
}

/* Which place is lit. The album tree lights its own row, and an album is not
 * a place -- so on an album none of the three is active, which is the truth:
 * you are in the sidebar's world, not in one of theirs. */
function syncPlaces() {
  const kind = state.sel ? state.sel.kind : null;
  $$('.place').forEach((button) => {
    const on = button.dataset.place === kind;
    button.classList.toggle('is-active', on);
    button.setAttribute('aria-current', on ? 'true' : 'false');
  });
}

function renderNode(node, depth, filter) {
  const selfMatch = !filter || node.path.toLowerCase().includes(filter);
  const kids = node.children
    .map((c) => renderNode(c, depth + 1, selfMatch ? '' : filter))
    .filter(Boolean);
  if (!selfMatch && !kids.length) return null;

  const active = state.sel && state.sel.kind === 'album' && state.sel.album === node.path;
  const open = filter ? true : state.openPaths.has(node.path);
  const errored = (state.issuesByAlbum[node.path] || 0) > 0;

  /* Depth as an ATTRIBUTE, not a style. This console serves itself under
   * `style-src 'self'`, so the inline padding that used to sit here was
   * dropped by the browser and every sub-album has been rendered flush left
   * -- while the CSP logged a refusal per row. */
  const row = el('div', {
    class: 'tree__row' + (active ? ' is-active' : ''),
    'data-depth': String(Math.min(depth, 8)),
    title: node.path,
    onclick: () => select({ kind: 'album', album: node.path }),
  },
    el('span', {
      class: 'tree__twisty' + (kids.length ? '' : ' is-leaf') + (open ? ' is-open' : ''),
      text: '▶',
      onclick: (ev) => {
        ev.stopPropagation();
        if (state.openPaths.has(node.path)) state.openPaths.delete(node.path);
        else state.openPaths.add(node.path);
        renderTree();
      },
    }),
    el('span', {
      class: 'tree__cover' + (errored ? ' has-err' : node.has_cfg ? ' has-cfg' : ''),
      title: errored ? 'has config issues' : node.has_cfg ? 'has an album.cfg' : '',
    }, node.cover
      ? el('img', { src: thumbUrl(node.cover), alt: '', loading: 'lazy',
                    onerror: (ev) => { ev.target.remove(); } })
      : null),
    el('span', { class: 'tree__name', text: node.name }),
    el('span', { class: 'tree__count', text: String(node.total_photos || '') }));

  const item = el('li', {}, row);
  if (kids.length && open) item.append(el('ul', {}, kids));
  return item;
}

/* ----- selection -------------------------------------------------------- */
async function select(sel, keepTab = false) {
  if (!keepTab && state.sel && dirty() &&
      !confirm('Discard the unsaved changes on this page?')) return;
  state.sel = sel;
  state.edits = {};
  state.browse = null;
  /* An album lands on its photos: the settings are twenty-one keys, and
   * looking at what is in the folder is the more common errand. The
   * gallery's own file has no photos to land on. */
  if (!keepTab) { state.tab = sel.kind === 'album' ? 'photos' : 'settings'; state.query = ''; }
  if (isNarrow()) setDrawer(false);
  if (sel.kind === 'album') {
    const parts = sel.album.split('/');
    for (let i = 1; i < parts.length; i++) state.openPaths.add(parts.slice(0, i).join('/'));
  }
  renderTree();
  syncPlaces();
  $('#pane').innerHTML = '';
  $('#pane').append(el('div', { class: 'pane__empty', text: 'Loading…' }));
  if (sel.kind === 'home') {
    await renderHome();
    return;
  }
  if (sel.kind === 'ops') {
    await renderOps();
    return;
  }
  if (sel.kind === 'links') {
    await renderLinks(sel.draft);
    return;
  }
  try {
    state.data = sel.kind === 'gallery'
      ? await api('/api/gallery')
      : await api('/api/album?path=' + encodeURIComponent(sel.album));
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: err.message }));
    return;
  }
  renderPane();
}

const dirty = () => Object.keys(state.edits).length > 0;

/* The theme keys — accent, font, font_scale, both wallpapers — are spelled
 * identically in album.cfg and gallery.cfg, but one tier down they name a
 * file in .gallery/ rather than in an .album/ and they dress the whole site
 * rather than one album. The server sends only that difference
 * (gallery_spec / gallery_help); everything else falls through, so no key
 * has to be described twice. */
function specFor(key) {
  const override = state.sel.kind === 'gallery' && state.meta.gallery_spec;
  return (override && override[key]) || state.meta.spec[key] || {};
}

function helpFor(key) {
  const override = state.sel.kind === 'gallery' && state.meta.gallery_help;
  return (override && override[key]) || state.meta.help[key] || '';
}

function value(key) {
  if (key in state.edits) return state.edits[key];
  const raw = state.data.values[key];
  if (raw === undefined) return null;
  const spec = specFor(key);
  const listy = ['photo_list', 'list', 'kv_list', 'welcome', 'album_list'].includes(spec.type);
  if (listy) return raw;
  // `joined` keys (loc) are one logical line the parser happened to split on
  // commas; the gallery rejoins them, so the editor shows them rejoined.
  if (spec.joined) return raw.join(', ');
  return raw.length ? raw[0] : '';
}

function setValue(key, next) {
  state.edits[key] = next;
  renderPane();
}

/* Re-rendering the pane on every keystroke would drop the caret out of the
 * field that triggered it. Inputs carry a stable `data-fk`, so focus and
 * selection can be put back on the freshly built node. */
function restoreFocus(fk, start, end) {
  if (!fk) return;
  const next = document.querySelector('[data-fk="' + CSS.escape(fk) + '"]');
  if (!next) return;
  next.focus();
  if (start !== null && next.setSelectionRange) {
    try { next.setSelectionRange(start, end); } catch (_) { /* not a text input */ }
  }
}

/* ----- pane ------------------------------------------------------------- */
/* Settings are grouped rather than dumped as one flat list of fifteen keys:
 * what an album *is*, how it presents itself, which photos it leans on, and
 * the editorial text. Each group is the answer to one question. */
const ALBUM_GROUPS = [
  ['The album', 'What it is called, and what this folder is to the gallery.', ['name', 'collection', 'showcase', 'cover']],
  ['Photos it leans on', 'Which photos get pulled out of the grid.', ['featured', 'order', 'reel', 'sort']],
  ['Look', 'Its own mark, title face, accent colour and page effect.',
    ['icon', 'font', 'font_scale', 'accent', 'effect']],
  ['Backdrop', 'What sits behind this album’s pages, and how the gallery treats it. Sub-albums inherit all four; leave them empty for the gallery’s default.',
    ['wallpaper', 'wallpaper_mobile', 'wallpaper_tint', 'wallpaper_dim']],
  ['Text & stats', 'What is written under the hero.', ['tags', 'loc', 'stat', 'stats']],
];

const GALLERY_GROUPS = [
  ['Identity', 'What this archive calls itself. All of it is yours to set; the “powered by” line in the gallery’s footer names the software instead and is not configurable. Leave it all empty and the gallery calls itself “Gallery” behind a neutral mark.',
    ['site_name', 'site_sub', 'site_hero', 'logo', 'favicon',
     'site_desc', 'site_desc_en', 'site_desc_de', 'site_desc_jp']],
  ['Look', 'The accent every page wears and the display face of the chrome — the wordmark, the welcome screen’s big word, the 404. Not an album’s hero title: that keeps its own `font`. An album that sets its own accent still wins for its pages.',
    ['accent', 'font', 'font_scale']],
  ['Backdrop', 'What sits behind every page, and how the gallery treats it — its own backdrop, and any an album brings. An album that sets these itself still wins for its pages.',
    ['wallpaper', 'wallpaper_mobile', 'wallpaper_tint', 'wallpaper_dim']],
  ['Operator & footer', 'The person behind the archive, the legal links, and the badge row. The footer’s operator card and the welcome screen’s “about me” button both need a URL to point at — without one, neither is rendered.',
    ['operator', 'operator_url', 'operator_pfp', 'privacy_url', 'imprint_url', 'badges']],
  ['Credit', 'Who took the photographs. Written as EXIF Artist/Copyright into every derived image — metadata only, nothing is drawn on a photo and the originals are never rewritten.',
    ['credit']],
  ['Welcome hero', 'Which photos cycle on the front page.', ['welcome_desktop', 'welcome_mobile', 'welcome']],
  ['Album list', 'The order and default sort on /albums.', ['album_order', 'album_sort']],
];

/* Where the pane was before the last re-render, and where it actually landed
 * afterwards — a scrollport too short for the old position silently clamps. */
let paneScroll = 0;
let paneScrollLanded = 0;

/* Markup that arrives after renderPane() has already returned (the photo grid
 * of a folder that still has to be fetched) calls this once it has height, so
 * the clamped scroll position can be restored. Left alone if the pane moved
 * in the meantime — that movement is the user scrolling. */
function keepPaneScroll() {
  const pane = $('#pane');
  if (!pane || pane.scrollTop !== paneScrollLanded) return;
  pane.scrollTop = paneScroll;
  paneScrollLanded = pane.scrollTop;
}

/* ============================================================
   OPERATIONS
   ------------------------------------------------------------
   The CLI's `status`, `scan`, `pause`/`resume` and `doctor`, in the browser.
   Nothing here talks to the indexer directly: a scan is a request written to
   the control channel and picked up by whichever process owns the indexer,
   which is the same path `python -m aperture.cli scan` takes. So this view
   works identically whether the gallery is in this process or in another
   container — and there is still exactly one place a scan can begin.
   ============================================================ */
const opsState = { status: null, doctor: null, busy: false, poll: null };

const ago = (ts) => {
  if (typeof ts !== 'number') return 'never';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
};

/* One row of a short list of facts: the label in the mono voice, the value
 * in tabular figures. Home and Operations both read the same status, so they
 * draw it with the same row. */
function fact(label, value, tone) {
  /* A PAIR, not a row in a wrapper: `.facts` already existed in this sheet
   * for a photo's metadata, with dt and dd as its own grid children. One
   * definition list, one look, whatever is being listed. */
  return [el('dt', { text: label }),
          el('dd', { class: tone ? 'is-' + tone : null, text: value })];
}

async function loadOpsStatus() {
  try {
    opsState.status = await api('/api/ops/status');
  } catch (err) {
    opsState.status = { error: err.message };
  }
}

async function renderOps() {
  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__empty', text: 'Reading the control channel…' }));
  await loadOpsStatus();
  if (state.sel.kind !== 'ops') return;   /* navigated away while we waited */
  paintOps();
}

function paintOps() {
  const pane = $('#pane');
  const st = opsState.status || {};
  const paths = st.paths || {};
  const idx = st.index || {};
  const scan = (st.server && st.server.last_scan) || null;
  const res = (scan && scan.result) || {};
  const live = !!st.live;
  const paused = !!st.paused;
  const scanning = !!(st.server && st.server.scanning);
  const ro = !!st.read_only;
  pane.innerHTML = '';

  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: st.control_dir || '' }),
      el('div', { class: 'head__line' },
        el('h1', { class: 'head__title', text: 'Operations' }),
        el('div', { class: 'head__meta' },
          el('span', { class: 'pill', text: 'role ' + (st.role || '—') }),
          ro ? el('span', { class: 'pill pill--warn', text: 'read-only' }) : null)))));

  if (st.error) {
    pane.append(el('div', { class: 'pane__empty', text: st.error }));
    return;
  }

  const grid = el('div', { class: 'home' });
  pane.append(grid);

  /* ----- the indexer, in full ----- */
  const tone = paused ? 'warn' : live ? 'ok' : 'bad';
  const word = paused ? 'paused' : scanning ? 'scanning' : live ? 'running' : 'not running';
  grid.append(card('Indexer', 'one writer, whoever asks',
    el('div', { class: 'lamp' },
      el('span', { class: 'lamp__dot is-' + tone }),
      el('span', { class: 'lamp__word is-' + tone, text: word }),
      el('span', { class: 'lamp__note', text: scanning
        ? 'triggered by ' + ((st.server && st.server.scan_trigger) || '?')
        : live ? '' : 'no heartbeat in the control file' })),
    el('dl', { class: 'facts' },
      paused && st.pause
        ? fact('paused', ago(st.pause.since) +
               (st.pause.reason ? ' — ' + st.pause.reason : ''), 'warn')
        : null,
      fact('last scan', scan
        ? ago(scan.finished_at) + ' · ' + (scan.trigger || '?') +
          (scan.seconds != null ? ' · ' + scan.seconds + 's' : '')
        : 'never'),
      fact('it did', scan ? scanSummary(res) : '—', res.failed ? 'warn' : null),
      scan && scan.error ? fact('last error', scan.error, 'bad') : null,
      fact('every', paths.scan_interval ? paths.scan_interval + 's' : 'manual only'),
      fact('watcher', paths.watcher ? 'on' : 'off'))));

  /* ----- what it has built ----- */
  grid.append(card('Index', 'what the scan has put in the database',
    el('div', { class: 'tiles' },
      tile('photos', String(idx.images ?? '—')),
      tile('albums', String(idx.albums ?? '—')),
      tile('featured', String(idx.featured ?? '—')),
      tile('tags', String(idx.tags ?? '—')),
      tile('originals', bytes(idx.bytes)),
      tile('database', bytes(idx.db_bytes)))));

  /* ----- where things are ----- */
  grid.append(card('Paths', 'read once at startup — aperture/runtime.py',
    el('dl', { class: 'facts' },
      fact('photos', paths.photos || '—'),
      fact('thumbnails', paths.thumbs || '—'),
      fact('previews', paths.previews || '—'),
      fact('data', paths.data || '—'),
      fact('control', st.control_dir || '—'))));

  /* ----- what it does to a photo ----- */
  /* Never on this screen before, and both of these decide what leaves the
   * server: how large a derivative is, and whether coordinates travel. */
  grid.append(card('Derivatives and privacy', 'what the scan makes, and drops',
    el('dl', { class: 'facts' },
      fact('thumbnail', (paths.thumb_size || '—') + ' px'),
      fact('preview', (paths.preview_size || '—') + ' px'),
      fact('hide gps', paths.hide_gps ? 'yes — never served in the API' : 'no'),
      fact('strip gps', paths.strip_gps
        ? 'yes — removed from the originals on scan' : 'no'))));

  /* ----- the buttons ----- */
  const albumField = el('input', {
    type: 'text', id: 'ops-album', placeholder: 'whole gallery — or one album path',
    autocomplete: 'off', disabled: ro,
  });
  const forceBox = el('input', { type: 'checkbox', id: 'ops-force', disabled: ro });
  /* A field rather than a window.prompt(): a prompt is disabled outright in a
   * sandboxed frame, and a reason that shows up in `status` afterwards is
   * worth typing where you can see it. */
  const reasonField = el('input', {
    type: 'text', id: 'ops-reason', placeholder: 'why — shown in status while paused',
    autocomplete: 'off', disabled: ro || paused,
  });

  grid.append(el('div', { class: 'home__wide' }, card('Actions',
    'a request on the control channel — the same one the CLI writes',
    ro ? el('p', { class: 'card__quiet', text:
        'The console is mounted read-only. Nothing here can be started from the browser.' })
       : null,
    el('div', { class: 'ops__form' },
      albumField,
      el('label', { class: 'ops__check' }, forceBox,
        el('span', { text: 'force — re-derive even when mtimes say nothing changed' }))),
    paused ? null : el('div', { class: 'ops__form' }, reasonField),
    el('div', { class: 'card__actions' },
      el('button', {
        type: 'button', class: 'btn btn--primary', id: 'ops-scan',
        disabled: ro || opsState.busy, text: 'Scan now',
        onclick: () => startScan(albumField.value.trim(), forceBox.checked),
      }),
      el('button', {
        type: 'button', class: 'btn', disabled: ro || opsState.busy,
        text: paused ? 'Resume indexing' : 'Pause indexing',
        onclick: () => (paused ? doResume() : doPause(reasonField.value.trim())),
      }),
      el('button', {
        type: 'button', class: 'btn', disabled: opsState.busy, text: 'Run doctor',
        onclick: () => runDoctor(albumField.value.trim()),
      })),
    el('p', { class: 'card__quiet', text:
      'It starts within a couple of seconds if an indexer is listening, and ' +
      'waits if none is.' }))));

  if (opsState.doctor) {
    grid.append(el('div', { class: 'home__wide' }, renderDoctor(opsState.doctor)));
  }
}

function renderDoctor(report) {
  const problems = report.problems || {};
  const body = [el('dl', { class: 'facts' },
    fact('scope', report.scope || 'whole gallery'),
    fact('checked', report.photos_on_disk + ' file(s) on disk · ' +
                    report.rows + ' row(s) indexed'),
    fact('result', report.total ? report.total + ' problem(s) found' : 'no problems found',
         report.total ? 'warn' : 'ok'))];

  for (const check of Object.keys(problems).sort()) {
    const items = problems[check];
    body.push(el('h3', { class: 'card__sub' },
      el('span', { text: check.replace(/_/g, ' ') }),
      el('span', { class: 'card__sub-n', text: String(items.length) })));
    body.push(el('div', { class: 'hrows' }, items.slice(0, 25).map((item) => homeRow(
      item.rel_path || item.album || '—',
      item.key || '',
      item.detail || '',
      item.album ? () => select({ kind: 'album', album: item.album }) : null))));
    if (items.length > 25) {
      body.push(el('p', { class: 'card__quiet', text: (items.length - 25) + ' more' }));
    }
  }
  return card('Doctor', 'index, files, derivatives and cfg, checked against each other',
              ...body);
}

/* A scan is asynchronous by nature: on a large share over SMB a full pass is
 * minutes. So the request returns an id and this polls for its summary rather
 * than holding an HTTP request open across the whole thing. */
/* Home and Operations both draw the indexer, and an action can be started
 * from either. Repaint whichever is actually up -- painting Operations over
 * the home screen because that is where the code used to live is how a tool
 * teaches people not to trust its buttons. */
function repaintOps() {
  if (state.sel && state.sel.kind === 'ops') paintOps();
  else if (state.sel && state.sel.kind === 'home') paintHome();
}

async function startScan(album, force) {
  opsState.busy = true;
  repaintOps();
  try {
    const res = await api('/api/ops/scan', {
      method: 'POST',
      body: JSON.stringify({ album: album || null, force: !!force }),
    });
    toast(res.note || 'Scan requested');
    pollScan(res.request.id);
  } catch (err) {
    opsState.busy = false;
    toast(err.message, 'err');
    repaintOps();
  }
}

function pollScan(requestId) {
  clearInterval(opsState.poll);
  const started = Date.now();
  opsState.poll = setInterval(async () => {
    let body;
    try {
      body = await api('/api/ops/scan/' + encodeURIComponent(requestId));
    } catch (_) {
      return;   /* a hiccup is not a reason to give up on a running scan */
    }
    if (body.result) {
      clearInterval(opsState.poll);
      opsState.busy = false;
      const r = body.result.result || {};
      toast(body.result.error
        ? 'Scan finished with errors: ' + body.result.error
        : 'Scan finished in ' + body.result.seconds + 's · ' +
          (r.indexed || 0) + ' indexed, ' + (r.thumbnails || 0) + ' thumbnails',
        body.result.error ? 'err' : 'ok');
      await loadOpsStatus();
      repaintOps();
      return;
    }
    /* Ten minutes is longer than any scan this has been pointed at; past it
     * the poller is the thing that is stuck, not the scan. */
    if (Date.now() - started > 600000) {
      clearInterval(opsState.poll);
      opsState.busy = false;
      toast('Still scanning — reload to see the result', 'warn');
      repaintOps();
    }
  }, 1500);
}

async function doPause(reason) {
  try {
    await api('/api/ops/pause', { method: 'POST', body: JSON.stringify({ reason: reason || '' }) });
    toast('Indexing paused');
  } catch (err) { toast(err.message, 'err'); return; }
  await loadOpsStatus();
  repaintOps();
  renderTree();
}

async function doResume() {
  try {
    await api('/api/ops/resume', { method: 'POST' });
    toast('Indexing resumed');
  } catch (err) { toast(err.message, 'err'); return; }
  await loadOpsStatus();
  repaintOps();
  renderTree();
}

async function runDoctor(album) {
  opsState.busy = true;
  repaintOps();
  toast('Checking…');
  try {
    opsState.doctor = await api('/api/ops/doctor' +
      (album ? '?album=' + encodeURIComponent(album) : ''));
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    opsState.busy = false;
    repaintOps();
  }
}

/* ----- home ------------------------------------------------------------- */
/* The screen the console opens on. It answers what a person arriving has to
 * ask anyway -- is the machine working, is anything broken, what happened
 * here last, what is still unwritten -- and it answers them out of routes
 * that already existed for other screens. Nothing on it is computed here: a
 * dashboard with figures of its own is a second opinion to keep in step.
 */
const home = { issues: null, audit: [], gallery: null };

async function renderHome() {
  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__empty', text: 'Reading the archive…' }));
  const [, issues, audit, gallery] = await Promise.all([
    loadOpsStatus(),
    api('/api/validate').catch(() => null),
    api('/api/audit?limit=8').catch(() => null),
    api('/api/gallery').catch(() => null),
  ]);
  if (!state.sel || state.sel.kind !== 'home') return;   /* navigated away */
  if (issues) { home.issues = issues; countIssues(issues); renderTree(); }
  home.audit = (audit && audit.entries) || [];
  home.gallery = gallery;
  paintHome();
}

function card(title, note, ...body) {
  return el('section', { class: 'card' },
    el('header', { class: 'card__head' },
      el('h2', { class: 'card__title', text: title }),
      note ? el('span', { class: 'card__note', text: note }) : null),
    el('div', { class: 'card__body' }, ...body));
}

function tile(label, value, tone) {
  return el('div', { class: 'tile' + (tone ? ' is-' + tone : '') },
    el('span', { class: 'tile__v', text: value }),
    el('span', { class: 'tile__k', text: label }));
}

/* What a finished scan did, as one line. */
function scanSummary(res) {
  return ['indexed', 'thumbnails', 'previews', 'removed', 'failed']
    .filter((k) => res[k]).map((k) => res[k] + ' ' + k).join(' · ') || 'nothing to do';
}

function homeRow(where, key, detail, onclick) {
  return el('div', { class: 'hrow' + (onclick ? ' is-link' : ''), onclick: onclick || null },
    el('span', { class: 'hrow__where', text: where }),
    el('span', { class: 'hrow__key', text: key }),
    el('span', { class: 'hrow__detail', text: detail }));
}

/* An audit line names a file; the console navigates by album. */
function auditTarget(target) {
  const value = String(target || '');
  if (value === '.gallery/links.cfg') return { kind: 'links' };
  if (value.startsWith('.gallery/')) return { kind: 'gallery' };
  const cut = value.indexOf('/.album/');
  if (cut > 0) return { kind: 'album', album: value.slice(0, cut) };
  const slash = value.lastIndexOf('/');
  return slash > 0 ? { kind: 'album', album: value.slice(0, slash) } : null;
}

const agoIso = (ts) => {
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? '—' : ago(parsed / 1000);
};

function paintHome() {
  const pane = $('#pane');
  const st = opsState.status || {};
  const idx = st.index || {};
  const paths = st.paths || {};
  const scan = (st.server && st.server.last_scan) || null;
  const res = (scan && scan.result) || {};
  const live = !!st.live;
  const paused = !!st.paused;
  const scanning = !!(st.server && st.server.scanning);
  const values = (home.gallery && home.gallery.values) || {};
  const title = (values.site_name || []).join(', ').trim() || 'This archive';
  const ro = !!st.read_only || READ_ONLY;

  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: paths.photos || state.meta.photos_dir }),
      el('div', { class: 'head__line' },
        el('h1', { class: 'head__title', text: title }),
        el('div', { class: 'head__meta' },
          ro ? el('span', { class: 'pill pill--warn', text: 'read-only' }) : null)))));

  const grid = el('div', { class: 'home' });
  pane.append(grid);

  /* ---- is the machine working ---- */
  const tone = paused ? 'warn' : live ? 'ok' : 'bad';
  const word = paused ? 'paused' : scanning ? 'scanning' : live ? 'running' : 'not running';
  grid.append(card('Indexer', st.control_dir ? 'via the control channel' : null,
    el('div', { class: 'lamp' },
      el('span', { class: 'lamp__dot is-' + tone }),
      el('span', { class: 'lamp__word is-' + tone, text: word }),
      el('span', { class: 'lamp__note', text: paused && st.pause
        ? 'since ' + agoIso(new Date(st.pause.since * 1000).toISOString()) +
          (st.pause.reason ? ' — ' + st.pause.reason : '')
        : live ? '' : 'no heartbeat — start the gallery to index' })),
    el('dl', { class: 'facts' },
      fact('last scan', scan
        ? ago(scan.finished_at) + ' · ' + (scan.trigger || '?') +
          (scan.seconds != null ? ' · ' + scan.seconds + 's' : '')
        : 'never'),
      fact('it did', scan ? scanSummary(res) : '—'),
      fact('every', paths.scan_interval ? paths.scan_interval + 's' : 'manual only'),
      fact('watcher', paths.watcher ? 'on' : 'off'),
      fact('role', st.role || '—')),
    el('div', { class: 'card__actions' },
      el('button', {
        type: 'button', class: 'btn btn--primary', text: 'Scan now',
        disabled: ro || opsState.busy, onclick: () => startScan('', false),
      }),
      el('button', {
        type: 'button', class: 'btn', disabled: ro || opsState.busy,
        text: paused ? 'Resume' : 'Pause',
        onclick: () => (paused ? doResume() : doPause('')),
      }),
      el('button', {
        type: 'button', class: 'btn btn--ghost', text: 'Operations →',
        onclick: () => select({ kind: 'ops' }),
      }))));

  /* ---- what is in it ---- */
  grid.append(card('Archive', 'what the index holds',
    el('div', { class: 'tiles' },
      tile('photos', String(idx.images ?? '—')),
      tile('albums', String(idx.albums ?? '—')),
      tile('featured', String(idx.featured ?? '—')),
      tile('tags', String(idx.tags ?? '—')),
      tile('originals', bytes(idx.bytes)),
      tile('database', bytes(idx.db_bytes)))));

  /* ---- is anything broken ---- */
  const issues = home.issues;
  const list = (issues && issues.issues) || [];
  const shown = list.slice(0, 6);
  grid.append(el('div', { class: 'home__wide' }, card('Needs attention',
    issues ? issues.errors + ' error(s) · ' + issues.warnings + ' warning(s)' : null,
    !issues
      ? el('p', { class: 'card__quiet', text: 'The check did not run.' })
      : !list.length
        ? el('p', { class: 'card__quiet',
                    text: 'Every config file checks out — nothing the gallery would ignore.' })
        : el('div', { class: 'hrows' }, shown.map((issue) => homeRow(
            issue.scope === 'gallery' ? 'gallery.cfg'
              : issue.scope === 'links' ? 'links.cfg' : issue.album,
            issue.key, issue.detail,
            () => select(issue.scope === 'album'
              ? { kind: 'album', album: issue.album } : { kind: issue.scope })))),
    list.length > shown.length
      ? el('p', { class: 'card__quiet',
                  text: 'and ' + (list.length - shown.length) + ' more' })
      : null,
    el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn', text: 'Check again', onclick: checkAll })))));

  /* ---- what is still unwritten ---- */
  const albums = [];
  (function walk(node) {
    for (const child of node.children || []) { albums.push(child); walk(child); }
  })(state.tree || { children: [] });
  const noCfg = albums.filter((a) => a.own_photos && !a.has_cfg);
  const noText = albums.filter((a) => a.own_photos && !a.has_desc);
  grid.append(card('Unwritten', albums.length + ' album(s) in the tree',
    !noCfg.length && !noText.length
      ? el('p', { class: 'card__quiet', text: 'Every album with photos has a cfg and a text.' })
      : el('div', { class: 'hrows' }, [
          ...noCfg.slice(0, 4).map((a) => homeRow(a.path, 'no cfg',
            a.own_photos + ' photo(s), nothing configured',
            () => select({ kind: 'album', album: a.path }))),
          ...noText.slice(0, 4).map((a) => homeRow(a.path, 'no text',
            a.own_photos + ' photo(s), no album_<lang>.md',
            () => select({ kind: 'album', album: a.path }))),
        ]),
    (noCfg.length > 4 || noText.length > 4)
      ? el('p', { class: 'card__quiet', text: 'and more — the tree marks them' })
      : null));

  /* ---- what happened here ---- */
  grid.append(card('Recent changes', 'this console, not the gallery',
    !home.audit.length
      ? el('p', { class: 'card__quiet', text: 'Nothing has been saved here yet.' })
      : el('div', { class: 'hrows' }, home.audit.map((entry) => {
          const target = auditTarget(entry.target);
          return homeRow(agoIso(entry.ts), entry.action || '—', entry.target || '',
            target ? () => select(target) : null);
        }))));
}


function renderPane() {
  const pane = $('#pane');
  const isGallery = state.sel.kind === 'gallery';
  const active = document.activeElement;
  const fk = active && active.dataset ? active.dataset.fk : null;
  const selStart = fk && active.selectionStart !== undefined ? active.selectionStart : null;
  const selEnd = fk && active.selectionEnd !== undefined ? active.selectionEnd : null;
  paneScroll = pane.scrollTop;
  pane.innerHTML = '';

  const tabs = isGallery
    ? [['settings', 'Settings'], ['assets', 'Files'], ['raw', 'Raw file']]
    : [['photos', 'Photos'], ['settings', 'Settings'], ['text', 'Description'],
       ['assets', 'Files'], ['raw', 'Raw file']];
  if (!tabs.some(([id]) => id === state.tab)) state.tab = tabs[0][0];

  /* Header and tabs travel together as one sticky block: on a settings page
   * three screens tall, scrolling used to take the album's name and every
   * tab with it. */
  pane.append(el('div', { class: 'pane__top' },
    renderHead(isGallery),
    el('div', { class: 'tabs' }, tabs.map(([id, label]) =>
      el('button', {
        class: 'tab' + (state.tab === id ? ' is-active' : ''),
        type: 'button', text: label,
        onclick: () => { state.tab = id; renderPane(); },
      })))));

  if (state.data.issues && state.data.issues.length) {
    pane.append(el('div', { class: 'issues' }, state.data.issues.map((issue) =>
      el('div', { class: 'issue issue--' + (issue.level === 'error' ? 'error' : 'warn') },
        el('span', { class: 'issue__key', text: issue.key }),
        el('span', { class: 'issue__detail', text: issue.detail })))));
  }

  if (state.tab === 'settings') {
    pane.append(renderSettings(isGallery ? GALLERY_GROUPS : ALBUM_GROUPS));
    pane.append(renderSaveBar());
  } else if (state.tab === 'raw') pane.append(renderRaw());
  else if (state.tab === 'photos') pane.append(renderPhotosTab());
  else if (state.tab === 'text') pane.append(renderDescriptions());
  else if (state.tab === 'assets') pane.append(renderAssets());

  pane.scrollTop = paneScroll;
  paneScrollLanded = pane.scrollTop;
  restoreFocus(fk, selStart, selEnd);
}

/* One album out of the loaded tree, by path. */
function treeNode(path, node) {
  node = node || state.tree;
  if (!node) return null;
  if (node.path === path) return node;
  for (const child of node.children || []) {
    const hit = treeNode(path, child);
    if (hit) return hit;
  }
  return null;
}

function renderHead(isGallery) {
  const data = state.data;
  const meta = [el('span', {
    class: 'pill ' + (data.exists ? 'pill--ok' : ''),
    text: data.exists ? 'cfg present' : 'no cfg yet',
  })];
  if (!isGallery) {
    meta.push(el('span', { class: 'pill', text: data.own_count + ' here' }));
    if (data.photo_count !== data.own_count) {
      meta.push(el('span', { class: 'pill', text: data.photo_count + ' subtree' }));
    }
    /* Whether anyone has written about this album. It is the one thing the
     * header could not say, and the thing most often still undone. */
    const langs = Object.keys(data.descriptions || {})
      .filter((lang) => (data.descriptions[lang] || '').trim());
    meta.push(langs.length
      ? el('span', { class: 'pill', text: 'text ' + langs.join(' ') })
      : el('span', { class: 'pill', text: 'no text yet' }));
  }
  const errors = (data.issues || []).filter((i) => i.level === 'error').length;
  const warns = (data.issues || []).filter((i) => i.level === 'warn').length;
  if (errors) meta.push(el('span', { class: 'pill pill--err', text: errors + ' errors' }));
  if (warns) meta.push(el('span', { class: 'pill pill--warn', text: warns + ' warnings' }));
  if (!isGallery && !READ_ONLY) meta.push(linkButton(state.sel.album, 'Link…'));

  const path = isGallery
    ? state.meta.photos_dir + '/gallery.cfg'
    : state.meta.photos_dir + '/' + state.sel.album + '/.album/album.cfg';

  /* Title and pills share one line so the sticky block stays short; the file
   * path is the first thing dropped once the pane is scrolled. */
  /* The album's own name for itself, when it has one: the folder name is in
   * the path above, and repeating it in the title says nothing twice. */
  const named = !isGallery && (data.values.name || []).join(', ').trim();
  const node = isGallery ? null : treeNode(state.sel.album);
  const cover = node && node.cover
    ? el('img', { class: 'head__cover', src: thumbUrl(node.cover), alt: '', loading: 'lazy',
                  onerror: (ev) => ev.target.remove() })
    : null;

  return el('div', { class: 'head' },
    el('div', { class: 'head__crumb', text: path, title: path }),
    el('div', { class: 'head__line' },
      cover,
      el('h1', { class: 'head__title', text: isGallery ? 'gallery.cfg' : (named || data.name) }),
      el('div', { class: 'head__meta' }, meta)));
}

/* ----- settings --------------------------------------------------------- */
/* Keys whose control is a list of rows: those get the full width. Everything
 * else is a one-line control that was sitting in a 210px column next to an
 * acre of nothing, so those tile two or three across instead. */
const WIDE_TYPES = new Set(['photo_list', 'welcome', 'album_list', 'kv_list']);

const isSet = (key) => value(key) !== null;

/* The filter reads the key name and its help text, so "colour" finds
 * `accent` and "phone" finds `wallpaper_mobile` — nobody remembers a
 * thirty-key vocabulary by name. */
function matchesQuery(key, q) {
  if (!q) return true;
  return key.toLowerCase().includes(q) ||
         String(helpFor(key)).toLowerCase().includes(q);
}

const groupId = (title) => state.sel.kind + ':' + title;

function toggleGroup(title) {
  const id = groupId(title);
  if (state.collapsed.has(id)) state.collapsed.delete(id);
  else state.collapsed.add(id);
  savePrefs();
  renderPane();
}

function renderSettings(groups) {
  const q = state.query.trim().toLowerCase();
  const allKeys = groups.flatMap(([, , keys]) => keys);
  const setTotal = allKeys.filter(isSet).length;
  const blocks = [];
  let shown = 0;

  for (const [title, blurb, keys] of groups) {
    // A query on the group's own title keeps the whole block: searching
    // "backdrop" should hand back the four keys that answer to it.
    const groupHit = !!q && title.toLowerCase().includes(q);
    const keep = keys.filter((key) =>
      (groupHit || matchesQuery(key, q)) &&
      (!state.setOnly || isSet(key) || key in state.edits));
    if (!keep.length) continue;
    shown += keep.length;

    const setHere = keys.filter(isSet).length;
    // A filter overrules a fold: a hit hidden inside a shut group reads as
    // "no result".
    const folded = !q && state.collapsed.has(groupId(title));

    blocks.push(el('section', { class: 'group' + (folded ? ' is-folded' : '') },
      el('header', {
        class: 'group__head', role: 'button', tabindex: '0',
        'aria-expanded': folded ? 'false' : 'true',
        title: folded ? 'show these keys' : 'fold this group away',
        onclick: () => toggleGroup(title),
        onkeydown: (ev) => {
          if (ev.key !== 'Enter' && ev.key !== ' ') return;
          ev.preventDefault();
          toggleGroup(title);
        },
      },
        el('span', { class: 'group__twisty', text: '▶' }),
        el('div', { class: 'group__text' },
          el('h2', { class: 'group__title', text: title }),
          el('p', { class: 'group__blurb', text: blurb })),
        el('span', {
          class: 'group__count' + (setHere ? ' is-on' : ''),
          text: setHere + ' / ' + keys.length,
          title: setHere + ' of ' + keys.length + ' keys written in this file',
        })),
      folded ? null : el('div', { class: 'fields' }, keep.map(renderField))));
  }

  const panel = el('div', { class: 'tabpanel' },
    renderSettingsBar(groups, setTotal, allKeys.length, shown));
  if (blocks.length) panel.append(...blocks);
  else {
    panel.append(el('div', { class: 'empty-note', text: state.setOnly && !q
      ? 'This file sets nothing yet — switch “only set” off to see the whole vocabulary.'
      : 'No key matches “' + state.query.trim() + '”.' }));
  }
  return panel;
}

/* One row above the groups: find a key, hide the ones this file leaves at
 * the gallery's default, drop the help text, fold everything shut. Which of
 * those you want is a habit, not a property of the album, so the three
 * switches persist across reloads. */
function renderSettingsBar(groups, setTotal, total, shown) {
  const q = state.query.trim();
  const allFolded = groups.every(([title]) => state.collapsed.has(groupId(title)));
  const filtering = !!q || state.setOnly;

  const sw = (on, label, title, onclick) => el('button', {
    class: 'chipbtn' + (on ? ' is-on' : ''), type: 'button', text: label,
    title, 'aria-pressed': on ? 'true' : 'false', onclick,
  });

  return el('div', { class: 'stoolbar' },
    el('div', { class: 'stoolbar__find' },
      el('input', {
        type: 'search', class: 'fieldsearch', 'data-fk': '__q', value: state.query,
        placeholder: 'Find a setting — name or description…', autocomplete: 'off',
        oninput: (ev) => { state.query = ev.target.value; renderPane(); },
      })),
    el('div', { class: 'stoolbar__switches' },
      sw(state.setOnly, 'Only set', 'show just the keys this file writes', () => {
        state.setOnly = !state.setOnly; savePrefs(); renderPane();
      }),
      sw(state.showHelp, 'Help', 'show the description under each key', () => {
        state.showHelp = !state.showHelp; syncHelpClass(); savePrefs(); renderPane();
      }),
      sw(false, allFolded ? 'Unfold all' : 'Fold all', 'fold every group', () => {
        if (allFolded) state.collapsed.clear();
        else for (const [title] of groups) state.collapsed.add(groupId(title));
        savePrefs(); renderPane();
      })),
    el('span', { class: 'stoolbar__count', text: filtering
      ? shown + ' of ' + total + ' keys shown'
      : setTotal + ' of ' + total + ' keys set' }));
}

function renderField(key) {
  const spec = specFor(key);
  const help = helpFor(key);
  const unset = value(key) === null;
  const edited = key in state.edits;

  return el('div', {
    class: 'field' + (WIDE_TYPES.has(spec.type) ? ' field--wide' : '') +
           (unset ? ' is-unset' : '') + (edited ? ' is-edited' : ''),
    'data-key': key,
  },
    el('div', { class: 'field__top' },
      el('span', { class: 'field__label', text: key }),
      edited ? el('span', { class: 'field__flag', text: 'edited',
                            title: 'changed here, not yet written to the file' }) : null,
      unset || READ_ONLY ? null : el('button', {
        class: 'field__unset', type: 'button', text: '✕',
        title: 'unset — remove this line from the file',
        onclick: () => setValue(key, null),
      })),
    el('div', { class: 'field__control' }, buildControl(key, spec)),
    help ? el('div', { class: 'field__help', text: help }) : null);
}

function buildControl(key, spec) {
  switch (spec.type) {
    case 'bool': return boolControl(key);
    case 'bool_off': return statsControl(key);
    case 'choice': return choiceControl(key, spec.choices || []);
    case 'number': return numberControl(key, spec);
    case 'ratio': return ratioControl(key, spec);
    case 'color': return colorControl(key);
    case 'text': return textControl(key);
    case 'list': return listControl(key);
    case 'kv_list': return kvListControl(key);
    case 'photo': return coverControl(key);
    case 'photo_list': return photoListControl(key);
    case 'welcome': return welcomeControl(key);
    case 'album_list': return albumOrderControl(key);
    case 'asset': return assetControl(key, spec);
    case 'brand_asset': return brandAssetControl(key, spec);
    default: return textControl(key);
  }
}

function toggle(options, current, onPick) {
  return el('div', { class: 'toggle' }, options.map(([val, label]) =>
    el('button', {
      type: 'button', class: current === val ? 'is-on' : '', text: label,
      disabled: READ_ONLY, onclick: () => onPick(val),
    })));
}

function boolControl(key) {
  const raw = value(key);
  const current = raw === null ? ''
    : (String(raw).toLowerCase() === 'true' || raw === true ? 'true' : 'false');
  return toggle([['', 'not set'], ['true', 'yes'], ['false', 'no']], current,
    (val) => setValue(key, val === '' ? null : val));
}

function statsControl(key) {
  return toggle([['', 'shown'], ['off', 'hidden']], value(key) === null ? '' : 'off',
    (val) => setValue(key, val === '' ? null : 'off'));
}

function choiceControl(key, choices) {
  const current = value(key);
  const node = el('select', {
    disabled: READ_ONLY,
    onchange: (ev) => setValue(key, ev.target.value === '' ? null : ev.target.value),
  },
    el('option', { value: '', text: '(not set — gallery default)' }),
    choices.map((c) => el('option', { value: c, text: c, selected: current === c })));
  if (current && !choices.includes(current)) {
    node.append(el('option', { value: current, text: current + '  (unknown value)', selected: true }));
  }
  return node;
}

function numberControl(key, spec) {
  /* The range comes off the key's own spec — this used to read font_scale's
   * for every numeric key, which was fine while font_scale was the only one
   * and silently wrong the moment it wasn't. */
  const [lo, hi] = spec.range || state.meta.font_scale_range;
  return el('input', {
    type: 'number', step: String(spec.step || 0.05), min: lo, max: hi, 'data-fk': key,
    value: value(key) || '', placeholder: 'e.g. 1.25', disabled: READ_ONLY,
    oninput: (ev) => setValue(key, ev.target.value.trim() || null),
  });
}

/* A 0–1 dial that also has a word for one end of it (`off`). The slider is
 * the honest control for "how much" — but it cannot express "off", and it
 * cannot express "not set" either, so the two words sit next to it as
 * buttons and the slider only appears once a number is actually chosen. */
function ratioControl(key, spec) {
  const [lo, hi] = spec.range || [0, 1];
  const raw = value(key);
  const word = String(raw ?? '').trim().toLowerCase();
  const isOff = ['off', 'none', 'no', 'false', '0'].includes(word);
  const num = isOff || raw === null || raw === '' ? null : Number(raw);
  const mid = Math.round(((lo + hi) / 2) * 100) / 100;

  const slider = el('input', {
    type: 'range', min: lo, max: hi, step: String(spec.step || 0.02),
    value: num === null || Number.isNaN(num) ? mid : num,
    disabled: READ_ONLY || num === null || Number.isNaN(num),
    'data-fk': key,
    oninput: (ev) => setValue(key, ev.target.value),
  });
  const state_ = num !== null && !Number.isNaN(num) ? 'num' : (isOff ? 'off' : '');
  return el('div', { class: 'ratio' },
    toggle([['', 'gallery default'], ['off', spec.off || 'off'], ['num', 'set…']], state_,
      (val) => setValue(key, val === '' ? null : (val === 'off' ? 'off' : String(mid)))),
    el('div', { class: 'ratio__dial' }, slider,
      el('span', { class: 'ratio__val mono',
        text: num !== null && !Number.isNaN(num) ? num.toFixed(2) : (isOff ? 'off' : '—') })));
}

/* Hex only, because that is all the gallery parses. The native swatch is the
 * quick way in; the text field stays authoritative so an unset key reads as
 * unset instead of silently becoming #000000, which is what a bare
 * <input type=color> would show. */
function colorControl(key) {
  const raw = String(value(key) || '').trim();
  const valid = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(raw);
  return el('div', { class: 'colorpick' },
    el('input', {
      type: 'color', value: valid ? raw : '#a594ff', disabled: READ_ONLY,
      title: 'pick a colour', class: 'colorpick__swatch',
      oninput: (ev) => setValue(key, ev.target.value),
    }),
    el('input', {
      type: 'text', value: raw, disabled: READ_ONLY, 'data-fk': key,
      class: 'colorpick__hex mono', placeholder: '#a594ff — empty keeps the gallery accent',
      oninput: (ev) => setValue(key, ev.target.value.trim() || null),
    }),
    raw && !valid ? el('span', { class: 'colorpick__bad', text: 'not a hex colour' }) : null);
}

const TEXT_PLACEHOLDER = {
  loc: 'City, Country',
  // the folder name is the fallback, so showing it is the honest hint
  name: 'e.g. Japan 2026 — empty keeps the folder name',
};

function textControl(key) {
  return el('input', {
    type: 'text', value: value(key) || '', disabled: READ_ONLY,
    'data-fk': key, placeholder: TEXT_PLACEHOLDER[key] || '',
    oninput: (ev) => setValue(key, ev.target.value || null),
  });
}

function listControl(key) {
  const items = value(key) || [];
  const box = el('div', { class: 'picklist' });
  items.forEach((item, index) => {
    box.append(el('div', { class: 'pickitem' },
      el('input', {
        type: 'text', value: item, disabled: READ_ONLY, 'data-fk': key + ':' + index,
        oninput: (ev) => {
          const next = items.slice();
          next[index] = ev.target.value;
          setValue(key, next);
        },
      }),
      READ_ONLY ? null : el('button', {
        class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕',
        onclick: () => setValue(key, items.filter((_, i) => i !== index)),
      })));
  });
  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'None.' }));
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' }, el('button', {
      class: 'btn btn--sm', type: 'button', text: '+ Add',
      onclick: () => setValue(key, items.concat([''])),
    })));
  }
  return box;
}

/* An album's custom attributes (`stat`). The gallery stores each as one
 * "Label: Value" line and splits on the first colon, so the editor works in
 * those two halves directly — which removes the only two ways to write a line
 * the gallery drops silently: no colon, or an empty value. */
function kvListControl(key) {
  const items = value(key) || [];
  const pairs = items.map((item) => {
    const at = String(item).indexOf(':');
    return at < 0 ? [String(item).trim(), '']
      : [String(item).slice(0, at).trim(), String(item).slice(at + 1).trim()];
  });
  // A blank row is kept as an empty entry so it can be typed into; the server
  // drops empty entries on save, so it never reaches the file.
  const commit = (next) => setValue(key,
    next.map(([k, v]) => (k || v) ? (k + ': ' + v) : ''));

  const box = el('div', { class: 'picklist' });
  pairs.forEach(([label, val], index) => {
    const edit = (which) => (ev) => {
      const next = pairs.map((p) => p.slice());
      next[index][which] = ev.target.value;
      commit(next);
    };
    box.append(el('div', { class: 'pickitem' + (val.includes(',') ? ' pickitem--missing' : '') },
      el('input', {
        class: 'kv__key', type: 'text', value: label, placeholder: 'Label',
        'data-fk': key + ':' + index + ':k', disabled: READ_ONLY, oninput: edit(0),
      }),
      el('span', { class: 'kv__sep', text: ':' }),
      el('input', {
        type: 'text', value: val, placeholder: 'Value', disabled: READ_ONLY,
        'data-fk': key + ':' + index + ':v',
        title: val.includes(',') ? 'a comma splits this into two entries' : '',
        oninput: edit(1),
      }),
      READ_ONLY ? null : el('button', {
        class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕',
        onclick: () => commit(pairs.filter((_, i) => i !== index)),
      })));
  });

  if (!pairs.length) box.append(el('div', { class: 'empty-note', text: 'No custom attributes.' }));
  if (pairs.some(([, v]) => v.includes(','))) {
    box.append(el('div', { class: 'field__help field__hint' },
      'A value with a comma gets split into two entries by the cfg parser.'));
  }
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' }, el('button', {
      class: 'btn btn--sm', type: 'button', text: '+ Add attribute',
      onclick: () => commit(pairs.concat([['', '']])),
    })));
  }
  return box;
}

/* ----- cover ------------------------------------------------------------ */
function coverControl(key) {
  const current = value(key);
  const album = state.sel.album;
  const box = el('div', { class: 'cover-preview' });
  if (current) {
    box.append(el('img', {
      src: thumbUrl(album + '/' + strip(current)), alt: '', loading: 'lazy',
      onerror: (ev) => { ev.target.classList.add('is-broken'); },
    }));
  }
  const [folder, name] = splitPath(strip(current || ''));
  box.append(el('div', { class: 'cover-preview__side' },
    current
      ? el('div', { class: 'pickitem__label' },
          el('b', { text: name }), ' ', el('span', { text: folder }))
      : el('div', { class: 'empty-note', text: 'Auto — newest photo in the album.' }),
    READ_ONLY ? null : el('button', {
      class: 'btn btn--sm', type: 'button', text: current ? 'Change…' : 'Pick a cover…',
      onclick: () => openPicker({
        title: 'Cover photo', root: album, single: true,
        picked: current ? [strip(current)] : [],
        onApply: (list) => setValue(key, list[0] || null),
      }),
    })));
  return box;
}

/* ----- ordered photo lists ---------------------------------------------- */
function photoListControl(key) {
  const items = value(key) || [];
  const album = state.sel.album;
  const box = el('div', { class: 'picklist' });

  items.forEach((item, index) => {
    const [folder, name] = splitPath(strip(item));
    box.append(sortableRow({
      index, items, key,
      thumb: thumbUrl(album + '/' + strip(item)),
      label: [el('b', { text: name }), ' ', el('span', { text: folder })],
    }));
  });

  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'No photos listed.' }));
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm', type: 'button', text: '+ Pick photos…',
        onclick: () => openPicker({
          title: key === 'featured' ? 'Featured photos' : 'Curated photo order',
          root: album, picked: items.map(strip),
          onApply: (list) => setValue(key, list),
        }),
      }),
      items.length ? el('button', {
        class: 'btn btn--sm btn--ghost', type: 'button', text: 'Clear',
        onclick: () => setValue(key, null),
      }) : null));
  }
  return box;
}

/* Drag to reorder. The file order *is* the display order for `featured`,
 * `order`, `album_order` and the welcome reel, so rearranging the rows is the
 * whole edit — there is nothing else to store. Dropping on a row inserts the
 * dragged entry at that row's position. */
let dragFrom = null;

function sortableRow({ index, items, key, thumb, label, group, extraClass }) {
  const row = el('div', {
    class: 'pickitem' + (group ? ' pickitem--group' : '') + (extraClass || ''),
    // `draggable` is an enumerated attribute, not a boolean one: an empty
    // value means "auto", which is NOT draggable. It has to be the string.
    draggable: READ_ONLY ? 'false' : 'true',
    ondragstart: (ev) => {
      dragFrom = index;
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', String(index));
      row.classList.add('is-dragging');
    },
    ondragend: () => {
      dragFrom = null;
      row.classList.remove('is-dragging');
      $$('.pickitem.is-over').forEach((n) => n.classList.remove('is-over'));
    },
    ondragover: (ev) => {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
      row.classList.add('is-over');
    },
    ondragleave: () => row.classList.remove('is-over'),
    ondrop: (ev) => {
      ev.preventDefault();
      row.classList.remove('is-over');
      const raw = ev.dataTransfer.getData('text/plain');
      const from = raw === '' ? dragFrom : Number(raw);
      if (from === null || Number.isNaN(from) || from === index) return;
      const next = items.slice();
      next.splice(index, 0, next.splice(from, 1)[0]);
      setValue(key, next);
    },
  });
  row.append(el('span', { class: 'pickitem__grip', title: 'drag to reorder', text: '⠿' }));
  if (thumb) {
    row.append(el('img', {
      class: 'pickitem__thumb', src: thumb, alt: '', loading: 'lazy', draggable: 'false',
      onerror: (ev) => { ev.target.classList.add('is-broken'); },
    }));
  }
  row.append(el('span', { class: 'pickitem__label' }, label));
  if (!READ_ONLY) {
    // Keyboard equivalent of the drag, so reordering does not need a mouse.
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '↑',
      title: 'move up', disabled: index === 0,
      onclick: () => {
        const next = items.slice();
        next.splice(index - 1, 0, next.splice(index, 1)[0]);
        setValue(key, next);
      },
    }));
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '↓',
      title: 'move down', disabled: index === items.length - 1,
      onclick: () => {
        const next = items.slice();
        next.splice(index + 1, 0, next.splice(index, 1)[0]);
        setValue(key, next);
      },
    }));
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕', title: 'remove',
      onclick: () => setValue(key, items.filter((_, i) => i !== index)),
    }));
  }
  return row;
}

/* ----- welcome ---------------------------------------------------------- */
function welcomeControl(key) {
  const items = value(key) || [];
  const keywords = state.meta.welcome_keywords;
  const isKeyword = items.length === 1 && keywords.includes(String(items[0]).toLowerCase());
  const box = el('div', { class: 'picklist' });

  box.append(el('select', {
    disabled: READ_ONLY,
    onchange: (ev) => {
      const val = ev.target.value;
      setValue(key, val === 'list' ? (isKeyword ? [] : items) : val ? [val] : null);
    },
  },
    el('option', { value: '', text: '(not set)', selected: !items.length }),
    el('option', { value: 'list', text: 'hand-picked list', selected: items.length > 0 && !isKeyword }),
    keywords.map((kw) => el('option', {
      value: kw, text: kw + ' (keyword)',
      selected: isKeyword && items[0].toLowerCase() === kw,
    }))));

  if (isKeyword || !items.length) return box;

  items.forEach((item, index) => {
    const [folder, name] = splitPath(strip(item));
    box.append(sortableRow({
      index, items, key,
      thumb: thumbUrl(strip(item)),
      label: [el('b', { text: name }), ' ', el('span', { text: folder })],
    }));
  });

  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' }, el('button', {
      class: 'btn btn--sm', type: 'button', text: '+ Pick photos…',
      onclick: () => openPicker({
        title: key.replace(/_/g, ' '), root: '', gallery: true,
        picked: items.map(strip),
        onApply: (list) => setValue(key, list),
      }),
    })));
  }
  return box;
}

/* ----- album_order ------------------------------------------------------ */
function albumOrderControl(key) {
  const items = value(key) || [];
  const known = state.data.albums || [];
  const box = el('div', { class: 'picklist' });

  items.forEach((item, index) => {
    const str = String(item);
    const group = str.startsWith('#');
    const missing = !group && !known.some((a) =>
      a.toLowerCase() === strip(str).toLowerCase() ||
      a.toLowerCase().replace(/^_/, '') === strip(str).toLowerCase().replace(/^_/, ''));
    box.append(sortableRow({
      index, items, key, group,
      extraClass: missing ? ' pickitem--missing' : '',
      label: group
        ? [el('b', { text: str.slice(1) }), ' ', el('span', { text: 'group header' })]
        : [el('b', { text: str }), missing ? ' ' : null,
           missing ? el('span', { text: 'no such folder' }) : null],
    }));
  });

  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'No curated order set.' }));
  if (READ_ONLY) return box;

  const unused = known.filter((a) => !a.includes('/') &&
    !items.some((i) => String(i).toLowerCase() === a.toLowerCase()));

  box.append(el('div', { class: 'row' },
    el('select', {
      onchange: (ev) => {
        if (ev.target.value) setValue(key, items.concat([ev.target.value]));
      },
    },
      el('option', { value: '', text: unused.length ? '+ Add album…' : '(all top-level albums listed)' }),
      unused.map((a) => el('option', { value: a, text: a }))),
    el('button', {
      class: 'btn btn--sm', type: 'button', text: '+ Group header',
      onclick: () => {
        const label = prompt('Group label (frames the albums listed below it):', 'trips');
        if (label && label.trim()) setValue(key, items.concat(['#' + label.trim()]));
      },
    }),
    items.length ? el('button', {
      class: 'btn btn--sm btn--ghost', type: 'button', text: 'Clear',
      onclick: () => setValue(key, null),
    }) : null));
  return box;
}

/* ----- icon / font / wallpaper ------------------------------------------ */
/* Every one of these picks a file out of the album's .album/ folder, so they
 * share one control. `kind` decides which files are offered and what the
 * preview under the picker looks like — a swatch, a type sample, or the
 * backdrop itself. The mobile wallpaper additionally refuses clips: the
 * gallery never loads a backdrop video on a phone, so offering one here
 * would let you configure something that silently does nothing. */
const ASSET_KIND = {
  icon: 'icon',
  font: 'font',
  wallpaper: 'wallpaper',
  wallpaper_mobile: 'wallpaper',
  /* the .gallery/ keys, so one preview dispatcher covers both folders */
  logo: 'icon',
  favicon: 'icon',
  operator_pfp: 'icon',
};
/* Assets live in one of two folders and every /api/asset call says which:
 * an album's own .album/, or the gallery-wide .gallery/ holding the logo,
 * the operator's portrait and the footer badges. */
function assetScope() {
  return state.sel.kind === 'gallery' ? 'gallery' : 'album';
}

function assetFolder() {
  return assetScope() === 'gallery'
    ? (state.meta.gallery_meta_dir || '.gallery') + '/'
    : state.sel.album + '/.album/';
}

function assetQuery(name) {
  return '/api/asset?name=' + encodeURIComponent(name) +
         '&scope=' + assetScope() +
         '&path=' + encodeURIComponent(state.sel.album || '');
}

/* the gallery.cfg keys that can point at a file in .gallery/ — the marks,
 * and the site's own face and backdrop */
const BRAND_CFG_KEYS = ['logo', 'favicon', 'operator_pfp',
                        'font', 'wallpaper', 'wallpaper_mobile'];
/* every cfg key that can point at a file in .album/ — "in use" means one of
 * these names it, and a .png could be named by any of them */
const ASSET_CFG_KEYS = Object.keys(ASSET_KIND);
const VIDEO_RE = /\.(mp4|webm)$/i;

/* one asset can fill several roles — see asset_kinds() in library.py. Falls
 * back to the single `kind` so an older server response still works. */
function assetIs(asset, role) {
  return asset.kinds ? asset.kinds.includes(role) : asset.kind === role;
}

/* What a row can actually put in an <img>. The .gallery/ folder carries no
 * per-file role — it holds faces, clips and the gallery.cfg itself next to the
 * marks — so the extension is what answers "is there a picture here", in both
 * folders. */
const IMAGE_RE = /\.(svg|png|webp|gif|jpe?g|avif)$/i;

/* One preview per ROLE, wherever the file happens to live: a mark is shown as
 * an image, a backdrop at the shape it will be seen in (a clip plays), a face
 * set in itself. Both pickers call this, so a font picked out of .gallery/
 * previews exactly the way one picked out of an .album/ does. */
function assetPreview(key, name, url) {
  const kind = ASSET_KIND[key] || 'icon';
  if (kind === 'font') return fontSample(name, url);
  if (kind === 'wallpaper') return wallpaperPreview(name, url);
  return el('img', { class: 'iconpreview', src: url, alt: '' });
}

/* The .gallery/ counterpart of assetControl. Nothing in that folder has a
 * role of its own — a .png is a possible logo, a badge and a portrait, and
 * since the site's own theme lives there too a .jpg is a possible backdrop
 * as well — so the offered files are filtered by the key's own extension
 * whitelist rather than by anything about the file. */
function brandAssetControl(key, spec) {
  const current = value(key);
  const exts = spec.exts || [];
  const files = (state.data.assets || []).filter(
    (a) => exts.some((e) => a.name.toLowerCase().endsWith(e)));
  const box = el('div', { class: 'field__control' });

  box.append(el('select', {
    disabled: READ_ONLY,
    onchange: (ev) => setValue(key, ev.target.value || null),
  },
    el('option', { value: '', text: '(not set)', selected: !current }),
    files.map((f) => el('option', { value: f.name, text: f.name, selected: current === f.name })),
    current && !files.some((f) => f.name === current)
      ? el('option', { value: current, text: current + '  (missing from .gallery/)', selected: true })
      : null));

  if (!files.length) {
    box.append(el('div', { class: 'field__help field__hint', text:
      'Nothing usable in .gallery/ yet — add a file on the “Files” tab. Accepted: ' +
      exts.join(', ') }));
  }
  if (current && files.some((f) => f.name === current)) {
    box.append(assetPreview(key, current, assetQuery(current)));
  }
  return box;
}

function assetControl(key, spec) {
  const current = value(key);
  const kind = ASSET_KIND[key] || 'font';
  /* `kinds`, not `kind`: the extension whitelists overlap, so a .png is both a
   * possible icon and a possible wallpaper and has to show up in both pickers. */
  let files = (state.data.assets || []).filter((a) => assetIs(a, kind));
  if (key === 'wallpaper_mobile') files = files.filter((f) => !VIDEO_RE.test(f.name));
  const box = el('div', { class: 'field__control' });

  box.append(el('select', {
    disabled: READ_ONLY,
    onchange: (ev) => setValue(key, ev.target.value || null),
  },
    el('option', { value: '', text: '(not set)', selected: !current }),
    files.map((f) => el('option', { value: f.name, text: f.name, selected: current === f.name })),
    current && !files.some((f) => f.name === current)
      ? el('option', { value: current, text: current + '  (missing from .album/)', selected: true })
      : null));

  if (!files.length) {
    box.append(el('div', { class: 'field__help field__hint', text:
      'No ' + kind + ' in this album’s .album/ yet — add one on the “Files” tab. Accepted: ' +
      spec.exts.join(', ') }));
  }
  if (current && files.some((f) => f.name === current)) {
    const url = '/api/asset?name=' + encodeURIComponent(current) +
                '&path=' + encodeURIComponent(state.sel.album);
    box.append(assetPreview(key, current, url));
  }
  return box;
}

/* the backdrop, at the shape it will be seen in — a clip plays muted and
 * looping the way the gallery plays it, a still just sits there */
function wallpaperPreview(name, url) {
  if (VIDEO_RE.test(name)) {
    return el('video', {
      class: 'wallpreview', src: url,
      autoplay: true, muted: true, loop: true, playsinline: true,
    });
  }
  return el('img', { class: 'wallpreview', src: url, alt: '' });
}

/* The face, set in the word it will actually carry: an album's name on the
 * album tab, the archive's own wordmark on the gallery tab. */
function fontSample(name, url) {
  const family = 'cfgfont_' + name.replace(/[^a-z0-9]/gi, '_');
  const label = (state.sel.kind === 'gallery'
    ? value('site_hero') || value('site_name')
    : state.data.name) || 'Sample';
  const sample = el('div', { class: 'fontsample', text: label });
  if (window.FontFace) {
    new FontFace(family, 'url("' + url + '")').load().then((loaded) => {
      document.fonts.add(loaded);
      sample.style.fontFamily = '"' + family + '", serif';
    }).catch(() => { sample.textContent = label + '  (font could not be loaded)'; });
  }
  return sample;
}

/* ----- save bar --------------------------------------------------------- */
/* Every staged key as its own chip: the name jumps to the field — through a
 * filter or a folded group, which is exactly when an edit is easy to lose
 * track of — and the ✕ drops that one change instead of all of them. As one
 * run-on "a · b · c" line, eight edits ran off the end of the bar. */
function renderSaveBar() {
  const changed = Object.keys(state.edits);
  const bar = el('div', { class: 'savebar savebar--settings' + (changed.length ? ' is-dirty' : '') });

  if (!changed.length) {
    bar.append(el('span', { class: 'savebar__note', text: READ_ONLY
      ? 'readonly mount · saving disabled'
      : 'idle · comments and untouched keys survive every save' }));
  } else {
    bar.append(el('span', { class: 'savebar__note',
      text: changed.length + ' unsaved' }));
    bar.append(el('div', { class: 'savebar__chips' }, changed.map((key) =>
      el('span', { class: 'chip chip--edit' },
        el('button', {
          class: 'chip__go', type: 'button', text: key, title: 'go to ' + key,
          onclick: () => revealField(key),
        }),
        el('button', {
          class: 'chip__x', type: 'button', text: '✕', title: 'undo this change',
          onclick: () => { delete state.edits[key]; renderPane(); },
        })))));
  }

  bar.append(el('div', { class: 'savebar__acts' },
    changed.length ? el('button', {
      class: 'btn', type: 'button', text: 'Discard all',
      onclick: () => { state.edits = {}; renderPane(); },
    }) : null,
    el('button', {
      class: 'btn btn--primary', type: 'button', text: 'Save',
      disabled: READ_ONLY || !changed.length, onclick: saveSettings,
    })));
  return bar;
}

/* Bring one key back on screen whatever is hiding it — a filter, "only set",
 * a folded group — and mark it for a moment so the eye lands on it. */
function revealField(key) {
  state.query = '';
  state.setOnly = false;
  state.collapsed.clear();
  savePrefs();
  renderPane();
  const node = document.querySelector('.field[data-key="' + CSS.escape(key) + '"]');
  if (!node) return;
  node.scrollIntoView({ block: 'center', behavior: 'smooth' });
  node.classList.add('is-flash');
  setTimeout(() => node.classList.remove('is-flash'), 1200);
}

async function saveSettings() {
  try {
    const payload = await api(
      state.sel.kind === 'gallery' ? '/api/gallery/cfg' : '/api/album/cfg',
      { method: 'PUT', body: JSON.stringify({ album: state.sel.album || '', values: state.edits }) });
    Object.assign(state.data, {
      values: payload.values, raw: payload.raw, issues: payload.issues, exists: true,
    });
    state.edits = {};
    renderPane();
    refreshIssueDots();
    toast('Saved');
  } catch (err) {
    toast('Save failed: ' + err.message, 'err');
  }
}

/* ----- raw tab ---------------------------------------------------------- */
function renderRaw() {
  const area = el('textarea', { class: 'u-tall', spellcheck: 'false', disabled: READ_ONLY });
  area.value = state.data.raw || '';
  return el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help tabnote u-mb' },
      'The file exactly as it sits on disk. Saving here replaces it wholesale — ' +
      'the structured tabs only ever rewrite the lines they own.'),
    area,
    el('div', { class: 'savebar' },
      el('span', { class: 'savebar__note',
        text: READ_ONLY ? 'readonly mount · saving disabled' : 'whole-file write' }),
      el('button', {
        class: 'btn', type: 'button', text: 'Revert',
        onclick: () => { area.value = state.data.raw || ''; },
      }),
      el('button', {
        class: 'btn btn--primary', type: 'button', text: 'Save file', disabled: READ_ONLY,
        onclick: async () => {
          try {
            const payload = await api(
              state.sel.kind === 'gallery' ? '/api/gallery/raw' : '/api/album/raw',
              { method: 'PUT', body: JSON.stringify({ album: state.sel.album || '', raw: area.value }) });
            Object.assign(state.data, {
              values: payload.values, raw: payload.raw, issues: payload.issues, exists: true,
            });
            state.edits = {};
            renderPane();
            refreshIssueDots();
            toast('File written');
          } catch (err) {
            toast('Save failed: ' + err.message, 'err');
          }
        },
      })));
}

/* ----- photo loading ---------------------------------------------------- */
/* One folder per request, never the whole subtree: a picker that flattens a
 * 391-photo trip into one wall is unusable, and it makes the browser fetch
 * hundreds of thumbnails nobody asked to see. */
const photoCache = new Map();

async function loadFolder(path, withTags = false) {
  const key = (path || '::root') + (withTags ? '|t' : '');
  if (photoCache.has(key)) return photoCache.get(key);
  const payload = await api('/api/photos?recursive=0' +
    (withTags ? '&tags=1' : '') + '&path=' + encodeURIComponent(path || ''));
  photoCache.set(key, payload);
  return payload;
}

/* The same lookup without the await: renderPhotosTab() needs to know whether
 * it can fill the grid in this tick or has to wait for the network. */
function cachedFolder(path, withTags = false) {
  return photoCache.get((path || '::root') + (withTags ? '|t' : '')) || null;
}

function invalidateFolder(path) {
  photoCache.delete((path || '::root') + '|t');
  photoCache.delete(path || '::root');
}

/* Breadcrumb from the browse root down to the current folder. */
function crumbs(root, current, onGo) {
  const rootLabel = root ? (root.split('/').pop() || root) : 'photos';
  const trail = [[root, rootLabel]];
  if (current !== root) {
    const rest = root ? current.slice(root.length + 1) : current;
    let acc = root;
    for (const part of rest.split('/')) {
      acc = acc ? acc + '/' + part : part;
      trail.push([acc, part]);
    }
  }
  return el('nav', { class: 'crumbs' }, trail.map(([path, label], i) => [
    i ? el('span', { class: 'crumbs__sep', text: '/' }) : null,
    i === trail.length - 1
      ? el('span', { class: 'crumbs__here', text: label })
      : el('button', { class: 'crumbs__link', type: 'button', text: label,
                       onclick: () => onGo(path) }),
  ]));
}

function folderTile(folder, onOpen) {
  return el('button', {
    class: 'foldertile', type: 'button', title: folder.path,
    onclick: () => onOpen(folder.path),
  },
    folder.cover
      ? el('img', { src: thumbUrl(folder.cover), alt: '', loading: 'lazy' })
      : el('span', { class: 'foldertile__blank' }),
    el('span', { class: 'foldertile__body' },
      el('span', { class: 'foldertile__name', text: folder.name }),
      el('span', { class: 'foldertile__count',
                   text: folder.count + (folder.count === 1 ? ' photo' : ' photos') })));
}

/* ----- Photos & tags tab ------------------------------------------------ */
function renderPhotosTab() {
  const album = state.sel.album;
  if (!state.browse) state.browse = { path: album, selected: new Set(), detail: null };
  const b = state.browse;

  const panel = el('div', { class: 'tabpanel' });
  panel.append(el('div', { class: 'field__help tabnote u-mb' },
    'Browse folder by folder. Click a photo to select it and see its metadata; ' +
    'use its corner tick — or ctrl-click — to add more, shift-click for a run. ' +
    'Photo files are never modified: tags go into a .tags sidecar next to each ' +
    'photo, which is what the gallery reads.'));

  /* The grid and the detail panel are laid out side by side, never stacked.
   * The detail used to be a sticky sheet over the bottom of the grid, which
   * covered most of the photos the moment one was selected — so picking a
   * second one was impossible without scrolling it out of the way. */
  const main = el('div', { class: 'photos__main' });
  const aside = el('div', { class: 'photos__aside' });
  panel.append(el('div', { class: 'photos' }, main, aside));

  main.append(crumbs(album, b.path, (path) => {
    b.path = path; b.detail = null; renderPane();
  }));

  const body = el('div', { class: 'browser' });
  main.append(body);

  /* A folder already in the cache is drawn in this same tick rather than a
   * microtask later: renderPane() puts the pane's scroll position back the
   * moment it returns, and against a body still holding nothing but
   * "Loading…" that position does not exist yet — the scrollport is too
   * short for it, the browser clamps it, and every click on a photo threw
   * the page back towards the top. */
  const cached = cachedFolder(b.path, true);
  if (cached) {
    fillBrowser(body, b, cached);
  } else {
    body.append(el('div', { class: 'empty-note', text: 'Loading…' }));
    loadFolder(b.path, true).then((payload) => {
      if (!state.browse || state.browse.path !== b.path) return;
      fillBrowser(body, b, payload);
      keepPaneScroll();
    }).catch((err) => {
      body.innerHTML = '';
      body.append(el('div', { class: 'empty-note', text: err.message }));
    });
  }

  if (b.selected.size) aside.append(renderTagBar(b));
  if (b.detail) aside.append(renderDetail(b.detail));
  if (!b.selected.size && !b.detail) {
    aside.append(el('div', { class: 'aside-empty' },
      'Click a photo to see its metadata. Tick its corner — or ctrl-click — ' +
      'to add it to a selection you can tag all at once.'));
  }
  return panel;
}

/* Draw one folder into the browser column: its sub-folders, the bar above
 * the grid, then the tiles. Split out of renderPhotosTab so a folder that
 * is already cached can be drawn without waiting for a microtask. */
function fillBrowser(body, b, payload) {
  const album = state.sel.album;
  body.innerHTML = '';


  if (payload.folders.length) {
    body.append(el('div', { class: 'folders' },
      payload.folders.map((f) => folderTile(f, (path) => {
        b.path = path; b.detail = null; renderPane();
      }))));
  }

  if (!payload.photos.length) {
    if (!payload.folders.length) {
      body.append(el('div', { class: 'empty-note', text: 'Nothing in this folder.' }));
    }
    return;
  }

  const rels = payload.photos.map((p) => p.rel);
  const allPicked = rels.every((r) => b.selected.has(r));
  body.append(el('div', { class: 'browser__bar' },
    el('span', { class: 'browser__count',
                 text: payload.photos.length + ' photo' +
                       (payload.photos.length === 1 ? '' : 's') + ' here' }),
    READ_ONLY ? null : el('button', {
      class: 'btn btn--sm', type: 'button',
      text: allPicked ? 'Deselect all here' : 'Select all here',
      onclick: () => {
        if (allPicked) rels.forEach((r) => b.selected.delete(r));
        else rels.forEach((r) => b.selected.add(r));
        renderPane();
      },
    })));

  const cover = value('cover');
  const featured = (value('featured') || []).map(strip);
  const order = (value('order') || []).map(strip);
  const subOf = (rel) => album ? rel.slice(album.length + 1) : rel;

  const toggle = (rel) => {
    if (b.selected.has(rel)) b.selected.delete(rel);
    else b.selected.add(rel);
  };

  const grid = el('div', { class: 'grid' });
  payload.photos.forEach((photo, index) => {
    const roles = [];
    const sub = subOf(photo.rel);
    if (cover && strip(cover) === sub) roles.push('cover');
    if (featured.includes(sub)) roles.push('featured');
    if (order.includes(sub)) roles.push('#' + (order.indexOf(sub) + 1));
    const picked = b.selected.has(photo.rel);

    grid.append(el('div', {
      class: 'cell' + (picked ? ' is-picked' : '') +
             (b.detail === photo.rel ? ' is-detail' : ''),
      title: photo.rel,
      onclick: (ev) => {
        if (ev.shiftKey && b.lastIndex !== undefined) {
          const [from, to] = [Math.min(b.lastIndex, index), Math.max(b.lastIndex, index)];
          for (let i = from; i <= to; i++) b.selected.add(payload.photos[i].rel);
        } else if (ev.ctrlKey || ev.metaKey) {
          toggle(photo.rel);
        } else {
          b.selected.clear();
          b.selected.add(photo.rel);
        }
        b.lastIndex = index;
        b.detail = photo.rel;
        renderPane();
      },
    },
      el('img', { src: thumbUrl(photo.rel), alt: '', loading: 'lazy' }),
      // A plain-click way to extend the selection: not everyone reaches for
      // a modifier, and on some setups ctrl-click never arrives at all.
      el('button', {
        class: 'cell__pick' + (picked ? ' is-on' : ''),
        type: 'button',
        text: picked ? '✓' : '',
        title: picked ? 'remove from the selection' : 'add to the selection',
        onclick: (ev) => {
          ev.stopPropagation();
          toggle(photo.rel);
          b.lastIndex = index;
          renderPane();
        },
      }),
      // Everything written over a tile has to survive being narrow: the roles
      // are separate chips along the top, and the tags share the footer with
      // the file name — one line each, ellipsised, with the whole text on the
      // tooltip. As two wide badges they wrapped across the photo, and the
      // tags landed on top of the roles.
      roles.length
        ? el('span', { class: 'cell__roles', title: roles.join(', ') },
            roles.map((r) => el('span', { class: 'cell__role', text: r })))
        : null,
      el('span', { class: 'cell__foot' },
        (photo.tags || []).length
          ? el('span', { class: 'cell__tags', text: photo.tags.join(' · '),
                         title: photo.tags.join(', ') })
          : null,
        el('span', { class: 'cell__name', text: photo.name }))));
  });
  body.append(grid);
}

/* Read-only EXIF for the photo last clicked, plus its own tags. */
function renderDetail(rel) {
  const box = el('aside', { class: 'detail' });
  box.append(el('div', { class: 'detail__head' },
    el('span', { class: 'detail__name', text: splitPath(rel)[1] }),
    el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕',
      onclick: () => { state.browse.detail = null; renderPane(); },
    })));
  const body = el('div', { class: 'detail__body' },
    el('div', { class: 'empty-note', text: 'Reading metadata…' }));
  box.append(body);

  api('/api/image?path=' + encodeURIComponent(rel)).then((info) => {
    if (!state.browse || state.browse.detail !== rel) return;
    body.innerHTML = '';
    const m = info.meta;

    body.append(el('img', { class: 'detail__thumb', src: thumbUrl(rel), alt: '' }));

    const facts = [
      ['Dimensions', m.width ? m.width + ' × ' + m.height : '—'],
      ['File', (m.format || '?') + ' · ' + bytes(m.size)],
    ].concat((m.fields || []).map((f) => [f.label, f.value]));
    if (m.gps) facts.push(['GPS', 'present in the file']);
    if (m.error) facts.push(['Unreadable', m.error]);

    body.append(el('dl', { class: 'facts' }, facts.map(([k, v]) => [
      el('dt', { text: k }), el('dd', { text: v }),
    ])));
    body.append(el('p', { class: 'field__help field__hint', text:
      'Read-only — the console never rewrites a photo file.' }));
    if (!READ_ONLY) body.append(el('div', { class: 'row' }, linkButton(rel, 'Pretty link…')));

    body.append(el('h3', { class: 'detail__sub', text: 'Tags' }));
    body.append(tagChips(info.tags, READ_ONLY ? null : (next) =>
      applyTags({ photos: [rel], set: next })));
    if (!READ_ONLY) {
      body.append(tagInput('Add a tag…', (tag) => applyTags({ photos: [rel], add: [tag] })));
    }
  }).catch((err) => {
    body.innerHTML = '';
    body.append(el('div', { class: 'empty-note', text: err.message }));
  });

  return box;
}

/* The bulk panel: whatever is selected gets tagged together. It lives in the
 * side column rather than as a bar across the bottom — a sticky bar covered
 * the photos it existed to tag. */
function renderTagBar(b) {
  const count = b.selected.size;
  const picked = [...b.selected];
  const box = el('aside', { class: 'detail bulk' });
  box.append(el('div', { class: 'detail__head' },
    el('span', { class: 'detail__name',
                 text: count + ' photo' + (count === 1 ? '' : 's') + ' selected' }),
    el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕',
      title: 'deselect all',
      onclick: () => { b.selected.clear(); renderPane(); },
    })));

  const body = el('div', { class: 'detail__body' });
  box.append(body);

  // What the selection already carries, so removing a tag is a click and not
  // a guess typed into a prompt.
  const common = new Map();
  for (const rel of picked) {
    for (const tag of (tagsOf(rel) || [])) {
      common.set(tag, (common.get(tag) || 0) + 1);
    }
  }
  if (common.size) {
    body.append(el('h3', { class: 'detail__sub', text: 'Tags in the selection' }));
    body.append(el('div', { class: 'chips' }, [...common.entries()]
      .sort((a, c) => c[1] - a[1] || a[0].localeCompare(c[0]))
      .map(([tag, n]) => el('span', { class: 'chip' },
        tag + (n < count ? ' (' + n + '/' + count + ')' : ''),
        READ_ONLY ? null : el('button', {
          class: 'chip__x', type: 'button', text: '✕',
          title: 'remove from all ' + count,
          onclick: () => applyTags({ photos: picked, remove: [tag] }),
        })))));
  }

  if (!READ_ONLY) {
    body.append(el('h3', { class: 'detail__sub', text: 'Add to all' }));
    body.append(tagInput('Type a tag, press ↵', (tag) =>
      applyTags({ photos: picked, add: [tag] })));
    body.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--danger', type: 'button', text: 'Clear every tag',
      onclick: () => {
        if (confirm('Remove every tag from the ' + count + ' selected photos?')) {
          applyTags({ photos: picked, set: [] });
        }
      },
    }));
  }
  return box;
}

/* Tags of one photo out of the folder payload already in the cache — the
 * bulk panel needs them without a request per selected photo. */
function tagsOf(rel) {
  const payload = photoCache.get((state.browse.path || '::root') + '|t');
  if (!payload) return [];
  const photo = payload.photos.find((p) => p.rel === rel);
  return photo ? (photo.tags || []) : [];
}

async function applyTags(payload) {
  try {
    const res = await api('/api/tags', { method: 'PUT', body: JSON.stringify(payload) });
    // The response carries each photo's new tag list, so the cached folder is
    // patched in place rather than thrown away: a refetch would land after the
    // re-render, leaving the panel briefly showing no tags at all.
    const cached = photoCache.get((state.browse.path || '::root') + '|t');
    if (cached) {
      for (const photo of cached.photos) {
        if (photo.rel in res.tags) photo.tags = res.tags[photo.rel];
      }
    }
    invalidateFolder(state.browse ? state.browse.path : '');
    if (cached) photoCache.set((state.browse.path || '::root') + '|t', cached);
    await loadVocab();
    renderPane();
    toast('Tags updated on ' + res.changed + ' photo' + (res.changed === 1 ? '' : 's'));
  } catch (err) {
    toast('Tagging failed: ' + err.message, 'err');
  }
}

function tagChips(tags, onChange) {
  if (!tags.length) return el('div', { class: 'empty-note', text: 'No tags yet.' });
  return el('div', { class: 'chips' }, tags.map((tag) =>
    el('span', { class: 'chip' }, tag,
      onChange ? el('button', {
        class: 'chip__x', type: 'button', text: '✕', title: 'remove',
        onclick: () => onChange(tags.filter((t) => t !== tag)),
      }) : null)));
}

/* A tag field backed by the gallery-wide vocabulary, so the same idea does
 * not end up spelled three ways across an album. */
function tagInput(placeholder, onSubmit, extraClass) {
  const listId = 'tagvocab';
  if (!$('#' + listId)) {
    document.body.append(el('datalist', { id: listId }));
  }
  const datalist = $('#' + listId);
  datalist.innerHTML = '';
  for (const t of state.vocab) {
    datalist.append(el('option', { value: t.name, label: t.name + ' (' + t.count + ')' }));
  }
  return el('input', {
    class: extraClass || '', type: 'text', placeholder, list: listId, autocomplete: 'off',
    onkeydown: (ev) => {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      const tag = ev.target.value.trim();
      if (!tag) return;
      ev.target.value = '';
      onSubmit(tag);
    },
  });
}

/* ----- description tab -------------------------------------------------- */
function renderDescriptions() {
  const area = el('textarea', { class: 'u-tall', spellcheck: 'false', disabled: READ_ONLY });
  area.value = state.data.descriptions[state.descLang] || '';

  return el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help tabnote u-mb' },
      'Markdown shown under the album hero, one file per language. Saving an empty ' +
      'editor deletes that language’s file.'),
    /* The gallery's own language selector is a segmented control, and this
     * is the same choice being made — so it is the same control. */
    el('div', { class: 'toggle desc-langs' }, state.meta.langs.map((lang) =>
      el('button', {
        class: state.descLang === lang ? 'is-on' : '',
        type: 'button',
        text: 'album_' + lang + '.md' + (state.data.descriptions[lang] ? '' : ' (empty)'),
        onclick: () => {
          state.data.descriptions[state.descLang] = area.value;
          state.descLang = lang;
          renderPane();
        },
      }))),
    area,
    el('div', { class: 'savebar' },
      el('span', { class: 'savebar__note', text: 'editing album_' + state.descLang + '.md' }),
      el('button', {
        class: 'btn btn--primary', type: 'button', text: 'Save description', disabled: READ_ONLY,
        onclick: async () => {
          try {
            const payload = await api('/api/album/description', {
              method: 'PUT',
              body: JSON.stringify({ album: state.sel.album, lang: state.descLang, text: area.value }),
            });
            state.data.descriptions[state.descLang] = payload.text;
            renderPane();
            toast('Description saved');
          } catch (err) {
            toast('Save failed: ' + err.message, 'err');
          }
        },
      })));
}

/* ----- assets tab ------------------------------------------------------- */
function renderAssets() {
  const gallery = assetScope() === 'gallery';
  /* In .gallery/ every file is usable by something, so nothing is filtered
   * out; in an album only the three roles the gallery reads are. */
  const usable = gallery
    ? (state.data.assets || [])
    : (state.data.assets || []).filter(
        (a) => ['icon', 'font', 'wallpaper'].some((k) => assetIs(a, k)));
  const list = el('div', { class: 'assets' }, usable.length
    ? usable.map(renderAssetRow)
    : el('div', { class: 'empty-note', text: gallery
        ? 'Nothing in photos/.gallery/ yet — this is where the logo, the operator’s portrait, the footer badges and the site’s own face and backdrop go.'
        : 'Nothing in this album’s .album/ folder yet.' }));

  const input = el('input', {
    type: 'file', class: 'u-hidden',
    onchange: (ev) => { if (ev.target.files.length) upload(ev.target.files[0]); },
  });
  const drop = el('div', {
    class: 'dropzone',
    text: READ_ONLY ? 'Read-only — uploads are disabled.'
      : gallery
        ? 'Drop a mark, a badge, a display face or a backdrop here, or click to choose. Accepted: ' +
          (state.meta.brand_exts || []).join(', ')
        : 'Drop an icon or a title font here, or click to choose. Accepted: ' +
          state.meta.icon_exts.concat(state.meta.font_exts).join(', '),
    onclick: () => { if (!READ_ONLY) input.click(); },
    ondragover: (ev) => { ev.preventDefault(); drop.classList.add('is-over'); },
    ondragleave: () => drop.classList.remove('is-over'),
    ondrop: (ev) => {
      ev.preventDefault();
      drop.classList.remove('is-over');
      if (!READ_ONLY && ev.dataTransfer.files.length) upload(ev.dataTransfer.files[0]);
    },
  });

  async function upload(file) {
    const form = new FormData();
    form.append('path', state.sel.album || '');
    form.append('scope', assetScope());
    form.append('file', file);
    try {
      const res = await fetch('/api/asset', {
        method: 'POST',
        body: form,
        headers: CSRF ? { 'X-Aperture-CSRF': CSRF } : {},
      });
      if (res.status === 401) { toLogin('timeout'); return; }
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || res.statusText);
      state.data.assets = payload.assets;
      renderPane();
      toast(gallery
        ? 'Uploaded ' + payload.name + ' — now point a gallery.cfg key at it on the Settings tab'
        : 'Uploaded ' + payload.name + ' — now pick it as the album’s ' + payload.kind);
    } catch (err) {
      toast('Upload failed: ' + err.message, 'err');
    }
  }

  return el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help tabnote u-mb' }, gallery
      ? 'Files here live in photos/.gallery/ next to the gallery.cfg — the gallery-wide ' +
        'counterpart of an album’s .album/. Uploading one does not select it — point ' +
        '`logo`, `favicon`, `operator_pfp`, `badges`, `font` or one of the wallpaper ' +
        'keys at it on the Settings tab.'
      : 'Files here live in ' + state.sel.album + '/.album/ next to the album.cfg. ' +
        'Uploading one does not select it — point `icon`, `font` or one of the ' +
        'wallpaper keys at it on the Settings tab.'),
    list, el('div', { class: 'u-gap' }), drop, input);
}

/* "in use" means: some cfg key points at this file. A wallpaper can be
 * claimed by either of the two keys, so kind alone doesn't answer it. */
function assetInUse(asset) {
  const keys = assetScope() === 'gallery' ? BRAND_CFG_KEYS : ASSET_CFG_KEYS;
  if (keys.some((k) => value(k) === asset.name)) return true;
  /* a badge is named inside a `file | label` line rather than by a key of
   * its own, so the badge list has to be read the same way the gallery does */
  return assetScope() === 'gallery'
    && (value('badges') || []).some((b) => b.split('|')[0].trim() === asset.name);
}

function renderAssetRow(asset) {
  const url = assetQuery(asset.name);
  const folder = assetFolder();
  return el('div', { class: 'asset' },
    el('div', { class: 'asset__icon' },
      /* A clip gets a play mark, a real image gets shown, everything else —
       * a face, the cfg itself — gets "Aa". It used to put ANY .gallery/ file
       * in an <img>, back when that folder only held marks; a font or the
       * gallery.cfg then rendered as a broken image and a 415 in the console. */
      VIDEO_RE.test(asset.name) ? '▶'
        : IMAGE_RE.test(asset.name) ? el('img', { src: url, alt: '' })
        : 'Aa'),
    el('span', { class: 'asset__name', text: asset.name }),
    assetInUse(asset) ? el('span', { class: 'pill pill--ok', text: 'in use' }) : null,
    el('span', { class: 'asset__meta', text: bytes(asset.size) }),
    READ_ONLY ? null : el('button', {
      class: 'btn btn--sm btn--ghost btn--danger', type: 'button', text: 'Delete',
      onclick: async () => {
        if (!confirm('Delete ' + asset.name + ' from ' + folder + '?')) return;
        try {
          const payload = await api(assetQuery(asset.name), { method: 'DELETE' });
          state.data.assets = payload.assets;
          renderPane();
          toast('Deleted ' + asset.name);
        } catch (err) {
          toast('Delete failed: ' + err.message, 'err');
        }
      },
    }));
}

/* ----- photo picker modal ----------------------------------------------- */
/* Same folder-by-folder browsing as the Photos tab. `picked` holds paths in
 * the form the cfg stores them: relative to the album for album.cfg, relative
 * to the photos root for gallery.cfg. */
const picker = { picked: [], root: '', path: '', single: false, onApply: null, gallery: false };

function wireModal() {
  $$('#modal [data-close]').forEach((node) =>
    node.addEventListener('click', () => { $('#modal').hidden = true; }));
  $('#modal-filter').addEventListener('input', drawPicker);
  $('#modal-ok').addEventListener('click', () => {
    $('#modal').hidden = true;
    if (picker.onApply) picker.onApply(picker.picked.slice());
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !$('#modal').hidden) $('#modal').hidden = true;
  });
}

function openPicker({ title, root, picked, single, onApply, gallery }) {
  Object.assign(picker, {
    picked: (picked || []).slice(),
    root: root || '', path: root || '',
    single: !!single, onApply, gallery: !!gallery,
  });
  $('#modal-title').textContent = title;
  $('#modal-filter').value = '';
  $('#modal').hidden = false;
  drawPicker();
}

/* The key a picked photo is stored under, given its full path. */
const pickKey = (rel) => picker.gallery ? rel
  : (picker.root ? rel.slice(picker.root.length + 1) : rel);

function drawPicker() {
  const body = $('#modal-body');
  const filter = $('#modal-filter').value.trim().toLowerCase();

  const head = el('div', {}, crumbs(picker.root, picker.path, (path) => {
    picker.path = path;
    $('#modal-filter').value = '';
    drawPicker();
  }));

  body.innerHTML = '';
  body.append(head);
  const slot = el('div', {}, el('div', { class: 'empty-note', text: 'Loading…' }));
  body.append(slot);
  updateModalCount();

  const wanted = picker.path;
  // With a filter typed, search the whole subtree — otherwise the filter would
  // only ever see the handful of photos in the folder you happen to be in.
  const req = filter
    ? api('/api/photos?recursive=1&path=' + encodeURIComponent(wanted))
    : loadFolder(wanted);

  req.then((payload) => {
    if (picker.path !== wanted) return;
    slot.innerHTML = '';

    if (!filter && payload.folders.length) {
      slot.append(el('div', { class: 'folders' },
        payload.folders.map((f) => folderTile(f, (path) => {
          picker.path = path;
          drawPicker();
        }))));
    }

    const photos = filter
      ? payload.photos.filter((p) => p.rel.toLowerCase().includes(filter))
      : payload.photos;

    if (!photos.length) {
      slot.append(el('div', { class: 'empty-note',
        text: filter ? 'Nothing matches in this folder or below.'
                     : payload.folders.length ? 'No photos directly here — open a folder above.'
                     : 'This folder is empty.' }));
      return;
    }

    const grid = el('div', { class: 'grid' });
    for (const photo of photos.slice(0, 600)) {
      const key = pickKey(photo.rel);
      const badge = el('span', { class: 'cell__roles' }, el('span', { class: 'cell__role' }));
      const cell = el('div', {
        class: 'cell',
        title: key,
        onclick: () => {
          const idx = picker.picked.indexOf(key);
          if (picker.single) picker.picked = idx >= 0 ? [] : [key];
          else if (idx >= 0) picker.picked.splice(idx, 1);
          else picker.picked.push(key);
          markPicks(grid);
        },
      },
        el('img', { src: thumbUrl(photo.rel), alt: '', loading: 'lazy' }),
        badge,
        el('span', { class: 'cell__foot' },
          el('span', { class: 'cell__name', text: photo.name })));
      cell._pickKey = key;
      cell._badge = badge;
      grid.append(cell);
    }
    markPicks(grid);
    slot.append(grid);
    if (photos.length > 600) {
      slot.append(el('div', { class: 'empty-note',
        text: 'Showing the first 600 of ' + photos.length + ' — narrow the filter.' }));
    }
  }).catch((err) => {
    slot.innerHTML = '';
    slot.append(el('div', { class: 'empty-note', text: err.message }));
  });
}

/* Repaint which tiles are picked, in place. Redrawing the whole grid on every
 * click emptied the modal body first, which threw its scroll position back to
 * the top — so picking the tenth photo of a folder meant scrolling down to it
 * again — and re-attached up to 600 thumbnails to do it. */
function markPicks(grid) {
  grid.querySelectorAll('.cell').forEach((cell) => {
    const at = picker.picked.indexOf(cell._pickKey);
    cell.classList.toggle('is-picked', at >= 0);
    cell._badge.classList.toggle('is-off', at < 0);
    cell._badge.firstChild.textContent = picker.single ? '✓' : String(at + 1);
  });
  updateModalCount();
}

function updateModalCount() {
  $('#modal-count').textContent = picker.single
    ? (picker.picked.length ? picker.picked[0] : 'nothing selected')
    : picker.picked.length + ' selected · click order becomes list order';
}

/* ----- pretty links ----------------------------------------------------- */
/* photos/.gallery/links.cfg as a screen: a short address on the public site
 * for one album or one photo — `/tokyo` instead of `/album/japan_2026/tokyo`.
 *
 * The server decides what a name may be and whether a target exists
 * (aperture/links.py), and says so on every write. The rule is mirrored here
 * only so a typo shows while it is typed rather than after a round trip; the
 * reserved names come from the server rather than from a second list. */
const LINK_SLUG = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const PHOTO_RE = /\.(jpe?g|png|webp|gif|bmp|tiff?|heic|heif)$/i;
const links = { data: null, draft: null, filter: '' };

const blankDraft = (seed = {}) => ({
  slug: seed.target ? suggestSlug(seed.target) : '',
  target: seed.target || '',
  was: null,
  touched: false,       // the name was typed, so a new target stops renaming it
});

/* The button the album header and the photo panel carry: open Links with the
 * form already pointing at this album or photo. */
function linkButton(target, label) {
  return el('button', {
    type: 'button', class: 'btn btn--sm', text: label,
    title: 'A short address on the public site for this ' + (PHOTO_RE.test(target) ? 'photo' : 'album'),
    onclick: () => select({ kind: 'links', draft: { target } }),
  });
}

/* A name out of a folder or file name: `Mt. Fuji_02.jpg` -> `mt-fuji-02`.
 * A name that is not Latin comes out empty, which leaves the field to type. */
function suggestSlug(target) {
  const leaf = String(target || '').split('/').filter(Boolean).pop() || '';
  return leaf.replace(PHOTO_RE, '')
    .normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '').slice(0, 64).replace(/-+$/, '');
}

function slugProblem(slug, was) {
  const d = links.data;
  if (!slug) return null;
  if (slug.length > d.slug_max) return 'at most ' + d.slug_max + ' characters';
  if (!LINK_SLUG.test(slug) || slug.includes('--')) {
    return 'lower-case letters, digits and single hyphens — starting and ending with a letter or digit';
  }
  if (d.reserved.includes(slug)) return '/' + slug + ' is one of the gallery’s own addresses';
  if (slug !== was && d.links.some((l) => l.slug === slug)) {
    return '/' + slug + ' already exists — edit it below instead';
  }
  return null;
}

/* The address a visitor types. The console is on its own port and cannot
 * know it unless PUBLIC_BASE_URL says; without that it is a path. */
const publicUrl = (slug) => (links.data.base || '') + '/' + slug;

async function renderLinks(seed) {
  try {
    links.data = await api('/api/links');
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: err.message }));
    return;
  }
  if (!state.sel || state.sel.kind !== 'links') return;   /* navigated away */
  links.draft = blankDraft(seed);
  links.filter = '';
  paintLinks();
}

function paintLinks() {
  const pane = $('#pane');
  const d = links.data;
  const broken = d.links.filter((l) => l.issues.length).length;
  pane.innerHTML = '';

  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: state.meta.photos_dir + '/.gallery/links.cfg' }),
      el('div', { class: 'head__line' },
        el('h1', { class: 'head__title', text: 'Links' }),
        el('div', { class: 'head__meta' },
          el('span', { class: 'pill' + (d.links.length ? ' pill--ok' : ''),
                       text: d.links.length + (d.links.length === 1 ? ' link' : ' links') }),
          broken ? el('span', { class: 'pill pill--err', text: broken + ' broken' }) : null,
          READ_ONLY ? el('span', { class: 'pill pill--warn', text: 'read-only' }) : null)))));

  const grid = el('div', { class: 'home' });
  pane.append(grid);
  if (!READ_ONLY) grid.append(el('div', { class: 'home__wide' }, linkForm()));
  grid.append(el('div', { class: 'home__wide' }, linkList()));
}

function linkForm() {
  const draft = links.draft;
  const editing = !!draft.was;
  const albums = [];
  (function walk(node) {
    for (const child of node.children || []) { albums.push(child.path); walk(child); }
  })(state.tree || { children: [] });

  const hint = el('p', { class: 'links__hint' });
  const thumb = el('span', { class: 'links__thumb' });
  const save = el('button', {
    type: 'button', class: 'btn btn--primary', text: editing ? 'Save link' : 'Create link',
    onclick: () => saveLink(),
  });
  const onEnter = (ev) => { if (ev.key === 'Enter' && !save.disabled) saveLink(); };

  const slugField = el('input', {
    type: 'text', value: draft.slug, placeholder: 'tokyo', maxlength: '64',
    autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Link name',
    oninput: (ev) => {
      draft.slug = ev.target.value.trim().toLowerCase();
      draft.touched = true;
      sync();
    },
    onkeydown: onEnter,
  });
  const targetField = el('input', {
    type: 'text', value: draft.target, list: 'links-albums',
    placeholder: 'an album — or album/photo.jpg', autocomplete: 'off', spellcheck: 'false',
    'aria-label': 'Where the link goes',
    oninput: (ev) => retarget(ev.target.value.trim()),
    onkeydown: onEnter,
  });

  function retarget(next) {
    draft.target = next;
    targetField.value = next;
    if (!draft.touched && !editing) {
      draft.slug = suggestSlug(next);
      slugField.value = draft.slug;
    }
    sync();
  }

  function sync() {
    const problem = slugProblem(draft.slug, draft.was);
    hint.classList.toggle('is-bad', !!problem);
    hint.textContent = problem
      || (draft.slug && draft.target ? publicUrl(draft.slug) + '  →  ' + draft.target
        : 'The name is the address: ' + publicUrl(draft.slug || 'name'));
    save.disabled = !draft.slug || !draft.target || !!problem;
    /* What the link will show, when it is a photo or an album the tree knows. */
    const cover = PHOTO_RE.test(draft.target) ? draft.target
      : (treeNode(draft.target) || {}).cover;
    thumb.innerHTML = '';
    if (cover) {
      thumb.append(el('img', { src: thumbUrl(cover), alt: '', loading: 'lazy',
                               onerror: (ev) => ev.target.remove() }));
    }
  }
  sync();

  return card(editing ? 'Edit /' + draft.was : 'New link',
    'one album or one photo, at a short address',
    el('div', { class: 'links__form' },
      thumb,
      el('div', { class: 'links__field' },
        el('span', { class: 'links__label', text: 'Name' }),
        el('div', { class: 'links__addr' },
          el('span', { class: 'links__base', text: (links.data.base || '') + '/',
                       title: links.data.base || 'set PUBLIC_BASE_URL to show the full address' }),
          slugField)),
      el('div', { class: 'links__field' },
        el('span', { class: 'links__label', text: 'Goes to' }),
        el('div', { class: 'links__target' },
          targetField,
          el('button', {
            type: 'button', class: 'btn', text: 'Pick a photo…',
            onclick: () => openPicker({
              title: 'Link to one photo', root: '', single: true, gallery: true,
              picked: PHOTO_RE.test(draft.target) ? [draft.target] : [],
              onApply: (list) => { if (list[0]) retarget(list[0]); },
            }),
          }))),
      el('datalist', { id: 'links-albums' }, albums.map((path) => el('option', { value: path })))),
    hint,
    el('div', { class: 'card__actions' },
      save,
      editing || draft.slug || draft.target
        ? el('button', {
            type: 'button', class: 'btn btn--ghost', text: editing ? 'Cancel' : 'Clear',
            onclick: () => { links.draft = blankDraft(); paintLinks(); },
          })
        : null));
}

function linkList() {
  const d = links.data;
  const box = el('div', { class: 'linkrows' });

  /* Filtering redraws the rows only, so the field keeps its focus. */
  function draw() {
    const f = links.filter.toLowerCase();
    const rows = d.links.filter((l) => !f || l.slug.includes(f) || l.target.toLowerCase().includes(f));
    box.innerHTML = '';
    if (!rows.length) {
      box.append(el('p', { class: 'card__quiet', text: d.links.length
        ? 'No link matches.'
        : 'No links yet. Name an album or a photo above — or use “Link…” in an album’s header.' }));
    }
    rows.forEach((link) => box.append(linkRow(link)));
  }
  draw();

  return card('Every link',
    d.base || 'set PUBLIC_BASE_URL to show and open full addresses',
    d.links.length > 6
      ? el('input', {
          type: 'search', class: 'fieldsearch links__filter', value: links.filter,
          placeholder: 'Filter links…', autocomplete: 'off',
          oninput: (ev) => { links.filter = ev.target.value.trim(); draw(); },
        })
      : null,
    box);
}

function linkRow(link) {
  const url = publicUrl(link.slug);
  const bad = link.issues.length > 0;
  return el('div', { class: 'linkrow' + (bad ? ' is-bad' : '') },
    el('span', { class: 'linkrow__thumb' }, link.thumb
      ? el('img', { src: thumbUrl(link.thumb), alt: '', loading: 'lazy',
                    onerror: (ev) => ev.target.remove() })
      : null),
    el('div', { class: 'linkrow__main' },
      el('div', { class: 'linkrow__slug', text: '/' + link.slug, title: url }),
      el('div', { class: 'linkrow__target' },
        el('span', { class: 'linkrow__kind', text: link.kind }),
        el('span', { class: 'linkrow__path', text: link.target || '—' })),
      bad ? el('div', { class: 'linkrow__issue', text: link.issues.map((i) => i.detail).join(' · ') })
          : null),
    el('div', { class: 'linkrow__actions' },
      el('button', { type: 'button', class: 'btn btn--sm', text: 'Copy', onclick: () => copyLink(url) }),
      /* Only with a known public address: a bare /name opened from here
       * would ask the console's own port, which has no such page. */
      links.data.base && link.destination
        ? el('a', { class: 'btn btn--sm btn--ghost', href: url, target: '_blank',
                    rel: 'noopener noreferrer', text: 'Open ↗' })
        : null,
      READ_ONLY ? null : el('button', {
        type: 'button', class: 'btn btn--sm btn--ghost', text: 'Edit',
        onclick: () => {
          links.draft = { slug: link.slug, target: link.target, was: link.slug, touched: true };
          paintLinks();
          $('#pane').scrollTo({ top: 0, behavior: 'instant' });
        },
      }),
      READ_ONLY ? null : el('button', {
        type: 'button', class: 'btn btn--sm btn--ghost btn--danger', text: 'Delete',
        onclick: () => deleteLink(link.slug),
      })));
}

async function saveLink() {
  const draft = links.draft;
  try {
    links.data = await api('/api/links', {
      method: 'PUT',
      body: JSON.stringify({ slug: draft.slug, target: draft.target, was: draft.was }),
    });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  toast((draft.was ? 'Saved ' : 'Created ') + publicUrl(draft.slug));
  links.draft = blankDraft();
  paintLinks();
}

async function deleteLink(slug) {
  if (!confirm('Delete /' + slug + '? Anyone who follows it gets a 404 from now on.')) return;
  try {
    links.data = await api('/api/links?slug=' + encodeURIComponent(slug), { method: 'DELETE' });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  if (links.draft.was === slug) links.draft = blankDraft();
  toast('Deleted /' + slug);
  paintLinks();
}

/* The clipboard API needs a secure context, and a console reached over plain
 * http on the LAN is not one; the fallback selects a hidden field and copies. */
async function copyLink(url) {
  try {
    await navigator.clipboard.writeText(url);
    toast('Copied ' + url);
    return;
  } catch (_) { /* fall through */ }
  const field = el('input', { type: 'text', value: url, class: 'links__copy', readonly: true });
  document.body.append(field);
  field.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) { /* not supported */ }
  field.remove();
  toast(ok ? 'Copied ' + url : 'Copy this: ' + url, ok ? 'ok' : 'warn');
}

/* ----- whole-gallery check ---------------------------------------------- */
async function checkAll() {
  toast('Checking every config…');
  let payload;
  try {
    payload = await api('/api/validate');
  } catch (err) {
    toast('Check failed: ' + err.message, 'err');
    return;
  }
  countIssues(payload);
  renderTree();
  /* The result belongs on the screen that is about the state of the archive,
   * not on a screen of its own that replaces whatever was open. */
  home.issues = payload;
  toast(payload.errors
    ? payload.errors + ' error(s), ' + payload.warnings + ' warning(s)'
    : payload.warnings
      ? payload.warnings + ' warning(s), no errors'
      : 'Every config file checks out',
    payload.errors ? 'err' : payload.warnings ? 'warn' : 'ok');
  if (state.sel && state.sel.kind === 'home') paintHome();
  else select({ kind: 'home' });
}

function countIssues(payload) {
  state.issuesByAlbum = {};
  for (const issue of payload.issues) {
    if (issue.scope === 'album') {
      state.issuesByAlbum[issue.album] = (state.issuesByAlbum[issue.album] || 0) + 1;
    }
  }
}

/* Refresh the per-album error dots after a save, quietly. */
async function refreshIssueDots() {
  try {
    countIssues(await api('/api/validate'));
    renderTree();
  } catch (_) { /* the dots are a nicety, not worth a toast */ }
}

window.addEventListener('beforeunload', (ev) => {
  if (dirty()) { ev.preventDefault(); ev.returnValue = ''; }
});

boot();
