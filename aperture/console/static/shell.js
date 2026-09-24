/* lucya.systems aperture — the console's shell.
 *
 * The frame every screen sits in: the places on the rail, the address bar
 * that names the place you are on, the command palette that reaches
 * everything by typing, and the keys. app.js draws what is IN a place; this
 * file decides which place that is and how you get to another.
 *
 * Loaded before app.js as a plain script, so the two share one global scope.
 * Nothing here runs at load time except declarations -- every function reads
 * app.js's `state`, `el` and `api` only when it is called, after both files
 * have been parsed.
 */
'use strict';

/* ----- places -------------------------------------------------------------
 * A place is a job, not a file. `kinds` are the state.sel kinds that light
 * it: an album is drawn by the Albums place, the changelog by System. */
const PLACES = [
  { id: 'home',    path: '/',        label: 'Overview', icon: 'fa-gauge',       key: 'o', kinds: ['home'] },
  { id: 'library', path: '/library', label: 'Library',  icon: 'fa-images',      key: 'l', kinds: ['library'] },
  { id: 'albums',  path: '/albums',  label: 'Albums',   icon: 'fa-folder-tree', key: 'a', kinds: ['albums', 'album'] },
  { id: 'tags',    path: '/tags',    label: 'Tags',     icon: 'fa-tags',        key: 't', kinds: ['tags'] },
  { id: 'gallery', path: '/site',    label: 'Site',     icon: 'fa-globe',       key: 's', kinds: ['gallery'] },
  { id: 'links',   path: '/links',   label: 'Links',    icon: 'fa-link',        key: 'k', kinds: ['links'] },
  { id: 'ops',     path: '/system',  label: 'System',   icon: 'fa-server',      key: 'y', kinds: ['ops', 'changelog'] },
];

const placeOf = (kind) => PLACES.find((p) => p.kinds.includes(kind)) || PLACES[0];

/* A folder path in an address: each segment escaped on its own, so the
 * slashes between them stay slashes. */
const encPath = (path) => String(path || '').split('/').map(encodeURIComponent).join('/');
const decPath = (path) => String(path || '').split('/').filter(Boolean)
  .map((part) => { try { return decodeURIComponent(part); } catch (_) { return part; } }).join('/');

/* ----- the address --------------------------------------------------------
 * sel -> path and back. The path is the whole truth about where you are, so
 * a reload, Back and a bookmark all land on the same screen. */
function pathFor(sel) {
  if (!sel) return '/';
  switch (sel.kind) {
    case 'library': return '/library' + (sel.album ? '/' + encPath(sel.album) : '');
    case 'albums': return '/albums';
    case 'album': return '/albums/' + encPath(sel.album);
    case 'tags': return '/tags';
    case 'gallery': return '/site';
    case 'links': return '/links';
    case 'changelog': return '/system/about';
    case 'ops': return '/system' + (sel.tab && sel.tab !== 'overview' ? '/' + sel.tab : '');
    default: return '/';
  }
}

function selFromPath(pathname) {
  const path = String(pathname || '/').replace(/\/+$/, '') || '/';
  const [, head = '', ...rest] = path.split('/');
  const tail = decPath(rest.join('/'));
  switch (head) {
    case '': return { kind: 'home' };
    case 'library':
      return { kind: 'library', album: tail && albumExists(tail) ? tail : '' };
    case 'albums':
      return tail && albumExists(tail) ? { kind: 'album', album: tail } : { kind: 'albums' };
    case 'tags': return { kind: 'tags' };
    case 'site': return { kind: 'gallery' };
    case 'links': return { kind: 'links' };
    case 'system':
      if (tail === 'about') return { kind: 'changelog' };
      return { kind: 'ops', tab: SYSTEM_TABS.some(([id]) => id === tail) ? tail : 'overview' };
    default: return { kind: 'home' };
  }
}

/* Write the address for a place without drawing anything: select() calls it
 * once it has decided to go, and a System section switch calls it on its own. */
function setAddress(sel, replace = false) {
  const path = pathFor(sel);
  if (path === location.pathname) return;
  if (replace) history.replaceState(null, '', path);
  else history.pushState(null, '', path);
}

