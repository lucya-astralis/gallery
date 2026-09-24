/* lucya.systems aperture — the Library and the tag manager.
 *
 * Every photo in the archive in one grid, narrowed by facets and by the
 * gallery's own search grammar, with a selection model that works the way a
 * photo tool's does: click, Ctrl-click, Shift-click, a lasso, Ctrl A for
 * everything that matches. Whatever is selected can be tagged in one go, and
 * every tag change can be taken back.
 *
 * The list comes from /api/library: files and tags off disk, capture facts
 * out of the index. Tags are written to sidecars (/api/tags), never to a
 * photo; featuring and covers are ordinary album.cfg saves.
 *
 * A plain script between shell.js and app.js, sharing their scope. Its one
 * load-time act is the key listener, registered here so it runs BEFORE the
 * shell's -- it has to see a pending "g" chord before the shell clears it.
 */
'use strict';

const L = {
  photos: null,          // every photo, as /api/library sent it
  byRel: new Map(),
  pending: null,         // the request for them, while it is out
  // what narrows the grid
  album: '',             // a folder, and everything under it
  q: '',
  server: null,          // {q, matches: Set, filters} for the server half of q
  serverPending: null,
  tags: new Set(),       // any of these (lower-cased)
  untagged: false,
  cameras: new Set(),
  years: new Set(),
  featured: false,
  unindexed: false,
  // how it is drawn
  sort: 'newest',
  size: 'm',
  facetsOpen: false,
  // what is picked
  shown: [],             // the filtered, sorted list the grid draws
  sel: new Set(),        // rels
  anchor: -1,            // index in `shown` a Shift range starts from
  focus: -1,             // index in `shown` the arrow keys move
  rendered: 0,
  undo: [],
  redo: [],
  recent: [],            // tags used this session, newest first
};
const LIB_PAGE = 240;
const LIB_PREFS = 'aperture.library';

try {
  const saved = JSON.parse(localStorage.getItem(LIB_PREFS) || '{}');
  if (['newest', 'oldest', 'name', 'album'].includes(saved.sort)) L.sort = saved.sort;
  if (['s', 'm', 'l'].includes(saved.size)) L.size = saved.size;
} catch (_) { /* defaults */ }

function saveLibPrefs() {
  try { localStorage.setItem(LIB_PREFS, JSON.stringify({ sort: L.sort, size: L.size })); } catch (_) { /* fine */ }
}

/* ----- data ---------------------------------------------------------------- */
function loadLibrary(force = false) {
  if (L.photos && !force) return Promise.resolve(L.photos);
  if (!L.pending || force) {
    L.pending = api('/api/library').then((body) => {
      L.photos = body.photos;
      L.byRel = new Map(L.photos.map((p) => [p.rel, p]));
      for (const rel of [...L.sel]) if (!L.byRel.has(rel)) L.sel.delete(rel);
      syncVocab();
      return L.photos;
    }).finally(() => { L.pending = null; });
  }
  return L.pending;
}

/* The vocabulary everything else autocompletes against, recounted from
 * what is actually on disk -- the Library just read every sidecar anyway. */
