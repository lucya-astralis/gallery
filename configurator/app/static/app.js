/* Gallery Configurator — client.
 *
 * One page, three moving parts: a tree of albums on the left, a tabbed editor
 * on the right, and a photo picker modal shared by every key that points at
 * photos. Edits collect into `state.edits` and only reach disk on Save, so a
 * half-finished list never lands in a cfg the gallery is reading live.
 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  meta: null,
  tree: null,
  issuesByAlbum: {},
  sel: null,        // {kind: 'gallery'|'album', album: string}
  data: null,       // last payload for the selection
  edits: {},        // key -> value staged for the next save
  tab: 'settings',
  descLang: 'en',
  photoCache: new Map(),
};

const READ_ONLY = document.documentElement.dataset.readOnly === '1';

/* ----- tiny helpers ----------------------------------------------------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
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

const bytes = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB'
                   : n > 1024 ? Math.round(n / 1024) + ' KB' : n + ' B';

const thumbUrl = (rel, size) =>
  '/api/thumb?size=' + (size || 160) + '&path=' + encodeURIComponent(rel);

/* Split a photo path into folder + filename for two-tone list labels. */
function splitPath(value) {
  const i = value.lastIndexOf('/');
  return i < 0 ? ['', value] : [value.slice(0, i + 1), value.slice(i + 1)];
}

/* ----- boot ------------------------------------------------------------- */
async function boot() {
  try {
    state.meta = await api('/api/meta');
    await loadTree();
    select({ kind: 'gallery' });
  } catch (err) {
    $('#pane').innerHTML = '';
    $('#pane').append(el('div', { class: 'pane__empty', text: 'Cannot reach the backend: ' + err.message }));
  }
  $('#btn-reload').addEventListener('click', async () => {
    await loadTree();
    if (state.sel) select(state.sel, true);
    toast('Tree reloaded');
  });
  $('#btn-check').addEventListener('click', checkAll);
  $('#tree-filter').addEventListener('input', renderTree);
  wireModal();
}

async function loadTree() {
  const payload = await api('/api/tree');
  state.tree = payload.root;
  renderTree();
}

/* ----- tree ------------------------------------------------------------- */
function renderTree() {
  const list = $('#tree');
  const filter = $('#tree-filter').value.trim().toLowerCase();
  list.innerHTML = '';

  const galleryRow = el('div', {
    class: 'tree__row tree__row--gallery' +
      (state.sel && state.sel.kind === 'gallery' ? ' is-active' : ''),
    onclick: () => select({ kind: 'gallery' }),
  },
    el('span', { class: 'tree__twisty is-leaf' }),
    el('span', { class: 'tree__name', text: 'gallery.cfg' }),
    el('span', { class: 'tree__count', text: 'root' }));
  list.append(el('li', {}, galleryRow));

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
  const open = filter ? true : isOpen(node.path);
  const errored = (state.issuesByAlbum[node.path] || 0) > 0;

  const twisty = el('span', {
    class: 'tree__twisty' + (kids.length ? '' : ' is-leaf') + (open ? ' is-open' : ''),
    text: '▶',
    onclick: (ev) => { ev.stopPropagation(); toggleOpen(node.path); renderTree(); },
  });

  const row = el('div', {
    class: 'tree__row' + (active ? ' is-active' : ''),
    style: 'padding-left:' + (depth * 12) + 'px',
    title: node.path,
    onclick: () => select({ kind: 'album', album: node.path }),
  },
    twisty,
    el('span', {
      class: 'tree__dot' + (errored ? ' has-err' : node.has_cfg ? ' has-cfg' : ''),
      title: errored ? 'has config issues' : node.has_cfg ? 'has an album.cfg' : '',
    }),
    el('span', { class: 'tree__name', text: node.name }),
    el('span', { class: 'tree__count', text: String(node.total_photos || '') }));

  const item = el('li', {}, row);
  if (kids.length && open) item.append(el('ul', {}, kids));
  return item;
}

