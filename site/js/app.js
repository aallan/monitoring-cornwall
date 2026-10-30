'use strict';

// Hash router: #/map (default) and #/about.
const VIEWS = ['map', 'about'];
let mapStarted = false;

function route() {
  const name = (location.hash.replace(/^#\//, '') || 'map').split('/')[0];
  const view = VIEWS.includes(name) ? name : 'map';
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${view}`));
  $$('#nav .nav-btn').forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#/${view}`));
  document.body.classList.toggle('map-mode', view === 'map');
  if (view === 'map') {
    // Start the map the first time it is visible: Leaflet sizes and fits
    // itself from the container, which has no size while the view is hidden.
    if (!mapStarted) { mapStarted = true; CornwallMap.init('map'); }
    else CornwallMap.resize();
  }
}

async function loadMeta() {
  try {
    const meta = await getJSON('data/meta.json');
    $('#data-generated').textContent = `Map data downloaded from OpenStreetMap on ${fmtDate(meta.generated)}.`;
  } catch (e) {
    $('#status').textContent = 'No map data yet. Run make data.';
  }
}

// The status line reports how fresh the live readings are.
function showLiveStatus(live) {
  const newest = Math.max(0, ...live.stations.map(s => s.ts || 0));
  if (!newest) {
    $('#status').textContent = 'No live readings yet';
    return;
  }
  const when = new Date(newest * 1000).toLocaleString('en-GB', {
    timeZone: 'Europe/London', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
  const delayed = Date.now() / 1000 - newest > 3600 ? ' (delayed)' : '';
  $('#status').textContent = `Latest readings ${when}${delayed}`;
}

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  window.onThemeChange = () => CornwallMap.restyle();
  window.onLiveUpdate = showLiveStatus;
  window.addEventListener('hashchange', route);
  route();
  loadMeta();
  FloodWarnings.init();
});