/* Go somewhere. The one entry point for links, the rail, the palette and the
 * keys, so every way of moving asks about unsaved changes the same way. */
function go(sel) {
  select(sel);
}

window.addEventListener('popstate', () => {
  const sel = selFromPath(location.pathname);
  select(sel, false, 'none').then((went) => {
    // Back was refused over unsaved changes: put the address back where the
    // screen still is.
    if (went === false) setAddress(state.sel);
  });
});

/* Plain links with data-go are ours: an ordinary click navigates in place,
 * a modified one (new tab, new window) is left to the browser. */
document.addEventListener('click', (ev) => {
  const link = ev.target.closest && ev.target.closest('a[data-go]');
  if (!link || ev.defaultPrevented || ev.button !== 0) return;
  if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  ev.preventDefault();
  go(selFromPath(new URL(link.href, location.href).pathname));
});

/* ----- the rail ---------------------------------------------------------- */
function renderRail() {
  const nav = $('#rail-nav');
  if (!nav) return;
  const counts = railCounts();
  nav.replaceChildren(...PLACES.map((place) => el('li', {},
    el('a', {
      class: 'rail__link', href: place.path, 'data-go': true, 'data-place': place.id,
      title: place.label + ' (g ' + place.key + ')',
    },
      ico(place.icon),
      el('span', { class: 'rail__label', text: place.label }),
      counts[place.id] != null
        ? el('span', { class: 'rail__count', text: String(counts[place.id]) }) : null))));
  syncRail();
  syncUpdateMark();
}

function railCounts() {
  const idx = (opsState.status && opsState.status.index) || {};
  return {
    library: L.photos ? L.photos.length : idx.images ?? null,
    albums: state.tree ? countAlbums(state.tree) : null,
    tags: state.vocab ? state.vocab.length : null,
  };
}

