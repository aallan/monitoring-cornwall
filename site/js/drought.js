'use strict';

// Drought status in Cornwall, from the Environment Agency's weekly report: a
// pill in the header at the most severe stage, which opens to show each
// stage as the report words it, South West Water's hosepipe ban, and a link
// to the report. A drought lasts months, so this stays out of the map's way.

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

  function render(d) {
    const box = $('#drought');
    const top = worst(d.stages);
    if (!top && !(d.bans || []).length) {
      box.hidden = true;
      return;
    }
    const pill = $('.drought-pill', box);
    box.className = `drought is-${CLASS[top] || 'ban'}`;
    $('.drought-pill-text', pill).textContent = pillText(d.stages) || 'Hosepipe ban';
    pill.title = top ? `${pillText(d.stages)} in Cornwall` : 'Hosepipe ban in force';
    box.hidden = false;

    const details = $('#drought-details');
    details.textContent = '';
    details.appendChild(el('div', 'drought-head', 'Drought in Cornwall'));
    for (const s of d.stages || []) {
      const item = el('div', `drought-item is-${CLASS[s.stage] || 'drought'}`);
      item.appendChild(el('span', 'drought-chip', s.stage));
      item.appendChild(el('div', 'drought-text', s.text));
      details.appendChild(item);
    }
    for (const b of d.bans || []) {
      const item = el('div', 'drought-item is-ban');
      item.appendChild(el('span', 'drought-chip', 'Hosepipe ban'));
      item.appendChild(el('div', 'drought-text', b));
      details.appendChild(item);
    }
    const source = el('div', 'drought-source', 'Environment Agency, ');
    if (d.report && /^https:\/\/www\.gov\.uk\//.test(d.report.url || '')) {
      const a = el('a', null, `Dry weather and drought in England: ${d.report.period}`);
      a.href = d.report.url;
      a.target = '_blank';
      a.rel = 'noopener';
      source.appendChild(a);
    }
    details.appendChild(source);
  }

  function setOpen(open) {
    $('#drought .drought-pill').setAttribute('aria-expanded', String(open));
    $('#drought-details').hidden = !open;
  }

  function load() {
    return getJSON('data/drought.json').then(render).catch(e => console.warn('No drought data:', e));
  }

  function init() {
    const pill = $('#drought .drought-pill');
    pill.addEventListener('click', e => {
      e.stopPropagation();
      setOpen(pill.getAttribute('aria-expanded') !== 'true');
    });
    // Closes like a menu: a click elsewhere, or Escape.
    document.addEventListener('click', e => { if (!e.target.closest('#drought')) setOpen(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
    load();
    setInterval(load, REFRESH_MS);
  }

  return { init, _internals: { worst, pillText } };
})();
