/* Aperture 3.2 · the trip dashboard (_trip.html, f/trip.css). Every date is
   the viewer's local wall-clock, so it reads right from home before the
   flight and on the ground once the trip is underway. State is class
   toggles and CSSOM custom properties only (the CSP drops inline styles).
   NB: new Japanese text may need a font-subset rebuild, see
   tools/build_jp_subset.py. */
const UI_LANG = (document.documentElement.lang || 'en').toLowerCase().slice(0, 2);
const UI_STRINGS = {
  en: {
    departsIn: 'Departs in',
    arrivingIn: (city) => 'Arriving in ' + city,
    leavingIn: (city) => 'Leaving ' + city + ' in',
    tripComplete: 'Trip complete',
    soon: 'soon',
    inDays: (d) => 'in ' + d + ' days',
    dayOf: (n, total) => 'Day ' + n + ' / ' + total,
    months: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    fmtDate: (d, M) => `${d.getDate()} ${M[d.getMonth()]} ${d.getFullYear()}`,
  },
  de: {
    departsIn: 'Abflug in',
    arrivingIn: (city) => 'Ankunft in ' + city,
    leavingIn: (city) => 'Abreise aus ' + city + ' in',
    tripComplete: 'Reise beendet',
    soon: 'bald',
    inDays: (d) => 'in ' + d + ' Tagen',
    dayOf: (n, total) => 'Tag ' + n + ' / ' + total,
    months: ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'],
    fmtDate: (d, M) => `${d.getDate()}. ${M[d.getMonth()]} ${d.getFullYear()}`,
  },
  ja: {
    departsIn: '出発まで',
    arrivingIn: (city) => city + 'に到着まで',
    leavingIn: (city) => city + 'を出発まで',
    tripComplete: '旅は終了しました',
    soon: 'まもなく',
    inDays: (d) => 'あと' + d + '日',
    dayOf: (n, total) => n + '日目 / ' + total + '日',
    months: null,
    fmtDate: (d) => `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`,
  },
};
const TXT = UI_STRINGS[UI_LANG] || UI_STRINGS.en;

