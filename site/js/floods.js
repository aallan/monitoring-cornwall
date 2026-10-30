'use strict';

// Environment Agency flood warnings and alerts: the header indicator, the
// banner under the header, and the warning areas handed to the map.

const FloodWarnings = (() => {
  const REFRESH_MS = 5 * 60 * 1000;
  const TZ = 'Europe/London';
  const CLASS = { 1: 'severe', 2: 'warning', 3: 'alert' };
  let warnings = [];

  const fmtWhen = ts => new Date(ts * 1000).toLocaleString('en-GB', {
    timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  // 'One severe flood warning, 2 flood warnings and 3 flood alerts in force'
  function summaryText(list) {
    const n = level => list.filter(w => w.severity === level).length;
    const parts = [];
    if (n(1)) parts.push(plural(n(1), 'severe flood warning', 'severe flood warnings'));
    if (n(2)) parts.push(plural(n(2), 'flood warning', 'flood warnings'));
    if (n(3)) parts.push(plural(n(3), 'flood alert', 'flood alerts'));
    const text = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
    return `${text} in force in Cornwall`;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // One warning, as shown in the banner and in its map popup.
  function detail(w, withMapButton) {
    const box = el('div', `flood-item is-${CLASS[w.severity]}`);
    const head = el('div', 'flood-item-head');
    head.appendChild(el('span', 'flood-chip', w.label));
    head.appendChild(el('span', 'flood-item-area', w.area || w.description || w.id));
    box.appendChild(head);
    const meta = [w.river, w.since ? `in force since ${fmtWhen(w.since)}` : null,
      w.updated && w.updated !== w.since ? `updated ${fmtWhen(w.updated)}` : null].filter(Boolean).join(' · ');
    if (meta) box.appendChild(el('div', 'flood-item-meta', meta));
    if (w.message) box.appendChild(el('div', 'flood-item-message', w.message));
    if (withMapButton && w.geometry) {
      const b = el('button', 'btn-toggle flood-show', 'Show on map');
      b.type = 'button';
      b.addEventListener('click', () => {
        const summary = $('#flood-banner .flood-summary');
        summary.setAttribute('aria-expanded', 'false');
        $('#flood-details').hidden = true;
        if (!location.hash.startsWith('#/map')) location.hash = '#/map';
        setTimeout(() => CornwallMap.focusFlood(w.id), 50);
      });
      box.appendChild(b);
    }
    box.appendChild(el('div', 'flood-item-source', `Environment Agency flood area ${w.id}`));
    return box;
  }

  function render() {
    const pill = $('#flood-pill');
    const banner = $('#flood-banner');
    const details = $('#flood-details');
    // The banner changes the header's height, and so the map's.
    if (banner.hidden !== !warnings.length) setTimeout(() => CornwallMap.resize(), 0);
    if (!warnings.length) {
      banner.hidden = true;
      pill.hidden = false;
      pill.title = 'No Environment Agency flood warnings or alerts in force in Cornwall';
      $('.flood-pill-text', pill).textContent = 'No flood warnings';
      return;
    }
    pill.hidden = true;
    const worst = Math.min(...warnings.map(w => w.severity));
    banner.className = `flood-banner is-${CLASS[worst]}`;
    banner.hidden = false;
    $('.flood-summary-text', banner).textContent = summaryText(warnings);
    details.textContent = '';
    warnings.forEach(w => details.appendChild(detail(w, true)));
  }

  function load() {
    return getJSON('data/warnings.json')
      .then(d => {
        warnings = d.warnings || [];
        render();
        CornwallMap.setFloodWarnings(warnings);
      })
      .catch(e => console.warn('No flood warning data:', e));
  }

  function init() {
    const summary = $('#flood-banner .flood-summary');
    summary.addEventListener('click', () => {
      const open = summary.getAttribute('aria-expanded') !== 'true';
      summary.setAttribute('aria-expanded', String(open));
      $('#flood-details').hidden = !open;
    });
    load();
    setInterval(load, REFRESH_MS);
  }

  return { init, detail };
})();
