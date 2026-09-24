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
/* A Font Awesome glyph from the gallery's subset. Decorative by rule: the
 * control it sits in carries the words -- a label, a title or an aria-label.
 * The name is always written whole, `icon: 'fa-floppy-disk'`, never glued
 * together: tools/build_fa_subset.py decides which glyphs to keep by reading
 * the string literals in this file, and a name built at runtime is one it
 * cannot see, so it would ship as an empty box. */
function ico(name, end = false) {
  return el('i', { class: 'fa ' + name + (end ? ' is-end' : ''), 'aria-hidden': 'true' });
}

/* A filter field with the gallery's magnifier in it. It takes the input's
 * own attributes, so it swaps in for `el('input', ...)` with nothing else
 * changing. */
function searchbox(attrs) {
  return el('label', { class: 'searchbox' }, ico('fa-magnifying-glass'), el('input', attrs));
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  let icon = null;
  let iconEnd = null;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'icon') icon = v;
    else if (k === 'iconEnd') iconEnd = v;
    else if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  // `text` above has already replaced the node's content, so the glyphs go
  // on last: one before the words, one after.
  if (icon) node.prepend(ico(icon));
  if (iconEnd) node.append(ico(iconEnd, true));
  return node;
}

/* A status is a glyph as well as a colour -- hue alone never says anything. */
const TOAST_GLYPH = {
  ok: 'fa-circle-check', warn: 'fa-triangle-exclamation', err: 'fa-circle-exclamation',
};

function toast(message, kind = 'ok') {
  const box = $('#toast');
  box.replaceChildren(ico(TOAST_GLYPH[kind] || 'fa-circle-info'), message);
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
     * back out, so a later plain reload lands where its address says. */
    try { sessionStorage.setItem(RETURN_KEY, pathFor(state.sel)); } catch (_) { /* fine */ }
  }
  window.location.replace('/login?reason=' + encodeURIComponent(reason));
}

/* The note, consumed: an address, which selFromPath() checks against the
 * tree that has just loaded rather than trusting it -- an album can have
 * been renamed while the door was open. */
function takeReturnNote() {
  let raw = null;
  try {
    raw = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
  } catch (_) { return null; }
  return typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//') ? raw : null;
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

/* ----- boot ------------------------------------------------------------- */
async function boot() {
  loadPrefs();
  // Before anything else: the CSRF token every later write has to carry.
  await refreshSession();
  try {
    state.meta = await api('/api/meta');
    await Promise.all([loadTree(), loadVocab(), loadOpsStatus()]);
    wireShell();
    /* The address says where to start. Coming back through the door after a
     * timeout is the one exception: the door sends everyone to /, so the
     * address that was lost rides in a one-shot note instead. */
    const back = takeReturnNote();
    await select(selFromPath(back || location.pathname), false, 'replace');
    loadUpdates();
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: 'Cannot reach the backend: ' + err.message }));
  }
  /* The boot screen is waiting on this: it covers the "Loading..." strings
   * above, so it has to know when there is something behind it. Sent on the
   * failure path too — an error message is also something to read, and a
   * splash that hangs over one is worse than no splash. */
  document.dispatchEvent(new Event('aperture:ready'));
  // The pane's own header sticks, and folds by CSS alone (a negative sticky
  // top, see .pane__top). The script only tells the bar that content runs
  // under it, which changes its paint and never its size.
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
      const scrolled = pane.scrollTop > 4;
      pane.classList.toggle('is-scrolled', scrolled);
      // the gallery brightens its nav hairline the moment the bar floats
      // over content; here the pane is the thing that scrolls under it
      $('.nav').classList.toggle('nav--scrolled', scrolled);
    });
  }, { passive: true });
}

async function loadTree() {
  state.tree = (await api('/api/tree')).root;
  refreshAlbumViews();
}

async function loadVocab() {
  try {
    state.vocab = (await api('/api/tags')).tags;
  } catch (_) { state.vocab = []; }
}

/* The album tree changed, or what the check says about it did: the rail's
 * counts and, if it is on screen, the Albums table follow. */
function refreshAlbumViews() {
  renderRail();
  if (state.sel && state.sel.kind === 'albums') paintAlbumRows();
}

/* ----- selection -------------------------------------------------------- */
/* Draw a place. `how` is what happens to the address: 'push' for a move you
 * made, 'replace' on boot, 'none' when the address already says it (Back,
 * Forward). Returns false when unsaved changes kept you where you were. */
async function select(sel, keepTab = false, how = 'push') {
  /* Unsaved edits are not thrown away and not asked about: they stay a
   * draft of their file, listed in the bar's "unsaved" tray, and come back
   * when you do. */
  stashDraft();
  state.sel = sel;
  state.edits = takeDraft(sel);
  state.browse = null;
  state.data = null;
  if (!keepTab) { state.tab = 'settings'; state.query = ''; }
  if (sel.kind === 'ops') opsState.tab = sel.tab || 'overview';
  if (how !== 'none') setAddress(sel, how === 'replace');
  syncRail();
  syncDirtyMark();
  $('#pane').scrollTop = 0;
  $('#pane').classList.remove('pane--library');
  $('#pane').innerHTML = '';
  $('#pane').append(el('div', { class: 'pane__empty', text: 'Loading…' }));
  if (sel.kind === 'home') { await renderHome(); return true; }
  if (sel.kind === 'ops') { await renderOps(); return true; }
  if (sel.kind === 'tags') { await renderTagsPlace(); return true; }
  if (sel.kind === 'links') { await renderLinks(sel.draft); return true; }
  if (sel.kind === 'changelog') { await renderChangelog(); return true; }
  if (sel.kind === 'albums') { renderAlbums(); return true; }
  if (sel.kind === 'library') { await renderLibrary(); return true; }
  try {
    state.data = sel.kind === 'gallery'
      ? await api('/api/gallery')
      : await api('/api/album?path=' + encodeURIComponent(sel.album));
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: err.message }));
    return true;
  }
  renderPane();
  return true;
}


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
  // The Library has no file open: nothing is set, so nothing is marked.
  if (!state.data || !state.data.values) return null;
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
  ['The album', 'What it is called, and what this folder is to the gallery.', ['name', 'collection', 'showcase', 'unlisted', 'cover']],
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


/* ============================================================
   OPERATIONS
   ------------------------------------------------------------
   Every CLI command that means anything outside a terminal, in the browser,
   one tab each — the reports from the same functions the CLI renders
   (aperture/ops.py, aperture/reports.py). Nothing here talks to the indexer
   directly: a scan, and every job that writes what the indexer owns
   (derivatives, featured flags, coordinates), is a request written to the
   control channel and picked up by whichever process owns the indexer. So
   this view works identically whether the gallery is in this process or in
   another container — and there is still exactly one writer.
   ============================================================ */
const opsState = {
  status: null, busy: false, poll: null,
  tab: 'overview',
  disk: null, archive: null,   // the overview's two walks, loaded after the status
  reports: {},                 // tab -> the payload its route answered (or {error})
  urls: {},                    // tab -> the request that produced it, to ask again
  loading: {},                 // tab -> true while its request is out
  inputs: {},                  // field -> what was typed, so a repaint keeps it
  job: null,                   // {id, kind, state} of the job this console queued last
  jobPoll: null,
  lookup: null,                // {kind, value} of the Lookup tab's last question
};

/* System's sections. Eight, where there were twelve: what an operator of a
 * gallery comes here to do. Featured, Front page, Translations and Lookup
 * went -- featuring lives in the Library, the welcome lists in the Site
 * editor and its check, translations are a developer's check the CLI
 * still runs, and a photo's diagnostics are in the Library's inspector.
 * Their API routes stay (the CLI parity tests hold them). */
const SYSTEM_GROUPS = [
  ['Machine', [
    ['overview', 'Indexer', 'fa-microchip'],
    ['storage', 'Storage', 'fa-hard-drive'],
    ['health', 'Health check', 'fa-stethoscope'],
  ]],
  ['Data', [
    ['privacy', 'Privacy', 'fa-location-dot'],
    ['export', 'Backup', 'fa-box-archive'],
    ['activity', 'Activity', 'fa-clock-rotate-left'],
  ]],
  ['Console', [
    ['access', 'Password', 'fa-key'],
    ['about', 'About', 'fa-scroll'],
  ]],
];
/* Addresses the old sections had, so a bookmark still lands somewhere
 * sensible. */
const SYSTEM_ALIASES = {
  derivatives: 'storage', doctor: 'health', gps: 'privacy', featured: 'health',
  frontpage: 'overview', i18n: 'overview', lookup: 'overview',
};
const SYSTEM_TABS = SYSTEM_GROUPS.flatMap(([, tabs]) => tabs);

/* The section list down the side of System. A column, not a strip: eleven
 * tabs across the top wrapped onto two rows at every laptop width. */
function systemNav() {
  const current = state.sel.kind === 'changelog' ? 'about' : opsState.tab;
  return el('nav', { class: 'subnav', 'aria-label': 'System' },
    SYSTEM_GROUPS.map(([title, tabs]) => el('div', { class: 'subnav__group' },
      el('span', { class: 'subnav__title', text: title }),
      tabs.map(([id, label, icon]) => el('a', {
        class: 'subnav__link' + (current === id ? ' is-active' : ''),
        href: id === 'about' ? '/system/about' : '/system' + (id === 'overview' ? '' : '/' + id),
        'aria-current': current === id ? 'page' : null, icon, text: label,
        onclick: (ev) => {
          if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
          ev.preventDefault();
          if (id === 'about') { go({ kind: 'changelog' }); return; }
          if (state.sel.kind !== 'ops') { go({ kind: 'ops', tab: id }); return; }
          opsState.tab = id;
          state.sel = { kind: 'ops', tab: id };
          setAddress(state.sel);
          paintOps();
          $('#pane').scrollTop = 0;
        },
      })))));
}

/* A System screen: the section list, and the section beside it. */
function systemFrame(pane, title, crumb, meta, fill) {
  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: crumb || '' }),
      el('div', { class: 'head__line' },
        el('h1', { class: 'head__title', text: title }),
        el('div', { class: 'head__meta' }, meta)))));
  const body = el('div', { class: 'home' });
  pane.append(el('div', { class: 'sysgrid' }, systemNav(), body));
  fill(body);
}

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

/* ----- small parts ------------------------------------------------------ */
const qs = (params) => Object.entries(params)
  .filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false)
  .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v === true ? 'true' : v))
  .join('&');
const opsValue = (id) => String(opsState.inputs[id] ?? '').trim();
const onEnter = (fn) => (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); fn(); } };
const wide = (node) => el('div', { class: 'home__wide' }, node);
const quiet = (text) => el('p', { class: 'card__quiet', text });
const sizeOr0 = (n) => (n ? bytes(n) : '0 B');
const onOps = () => !!state.sel && state.sel.kind === 'ops';

/* A field whose value outlives a repaint: a job finishing repaints the whole
 * screen, and a half-typed album path should still be there afterwards. */
function opsInput(id, attrs = {}) {
  const input = el('input', { type: 'text', autocomplete: 'off', ...attrs,
                              id: 'ops-' + id, value: opsState.inputs[id] ?? '' });
  input.addEventListener('input', () => { opsState.inputs[id] = input.value; });
  return input;
}