const openPaths = new Set();
const isOpen = (path) => openPaths.has(path);
function toggleOpen(path) {
  if (openPaths.has(path)) openPaths.delete(path);
  else openPaths.add(path);
}

/* ----- selection -------------------------------------------------------- */
async function select(sel, keepTab = false) {
  if (!keepTab && state.sel && dirty() &&
      !confirm('Discard the unsaved changes on this page?')) return;
  state.sel = sel;
  state.edits = {};
  if (!keepTab) state.tab = 'settings';
  // Keep the whole path to the selected album expanded.
  if (sel.kind === 'album') {
    const parts = sel.album.split('/');
    for (let i = 1; i < parts.length; i++) openPaths.add(parts.slice(0, i).join('/'));
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

/* Current value of a key: staged edit first, then what is on disk. */
function value(key) {
  if (key in state.edits) return state.edits[key];
  const raw = state.data.values[key];
  if (raw === undefined) return null;
  const spec = state.meta.spec[key] || {};
  // Every type whose on-disk form is a list of entries rather than one scalar.
  const listy = ['photo_list', 'list', 'kv_list', 'welcome', 'album_list'].includes(spec.type);
  if (listy) return raw;
  // `joined` keys (loc) are one logical line the parser happened to split
  // on commas; the gallery rejoins them, so the editor shows them rejoined.
  if (spec.joined) return raw.join(', ');
  return raw.length ? raw[0] : '';
}

function setValue(key, next) {
  state.edits[key] = next;
  renderPane();
}

/* Re-rendering the pane on every change would drop the caret out of whatever
 * field triggered it. Inputs carry a stable `data-fk`, so focus and selection
 * can be put back on the freshly built node. */
function focusKey(node) {
  return node && node.dataset ? node.dataset.fk : null;
}

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
function renderPane() {
  const pane = $('#pane');
  const isGallery = state.sel.kind === 'gallery';
  const active = document.activeElement;
  const fk = focusKey(active);
  const selStart = fk && active.selectionStart !== undefined ? active.selectionStart : null;
  const selEnd = fk && active.selectionEnd !== undefined ? active.selectionEnd : null;
  const scrollTop = pane.scrollTop;
  pane.innerHTML = '';

  const tabs = isGallery
    ? [['settings', 'Settings'], ['raw', 'Raw file']]
    : [['settings', 'Settings'], ['photos', 'Photos'], ['text', 'Description'],
       ['assets', 'Icon & font'], ['raw', 'Raw file']];
  if (!tabs.some(([id]) => id === state.tab)) state.tab = 'settings';

  pane.append(renderHead(isGallery));
  pane.append(el('div', { class: 'tabs' }, tabs.map(([id, label]) =>
    el('button', {
      class: 'tab' + (state.tab === id ? ' is-active' : ''),
      type: 'button',
      text: label,
      onclick: () => { state.tab = id; renderPane(); },
    }))));

  if (state.data.issues && state.data.issues.length) {
    pane.append(el('div', { class: 'issues' }, state.data.issues.map(renderIssue)));
  }

  if (state.tab === 'settings') pane.append(renderSettings(isGallery));
  else if (state.tab === 'raw') pane.append(renderRaw());
  else if (state.tab === 'photos') pane.append(renderPhotosTab());
  else if (state.tab === 'text') pane.append(renderDescriptions());
  else if (state.tab === 'assets') pane.append(renderAssets());

  if (state.tab === 'settings') pane.append(renderSaveBar());

  pane.scrollTop = scrollTop;
  restoreFocus(fk, selStart, selEnd);
}

function renderHead(isGallery) {
  const data = state.data;
  const meta = [];
  meta.push(el('span', {
    class: 'pill ' + (data.exists ? 'pill--ok' : ''),
    text: data.exists ? 'cfg present' : 'no cfg yet',
  }));
  if (!isGallery) {
    meta.push(el('span', { class: 'pill', text: data.own_count + ' here' }));
    if (data.photo_count !== data.own_count) {
      meta.push(el('span', { class: 'pill', text: data.photo_count + ' subtree' }));
    }
  }
  const errors = (data.issues || []).filter((i) => i.level === 'error').length;
  const warns = (data.issues || []).filter((i) => i.level === 'warn').length;
  if (errors) meta.push(el('span', { class: 'pill pill--err', text: errors + ' error' + (errors > 1 ? 's' : '') }));
  if (warns) meta.push(el('span', { class: 'pill pill--warn', text: warns + ' warning' + (warns > 1 ? 's' : '') }));

  return el('div', { class: 'head' },
    el('div', {
      class: 'head__crumb',
      text: isGallery ? state.meta.photos_dir + '/gallery.cfg'
                      : state.meta.photos_dir + '/' + state.sel.album + '/.album/album.cfg',
    }),
    el('h1', { class: 'head__title', text: isGallery ? 'gallery.cfg' : data.name }),
    el('div', { class: 'head__meta' }, meta));
}

function renderIssue(issue) {
  return el('div', { class: 'issue issue--' + (issue.level === 'error' ? 'error' : 'warn') },
    el('span', { class: 'issue__key', text: issue.key }),
    el('span', { class: 'issue__detail', text: issue.detail }));
}

/* ----- settings tab ----------------------------------------------------- */
function renderSettings(isGallery) {
  const keys = isGallery ? state.meta.gallery_keys : state.meta.album_keys;
  return el('div', { class: 'tabpanel' }, keys.map(renderField));
}

function renderField(key) {
  const spec = state.meta.spec[key] || {};
  const help = state.meta.help[key] || '';
  const unset = value(key) === null;

  const label = el('div', { class: 'field__label' },
    key,
    el('small', { text: spec.type }));

  const control = el('div', { class: 'field__control' });
  control.append(buildControl(key, spec));
  if (!unset && !READ_ONLY) {
    control.append(el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm btn--ghost btn--danger',
        type: 'button',
        text: 'Unset',
        title: 'remove this line from the file',
        onclick: () => setValue(key, null),
      })));
  }

  return el('div', { class: 'field' + (unset ? ' is-unset' : '') },
    label, control,
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
      type: 'button',
      class: current === val ? 'is-on' : '',
      text: label,
      disabled: READ_ONLY,
      onclick: () => onPick(val),
    })));
}