function syncVocab() {
  const counts = new Map();
  for (const p of L.photos || []) {
    for (const tag of p.tags) {
      const key = tag.toLowerCase();
      const had = counts.get(key);
      counts.set(key, { name: had ? had.name : tag, count: (had ? had.count : 0) + 1 });
    }
  }
  state.vocab = [...counts.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const list = $('#vocab-list');
  if (list) list.replaceChildren(...state.vocab.map((t) => el('option', { value: t.name })));
  if (typeof renderRail === 'function') renderRail();
}

/* ----- the query ----------------------------------------------------------
 * One field, two halves. `tag:`, `album:` and `is:` are the console's own --
 * they filter on sidecars and folders, which the index does not know about
 * yet. Everything else is the gallery's grammar (aperture/search.py), sent
 * to the server as typed, so it means exactly what it means on /search. */
const Q_TOKEN = /([A-Za-z]+):("[^"]*"?|\S*)|"([^"]*)"?|(\S+)/g;

function splitQuery(q) {
  const local = [];
  const rest = [];
  for (const m of String(q || '').matchAll(Q_TOKEN)) {
    const key = (m[1] || '').toLowerCase();
    if (['tag', 'album', 'is'].includes(key)) {
      local.push({ key, value: (m[2] || '').replace(/"/g, '').trim().toLowerCase() });
    } else {
      rest.push(m[0]);
    }
  }
  return { local, server: rest.join(' ').trim() };
}

function askServer(q) {
  if (L.server && L.server.q === q) return;
  if (L.serverPending === q) return;
  L.serverPending = q;
  api('/api/library/search?q=' + encodeURIComponent(q)).then((body) => {
    if (L.serverPending !== q) return;
    L.server = { q, matches: new Set(body.matches), filters: body.filters };
    L.serverPending = null;
    if (onLibrary()) refilter();
  }).catch((err) => {
    L.serverPending = null;
    toast('Search failed: ' + err.message, 'err');
  });
}

const onLibrary = () => !!state.sel && state.sel.kind === 'library';
const yearOf = (p) => (p.taken ? p.taken.slice(0, 4) : 'Undated');
const cameraOf = (p) => p.camera || 'Unknown';
const inAlbum = (p, album) => !album || p.album === album || p.album.startsWith(album + '/');

/* Every photo that passes every filter except `skip` -- which is how a
 * facet counts: what you would get by ticking that one row as well. */
function passing(skip) {
  const { local, server } = splitQuery(L.q);
  const serverSet = server && L.server && L.server.q === server ? L.server.matches : null;
  return (L.photos || []).filter((p) => {
    if (skip !== 'album' && !inAlbum(p, L.album)) return false;
    if (serverSet && !serverSet.has(p.rel)) return false;
    for (const { key, value } of local) {
      if (!value) continue;
      if (key === 'tag' && !p.tags.some((t) => t.toLowerCase() === value)) return false;
      // the album and everything under it -- the gallery's search means the same
      if (key === 'album' && !(p.album.toLowerCase() === value.replace(/\/+$/, '') ||
                               p.album.toLowerCase().startsWith(value.replace(/\/+$/, '') + '/'))) return false;
      if (key === 'is') {
        if (value === 'featured' && !p.featured) return false;
        if (value === 'untagged' && p.tags.length) return false;
        if ((value === 'new' || value === 'unindexed') && p.indexed) return false;
        if (value === 'selected' && !L.sel.has(p.rel)) return false;
      }
    }
    if (skip !== 'tags' && (L.untagged || L.tags.size)) {
      const hit = (L.untagged && !p.tags.length) || p.tags.some((t) => L.tags.has(t.toLowerCase()));
      if (!hit) return false;
    }
    if (skip !== 'cameras' && L.cameras.size && !L.cameras.has(cameraOf(p))) return false;
    if (skip !== 'years' && L.years.size && !L.years.has(yearOf(p))) return false;
    if (skip !== 'status') {
      if (L.featured && !p.featured) return false;
      if (L.unindexed && p.indexed) return false;
    }
    return true;
  });
}

const SORTS = {
  newest: (a, b) => (b.taken || '').localeCompare(a.taken || '') || b.mtime - a.mtime,
  oldest: (a, b) => (a.taken || '9999').localeCompare(b.taken || '9999') || a.mtime - b.mtime,
  name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }),
  album: (a, b) => a.album.localeCompare(b.album) || a.name.localeCompare(b.name, undefined, { numeric: true }),
};

function computeShown() {
  const { server } = splitQuery(L.q);
  if (server) askServer(server);
  L.shown = passing(null).sort(SORTS[L.sort]);
  // A photo a filter took away is no longer something you can see you have
  // picked; keeping it selected would tag it behind your back.
  const visible = new Set(L.shown.map((p) => p.rel));
  for (const rel of [...L.sel]) if (!visible.has(rel)) L.sel.delete(rel);
  L.focus = Math.min(L.focus, L.shown.length - 1);
  L.anchor = Math.min(L.anchor, L.shown.length - 1);
}

const anyFilter = () => !!(L.q.trim() || L.tags.size || L.untagged || L.cameras.size ||
  L.years.size || L.featured || L.unindexed);

function clearFilters() {
  L.q = '';
  L.tags.clear();
  L.cameras.clear();
  L.years.clear();
  L.untagged = L.featured = L.unindexed = false;
  const input = $('#lib-q');
  if (input) input.value = '';
  refilter();
}

/* ----- the place ----------------------------------------------------------- */
async function renderLibrary() {
  const pane = $('#pane');
  L.album = state.sel.album || '';
  if (!L.photos) {
    pane.innerHTML = '';
    pane.append(el('div', { class: 'pane__empty', text: 'Reading the library…' }));
    try {
      await loadLibrary();
    } catch (err) {
      pane.innerHTML = '';
      pane.append(el('div', { class: 'pane__empty', text: err.message }));
      return;
    }
    if (!onLibrary()) return;   /* navigated away */
  }
  computeShown();

  const node = L.album ? treeNode(L.album) : null;
  pane.innerHTML = '';
  pane.classList.add('pane--library');
  pane.append(el('div', { class: 'pane__top' },
    pageHead(node ? node.name : 'Library', [
      el('span', { class: 'pill', icon: 'fa-image', id: 'lib-count' }),
      node ? el('a', { class: 'btn', href: pathFor({ kind: 'album', album: L.album }), 'data-go': true,
                       icon: 'fa-sliders', text: 'Album settings' }) : null,
    ], { tip: state.meta.photos_dir + (L.album ? '/' + L.album : '') }),
    libToolbar()));

  pane.append(el('div', { class: 'lib' + (L.facetsOpen ? ' is-facets-open' : '') },
    el('aside', { class: 'lib__facets', id: 'lib-facets', 'aria-label': 'Filters' }),
    el('div', { class: 'lib__main', id: 'lib-main' },
      el('div', { class: 'lib__active', id: 'lib-active' }),
      el('div', { class: 'lgrid lgrid--' + L.size, id: 'lib-grid', role: 'grid',
                  'aria-multiselectable': 'true', 'aria-label': 'Photos' }),
      el('div', { class: 'lib__more', id: 'lib-more' })),
    el('aside', { class: 'lib__insp', id: 'lib-insp', 'aria-label': 'Selection' })));

  wireLasso($('#lib-grid'));
  paintFacets();
  paintActive();
  paintGrid();
  paintInspector();
  paintCount();
}

function libToolbar() {
  return el('div', { class: 'tbar lib__bar' },
    el('button', {
      type: 'button', class: 'btn lib__facetbtn', icon: 'fa-filter', text: 'Filters',
      'aria-expanded': String(L.facetsOpen),
      onclick: () => { L.facetsOpen = !L.facetsOpen; $('.lib').classList.toggle('is-facets-open', L.facetsOpen); },
    }),
    searchbox({
      type: 'search', class: 'fieldsearch', id: 'lib-q', value: L.q, autocomplete: 'off', spellcheck: 'false',
      placeholder: 'Search — words, camera:, lens:, date:2026, tag:, is:untagged',
      oninput: (ev) => {
        L.q = ev.target.value;
        clearTimeout(L.qTimer);
        L.qTimer = setTimeout(refilter, 220);
      },
      onkeydown: (ev) => { if (ev.key === 'Escape' && ev.target.value) { ev.stopPropagation(); ev.target.value = ''; L.q = ''; refilter(); } },
    }),
    el('div', { class: 'seg', role: 'group', 'aria-label': 'Sort' },
      [['newest', 'Newest'], ['oldest', 'Oldest'], ['name', 'Name'], ['album', 'Album']].map(([id, label]) =>
        el('button', {
          type: 'button', class: 'seg__opt' + (L.sort === id ? ' is-on' : ''), text: label,
          'aria-pressed': String(L.sort === id),
          onclick: () => { L.sort = id; saveLibPrefs(); renderLibrary(); },
        }))),
    el('div', { class: 'seg', role: 'group', 'aria-label': 'Thumbnail size' },
      [['s', 'S'], ['m', 'M'], ['l', 'L']].map(([id, label]) =>
        el('button', {
          type: 'button', class: 'seg__opt' + (L.size === id ? ' is-on' : ''), text: label,
          'aria-pressed': String(L.size === id), title: { s: 'Small', m: 'Medium', l: 'Large' }[id] + ' thumbnails',
          onclick: () => {
            L.size = id; saveLibPrefs();
            $('#lib-grid').className = 'lgrid lgrid--' + id;
            $$('.lib__bar .seg:last-child .seg__opt').forEach((b) => {
              const on = b.textContent === label;
              b.classList.toggle('is-on', on);
              b.setAttribute('aria-pressed', String(on));
            });
          },
        }))));
}

/* Filter again and redraw what depends on it, keeping the toolbar -- and so
 * the caret in the search field -- where it is. */
function refilter() {
  if (!onLibrary() || !$('#lib-grid')) return;
  computeShown();
  paintFacets();
  paintActive();
  paintGrid();
  paintInspector();
  paintCount();
}

function paintCount() {
  const pill = $('#lib-count');
  if (!pill) return;
  const total = L.album ? L.photos.filter((p) => inAlbum(p, L.album)).length : L.photos.length;
  pill.replaceChildren(ico('fa-image'),
    (L.shown.length === total ? total : L.shown.length + ' of ' + total) + ' photos');
}

/* ----- facets -------------------------------------------------------------- */
function facetRow({ label, count, on, onclick, title, icon }) {
  return el('button', {
    type: 'button', class: 'facet' + (on ? ' is-on' : '') + (count ? '' : ' is-empty'),
    'aria-pressed': String(!!on), title: title || label, onclick,
  },
    icon ? ico(icon) : el('span', { class: 'facet__box' }),
    el('span', { class: 'facet__label', text: label }),
    el('span', { class: 'facet__n', text: String(count) }));
}

function facetGroup(title, rows, extra) {
  if (!rows.length) return null;
  return el('section', { class: 'facets__group' },
    el('h2', { class: 'facets__title', text: title }), rows, extra || null);
}

function counted(list, keyOf) {
  const counts = new Map();
  for (const p of list) {
    for (const key of [].concat(keyOf(p))) counts.set(key, (counts.get(key) || 0) + 1);
  }
  return counts;
}

function paintFacets() {
  const box = $('#lib-facets');
  if (!box) return;
  const groups = [];

  // Where: the folder you are in, and the folders under it.
  const inWhere = passing('album');
  const here = L.album ? treeNode(L.album) : state.tree;
  const children = (here && here.children) || [];
  const whereRows = [];
  if (L.album) {
    const parent = L.album.includes('/') ? L.album.slice(0, L.album.lastIndexOf('/')) : '';
    whereRows.push(el('a', {
      class: 'facet facet--up', href: pathFor({ kind: 'library', album: parent }), 'data-go': true,
    }, ico('fa-arrow-turn-up'), el('span', { class: 'facet__label', text: parent ? parent.split('/').pop() : 'All photos' })));
  }
  for (const child of children) {
    const n = inWhere.filter((p) => inAlbum(p, child.path)).length;
    whereRows.push(el('a', {
      class: 'facet' + (n ? '' : ' is-empty'), href: pathFor({ kind: 'library', album: child.path }),
      'data-go': true, title: child.path,
    }, ico('fa-folder'), el('span', { class: 'facet__label', text: child.name }),
    el('span', { class: 'facet__n', text: String(n) })));
  }
  groups.push(facetGroup(L.album ? 'In ' + (here ? here.name : L.album) : 'Albums', whereRows));

  // Tags: any of the ticked ones, and "untagged" as one more of them.
  const inTags = passing('tags');
  const tagCounts = counted(inTags, (p) => p.tags.map((t) => t.toLowerCase()));
  const names = new Map((state.vocab || []).map((t) => [t.name.toLowerCase(), t.name]));
  const tagKeys = [...new Set([...tagCounts.keys(), ...L.tags])]
    .sort((a, b) => (tagCounts.get(b) || 0) - (tagCounts.get(a) || 0) || a.localeCompare(b));
  const untaggedN = inTags.filter((p) => !p.tags.length).length;
  const tagRows = [facetRow({
    label: 'Untagged', count: untaggedN, on: L.untagged, icon: 'fa-tag',
    onclick: () => { L.untagged = !L.untagged; refilter(); },
  })];
  const showAll = L.facetTagsAll;
  for (const key of (showAll ? tagKeys : tagKeys.slice(0, 12))) {
    tagRows.push(facetRow({
      label: names.get(key) || key, count: tagCounts.get(key) || 0, on: L.tags.has(key),
      onclick: () => { if (L.tags.has(key)) L.tags.delete(key); else L.tags.add(key); refilter(); },
    }));
  }
  groups.push(facetGroup('Tags', tagRows, tagKeys.length > 12 ? el('button', {
    type: 'button', class: 'facets__more', text: showAll ? 'Fewer' : 'All ' + tagKeys.length + ' tags',
    onclick: () => { L.facetTagsAll = !showAll; paintFacets(); },
  }) : null));

  // Year and camera: what a scan read out of the EXIF.
  const years = counted(passing('years'), yearOf);
  groups.push(facetGroup('Year', [...new Set([...years.keys(), ...L.years])]
    .sort((a, b) => (a === 'Undated') - (b === 'Undated') || b.localeCompare(a)).map((y) => facetRow({
    label: y, count: years.get(y) || 0, on: L.years.has(y),
    onclick: () => { if (L.years.has(y)) L.years.delete(y); else L.years.add(y); refilter(); },
  }))));
  const cams = counted(passing('cameras'), cameraOf);
  groups.push(facetGroup('Camera', [...new Set([...cams.keys(), ...L.cameras])]
    .sort((a, b) => (cams.get(b) || 0) - (cams.get(a) || 0)).map((c) => facetRow({
      label: c, count: cams.get(c) || 0, on: L.cameras.has(c),
      onclick: () => { if (L.cameras.has(c)) L.cameras.delete(c); else L.cameras.add(c); refilter(); },
    }))));

  const inStatus = passing('status');
  groups.push(facetGroup('Status', [
    facetRow({ label: 'Featured', icon: 'fa-star', count: inStatus.filter((p) => p.featured).length,
               on: L.featured, onclick: () => { L.featured = !L.featured; refilter(); } }),
    facetRow({ label: 'Not indexed yet', icon: 'fa-clock-rotate-left', count: inStatus.filter((p) => !p.indexed).length,
               on: L.unindexed, onclick: () => { L.unindexed = !L.unindexed; refilter(); },
               title: 'On disk, but no scan has read it yet — no date or camera to filter on' }),
  ]));

  box.replaceChildren(viewsGroup(), ...groups.filter(Boolean));
}

/* What is narrowing the grid right now, each one a click from gone. */
function paintActive() {
  const box = $('#lib-active');
  if (!box) return;
  const chips = [];
  const drop = (label, fn) => chips.push(el('button', {
    type: 'button', class: 'chip chip--filter', title: 'Remove this filter', onclick: () => { fn(); refilter(); },
  }, el('span', { text: label }), ico('fa-xmark', true)));
  if (L.q.trim()) drop('“' + L.q.trim() + '”', () => { L.q = ''; $('#lib-q').value = ''; });
  if (L.untagged) drop('untagged', () => { L.untagged = false; });
  for (const t of L.tags) drop('tag: ' + t, () => L.tags.delete(t));
  for (const y of L.years) drop(y, () => L.years.delete(y));
  for (const c of L.cameras) drop(c, () => L.cameras.delete(c));
  if (L.featured) drop('featured', () => { L.featured = false; });
  if (L.unindexed) drop('not indexed', () => { L.unindexed = false; });
  const bad = L.server && splitQuery(L.q).server === L.server.q
    ? L.server.filters.filter((f) => !f.ok) : [];
  box.replaceChildren(...chips,
    ...bad.map((f) => el('span', { class: 'status status--warn', icon: 'fa-triangle-exclamation',
                                   text: f.label + ' cannot be read — ignored' })),
    ...(chips.length ? [el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: 'Clear all', onclick: clearFilters })] : []));
  box.hidden = !box.childNodes.length;
}

/* ----- the grid ------------------------------------------------------------ */
function paintGrid() {
  const grid = $('#lib-grid');
  if (!grid) return;
  grid.replaceChildren();
  L.rendered = 0;
  if (!L.shown.length) {
    grid.append(el('div', { class: 'lgrid__none' },
      el('p', { text: L.photos.length ? 'No photo matches.' : 'There are no photos yet.' }),
      anyFilter() ? el('button', { type: 'button', class: 'btn', text: 'Clear the filters', onclick: clearFilters }) : null));
    $('#lib-more').replaceChildren();
    return;
  }
  appendCells();
}

function appendCells() {
  const grid = $('#lib-grid');
  const upto = Math.min(L.shown.length, L.rendered + LIB_PAGE);
  const frag = document.createDocumentFragment();
  for (let i = L.rendered; i < upto; i++) frag.append(cellFor(L.shown[i], i));
  grid.append(frag);
  L.rendered = upto;
  const more = $('#lib-more');
  more.replaceChildren();
  if (L.rendered < L.shown.length) {
    // The rest arrive as the end of the grid comes into view: ten thousand
    // tiles at once would cost seconds on a phone for nothing on screen.
    const sentinel = el('button', {
      type: 'button', class: 'btn btn--ghost', text: 'Show ' + Math.min(LIB_PAGE, L.shown.length - L.rendered) + ' more',
      onclick: appendCells,
    });
    more.append(sentinel);
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { io.disconnect(); appendCells(); }
    }, { rootMargin: '600px' });
    io.observe(sentinel);
  }
}

