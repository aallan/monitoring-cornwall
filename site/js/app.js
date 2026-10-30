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
    const parts = [
      `OpenStreetMap snapshot ${fmtDate(meta.generated)}`,
      `${fmtInt(meta.counts.rivers)} named rivers`,
      `${fmtInt(meta.counts.stations)} stations`,
    ];
    $('#status').textContent = parts.join(' · ');
    $('#data-generated').textContent = `Map data downloaded from OpenStreetMap on ${fmtDate(meta.generated)}.`;
  } catch (e) {
    $('#status').textContent = 'No map data yet. Run make data.';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  window.onThemeChange = () => CornwallMap.restyle();
  window.addEventListener('hashchange', route);
  route();
  loadMeta();
});