function boolControl(key) {
  const raw = value(key);
  const current = raw === null ? '' : (String(raw).toLowerCase() === 'true' || raw === true ? 'true' : 'false');
  return toggle([['', 'not set'], ['true', 'true'], ['false', 'false']], current,
    (val) => setValue(key, val === '' ? null : val));
}

function statsControl(key) {
  const raw = value(key);
  const current = raw === null ? '' : 'off';
  return toggle([['', 'stats block shown'], ['off', 'stats block hidden']], current,
    (val) => setValue(key, val === '' ? null : 'off'));
}

function choiceControl(key, choices) {
  const current = value(key);
  const select = el('select', {
    disabled: READ_ONLY,
    onchange: (ev) => setValue(key, ev.target.value === '' ? null : ev.target.value),
  },
    el('option', { value: '', text: '(not set — gallery default)' }),
    choices.map((c) => el('option', { value: c, text: c, selected: current === c })));
  if (current && !choices.includes(current)) {
    select.append(el('option', { value: current, text: current + '  (not a known value)', selected: true }));
  }
  return select;
}

function numberControl(key) {
  const [lo, hi] = state.meta.font_scale_range;
  return el('input', {
    type: 'number', step: '0.05', min: lo, max: hi, 'data-fk': key,
    value: value(key) || '', placeholder: 'e.g. 1.25', disabled: READ_ONLY,
    oninput: (ev) => setValue(key, ev.target.value.trim() || null),
  });
}

function textControl(key) {
  return el('input', {
    type: 'text', value: value(key) || '', disabled: READ_ONLY,
    'data-fk': key, placeholder: key === 'loc' ? 'City, Country' : '',
    oninput: (ev) => setValue(key, ev.target.value || null),
  });
}

