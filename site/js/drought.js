'use strict';

// Drought status in Cornwall, from the Environment Agency's weekly report: a
// pill in the header at the most severe stage, which opens to show each
// stage as the report words it, South West Water's hosepipe ban, and a link
// to the report; or "No drought", which opens to say so. A drought lasts
// months, so this stays out of the map's way.

const Drought = (() => {
  // Checked as often as the other feeds: the file is small, and a page opened
  // while the server is starting up finds it within minutes.
  const REFRESH_MS = 5 * 60 * 1000;
  const CLASS = { 'Severe drought': 'severe', Drought: 'drought', 'Prolonged dry weather': 'dry', Recovery: 'recovery' };
  const ORDER = Object.keys(CLASS);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // The most severe stage any part of Cornwall is at, or null.
  function worst(stages) {
    const known = (stages || []).map(s => s.stage).filter(s => s in CLASS);
    return known.length ? ORDER.find(s => known.includes(s)) : null;
  }

  // The pill's words: the stage, and 'in parts' when the county is split.
  function pillText(stages) {
    const top = worst(stages);
    if (!top) return null;
    const split = new Set((stages || []).map(s => s.stage)).size > 1;
    return split ? `${top} in parts` : top;
  }

  function reportLink(d) {
    return d.report && /^https:\/\/www\.gov\.uk\//.test(d.report.url || '')
      ? { text: `Dry weather and drought in England: ${d.report.period}`, href: d.report.url } : null;
  }

  function render(d) {
    const box = $('#drought');
    const top = worst(d.stages);
    const bans = d.bans || [];
    const clear = !top && !bans.length;
    const pill = $('.drought-pill', box);
    box.className = `drought pill-menu is-${clear ? 'clear' : CLASS[top] || 'ban'}`;
    $('.drought-pill-text', pill).textContent = clear ? 'No drought' : pillText(d.stages) || 'Hosepipe ban';
    pill.title = clear ? 'No part of Cornwall is in drought'
      : top ? `${pillText(d.stages)} in Cornwall` : 'Hosepipe ban in force';
    box.hidden = false;

    const details = $('#drought-details');
    if (clear) {
      PillMenus.fill(details, 'No drought in Cornwall', [
        "The Environment Agency's latest weekly report doesn't put any part of Cornwall or the Isles of Scilly in " +
        'prolonged dry weather or drought, and South West Water has no hosepipe ban in force.',
      ], 'Environment Agency · ', reportLink(d));
      return;
    }
    PillMenus.fill(details, 'Drought in Cornwall', [], 'Environment Agency · ', reportLink(d));
    const source = details.lastChild;
    for (const s of d.stages || []) {
      const item = el('div', `drought-item is-${CLASS[s.stage] || 'drought'}`);
      item.appendChild(el('span', 'drought-chip', s.stage));
      item.appendChild(el('div', 'drought-text', s.text));
      details.insertBefore(item, source);
    }
    for (const b of bans) {
      const item = el('div', 'drought-item is-ban');
      item.appendChild(el('span', 'drought-chip', 'Hosepipe ban'));
      item.appendChild(el('div', 'drought-text', b));
      details.insertBefore(item, source);
    }
  }

  function load() {
    return getJSON('data/drought.json').then(render).catch(e => console.warn('No drought data:', e));
  }

  function init() {
    PillMenus.add($('#drought'));
    load();
    setInterval(load, REFRESH_MS);
  }

  return { init, _internals: { worst, pillText } };
})();