function syncRail() {
  const current = state.sel ? placeOf(state.sel.kind).id : null;
  $$('.rail__link').forEach((link) => {
    const on = link.dataset.place === current;
    link.classList.toggle('is-active', on);
    if (on) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (state.issuesByAlbum) {
    const home = $('.rail__link[data-place="home"]');
    if (home) home.classList.toggle('has-issues', Object.keys(state.issuesByAlbum).length > 0);
  }
}

/* Every folder below the root that can carry an album.cfg. */
function countAlbums(node) {
  return (node.children || []).reduce((n, child) => n + 1 + countAlbums(child), 0);
}

/* Every album, depth-first, as the tree lists them. */
function allAlbums() {
  const out = [];
  (function walk(node, depth) {
    for (const child of node.children || []) {
      out.push({ node: child, depth });
      walk(child, depth + 1);
    }
  })(state.tree || { children: [] }, 0);
  return out;
}

/* ----- the bar's two marks ------------------------------------------------ */
/* The indexer's lamp, in the bar on every screen: the one fact about the
 * machine worth knowing wherever you are. */
function syncLamp() {
  const st = opsState.status || {};
  const dot = $('#nav-lamp-dot');
  const word = $('#nav-lamp-word');
  if (!dot || !word) return;
  const scanning = !!(st.server && st.server.scanning);
  const tone = st.error ? 'bad' : st.paused ? 'warn' : st.live ? 'ok' : 'bad';
  dot.className = 'lamp__dot is-' + tone;
  word.textContent = st.error ? 'offline' : st.paused ? 'paused'
    : scanning ? 'scanning' : st.live ? 'indexer' : 'stopped';
  $('#nav-lamp').title = st.error ? 'The indexer status could not be read'
    : st.paused ? 'The indexer is paused' : scanning ? 'A scan is running'
      : st.live ? 'The indexer is running' : 'The indexer has no heartbeat';
}

let lampTimer = null;
function watchLamp() {
  clearInterval(lampTimer);
  lampTimer = setInterval(async () => {
    if (document.hidden) return;
    await loadOpsStatus();
    syncLamp();
  }, 30000);
}

/* Unsaved edits on the page you are on, said in the bar so they are visible
 * from the top of a settings page three screens deep. */
function syncDirtyMark() {
  const mark = $('#dirty-mark');
  if (!mark) return;
  const files = pendingDrafts().length;
  const texts = Object.values(descDrafts).filter((v) => v != null).length;
  const n = files + texts;
  mark.hidden = !n;
  $('#dirty-count').textContent = String(n);
  mark.title = n ? files + ' file' + (files === 1 ? '' : 's') + ' with unsaved settings' +
    (texts ? ', ' + texts + ' unsaved text' + (texts === 1 ? '' : 's') : '') : '';
}

/* ----- albums: the place ---------------------------------------------------
 * Every album as a row of facts, instead of a tree in a sidebar that the
 * whole console had to share its width with. What the Home screen calls
 * "unwritten" is a column here, so it can be sorted and filtered. */
const albumsView = { filter: '', show: 'all', sort: 'tree', sel: new Set() };

function renderAlbums() {
  const pane = $('#pane');
  pane.innerHTML = '';
  const rows = allAlbums();
  const withPhotos = rows.filter((r) => r.node.own_photos);

  pane.append(el('div', { class: 'pane__top' },
    el('div', { class: 'head' },
      el('div', { class: 'head__crumb', text: state.meta.photos_dir }),
      el('div', { class: 'head__line' },
        el('h1', { class: 'head__title', text: 'Albums' }),
        el('div', { class: 'head__meta' },
          el('span', { class: 'pill', icon: 'fa-folder-tree', text: rows.length + ' folders' }),
          el('span', { class: 'pill', icon: 'fa-image', text: withPhotos.length + ' with photos' })))),
    el('div', { class: 'tbar' },
      searchbox({
        type: 'search', class: 'fieldsearch', id: 'albums-filter', value: albumsView.filter,
        placeholder: 'Filter albums…', autocomplete: 'off',
        oninput: (ev) => { albumsView.filter = ev.target.value; paintAlbumRows(); },
      }),
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Show' },
        [['all', 'All'], ['unwritten', 'Unwritten'], ['issues', 'With issues']].map(([id, label]) =>
          el('button', {
            type: 'button', class: 'seg__opt' + (albumsView.show === id ? ' is-on' : ''),
            'aria-pressed': String(albumsView.show === id), text: label,
            onclick: () => { albumsView.show = id; renderAlbums(); },
          }))),
      el('div', { class: 'seg', role: 'group', 'aria-label': 'Sort' },
        [['tree', 'Tree'], ['name', 'A–Z'], ['photos', 'Most photos']].map(([id, label]) =>
          el('button', {
            type: 'button', class: 'seg__opt' + (albumsView.sort === id ? ' is-on' : ''),
            'aria-pressed': String(albumsView.sort === id), text: label,
            onclick: () => { albumsView.sort = id; renderAlbums(); },
          }))))));

  pane.append(el('div', { class: 'tabpanel' },
    el('div', { class: 'albums__bulk', id: 'albums-bulk', hidden: true }),
    el('div', { class: 'tbl-wrap' },
      el('table', { class: 'tbl albums' },
        el('thead', {}, el('tr', {},
          el('th', { class: 'albums__tickcol' }, el('input', {
            type: 'checkbox', id: 'albums-all', 'aria-label': 'Tick every album shown',
            onchange: (ev) => {
              $$('#albums-rows .albums__tick').forEach((box) => {
                box.checked = ev.target.checked;
                if (ev.target.checked) albumsView.sel.add(box.value); else albumsView.sel.delete(box.value);
              });
              paintAlbumsBulk();
            },
          })),
          el('th', { text: 'Album' }),
          el('th', { class: 'r', text: 'Photos' }),
          el('th', { text: 'Config' }),
          el('th', { text: 'Text' }),
          el('th', { class: 'r', text: '' }))),
        el('tbody', { id: 'albums-rows' })))));
  paintAlbumRows();
}

function paintAlbumRows() {
  const body = $('#albums-rows');
  if (!body) return;
  const q = albumsView.filter.trim().toLowerCase();
  let rows = allAlbums().filter(({ node }) => {
    if (q && !node.path.toLowerCase().includes(q)) return false;
    if (albumsView.show === 'unwritten') return node.own_photos && (!node.has_cfg || !node.has_desc);
    if (albumsView.show === 'issues') return (state.issuesByAlbum[node.path] || 0) > 0;
    return true;
  });
  if (albumsView.sort === 'name') {
    rows = rows.slice().sort((a, b) => a.node.name.localeCompare(b.node.name));
  } else if (albumsView.sort === 'photos') {
    rows = rows.slice().sort((a, b) => (b.node.total_photos || 0) - (a.node.total_photos || 0));
  }
  const flat = albumsView.sort !== 'tree' || !!q;

  if (!rows.length) {
    body.replaceChildren(el('tr', {}, el('td', { colspan: '6', class: 'albums__none',
      text: q ? 'No album matches “' + albumsView.filter.trim() + '”.' : 'Nothing to show here.' })));
    return;
  }
  body.replaceChildren(...rows.map(({ node, depth }) => {
    const issues = state.issuesByAlbum[node.path] || 0;
    const open = pathFor({ kind: 'album', album: node.path });
    return el('tr', { class: 'albums__row', onclick: (ev) => {
      if (ev.target.closest('a, button, input, label')) return;
      go({ kind: 'album', album: node.path });
    } },
      el('td', { class: 'albums__tickcol' }, el('input', {
        type: 'checkbox', class: 'albums__tick', value: node.path, checked: albumsView.sel.has(node.path),
        'aria-label': 'Tick ' + node.path,
        onchange: (ev) => {
          if (ev.target.checked) albumsView.sel.add(node.path); else albumsView.sel.delete(node.path);
          paintAlbumsBulk();
        },
      })),
      el('td', {},
        el('a', {
          class: 'albums__name', href: open, 'data-go': true,
          'data-depth': String(flat ? 0 : Math.min(depth, 6)), title: node.path,
        },
          el('span', { class: 'albums__cover' }, node.cover
            ? el('img', { src: thumbUrl(node.cover), alt: '', loading: 'lazy',
                          onerror: (e) => e.target.remove() })
            : null),
          el('span', { class: 'albums__text' },
            el('span', { class: 'albums__title', text: node.name }),
            flat && node.path !== node.name
              ? el('span', { class: 'albums__path', text: node.path }) : null))),
      el('td', { class: 'num r', text: node.own_photos === node.total_photos
        ? String(node.total_photos || 0)
        : (node.own_photos || 0) + ' / ' + (node.total_photos || 0),
        title: node.own_photos === node.total_photos ? null : 'in this folder / with sub-albums' }),
      el('td', {}, issues
        ? el('span', { class: 'status status--err', icon: 'fa-circle-exclamation', text: issues + ' issue' + (issues === 1 ? '' : 's') })
        : node.has_cfg
          ? el('span', { class: 'status status--ok', icon: 'fa-circle-check', text: 'album.cfg' })
          : el('span', { class: 'status', text: node.own_photos ? 'none yet' : '—' })),
      el('td', {}, node.has_desc
        ? el('span', { class: 'status status--ok', icon: 'fa-circle-check', text: 'written' })
        : el('span', { class: 'status', text: node.own_photos ? 'none yet' : '—' })),
      el('td', { class: 'r' },
        el('a', { class: 'btn btn--ghost', href: pathFor({ kind: 'library', album: node.path }),
                  'data-go': true, icon: 'fa-images', text: 'Photos' })));
  }));
  paintAlbumsBulk();
}

/* What is ticked, and the one thing to do with it: set a key on all of
 * them (tools.js, openBulk). */
function paintAlbumsBulk() {
  const bar = $('#albums-bulk');
  if (!bar) return;
  const n = albumsView.sel.size;
  bar.hidden = !n;
  bar.replaceChildren(...(n ? [
    el('span', { class: 'albums__bulkcount', text: n + (n === 1 ? ' album' : ' albums') + ' ticked' }),
    el('button', { type: 'button', class: 'btn btn--primary', icon: 'fa-sliders', text: 'Set a key…',
                   disabled: READ_ONLY, onclick: () => openBulk([...albumsView.sel]) }),
    el('button', { type: 'button', class: 'btn btn--ghost', text: 'Untick all',
                   onclick: () => { albumsView.sel.clear(); renderAlbums(); } }),
  ] : []));
}

/* ----- the command palette -------------------------------------------------
 * One field that reaches everything. Places, every album, every setting on
 * either file, every tag, and the handful of actions that used to be buttons
 * scattered over the header and the Operations tabs. */
const palette = { items: [], shown: [], idx: 0 };

function paletteItems() {
  const items = [];
  for (const place of PLACES) {
    items.push({ group: 'Places', label: place.label, icon: place.icon, keys: 'g ' + place.key,
                 run: () => go({ kind: place.kinds[0] === 'albums' ? 'albums' : place.kinds[0] }) });
  }
  for (const { node } of allAlbums()) {
    items.push({
      group: 'Albums', label: node.path, icon: 'fa-folder',
      sub: (node.total_photos || 0) + ' photos',
      run: () => go({ kind: 'album', album: node.path }),
      alt: () => go({ kind: 'library', album: node.path }),
    });
  }
  // Settings: the site's own file always, and the open album's while one is.
  const help = state.meta ? state.meta.help || {} : {};
  for (const key of (state.meta && state.meta.gallery_keys) || []) {
    items.push({ group: 'Settings', label: key, icon: 'fa-sliders', sub: 'Site',
                 hay: (state.meta.gallery_help || {})[key] || help[key] || '',
                 run: () => openSetting({ kind: 'gallery' }, key) });
  }
  if (state.sel && state.sel.kind === 'album') {
    const album = state.sel.album;
    for (const key of state.meta.album_keys || []) {
      items.push({ group: 'Settings', label: key, icon: 'fa-sliders', sub: album,
                   hay: help[key] || '', run: () => openSetting({ kind: 'album', album }, key) });
    }
  }
  for (const tag of state.vocab || []) {
    items.push({ group: 'Tags', label: tag.name, icon: 'fa-tag',
                 sub: tag.count + ' photo' + (tag.count === 1 ? '' : 's'),
                 run: () => openTag(tag.name) });
  }
  const st = opsState.status || {};
  const ro = READ_ONLY || !!st.read_only;
  const actions = [
    ['Scan the whole gallery', 'fa-arrows-rotate', () => startScan('', false), ro],
    [st.paused ? 'Resume the indexer' : 'Pause the indexer', st.paused ? 'fa-play' : 'fa-pause',
      () => (st.paused ? doResume() : doPause('')), ro],
    ['Check every config file', 'fa-list-check', () => checkAll(), false],
    ['Reload albums and tags', 'fa-rotate', () => reloadAll(), false],
    ['New pretty link', 'fa-link', () => go({ kind: 'links' }), ro],
    ['Review unsaved changes', 'fa-floppy-disk', () => openTray(), !pendingDrafts().length],
    ['Keyboard shortcuts', 'fa-keyboard', () => openKeys(), false],
  ];
  for (const [label, icon, run, off] of actions) {
    if (!off) items.push({ group: 'Actions', label, icon, run });
  }
  if ($('#btn-signout')) {
    items.push({ group: 'Actions', label: 'Sign out', icon: 'fa-right-from-bracket', run: signOut });
  }
  return items;
}

/* Subsequence match with a score: a hit at the start of the label or of a
 * path segment beats one in the middle, a run of consecutive letters beats
 * scattered ones, and a hit only in the help text ranks last. */
function paletteScore(item, q) {
  const label = item.label.toLowerCase();
  const at = label.indexOf(q);
  if (at === 0) return 1000 - label.length;
  if (at > 0) return (/[/_\s-]/.test(label[at - 1]) ? 800 : 600) - at;
  let i = 0;
  let run = 0;
  let score = 0;
  let first = -1;
  let last = -1;
  for (let j = 0; j < label.length && i < q.length; j++) {
    if (label[j] === q[i]) {
      if (first < 0) first = j;
      last = j;
      i++; run++; score += run * 3;
    } else run = 0;
  }
  // Letters strewn across a whole sentence are not a match anyone meant.
  if (i === q.length && last - first < q.length * 4) return 200 + score - label.length / 10;
  const extra = ((item.sub || '') + ' ' + (item.hay || '')).toLowerCase();
  return extra.includes(q) ? 50 : -1;
}

const GROUP_ORDER = ['Places', 'Actions', 'Albums', 'Settings', 'Tags'];
const GROUP_CAP = { Albums: 8, Settings: 6, Tags: 6, Actions: 8, Places: 7 };

function paletteFilter(q) {
  q = q.trim().toLowerCase();
  let hits;
  if (!q) {
    hits = palette.items.filter((it) => it.group === 'Places' || it.group === 'Actions')
      .map((it) => ({ it, s: 0 }));
  } else {
    hits = palette.items.map((it) => ({ it, s: paletteScore(it, q) })).filter((h) => h.s >= 0);
  }
  const out = [];
  for (const group of GROUP_ORDER) {
    const inGroup = hits.filter((h) => h.it.group === group).sort((a, b) => b.s - a.s)
      .slice(0, GROUP_CAP[group]);
    out.push(...inGroup.map((h) => h.it));
  }
  // With a query, the best single hit leads regardless of its group.
  if (q && out.length) {
    const best = hits.slice().sort((a, b) => b.s - a.s)[0].it;
    const at = out.indexOf(best);
    if (at > 0 && GROUP_ORDER.indexOf(best.group) > GROUP_ORDER.indexOf(out[0].group)) {
      palette.idx = at;
      return out;
    }
  }
  palette.idx = 0;
  return out;
}

function paintPalette() {
  const list = $('#palette-list');
  list.innerHTML = '';
  if (!palette.shown.length) {
    list.append(el('li', { class: 'palette__none', text: 'Nothing matches.' }));
    return;
  }
  let group = null;
  palette.shown.forEach((item, i) => {
    if (item.group !== group) {
      group = item.group;
      list.append(el('li', { class: 'palette__group', role: 'presentation', text: group }));
    }
    list.append(el('li', {
      class: 'palette__item' + (i === palette.idx ? ' is-on' : ''),
      role: 'option', id: 'pal-' + i, 'aria-selected': String(i === palette.idx),
      onmousemove: () => { if (palette.idx !== i) { palette.idx = i; markPalette(); } },
      onclick: (ev) => runPalette(i, ev.shiftKey),
    },
      ico(item.icon || 'fa-circle'),
      el('span', { class: 'palette__label', text: item.label }),
      item.sub ? el('span', { class: 'palette__sub', text: item.sub }) : null,
      item.keys ? el('kbd', { text: item.keys }) : null));
  });
  markPalette();
}

function markPalette() {
  $$('#palette-list .palette__item').forEach((node, i) => {
    const on = i === palette.idx;
    node.classList.toggle('is-on', on);
    node.setAttribute('aria-selected', String(on));
    if (on) node.scrollIntoView({ block: 'nearest' });
  });
  $('#palette-input').setAttribute('aria-activedescendant', 'pal-' + palette.idx);
}

function openPalette(seed = '') {
  const dlg = $('#palette');
  if (dlg.open) return;
  palette.items = paletteItems();
  const input = $('#palette-input');
  input.value = seed;
  palette.shown = paletteFilter(seed);
  paintPalette();
  dlg.showModal();
  input.focus();
}

function closePalette() {
  const dlg = $('#palette');
  if (dlg.open) dlg.close();
}

function runPalette(i, alt) {
  const item = palette.shown[i];
  if (!item) return;
  closePalette();
  (alt && item.alt ? item.alt : item.run)();
}

function wirePalette() {
  const dlg = $('#palette');
  const input = $('#palette-input');
  $('#btn-palette').addEventListener('click', () => openPalette());
  input.addEventListener('input', () => {
    palette.shown = paletteFilter(input.value);
    paintPalette();
  });
  input.addEventListener('keydown', (ev) => {
    const n = palette.shown.length;
    if (ev.key === 'ArrowDown') { ev.preventDefault(); if (n) { palette.idx = (palette.idx + 1) % n; markPalette(); } }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); if (n) { palette.idx = (palette.idx - 1 + n) % n; markPalette(); } }
    else if (ev.key === 'Enter') { ev.preventDefault(); runPalette(palette.idx, ev.shiftKey); }
  });
  // A click on the backdrop -- outside the box -- closes it, like Escape.
  dlg.addEventListener('click', (ev) => { if (ev.target === dlg) closePalette(); });
}