function opsCheck(id, label, attrs = {}) {
  const box = el('input', { type: 'checkbox', ...attrs, id: 'ops-' + id,
                            checked: !!opsState.inputs[id] });
  box.addEventListener('change', () => { opsState.inputs[id] = box.checked; });
  return el('label', { class: 'ops__check' }, box, el('span', { text: label }));
}

/* A figure read against its neighbours. SVG, because the bar's length is a
 * geometry attribute there -- under style-src 'self' an inline width would be
 * dropped without a word. */
const SVG_NS = 'http://www.w3.org/2000/svg';
function meter(value, max, tone) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'meter' + (tone ? ' is-' + tone : ''));
  svg.setAttribute('viewBox', '0 0 100 4');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  for (const [cls, width] of [['meter__track', 100], ['meter__fill', pct]]) {
    const rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('class', cls);
    rect.setAttribute('width', String(width));
    rect.setAttribute('height', '4');
    svg.append(rect);
  }
  return svg;
}

function barRow(label, value, max, shown, tone, onclick) {
  return el('div', { class: 'brow' + (onclick ? ' is-link' : ''), onclick: onclick || null, title: label },
    el('span', { class: 'brow__k', text: label }),
    meter(value, max, tone),
    el('span', { class: 'brow__v', text: shown }));
}

function lineRow(text, onclick) {
  return el('div', { class: 'hrow hrow--line' + (onclick ? ' is-link' : ''), onclick: onclick || null },
    el('span', { class: 'hrow__detail', text }));
}

function sub(title, count) {
  return el('h3', { class: 'card__sub' },
    el('span', { text: title }),
    count === undefined ? null : el('span', { class: 'card__sub-n', text: String(count) }));
}

/* Ask a report route, keep the answer under its tab, and repaint if the
 * operator is still here to see it. */
async function opsLoad(tab, url, { quiet: silent = false } = {}) {
  opsState.loading[tab] = true;
  opsState.urls[tab] = url;
  if (!silent && onOps()) paintOps();
  try {
    opsState.reports[tab] = await api(url);
  } catch (err) {
    opsState.reports[tab] = { error: err.message };
  } finally {
    opsState.loading[tab] = false;
  }
  if (onOps()) paintOps();
}

/* Load a tab's report the first time it is shown; the reports that walk the
 * whole share (doctor, derivatives, gps) wait for a button instead. */
function opsAuto(tab, url) {
  if (!opsState.reports[tab] && !opsState.loading[tab]) opsLoad(tab, url, { quiet: true });
}

/* The report as it stands: a note while it loads, its error, or nothing yet. */
function opsPending(tab, icon, title, idle) {
  if (opsState.loading[tab]) return wide(card(icon, title, null, quiet('Asking…')));
  const report = opsState.reports[tab];
  if (report && report.error) return wide(card(icon, title, null, quiet(report.error)));
  if (!report && idle) return wide(card(icon, title, null, quiet(idle)));
  return null;
}

/* ----- the screen ------------------------------------------------------- */
async function renderOps() {
  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__empty', text: 'Reading the control channel…' }));
  await loadOpsStatus();
  if (!onOps()) return;   /* navigated away while we waited */
  paintOps();
}

function paintOps() {
  const pane = $('#pane');
  const st = opsState.status || {};
  const ro = !!st.read_only || READ_ONLY;
  const scroll = pane.scrollTop;
  pane.innerHTML = '';

  const fill = (grid) => {
    if (st.error) {
      grid.append(el('div', { class: 'home__wide pane__empty', text: st.error }));
      return;
    }
    const job = jobCard();
    if (job) grid.append(el('div', { class: 'home__wide', id: 'ops-job' }, job));
    (OPS_PAINT[opsState.tab] || paintIndexer)(grid, st, ro);
  };

  const label = (SYSTEM_TABS.find(([id]) => id === opsState.tab) || SYSTEM_TABS[0])[1];
  systemFrame(pane, label, st.control_dir || '', [
    el('span', { class: 'pill', icon: 'fa-server', text: 'role ' + (st.role || '—') }),
    ro ? el('span', { class: 'pill pill--warn', icon: 'fa-lock', text: 'read-only' }) : null,
  ], fill);
  pane.scrollTop = scroll;
}

/* ----- indexer ---------------------------------------------------------- */
/* The machine and the two things you do to it: scan, pause. The counts are
 * the Overview's; this is where the indexer itself is looked at. */
async function loadDisk() {
  if (opsState.loading.disk) return;
  opsState.loading.disk = true;
  opsState.disk = await api('/api/ops/disk').catch((err) => ({ error: err.message }));
  opsState.loading.disk = false;
  if (onOps() && opsState.tab === 'storage') paintOps();
}

function paintIndexer(grid, st, ro) {
  const paths = st.paths || {};
  const scan = (st.server && st.server.last_scan) || null;
  const res = (scan && scan.result) || {};
  const live = !!st.live;
  const paused = !!st.paused;
  const scanning = !!(st.server && st.server.scanning);
  const tone = paused ? 'warn' : live ? 'ok' : 'bad';
  const word = paused ? 'paused' : scanning ? 'scanning' : live ? 'running' : 'not running';
  const pending = (st.server && st.server.pending_jobs) || [];

  grid.append(wide(card('fa-microchip', 'Indexer', 'the one writer of the index, whoever asks',
    el('div', { class: 'lamp' },
      el('span', { class: 'lamp__dot is-' + tone }),
      el('span', { class: 'lamp__word is-' + tone, text: word }),
      el('span', { class: 'lamp__note', text: scanning
        ? 'triggered by ' + ((st.server && st.server.scan_trigger) || '?')
        : live ? '' : 'no heartbeat — start the gallery to index' })),
    el('dl', { class: 'facts' },
      paused && st.pause
        ? fact('Paused', ago(st.pause.since) + (st.pause.reason ? ' — ' + st.pause.reason : ''), 'warn')
        : null,
      fact('Last scan', scan
        ? ago(scan.finished_at) + ' · ' + (scan.trigger || '?') +
          (scan.seconds != null ? ' · ' + scan.seconds + 's' : '')
        : 'never'),
      fact('It did', scan ? scanSummary(res) : '—', res.failed || res.held ? 'warn' : null),
      scan && scan.error ? fact('Last error', scan.error, 'bad') : null,
      pending.length ? fact('Jobs queued', String(pending.length), 'warn') : null,
      fact('Scans', (paths.scan_interval ? 'every ' + paths.scan_interval + 's' : 'on request only') +
        ' · watcher ' + (paths.watcher ? 'on' : 'off'))),
    ro ? quiet('The console is mounted read-only. Nothing here can be started from the browser.') : null,
    el('div', { class: 'card__actions' },
      el('button', {
        type: 'button', class: 'btn btn--primary', id: 'ops-scan',
        disabled: ro || opsState.busy, icon: 'fa-arrows-rotate', text: 'Scan now',
        onclick: () => startScan(opsValue('album'), !!opsState.inputs.force),
      }),
      el('button', {
        type: 'button', class: 'btn', disabled: ro || opsState.busy,
        icon: paused ? 'fa-play' : 'fa-pause',
        text: paused ? 'Resume indexing' : 'Pause indexing',
        onclick: () => (paused ? doResume(!!opsState.inputs['resume-scan']) : doPause(opsValue('reason'))),
      })),
    /* The rarely needed half, folded: one album instead of all, a forced
     * re-read, why it is paused. */
    el('details', { class: 'ops__more' },
      el('summary', { class: 'release__toggle', icon: 'fa-chevron-down', text: 'Scope, force, a reason' }),
      el('div', { class: 'ops__form' },
        opsInput('album', { placeholder: 'whole gallery — or one album path', disabled: ro,
                            onkeydown: onEnter(() => startScan(opsValue('album'), !!opsState.inputs.force)) }),
        opsCheck('force', 'force — re-read every photo even when nothing seems to have changed', { disabled: ro })),
      el('div', { class: 'ops__form' },
        paused
          ? opsCheck('resume-scan', 'scan right away when resuming', { disabled: ro })
          : opsInput('reason', { placeholder: 'why — shown while paused', disabled: ro }))),
    quiet('A scan starts within a couple of seconds if an indexer is listening, and waits if none is.'))));

  grid.append(wide(card('fa-folder-tree', 'Setup', 'read once at startup from the environment',
    el('dl', { class: 'facts' },
      fact('Photos', paths.photos || '—'),
      fact('Thumbnails', (paths.thumbs || '—') + ' · ' + (paths.thumb_size || '—') + ' px'),
      fact('Previews', (paths.previews || '—') + ' · ' + (paths.preview_size || '—') + ' px'),
      fact('Data', paths.data || '—'),
      fact('Control', st.control_dir || '—'),
      fact('Role', st.role || '—')))));
}

/* ----- storage ---------------------------------------------------------- */
/* What the generated trees cost and whether they are complete -- one topic,
 * which used to be two screens (a disk card on the overview, Derivatives on
 * its own). Thumbnails and previews are a cache: missing ones are built,
 * leftovers deleted, both as jobs in the indexer. */
const TIER_WORDS = { thumbnails: 'thumbnails', previews: 'previews', fulls: 'HEIC conversions' };

function paintStorage(grid, st, ro) {
  if (!opsState.disk) loadDisk();
  grid.append(wide(diskCard()));

  const inspect = () => opsLoad('derivatives', '/api/ops/derivatives?' +
    qs({ album: opsValue('der-album'), all: !!opsState.inputs['der-all'] }));
  const pending = opsPending('derivatives', 'fa-images', 'Thumbnails and previews');
  const r = opsState.reports.derivatives;
  if (pending) { grid.append(pending); return; }
  if (!r) {
    grid.append(wide(card('fa-images', 'Thumbnails and previews', 'missing, stale, left over',
      el('div', { class: 'ops__form' },
        opsInput('der-album', { placeholder: 'whole gallery — or one album path', onkeydown: onEnter(inspect) }),
        opsCheck('der-all', 'count every one, not only the missing and stale')),
      el('div', { class: 'card__actions' },
        el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-magnifying-glass',
                       text: 'Check what needs building', onclick: inspect })),
      quiet('Reads every photo against its thumbnail and preview; on a large share it takes a moment.'))));
    return;
  }
  const scope = r.scope || null;
  const missing = (r.pending || []).length;
  const body = [el('dl', { class: 'facts' },
    fact('Scope', scope || 'whole gallery'),
    fact('Photos', String(r.photos)),
    fact(r.all ? 'To rebuild' : 'To build', r.to_build + ' file(s)', missing ? 'warn' : 'ok'),
    fact('Left over', r.orphans_checked
      ? r.orphans.length + ' file(s) · ' + sizeOr0(r.orphan_bytes)
      : 'not checked — only a whole-gallery check can tell', r.orphans.length ? 'warn' : null))];
  if (missing) {
    body.push(sub('missing or stale', missing));
    body.push(...foldedRows('der:pending', r.pending, 10, (i) =>
      homeRow(i.rel_path, i.kind, i.state, () => showPhoto(i.rel_path))));
  }
  if (r.orphans.length) {
    body.push(sub('no photo maps to these', r.orphans.length));
    body.push(...foldedRows('der:orphans', r.orphans, 10, (path) => lineRow(path)));
  }
  body.push(el('div', { class: 'card__actions' },
    el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-hammer',
      disabled: ro || !missing, text: missing ? 'Build ' + missing + ' missing or stale' : 'Nothing to build',
      onclick: () => startJob('rebuild', { album: scope }) }),
    el('button', { type: 'button', class: 'btn', icon: 'fa-trash-can',
      disabled: ro || !r.orphans_checked || !r.orphans.length,
      text: 'Delete ' + r.orphans.length + ' left over',
      onclick: () => startJob('prune', {},
        'Delete ' + r.orphans.length + ' generated file(s) (' + sizeOr0(r.orphan_bytes) +
        ') that no photo maps to?') }),
    el('button', { type: 'button', class: 'btn btn--ghost', icon: 'fa-hammer', disabled: ro,
      text: 'Rebuild all',
      onclick: () => startJob('rebuild', { album: scope, all: true },
        'Rebuild every thumbnail and preview in ' + (scope || 'the whole gallery') +
        '? On a large share this takes a long time.') }),
    el('button', { type: 'button', class: 'btn btn--ghost', icon: 'fa-rotate-right', text: 'Check again',
      onclick: () => { delete opsState.reports.derivatives; paintOps(); } })));
  grid.append(wide(card('fa-images', 'Thumbnails and previews', r.all ? 'every one' : 'what needs doing', ...body)));
}