function cellFor(p, i) {
  const picked = L.sel.has(p.rel);
  const cell = el('div', {
    class: 'lcell' + (picked ? ' is-picked' : '') + (i === L.focus ? ' is-focus' : ''),
    role: 'gridcell', 'aria-selected': String(picked), 'data-i': String(i), title: p.rel,
  },
    el('img', { src: thumbUrl(p.rel), alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' }),
    el('button', { type: 'button', class: 'lcell__tick', tabindex: '-1',
                   'aria-label': (picked ? 'Deselect ' : 'Select ') + p.name }),
    el('span', { class: 'lcell__marks' },
      p.featured ? el('span', { class: 'lcell__mark', icon: 'fa-star', title: 'Featured' }) : null,
      !p.indexed ? el('span', { class: 'lcell__mark', icon: 'fa-clock-rotate-left', title: 'Not indexed yet' }) : null,
      p.tags.length ? el('span', { class: 'lcell__tags', text: String(p.tags.length), title: p.tags.join(', ') }) : null),
    el('span', { class: 'lcell__name', text: p.name }));
  return cell;
}

/* Selection shown without redrawing a single tile. */
function paintSelection() {
  $$('#lib-grid .lcell').forEach((cell) => {
    const i = Number(cell.dataset.i);
    const p = L.shown[i];
    const on = !!p && L.sel.has(p.rel);
    cell.classList.toggle('is-picked', on);
    cell.classList.toggle('is-focus', i === L.focus);
    cell.setAttribute('aria-selected', String(on));
  });
  paintInspector();
}

function setFocus(i, { scroll = true } = {}) {
  if (!L.shown.length) return;
  L.focus = Math.max(0, Math.min(L.shown.length - 1, i));
  while (L.focus >= L.rendered) appendCells();
  if (scroll) {
    const cell = $('#lib-grid .lcell[data-i="' + L.focus + '"]');
    if (cell) cell.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }
}

function selectRange(from, to, add) {
  if (!add) L.sel.clear();
  const [a, b] = from < to ? [from, to] : [to, from];
  for (let i = Math.max(0, a); i <= b; i++) L.sel.add(L.shown[i].rel);
}

/* Click, Ctrl-click, Shift-click, the tick: the four ways every photo tool
 * agrees on. A plain click picks one; the tick and Ctrl add or take away. */
function onGridClick(ev) {
  const cell = ev.target.closest('.lcell');
  if (!cell) return;
  const i = Number(cell.dataset.i);
  const p = L.shown[i];
  if (!p) return;
  const toggle = ev.ctrlKey || ev.metaKey || ev.target.closest('.lcell__tick');
  if (ev.shiftKey && L.anchor >= 0) {
    selectRange(L.anchor, i, toggle || ev.shiftKey);
  } else if (toggle) {
    if (L.sel.has(p.rel)) L.sel.delete(p.rel); else L.sel.add(p.rel);
    L.anchor = i;
  } else {
    L.sel.clear();
    L.sel.add(p.rel);
    L.anchor = i;
  }
  setFocus(i, { scroll: false });
  paintSelection();
}

/* The lasso: drag across empty grid to take every tile it touches. Shift or
 * Ctrl adds to what was already picked; without them it replaces it. */
function wireLasso(grid) {
  grid.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || ev.pointerType === 'touch') return;
    if (ev.target.closest('.lcell') && !ev.altKey) return;
    // Everything in the grid's own coordinates: the tiles never move inside
    // it, so a pane that scrolls under the drag changes nothing but where
    // the grid is -- which is read again on every move.
    const at = (e) => {
      const r = grid.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const start = at(ev);
    const base = ev.shiftKey || ev.ctrlKey || ev.metaKey ? new Set(L.sel) : new Set();
    const g = grid.getBoundingClientRect();
    const cells = $$('.lcell', grid).map((cell) => {
      const r = cell.getBoundingClientRect();
      return { i: Number(cell.dataset.i), l: r.left - g.left, t: r.top - g.top,
               r: r.right - g.left, b: r.bottom - g.top };
    });
    const box = el('div', { class: 'lasso', 'aria-hidden': 'true' });
    let moved = false;
    const move = (e) => {
      const cur = at(e);
      const l = Math.min(start.x, cur.x);
      const t = Math.min(start.y, cur.y);
      const r = Math.max(start.x, cur.x);
      const b = Math.max(start.y, cur.y);
      if (!moved && r - l < 4 && b - t < 4) return;
      if (!moved) { moved = true; grid.append(box); }
      setLassoBox(box, l, t, r - l, b - t);
      L.sel = new Set(base);
      for (const c of cells) {
        if (c.l < r && c.r > l && c.t < b && c.b > t) L.sel.add(L.shown[c.i].rel);
      }
      paintSelection();
    };
    const up = () => {
      grid.removeEventListener('pointermove', move);
      grid.removeEventListener('pointerup', up);
      grid.removeEventListener('pointercancel', up);
      box.remove();
      // a click on the gaps between tiles lets go of everything
      if (!moved && !base.size && L.sel.size) { L.sel.clear(); paintSelection(); }
    };
    grid.setPointerCapture(ev.pointerId);
    grid.addEventListener('pointermove', move);
    grid.addEventListener('pointerup', up);
    grid.addEventListener('pointercancel', up);
  });
}