/* Open a file's settings with one key in front of you: the finder filtered
 * to it, so it is the only tile on the page. */
async function openSetting(sel, key) {
  const same = state.sel && state.sel.kind === sel.kind && state.sel.album === sel.album;
  if (!same) {
    const went = await select(sel);
    if (went === false) return;
  }
  state.tab = 'settings';
  state.query = key;
  renderPane();
  const input = $('input[data-fk="__q"]');
  if (input) input.focus();
}

async function reloadAll() {
  photoCache.clear();
  L.photos = null;
  await Promise.all([loadTree(), loadVocab(), loadOpsStatus()]);
  syncLamp();
  renderRail();
  if (state.sel) await select(state.sel, true, 'none');
  toast('Reloaded');
}

async function signOut() {
  try { await api('/api/session', { method: 'DELETE' }); } catch (_) { /* going anyway */ }
  toLogin('signout');
}

/* ----- keys ----------------------------------------------------------------
 * Nothing here fires while you are typing in a field, except the palette's
 * own Ctrl K, which is the way out of any field to anywhere. */
const KEYS = [
  ['Anywhere', [
    ['Ctrl K', 'Go to or run anything'],
    ['/', 'The same, when no field has focus'],
    ['?', 'This list'],
    ['Ctrl S', 'Review and save the file you are editing'],
    ['Esc', 'Close a dialog'],
  ]],
  ['Places', PLACES.map((p) => ['g ' + p.key, p.label])],
  ['Library', [
    ['Ctrl A', 'Select everything that matches'],
    ['Ctrl I', 'Invert the selection'],
    ['T', 'Tag the selection'],
    ['Ctrl Z', 'Undo a tag change'],
    ['Space', 'Look closer'],
    ['Esc', 'Let go of the selection'],
  ]],
];