/* A plain list of freeform strings, one per row (tags, stat). */
function listControl(key) {
  const items = value(key) || [];
  const box = el('div', { class: 'picklist' });
  items.forEach((item, index) => {
    box.append(el('div', { class: 'pickitem' },
      el('input', {
        type: 'text', value: item, disabled: READ_ONLY,
        'data-fk': key + ':' + index,
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
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm', type: 'button',
        text: key === 'stat' ? '+ Add stat line' : '+ Add entry',
        onclick: () => setValue(key, items.concat([key === 'stat' ? 'Label: Value' : 'new'])),
      })));
  }
  return box;
}

/* An album's custom attributes (`stat`). The gallery stores each as one
 * "Label: Value" line and splits on the first colon, so the editor works in
 * those two halves directly — no way left to write a line that has no colon,
 * which is the one way to make the gallery drop it silently. */
function kvListControl(key) {
  const items = value(key) || [];
  const pairs = items.map((item) => {
    const at = String(item).indexOf(':');
    return at < 0 ? [String(item).trim(), ''] : [
      String(item).slice(0, at).trim(),
      String(item).slice(at + 1).trim(),
    ];
  });
  // A blank row is kept as an empty entry so the user can actually type into
  // it; the server drops empty entries on save, so it never reaches the file.
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
        'data-fk': key + ':' + index + ':k',
        disabled: READ_ONLY, oninput: edit(0),
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
      'A value with a comma is split into two entries by the cfg parser — the gallery will not show it whole.'));
  }
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' },
      el('button', {
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
  const resolved = current ? album + '/' + String(current).replace(/^\/+/, '') : null;
  const box = el('div', { class: 'cover-preview' });
  if (current) {
    box.append(el('img', {
      src: thumbUrl(resolved, 220), alt: '', loading: 'lazy',
      onerror: (ev) => { ev.target.style.opacity = '.25'; },
    }));
  }
  box.append(el('div', { class: 'field__control' },
    el('div', { class: 'pickitem__label', html: current
      ? '<b>' + escapeHtml(splitPath(current)[1]) + '</b> <span>' + escapeHtml(splitPath(current)[0]) + '</span>'
      : '<span>auto — newest photo in the album</span>' }),
    READ_ONLY ? null : el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm', type: 'button', text: current ? 'Change…' : 'Pick a cover…',
        onclick: () => openPicker({
          title: 'Cover photo', album, single: true, picked: current ? [current] : [],
          onApply: (list) => setValue(key, list[0] || null),
        }),
      }))));
  return box;
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ----- ordered photo lists (featured / order) --------------------------- */
function photoListControl(key) {
  const items = value(key) || [];
  const album = state.sel.album;
  const box = el('div', { class: 'picklist' });

  items.forEach((item, index) => {
    const rel = album + '/' + String(item).replace(/^\/+/, '');
    const [folder, name] = splitPath(String(item).replace(/^\/+/, ''));
    box.append(sortableRow({
      index, items, key,
      thumb: thumbUrl(rel, 96),
      html: '<b>' + escapeHtml(name) + '</b> <span>' + escapeHtml(folder) + '</span>',
    }));
  });

  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'No photos listed.' }));
  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm', type: 'button', text: '+ Pick photos…',
        onclick: () => openPicker({
          title: key === 'featured' ? 'Featured photos' : 'Curated photo order',
          album, picked: items.map((i) => String(i).replace(/^\/+/, '')),
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

/* A drag-reorderable row. Reordering rewrites the whole list, which is what
 * `featured` / `order` / `album_order` mean — the file order is the display
 * order, so there is nothing else to store. */
function sortableRow({ index, items, key, thumb, html, group }) {
  const row = el('div', {
    class: 'pickitem' + (group ? ' pickitem--group' : ''),
    draggable: !READ_ONLY,
    ondragstart: (ev) => {
      ev.dataTransfer.setData('text/plain', String(index));
      row.classList.add('is-dragging');
    },
    ondragend: () => row.classList.remove('is-dragging'),
    ondragover: (ev) => { ev.preventDefault(); row.classList.add('is-over'); },
    ondragleave: () => row.classList.remove('is-over'),
    ondrop: (ev) => {
      ev.preventDefault();
      row.classList.remove('is-over');
      const from = Number(ev.dataTransfer.getData('text/plain'));
      if (Number.isNaN(from) || from === index) return;
      const next = items.slice();
      next.splice(index, 0, next.splice(from, 1)[0]);
      setValue(key, next);
    },
  });
  row.append(el('span', { class: 'pickitem__grip', text: '≡' }));
  if (thumb) {
    row.append(el('img', {
      class: 'pickitem__thumb', src: thumb, alt: '', loading: 'lazy',
      onerror: (ev) => { ev.target.style.opacity = '.2'; },
    }));
  }
  row.append(el('span', { class: 'pickitem__label', html }));
  if (!READ_ONLY) {
    row.append(el('button', {
      class: 'btn btn--sm btn--ghost btn--icon', type: 'button', text: '✕', title: 'remove',
      onclick: () => setValue(key, items.filter((_, i) => i !== index)),
    }));
  }
  return row;
}

/* ----- welcome (gallery-wide photo paths, or a keyword) ----------------- */
function welcomeControl(key) {
  const items = value(key) || [];
  const keywords = state.meta.welcome_keywords;
  const isKeyword = items.length === 1 && keywords.includes(String(items[0]).toLowerCase());
  const box = el('div', { class: 'picklist' });

  box.append(el('div', { class: 'row' },
    el('select', {
      disabled: READ_ONLY,
      onchange: (ev) => {
        const val = ev.target.value;
        setValue(key, val === 'list' ? (isKeyword ? [] : items) : val ? [val] : null);
      },
    },
      el('option', { value: '', text: '(not set)', selected: !items.length }),
      el('option', { value: 'list', text: 'hand-picked list', selected: items.length > 0 && !isKeyword }),
      keywords.map((kw) => el('option', {
        value: kw, text: kw + ' (keyword)', selected: isKeyword && items[0].toLowerCase() === kw,
      })))));

  if (isKeyword || !items.length) return box;

  items.forEach((item, index) => {
    const [folder, name] = splitPath(String(item).replace(/^\/+/, ''));
    box.append(sortableRow({
      index, items, key,
      thumb: thumbUrl(String(item).replace(/^\/+/, ''), 96),
      html: '<b>' + escapeHtml(name) + '</b> <span>' + escapeHtml(folder) + '</span>',
    }));
  });

  if (!READ_ONLY) {
    box.append(el('div', { class: 'row' },
      el('button', {
        class: 'btn btn--sm', type: 'button', text: '+ Pick photos…',
        onclick: () => openPicker({
          title: key.replace('_', ' '), album: '', gallery: true,
          picked: items.map((i) => String(i).replace(/^\/+/, '')),
          onApply: (list) => setValue(key, list),
        }),
      })));
  }
  return box;
}

/* ----- album_order (albums plus #group headers) ------------------------- */
function albumOrderControl(key) {
  const items = value(key) || [];
  const known = state.data.albums || [];
  const box = el('div', { class: 'picklist' });

  items.forEach((item, index) => {
    const str = String(item);
    const group = str.startsWith('#');
    const missing = !group && !known.some((a) =>
      a.toLowerCase() === str.toLowerCase().replace(/^\/+/, '') ||
      a.toLowerCase().replace(/^_/, '') === str.toLowerCase().replace(/^[/_]+/, ''));
    const row = sortableRow({
      index, items, key, group,
      html: group
        ? '<b>' + escapeHtml(str.slice(1)) + '</b> <span>group header</span>'
        : '<b>' + escapeHtml(str) + '</b>' + (missing ? ' <span>no such folder</span>' : ''),
    });
    if (missing) row.classList.add('pickitem--missing');
    box.append(row);
  });

  if (!items.length) box.append(el('div', { class: 'empty-note', text: 'No curated order set.' }));
  if (READ_ONLY) return box;

  const unused = known.filter((a) => !a.includes('/') &&
    !items.some((i) => String(i).toLowerCase() === a.toLowerCase()));

  box.append(el('div', { class: 'row' },
    el('select', {
      onchange: (ev) => {
        if (!ev.target.value) return;
        setValue(key, items.concat([ev.target.value]));
      },
    },
      el('option', { value: '', text: unused.length ? '+ Add album…' : '(every top-level album is listed)' }),
      unused.map((a) => el('option', { value: a, text: a }))),
    el('button', {
      class: 'btn btn--sm', type: 'button', text: '+ Add group header',
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

/* ----- icon / font ------------------------------------------------------ */
function assetControl(key, spec) {
  const current = value(key);
  const kind = key === 'icon' ? 'icon' : 'font';
  const files = (state.data.assets || []).filter((a) => a.kind === kind);
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
      'No ' + kind + ' in this album’s .album/ folder yet — add one on the “Icon & font” tab. Accepted: ' + spec.exts.join(', ') }));
  }
  if (current && kind === 'icon' && files.some((f) => f.name === current)) {
    box.append(el('img', {
      class: 'iconpreview', alt: '',
      src: '/api/asset?name=' + encodeURIComponent(current) + '&path=' + encodeURIComponent(state.sel.album),
    }));
  }
  if (current && kind === 'font' && files.some((f) => f.name === current)) {
    box.append(fontSample(current));
  }
  return box;
}

/* Load the album's own title face and show the album name set in it, the way
 * the gallery renders its hero. */
function fontSample(name) {
  const family = 'cfgfont_' + name.replace(/[^a-z0-9]/gi, '_');
  const url = '/api/asset?name=' + encodeURIComponent(name) +
              '&path=' + encodeURIComponent(state.sel.album);
  const sample = el('div', { class: 'fontsample', text: state.data.name || 'Sample' });
  if (window.FontFace) {
    const face = new FontFace(family, 'url("' + url + '")');
    face.load().then((loaded) => {
      document.fonts.add(loaded);
      sample.style.fontFamily = '"' + family + '", serif';
    }).catch(() => { sample.textContent = state.data.name + '  (font could not be loaded)'; });
  }
  return sample;
}

/* ----- save bar --------------------------------------------------------- */
function renderSaveBar() {
  const changed = Object.keys(state.edits);
  const bar = el('div', { class: 'savebar' + (changed.length ? ' is-dirty' : '') },
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
      disabled: READ_ONLY || !changed.length,
      onclick: saveSettings,
    }));
  return bar;
}

async function saveSettings() {
  const body = JSON.stringify({ album: state.sel.album || '', values: state.edits });
  const url = state.sel.kind === 'gallery' ? '/api/gallery/cfg' : '/api/album/cfg';
  try {
    const payload = await api(url, { method: 'PUT', body });
    Object.assign(state.data, {
      values: payload.values, raw: payload.raw, issues: payload.issues, exists: true,
    });
    state.edits = {};
    renderPane();
    await refreshIssueDots();
    toast('Saved');
  } catch (err) {
    toast('Save failed: ' + err.message, 'err');
  }
}

/* ----- raw tab ---------------------------------------------------------- */
function renderRaw() {
  const area = el('textarea', {
    class: 'u-tall', spellcheck: 'false', disabled: READ_ONLY,
  });
  area.value = state.data.raw || '';
  const panel = el('div', { class: 'tabpanel' },
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
          const url = state.sel.kind === 'gallery' ? '/api/gallery/raw' : '/api/album/raw';
          try {
            const payload = await api(url, {
              method: 'PUT',
              body: JSON.stringify({ album: state.sel.album || '', raw: area.value }),
            });
            Object.assign(state.data, {
              values: payload.values, raw: payload.raw, issues: payload.issues, exists: true,
            });
            state.edits = {};
            renderPane();
            await refreshIssueDots();
            toast('File written');
          } catch (err) {
            toast('Save failed: ' + err.message, 'err');
          }
        },
      })));
  return panel;
}