function diskCard() {
  const d = opsState.disk;
  const title = 'Disk';
  const note = 'what the generated files cost, next to what they are made from';
  if (!d) return card('fa-hard-drive', title, note, quiet('Measuring the thumbnail and preview trees…'));
  if (d.error) return card('fa-hard-drive', title, note, quiet(d.error));
  const tiers = d.tiers || [];
  const biggest = Math.max(1, ...tiers.map((t) => t.bytes));
  const stale = tiers.filter((t) => t.stale_formats && t.stale_formats.length);
  const body = [
    el('div', { class: 'tiles' },
      tile('generated', sizeOr0(d.derivatives.bytes)),
      tile('originals', sizeOr0(d.originals.bytes)),
      tile('of originals', d.ratio == null ? '—' : (d.ratio * 100).toFixed(1) + '%')),
    el('div', { class: 'brows' }, tiers.map((t) => barRow(
      TIER_WORDS[t.key] || t.key, t.bytes, biggest,
      !t.exists ? 'not there'
        : sizeOr0(t.bytes) + ' · ' + t.files + ' file(s)' +
          (t.per_photo ? ' · ' + bytes(t.per_photo) + '/photo' : '')))),
  ];
  if (stale.length) {
    body.push(sub('left in an old format'));
    body.push(el('div', { class: 'hrows' }, stale.flatMap((t) => t.stale_formats.map((ext) =>
      homeRow(TIER_WORDS[t.key] || t.key, ext,
        t.formats[ext].files + ' file(s) · ' + sizeOr0(t.formats[ext].bytes) +
        ' in a format this tier no longer writes — "Delete left over" below removes them')))));
  }
  const volumes = d.volumes || [];
  if (volumes.length) {
    body.push(sub('volumes'));
    body.push(el('div', { class: 'brows' }, volumes.map((v) => {
      const used = v.total ? v.used / v.total : 0;
      return barRow(v.holds.join(', '), v.used, v.total,
        sizeOr0(v.free) + ' free of ' + sizeOr0(v.total),
        used >= 0.95 ? 'bad' : used >= 0.85 ? 'warn' : null);
    })));
  }
  body.push(el('div', { class: 'card__actions' },
    el('button', { type: 'button', class: 'btn btn--ghost', icon: 'fa-rotate-right', text: 'Measure again',
                   onclick: () => { opsState.disk = null; paintOps(); } })));
  return card('fa-hard-drive', title, note, ...body);
}

/* ----- health check ----------------------------------------------------- */
/* The one check that holds index, files, derivatives, featured flags and
 * cfg against each other -- `doctor`, with its fixes as the jobs that do
 * them. It absorbed what Featured and Tags drift used to report apart. */
function paintOpsDoctor(grid, st, ro) {
  const run = () => opsLoad('doctor', '/api/ops/doctor?' +
    qs({ album: opsValue('doctor-album'), limit_slow: opsValue('doctor-slow') }));
  const report = opsState.reports.doctor;
  grid.append(wide(card('fa-stethoscope', 'Health check',
    'index, files, thumbnails, featured flags and cfg, held against each other',
    el('div', { class: 'ops__form' },
      opsInput('doctor-album', { placeholder: 'whole gallery — or one album path', onkeydown: onEnter(run) })),
    el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-stethoscope',
                     disabled: !!opsState.loading.doctor,
                     text: opsState.loading.doctor ? 'Checking…' : report ? 'Check again' : 'Run the check', onclick: run })),
    quiet('Walks every photo and every generated file, so on a large share it takes a while. ' +
          'Nothing is written — the fixes are jobs you start.'))));
  const pending = opsPending('doctor', 'fa-stethoscope', 'Result');
  if (pending) { grid.append(pending); return; }
  if (!report) return;
  if (report.total) grid.append(wide(doctorNext(report, ro)));
  grid.append(wide(renderDoctor(report)));
}

function renderDoctor(report) {
  const problems = report.problems || {};
  const body = [el('dl', { class: 'facts' },
    fact('Scope', report.scope || 'whole gallery'),
    fact('Checked', report.photos_on_disk + ' file(s) on disk · ' + report.rows + ' row(s) indexed'),
    fact('Result', report.total ? report.total + ' problem(s) found' : 'everything agrees',
         report.total ? 'warn' : 'ok'))];
  for (const check of Object.keys(problems).sort()) {
    const items = problems[check];
    body.push(sub(check.replace(/_/g, ' '), items.length));
    const photoish = !['config', 'database', 'orphan_derivative'].includes(check);
    body.push(...foldedRows('doctor:' + check, items, 15, (item) => homeRow(
      item.rel_path || item.album || '—',
      item.key || '',
      item.detail || '',
      item.album ? () => go({ kind: 'album', album: item.album })
        : photoish && item.rel_path ? () => showPhoto(item.rel_path) : null)));
  }
  return card('fa-list-check', 'Findings', null, ...body);
}

/* The CLI's "what now" block, as the buttons that do it. */
function doctorNext(report, ro) {
  const p = report.problems || {};
  const n = (...keys) => keys.reduce((sum, k) => sum + ((p[k] || []).length), 0);
  const scope = report.scope || null;
  const steps = [];
  const derivs = n('missing_thumb', 'stale_thumb', 'missing_preview', 'stale_preview');
  if (derivs) {
    steps.push(el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-hammer', disabled: ro,
      text: 'Build ' + derivs + ' missing or stale',
      onclick: () => startJob('rebuild', { album: scope }) }));
  }
  if (n('orphan_derivative')) {
    steps.push(el('button', { type: 'button', class: 'btn', icon: 'fa-trash-can', disabled: ro,
      text: 'Delete ' + n('orphan_derivative') + ' left over',
      onclick: () => startJob('prune', {},
        'Delete ' + n('orphan_derivative') + ' generated file(s) that no photo maps to?') }));
  }
  if (n('unindexed', 'missing_file')) {
    steps.push(el('button', { type: 'button', class: 'btn', icon: 'fa-arrows-rotate', disabled: ro || opsState.busy,
      text: 'Scan' + (scope ? ' ' + scope : ''), onclick: () => startScan(scope, false) }));
  }
  if (n('stale_index')) {
    steps.push(el('button', { type: 'button', class: 'btn', icon: 'fa-arrows-rotate', disabled: ro || opsState.busy,
      text: 'Scan with force', onclick: () => startScan(scope, true) }));
  }
  if (n('featured_drift')) {
    steps.push(el('button', { type: 'button', class: 'btn', icon: 'fa-star', disabled: ro,
      text: 'Recompute featured flags', onclick: () => startJob('featured') }));
  }
  const notes = [];
  if (n('config')) notes.push('cfg findings open their album — each one has its fix there.');
  if (n('unreadable')) notes.push('An unreadable file stays in the gallery without a thumbnail until it is replaced or removed.');
  if (n('database')) notes.push('Tags no photo uses any more go away with the next scan.');
  return card('fa-screwdriver-wrench', 'Fix it', 'each problem, as the job that fixes it',
    steps.length ? el('div', { class: 'card__actions' }, steps) : null,
    ...notes.map(quiet));
}

/* ----- privacy ---------------------------------------------------------- */
/* Where a photo can give away where it was taken. The two settings, the
 * originals that still carry coordinates, and the one job in this console
 * that rewrites an original: stripping them. */
function paintPrivacy(grid, st, ro) {
  const paths = st.paths || {};
  const run = () => opsLoad('gps', '/api/ops/gps?' + qs({ album: opsValue('gps-album') }));
  grid.append(wide(card('fa-location-dot', 'Location data', 'GPS in the originals',
    el('dl', { class: 'facts' },
      fact('Hide GPS', paths.hide_gps ? 'on — coordinates are never served' : 'off — the API hands them out',
           paths.hide_gps ? 'ok' : 'warn'),
      fact('Strip GPS', paths.strip_gps ? 'on — each scan removes them from the originals' : 'off')),
    el('div', { class: 'ops__form' },
      opsInput('gps-album', { placeholder: 'whole gallery — or one album path', onkeydown: onEnter(run) })),
    el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-magnifying-glass',
                     disabled: !!opsState.loading.gps,
                     text: opsState.loading.gps ? 'Reading EXIF…' : 'Find photos with coordinates', onclick: run })),
    quiet('Opens every original to read its EXIF, so on a large share it takes a while.'))));
  const pending = opsPending('gps', 'fa-location-dot', 'Result');
  if (pending) { grid.append(pending); return; }
  const r = opsState.reports.gps;
  if (!r) return;
  const hits = r.with_gps.length;
  const body = [el('dl', { class: 'facts' },
    fact('Scope', r.album || 'whole gallery'),
    fact('Checked', r.checked + ' original(s)'),
    fact('With coordinates', hits + ' photo(s)', hits ? 'warn' : 'ok'),
    r.unreadable.length ? fact('Unreadable', String(r.unreadable.length), 'warn') : null)];
  if (hits) {
    body.push(...foldedRows('gps:hits', r.with_gps, 10, (rel) => homeRow(rel, '', '', () => showPhoto(rel))));
    body.push(el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn', icon: 'fa-location-dot', disabled: ro,
        text: 'Strip them from ' + hits + ' original(s)',
        onclick: () => startJob('gps_strip', { album: r.album },
          'Rewrite ' + hits + ' original photo(s) in place to remove their GPS block?\n\n' +
          'This changes the originals themselves. A scan of that scope is queued afterwards.') })));
    body.push(quiet('This rewrites the photographs — the one job in the console that touches an original.'));
  }
  if (r.unreadable.length) {
    body.push(sub('unreadable', r.unreadable.length));
    body.push(...foldedRows('gps:unreadable', r.unreadable, 8, (line) => lineRow(line)));
  }
  grid.append(wide(card('fa-location-dot', 'Result', null, ...body)));
}