function paintKeys() {
  $('#keys-body').replaceChildren(...KEYS.map(([title, rows]) => el('section', { class: 'keys__group' },
    el('h3', { class: 'keys__title', text: title }),
    el('dl', { class: 'keys__list' }, rows.map(([keys, what]) => [
      el('dt', {}, keys.split(' ').map((k) => el('kbd', { text: k }))),
      el('dd', { text: what }),
    ])))));
}

function openKeys() {
  const dlg = $('#keys');
  if (dlg.open) return;
  paintKeys();
  dlg.showModal();
}

const typing = (target) => !!target && (target.isContentEditable ||
  /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));

let gPending = 0;

function wireKeys() {
  $('#btn-keys').addEventListener('click', openKeys);
  $$('#keys [data-close]').forEach((b) => b.addEventListener('click', () => $('#keys').close()));
  $('#keys').addEventListener('click', (ev) => { if (ev.target === $('#keys')) $('#keys').close(); });

  document.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === 's') {
      // Save is always "review, then save" -- never a blind write.
      ev.preventDefault();
      if (draftKey(state.sel) && Object.keys(state.edits).length) reviewDraft(currentDraft());
      else if (pendingDrafts().length) openTray();
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      if ($('#palette').open) closePalette();
      else openPalette();
      return;
    }
    if (ev.ctrlKey || ev.metaKey || ev.altKey || typing(ev.target)) return;
    if ($('#palette').open || $('#keys').open || !$('#modal').hidden) return;

    if (ev.key === '/') { ev.preventDefault(); openPalette(); return; }
    if (ev.key === '?') { ev.preventDefault(); openKeys(); return; }
    if (ev.key === 'g') { gPending = Date.now(); return; }
    if (gPending && Date.now() - gPending < 1200) {
      gPending = 0;
      const place = PLACES.find((p) => p.key === ev.key.toLowerCase());
      if (place) {
        ev.preventDefault();
        go({ kind: place.kinds[0] });
      }
    }
  });
}

function wireShell() {
  renderRail();
  syncLamp();
  watchLamp();
  wirePalette();
  wireKeys();
  const signout = $('#btn-signout');
  if (signout) signout.addEventListener('click', signOut);
  $('#dirty-mark').addEventListener('click', (ev) => {
    ev.preventDefault();
    openTray();
  });
}
