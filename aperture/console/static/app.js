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
  sel: null,          // {kind: 'gallery'|'album', album}
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
 * silently stops saving. */
function toLogin() {
  window.location.replace('/login');
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
  if (res.status === 401) { toLogin(); throw new Error('signed out'); }
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

const bytes = (n) => !n ? '—'
  : n > 1048576 ? (n / 1048576).toFixed(1) + ' MB'
  : n > 1024 ? Math.round(n / 1024) + ' KB' : n + ' B';

/* Previews always come from /api/thumb, which hands back the gallery's own
 * thumbnail when that tree is mounted — a grid of 200px tiles must never pull
 * the full-size originals. */
const thumbUrl = (rel, size) =>
  '/api/thumb?size=' + (size || 200) + '&path=' + encodeURIComponent(rel);

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
    select({ kind: 'gallery' });
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: 'Cannot reach the backend: ' + err.message }));
  }
  $('#btn-reload').addEventListener('click', async () => {
    photoCache.clear();
    await Promise.all([loadTree(), loadVocab()]);
    if (state.sel) select(state.sel, true);
    toast('Reloaded');
  });
  $('#btn-check').addEventListener('click', () => { setDrawer(false); checkAll(); });
  const signout = $('#btn-signout');
  if (signout) {
    signout.addEventListener('click', async () => {
      try { await api('/api/session', { method: 'DELETE' }); } catch (_) { /* going anyway */ }
      toLogin();
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

  /* Operations sits above the config tree because it is a different errand:
   * everything below this row edits a file, this row watches and drives the
   * indexer. It is the CLI's `status`, `scan`, `pause` and `doctor` — the
   * console can reach them now that it ships with the gallery. */
  list.append(el('li', {}, el('div', {
    class: 'tree__row tree__row--ops' +
      (state.sel && state.sel.kind === 'ops' ? ' is-active' : ''),
    onclick: () => select({ kind: 'ops' }),
  },
    el('span', { class: 'tree__twisty is-leaf' }),
    el('span', { class: 'tree__name', text: 'operations' }),
    el('span', { class: 'tree__count', id: 'ops-lamp', text: '' }))));

  list.append(el('li', {}, el('div', {
    class: 'tree__row tree__row--gallery' +
      (state.sel && state.sel.kind === 'gallery' ? ' is-active' : ''),
    onclick: () => select({ kind: 'gallery' }),
  },
    el('span', { class: 'tree__twisty is-leaf' }),
    el('span', { class: 'tree__name', text: 'gallery.cfg' }),
    el('span', { class: 'tree__count', text: 'root' }))));

  if (!state.tree) return;
  for (const child of state.tree.children) {
    const item = renderNode(child, 1, filter);
    if (item) list.append(item);
  }
  if (list.children.length === 2 && filter) {
    list.append(el('li', { class: 'tree__empty', text: 'No album matches.' }));
  }
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

  const row = el('div', {
    class: 'tree__row' + (active ? ' is-active' : ''),
    style: 'padding-left:' + (depth * 12) + 'px',
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
      ? el('img', { src: thumbUrl(node.cover, 64), alt: '', loading: 'lazy',
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
  if (!keepTab) { state.tab = 'settings'; state.query = ''; }
  if (isNarrow()) setDrawer(false);
  if (sel.kind === 'album') {
    const parts = sel.album.split('/');
    for (let i = 1; i < parts.length; i++) state.openPaths.add(parts.slice(0, i).join('/'));
  }
  renderTree();
  $('#pane').innerHTML = '';
  $('#pane').append(el('div', { class: 'pane__empty', text: 'Loading…' }));
  if (sel.kind === 'ops') {
    await renderOps();
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

function opsRow(label, value, tone) {
  return el('div', { class: 'ops__row' },
    el('span', { class: 'ops__label', text: label }),
    el('span', { class: 'ops__value' + (tone ? ' is-' + tone : ''), text: value }));
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
  pane.innerHTML = '';

  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: st.control_dir || '' }),
      el('h1', { class: 'head__title', text: 'Operations' }))));

  if (st.error) {
    pane.append(el('div', { class: 'pane__empty', text: st.error }));
    return;
  }

  const paths = st.paths || {};
  const scan = (st.server && st.server.last_scan) || null;
  const res = (scan && scan.result) || {};
  const live = !!st.live;
  const paused = !!st.paused;

  /* ----- the indexer ----- */
  pane.append(el('section', { class: 'ops' },
    el('h2', { class: 'ops__head', text: 'Indexer' }),
    opsRow('state', live ? (paused ? 'paused' : 'running') : 'not running',
           live ? (paused ? 'warn' : 'ok') : 'bad'),
    paused && st.pause
      ? opsRow('paused since', ago(st.pause.since) +
               (st.pause.reason ? ' — ' + st.pause.reason : ''), 'warn')
      : null,
    opsRow('scanning now', (st.server && st.server.scanning)
           ? 'yes (' + (st.server.scan_trigger || '?') + ')' : 'no'),
    opsRow('last scan', scan
           ? ago(scan.finished_at) + ' · ' + (scan.trigger || '?') +
             ' · ' + (scan.seconds != null ? scan.seconds + 's' : '—')
           : 'never'),
    scan && scan.error ? opsRow('last error', scan.error, 'bad') : null,
    scan ? opsRow('last result',
      ['indexed', 'thumbnails', 'previews', 'removed', 'failed']
        .filter((k) => res[k]).map((k) => res[k] + ' ' + k).join(' · ')
        || 'nothing to do',
      res.failed ? 'warn' : null) : null,
    opsRow('scan interval', paths.scan_interval
           ? paths.scan_interval + 's' : 'off (manual only)'),
    opsRow('watcher', paths.watcher ? 'on' : 'off')));

  /* ----- what it is working on ----- */
  const idx = st.index || {};
  pane.append(el('section', { class: 'ops' },
    el('h2', { class: 'ops__head', text: 'Index' }),
    opsRow('photos', String(idx.images ?? '—')),
    opsRow('albums', String(idx.albums ?? '—')),
    opsRow('featured', String(idx.featured ?? '—')),
    opsRow('tags', String(idx.tags ?? '—')),
    opsRow('originals', bytes(idx.bytes)),
    opsRow('database', bytes(idx.db_bytes)),
    opsRow('photos dir', paths.photos || '—'),
    opsRow('data dir', paths.data || '—'),
    opsRow('role', st.role || '—')));

  /* ----- the buttons ----- */
  const ro = !!st.read_only;
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

  pane.append(el('section', { class: 'ops' },
    el('h2', { class: 'ops__head', text: 'Actions' }),
    ro ? el('p', { class: 'ops__note', text:
        'The console is mounted read-only. Nothing here can be started from the browser.' })
       : null,
    el('div', { class: 'ops__form' },
      albumField,
      el('label', { class: 'ops__check' }, forceBox,
        el('span', { text: 'force — re-derive even when mtimes say nothing changed' }))),
    paused ? null : el('div', { class: 'ops__form' }, reasonField),
    el('div', { class: 'ops__buttons' },
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
    el('p', { class: 'ops__note', text:
      'A scan is a request written to the control channel — the same one ' +
      'the CLI uses. It starts within a couple of seconds if an indexer is ' +
      'listening, and waits if none is.' })));

  if (opsState.doctor) pane.append(renderDoctor(opsState.doctor));
}

function renderDoctor(report) {
  const problems = report.problems || {};
  const section = el('section', { class: 'ops' },
    el('h2', { class: 'ops__head', text: 'Doctor' }),
    opsRow('scope', report.scope || 'whole gallery'),
    opsRow('checked', report.photos_on_disk + ' file(s) on disk · ' +
                      report.rows + ' row(s) indexed'),
    opsRow('result', report.total ? report.total + ' problem(s) found'
                                  : 'no problems found',
           report.total ? 'warn' : 'ok'));

  for (const check of Object.keys(problems).sort()) {
    const items = problems[check];
    section.append(el('div', { class: 'ops__finding' },
      el('div', { class: 'ops__finding-head' },
        el('span', { class: 'ops__label', text: check.replace(/_/g, ' ') }),
        el('span', { class: 'ops__count', text: String(items.length) })),
      el('ul', { class: 'ops__list' },
        items.slice(0, 25).map((item) =>
          el('li', {},
            el('code', { text: item.rel_path || (item.album + ' · ' + item.key) }),
            el('span', { class: 'ops__detail', text: item.detail || '' }))),
        items.length > 25
          ? el('li', { class: 'ops__more', text: (items.length - 25) + ' more' })
          : null)));
  }
  return section;
}

/* A scan is asynchronous by nature: on a large share over SMB a full pass is
 * minutes. So the request returns an id and this polls for its summary rather
 * than holding an HTTP request open across the whole thing. */
async function startScan(album, force) {
  opsState.busy = true;
  paintOps();
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
    paintOps();
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
      if (state.sel.kind === 'ops') paintOps();
      return;
    }
    /* Ten minutes is longer than any scan this has been pointed at; past it
     * the poller is the thing that is stuck, not the scan. */
    if (Date.now() - started > 600000) {
      clearInterval(opsState.poll);
      opsState.busy = false;
      toast('Still scanning — reload to see the result', 'warn');
      if (state.sel.kind === 'ops') paintOps();
    }
  }, 1500);
}

async function doPause(reason) {
  try {
    await api('/api/ops/pause', { method: 'POST', body: JSON.stringify({ reason: reason || '' }) });
    toast('Indexing paused');
  } catch (err) { toast(err.message, 'err'); return; }
  await loadOpsStatus();
  paintOps();
  renderTree();
}

async function doResume() {
  try {
    await api('/api/ops/resume', { method: 'POST' });
    toast('Indexing resumed');
  } catch (err) { toast(err.message, 'err'); return; }
  await loadOpsStatus();
  paintOps();
  renderTree();
}

async function runDoctor(album) {
  opsState.busy = true;
  paintOps();
  toast('Checking…');
  try {
    opsState.doctor = await api('/api/ops/doctor' +
      (album ? '?album=' + encodeURIComponent(album) : ''));
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    opsState.busy = false;
    if (state.sel.kind === 'ops') paintOps();
  }
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
    : [['settings', 'Settings'], ['photos', 'Photos & tags'], ['text', 'Description'],
       ['assets', 'Files'], ['raw', 'Raw file']];
  if (!tabs.some(([id]) => id === state.tab)) state.tab = 'settings';

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
  }
  const errors = (data.issues || []).filter((i) => i.level === 'error').length;
  const warns = (data.issues || []).filter((i) => i.level === 'warn').length;
  if (errors) meta.push(el('span', { class: 'pill pill--err', text: errors + ' errors' }));
  if (warns) meta.push(el('span', { class: 'pill pill--warn', text: warns + ' warnings' }));

  const path = isGallery
    ? state.meta.photos_dir + '/gallery.cfg'
    : state.meta.photos_dir + '/' + state.sel.album + '/.album/album.cfg';

  /* Title and pills share one line so the sticky block stays short; the file
   * path is the first thing dropped once the pane is scrolled. */
  return el('div', { class: 'head' },
    el('div', { class: 'head__crumb', text: path, title: path }),
    el('div', { class: 'head__line' },
      el('h1', { class: 'head__title', text: isGallery ? 'gallery.cfg' : data.name }),
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
      src: thumbUrl(album + '/' + strip(current), 160), alt: '', loading: 'lazy',
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
      thumb: thumbUrl(album + '/' + strip(item), 120),
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
      thumb: thumbUrl(strip(item), 120),
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
      ? el('img', { src: thumbUrl(folder.cover, 200), alt: '', loading: 'lazy' })
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
      el('img', { src: thumbUrl(photo.rel, 220), alt: '', loading: 'lazy' }),
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

    body.append(el('img', { class: 'detail__thumb', src: thumbUrl(rel, 420), alt: '' }));

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
      if (res.status === 401) { toLogin(); return; }
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
        el('img', { src: thumbUrl(photo.rel, 220), alt: '', loading: 'lazy' }),
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

  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'head' },
    el('h1', { class: 'head__title', text: 'Config check' }),
    el('div', { class: 'head__meta' },
      el('span', { class: 'pill ' + (payload.errors ? 'pill--err' : 'pill--ok'),
                   text: payload.errors + ' errors' }),
      el('span', { class: 'pill ' + (payload.warnings ? 'pill--warn' : ''),
                   text: payload.warnings + ' warnings' }),
      el('span', { class: 'pill', text: payload.took_ms + ' ms' }))));

  if (!payload.issues.length) {
    pane.append(el('div', { class: 'empty-note', text: 'Every config file checks out.' }));
    return;
  }
  pane.append(el('div', { class: 'issues' }, payload.issues.map((issue) =>
    el('div', { class: 'issue issue--' + (issue.level === 'error' ? 'error' : 'warn') },
      el('span', {
        class: 'issue__where',
        text: issue.scope === 'gallery' ? 'gallery.cfg' : issue.album,
        onclick: () => select(issue.scope === 'gallery'
          ? { kind: 'gallery' } : { kind: 'album', album: issue.album }),
      }),
      el('span', { class: 'issue__key', text: issue.key }),
      el('span', { class: 'issue__detail', text: issue.detail })))));
  state.sel = null;
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