/* The lasso's box. Its geometry is the one thing here that has to change
 * continuously, and a strict CSP drops style attributes -- so it rides the
 * CSSOM, which the policy does not touch. */
function setLassoBox(box, l, t, w, h) {
  box.style.setProperty('--l', l + 'px');
  box.style.setProperty('--t', t + 'px');
  box.style.setProperty('--w', w + 'px');
  box.style.setProperty('--h', h + 'px');
}

/* ----- the inspector ------------------------------------------------------- */
function picked() {
  return [...L.sel].map((rel) => L.byRel.get(rel)).filter(Boolean);
}

function paintInspector() {
  const box = $('#lib-insp');
  if (!box) return;
  const photos = picked();
  box.replaceChildren(...(photos.length === 0 ? inspectNothing()
    : photos.length === 1 ? inspectOne(photos[0]) : inspectMany(photos)).filter(Boolean));
}

function inspectNothing() {
  return [
    el('div', { class: 'insp__block' },
      el('h2', { class: 'insp__title', text: 'Nothing selected' }),
      el('p', { class: 'insp__quiet', text: 'Click a photo, drag a lasso over several, or press Ctrl A for all ' +
        L.shown.length + ' that match. Whatever you pick can be tagged at once.' })),
    el('dl', { class: 'keys__list insp__keys' }, [
      ['Ctrl A', 'Select all that match'], ['Shift click', 'A run'], ['Ctrl click', 'Add or remove one'],
      ['Space', 'Look closer'], ['T', 'Tag the selection'], ['Ctrl Z', 'Undo a tag change'], ['Esc', 'Let go'],
    ].map(([k, what]) => [el('dt', {}, k.split(' ').map((x) => el('kbd', { text: x }))), el('dd', { text: what })])),
    undoLine(),
  ];
}

function inspectOne(p) {
  const exposure = [p.f ? 'f/' + p.f : null, p.iso ? 'ISO ' + p.iso : null, p.mm ? p.mm + ' mm' : null]
    .filter(Boolean).join(' · ');
  return [
    el('button', { type: 'button', class: 'insp__preview', title: 'Look closer (Space)', onclick: () => openLoupe(L.focus) },
      el('img', { src: thumbUrl(p.rel), alt: '' })),
    el('div', { class: 'insp__block' },
      el('h2', { class: 'insp__title insp__title--name', text: p.name }),
      el('a', { class: 'insp__album', href: pathFor({ kind: 'album', album: p.album }), 'data-go': true,
                icon: 'fa-folder', text: p.album || 'photos' })),
    el('dl', { class: 'facts' },
      fact('Taken', p.taken ? p.taken.replace('T', ' ').slice(0, 16) : (p.indexed ? 'no date in the file' : 'not indexed yet')),
      p.w ? fact('Size', p.w + ' × ' + p.h + ' · ' + bytes(p.size)) : fact('File', bytes(p.size)),
      p.camera ? fact('Camera', p.camera) : null,
      p.lens ? fact('Lens', p.lens) : null,
      exposure ? fact('Exposure', exposure) : null),
    tagEditor([p]),
    selectionActions([p]),
    el('details', {
      class: 'insp__block insp__diag',
      ontoggle: (ev) => { if (ev.currentTarget.open) loadDiagnostics(p, ev.currentTarget); },
    },
      el('summary', { class: 'insp__sub insp__summary', icon: 'fa-stethoscope', text: 'Diagnostics' }),
      el('div', { class: 'insp__diagbody' })),
    undoLine(),
  ];
}

