/* Gallery Configurator — client.
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
};

const READ_ONLY = document.documentElement.dataset.readOnly === '1';

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

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* empty body */ }
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

/* ----- boot ------------------------------------------------------------- */
async function boot() {
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
  $('#btn-check').addEventListener('click', checkAll);
  $('#tree-filter').addEventListener('input', renderTree);
  wireModal();
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
  if (list.children.length === 1 && filter) {
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
  if (!keepTab) state.tab = 'settings';
  if (sel.kind === 'album') {
    const parts = sel.album.split('/');
    for (let i = 1; i < parts.length; i++) state.openPaths.add(parts.slice(0, i).join('/'));
  }
  renderTree();
  $('#pane').innerHTML = '';
  $('#pane').append(el('div', { class: 'pane__empty', text: 'Loading…' }));
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

function value(key) {
  if (key in state.edits) return state.edits[key];
  const raw = state.data.values[key];
  if (raw === undefined) return null;
  const spec = state.meta.spec[key] || {};
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
  ['Look', 'Its own mark, title face and page effect.', ['icon', 'font', 'font_scale', 'effect']],
  ['Backdrop', 'What sits behind this album’s pages. Sub-albums inherit it; leave both empty for the gallery’s default.',
    ['wallpaper', 'wallpaper_mobile']],
  ['Text & stats', 'What is written under the hero.', ['tags', 'loc', 'stat', 'stats']],
];

const GALLERY_GROUPS = [
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
    ? [['settings', 'Settings'], ['raw', 'Raw file']]
    : [['settings', 'Settings'], ['photos', 'Photos & tags'], ['text', 'Description'],
       ['assets', 'Files'], ['raw', 'Raw file']];
  if (!tabs.some(([id]) => id === state.tab)) state.tab = 'settings';

  pane.append(renderHead(isGallery));
  pane.append(el('div', { class: 'tabs' }, tabs.map(([id, label]) =>
    el('button', {
      class: 'tab' + (state.tab === id ? ' is-active' : ''),
      type: 'button', text: label,
      onclick: () => { state.tab = id; renderPane(); },
    }))));

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

  return el('div', { class: 'head' },
    el('div', {
      class: 'head__crumb',
      text: isGallery ? state.meta.photos_dir + '/gallery.cfg'
                      : state.meta.photos_dir + '/' + state.sel.album + '/.album/album.cfg',
    }),
    el('h1', { class: 'head__title', text: isGallery ? 'gallery.cfg' : data.name }),
    el('div', { class: 'head__meta' }, meta));
}

/* ----- settings --------------------------------------------------------- */
/* Keys whose control is a list of rows: those get the full width. Everything
 * else is a one-line control that was sitting in a 210px column next to an
 * acre of nothing, so those tile two or three across instead. */
const WIDE_TYPES = new Set(['photo_list', 'welcome', 'album_list', 'kv_list']);

function renderSettings(groups) {
  return el('div', { class: 'tabpanel' }, groups.map(([title, blurb, keys]) =>
    el('section', { class: 'group' },
      el('header', { class: 'group__head' },
        el('h2', { class: 'group__title', text: title }),
        el('p', { class: 'group__blurb', text: blurb })),
      el('div', { class: 'fields' }, keys.map(renderField)))));
}

function renderField(key) {
  const spec = state.meta.spec[key] || {};
  const help = state.meta.help[key] || '';
  const unset = value(key) === null;

  return el('div', {
    class: 'field' + (WIDE_TYPES.has(spec.type) ? ' field--wide' : '') +
           (unset ? ' is-unset' : ''),
  },
    el('div', { class: 'field__top' },
      el('span', { class: 'field__label', text: key }),
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
    case 'number': return numberControl(key);
    case 'text': return textControl(key);
    case 'list': return listControl(key);
    case 'kv_list': return kvListControl(key);
    case 'photo': return coverControl(key);
    case 'photo_list': return photoListControl(key);
    case 'welcome': return welcomeControl(key);
    case 'album_list': return albumOrderControl(key);
    case 'asset': return assetControl(key, spec);
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

function numberControl(key) {
  const [lo, hi] = state.meta.font_scale_range;
  return el('input', {
    type: 'number', step: '0.05', min: lo, max: hi, 'data-fk': key,
    value: value(key) || '', placeholder: 'e.g. 1.25', disabled: READ_ONLY,
    oninput: (ev) => setValue(key, ev.target.value.trim() || null),
  });
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
    box.append(el('div', { class: 'field__help' },
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
};
/* every cfg key that can point at a file in .album/ — "in use" means one of
 * these names it, and a .png could be named by any of them */
const ASSET_CFG_KEYS = Object.keys(ASSET_KIND);
const VIDEO_RE = /\.(mp4|webm)$/i;

/* one asset can fill several roles — see asset_kinds() in library.py. Falls
 * back to the single `kind` so an older server response still works. */
function assetIs(asset, role) {
  return asset.kinds ? asset.kinds.includes(role) : asset.kind === role;
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
    box.append(el('div', { class: 'field__help', text:
      'No ' + kind + ' in this album’s .album/ yet — add one on the “Files” tab. Accepted: ' +
      spec.exts.join(', ') }));
  }
  if (current && files.some((f) => f.name === current)) {
    const url = '/api/asset?name=' + encodeURIComponent(current) +
                '&path=' + encodeURIComponent(state.sel.album);
    if (kind === 'icon') box.append(el('img', { class: 'iconpreview', src: url, alt: '' }));
    else if (kind === 'wallpaper') box.append(wallpaperPreview(current, url));
    else box.append(fontSample(current, url));
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

function fontSample(name, url) {
  const family = 'cfgfont_' + name.replace(/[^a-z0-9]/gi, '_');
  const sample = el('div', { class: 'fontsample', text: state.data.name || 'Sample' });
  if (window.FontFace) {
    new FontFace(family, 'url("' + url + '")').load().then((loaded) => {
      document.fonts.add(loaded);
      sample.style.fontFamily = '"' + family + '", serif';
    }).catch(() => { sample.textContent = state.data.name + '  (font could not be loaded)'; });
  }
  return sample;
}

/* ----- save bar --------------------------------------------------------- */
function renderSaveBar() {
  const changed = Object.keys(state.edits);
  return el('div', { class: 'savebar' + (changed.length ? ' is-dirty' : '') },
    el('span', {
      class: 'savebar__note',
      text: READ_ONLY ? 'readonly mount · saving disabled'
        : changed.length ? changed.length + ' unsaved · ' + changed.join(' · ')
        : 'idle · comments and untouched keys survive every save',
    }),
    changed.length ? el('button', {
      class: 'btn', type: 'button', text: 'Discard',
      onclick: () => { state.edits = {}; renderPane(); },
    }) : null,
    el('button', {
      class: 'btn btn--primary', type: 'button', text: 'Save',
      disabled: READ_ONLY || !changed.length, onclick: saveSettings,
    }));
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
    el('div', { class: 'field__help u-mb' },
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
  panel.append(el('div', { class: 'field__help u-mb' },
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
    body.append(el('p', { class: 'field__help', text:
      'Read-only — the configurator never rewrites a photo file.' }));

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
    el('div', { class: 'field__help u-mb' },
      'Markdown shown under the album hero, one file per language. Saving an empty ' +
      'editor deletes that language’s file.'),
    el('div', { class: 'desc-langs' }, state.meta.langs.map((lang) =>
      el('button', {
        class: 'btn' + (state.descLang === lang ? ' btn--primary' : ''),
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
  const usable = (state.data.assets || []).filter(
    (a) => ['icon', 'font', 'wallpaper'].some((k) => assetIs(a, k)));
  const list = el('div', { class: 'assets' }, usable.length
    ? usable.map(renderAssetRow)
    : el('div', { class: 'empty-note', text: 'Nothing in this album’s .album/ folder yet.' }));

  const input = el('input', {
    type: 'file', class: 'u-hidden',
    onchange: (ev) => { if (ev.target.files.length) upload(ev.target.files[0]); },
  });
  const drop = el('div', {
    class: 'dropzone',
    text: READ_ONLY ? 'Read-only — uploads are disabled.'
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
    form.append('path', state.sel.album);
    form.append('file', file);
    try {
      const res = await fetch('/api/asset', { method: 'POST', body: form });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || res.statusText);
      state.data.assets = payload.assets;
      renderPane();
      toast('Uploaded ' + payload.name + ' — now pick it as the album’s ' + payload.kind);
    } catch (err) {
      toast('Upload failed: ' + err.message, 'err');
    }
  }

  return el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help u-mb' },
      'Files here live in ' + state.sel.album + '/.album/ next to the album.cfg. ' +
      'Uploading one does not select it — point `icon`, `font` or one of the ' +
      'wallpaper keys at it on the Settings tab.'),
    list, el('div', { class: 'u-gap' }), drop, input);
}

/* "in use" means: some cfg key points at this file. A wallpaper can be
 * claimed by either of the two keys, so kind alone doesn't answer it. */
function assetInUse(asset) {
  return ASSET_CFG_KEYS.some((k) => value(k) === asset.name);
}

function renderAssetRow(asset) {
  const url = '/api/asset?name=' + encodeURIComponent(asset.name) +
              '&path=' + encodeURIComponent(state.sel.album);
  return el('div', { class: 'asset' },
    el('div', { class: 'asset__icon' },
      VIDEO_RE.test(asset.name) ? '▶'
        : (assetIs(asset, 'icon') || assetIs(asset, 'wallpaper'))
          ? el('img', { src: url, alt: '' })
          : 'Aa'),
    el('span', { class: 'asset__name', text: asset.name }),
    assetInUse(asset) ? el('span', { class: 'pill pill--ok', text: 'in use' }) : null,
    el('span', { class: 'asset__meta', text: bytes(asset.size) }),
    READ_ONLY ? null : el('button', {
      class: 'btn btn--sm btn--ghost btn--danger', type: 'button', text: 'Delete',
      onclick: async () => {
        if (!confirm('Delete ' + asset.name + ' from .album/?')) return;
        try {
          const payload = await api('/api/asset?name=' + encodeURIComponent(asset.name) +
            '&path=' + encodeURIComponent(state.sel.album), { method: 'DELETE' });
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
