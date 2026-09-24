/* lucya.systems aperture — the console's tools: history, many albums at
 * once, saved Library views, and the activity log.
 *
 * Plain script between library.js and app.js, sharing their scope; nothing
 * here runs at load time but declarations.
 */
'use strict';

/* ----- history -------------------------------------------------------------
 * Every overwrite already leaves the previous version in data/console/backups.
 * This shows them: pick a version, read what putting it back would change,
 * put it back. A restore is an ordinary write -- backed up and audited -- so
 * it can be undone the same way. */
async function openHistory(target) {
  const params = qs(target);
  let hist;
  try {
    hist = await api('/api/history?' + params);
  } catch (err) {
    toast(err.message, 'err');
    return;
  }
  const dlg = reviewDialog();
  const pick = { id: hist.versions.length ? hist.versions[0].id : null };
  const pane = el('div', { class: 'history__diff' });

  const showVersion = async (id) => {
    pick.id = id;
    $$('.history__v', dlg).forEach((b) => b.classList.toggle('is-on', b.dataset.id === id));
    pane.replaceChildren(el('p', { class: 'insp__quiet', text: 'Reading…' }));
    let version;
    try {
      version = await api('/api/history/version?' + params + '&version=' + encodeURIComponent(id));
    } catch (err) {
      pane.replaceChildren(el('p', { class: 'insp__quiet', text: err.message }));
      return;
    }
    if (pick.id !== id) return;
    const diff = renderDiff(hist.current, version.text);
    pane.replaceChildren(
      el('p', { class: 'review__count', text: diff.added || diff.removed
        ? 'Restoring this version changes −' + diff.removed + ' +' + diff.added + ' lines of the file as it is now:'
        : 'This version is the same as the file as it is now.' }),
      diff.node);
    $('#history-restore').disabled = READ_ONLY || !(diff.added || diff.removed);
  };

  dlg.replaceChildren(
    el('header', { class: 'dlg__head' },
      el('h2', { id: 'review-title', text: 'History' }),
      el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Close', onclick: () => dlg.close() },
        ico('fa-xmark'))),
    el('div', { class: 'dlg__body' },
      el('p', { class: 'review__file' },
        el('code', { text: hist.file }),
        el('span', { class: 'review__count', text: hist.versions.length
          ? hist.versions.length + ' earlier version' + (hist.versions.length === 1 ? '' : 's') + ' kept'
          : 'no earlier versions yet — one is kept every time this file is saved' })),
      hist.versions.length ? el('div', { class: 'history' },
        el('ol', { class: 'history__list' }, hist.versions.map((v) => el('li', {},
          el('button', {
            type: 'button', class: 'history__v', 'data-id': v.id,
            onclick: () => showVersion(v.id),
          },
            el('span', { class: 'history__when', text: agoIso(v.saved) }),
            el('span', { class: 'history__stamp', text: v.saved.replace('T', ' ').slice(0, 16) + ' UTC' }))))),
        pane) : null),
    el('footer', { class: 'dlg__foot' },
      el('button', { type: 'button', class: 'btn btn--ghost', text: 'Close', onclick: () => dlg.close() }),
      hist.versions.length ? el('button', {
        type: 'button', class: 'btn btn--primary', icon: 'fa-clock-rotate-left', text: 'Restore this version',
        id: 'history-restore', disabled: true,
        onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          try {
            await api('/api/history/restore', { method: 'POST', body: JSON.stringify({ ...target, version: pick.id }) });
          } catch (err) {
            toast(err.message, 'err');
            ev.currentTarget.disabled = false;
            return;
          }
          dlg.close();
          toast('Restored ' + hist.file + ' — the version it replaced is in its history now');
          if (target.file === 'desc') {
            delete descDrafts[target.album + '|' + target.lang];
          }
          await select(state.sel, true, 'none');
          refreshIssueDots();
        },
      }) : null));
  if (!dlg.open) dlg.showModal();
  if (pick.id) showVersion(pick.id);
}

/* ----- many albums at once --------------------------------------------------
 * Tick albums in the table, set one key for all of them. Each album.cfg is
 * written by the same save a single album uses -- comments kept, backed up,
 * audited -- and the whole batch is reviewed as diffs first. */
const BULK_TYPES = new Set(['bool', 'bool_off', 'choice', 'number', 'color', 'ratio', 'text', 'list']);

function bulkKeys() {
  return (state.meta.album_keys || []).filter((key) => key !== 'name' &&
    BULK_TYPES.has((state.meta.spec[key] || {}).type));
}