/* What the app knows about one photo beyond what it looks like: when it was
 * indexed, whether the file changed since, the state of its thumbnail and
 * preview, which album.cfg features it, the addresses it is served at. It
 * used to be System's Lookup; a photo is looked at here. */
async function loadDiagnostics(p, box) {
  const body = box.querySelector('.insp__diagbody');
  body.replaceChildren(el('p', { class: 'insp__quiet', text: 'Asking the index…' }));
  let r;
  try {
    r = await api('/api/ops/photo?' + qs({ path: p.rel }));
  } catch (err) {
    body.replaceChildren(el('p', { class: 'insp__quiet', text: err.message }));
    return;
  }
  const stamp = (ts) => (typeof ts === 'number' ? new Date(ts * 1000).toISOString().replace('T', ' ').slice(0, 19) : '—');
  const drift = r.file_mtime != null && Math.abs(r.file_mtime - r.mtime) >= 1;
  body.replaceChildren(
    el('dl', { class: 'facts' },
      fact('Indexed', r.indexed_at || '—'),
      fact('File', r.file_exists ? (drift ? 'changed since — ' + stamp(r.file_mtime) + '; the next scan reads it again' : 'unchanged since')
        : 'gone — the row is stale', r.file_exists && !drift ? null : 'warn'),
      fact('Featured by', r.featured_by.length
        ? r.featured_by.map((f) => f.album + ' → ' + f.entry).join(', ') : 'no album.cfg'),
      ...Object.entries(r.derivatives).map(([k, info]) =>
        fact(k.charAt(0).toUpperCase() + k.slice(1), info.state, info.state === 'ok' ? null : 'warn'))),
    el('div', { class: 'insp__urls' }, r.urls.map((url) => el('code', { text: url }))));
}

/* One photo, wherever a report names it: the Library, on its folder, with
 * the photo picked and the inspector showing it. */
async function showPhoto(rel) {
  const album = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '';
  L.q = '';
  L.tags.clear();
  L.cameras.clear();
  L.years.clear();
  L.untagged = L.featured = L.unindexed = false;
  L.sel = new Set([rel]);
  await select({ kind: 'library', album });
  const i = L.shown.findIndex((x) => x.rel === rel);
  if (i < 0) { toast('That photo is not on disk any more', 'warn'); return; }
  L.anchor = i;
  setFocus(i);
  paintSelection();
}

function inspectMany(photos) {
  const albums = new Set(photos.map((p) => p.album));
  const dates = photos.map((p) => p.taken).filter(Boolean).sort();
  const span = dates.length
    ? (dates[0].slice(0, 10) === dates[dates.length - 1].slice(0, 10) ? dates[0].slice(0, 10)
      : dates[0].slice(0, 10) + ' – ' + dates[dates.length - 1].slice(0, 10))
    : '—';
  return [
    el('div', { class: 'insp__block' },
      el('h2', { class: 'insp__title', text: photos.length + ' photos selected' }),
      el('dl', { class: 'facts' },
        fact('Albums', albums.size === 1 ? [...albums][0] || 'photos' : String(albums.size)),
        fact('Taken', span),
        fact('Tagged', photos.filter((p) => p.tags.length).length + ' of ' + photos.length))),
    tagEditor(photos),
    selectionActions(photos),
    undoLine(),
  ];
}

/* ----- tagging ------------------------------------------------------------
 * The tags in the selection, each with how many of the selected photos carry
 * it: a full chip is on all of them, a dashed one on some -- click that one
 * to put it on the rest. The field adds to every picked photo; commas add
 * several at once. */
function tagEditor(photos) {
  const n = photos.length;
  const counts = new Map();
  for (const p of photos) {
    for (const t of p.tags) {
      const key = t.toLowerCase();
      const had = counts.get(key);
      counts.set(key, { name: had ? had.name : t, n: (had ? had.n : 0) + 1 });
    }
  }
  const rels = photos.map((p) => p.rel);
  const rows = [...counts.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
  const ro = READ_ONLY;
  const input = el('input', {
    type: 'text', id: 'lib-tag', class: 'tagfield', list: 'vocab-list', autocomplete: 'off',
    placeholder: n === 1 ? 'Add a tag…' : 'Add a tag to all ' + n + '…', disabled: ro,
    onkeydown: (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        const add = ev.target.value.split(',').map((t) => t.trim()).filter(Boolean);
        if (add.length) tagOp(rels, add, []);
      } else if (ev.key === 'Escape') {
        ev.target.blur();
      }
    },
  });
  const recent = L.recent.filter((t) => !rows.some((r) => r.n === n && r.name.toLowerCase() === t.toLowerCase())).slice(0, 6);
  return el('section', { class: 'insp__block insp__tags' },
    el('h3', { class: 'insp__sub', text: n === 1 ? 'Tags' : 'Tags in the selection' }),
    rows.length
      ? el('div', { class: 'chips' }, rows.map((r) => el('span', {
          class: 'chip chip--tag' + (r.n === n ? '' : ' is-partial'),
          title: r.n === n ? 'On every selected photo' : 'On ' + r.n + ' of ' + n + ' — click to put it on all',
        },
          r.n === n
            ? el('span', { class: 'chip__label', text: r.name })
            : el('button', { type: 'button', class: 'chip__label chip__fill', disabled: ro,
                             onclick: () => tagOp(rels, [r.name], []) },
                r.name, el('span', { class: 'chip__n', text: r.n + '/' + n })),
          el('button', { type: 'button', class: 'chip__x', disabled: ro, 'aria-label': 'Remove ' + r.name + ' from the selection',
                         onclick: () => tagOp(rels, [], [r.name]) }, ico('fa-xmark')))))
      : el('p', { class: 'insp__quiet', text: n === 1 ? 'No tags yet.' : 'None of them has a tag yet.' }),
    input,
    recent.length ? el('div', { class: 'insp__recent' },
      el('span', { class: 'insp__recentlabel', text: 'Recent' }),
      recent.map((t) => el('button', { type: 'button', class: 'chip chip--add', disabled: ro,
                                       onclick: () => tagOp(rels, [t], []) }, ico('fa-plus'), t))) : null);
}