/* ----- photos tab ------------------------------------------------------- */
function renderPhotosTab() {
  const panel = el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help u-mb' },
      'Every photo in this album’s subtree. Photos a config key points at are marked.'),
    el('div', { class: 'grid', id: 'photos-grid' }, el('div', { class: 'empty-note', text: 'Loading…' })));
  loadPhotos(state.sel.album).then((photos) => {
    const grid = $('#photos-grid', panel);
    if (!grid) return;
    grid.innerHTML = '';
    if (!photos.length) {
      grid.append(el('div', { class: 'empty-note', text: 'No photos in this folder.' }));
      return;
    }
    const cover = value('cover');
    const featured = (value('featured') || []).map((v) => String(v).replace(/^\/+/, ''));
    const order = (value('order') || []).map((v) => String(v).replace(/^\/+/, ''));
    for (const photo of photos) {
      const roles = [];
      if (cover && String(cover).replace(/^\/+/, '') === photo.sub) roles.push('cover');
      if (featured.includes(photo.sub)) roles.push('featured');
      if (order.includes(photo.sub)) roles.push('#' + (order.indexOf(photo.sub) + 1));
      grid.append(el('div', {
        class: 'cell' + (roles.length ? ' is-picked' : ''),
        title: photo.sub + '\n' + bytes(photo.size),
      },
        el('img', { src: thumbUrl(photo.rel, 200), alt: '', loading: 'lazy' }),
        roles.length ? el('span', { class: 'cell__order', text: roles.join(' · ') }) : null,
        el('span', { class: 'cell__name', text: photo.name })));
    }
  });
  return panel;
}