document.addEventListener('DOMContentLoaded', () => {
  const root = document.querySelector('[data-trip]');
  if (!root) return;

  const DAY = 86400000;
  const pad2 = (n) => String(n).padStart(2, '0');

  // "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS" -> local Date (NOT UTC).
  const parseLocal = (s) => {
    const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  };
  const fmtDate = (d) => d ? TXT.fmtDate(d, TXT.months) : '';
  const ceilDays = (from, to) => Math.max(0, Math.ceil((to - from) / DAY));

  const depart = parseLocal(root.dataset.depart);
  // `city` is the stop's region name and stays the English key (matches
  // _trip_map.html's data-map-city); `cityLabel` is
  // what countdown sentences display — the Japanese name on the JP page when
  // the template provides one.
  const stops = Array.from(root.querySelectorAll('[data-stop]')).map((el) => ({
    el,
    city: el.dataset.city || '',
    cityLabel: (UI_LANG === 'ja' && el.dataset.cityJp) ? el.dataset.cityJp : (el.dataset.city || ''),
    start: parseLocal(el.dataset.start),
    end: parseLocal(el.dataset.end),
    meta: el.querySelector('[data-stop-meta]'),
  })).filter((s) => s.start && s.end);

  // route map (_trip_map.html): dots + visited prefectures mirror the stop
  // states; segments (keyed by the stop they END at) get is-done / is-next.
  // The map lives in the album sidebar, outside [data-trip], so look it up
  // document-wide.
  const mapCity = {};
  const mapPref = {};
  document.querySelectorAll('[data-map-city]').forEach((g) => { mapCity[g.dataset.mapCity] = g; });
  document.querySelectorAll('[data-map-pref]').forEach((p) => { mapPref[p.dataset.mapPref] = p; });
  const mapSegs = Array.from(document.querySelectorAll('[data-map-seg]'));

  // side trips (_trip.html [data-side] + the map's *--side shapes): an
  // excursion from a leg's base that is not a leg. Same three states as a
  // leg, but the base stays the active leg and the countdown keeps running
  // to it — the base's map dot only hands its pulse over (is-away) so the
  // map never shows two "you are here"s.
  const sides = Array.from(root.querySelectorAll('[data-side]')).map((el) => ({
    el,
    city: el.dataset.city || '',
    from: el.dataset.from || '',
    region: el.dataset.region || '',
    // back to a region a leg already covers: that leg keeps its own state on
    // the map, its dot only takes the pulse while you are there (is-here)
    revisit: 'revisit' in el.dataset,
    start: parseLocal(el.dataset.start),
    end: parseLocal(el.dataset.end),
    meta: el.querySelector('[data-side-meta]'),
  })).filter((s) => s.start && s.end);
  const mapSide = {};
  document.querySelectorAll('[data-map-side]').forEach((p) => { mapSide[p.dataset.mapSide] = p; });

  // JST wall clock in the top bar (Japan has a single, DST-free zone). The
  // place label and the zone code are static markup — only the digits are
  // rewritten here; the whole readout hides when Intl can't do Asia/Tokyo.
  const clockEl = root.querySelector('[data-trip-clock]');
  const clockTimeEl = root.querySelector('[data-trip-time]');
  let jstFmt = null;
  try {
    jstFmt = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Tokyo', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
  } catch (e) {
    if (clockEl) clockEl.hidden = true;
  }

  const cd = {
    d: root.querySelector('[data-cd="d"]'),
    h: root.querySelector('[data-cd="h"]'),
    m: root.querySelector('[data-cd="m"]'),
    s: root.querySelector('[data-cd="s"]'),
  };
  const labelEl = root.querySelector('[data-trip-cd-label]');
  const targetEl = root.querySelector('[data-trip-cd-target]');

  const firstStart = stops.length ? stops[0].start : null;
  const lastEnd = stops.length ? stops[stops.length - 1].end : null;

  const setPhase = (p) => ['pre', 'transit', 'active', 'done']
    .forEach((x) => root.classList.toggle('trip--' + x, x === p));

  const setState = (el, state) => {
    if (!el) return;
    el.classList.toggle('is-upcoming', state === 'upcoming');
    el.classList.toggle('is-active', state === 'active');
    el.classList.toggle('is-done', state === 'done');
  };

  // Every readout the tick writes goes through here. Text that has not
  // changed is not touched at all (the tick used to rewrite ~30 nodes a
  // second, mostly with what they already said), and text that has is
  // changed IN its text node: replacing the node — which is what assigning
  // textContent does — reaches through the layout boundary around the
  // digits (.g-trip__unit b / .g-trip__clock b in trip.css) and WebKit lays
  // the whole page out again, photo grid and all: ~100 ms every second on
  // the Japan album once it had been scrolled through, which is what left it
  // deaf to taps and showing painted tiles as empty boxes.
  const setText = (el, v) => {
    if (!el) return;
    const node = el.firstChild;
    if (node && node.nodeType === 3 && !node.nextSibling) {
      if (node.data !== v) node.data = v;
    } else if (el.textContent !== v) {
      el.textContent = v;
    }
  };

  const setClock = (ms) => {
    const t = Math.max(0, ms);
    setText(cd.d, String(Math.floor(t / DAY)));
    setText(cd.h, pad2(Math.floor((t % DAY) / 3600000)));
    setText(cd.m, pad2(Math.floor((t % 3600000) / 60000)));
    setText(cd.s, pad2(Math.floor((t % 60000) / 1000)));
  };

  const setProp = (el, k, v) => { if (el && el.style.getPropertyValue(k) !== String(v)) el.style.setProperty(k, v); };
  const clampPct = (x) => Math.max(0, Math.min(100, x));

  // the journey bar: departure (or the first stop) to the flight home. Each
  // leg and side trip is placed once (--a/--b in percent of that span); the
  // tick only moves the fills and the "now" line.
  const journey = root.querySelector('[data-journey]');
  const t0 = depart || firstStart, span0 = (t0 && lastEnd) ? lastEnd - t0 : 0;
  const at = (d) => clampPct((d - t0) / span0 * 100).toFixed(3);
  const jStop = {}, jName = {}, jSide = {};
  const jNow = journey && journey.querySelector('[data-j-now]');
  if (journey && span0 > 0) {
    stops.forEach((s) => {
      const el = journey.querySelector(`[data-j-stop="${CSS.escape(s.city)}"]`);
      const nm = journey.querySelector(`[data-j-name="${CSS.escape(s.city)}"]`);
      [el, nm].forEach((x) => { if (x) { setProp(x, '--a', at(s.start)); setProp(x, '--b', at(s.end)); } });
      if (el) jStop[s.city] = { el, fill: el.querySelector('[data-j-fill]') };
      if (nm) jName[s.city] = nm;
    });
    sides.forEach((s) => {
      const el = journey.querySelector(`[data-j-side="${CSS.escape(s.city)}"]`);
      if (el) { setProp(el, '--a', at(s.start)); setProp(el, '--b', at(s.end)); jSide[s.city] = el; }
    });
    journey.hidden = false;
    // a stay too short for its name keeps the bar and drops the word
    const fit = () => Object.values(jName).forEach((nm) => {
      nm.classList.remove('is-tight');
      nm.classList.toggle('is-tight', nm.scrollWidth > nm.clientWidth + 1);
    });
    fit();
    addEventListener('resize', fit, { passive: true });
    document.fonts?.ready.then(fit);
  }

  function tick() {
    const now = new Date();

    // per-stop state + progress fill. Boundaries are start-inclusive /
    // end-exclusive (the last stop includes its end) so a shared travel-day
    // date belongs to the city you're arriving in — only one stop is ever
    // "active".
    let activeIdx = -1;
    const states = [];
    stops.forEach((s, i) => {
      const isLast = i === stops.length - 1;
      let state;
      if (now < s.start) state = 'upcoming';
      else if (isLast ? now > s.end : now >= s.end) state = 'done';
      else { state = 'active'; activeIdx = i; }
      states.push(state);

      setState(s.el, state);
      setState(mapCity[s.city], state);
      setState(mapPref[s.city], state);

      const span = s.end - s.start;
      let pct = state === 'done' ? 100
        : state === 'upcoming' ? 0
        : span > 0 ? ((now - s.start) / span) * 100 : 0;
      pct = Math.max(0, Math.min(100, pct));
      const j = jStop[s.city];
      if (j) { setState(j.el, state); setProp(j.fill, '--p', pct.toFixed(2)); }
      if (jName[s.city]) jName[s.city].classList.toggle('is-active', state === 'active');
      if (s.meta) {
        if (state === 'upcoming') {
          const dleft = ceilDays(now, s.start);
          setText(s.meta, dleft <= 1 ? TXT.soon : TXT.inDays(dleft));
        } else if (state === 'active') {
          const total = Math.max(1, Math.round(span / DAY));
          const dayNum = Math.min(total, Math.floor((now - s.start) / DAY) + 1);
          setText(s.meta, TXT.dayOf(dayNum, total));
        } else {
          setText(s.meta, '✓');
        }
      }
    });

    // map segments: seg i is the leg INTO stop i (there is no leg into stop
    // 0). Done once that stop is reached; "next" (animated dashes) while its
    // origin stop is underway/passed but the destination hasn't started.
    mapSegs.forEach((seg) => {
      const i = +seg.dataset.mapSeg;
      const reached = states[i] && states[i] !== 'upcoming';
      const next = states[i] === 'upcoming' && !!states[i - 1] && states[i - 1] !== 'upcoming';
      seg.classList.toggle('is-done', !!reached);
      seg.classList.toggle('is-next', next);
    });

    let away = null;
    sides.forEach((s) => {
      const state = now < s.start ? 'upcoming' : now >= s.end ? 'done' : 'active';
      if (state === 'active') away = s;
      setState(s.el, state);
      if (s.revisit) {
        if (mapCity[s.region]) mapCity[s.region].classList.toggle('is-here', state === 'active');
      } else {
        setState(mapCity[s.city], state);
        if (s.region) setState(mapPref[s.region], state);
      }
      if (mapSide[s.city]) mapSide[s.city].classList.toggle('is-done', state !== 'upcoming');
      setState(jSide[s.city], state);
      if (s.meta) {
        if (state === 'upcoming') {
          const dleft = ceilDays(now, s.start);
          setText(s.meta, dleft <= 1 ? TXT.soon : TXT.inDays(dleft));
        } else if (state === 'active') {
          const total = Math.max(1, Math.ceil((s.end - s.start) / DAY));
          setText(s.meta, TXT.dayOf(Math.min(total, Math.floor((now - s.start) / DAY) + 1), total));
        } else {
          setText(s.meta, '✓');
        }
      }
    });
    stops.forEach((s) => {
      if (mapCity[s.city]) mapCity[s.city].classList.toggle('is-away', !!away && away.from === s.city);
    });

    if (jstFmt && clockTimeEl) setText(clockTimeEl, jstFmt.format(now));

    // phase + headline countdown (label is localized).
    let phase, target, label;
    if (depart && now < depart) {
      phase = 'pre'; target = depart;
      label = TXT.departsIn;
    } else if (firstStart && now < firstStart) {
      phase = 'transit'; target = firstStart;
      label = TXT.arrivingIn(stops[0].cityLabel);
    } else if (activeIdx >= 0) {
      phase = 'active'; target = stops[activeIdx].end;
      label = TXT.leavingIn(stops[activeIdx].cityLabel);
    } else {
      phase = 'done'; target = null;
      label = TXT.tripComplete;
    }

    setPhase(phase);
    setClock(target ? target - now : 0);
    setText(labelEl, label);
    if (jNow && span0 > 0) setProp(jNow, '--p', clampPct((now - t0) / span0 * 100).toFixed(2));
    setText(targetEl, fmtDate(target || lastEnd));
  }

  root.classList.add('is-live');
  tick();
  setInterval(tick, 1000);

  // ---- current weather per stop (same-origin proxy) ----
  // One fetch per page view against /api/trip-weather (server-cached proxy
  // to Open-Meteo — the browser never talks to a third party, so no consent
  // UI is needed and connect-src 'self' holds). Chips stay hidden unless
  // real data arrives; glyphs use text-presentation symbols so they render
  // in the mono HUD style instead of as emoji. Condition names follow the page language.
  const wxByCity = {};
  stops.forEach((s) => {
    const el = s.el.querySelector('[data-stop-wx]');
    if (el) wxByCity[s.city] = el;
  });
  const tripKey = root.dataset.tripKey;
  if (tripKey && window.fetch && Object.keys(wxByCity).length) {
    // WMO weather_code buckets -> [glyph, label, kind]; ☀/☾ pick by is_day.
    // `kind` lands on the chip as data-wx-kind and only tints the glyph
    // (trip.css): warm for sun and storm, the accent for rain and snow
    // the weather's words in the page's language (they were English only)
    const WX = {
      de: { 'Clear': 'Klar', 'Mostly clear': 'Überwiegend klar', 'Partly cloudy': 'Teils bewölkt', 'Overcast': 'Bedeckt',
            'Fog': 'Nebel', 'Drizzle': 'Nieselregen', 'Rain': 'Regen', 'Snow': 'Schnee', 'Rain showers': 'Regenschauer',
            'Snow showers': 'Schneeschauer', 'Thunderstorm': 'Gewitter' },
      ja: { 'Clear': '快晴', 'Mostly clear': '晴れ', 'Partly cloudy': '晴れ時々曇り', 'Overcast': '曇り', 'Fog': '霧',
            'Drizzle': '霧雨', 'Rain': '雨', 'Snow': '雪', 'Rain showers': 'にわか雨', 'Snow showers': 'にわか雪', 'Thunderstorm': '雷雨' },
    }[UI_LANG] || {};
    const wmo = (code, isDay) => {
      const [glyph, label, kind] = wmoEn(code, isDay);
      return [glyph, WX[label] || label, kind];
    };
    const wmoEn = (code, isDay) => {
      if (code <= 1) return [isDay ? '☀︎' : '☾︎', code === 0 ? 'Clear' : 'Mostly clear', isDay ? 'sun' : 'night'];
      if (code <= 3) return ['☁︎', code === 2 ? 'Partly cloudy' : 'Overcast', 'cloud'];
      if (code <= 48) return ['≡', 'Fog', 'fog'];
      if (code <= 57) return ['☂︎', 'Drizzle', 'rain'];
      if (code <= 67) return ['☂︎', 'Rain', 'rain'];
      if (code <= 77) return ['❄︎', 'Snow', 'snow'];
      if (code <= 82) return ['☂︎', 'Rain showers', 'rain'];
      if (code <= 86) return ['❄︎', 'Snow showers', 'snow'];
      return ['⚡︎', 'Thunderstorm', 'storm'];
    };
    fetch('/api/trip-weather?trip=' + encodeURIComponent(tripKey), { credentials: 'omit' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !Array.isArray(data.stops)) return;
        data.stops.forEach((w) => {
          const el = wxByCity[w.city];
          if (!el || typeof w.temp !== 'number') return;
          const ico = el.querySelector('[data-wx-ico]');
          const temp = el.querySelector('[data-wx-temp]');
          const cond = el.querySelector('[data-wx-cond]');
          const range = el.querySelector('[data-wx-range]');
          if (!ico || !temp) return;
          const [glyph, label, kind] = wmo(w.code, w.is_day);
          ico.textContent = glyph;
          temp.textContent = Math.round(w.temp) + '°';
          if (cond) cond.textContent = label;
          el.dataset.wxKind = kind;
          // today's envelope is a bonus — the row stays hidden when the
          // upstream payload carries no daily block
          let tip = label;
          if (range) {
            const hasRange = typeof w.hi === 'number' && typeof w.lo === 'number';
            if (hasRange) {
              range.textContent = '↑' + w.hi + '° ↓' + w.lo + '°';
              tip += ' · today ' + w.lo + '–' + w.hi + '°';
            }
            range.hidden = !hasRange;
          }
          el.title = tip + ' · weather: open-meteo.com';
          el.hidden = false;
        });
      })
      .catch(() => {}); // no weather is a fine state — chips just stay hidden
  }
});