/* ----- export ----------------------------------------------------------- */
function paintOpsExport(grid) {
  opsAuto('export', '/api/ops/export/contents');
  const pending = opsPending('export', 'fa-box-archive', 'Backup');
  if (pending) { grid.append(pending); return; }
  const r = opsState.reports.export;
  if (!r) return;
  grid.append(wide(card('fa-box-archive', 'Backup', 'every hand-written file, as one .tar.gz',
    el('dl', { class: 'facts' },
      fact('Contents', r.files.length + ' file(s) · ' + sizeOr0(r.bytes)),
      fact('Holds', 'gallery.cfg and .gallery/, every .album/ — config, descriptions, icons, fonts, backdrops'),
      fact('Leaves out', 'the photos: they already are the backup')),
    el('div', { class: 'card__actions' },
      el('a', { class: 'btn btn--primary', href: '/api/ops/export', download: '', icon: 'fa-download',
                text: 'Download the archive' }),
      el('button', { type: 'button', class: 'btn', icon: 'fa-rotate-right', text: 'List again',
                     onclick: () => opsLoad('export', '/api/ops/export/contents') })),
    quiet('Restore with: tar -xzf <archive> -C <photos dir>'),
    sub('files', r.files.length),
    ...foldedRows('export:files', r.files, 15, (name) => lineRow(name)))));
}

/* ----- the password ----------------------------------------------------- */
function paintOpsAccess(grid, st, ro) {
  const auth = st.auth || {};
  const isSet = auth.mode === 'password';
  grid.append(wide(card('fa-key', isSet ? 'Change the password' : 'Set a password',
    isSet ? 'the console asks for it wherever it listens' : 'none is set — anyone who reaches this port can use the console',
    el('dl', { class: 'facts' },
      fact('Door', isSet ? 'password' : 'open', isSet ? 'ok' : 'warn'),
      fact('Listens on', auth.bind || '—'),
      fact('Without one', auth.may_run_open
        ? 'allowed here — loopback, or CONSOLE_ALLOW_OPEN=1'
        : 'not allowed — this console would refuse to start')),
    ro ? quiet('The console is mounted read-only.') : null,
    el('div', { class: 'ops__form' },
      isSet ? opsInput('pw-current', { type: 'password', placeholder: 'current password', autocomplete: 'current-password', disabled: ro }) : null,
      opsInput('pw-new', { type: 'password', placeholder: 'new password — 8 characters or more', autocomplete: 'new-password', disabled: ro }),
      opsInput('pw-again', { type: 'password', placeholder: 'again', autocomplete: 'new-password', disabled: ro,
                             onkeydown: onEnter(setPassword) })),
    el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-key', disabled: ro,
                     text: isSet ? 'Change the password' : 'Set the password', onclick: setPassword })),
    quiet('Stored as an scrypt hash in data/console/credentials. Setting it ends every session, ' +
          'this one included — you sign in again with the new one.'))));

  if (!isSet) return;
  grid.append(wide(card('fa-lock-open', 'Remove the password', 'passwd --clear',
    auth.may_run_open
      ? [el('div', { class: 'ops__form' },
           opsInput('pw-clear', { type: 'password', placeholder: 'current password', autocomplete: 'current-password', disabled: ro })),
         el('div', { class: 'card__actions' },
           el('button', { type: 'button', class: 'btn', icon: 'fa-lock-open', disabled: ro,
                          text: 'Remove the password', onclick: clearPassword })),
         quiet('The console then runs open to anything that can reach ' + auth.bind + '.')]
      : quiet('This console listens on ' + auth.bind + ', where it may not run without a password — ' +
              'removing it would leave the door open until the next start, and then keep the console ' +
              'from starting at all. Change it instead.'))));
}

