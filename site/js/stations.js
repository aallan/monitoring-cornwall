'use strict';

// Station popups: the latest reading, and a chart of recent history fetched
// from /api/stations/<id>/readings when the popup opens.

const StationPopup = (() => {
  const TZ = 'Europe/London';
  const RANGES = [[1, '24 hours'], [7, '7 days'], [28, '28 days']];
  const HISTORY_DAYS = 28;
  const history = new Map();  // station id -> Promise of the API response

  // Formatting ---------------------------------------------------------------
  const unitLabel = u => (u === 'mAOD' ? 'm AOD' : u === 'mASD' ? 'm' : u || '');

  function fmtValue(s, v) {
    if (v == null) return '—';
    if (s.kind === 'rain') return `${Number(v).toLocaleString('en-GB', { maximumFractionDigits: 1 })} mm`;
    return `${Number(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unitLabel(s.unit)}`;
  }

  function fmtTime(ts, withDay = true) {
    const opts = { timeZone: TZ, hour: '2-digit', minute: '2-digit' };
    if (withDay) Object.assign(opts, { weekday: 'short', day: 'numeric', month: 'short' });
    return new Date(ts * 1000).toLocaleString('en-GB', opts);
  }

  function fmtAgo(ts) {
    const mins = Math.round((Date.now() / 1000 - ts) / 60);
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.round(mins / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
  }

  const STATE_TEXT = {
    high: 'Above its typical range', normal: 'Within its typical range', low: 'Below its typical range',
    unknown: 'No typical range published', stale: 'No recent reading', 'no-data': 'No readings yet',
    tidal: 'Tidal', raining: 'Raining', dry: 'Dry',
  };

  // One line for tooltips: the reading and what it means.
  function summary(s) {
    if (s.state === 'no-data') return 'no readings yet';
    if (s.kind === 'rain') {
      return s.state === 'stale' ? `no reading since ${fmtTime(s.ts)}`
        : `${fmtValue(s, s.h1)} in the last hour · ${fmtValue(s, s.h24)} in 24 h`;
    }
    const trend = s.trend ? ` · ${s.trend}` : '';
    return s.state === 'stale' ? `${fmtValue(s, s.value)} at ${fmtTime(s.ts)}` : `${fmtValue(s, s.value)}${trend}`;
  }

  // Chart ----------------------------------------------------------------------
  const W = 320, H = 150, M = { l: 40, r: 8, t: 10, b: 22 };
  const NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs = {}, parent) {
    const e = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }

  // Rain is drawn as totals per bucket: 15 minutes, an hour or a day.
  function rainBuckets(readings, days, from) {
    const size = days <= 1 ? 900 : days <= 7 ? 3600 : 86400;
    const sums = new Map();
    for (const [t, v] of readings) {
      if (t <= from) continue;
      const b = Math.floor(t / size) * size;
      sums.set(b, (sums.get(b) || 0) + v);
    }
    return { size, bars: [...sums.entries()].sort((a, b) => a[0] - b[0]) };
  }

  function niceTicks(lo, hi, count = 3) {
    const span = hi - lo || 1;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0);
    const ticks = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
    return ticks;
  }

  // Ticks at UK local hours, found hour by hour so that the change between
  // GMT and BST never shifts them: every 6 hours over a day, midnights over a
  // week, and Monday midnights over four weeks.
  const hourParts = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: 'numeric', hourCycle: 'h23', weekday: 'short' });
  function timeTicks(from, to, days) {
    const ticks = [];
    for (let t = Math.ceil(from / 3600) * 3600; t <= to; t += 3600) {
      const parts = hourParts.formatToParts(new Date(t * 1000));
      const hour = Number(parts.find(p => p.type === 'hour').value);
      const weekday = parts.find(p => p.type === 'weekday').value;
      if (days <= 1 ? hour % 6 === 0 : hour === 0 && (days <= 7 || weekday === 'Mon')) ticks.push(t);
    }
    return ticks;
  }

  function tickLabel(t, days) {
    const d = new Date(t * 1000);
    if (days <= 1) return d.toLocaleTimeString('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
    if (days <= 7) return d.toLocaleDateString('en-GB', { timeZone: TZ, weekday: 'short' });
    return d.toLocaleDateString('en-GB', { timeZone: TZ, day: 'numeric', month: 'short' });
  }

  function drawChart(host, readout, s, data, days) {
    host.textContent = '';
    const now = Math.floor(Date.now() / 1000);
    const from = now - days * 86400;
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'stn-chart', role: 'img',
      'aria-label': `${s.name}: readings over the last ${days === 1 ? '24 hours' : days + ' days'}` });
    host.appendChild(svg);
    const x = t => M.l + (t - from) / (now - from) * (W - M.l - M.r);

    let points, lo, hi, barWidth = 0;
    if (s.kind === 'rain') {
      const { size, bars } = rainBuckets(data.readings, days, from);
      points = bars;
      lo = 0;
      hi = Math.max(1, ...bars.map(b => b[1]));
      barWidth = Math.max(1, (x(from + size) - x(from)) - 1);
    } else {
      points = data.readings.filter(r => r[0] > from);
      const vals = points.map(r => r[1]);
      const typical = (data.typical || []).filter(v => v != null);
      lo = Math.min(...vals, ...typical);
      hi = Math.max(...vals, ...typical);
      const pad = (hi - lo) * 0.1 || 0.1;
      lo -= pad; hi += pad;
    }
    if (!points.length) {
      el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'stn-chart-empty' }, svg)
        .textContent = 'No readings in this period';
      return;
    }
    const y = v => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);

    // Grid and axes
    for (const v of niceTicks(lo, hi)) {
      el('line', { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v), class: 'stn-grid' }, svg);
      el('text', { x: M.l - 5, y: y(v) + 3, 'text-anchor': 'end', class: 'stn-axis' }, svg).textContent =
        v.toLocaleString('en-GB', { maximumFractionDigits: 2 });
    }
    for (const t of timeTicks(from, now, days)) {
      el('line', { x1: x(t), x2: x(t), y1: M.t, y2: H - M.b, class: 'stn-grid' }, svg);
      el('text', { x: x(t), y: H - 6, 'text-anchor': 'middle', class: 'stn-axis' }, svg).textContent = tickLabel(t, days);
    }

    // Typical range band for river levels
    if (s.kind === 'level' && data.typical && data.typical[0] != null && data.typical[1] != null) {
      const top = y(data.typical[1]), bottom = y(data.typical[0]);
      el('rect', { x: M.l, y: top, width: W - M.l - M.r, height: Math.max(0, bottom - top), class: 'stn-band' }, svg);
    }

    if (s.kind === 'rain') {
      for (const [t, v] of points) {
        if (v <= 0) continue;
        el('rect', { x: x(t), y: y(v), width: barWidth, height: y(0) - y(v), class: 'stn-bar' }, svg);
      }
    } else {
      // Break the line where readings are missing for more than an hour.
      let d = '';
      let prev = null;
      for (const [t, v] of points) {
        d += `${prev === null || t - prev > 3600 ? 'M' : 'L'}${x(t).toFixed(1)},${y(v).toFixed(1)}`;
        prev = t;
      }
      el('path', { d, class: `stn-line stn-line-${s.kind}` }, svg);
    }

    // Hover readout
    const cursor = el('line', { y1: M.t, y2: H - M.b, class: 'stn-cursor', visibility: 'hidden' }, svg);
    const caption = s.kind === 'rain'
      ? `${days <= 1 ? 'per 15 minutes' : days <= 7 ? 'per hour' : 'per day'}`
      : '';
    readout.textContent = caption ? `Rainfall ${caption}` : '';
    svg.addEventListener('mousemove', e => {
      const box = svg.getBoundingClientRect();
      const t = from + ((e.clientX - box.left) * W / box.width - M.l) / (W - M.l - M.r) * (now - from);
      let best = null;
      for (const p of points) if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      if (!best) return;
      cursor.setAttribute('x1', x(best[0]));
      cursor.setAttribute('x2', x(best[0]));
      cursor.setAttribute('visibility', 'visible');
      readout.textContent = `${fmtTime(best[0])} · ${fmtValue(s, best[1])}${caption ? ' ' + caption : ''}`;
    });
    svg.addEventListener('mouseleave', () => {
      cursor.setAttribute('visibility', 'hidden');
      readout.textContent = caption ? `Rainfall ${caption}` : '';
    });
  }

  // Popup ----------------------------------------------------------------------
  function node(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function content(s) {
    const box = node('div', 'stn-pop');
    box.appendChild(node('div', 'stn-pop-title', s.name));
    const kindText = { level: 'River level', tidal: 'Tidal level', rain: 'Rain gauge' }[s.kind];
    box.appendChild(node('div', 'stn-pop-sub', [s.river, kindText].filter(Boolean).join(' · ')));

    const now = node('div', 'stn-pop-now');
    if (s.kind === 'rain') {
      now.appendChild(node('span', 'stn-pop-value', fmtValue(s, s.h24)));
      now.appendChild(node('span', 'stn-pop-note', 'in the last 24 hours'));
    } else {
      now.appendChild(node('span', 'stn-pop-value', fmtValue(s, s.value)));
      if (s.trend) now.appendChild(node('span', 'stn-pop-note', s.trend));
    }
    box.appendChild(now);
    const state = node('div', `stn-pop-state is-${s.state}`);
    state.appendChild(node('span', 'stn-pop-dot'));
    state.appendChild(document.createTextNode(STATE_TEXT[s.state] || ''));
    if (s.ts) state.appendChild(node('span', 'stn-pop-when', ` · ${fmtAgo(s.ts)}`));
    box.appendChild(state);

    const tabs = node('div', 'stn-pop-ranges');
    const chartHost = node('div', 'stn-pop-chart');
    const readout = node('div', 'stn-pop-readout');
    box.append(tabs, chartHost, readout);
    box.appendChild(node('div', 'stn-pop-source', `Environment Agency station ${s.id}`));

    let current = 7;
    const render = () => {
      chartHost.classList.add('loading');
      load(s.id).then(data => {
        chartHost.classList.remove('loading');
        drawChart(chartHost, readout, s, data, current);
      }).catch(() => {
        chartHost.classList.remove('loading');
        chartHost.textContent = 'History is unavailable right now.';
      });
    };
    for (const [days, text] of RANGES) {
      const b = node('button', 'btn-toggle' + (days === current ? ' active' : ''), text);
      b.type = 'button';
      b.addEventListener('click', () => {
        current = days;
        tabs.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
        render();
      });
      tabs.appendChild(b);
    }
    render();
    return box;
  }

  function load(id) {
    if (!history.has(id)) {
      const p = getJSON(`/api/stations/${encodeURIComponent(id)}/readings?days=${HISTORY_DAYS}`);
      p.catch(() => history.delete(id));
      history.set(id, p);
    }
    return history.get(id);
  }

  // Forget cached history when new readings arrive.
  function invalidate() { history.clear(); }

  function open(map, s) {
    L.popup({ className: 'stn-popup', maxWidth: 360, minWidth: 320, autoPanPadding: [24, 24] })
      .setLatLng([s.lat, s.lon])
      .setContent(content(s))
      .openOn(map);
  }

  return { open, summary, invalidate };
})();
