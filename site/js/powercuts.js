'use strict';

// Live power cuts in Cornwall, from NGED's open data: the tooltip and popup
// for each one. map.js draws the markers.

const PowerCuts = (() => {
  const TZ = 'Europe/London';
  const NGED_MAP = 'https://powercuts.nationalgrid.co.uk/';
  const PARTS = {
    OVERHEAD: 'overhead line', UNDERGROUND: 'underground cable', FUSE: 'fuse', DAMAGE: 'damage',
  };

  const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
  const fmtDayTime = ts => new Date(ts * 1000).toLocaleString('en-GB', {
    timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
  const sameDay = (a, b) => new Date(a * 1000).toLocaleDateString('en-GB', { timeZone: TZ }) ===
    new Date(b * 1000).toLocaleDateString('en-GB', { timeZone: TZ });
  // A time today as '17:30', otherwise with its day.
  const fmtWhen = (ts, now) => (sameDay(ts, now) ? fmtTime(ts) : fmtDayTime(ts));
  const plural = (n, one, many) => `${n.toLocaleString('en-GB')} ${n === 1 ? one : many}`;

  // NGED's categories, such as 'HV OVERHEAD' or 'LV GENERIC', in words.
  function describe(category) {
    const [volts, ...rest] = String(category || '').trim().split(/\s+/);
    const level = { HV: 'High voltage', LV: 'Low voltage', EHV: 'Extra high voltage' }[volts];
    if (!level) return category ? String(category).toLowerCase().replace(/^./, c => c.toUpperCase()) : '';
    const what = rest.filter(w => w !== 'GENERIC').map(w => PARTS[w] || w.toLowerCase()).join(' ');
    return what ? `${level} ${what}` : level;
  }

  function title(c) { return c.place ? `Power cut near ${c.place}` : 'Power cut'; }

  function summary(c, now = Date.now() / 1000) {
    const back = c.etr ? `back on by ${fmtWhen(c.etr, now)}` : 'no estimate yet';
    return `${plural(c.off, 'customer', 'customers')} off · ${back}`;
  }

  // Postcodes grouped by district: 'PL13 (13 postcodes)'.
  function districts(postcodes) {
    const n = new Map();
    for (const p of postcodes || []) {
      const d = p.split(' ')[0];
      n.set(d, (n.get(d) || 0) + 1);
    }
    return [...n].map(([d, k]) => `${d} (${plural(k, 'postcode', 'postcodes')})`).join(', ');
  }

  function node(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function content(c, updated) {
    const now = Date.now() / 1000;
    const box = node('div', 'stn-pop');
    box.appendChild(node('div', 'stn-pop-title', title(c)));
    box.appendChild(node('div', 'stn-pop-sub', [c.planned ? 'Planned work' : 'Unplanned', describe(c.category)].filter(Boolean).join(' · ')));
    const value = node('div', 'stn-pop-now');
    value.appendChild(node('span', 'stn-pop-value', c.off.toLocaleString('en-GB')));
    value.appendChild(node('span', 'stn-pop-note', c.off === 1 ? 'customer off' : 'customers off'));
    if (c.restored) value.appendChild(node('span', 'stn-pop-note', `· ${c.restored.toLocaleString('en-GB')} back on`));
    box.appendChild(value);
    const state = node('div', 'stn-pop-state is-power');
    state.appendChild(node('span', 'stn-pop-dot'));
    state.appendChild(document.createTextNode(c.etr
      ? `Expected back on by ${fmtWhen(c.etr, now)}` : 'No estimate yet of when power will be back'));
    box.appendChild(state);
    if (c.start) box.appendChild(node('div', 'stn-pop-line', `Started ${fmtWhen(c.start, now)}`));
    if (c.postcodes && c.postcodes.length) {
      const list = node('details', 'pcut-postcodes');
      list.appendChild(node('summary', null, districts(c.postcodes)));
      list.appendChild(node('div', 'pcut-postcode-list', c.postcodes.join(', ')));
      box.appendChild(list);
    }
    box.appendChild(node('div', 'stn-pop-line', 'To report a power cut, or for updates, call 105.'));
    const source = node('div', 'stn-pop-source');
    source.appendChild(document.createTextNode(`NGED incident ${c.id}${updated ? `, as of ${fmtTime(updated)}` : ''} · `));
    const a = node('a', 'stn-pop-link', 'NGED power cut map');
    a.href = NGED_MAP;
    a.target = '_blank';
    a.rel = 'noopener';
    source.appendChild(a);
    box.appendChild(source);
    return box;
  }

  function open(map, c, updated) {
    L.popup({ className: 'stn-popup', maxWidth: 340, minWidth: 280, autoPanPadding: [24, 24] })
      .setLatLng([c.lat, c.lon]).setContent(content(c, updated)).openOn(map);
  }

  return { open, title, summary, _internals: { describe, districts } };
})();