function bulkControl(key, onValue) {
  const spec = state.meta.spec[key] || {};
  const help = el('p', { class: 'field__help', text: state.meta.help[key] || '' });
  let control;
  if (spec.type === 'bool') {
    control = el('select', { class: 'bulk__value', onchange: (ev) => onValue(ev.target.value) },
      el('option', { value: 'true', text: 'Yes' }), el('option', { value: 'false', text: 'No' }));
    onValue('true');
  } else if (spec.type === 'choice') {
    const choices = spec.choices || [];
    control = el('select', { class: 'bulk__value', onchange: (ev) => onValue(ev.target.value) },
      choices.map((c) => el('option', { value: c, text: c })));
    onValue(choices[0] || '');
  } else if (spec.type === 'bool_off') {
    control = el('select', { class: 'bulk__value', onchange: (ev) => onValue(ev.target.value) },
      el('option', { value: 'off', text: 'Off' }));
    onValue('off');
  } else if (spec.type === 'list') {
    control = el('input', { type: 'text', class: 'bulk__value', placeholder: 'comma, separated',
      oninput: (ev) => onValue(ev.target.value.split(',').map((t) => t.trim()).filter(Boolean)) });
    onValue([]);
  } else {
    control = el('input', { type: 'text', class: 'bulk__value',
      placeholder: spec.type === 'color' ? '#616ef3' : spec.type === 'ratio' ? '0–1 or off' : '',
      oninput: (ev) => onValue(ev.target.value.trim()) });
    onValue('');
  }
  return [control, help];
}

function openBulk(albums) {
  const dlg = reviewDialog();
  const keys = bulkKeys();
  const job = { key: keys[0], value: null, remove: false };
  const valueBox = el('div', { class: 'bulk__control' });
  const diffBox = el('div', { class: 'bulk__diffs' });

  const paintValue = () => valueBox.replaceChildren(...bulkControl(job.key, (v) => { job.value = v; }));
  const values = () => ({ [job.key]: job.remove ? null : job.value });

  const preview = async () => {
    diffBox.replaceChildren(el('p', { class: 'insp__quiet', text: 'Working out ' + albums.length + ' diffs…' }));
    const parts = [];
    let changed = 0;
    for (const album of albums) {
      try {
        const res = await api('/api/album/cfg/preview', { method: 'POST', body: JSON.stringify({ album, values: values() }) });
        const diff = renderDiff(res.before, res.after);
        if (diff.added || diff.removed) changed++;
        parts.push(el('details', { class: 'bulk__album', open: albums.length <= 3 },
          el('summary', {}, el('code', { text: album }),
            el('span', { class: 'review__count', text: diff.added || diff.removed ? '−' + diff.removed + ' +' + diff.added : 'no change' })),
          diff.node));
      } catch (err) {
        parts.push(el('p', { class: 'status status--err', icon: 'fa-circle-exclamation', text: album + ': ' + err.message }));
      }
    }
    diffBox.replaceChildren(el('p', { class: 'review__count', text: changed + ' of ' + albums.length + ' album.cfg files change' }), ...parts);
    $('#bulk-apply').disabled = READ_ONLY || !changed;
  };

  dlg.replaceChildren(
    el('header', { class: 'dlg__head' },
      el('h2', { id: 'review-title', text: 'Set a key on ' + albums.length + ' albums' }),
      el('button', { type: 'button', class: 'btn btn--ghost btn--icon', 'aria-label': 'Close', onclick: () => dlg.close() },
        ico('fa-xmark'))),
    el('div', { class: 'dlg__body' },
      el('p', { class: 'insp__quiet', text: albums.join(', ') }),
      el('div', { class: 'bulk__form' },
        el('label', { class: 'bulk__field' }, el('span', { class: 'bulk__label', text: 'Key' }),
          el('select', { onchange: (ev) => { job.key = ev.target.value; paintValue(); diffBox.replaceChildren(); $('#bulk-apply').disabled = true; } },
            keys.map((k) => el('option', { value: k, text: humanKey(k) + ' (' + k + ')' })))),
        el('div', { class: 'bulk__field' }, el('span', { class: 'bulk__label', text: 'Value' }), valueBox),
        el('label', { class: 'bulk__remove' },
          el('input', { type: 'checkbox', onchange: (ev) => { job.remove = ev.target.checked; valueBox.classList.toggle('is-off', job.remove); } }),
          'Remove the key instead')),
      el('div', { class: 'card__actions' },
        el('button', { type: 'button', class: 'btn', icon: 'fa-list-check', text: 'Preview the diffs', onclick: preview })),
      diffBox),
    el('footer', { class: 'dlg__foot' },
      el('button', { type: 'button', class: 'btn btn--ghost', text: 'Cancel', onclick: () => dlg.close() }),
      el('button', {
        type: 'button', class: 'btn btn--primary', icon: 'fa-floppy-disk', text: 'Write ' + albums.length + ' files',
        id: 'bulk-apply', disabled: true,
        onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          let done = 0;
          const failed = [];
          for (const album of albums) {
            try {
              await api('/api/album/cfg', { method: 'PUT', body: JSON.stringify({ album, values: values() }) });
              done++;
            } catch (err) {
              failed.push(album + ': ' + err.message);
            }
          }
          dlg.close();
          toast(failed.length ? done + ' saved, ' + failed.length + ' failed — ' + failed[0] : 'Saved ' + done + ' album.cfg files',
                failed.length ? 'err' : 'ok');
          albumsView.sel.clear();
          await loadTree();
          refreshIssueDots();
          if (state.sel.kind === 'albums') renderAlbums();
        },
      })));
  paintValue();
  if (!dlg.open) dlg.showModal();
}

