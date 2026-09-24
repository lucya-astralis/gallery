/* lucya.systems aperture — the console's charts.
 *
 * Plain HTML, no library: every chart here shows ONE series, so it takes one
 * neutral ink (--label, --chrome under the pointer) and no categorical
 * palette. The accent is state in Nebula, not data, so no chart wears it --
 * only a progress fill does (the tag coverage meter). Sizes ride custom
 * properties through the CSSOM, because the console's CSP drops a style
 * attribute.
 *
 * A plain script before app.js; declarations only.
 */
'use strict';

/* A column chart over time: one column per bucket, gaps kept as zeros so
 * the time axis is honest, a hover/focus tooltip with the exact figure,
 * the axis labelled only where a new year starts. `buckets` is
 * [{key, label, value, axis?, onclick?}]. */
function columnChart(buckets, { unit = 'photos', height = 'm' } = {}) {
  const max = Math.max(1, ...buckets.map((b) => b.value));
  const tip = el('div', { class: 'colchart__tip', role: 'status', hidden: true });
  const cols = el('div', { class: 'colchart__cols' });
  const axis = el('div', { class: 'colchart__axis', 'aria-hidden': 'true' });
  const n = buckets.length;

  const show = (b, i, col) => {
    tip.textContent = b.label + ' · ' + b.value + ' ' + unit;
    tip.hidden = false;
    const xf = (i + 0.5) / n;
    tip.style.setProperty('--xf', String(xf));
    // near an edge the tip hangs inward instead of past the card
    tip.classList.toggle('is-left', xf < 0.12);
    tip.classList.toggle('is-right', xf > 0.88);
    tip.style.setProperty('--h', (b.value / max * 100) + '%');
    $$('.colchart__col.is-on', cols).forEach((c) => c !== col && c.classList.remove('is-on'));
    col.classList.add('is-on');
  };
  const hide = () => {
    tip.hidden = true;
    $$('.colchart__col.is-on', cols).forEach((c) => c.classList.remove('is-on'));
  };

  buckets.forEach((b, i) => {
    const col = el(b.onclick ? 'button' : 'span', {
      class: 'colchart__col' + (b.value ? '' : ' is-zero'),
      type: b.onclick ? 'button' : null,
      'aria-label': b.label + ': ' + b.value + ' ' + unit,
      onclick: b.onclick || null,
      onpointerenter: () => show(b, i, col),
      onfocus: () => show(b, i, col),
      onpointerleave: hide,
      onblur: hide,
    }, el('span', { class: 'colchart__bar' }));
    col.style.setProperty('--h', (b.value / max * 100) + '%');
    cols.append(col);
    axis.append(el('span', { class: 'colchart__tick', text: b.axis || '' }));
  });

  return el('div', { class: 'colchart colchart--' + height },
    el('div', { class: 'colchart__plot' },
      el('div', { class: 'colchart__grid', 'aria-hidden': 'true' },
        el('span', { class: 'colchart__gl', 'data-v': String(max) }),
        el('span', { class: 'colchart__gl', 'data-v': String(Math.round(max / 2)) }),
        el('span', { class: 'colchart__gl colchart__gl--base', 'data-v': '0' })),
      cols, tip),
    axis);
}

/* Photos per month between the first and the last dated one. Past six years
 * of months the columns get too thin to point at, so it counts per quarter. */
function photosOverTime(photos, onBucket) {
  let dated = photos.map((p) => p.taken).filter(Boolean).map((t) => t.slice(0, 7)).sort();
  if (!dated.length) return null;
  /* A camera with its clock never set says 2001, and one such photo would
   * stretch the axis over twenty years with everything real crammed into
   * the last few columns. The earliest 2% get one column of their own
   * instead, and the axis starts where the photos do. */
  const cut = dated[Math.floor(dated.length * 0.02)];
  const earlier = dated.filter((ym) => ym < cut).length;
  dated = dated.filter((ym) => ym >= cut);
  const [fy, fm] = dated[0].split('-').map(Number);
  const [ly, lm] = dated[dated.length - 1].split('-').map(Number);
  const months = (ly - fy) * 12 + (lm - fm) + 1;
  const quarterly = months > 72;
  const counts = new Map();
  for (const ym of dated) {
    const [y, m] = ym.split('-').map(Number);
    const key = quarterly ? y + '-Q' + (Math.floor((m - 1) / 3) + 1) : ym;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const buckets = [];
  if (earlier) {
    buckets.push({ key: 'earlier', label: 'Before ' + names[fm - 1] + ' ' + fy, value: earlier, axis: '…' });
  }
  let y = fy;
  let m = quarterly ? Math.floor((fm - 1) / 3) * 3 + 1 : fm;
  while (y < ly || (y === ly && m <= lm)) {
    const key = quarterly ? y + '-Q' + (Math.floor((m - 1) / 3) + 1) : y + '-' + String(m).padStart(2, '0');
    const first = buckets.length === (earlier ? 1 : 0) || m === 1;
    buckets.push({
      key,
      label: quarterly ? 'Q' + (Math.floor((m - 1) / 3) + 1) + ' ' + y : names[m - 1] + ' ' + y,
      value: counts.get(key) || 0,
      axis: first ? String(y) : '',
      onclick: onBucket ? () => onBucket(key, quarterly) : null,
    });
    m += quarterly ? 3 : 1;
    if (m > 12) { m -= 12; y++; }
  }
  return { node: columnChart(buckets), quarterly, buckets };
}

/* A ranked list of bars: the top `limit` rows, the rest folded into one
 * "and N more" line rather than generated into ever-thinner bars. */
function rankedBars(rows, { limit = 8, unit = 'photos', onRow } = {}) {
  const top = rows.slice(0, limit);
  const peak = Math.max(1, ...top.map((r) => r.value));
  const out = [el('div', { class: 'brows' }, top.map((r) => barRow(
    r.label, r.value, peak, r.value + ' ' + unit, null, onRow ? () => onRow(r) : null)))];
  if (rows.length > limit) {
    const rest = rows.slice(limit).reduce((n, r) => n + r.value, 0);
    out.push(quiet('and ' + (rows.length - limit) + ' more · ' + rest + ' ' + unit));
  }
  return out;
}