async function tagOp(rels, add, remove) {
  const before = new Map(rels.map((rel) => [rel, [...(L.byRel.get(rel) || { tags: [] }).tags]]));
  let res;
  try {
    res = await api('/api/tags', { method: 'PUT', body: JSON.stringify({ photos: rels, add, remove }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  applyTagResult(res.tags);
  const changed = rels.filter((rel) => JSON.stringify(before.get(rel)) !== JSON.stringify((L.byRel.get(rel) || {}).tags));
  if (changed.length) {
    L.undo.push({ label: describeTagOp(add, remove, changed.length),
                  before: new Map(changed.map((rel) => [rel, before.get(rel)])),
                  after: new Map(changed.map((rel) => [rel, [...L.byRel.get(rel).tags]])) });
    if (L.undo.length > 50) L.undo.shift();
    L.redo = [];
  }
  for (const t of add) {
    L.recent = [t, ...L.recent.filter((x) => x.toLowerCase() !== t.toLowerCase())].slice(0, 12);
  }
  toast(changed.length ? describeTagOp(add, remove, changed.length) : 'Nothing to change', changed.length ? 'ok' : 'warn');
  afterTags();
  const field = $('#lib-tag');
  if (field && add.length) field.focus();
}

function describeTagOp(add, remove, n) {
  const photos = n + (n === 1 ? ' photo' : ' photos');
  return add.length ? 'Tagged ' + photos + ' with ' + add.join(', ')
    : 'Took ' + remove.join(', ') + ' off ' + photos;
}

function applyTagResult(map) {
  for (const [rel, tags] of Object.entries(map || {})) {
    const p = L.byRel.get(rel);
    if (p) p.tags = tags;
  }
}

/* The grid's badges, the facets' counts and the vocabulary all follow a tag
 * change. The grid is refiltered too -- tagging on "untagged" empties it as
 * you go, which is the point of that view. */
function afterTags() {
  syncVocab();
  if (onLibrary()) refilter();
}

/* Undo writes back each photo's own earlier tags: photos that had the same
 * list share one request, so forty photos that all lacked a tag are one. */
async function undoTags(redo = false) {
  const from = redo ? L.redo : L.undo;
  const step = from.pop();
  if (!step) { toast(redo ? 'Nothing to redo' : 'Nothing to undo', 'warn'); return; }
  const target = redo ? step.after : step.before;
  const groups = new Map();
  for (const [rel, tags] of target) {
    const key = JSON.stringify(tags);
    if (!groups.has(key)) groups.set(key, { tags, rels: [] });
    groups.get(key).rels.push(rel);
  }
  try {
    for (const { tags, rels } of groups.values()) {
      const res = await api('/api/tags', { method: 'PUT', body: JSON.stringify({ photos: rels, set: tags }) });
      applyTagResult(res.tags);
    }
  } catch (err) {
    from.push(step);
    toast(err.message, 'err');
    return;
  }
  (redo ? L.undo : L.redo).push(step);
  toast((redo ? 'Redone: ' : 'Undone: ') + step.label);
  afterTags();
}

function undoLine() {
  const last = L.undo[L.undo.length - 1];
  const next = L.redo[L.redo.length - 1];
  if (!last && !next) return null;
  return el('div', { class: 'insp__block insp__undo' },
    last ? el('button', { type: 'button', class: 'btn btn--ghost btn--sm', icon: 'fa-rotate-left',
                          text: 'Undo', title: last.label + ' (Ctrl Z)', onclick: () => undoTags(false) }) : null,
    next ? el('button', { type: 'button', class: 'btn btn--ghost btn--sm', icon: 'fa-rotate-right',
                          text: 'Redo', title: next.label + ' (Ctrl Shift Z)', onclick: () => undoTags(true) }) : null,
    el('span', { class: 'insp__quiet', text: last ? last.label : '' }));
}

/* ----- acting on the selection ------------------------------------------- */
function selectionActions(photos) {
  const ro = READ_ONLY;
  const one = photos.length === 1 ? photos[0] : null;
  const allFeatured = photos.every((p) => p.featured);
  return el('section', { class: 'insp__block insp__acts' },
    el('h3', { class: 'insp__sub', text: 'Do with ' + (one ? 'this photo' : 'these ' + photos.length) }),
    el('div', { class: 'insp__btns' },
      el('button', { type: 'button', class: 'btn', icon: 'fa-star', disabled: ro,
                     text: allFeatured ? 'Unfeature' : 'Feature',
                     title: 'Add to (or take off) the featured list of each photo’s own album',
                     onclick: () => setFeatured(photos, !allFeatured) }),
      one ? el('button', { type: 'button', class: 'btn', icon: 'fa-image', disabled: ro, text: 'Make cover',
                           title: 'Pin this photo as its album’s cover', onclick: () => makeCover(one) }) : null,
      one ? el('button', { type: 'button', class: 'btn', icon: 'fa-link', disabled: ro, text: 'Link…',
                           title: 'A short address on the public site for this photo',
                           onclick: () => go({ kind: 'links', draft: { target: one.rel } }) }) : null,
      el('button', { type: 'button', class: 'btn', icon: 'fa-copy', text: one ? 'Copy path' : 'Copy paths',
                     onclick: () => copyPaths(photos) })));
}

/* A photo's album is the folder it sits in; its featured list and its cover
 * are keys of that folder's album.cfg, written the way every save writes. */
function byAlbum(photos) {
  const groups = new Map();
  for (const p of photos) {
    if (!groups.has(p.album)) groups.set(p.album, []);
    groups.get(p.album).push(p);
  }
  return groups;
}

async function setFeatured(photos, on) {
  let files = 0;
  try {
    for (const [album, group] of byAlbum(photos)) {
      if (!album) continue;
      const data = await api('/api/album?path=' + encodeURIComponent(album));
      const list = (data.values.featured || []).map(strip);
      const names = group.map((p) => p.name);
      const next = on
        ? list.concat(names.filter((n) => !list.includes(n)))
        : list.filter((n) => !names.includes(n));
      if (next.length === list.length && next.every((n, i) => n === list[i])) continue;
      await api('/api/album/cfg', { method: 'PUT',
        body: JSON.stringify({ album, values: { featured: next.length ? next : null } }) });
      files++;
    }
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  for (const p of photos) if (p.album) p.featured = on;
  toast((on ? 'Featured ' : 'Unfeatured ') + photos.length + (photos.length === 1 ? ' photo' : ' photos') +
    ' · ' + files + ' album.cfg' + (files === 1 ? '' : ' files') + ' saved');
  refilter();
}

async function makeCover(p) {
  if (!p.album) { toast('A photo at the root belongs to no album', 'warn'); return; }
  try {
    await api('/api/album/cfg', { method: 'PUT', body: JSON.stringify({ album: p.album, values: { cover: p.name } }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  toast('Cover of ' + p.album + ' is now ' + p.name);
  loadTree();
}

async function copyPaths(photos) {
  const text = photos.map((p) => p.rel).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied ' + photos.length + (photos.length === 1 ? ' path' : ' paths'));
  } catch (_) {
    toast('The browser refused the clipboard', 'warn');
  }
}

/* ----- the loupe ----------------------------------------------------------- */
const loupe = { i: -1 };

function openLoupe(i) {
  if (i < 0 || !L.shown[i]) i = L.shown.findIndex((p) => L.sel.has(p.rel));
  if (i < 0) i = 0;
  if (!L.shown[i]) return;
  let dlg = $('#loupe');
  if (!dlg) {
    dlg = el('dialog', { class: 'loupe', id: 'loupe', 'aria-label': 'Photo' },
      el('img', { class: 'loupe__img', id: 'loupe-img', alt: '' }),
      el('button', { type: 'button', class: 'loupe__nav loupe__nav--prev', 'aria-label': 'Previous',
                     onclick: () => stepLoupe(-1) }, ico('fa-chevron-left')),
      el('button', { type: 'button', class: 'loupe__nav loupe__nav--next', 'aria-label': 'Next',
                     onclick: () => stepLoupe(1) }, ico('fa-chevron-right')),
      el('div', { class: 'loupe__bar', id: 'loupe-bar' }));
    dlg.addEventListener('keydown', (ev) => {
      if (ev.target.tagName === 'INPUT') return;
      if (ev.key === 'ArrowRight') { ev.preventDefault(); stepLoupe(1); }
      else if (ev.key === 'ArrowLeft') { ev.preventDefault(); stepLoupe(-1); }
      else if (ev.key === ' ') { ev.preventDefault(); dlg.close(); }
      else if (ev.key.toLowerCase() === 'x') { ev.preventDefault(); toggleInLoupe(); }
    });
    dlg.addEventListener('close', () => { setFocus(loupe.i); paintSelection(); });
    document.body.append(dlg);
  }
  loupe.i = i;
  paintLoupe();
  if (!dlg.open) dlg.showModal();
}

function stepLoupe(d) {
  const next = loupe.i + d;
  if (next < 0 || next >= L.shown.length) return;
  loupe.i = next;
  paintLoupe();
}

function toggleInLoupe() {
  const p = L.shown[loupe.i];
  if (L.sel.has(p.rel)) L.sel.delete(p.rel); else L.sel.add(p.rel);
  paintLoupe();
}

function paintLoupe() {
  const p = L.shown[loupe.i];
  $('#loupe-img').src = '/api/preview?path=' + encodeURIComponent(p.rel);
  const on = L.sel.has(p.rel);
  $('#loupe-bar').replaceChildren(...[
    el('span', { class: 'loupe__pos', text: (loupe.i + 1) + ' / ' + L.shown.length }),
    el('span', { class: 'loupe__name', text: p.rel }),
    p.tags.length ? el('span', { class: 'loupe__tags', text: p.tags.join(' · ') }) : null,
    el('button', { type: 'button', class: 'btn btn--sm' + (on ? ' is-on' : ''), icon: on ? 'fa-check' : 'fa-plus',
                   text: on ? 'Selected' : 'Select', title: 'X', onclick: toggleInLoupe }),
    el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Close', onclick: () => $('#loupe').close() },
      ico('fa-xmark'))].filter(Boolean));
  // the neighbours, fetched before they are asked for
  for (const d of [1, -1]) {
    const n = L.shown[loupe.i + d];
    if (n) new Image().src = '/api/preview?path=' + encodeURIComponent(n.rel);
  }
}

/* ----- keys ---------------------------------------------------------------- */
function gridColumns() {
  const cells = $$('#lib-grid .lcell');
  if (cells.length < 2) return 1;
  const top = cells[0].offsetTop;
  const n = cells.findIndex((c) => c.offsetTop !== top);
  return n < 0 ? cells.length : n;
}

document.addEventListener('keydown', (ev) => {
  if (!onLibrary() || !$('#lib-grid')) return;
  if (typeof gPending !== 'undefined' && gPending && Date.now() - gPending < 1200) return;
  if ($('#palette').open || $('#keys').open || ($('#loupe') && $('#loupe').open) || !$('#modal').hidden) return;
  const mod = ev.ctrlKey || ev.metaKey;
  const k = ev.key.toLowerCase();
  if (mod && k === 'z') {
    if (typing(ev.target) && ev.target.id !== 'lib-tag') return;
    ev.preventDefault();
    undoTags(ev.shiftKey);
    return;
  }
  if (mod && k === 'y') { ev.preventDefault(); undoTags(true); return; }
  if (typing(ev.target)) return;
  if (mod && k === 'a') {
    ev.preventDefault();
    L.sel = new Set(L.shown.map((p) => p.rel));
    paintSelection();
    return;
  }
  if (mod && k === 'i') {
    ev.preventDefault();
    L.sel = new Set(L.shown.filter((p) => !L.sel.has(p.rel)).map((p) => p.rel));
    paintSelection();
    return;
  }
  if (mod || ev.altKey) return;
  if (ev.key === 'Escape' && L.sel.size) { L.sel.clear(); paintSelection(); return; }
  if (k === 't' && L.sel.size) {
    ev.preventDefault();
    const field = $('#lib-tag');
    if (field) field.focus();
    return;
  }
  if (ev.key === ' ') { ev.preventDefault(); openLoupe(L.focus); return; }
  if (ev.key === 'Enter' && L.focus >= 0) { ev.preventDefault(); openLoupe(L.focus); return; }
  const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: gridColumns(), ArrowUp: -gridColumns() }[ev.key];
  if (step !== undefined) {
    ev.preventDefault();
    const from = L.focus < 0 ? 0 : L.focus;
    const to = L.focus < 0 ? 0 : Math.max(0, Math.min(L.shown.length - 1, from + step));
    setFocus(to);
    if (ev.shiftKey) {
      if (L.anchor < 0) L.anchor = from;
      selectRange(L.anchor, to, false);
    } else if (!L.sel.size || L.sel.size === 1) {
      L.sel = new Set([L.shown[to].rel]);
      L.anchor = to;
    }
    paintSelection();
  }
});

/* The grid's own listeners, attached once per drawn grid. */
document.addEventListener('click', (ev) => {
  if (ev.target.closest && ev.target.closest('#lib-grid')) onGridClick(ev);
});
document.addEventListener('dblclick', (ev) => {
  const cell = ev.target.closest && ev.target.closest('#lib-grid .lcell');
  if (cell) openLoupe(Number(cell.dataset.i));
});

/* =========================================================================
   THE TAGS PLACE  —  the vocabulary, and what to do about it
   ========================================================================= */
const tagsView = { filter: '', editing: null, report: null };

async function renderTagsPlace() {
  const pane = $('#pane');
  try {
    await loadLibrary();
  } catch (err) {
    pane.innerHTML = '';
    pane.append(el('div', { class: 'pane__empty', text: err.message }));
    return;
  }
  if (!state.sel || state.sel.kind !== 'tags') return;
  paintTagsPlace();
  // the index's side of the story, second: it walks every sidecar again
  api('/api/ops/tags').then((report) => {
    tagsView.report = report;
    if (state.sel && state.sel.kind === 'tags') paintTagsDrift();
  }).catch(() => {});
}

function tagStats() {
  const map = new Map();
  for (const p of L.photos || []) {
    for (const t of p.tags) {
      const key = t.toLowerCase();
      const had = map.get(key) || { name: t, count: 0, albums: new Set() };
      had.count++;
      had.albums.add(p.album.split('/')[0] || 'photos');
      map.set(key, had);
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/* Two tags that are probably one: the same letters once case, spaces,
 * hyphens and underscores are gone, or one typo apart. */
const squash = (s) => s.toLowerCase().replace(/[\s_\-.]+/g, '');
function oneEditApart(a, b) {
  if (Math.abs(a.length - b.length) > 1 || a === b) return false;
  let i = 0;
  let j = 0;
  let edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++;
    else if (b.length > a.length) j++;
    else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

function lookalikes(stats) {
  const out = [];
  for (let x = 0; x < stats.length; x++) {
    for (let y = x + 1; y < stats.length; y++) {
      const a = stats[x];
      const b = stats[y];
      const sa = squash(a.name);
      const sb = squash(b.name);
      if (sa === sb || (sa.length > 3 && oneEditApart(sa, sb))) out.push([a, b]);
    }
  }
  return out.slice(0, 12);
}

function paintTagsPlace() {
  const pane = $('#pane');
  const stats = tagStats();
  const tagged = (L.photos || []).filter((p) => p.tags.length).length;
  pane.innerHTML = '';
  pane.append(el('div', { class: 'pane__top' },
    pageHead('Tags', [
      el('span', { class: 'pill', icon: 'fa-tags', text: stats.length + ' tags' }),
      el('span', { class: 'pill', icon: 'fa-image', text: tagged + ' of ' + L.photos.length + ' photos tagged' }),
      el('a', { class: 'btn', href: '/library', 'data-go': true, icon: 'fa-tag', text: 'Tag untagged photos',
                onclick: (ev) => { ev.preventDefault(); clearFilters(); L.untagged = true; go({ kind: 'library' }); } }),
    ], { tip: 'every .tags sidecar under ' + state.meta.photos_dir }),
    el('div', { class: 'tbar' },
      searchbox({ type: 'search', class: 'fieldsearch', id: 'tags-filter', value: tagsView.filter,
                  placeholder: 'Filter tags…', autocomplete: 'off',
                  oninput: (ev) => { tagsView.filter = ev.target.value; paintTagRows(); } }))));

  const body = el('div', { class: 'home' });
  pane.append(body);

  const pairs = lookalikes(stats);
  if (pairs.length && !READ_ONLY) {
    body.append(wide(card('fa-code-merge', 'Probably the same tag', pairs.length + ' pair' + (pairs.length === 1 ? '' : 's'),
      el('div', { class: 'hrows' }, pairs.map(([a, b]) => {
        const [keep, drop] = a.count >= b.count ? [a, b] : [b, a];
        return el('div', { class: 'hrow merge' },
          el('span', { class: 'merge__pair' },
            el('span', { class: 'chip chip--tag' }, el('span', { class: 'chip__label', text: drop.name })),
            ico('fa-arrow-right'),
            el('span', { class: 'chip chip--tag' }, el('span', { class: 'chip__label', text: keep.name }))),
          el('span', { class: 'hrow__detail', text: drop.count + ' + ' + keep.count + ' photos' }),
          el('button', { type: 'button', class: 'btn btn--sm', icon: 'fa-code-merge', text: 'Merge',
                         onclick: () => renameTag(drop.name, keep.name) }));
      })))));
  }

  body.append(wide(card('fa-tags', 'Every tag', 'rename a tag onto another to merge them',
    el('div', { class: 'tbl-wrap' },
      el('table', { class: 'tbl tagtbl' },
        el('thead', {}, el('tr', {},
          el('th', { text: 'Tag' }), el('th', { class: 'r', text: 'Photos' }), el('th', { text: 'Where' }),
          el('th', { class: 'r', text: '' }))),
        el('tbody', { id: 'tag-rows' }))))));
  body.append(el('div', { class: 'home__wide', id: 'tags-drift' }));
  paintTagRows();
  paintTagsDrift();
}

function paintTagRows() {
  const box = $('#tag-rows');
  if (!box) return;
  const q = tagsView.filter.trim().toLowerCase();
  const stats = tagStats().filter((t) => !q || t.name.toLowerCase().includes(q));
  const peak = stats.reduce((m, t) => Math.max(m, t.count), 1);
  if (!stats.length) {
    box.replaceChildren(el('tr', {}, el('td', { colspan: '4', class: 'albums__none',
      text: q ? 'No tag matches.' : 'No photo carries a tag yet — select some in the Library and press T.' })));
    return;
  }
  box.replaceChildren(...stats.map((t) => {
    const editing = tagsView.editing === t.name;
    return el('tr', {},
      el('td', {}, editing
        ? el('input', {
            type: 'text', class: 'tagfield', value: t.name, list: 'vocab-list', 'data-fk': 'tag-edit',
            'aria-label': 'New name for ' + t.name,
            onkeydown: (ev) => {
              if (ev.key === 'Enter') { ev.preventDefault(); renameTag(t.name, ev.target.value.trim()); }
              if (ev.key === 'Escape') { tagsView.editing = null; paintTagRows(); }
            },
          })
        : el('a', { class: 'tagname', href: '/library', title: 'Show these photos in the Library',
                    onclick: (ev) => { ev.preventDefault(); openTag(t.name); } }, ico('fa-tag'), t.name)),
      el('td', { class: 'num r' },
        el('span', { class: 'tagbar' }, meter(t.count, peak)), String(t.count)),
      el('td', { class: 'tagwhere', text: [...t.albums].slice(0, 3).join(', ') + (t.albums.size > 3 ? ' +' + (t.albums.size - 3) : '') }),
      el('td', { class: 'r tagacts' }, READ_ONLY ? null : editing
        ? [el('button', { type: 'button', class: 'btn btn--sm btn--primary', text: 'Rename',
                          onclick: (ev) => renameTag(t.name, ev.target.closest('tr').querySelector('input').value.trim()) }),
           el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: 'Cancel',
                          onclick: () => { tagsView.editing = null; paintTagRows(); } })]
        : [el('button', { type: 'button', class: 'btn btn--ghost btn--sm', icon: 'fa-pen', text: 'Rename',
                          onclick: () => { tagsView.editing = t.name; paintTagRows(); const f = $('input[data-fk="tag-edit"]'); if (f) { f.focus(); f.select(); } } }),
           el('button', { type: 'button', class: 'btn btn--ghost btn--sm', icon: 'fa-trash-can', text: 'Delete',
                          onclick: () => deleteTag(t.name, t.count) })]));
  }));
}

function paintTagsDrift() {
  const box = $('#tags-drift');
  const r = tagsView.report;
  if (!box || !r || !r.drift) return;
  const rows = [
    ...r.drift.not_indexed.map((item) => homeRow(item, 'not indexed', 'in a sidecar, not in the index yet — the next scan reads it')),
    ...r.drift.indexed_without_sidecar.map((item) => homeRow(item, 'no sidecar', 'the index has tags the disk no longer does — scan that album with force')),
    ...r.orphan_sidecars.map((item) => homeRow(item, 'orphan', 'a sidecar whose photo is gone')),
  ];
  box.replaceChildren(card('fa-scale-balanced', 'Sidecars and the index',
    rows.length ? rows.length + ' difference' + (rows.length === 1 ? '' : 's') : 'in step',
    rows.length ? foldedRows('tags:drift', rows, 8, (row) => row)
      : el('p', { class: 'card__quiet', text: 'The index agrees with every sidecar on disk.' })));
}

async function renameTag(from, to) {
  if (!to || to === from) { tagsView.editing = null; paintTagRows(); return; }
  if (/[,\n]/.test(to)) { toast('A tag cannot contain a comma', 'warn'); return; }
  const merging = tagStats().some((t) => t.name.toLowerCase() === to.toLowerCase() && t.name.toLowerCase() !== from.toLowerCase());
  let res;
  try {
    res = await api('/api/tags/rename', { method: 'POST', body: JSON.stringify({ from, to }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  tagsView.editing = null;
  toast((merging ? 'Merged ' + from + ' into ' + to : 'Renamed ' + from + ' to ' + to) +
    ' · ' + res.changed + (res.changed === 1 ? ' photo' : ' photos'));
  await loadLibrary(true);
  if (state.sel && state.sel.kind === 'tags') paintTagsPlace();
}

async function deleteTag(name, count) {
  if (!confirm('Take “' + name + '” off ' + count + (count === 1 ? ' photo' : ' photos') + '? ' +
               'Every sidecar is backed up first, but the Library cannot undo this.')) return;
  let res;
  try {
    res = await api('/api/tags/delete', { method: 'POST', body: JSON.stringify({ tag: name }) });
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  toast('Deleted ' + name + ' from ' + res.changed + (res.changed === 1 ? ' photo' : ' photos'));
  L.tags.delete(name.toLowerCase());
  await loadLibrary(true);
  if (state.sel && state.sel.kind === 'tags') paintTagsPlace();
}

/* A tag, wherever tags are looked at: the Library, narrowed to it. */
function openTag(tag) {
  clearFilters();
  L.tags = new Set([tag.toLowerCase()]);
  go({ kind: 'library' });
}