/* ----- saved Library views --------------------------------------------------
 * A filter you come back to -- "untagged, this year", "featured without a
 * tag" -- under a name. Kept in this browser: a way of working, not a
 * property of the gallery. */
const VIEWS_KEY = 'aperture.views';

function loadViews() {
  try {
    const list = JSON.parse(localStorage.getItem(VIEWS_KEY) || '[]');
    return Array.isArray(list) ? list.filter((v) => v && typeof v.name === 'string') : [];
  } catch (_) { return []; }
}

function storeViews(list) {
  try { localStorage.setItem(VIEWS_KEY, JSON.stringify(list)); } catch (_) { toast('This browser will not keep views', 'warn'); }
}

function currentView(name) {
  return { name, album: L.album, q: L.q, tags: [...L.tags], untagged: L.untagged,
           cameras: [...L.cameras], years: [...L.years], featured: L.featured, unindexed: L.unindexed,
           sort: L.sort };
}

function applyView(view) {
  L.q = view.q || '';
  L.tags = new Set(view.tags || []);
  L.cameras = new Set(view.cameras || []);
  L.years = new Set(view.years || []);
  L.untagged = !!view.untagged;
  L.featured = !!view.featured;
  L.unindexed = !!view.unindexed;
  if (view.sort && SORTS[view.sort]) L.sort = view.sort;
  if ((view.album || '') !== L.album) go({ kind: 'library', album: view.album || '' });
  else renderLibrary();
}

function viewsGroup() {
  const views = loadViews();
  const rows = views.map((view, i) => el('div', { class: 'facet facet--view' },
    el('button', { type: 'button', class: 'facet__go', title: 'Apply this view', onclick: () => applyView(view) },
      ico('fa-filter'), el('span', { class: 'facet__label', text: view.name })),
    el('button', { type: 'button', class: 'facet__x', 'aria-label': 'Forget ' + view.name,
                   onclick: () => { views.splice(i, 1); storeViews(views); paintFacets(); } }, ico('fa-xmark'))));
  const input = el('input', {
    type: 'text', class: 'views__name', placeholder: 'Name this view…', 'aria-label': 'Name for the current filters',
    onkeydown: (ev) => {
      if (ev.key !== 'Enter') return;
      const name = ev.target.value.trim();
      if (!name) return;
      const list = loadViews().filter((v) => v.name !== name);
      list.push(currentView(name));
      storeViews(list);
      toast('Saved the view “' + name + '”');
      paintFacets();
    },
  });
  return el('section', { class: 'facets__group' },
    el('h2', { class: 'facets__title', text: 'Views' }),
    rows,
    anyFilter() || L.album ? input : (views.length ? null : el('p', { class: 'facets__hint', text: 'Filter the grid, then name the filters here to keep them.' })));
}

/* ----- activity: every write this console made -------------------------- */
const activity = { filter: '', entries: null };

function paintActivity(grid) {
  if (!activity.entries) {
    grid.append(wide(card('fa-clock-rotate-left', 'Activity', null, quiet('Reading the log…'))));
    api('/api/audit?limit=200').then((res) => {
      activity.entries = res.entries || [];
      if (onOps() && opsState.tab === 'activity') paintOps();
    }).catch((err) => { activity.entries = []; toast(err.message, 'err'); });
    return;
  }
  const q = activity.filter.trim().toLowerCase();
  const list = activity.entries.filter((e) => !q ||
    (e.target || '').toLowerCase().includes(q) || (e.action || '').toLowerCase().includes(q));
  grid.append(wide(card('fa-clock-rotate-left', 'Activity', list.length + ' of ' + activity.entries.length + ' writes',
    el('div', { class: 'ops__form' },
      searchbox({ type: 'search', class: 'fieldsearch', value: activity.filter, 'data-fk': 'activity-q',
                  placeholder: 'Filter by file or action…', autocomplete: 'off',
                  oninput: (ev) => { activity.filter = ev.target.value; paintOps(); } }),
      el('button', { type: 'button', class: 'btn', icon: 'fa-rotate-right', text: 'Read again',
                     onclick: () => { activity.entries = null; paintOps(); } })),
    list.length
      ? foldedRows('activity', list, 40, (entry) => {
          const target = auditTarget(entry.target);
          return homeRow(agoIso(entry.ts), entry.action || '—', entry.target || '',
            target ? () => go(target) : null);
        })
      : quiet(q ? 'Nothing matches.' : 'Nothing has been saved here yet.'))));
  const field = $('input[data-fk="activity-q"]');
  if (field && q) { field.focus(); field.setSelectionRange(field.value.length, field.value.length); }
}
