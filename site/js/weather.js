'use strict';

// Met Office weather warnings for Cornwall: a banner under the header, in the
// colour of the most severe warning, which opens to show each one. The Met
// Office's terms ask for its content and links to be shown as supplied, so
// each warning's title and description appear word for word, with its link.

const WeatherWarnings = (() => {
  const REFRESH_MS = 5 * 60 * 1000;
  const TZ = 'Europe/London';
  const LEVELS = ['Yellow', 'Amber', 'Red'];
  let warnings = [];

  const fmtWhen = ts => new Date(ts * 1000).toLocaleString('en-GB', {
    timeZone: TZ, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function when(w, now) {
    if (!w.from || !w.to) return null;
    return w.from <= now ? `In force until ${fmtWhen(w.to)}` : `From ${fmtWhen(w.from)} to ${fmtWhen(w.to)}`;
  }

  function detail(w, now) {
    const box = el('div', `flood-item is-${w.level.toLowerCase()}`);
    const head = el('div', 'flood-item-head');
    head.appendChild(el('span', 'flood-chip', w.level));
    head.appendChild(el('span', 'flood-item-area', w.title));
    box.appendChild(head);
    const meta = when(w, now);
    if (meta) box.appendChild(el('div', 'flood-item-meta', meta));
    box.appendChild(el('div', 'flood-item-message', w.description));
    if (/^https:\/\/(www|weather)\.metoffice\.gov\.uk\//.test(w.link || '')) {
      const a = el('a', 'weather-link', 'This warning on the Met Office site');
      a.href = w.link;
      a.rel = 'noopener';
      a.target = '_blank';
      box.appendChild(a);
    }
    box.appendChild(el('div', 'flood-item-source', 'Met Office weather warning'));
    return box;
  }

  function render() {
    const banner = $('#weather-banner');
    const details = $('#weather-details');
    // A warning that has ended stays in the file until the next poll.
    const now = Date.now() / 1000;
    const current = warnings.filter(w => !w.to || w.to > now);
    if (banner.hidden !== !current.length) setTimeout(() => CornwallMap.resize(), 0);
    if (!current.length) {
      banner.hidden = true;
      return;
    }
    const worst = LEVELS[Math.max(...current.map(w => LEVELS.indexOf(w.level)))];
    banner.className = `flood-banner is-${worst.toLowerCase()}`;
    banner.hidden = false;
    $('.flood-summary-text', banner).textContent = current.length === 1
      ? current[0].title
      : `${current.length} Met Office weather warnings for Cornwall`;
    details.textContent = '';
    current.forEach(w => details.appendChild(detail(w, now)));
  }

  function load() {
    return getJSON('data/weather.json')
      .then(d => { warnings = d.warnings || []; render(); })
      .catch(e => console.warn('No weather warning data:', e));
  }

  function init() {
    const summary = $('#weather-banner .flood-summary');
    summary.addEventListener('click', () => {
      const open = summary.getAttribute('aria-expanded') !== 'true';
      summary.setAttribute('aria-expanded', String(open));
      $('#weather-details').hidden = !open;
    });
    load();
    setInterval(load, REFRESH_MS);
  }

  return { init };
})();
