/* lucya.systems aperture — the console's charts.
 *
 * Plain HTML, no library: every chart here shows ONE series, so it takes one
 * neutral ink (--label, --chrome under the pointer) and no categorical
 * palette -- the glyph hue of the kind of thing it counts (time, albums,
 * tags, the machine), the same hue its card's icon
 * wears. The accent is state in Nebula, not data, so no chart wears it --
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
function columnChart(buckets, { unit = 'photos', height = 'm', hue = null } = {}) {
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

  return el('div', { class: 'colchart colchart--' + height + (hue ? ' colchart--' + hue : '') },
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
  return { node: columnChart(buckets, { hue: 'time', height: 's' }), quarterly, buckets };
}

/* A ranked list of bars: the top `limit` rows, the rest folded into one
 * "and N more" line rather than generated into ever-thinner bars. */
function rankedBars(rows, { limit = 8, unit = 'photos', onRow, hue = null } = {}) {
  const top = rows.slice(0, limit);
  const peak = Math.max(1, ...top.map((r) => r.value));
  const out = [el('div', { class: 'brows' }, top.map((r) => barRow(
    r.label, r.value, peak, r.value + ' ' + unit, hue, onRow ? () => onRow(r) : null)))];
  if (rows.length > limit) {
    const rest = rows.slice(limit).reduce((n, r) => n + r.value, 0);
    out.push(quiet('and ' + (rows.length - limit) + ' more · ' + rest + ' ' + unit));
  }
  return out;
}

/* A ring: parts of one whole, when there are few of them. The biggest four
 * take the four steps of ONE hue family -- light to dark, largest first, so
 * the order reads in the colour -- and everything else is one grey "Other".
 * Never a hue per slice: that is the rainbow a ring invites. `parts` is
 * [{label, value}], `hue` a glyph family (album, tag, time, machine). The
 * legend beside it carries the names and figures, so identity is never the
 * colour alone; hovering a slice or a row lights both and puts that part in
 * the middle. */
function donutChart(parts, { hue = 'machine', center = '', sub = '', unit = 'photos', onPart = null, keep = 4, ranked = true } = {}) {
  // ranked: largest first; otherwise the order given (a share and its rest)
  const kept = parts.filter((p) => p.value > 0);
  const sorted = ranked ? kept.sort((a, b) => b.value - a.value) : kept;
  let n = 0;
  const top = sorted.slice(0, keep).map((p) => ({ ...p, step: p.other ? 'other' : String(++n) }));
  const rest = sorted.slice(keep).reduce((n, p) => n + p.value, 0);
  if (rest) top.push({ label: 'Other', value: rest, step: 'other', other: true });
  const total = top.reduce((n, p) => n + p.value, 0) || 1;

  const NS = 'http://www.w3.org/2000/svg';
  const R = 46;
  const C = 2 * Math.PI * R;
  const GAP = top.length > 1 ? 1.6 : 0;   // the 2px surface gap between slices
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 120 120');
  svg.setAttribute('class', 'donut__svg');
  svg.setAttribute('aria-hidden', 'true');
  const track = document.createElementNS(NS, 'circle');
  track.setAttribute('class', 'donut__track');
  for (const [k, v] of [['cx', 60], ['cy', 60], ['r', R]]) track.setAttribute(k, v);
  svg.append(track);

  const mid = el('div', { class: 'donut__mid' },
    el('span', { class: 'donut__center', text: center }),
    el('span', { class: 'donut__sub', text: sub }));
  const legend = el('ul', { class: 'donut__legend' });
  const rows = [];
  const segs = [];
  const light = (i) => {
    segs.forEach((s, j) => s.classList.toggle('is-dim', i !== null && i !== j));
    rows.forEach((r, j) => r.classList.toggle('is-on', i === j));
    const p = i === null ? null : top[i];
    mid.firstChild.textContent = p ? Math.round(p.value / total * 100) + '%' : center;
    mid.lastChild.textContent = p ? p.label : sub;
  };

  let at = 0;
  top.forEach((p, i) => {
    const len = Math.max(0, p.value / total * C - GAP);
    const seg = document.createElementNS(NS, 'circle');
    seg.setAttribute('class', 'donut__seg donut__seg--' + p.step);
    for (const [k, v] of [['cx', 60], ['cy', 60], ['r', R]]) seg.setAttribute(k, v);
    seg.setAttribute('stroke-dasharray', len + ' ' + (C - len));
    seg.setAttribute('stroke-dashoffset', String(-at));
    seg.addEventListener('pointerenter', () => light(i));
    seg.addEventListener('pointerleave', () => light(null));
    if (onPart && !p.other) seg.addEventListener('click', () => onPart(p));
    svg.append(seg);
    segs.push(seg);
    at += p.value / total * C;

    const row = el(onPart && !p.other ? 'button' : 'div', {
      class: 'donut__row', type: onPart && !p.other ? 'button' : null,
      onpointerenter: () => light(i), onpointerleave: () => light(null),
      onfocus: () => light(i), onblur: () => light(null),
      onclick: onPart && !p.other ? () => onPart(p) : null,
    },
      el('span', { class: 'donut__sw donut__sw--' + p.step }),
      el('span', { class: 'donut__label', text: p.label }),
      el('span', { class: 'donut__v', text: p.value + ' · ' + Math.round(p.value / total * 100) + '%' }));
    rows.push(row);
    legend.append(el('li', {}, row));
  });

  return el('div', { class: 'donut donut--' + hue, role: 'group',
                     'aria-label': top.map((p) => p.label + ' ' + p.value + ' ' + unit).join(', ') },
    el('div', { class: 'donut__ring' }, svg, mid),
    legend);
}