async function setPassword() {
  const inputs = opsState.inputs;
  const next = inputs['pw-new'] || '';
  if (next !== (inputs['pw-again'] || '')) { toast('The two entries do not match', 'err'); return; }
  try {
    await api('/api/ops/password', { method: 'POST',
      body: JSON.stringify({ current: inputs['pw-current'] || '', password: next }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  } finally {
    delete inputs['pw-current']; delete inputs['pw-new']; delete inputs['pw-again'];
  }
  toLogin('password');
}

async function clearPassword() {
  if (!confirm('Remove the console password? Anything that can reach this port can then use the console.')) return;
  try {
    await api('/api/ops/password', { method: 'DELETE',
      body: JSON.stringify({ current: opsState.inputs['pw-clear'] || '' }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  } finally {
    delete opsState.inputs['pw-clear'];
  }
  window.location.replace('/');
}

const OPS_PAINT = {
  overview: paintIndexer,
  storage: paintStorage,
  health: paintOpsDoctor,
  privacy: paintPrivacy,
  export: paintOpsExport,
  access: paintOpsAccess,
  activity: (grid) => paintActivity(grid),
};

/* ----- jobs ------------------------------------------------------------- */
/* The writes that are not a scan -- rebuilding and pruning derivatives,
 * recomputing flags, stripping coordinates -- queue on the control channel
 * like a scan does, and the indexer runs them. The console follows the one
 * it queued last: its progress while it runs, its summary when it is done. */
const JOB_TITLES = {
  rebuild: 'Rebuilding derivatives',
  prune: 'Deleting orphaned files',
  featured: 'Recomputing featured flags',
  gps_strip: 'Stripping coordinates',
};
// which reports a finished job has made stale, to be asked again
const JOB_STALE = {
  rebuild: ['derivatives', 'doctor'],
  prune: ['derivatives', 'doctor'],
  featured: ['featured', 'doctor'],
  gps_strip: ['gps'],
};

function jobSummary(kind, summary) {
  const r = summary.result || {};
  const took = summary.seconds != null ? ' in ' + summary.seconds + 's' : '';
  if (summary.error) return summary.error;
  if (kind === 'rebuild') return r.built + ' built, ' + r.failed + ' failed of ' + r.to_build + took;
  if (kind === 'prune') return r.pruned + ' of ' + r.orphans + ' deleted · ' + sizeOr0(r.freed_bytes) + ' freed' + took;
  if (kind === 'featured') {
    return r.db_flagged + ' photo(s) flagged' + (r.unresolved ? ' · ' + r.unresolved + ' entr(ies) match nothing' : '') + took;
  }
  if (kind === 'gps_strip') {
    return (r.stripped || []).length + ' of ' + (r.with_gps || []).length + ' rewritten' +
           (r.scan_requested ? ' · a scan was queued' : '') + took;
  }
  return 'done' + took;
}

function jobCard() {
  const job = opsState.job;
  if (!job) return null;
  const s = job.state || {};
  const done = s.result;
  const running = s.running;
  const tone = done ? (done.error ? 'bad' : 'ok') : running ? 'ok' : 'warn';
  const word = done ? (done.error ? 'failed' : 'done') : running ? 'running' : 'queued';
  const live = opsState.status && opsState.status.live;
  const note = done ? jobSummary(job.kind, done)
    : running ? (running.label || '')
      : live ? 'waiting for the indexer to pick it up' : 'no indexer is listening — it runs when one starts';
  const result = (done && done.result) || {};
  const problems = [...(result.broken || []), ...(result.errors || [])];
  return card('fa-screwdriver-wrench', JOB_TITLES[job.kind] || job.kind,
    ((opsState.status && opsState.status.jobs) || {})[job.kind] || null,
    el('div', { class: 'lamp' },
      el('span', { class: 'lamp__dot is-' + tone }),
      el('span', { class: 'lamp__word is-' + tone, text: word }),
      el('span', { class: 'lamp__note', text: note })),
    running && running.total
      ? el('div', { class: 'job__meter' }, meter(running.done || 0, running.total, 'acc'),
          el('span', { class: 'brow__v', text: (running.done || 0) + ' / ' + running.total }))
      : null,
    problems.length ? foldedRows('job:problems', problems, 6, (line) => lineRow(line)) : null,
    done ? el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn btn--ghost', icon: 'fa-xmark', text: 'Dismiss',
                     onclick: () => { opsState.job = null; paintOps(); } })) : null);
}

/* Progress repaints only the job's own card: the rest of the screen may be a
 * form someone is typing into. */
function paintJob() {
  const slot = $('#ops-job');
  const next = jobCard();
  if (slot && next) slot.replaceChildren(next);
  else if (onOps()) paintOps();
}

async function startJob(kind, params = {}, question = null) {
  if (question && !confirm(question)) return;
  let res;
  try {
    res = await api('/api/ops/jobs', { method: 'POST', body: JSON.stringify({ kind, ...params }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  opsState.job = { id: res.job.id, kind, state: null };
  toast(res.note || 'Queued — the indexer picks it up within a couple of seconds', res.note ? 'warn' : 'ok');
  if (onOps()) {
    paintOps();
    $('#pane').scrollTop = 0;
  }
  pollJob();
}

function pollJob() {
  clearInterval(opsState.jobPoll);
  const job = opsState.job;
  opsState.jobPoll = setInterval(async () => {
    if (opsState.job !== job) { clearInterval(opsState.jobPoll); return; }
    let body;
    try {
      body = await api('/api/ops/jobs/' + encodeURIComponent(job.id));
    } catch (_) {
      return;   /* a hiccup is not a reason to stop following a running job */
    }
    job.state = body;
    if (!body.result) {
      if (onOps()) paintJob();
      return;
    }
    clearInterval(opsState.jobPoll);
    toast(body.result.error ? 'Job failed: ' + body.result.error : jobSummary(job.kind, body.result),
          body.result.error ? 'err' : 'ok');
    opsState.disk = null;
    await loadOpsStatus();
    for (const tab of JOB_STALE[job.kind] || []) {
      if (opsState.reports[tab] && opsState.urls[tab]) opsLoad(tab, opsState.urls[tab], { quiet: true });
    }
    if (onOps()) paintOps();
  }, 1500);
}

/* A scan is asynchronous by nature: on a large share over SMB a full pass is
 * minutes. So the request returns an id and this polls for its summary rather
 * than holding an HTTP request open across the whole thing. */
/* Home and Operations both draw the indexer, and an action can be started
 * from either. Repaint whichever is actually up -- painting Operations over
 * the home screen because that is where the code used to live is how a tool
 * teaches people not to trust its buttons. */
function repaintOps() {
  syncLamp();
  if (onOps()) paintOps();
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
      opsState.disk = null;
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
  delete opsState.inputs.reason;
  await loadOpsStatus();
  repaintOps();
  refreshAlbumViews();
}

async function doResume(scan) {
  try {
    const res = await api('/api/ops/resume', { method: 'POST', body: JSON.stringify({ scan: !!scan }) });
    toast(res.request ? 'Indexing resumed · scan requested' : 'Indexing resumed');
    if (res.request) {
      opsState.busy = true;
      pollScan(res.request.id);
    }
  } catch (err) { toast(err.message, 'err'); return; }
  delete opsState.inputs['resume-scan'];
  await loadOpsStatus();
  repaintOps();
  refreshAlbumViews();
}

/* ----- home ------------------------------------------------------------- */
/* The screen the console opens on. It answers what a person arriving has to
 * ask anyway -- is the machine working, is anything broken, what happened
 * here last, what is still unwritten -- and it answers them out of routes
 * that already existed for other screens. Nothing on it is computed here: a
 * dashboard with figures of its own is a second opinion to keep in step.
 */
const home = { issues: null, audit: [], gallery: null };

/* ----- updates -------------------------------------------------------------
 * Whether lucya.sh knows a newer aperture. The server asks and keeps the
 * answer (aperture/update_check.py); this only shows it -- a dot on the Changelog
 * place, a card on Home when there is something to get, the whole state on
 * the Changelog place. One request per page load serves all three. */
const updates = { info: null, pending: null };

function loadUpdates(fresh = false) {
  if (!updates.pending || fresh) {
    updates.pending = api('/api/updates' + (fresh ? '?fresh=1' : ''))
      .then((info) => { updates.info = info; syncUpdateMark(); return info; })
      .catch(() => updates.info);
  }
  return updates.pending;
}

function syncUpdateMark() {
  const button = $('.rail__link[data-place="ops"]');
  if (!button) return;
  const info = updates.info;
  const on = !!info && info.state === 'available';
  button.classList.toggle('has-update', on);
  if (on) button.title = 'Update available: ' + info.latest.version;
  else button.removeAttribute('title');
}

/* The check as a card. Home shows it only when there is a release to get, and
 * without the button; the Changelog place shows every state. */
function updateCard(info, withAgain) {
  const quiet = (text) => el('p', { class: 'card__quiet', text });
  const again = withAgain ? el('div', { class: 'card__actions' }, el('button', {
    type: 'button', class: 'btn btn--ghost', icon: 'fa-rotate-right', text: 'Check again',
    onclick: async (event) => {
      event.currentTarget.disabled = true;
      await loadUpdates(true);
      if (state.sel && state.sel.kind === 'changelog') renderChangelog();
    },
  })) : null;
  if (!info) return card('fa-circle-info', 'Updates', null, quiet('The check did not run.'));
  const checked = info.checked_at ? 'checked ' + ago(info.checked_at) : null;
  const latest = info.latest || {};
  const body = (...rows) => el('div', { class: 'update' }, ...rows);
  if (info.state === 'available') {
    const link = latest.url
      ? el('a', { class: 'btn btn--primary', href: latest.url, target: '_blank', rel: 'noopener',
                  text: 'Release notes', iconEnd: 'fa-arrow-up-right-from-square' })
      : null;
    return card('fa-circle-up', 'Update available', latest.date ? 'released ' + latest.date : checked,
      body(
        el('p', { class: 'update__versions' },
          el('span', { text: info.current }),
          el('span', { class: 'update__arrow', text: '→' }),
          el('strong', { text: latest.version })),
        latest.notes ? quiet(latest.notes) : null,
        link || again ? el('div', { class: 'card__actions' }, link, again && again.firstChild) : null));
  }
  if (info.state === 'current') {
    return card('fa-circle-check', 'Up to date', checked,
      body(quiet(info.current + ' is the newest release there is.'), again));
  }
  if (info.state === 'unreachable') {
    const host = (info.source || '').replace(/^https?:\/\//, '').split('/')[0] || 'the update server';
    return card('fa-circle-exclamation', 'Updates', checked,
      body(quiet('Could not ask ' + host + ' (' + info.error + '). It is asked again within the hour.'), again));
  }
  return card('fa-circle-info', 'Updates', null, quiet('The update check is off (UPDATE_CHECK=0).'));
}

/* ----- changelog -----------------------------------------------------------
 * The release notes this build ships with, where its code lives and who makes
 * it. The notes arrive as HTML the server rendered from CHANGELOG.md -- this
 * repo's own file, so it is set as markup; the console's CSP still runs no
 * script but its own. The newest release is open, every older one folds. */
async function renderChangelog() {
  let about = null;
  const pendingUpdates = loadUpdates();
  try {
    about = await api('/api/about');
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: err.message }));
    return;
  }
  const info = await pendingUpdates;
  if (!state.sel || state.sel.kind !== 'changelog') return;   /* navigated away */
  const pane = $('#pane');
  pane.innerHTML = '';
  let grid = null;
  systemFrame(pane, 'About', about.repo, [
    el('span', { class: 'pill', icon: 'fa-code-branch', text: about.product + ' ' + about.version }),
    info && info.state === 'available'
      ? el('span', { class: 'pill pill--warn', icon: 'fa-circle-up', text: info.latest.version + ' available' })
      : null,
  ], (body) => { grid = body; });
  grid.append(el('div', { class: 'home__wide' }, updateCard(info, true)));

  const maker = about.maker || {};
  const outward = { target: '_blank', rel: 'noopener' };
  grid.append(el('div', { class: 'home__wide' }, card('fa-user-pen', 'Made by', 'who builds this software',
    el('div', { class: 'maker' },
      maker.pfp ? el('img', { class: 'maker__pfp', src: maker.pfp, alt: '', width: '64', height: '64' }) : null,
      el('div', { class: 'maker__text' },
        el('a', { class: 'maker__name', href: maker.url, ...outward, text: maker.name,
                  iconEnd: 'fa-arrow-up-right-from-square' }),
        el('a', { class: 'maker__repo', href: about.repo, ...outward, icon: 'fa-code-branch',
                  text: about.repo.replace(/^https?:\/\//, '') }))))));

  const releases = about.releases || [];
  if (!releases.length) {
    grid.append(el('div', { class: 'home__wide' }, card('fa-scroll', 'Releases', null,
      el('p', { class: 'card__quiet', text: 'CHANGELOG.md is not part of this build.' }))));
    return;
  }
  releases.forEach((release, i) => {
    const notes = el('div', { class: 'notes' });
    notes.innerHTML = release.html;
    notes.querySelectorAll('a[href^="http"]').forEach((a) => {
      a.target = '_blank';
      a.rel = 'noopener';
    });
    const current = release.version === about.version;
    const body = i === 0 ? notes
      : el('details', { class: 'release' },
          el('summary', { class: 'release__toggle', icon: 'fa-chevron-down', text: 'Read the notes' }),
          notes);
    grid.append(el('div', { class: 'home__wide' },
      card(i === 0 ? 'fa-scroll' : null, release.version,
           release.date + (current ? ' · this build' : ''), body)));
  });
}

async function renderHome() {
  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__empty', text: 'Reading the archive…' }));
  const [, issues, audit, gallery] = await Promise.all([
    loadOpsStatus(),
    api('/api/validate').catch(() => null),
    api('/api/audit?limit=8').catch(() => null),
    api('/api/gallery').catch(() => null),
    loadUpdates(),
  ]);
  if (!state.sel || state.sel.kind !== 'home') return;   /* navigated away */
  if (issues) { home.issues = issues; countIssues(issues); refreshAlbumViews(); }
  home.audit = (audit && audit.entries) || [];
  home.gallery = gallery;
  paintHome();
}

function card(icon, title, note, ...body) {
  return el('section', { class: 'card' },
    el('header', { class: 'card__head' },
      el('h2', { class: 'card__title', icon, text: title }),
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
  const done = ['indexed', 'thumbnails', 'previews', 'removed', 'failed']
    .filter((k) => res[k]).map((k) => res[k] + ' ' + k).join(' · ') || 'nothing to do';
  // the empty-walk guard: no photos found, so the index was left alone
  return res.held ? done + ' · held: no photos found, index kept (share mounted?)' : done;
}

/* A report list that shows its first `limit` rows and folds the rest behind
 * one button, so a long list is complete without pushing everything under it
 * off the screen. Open lists are remembered by name: the home screen repaints
 * on every poll while a scan runs, and would otherwise fold them up again. */
const unfolded = new Set();
function foldedRows(name, items, limit, row) {
  const list = el('div', { class: 'hrows' });
  if (items.length <= limit) {
    list.append(...items.map(row));
    return [list];
  }
  const toggle = el('button', { type: 'button', class: 'btn' });
  const paint = () => {
    const open = unfolded.has(name);
    list.replaceChildren(...(open ? items : items.slice(0, limit)).map(row));
    toggle.setAttribute('aria-expanded', String(open));
    toggle.replaceChildren(ico(open ? 'fa-chevron-up' : 'fa-chevron-down'),
      open ? 'show fewer' : (items.length - limit) + ' more');
  };
  toggle.addEventListener('click', () => {
    if (unfolded.has(name)) unfolded.delete(name);
    else unfolded.add(name);
    paint();
  });
  paint();
  return [list, el('div', { class: 'card__actions' }, toggle)];
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
          ro ? el('span', { class: 'pill pill--warn', icon: 'fa-lock', text: 'read-only' }) : null)))));

  const grid = el('div', { class: 'home' });
  pane.append(grid);

  /* ---- is there a newer release ---- */
  if (updates.info && updates.info.state === 'available') {
    grid.append(el('div', { class: 'home__wide' }, updateCard(updates.info, false)));
  }

  /* ---- is the machine working ---- */
  const tone = paused ? 'warn' : live ? 'ok' : 'bad';
  const word = paused ? 'paused' : scanning ? 'scanning' : live ? 'running' : 'not running';
  grid.append(card('fa-microchip', 'Indexer', st.control_dir ? 'via the control channel' : null,
    el('div', { class: 'lamp' },
      el('span', { class: 'lamp__dot is-' + tone }),
      el('span', { class: 'lamp__word is-' + tone, text: word }),
      el('span', { class: 'lamp__note', text: paused && st.pause
        ? 'since ' + agoIso(new Date(st.pause.since * 1000).toISOString()) +
          (st.pause.reason ? ' — ' + st.pause.reason : '')
        : live ? '' : 'no heartbeat — start the gallery to index' })),
    el('dl', { class: 'facts' },
      fact('Last scan', scan
        ? ago(scan.finished_at) + ' · ' + (scan.trigger || '?') +
          (scan.seconds != null ? ' · ' + scan.seconds + 's' : '')
        : 'never'),
      fact('It did', scan ? scanSummary(res) : '—'),
      fact('Every', paths.scan_interval ? paths.scan_interval + 's' : 'manual only'),
      fact('Watcher', paths.watcher ? 'on' : 'off'),
      fact('Role', st.role || '—')),
    el('div', { class: 'card__actions' },
      el('button', {
        type: 'button', class: 'btn btn--primary', icon: 'fa-arrows-rotate', text: 'Scan now',
        disabled: ro || opsState.busy, onclick: () => startScan('', false),
      }),
      el('button', {
        type: 'button', class: 'btn', disabled: ro || opsState.busy,
        icon: paused ? 'fa-play' : 'fa-pause',
        text: paused ? 'Resume' : 'Pause',
        onclick: () => (paused ? doResume() : doPause('')),
      }),
      el('button', {
        type: 'button', class: 'btn btn--ghost', text: 'System', iconEnd: 'fa-arrow-right',
        onclick: () => go({ kind: 'ops' }),
      }))));

  /* ---- what is in it ---- */
  grid.append(card('fa-box-archive', 'Archive', 'what the index holds',
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
  grid.append(el('div', { class: 'home__wide' }, card('fa-triangle-exclamation', 'Needs attention',
    issues ? issues.errors + ' error(s) · ' + issues.warnings + ' warning(s)' : null,
    !issues
      ? el('p', { class: 'card__quiet', text: 'The check did not run.' })
      : !list.length
        ? el('p', { class: 'card__quiet',
                    text: 'Every config file checks out — nothing the gallery would ignore.' })
        : foldedRows('issues', list, 6, (issue) => {
            const target = issue.scope === 'album'
              ? { kind: 'album', album: issue.album } : { kind: issue.scope };
            const row = homeRow(
              issue.scope === 'gallery' ? 'gallery.cfg'
                : issue.scope === 'links' ? 'links.cfg' : issue.album,
              issue.key, issue.detail, () => go(target));
            /* The fix opens the file with the edit already staged: it is
             * reviewed and saved there, like any other change. */
            if (issue.fix && !READ_ONLY && issue.scope !== 'links') {
              row.append(el('button', {
                type: 'button', class: 'btn btn--sm hrow__fix', icon: 'fa-screwdriver-wrench', text: issue.fix.label,
                onclick: async (ev) => {
                  ev.stopPropagation();
                  await select(target);
                  applyFix(issue.fix);
                },
              }));
            }
            return row;
          }),
    el('div', { class: 'card__actions' },
      el('button', { type: 'button', class: 'btn', icon: 'fa-rotate-right', text: 'Check again', onclick: checkAll })))));

  /* ---- what is still unwritten ---- */
  const albums = [];
  (function walk(node) {
    for (const child of node.children || []) { albums.push(child); walk(child); }
  })(state.tree || { children: [] });
  const noCfg = albums.filter((a) => a.own_photos && !a.has_cfg);
  const noText = albums.filter((a) => a.own_photos && !a.has_desc);
  grid.append(card('fa-pen-to-square', 'Unwritten', albums.length + ' album(s) in the tree',
    !noCfg.length && !noText.length
      ? el('p', { class: 'card__quiet', text: 'Every album with photos has a cfg and a text.' })
      : foldedRows('unwritten', [
          ...noCfg.map((a) => [a, 'no cfg', a.own_photos + ' photo(s), nothing configured']),
          ...noText.map((a) => [a, 'no text', a.own_photos + ' photo(s), no album_<lang>.md']),
        ], 8, ([a, key, detail]) => homeRow(a.path, key, detail,
          () => select({ kind: 'album', album: a.path })))));

  /* ---- what happened here ---- */
  grid.append(card('fa-clock-rotate-left', 'Recent changes', 'this console, not the gallery',
    !home.audit.length
      ? el('p', { class: 'card__quiet', text: 'Nothing has been saved here yet.' })
      : el('div', { class: 'hrows' }, home.audit.map((entry) => {
          const target = auditTarget(entry.target);
          return homeRow(agoIso(entry.ts), entry.action || '—', entry.target || '',
            target ? () => select(target) : null);
        }))));
}


/* An album's -- and the site's -- tabs. Not one per FILE, as the console
 * once had (Settings / Description / Files / Raw), and not one long page
 * with everything on it, which was too much at once: one per thing you
 * came to change. Each settings tab holds one or two of the groups. */
const ALBUM_TABS = [
  ['details', 'Details', 'fa-circle-info', ['The album', 'Text & stats']],
  ['photos', 'Photos', 'fa-star', ['Photos it leans on']],
  ['look', 'Look', 'fa-palette', ['Look', 'Backdrop']],
  ['text', 'Text', 'fa-align-left', null],
  ['files', 'Files', 'fa-folder-open', null],
  ['raw', 'Raw file', 'fa-file-code', null],
];
const GALLERY_TABS = [
  ['identity', 'Identity', 'fa-id-card', ['Identity', 'Credit']],
  ['look', 'Look', 'fa-palette', ['Look', 'Backdrop']],
  ['front', 'Front page', 'fa-house', ['Welcome hero', 'Album list']],
  ['footer', 'Footer', 'fa-shoe-prints', ['Operator & footer']],
  ['files', 'Files', 'fa-folder-open', null],
  ['raw', 'Raw file', 'fa-file-code', null],
];

function editorTabs() {
  return state.sel.kind === 'gallery' ? GALLERY_TABS : ALBUM_TABS;
}

function editorGroups() {
  return state.sel.kind === 'gallery' ? GALLERY_GROUPS : ALBUM_GROUPS;
}

/* The tab a key lives on -- where a search hit, a save-bar chip or a fix
 * has to take you. */
function tabOfKey(key) {
  const group = editorGroups().find(([, , keys]) => keys.includes(key));
  const tab = group && editorTabs().find(([, , , titles]) => titles && titles.includes(group[0]));
  return tab ? tab[0] : editorTabs()[0][0];
}

function renderPane() {
  if (state.sel.kind === 'library') { renderLibrary(); return; }
  const pane = $('#pane');
  const isGallery = state.sel.kind === 'gallery';
  const active = document.activeElement;
  const fk = active && active.dataset ? active.dataset.fk : null;
  const selStart = fk && active.selectionStart !== undefined ? active.selectionStart : null;
  const selEnd = fk && active.selectionEnd !== undefined ? active.selectionEnd : null;
  paneScroll = pane.scrollTop;
  pane.innerHTML = '';

  const tabs = editorTabs();
  if (!tabs.some(([id]) => id === state.tab)) state.tab = tabs[0][0];
  const [, , , titles] = tabs.find(([id]) => id === state.tab);

  /* Which tabs have something to say: an unsaved edit (amber), an issue
   * (red). A tab that hides a change is how an edit gets forgotten. */
  const issueKeys = new Set((state.data.issues || []).map((i) => i.key));
  const mark = (groupTitles) => {
    if (!groupTitles) return null;
    const keys = editorGroups().filter(([t]) => groupTitles.includes(t)).flatMap(([, , k]) => k);
    if (keys.some((k) => k in state.edits)) return 'edit';
    if (keys.some((k) => issueKeys.has(k))) return 'issue';
    return null;
  };

  /* Header and tabs travel together as one sticky block. */
  pane.append(el('div', { class: 'pane__top' },
    renderHead(isGallery),
    el('div', { class: 'tabs', role: 'tablist' }, tabs.map(([id, label, icon, groupTitles]) => {
      const m = mark(groupTitles);
      return el('button', {
        class: 'tab' + (state.tab === id ? ' is-active' : '') + (m ? ' has-' + m : ''),
        type: 'button', role: 'tab', 'aria-selected': String(state.tab === id), icon, text: label,
        title: m === 'edit' ? 'Unsaved changes on this tab' : m === 'issue' ? 'The check has something to say here' : null,
        onclick: () => { state.tab = id; state.query = ''; paneScroll = 0; renderPane(); $('#pane').scrollTop = 0; },
      });
    }))));

  if (state.tab === 'raw') {
    pane.append(renderRaw());
  } else {
    const issues = renderIssues();
    if (issues) pane.append(issues);
    if (titles || state.query.trim()) {
      /* A search looks through every tab, so a key is found wherever it
       * lives; without one, the tab shows its own groups. */
      const groups = state.query.trim() ? editorGroups()
        : editorGroups().filter(([title]) => titles.includes(title));
      pane.append(renderSettings(groups));
      pane.append(renderSaveBar());
    } else if (state.tab === 'text') {
      pane.append(renderDescriptions());
    } else if (state.tab === 'files') {
      pane.append(renderAssets());
    }
  }

  pane.scrollTop = paneScroll;
  paneScrollLanded = pane.scrollTop;
  restoreFocus(fk, selStart, selEnd);
  syncDirtyMark();
}

/* What the checks say about this file, each with its fix when there is one.
 * A fix is staged like any edit -- it shows in the diff before anything is
 * written. */
function renderIssues() {
  const list = (state.data.issues || []);
  if (!list.length) return null;
  return el('div', { class: 'issues' }, list.map((issue) => {
    const staged = issue.fix && issue.fix.key in state.edits &&
      JSON.stringify(state.edits[issue.fix.key]) === JSON.stringify(issue.fix.value);
    return el('div', { class: 'issue issue--' + (issue.level === 'error' ? 'error' : 'warn') },
      el('span', { class: 'issue__key', text: issue.key }),
      el('span', { class: 'issue__detail', text: issue.detail }),
      issue.fix && !READ_ONLY
        ? el('button', {
            type: 'button', class: 'btn btn--sm' + (staged ? ' is-on' : ''), disabled: staged,
            icon: staged ? 'fa-check' : 'fa-screwdriver-wrench', text: staged ? 'Staged' : issue.fix.label,
            title: staged ? 'Staged — review it with Save' : 'Stage this fix; it is saved with the rest',
            onclick: () => applyFix(issue.fix),
          })
        : null);
  }));
}

function applyFix(fix) {
  state.edits[fix.key] = fix.value;
  state.tab = tabOfKey(fix.key);
  renderPane();
  toast('Staged: ' + fix.label + ' — review it with Save');
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
    icon: data.exists ? 'fa-circle-check' : 'fa-file',
    text: data.exists ? 'cfg present' : 'no cfg yet',
  })];
  if (!isGallery) {
    meta.push(el('span', { class: 'pill', icon: 'fa-image', text: data.own_count + ' here' }));
    if (data.photo_count !== data.own_count) {
      meta.push(el('span', { class: 'pill', icon: 'fa-folder-tree', text: data.photo_count + ' subtree' }));
    }
    /* Whether anyone has written about this album. It is the one thing the
     * header could not say, and the thing most often still undone. */
    const langs = Object.keys(data.descriptions || {})
      .filter((lang) => (data.descriptions[lang] || '').trim());
    meta.push(langs.length
      ? el('span', { class: 'pill', icon: 'fa-align-left', text: 'text ' + langs.join(' ') })
      : el('span', { class: 'pill', icon: 'fa-align-left', text: 'no text yet' }));
  }
  const errors = (data.issues || []).filter((i) => i.level === 'error').length;
  const warns = (data.issues || []).filter((i) => i.level === 'warn').length;
  if (errors) meta.push(el('span', { class: 'pill pill--err', icon: 'fa-circle-exclamation', text: errors + ' errors' }));
  if (warns) meta.push(el('span', { class: 'pill pill--warn', icon: 'fa-triangle-exclamation', text: warns + ' warnings' }));
  if (!isGallery) {
    meta.push(el('a', { class: 'btn', href: pathFor({ kind: 'library', album: state.sel.album }),
                        'data-go': true, icon: 'fa-images', text: 'Photos' }));
  }
  meta.push(el('button', {
    type: 'button', class: 'btn', icon: 'fa-clock-rotate-left', text: 'History',
    title: 'Earlier versions of this file, and putting one back',
    onclick: () => openHistory(isGallery ? { file: 'gallery' } : { file: 'album', album: state.sel.album }),
  }));

  if (!isGallery && !READ_ONLY) meta.push(linkButton(state.sel.album, 'Link…'));

  const path = isGallery
    ? state.meta.photos_dir + '/gallery.cfg'
    : state.meta.photos_dir + '/' + state.sel.album + '/.album/album.cfg';

  /* Title and pills share one line so the sticky block stays short; the file
   * path is the part that folds away under the pane's edge on scroll. */
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
      el('h1', { class: 'head__title', text: isGallery ? 'Site' : (named || data.name) }),
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
        el('span', { class: 'group__twisty', icon: 'fa-chevron-right' }),
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

  const sw = (on, icon, label, title, onclick) => el('button', {
    class: 'chipbtn' + (on ? ' is-on' : ''), type: 'button', icon, text: label,
    title, 'aria-pressed': on ? 'true' : 'false', onclick,
  });

  return el('div', { class: 'stoolbar' },
    el('div', { class: 'stoolbar__find' },
      searchbox({
        type: 'search', class: 'fieldsearch', 'data-fk': '__q', value: state.query,
        placeholder: 'Find a setting — name or description…', autocomplete: 'off',
        oninput: (ev) => { state.query = ev.target.value; renderPane(); },
      })),
    el('div', { class: 'stoolbar__switches' },
      sw(state.setOnly, 'fa-filter', 'Only set', 'show just the keys this file writes', () => {
        state.setOnly = !state.setOnly; savePrefs(); renderPane();
      }),
      sw(state.showHelp, 'fa-circle-info', 'Help', 'show the description under each key', () => {
        state.showHelp = !state.showHelp; syncHelpClass(); savePrefs(); renderPane();
      }),
      sw(false, allFolded ? 'fa-angles-down' : 'fa-angles-up', allFolded ? 'Unfold all' : 'Fold all', 'fold every group', () => {
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
    el('div', { class: 'field__text' },
      el('div', { class: 'field__top' },
        el('span', { class: 'field__name', text: humanKey(key) }),
        el('code', { class: 'field__label', text: key }),
        edited ? el('span', { class: 'field__flag', text: 'edited',
                              title: 'changed here, not yet written to the file' }) : null,
        unset || READ_ONLY ? null : el('button', {
          class: 'field__unset', type: 'button', icon: 'fa-xmark',
          title: 'unset — remove this line from the file',
          'aria-label': 'Unset ' + key,
          onclick: () => setValue(key, null),
        })),
      help ? el('div', { class: 'field__help', text: help }) : null),
    el('div', { class: 'field__control' }, buildControl(key, spec)));
}

/* A key as words: the file spells it `wallpaper_mobile`, a person reads
 * "Wallpaper mobile". The key itself stays beside it, as the file has it. */
const humanKey = (key) => {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

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
        class: 'btn btn--sm btn--ghost btn--icon', type: 'button', icon: 'fa-xmark', title: 'remove',
        onclick: () => setValue(key, items.filter((_, i) => i !== index)),
      })));
  });
  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'None.' }));
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' }, el('button', {
      class: 'btn btn--sm', type: 'button', icon: 'fa-plus', text: 'Add',
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
        class: 'btn btn--sm btn--ghost btn--icon', type: 'button', icon: 'fa-xmark', title: 'remove',
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
      class: 'btn btn--sm', type: 'button', icon: 'fa-plus', text: 'Add attribute',
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
      class: 'btn btn--sm', type: 'button', icon: 'fa-image', text: current ? 'Change…' : 'Pick a cover…',
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
        class: 'btn btn--sm', type: 'button', icon: 'fa-images', text: 'Pick photos…',
        onclick: () => openPicker({
          title: key === 'featured' ? 'Featured photos' : 'Curated photo order',
          root: album, picked: items.map(strip),
          onApply: (list) => setValue(key, list),
        }),
      }),
      items.length ? el('button', {
        class: 'btn btn--sm btn--ghost', type: 'button', icon: 'fa-eraser', text: 'Clear',
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
  row.append(el('span', { class: 'pickitem__grip', title: 'drag to reorder', icon: 'fa-grip-vertical' }));
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
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', icon: 'fa-arrow-up',
      title: 'move up', disabled: index === 0,
      onclick: () => {
        const next = items.slice();
        next.splice(index - 1, 0, next.splice(index, 1)[0]);
        setValue(key, next);
      },
    }));
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', icon: 'fa-arrow-down',
      title: 'move down', disabled: index === items.length - 1,
      onclick: () => {
        const next = items.slice();
        next.splice(index + 1, 0, next.splice(index, 1)[0]);
        setValue(key, next);
      },
    }));
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', icon: 'fa-xmark', title: 'remove',
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
      class: 'btn btn--sm', type: 'button', icon: 'fa-images', text: 'Pick photos…',
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
      class: 'btn btn--sm', type: 'button', icon: 'fa-heading', text: 'Group header',
      onclick: () => {
        const label = prompt('Group label (frames the albums listed below it):', 'trips');
        if (label && label.trim()) setValue(key, items.concat(['#' + label.trim()]));
      },
    }),
    items.length ? el('button', {
      class: 'btn btn--sm btn--ghost', type: 'button', icon: 'fa-eraser', text: 'Clear',
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
      ? 'Read-only mount — saving is off'
      : 'Nothing to save. Comments and untouched keys survive every save.' }));
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
          class: 'chip__x', type: 'button', icon: 'fa-xmark', title: 'undo this change',
          onclick: () => { delete state.edits[key]; renderPane(); },
        })))));
  }

  bar.append(el('div', { class: 'savebar__acts' },
    changed.length ? el('button', {
      class: 'btn', type: 'button', icon: 'fa-rotate-left', text: 'Discard all',
      onclick: () => { state.edits = {}; renderPane(); },
    }) : null,
    el('button', {
      class: 'btn btn--primary', type: 'button', icon: 'fa-floppy-disk', text: 'Review & save',
      title: 'See exactly which lines change, then write them (Ctrl S)',
      disabled: READ_ONLY || !changed.length, onclick: () => reviewDraft(currentDraft()),
    })));
  return bar;
}