async function loadPhotos(album) {
  const cacheKey = album || '::root';
  if (state.photoCache.has(cacheKey)) return state.photoCache.get(cacheKey);
  const payload = await api('/api/photos?recursive=1&path=' + encodeURIComponent(album));
  state.photoCache.set(cacheKey, payload.photos);
  return payload.photos;
}

/* ----- description tab -------------------------------------------------- */
function renderDescriptions() {
  const langs = state.meta.langs;
  const area = el('textarea', { class: 'u-tall', spellcheck: 'false', disabled: READ_ONLY });
  area.value = state.data.descriptions[state.descLang] || '';

  const switcher = el('div', { class: 'desc-langs' }, langs.map((lang) =>
    el('button', {
      class: 'btn' + (state.descLang === lang ? ' btn--primary' : ''),
      type: 'button',
      text: 'album_' + lang + '.md' + (state.data.descriptions[lang] ? '' : '  (empty)'),
      onclick: () => {
        state.data.descriptions[state.descLang] = area.value;
        state.descLang = lang;
        renderPane();
      },
    })));

  return el('div', { class: 'tabpanel' },
    el('div', { class: 'field__help u-mb' },
      'Markdown shown under the album hero, one file per language. Saving an empty ' +
      'editor deletes that language’s file.'),
    switcher, area,
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
  const usable = (state.data.assets || []).filter((a) => a.kind === 'icon' || a.kind === 'font');
  const list = el('div', { class: 'assets' }, usable.length
    ? usable.map(renderAssetRow)
    : el('div', { class: 'empty-note', text: 'Nothing in this album’s .album/ folder yet.' }));

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
  const input = el('input', {
    type: 'file', style: 'display:none',
    onchange: (ev) => { if (ev.target.files.length) upload(ev.target.files[0]); },
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
      'Uploading one does not select it — set `icon` or `font` on the Settings tab.'),
    list, el('div', { class: 'u-gap' }), drop, input);
}

function renderAssetRow(asset) {
  const url = '/api/asset?name=' + encodeURIComponent(asset.name) +
              '&path=' + encodeURIComponent(state.sel.album);
  const inUse = value(asset.kind) === asset.name;
  return el('div', { class: 'asset' },
    el('div', { class: 'asset__icon' }, asset.kind === 'icon'
      ? el('img', { src: url, alt: '' })
      : 'Aa'),
    el('span', { class: 'asset__name', text: asset.name }),
    inUse ? el('span', { class: 'pill pill--ok', text: 'in use' }) : null,
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
const picker = { picked: [], photos: [], single: false, onApply: null, gallery: false };

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

async function openPicker({ title, album, picked, single, onApply, gallery }) {
  picker.picked = (picked || []).slice();
  picker.single = !!single;
  picker.onApply = onApply;
  picker.gallery = !!gallery;
  $('#modal-title').textContent = title;
  $('#modal-filter').value = '';
  $('#modal-body').innerHTML = '';
  $('#modal-body').append(el('div', { class: 'empty-note', text: 'Loading photos…' }));
  $('#modal').hidden = false;
  picker.photos = await loadPhotos(gallery ? '' : album);
  drawPicker();
}

function drawPicker() {
  const filter = $('#modal-filter').value.trim().toLowerCase();
  // Picked entries are keyed the way the cfg stores them: relative to the
  // album for album.cfg, relative to the photos root for gallery.cfg.
  const keyOf = (photo) => picker.gallery ? photo.rel : photo.sub;
  const shown = filter
    ? picker.photos.filter((p) => keyOf(p).toLowerCase().includes(filter))
    : picker.photos;

  const grid = el('div', { class: 'grid' });
  for (const photo of shown.slice(0, 1200)) {
    const key = keyOf(photo);
    const at = picker.picked.indexOf(key);
    grid.append(el('div', {
      class: 'cell' + (at >= 0 ? ' is-picked' : ''),
      title: key,
      onclick: () => {
        const idx = picker.picked.indexOf(key);
        if (picker.single) picker.picked = idx >= 0 ? [] : [key];
        else if (idx >= 0) picker.picked.splice(idx, 1);
        else picker.picked.push(key);
        drawPicker();
      },
    },
      el('img', { src: thumbUrl(photo.rel, 200), alt: '', loading: 'lazy' }),
      at >= 0 && !picker.single ? el('span', { class: 'cell__order', text: String(at + 1) }) : null,
      at >= 0 && picker.single ? el('span', { class: 'cell__order', text: '✓' }) : null,
      el('span', { class: 'cell__name', text: photo.name })));
  }

  const body = $('#modal-body');
  body.innerHTML = '';
  body.append(shown.length ? grid : el('div', { class: 'empty-note', text: 'Nothing matches.' }));
  if (shown.length > 1200) {
    body.append(el('div', { class: 'empty-note', text:
      'Showing the first 1200 of ' + shown.length + ' — narrow the filter to reach the rest.' }));
  }
  $('#modal-count').textContent = picker.single
    ? (picker.picked.length ? picker.picked[0] : 'nothing selected')
    : picker.picked.length + ' selected (click order becomes list order)';
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
  state.issuesByAlbum = {};
  for (const issue of payload.issues) {
    if (issue.scope === 'album') {
      state.issuesByAlbum[issue.album] = (state.issuesByAlbum[issue.album] || 0) + 1;
    }
  }
  renderTree();

  const pane = $('#pane');
  pane.innerHTML = '';
  pane.append(el('div', { class: 'head' },
    el('h1', { class: 'head__title', text: 'Config check' }),
    el('div', { class: 'head__meta' },
      el('span', {
        class: 'pill ' + (payload.errors ? 'pill--err' : 'pill--ok'),
        text: payload.errors + ' errors',
      }),
      el('span', {
        class: 'pill ' + (payload.warnings ? 'pill--warn' : ''),
        text: payload.warnings + ' warnings',
      }),
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

/* Refresh the per-album error dots after a save, quietly. */
async function refreshIssueDots() {
  try {
    const payload = await api('/api/validate');
    state.issuesByAlbum = {};
    for (const issue of payload.issues) {
      if (issue.scope === 'album') {
        state.issuesByAlbum[issue.album] = (state.issuesByAlbum[issue.album] || 0) + 1;
      }
    }
    renderTree();
  } catch (_) { /* the dots are a nicety, not worth a toast */ }
}

window.addEventListener('beforeunload', (ev) => {
  if (dirty()) { ev.preventDefault(); ev.returnValue = ''; }
});

boot();