/* Bring one key back on screen whatever is hiding it — a filter, "only set",
 * a folded group — and mark it for a moment so the eye lands on it. */
function revealField(key) {
  state.query = '';
  state.setOnly = false;
  state.collapsed.clear();
  state.tab = tabOfKey(key);
  savePrefs();
  renderPane();
  const node = document.querySelector('.field[data-key="' + CSS.escape(key) + '"]');
  if (!node) return;
  node.scrollIntoView({ block: 'center', behavior: 'smooth' });
  node.classList.add('is-flash');
  setTimeout(() => node.classList.remove('is-flash'), 1200);
}

/* ----- drafts ------------------------------------------------------------
 * Unsaved edits belong to their FILE, not to the screen: moving to another
 * album keeps them, and the bar's "unsaved" mark lists every file with
 * something waiting. Nothing reaches disk until it has been reviewed as a
 * line diff -- the dry run the server renders with the same parser and the
 * same apply() the save uses. */
const drafts = new Map();          // file key -> {sel, edits}
const descDrafts = {};             // "album|lang" -> text typed, not saved

const draftKey = (sel) => !sel ? null
  : sel.kind === 'gallery' ? 'gallery' : sel.kind === 'album' ? 'album:' + sel.album : null;
const draftLabel = (sel) => (sel.kind === 'gallery' ? 'gallery.cfg' : sel.album + '/.album/album.cfg');

function stashDraft() {
  const key = draftKey(state.sel);
  if (!key) return;
  if (Object.keys(state.edits || {}).length) drafts.set(key, { sel: { ...state.sel }, edits: state.edits });
  else drafts.delete(key);
}

function takeDraft(sel) {
  const key = draftKey(sel);
  return key && drafts.has(key) ? drafts.get(key).edits : {};
}

function currentDraft() {
  return { key: draftKey(state.sel), sel: { ...state.sel }, edits: state.edits };
}

/* Every file with something unsaved, the one on screen first. */
function pendingDrafts() {
  const out = [];
  const here = draftKey(state.sel);
  if (here && Object.keys(state.edits || {}).length) out.push(currentDraft());
  for (const [key, draft] of drafts) {
    if (key !== here && Object.keys(draft.edits).length) out.push({ key, ...draft });
  }
  return out;
}

/* A line diff: the longest common run of lines, then what was taken out and
 * what was put in around it. cfg files are a few hundred lines at most, so
 * the plain table is fine. */
function lineDiff(a, b) {
  const x = a.split('\n');
  const y = b.split('\n');
  const n = x.length;
  const m = y.length;
  const t = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push([' ', x[i]]); i++; j++; }
    else if (t[i + 1][j] >= t[i][j + 1]) out.push(['-', x[i++]]);
    else out.push(['+', y[j++]]);
  }
  while (i < n) out.push(['-', x[i++]]);
  while (j < m) out.push(['+', y[j++]]);
  return out;
}

/* The diff as hunks: every changed line with two lines of context, the
 * unchanged stretches between them folded to a count. */
function renderDiff(before, after) {
  const lines = lineDiff(before.replace(/\n$/, ''), after.replace(/\n$/, ''));
  const keep = new Set();
  lines.forEach(([op], i) => {
    if (op !== ' ') for (let k = Math.max(0, i - 2); k <= Math.min(lines.length - 1, i + 2); k++) keep.add(k);
  });
  const rows = [];
  let skipped = 0;
  lines.forEach(([op, text], i) => {
    if (!keep.has(i)) { skipped++; return; }
    if (skipped) { rows.push(el('div', { class: 'diff__skip', text: '… ' + skipped + ' unchanged line' + (skipped === 1 ? '' : 's') })); skipped = 0; }
    rows.push(el('div', { class: 'diff__line diff__line--' + ({ ' ': 'ctx', '-': 'del', '+': 'add' })[op] },
      el('span', { class: 'diff__op', text: op === ' ' ? '' : op }),
      el('span', { class: 'diff__text', text: text || ' ' })));
  });
  if (skipped) rows.push(el('div', { class: 'diff__skip', text: '… ' + skipped + ' unchanged line' + (skipped === 1 ? '' : 's') }));
  const added = lines.filter(([op]) => op === '+').length;
  const removed = lines.filter(([op]) => op === '-').length;
  return { node: el('div', { class: 'diff' }, rows), added, removed };
}

function reviewDialog() {
  let dlg = $('#review');
  if (!dlg) {
    dlg = el('dialog', { class: 'dlg review', id: 'review', 'aria-labelledby': 'review-title' });
    dlg.addEventListener('click', (ev) => { if (ev.target === dlg) dlg.close(); });
    document.body.append(dlg);
  }
  return dlg;
}

/* One file's changes, as the lines that will change, with Save underneath.
 * Opened from the page's save bar, Ctrl S, and the tray. */
async function reviewDraft(draft, then) {
  if (!draft || !draft.key || !Object.keys(draft.edits).length) return;
  const isGallery = draft.sel.kind === 'gallery';
  let preview;
  try {
    preview = await api(isGallery ? '/api/gallery/cfg/preview' : '/api/album/cfg/preview', {
      method: 'POST', body: JSON.stringify({ album: draft.sel.album || '', values: draft.edits }),
    });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  const dlg = reviewDialog();
  const diff = renderDiff(preview.before, preview.after);
  const warnings = (preview.issues || []);
  dlg.replaceChildren(
    el('header', { class: 'dlg__head' },
      el('h2', { id: 'review-title', text: 'Review changes' }),
      el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Close', onclick: () => dlg.close() },
        ico('fa-xmark'))),
    el('div', { class: 'dlg__body' },
      el('p', { class: 'review__file' },
        el('code', { text: draftLabel(draft.sel) }),
        el('span', { class: 'review__count', text: '−' + diff.removed + ' +' + diff.added + ' lines · comments and every other line stay as they are' })),
      diff.node,
      warnings.length ? el('div', { class: 'review__after' },
        el('h3', { class: 'insp__sub', text: 'After this save the check still says' }),
        el('ul', { class: 'review__issues' }, warnings.map((w) => el('li', {},
          el('code', { text: w.key }), ' ', w.detail)))) : null),
    el('footer', { class: 'dlg__foot' },
      el('button', { type: 'button', class: 'btn btn--ghost', text: 'Keep editing', onclick: () => dlg.close() }),
      el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-floppy-disk', text: 'Save ' + (isGallery ? 'gallery.cfg' : 'album.cfg'),
                     id: 'review-save', disabled: READ_ONLY,
                     onclick: async (ev) => {
                       ev.currentTarget.disabled = true;
                       const ok = await writeDraft(draft);
                       if (ok) { dlg.close(); if (then) then(); }
                       else ev.currentTarget.disabled = false;
                     } })));
  if (!dlg.open) dlg.showModal();
  $('#review-save').focus();
}

/* Write one draft. The file on screen takes the server's answer as its new
 * state; a file elsewhere simply stops being a draft. */
async function writeDraft(draft) {
  const isGallery = draft.sel.kind === 'gallery';
  let payload;
  try {
    payload = await api(isGallery ? '/api/gallery/cfg' : '/api/album/cfg',
      { method: 'PUT', body: JSON.stringify({ album: draft.sel.album || '', values: draft.edits }) });
  } catch (err) {
    toast('Save failed: ' + err.message, 'err');
    return false;
  }
  drafts.delete(draft.key);
  if (draftKey(state.sel) === draft.key) {
    state.edits = {};
    if (state.data) {
      Object.assign(state.data, { values: payload.values, raw: payload.raw, issues: payload.issues, exists: true });
    }
    renderPane();
  }
  syncDirtyMark();
  refreshIssueDots();
  toast('Saved ' + draftLabel(draft.sel));
  return true;
}

/* The tray: every file with something unsaved. */
function openTray() {
  const dlg = reviewDialog();
  const list = pendingDrafts();
  if (!list.length) { toast('Nothing unsaved'); return; }
  dlg.replaceChildren(
    el('header', { class: 'dlg__head' },
      el('h2', { id: 'review-title', text: 'Unsaved changes' }),
      el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Close', onclick: () => dlg.close() },
        ico('fa-xmark'))),
    el('div', { class: 'dlg__body' },
      el('p', { class: 'insp__quiet', text: 'Edits wait here, per file, until they are reviewed and saved — moving to another album does not lose them.' }),
      el('div', { class: 'tray' }, list.map((draft) => el('div', { class: 'tray__row' },
        el('div', { class: 'tray__text' },
          el('code', { class: 'tray__file', text: draftLabel(draft.sel) }),
          el('span', { class: 'tray__keys', text: Object.keys(draft.edits).join(', ') })),
        el('div', { class: 'tray__acts' },
          el('button', { type: 'button', class: 'btn btn--sm', icon: 'fa-arrow-right', text: 'Open',
                         onclick: () => { dlg.close(); go(draft.sel); } }),
          el('button', { type: 'button', class: 'btn btn--sm', text: 'Discard',
                         onclick: () => { discardDraft(draft); openTray(); } }),
          el('button', { type: 'button', class: 'btn btn--sm btn--primary', icon: 'fa-floppy-disk', text: 'Review',
                         disabled: READ_ONLY, onclick: () => reviewDraft(draft, () => { if (pendingDrafts().length) openTray(); }) })))))));
  if (!dlg.open) dlg.showModal();
}

function discardDraft(draft) {
  drafts.delete(draft.key);
  if (draftKey(state.sel) === draft.key) { state.edits = {}; renderPane(); }
  syncDirtyMark();
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
        text: READ_ONLY ? 'Read-only mount — saving is off' : 'Saving writes the whole file.' }),
      el('button', {
        class: 'btn', type: 'button', icon: 'fa-rotate-left', text: 'Revert',
        onclick: () => { area.value = state.data.raw || ''; },
      }),
      el('button', {
        class: 'btn btn--primary', type: 'button', icon: 'fa-floppy-disk', text: 'Save file', disabled: READ_ONLY,
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

/* ----- description tab -------------------------------------------------- */
function renderDescriptions() {
  /* The text section shares its page with the settings now, and a setting
   * change redraws the page -- so what is typed here is kept as a draft of
   * its own and put back, rather than living only in the textarea. */
  const dkey = state.sel.album + '|' + state.descLang;
  const area = el('textarea', {
    class: 'u-tall', spellcheck: 'false', disabled: READ_ONLY, 'data-fk': 'desc',
    oninput: (ev) => {
      descDrafts[dkey] = ev.target.value === (state.data.descriptions[state.descLang] || '') ? null : ev.target.value;
      syncDirtyMark();
    },
  });
  area.value = descDrafts[dkey] ?? (state.data.descriptions[state.descLang] || '');

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
          state.descLang = lang;
          renderPane();
        },
      }))),
    area,
    el('div', { class: 'savebar' },
      el('span', { class: 'savebar__note', text: descDrafts[dkey] != null
        ? 'album_' + state.descLang + '.md has unsaved text' : 'album_' + state.descLang + '.md' }),
      el('button', {
        class: 'btn btn--ghost', type: 'button', icon: 'fa-clock-rotate-left', text: 'History',
        onclick: () => openHistory({ file: 'desc', album: state.sel.album, lang: state.descLang }),
      }),
      el('button', {
        class: 'btn btn--primary', type: 'button', icon: 'fa-floppy-disk', text: 'Save description', disabled: READ_ONLY,
        onclick: async () => {
          try {
            const payload = await api('/api/album/description', {
              method: 'PUT',
              body: JSON.stringify({ album: state.sel.album, lang: state.descLang, text: area.value }),
            });
            state.data.descriptions[state.descLang] = payload.text;
            descDrafts[dkey] = null;
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
    class: 'dropzone', icon: 'fa-upload',
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
      VIDEO_RE.test(asset.name) ? ico('fa-film')
        : IMAGE_RE.test(asset.name) ? el('img', { src: url, alt: '' })
        : 'Aa'),
    el('span', { class: 'asset__name', text: asset.name }),
    assetInUse(asset) ? el('span', { class: 'pill pill--ok', icon: 'fa-check', text: 'in use' }) : null,
    el('span', { class: 'asset__meta', text: bytes(asset.size) }),
    READ_ONLY ? null : el('button', {
      class: 'btn btn--sm btn--ghost btn--danger', type: 'button', icon: 'fa-trash', text: 'Delete',
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
 * for one album or one photo — `/s/tokyo` instead of `/album/japan_2026/tokyo`.
 *
 * The server decides what a name may be and whether a target exists
 * (aperture/links.py), and says so on every write. The rule is mirrored here
 * only so a typo shows while it is typed rather than after a round trip; the
 * `/s/` prefix comes from the server rather than being spelled a second time. */
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
    type: 'button', class: 'btn btn--sm', icon: 'fa-link', text: label,
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
  if (slug !== was && d.links.some((l) => l.slug === slug)) {
    return d.prefix + slug + ' already exists — edit it below instead';
  }
  return null;
}

/* The address a visitor types. The console is on its own port and cannot
 * know it unless PUBLIC_BASE_URL says; without that it is a path. */
const publicUrl = (slug) => (links.data.base || '') + links.data.prefix + slug;

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
          el('span', { class: 'pill' + (d.links.length ? ' pill--ok' : ''), icon: 'fa-link',
                       text: d.links.length + (d.links.length === 1 ? ' link' : ' links') }),
          broken ? el('span', { class: 'pill pill--err', icon: 'fa-link-slash', text: broken + ' broken' }) : null,
          READ_ONLY ? el('span', { class: 'pill pill--warn', icon: 'fa-lock', text: 'read-only' }) : null)))));

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
    type: 'button', class: 'btn btn--primary', icon: editing ? 'fa-floppy-disk' : 'fa-plus', text: editing ? 'Save link' : 'Create link',
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

  return card(editing ? 'fa-pen' : 'fa-link',
    editing ? 'Edit ' + links.data.prefix + draft.was : 'New link',
    'one album or one photo, at a short address',
    el('div', { class: 'links__form' },
      thumb,
      el('div', { class: 'links__field' },
        el('span', { class: 'links__label', text: 'Name' }),
        el('div', { class: 'links__addr' },
          el('span', { class: 'links__base', text: (links.data.base || '') + links.data.prefix,
                       title: links.data.base || 'set PUBLIC_BASE_URL to show the full address' }),
          slugField)),
      el('div', { class: 'links__field' },
        el('span', { class: 'links__label', text: 'Goes to' }),
        el('div', { class: 'links__target' },
          targetField,
          el('button', {
            type: 'button', class: 'btn', icon: 'fa-image', text: 'Pick a photo…',
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
            type: 'button', class: 'btn btn--ghost', icon: editing ? 'fa-xmark' : 'fa-eraser',
            text: editing ? 'Cancel' : 'Clear',
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

  return card('fa-list', 'Every link',
    d.base || 'set PUBLIC_BASE_URL to show and open full addresses',
    d.links.length > 6
      ? searchbox({
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
      el('div', { class: 'linkrow__slug', text: links.data.prefix + link.slug, title: url }),
      el('div', { class: 'linkrow__target' },
        el('span', { class: 'linkrow__kind', text: link.kind }),
        el('span', { class: 'linkrow__path', text: link.target || '—' })),
      bad ? el('div', { class: 'linkrow__issue', text: link.issues.map((i) => i.detail).join(' · ') })
          : null),
    el('div', { class: 'linkrow__actions' },
      el('button', { type: 'button', class: 'btn btn--sm', icon: 'fa-copy', text: 'Copy', onclick: () => copyLink(url) }),
      /* Only with a known public address: a bare /name opened from here
       * would ask the console's own port, which has no such page. */
      links.data.base && link.destination
        ? el('a', { class: 'btn btn--sm btn--ghost', href: url, target: '_blank',
                    rel: 'noopener noreferrer', text: 'Open',
                    iconEnd: 'fa-arrow-up-right-from-square' })
        : null,
      READ_ONLY ? null : el('button', {
        type: 'button', class: 'btn btn--sm btn--ghost', icon: 'fa-pen', text: 'Edit',
        onclick: () => {
          links.draft = { slug: link.slug, target: link.target, was: link.slug, touched: true };
          paintLinks();
          $('#pane').scrollTo({ top: 0, behavior: 'instant' });
        },
      }),
      READ_ONLY ? null : el('button', {
        type: 'button', class: 'btn btn--sm btn--ghost btn--danger', icon: 'fa-trash', text: 'Delete',
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
  if (!confirm('Delete ' + links.data.prefix + slug + '? Anyone who follows it gets a 404 from now on.')) return;
  try {
    links.data = await api('/api/links?slug=' + encodeURIComponent(slug), { method: 'DELETE' });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  if (links.draft.was === slug) links.draft = blankDraft();
  toast('Deleted ' + links.data.prefix + slug);
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
  refreshAlbumViews();
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
    refreshAlbumViews();
  } catch (_) { /* the dots are a nicety, not worth a toast */ }
}

window.addEventListener('beforeunload', (ev) => {
  stashDraft();
  if (drafts.size || Object.values(descDrafts).some(Boolean)) { ev.preventDefault(); ev.returnValue = ''; }
});

boot();
