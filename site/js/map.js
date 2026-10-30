'use strict';

// The Cornwall map: basemap, boundary mask, waterways, railways, legend.
// Colours come from CSS variables so the map follows light/dark mode.

const CornwallMap = (() => {
  const DATA = 'data/';

  // Mainland Cornwall; Scilly is reachable by panning.
  const CORNWALL_BOUNDS = [[49.95, -5.76], [50.94, -4.16]];
  const MAX_BOUNDS = [[49.55, -6.75], [51.35, -3.55]];

  const MAJOR_RIVER_KM = 20;     // rivers at least this long are labelled first
  const MAJOR_LABEL_ZOOM = 9;    // long rivers labelled from here
  const RAIL_LABEL_ZOOM = 10;    // passenger line names from here
  const STREAM_MIN_ZOOM = 12;    // streams appear from here
  const MINOR_LABEL_ZOOM = 11;   // shorter rivers and freight lines get labels from here
  const ARROW_MIN_ZOOM = 12;     // flow arrows appear from here
  const ARROW_SPACING_KM = 2.5;

  const TILES = {
    light: ['Canvas/World_Light_Gray_Base', 'Canvas/World_Light_Gray_Reference'],
    dark: ['Canvas/World_Dark_Gray_Base', 'Canvas/World_Dark_Gray_Reference'],
  };
  const tileUrl = svc => `https://server.arcgisonline.com/ArcGIS/rest/services/${svc}/MapServer/tile/{z}/{y}/{x}`;

  let map;
  let tileTheme = null;
  let baseTiles = null;
  let refTiles = null;

  // Legend toggles. Each map part belongs to one toggle and may have a min zoom.
  const toggles = {
    boundary:  { on: true },
    rivers:    { on: true },
    streams:   { on: true },
    passenger: { on: true },
    freight:   { on: true },
    stations:  { on: true },
  };
  const parts = [];       // { layer, toggle, minZoom, z }
  const restylers = [];   // functions re-applying colours/weights
  const renderers = {};

  // Draw order on the shared canvas, bottom to top. Labels and arrows are
  // DOM markers in their own pane, so their order doesn't matter.
  const Z = { mask: 0, streams: 1, rivers: 2, boundary: 3, rail: 4, stations: 5, markers: 9 };

  function addPart(layer, toggle, z, minZoom = 0) {
    parts.push({ layer, toggle, z, minZoom });
  }

  // All interactive overlays share one canvas, which draws in the order layers
  // were added. So when the visible set changes, re-add everything in Z order
  // rather than appending, or a re-enabled layer would land on top.
  let shownKey = '';
  function updateVisibility() {
    const zoom = map.getZoom();
    const shown = parts
      .map((p, i) => ({ ...p, i }))
      .filter(p => toggles[p.toggle].on && zoom >= p.minZoom)
      .sort((a, b) => a.z - b.z || a.i - b.i);
    const key = shown.map(p => p.i).join(',');
    if (key !== shownKey) {
      shownKey = key;
      for (const p of parts) if (map.hasLayer(p.layer)) map.removeLayer(p.layer);
      for (const p of shown) p.layer.addTo(map);
    }
    const hint = $('#legend-streams-hint');
    if (hint) hint.textContent = zoom < STREAM_MIN_ZOOM ? 'zoom in' : '';
  }

  // ---------------------------------------------------------------- panes
  // One canvas for every interactive overlay. Separate canvases per pane look
  // tidier, but the topmost canvas swallows all mouse events, so nothing
  // beneath it gets a tooltip. The mask is not interactive, so it can have its
  // own canvas underneath, and place-name tiles sit above in a click-through pane.
  function makePanes() {
    map.createPane('mask').style.zIndex = 350;
    map.createPane('labels').style.zIndex = 450;
    map.getPane('mask').style.pointerEvents = 'none';
    map.getPane('labels').style.pointerEvents = 'none';
    renderers.mask = L.canvas({ pane: 'mask' });
    const shared = L.canvas({ tolerance: 5 });
    for (const name of ['streams', 'rivers', 'boundary', 'rail', 'stations']) renderers[name] = shared;
  }

  // ---------------------------------------------------------------- basemap
  function setTiles() {
    const theme = isDark() ? 'dark' : 'light';
    if (theme === tileTheme) return;
    tileTheme = theme;
    if (baseTiles) map.removeLayer(baseTiles);
    if (refTiles) map.removeLayer(refTiles);
    const [base, ref] = TILES[theme];
    baseTiles = L.tileLayer(tileUrl(base), {
      maxZoom: 16,
      attribution: 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>',
    }).addTo(map);
    // Place names sit above the overlays so towns stay readable.
    refTiles = L.tileLayer(tileUrl(ref), { maxZoom: 16, pane: 'labels' }).addTo(map);
  }

  // ---------------------------------------------------------------- helpers
  const toLatLngs = ring => ring.map(([x, y]) => [y, x]);

  function kmBetween(a, b) {
    const kx = 111.32 * Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
    return Math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * 110.57);
  }

  function partLength(coords) {
    let km = 0;
    for (let i = 1; i < coords.length; i++) km += kmBetween(coords[i - 1], coords[i]);
    return km;
  }

  function longestPart(geom) {
    const lines = geom.type === 'MultiLineString' ? geom.coordinates : [geom.coordinates];
    let best = null, bestKm = -1;
    for (const l of lines) {
      const km = partLength(l);
      if (km > bestKm) { best = l; bestKm = km; }
    }
    return best;
  }

  // The [lat, lng] a given fraction of the way along a line.
  function pointAlong(coords, frac) {
    const target = partLength(coords) * frac;
    let acc = 0;
    for (let i = 1; i < coords.length; i++) {
      const seg = kmBetween(coords[i - 1], coords[i]);
      if (acc + seg >= target) {
        const t = seg ? (target - acc) / seg : 0;
        const [x1, y1] = coords[i - 1], [x2, y2] = coords[i];
        return [y1 + (y2 - y1) * t, x1 + (x2 - x1) * t];
      }
      acc += seg;
    }
    const [x, y] = coords[coords.length - 1];
    return [y, x];
  }

  function label(latlng, text, cls) {
    return L.marker(latlng, {
      interactive: false,
      keyboard: false,
      icon: L.divIcon({ className: '', iconSize: [0, 0], html: `<div class="map-label ${cls}">${esc(text)}</div>` }),
    });
  }

  function tip(name, sub) {
    return `${esc(name)}${sub ? `<span class="tip-sub">${esc(sub)}</span>` : ''}`;
  }
  const TIP_OPTS = { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -8] };

  // Zoom-dependent line weight multiplier.
  const zoomScale = () => {
    const z = map.getZoom();
    return z >= 13 ? 1.7 : z >= 12 ? 1.45 : z >= 11 ? 1.2 : z >= 10 ? 1.05 : 0.9;
  };

  // ---------------------------------------------------------------- boundary
  function addBoundary(fc) {
    const rings = fc.features.flatMap(f => f.geometry.coordinates.map(poly => toLatLngs(poly[0])));
    const world = [[-89, -179], [-89, 179], [89, 179], [89, -179]];

    // Dim everything outside Cornwall so the county reads as the subject.
    const mask = L.polygon([world, ...rings], {
      renderer: renderers.mask, interactive: false, stroke: false, fillRule: 'evenodd',
    });
    const outline = L.polygon(rings, { renderer: renderers.boundary, interactive: false, fill: false });

    const style = () => {
      mask.setStyle({ fillColor: cssVar('--mask'), fillOpacity: parseFloat(cssVar('--mask-opacity')) || 0.5 });
      outline.setStyle({ color: cssVar('--accent'), weight: 1.4, opacity: 0.75 });
    };
    style();
    restylers.push(style);
    addPart(mask, 'boundary', Z.mask);
    addPart(outline, 'boundary', Z.boundary);
  }

  // ---------------------------------------------------------------- waterways
  function addRivers(fc) {
    const named = fc.features.filter(f => f.properties.name);
    const unnamed = fc.features.filter(f => !f.properties.name);

    const style = f => {
      const km = f.properties.length_km || 0;
      const base = !f.properties.name ? 1.0 : km >= MAJOR_RIVER_KM ? 2.4 : 1.6;
      return {
        color: cssVar('--water'), weight: base * zoomScale(),
        opacity: f.properties.name ? 0.9 : 0.65, lineCap: 'round', lineJoin: 'round',
      };
    };

    const namedLayer = L.geoJSON({ type: 'FeatureCollection', features: named }, {
      renderer: renderers.rivers, style,
      onEachFeature: (f, layer) => {
        const sub = [f.properties.kind === 'canal' ? 'canal' : null, f.properties.length_km ? fmtKm(f.properties.length_km) : null]
          .filter(Boolean).join(' · ');
        layer.bindTooltip(tip(f.properties.name, sub), TIP_OPTS);
      },
    });
    const unnamedLayer = L.geoJSON({ type: 'FeatureCollection', features: unnamed }, {
      renderer: renderers.rivers, style, interactive: false,
    });

    // Labels: long rivers always, short ones once zoomed in.
    const major = L.layerGroup(), minor = L.layerGroup();
    for (const f of named) {
      const line = longestPart(f.geometry);
      if (!line || line.length < 2) continue;
      const isMajor = f.properties.length_km >= MAJOR_RIVER_KM;
      label(pointAlong(line, 0.5), f.properties.name, `river${isMajor ? ' major' : ''}`).addTo(isMajor ? major : minor);
    }

    // Flow arrows: OSM waterways are drawn source to mouth.
    const arrows = L.layerGroup();
    for (const f of named) {
      if (f.properties.kind !== 'river') continue;
      for (const coords of f.geometry.coordinates) {
        let acc = 0, next = ARROW_SPACING_KM / 2;
        for (let i = 1; i < coords.length; i++) {
          const a = coords[i - 1], b = coords[i];
          const seg = kmBetween(a, b);
          while (seg > 0 && acc + seg >= next) {
            const t = (next - acc) / seg;
            const lat = a[1] + (b[1] - a[1]) * t, lng = a[0] + (b[0] - a[0]) * t;
            const bearing = Math.atan2((b[0] - a[0]) * Math.cos(lat * Math.PI / 180), b[1] - a[1]) * 180 / Math.PI;
            L.marker([lat, lng], {
              interactive: false, keyboard: false,
              icon: L.divIcon({
                className: '', iconSize: [10, 10], iconAnchor: [5, 5],
                html: `<div class="flow-arrow" style="transform: rotate(${bearing.toFixed(0)}deg)">&#x25B2;</div>`,
              }),
            }).addTo(arrows);
            next += ARROW_SPACING_KM;
          }
          acc += seg;
        }
      }
    }

    restylers.push(() => { namedLayer.setStyle(style); unnamedLayer.setStyle(style); });
    addPart(unnamedLayer, 'rivers', Z.rivers);
    addPart(namedLayer, 'rivers', Z.rivers);
    addPart(major, 'rivers', Z.markers, MAJOR_LABEL_ZOOM);
    addPart(minor, 'rivers', Z.markers, MINOR_LABEL_ZOOM);
    addPart(arrows, 'rivers', Z.markers, ARROW_MIN_ZOOM);
    return named.length;
  }

  function addStreams(fc) {
    const style = () => ({
      color: cssVar('--water-minor'), weight: 0.9 * zoomScale(), opacity: 0.85, lineCap: 'round', lineJoin: 'round',
    });
    const named = L.geoJSON({ type: 'FeatureCollection', features: fc.features.filter(f => f.properties.name) }, {
      renderer: renderers.streams, style,
      onEachFeature: (f, layer) => layer.bindTooltip(tip(f.properties.name, 'stream'), TIP_OPTS),
    });
    const unnamed = L.geoJSON({ type: 'FeatureCollection', features: fc.features.filter(f => !f.properties.name) }, {
      renderer: renderers.streams, style, interactive: false,
    });
    restylers.push(() => { named.setStyle(style); unnamed.setStyle(style); });
    addPart(unnamed, 'streams', Z.streams, STREAM_MIN_ZOOM);
    addPart(named, 'streams', Z.streams, STREAM_MIN_ZOOM);
  }

  // ---------------------------------------------------------------- railways
  const RAIL_KIND = {
    passenger: { colour: '--rail',          sub: 'passenger line' },
    freight:   { colour: '--rail-freight',  sub: 'freight branch' },
  };

  function addRailways(fc) {
    for (const kind of Object.keys(RAIL_KIND)) {
      const feats = { type: 'FeatureCollection', features: fc.features.filter(f => f.properties.kind === kind) };
      const group = L.layerGroup();
      const labels = L.layerGroup();

      // Passenger lines get the classic map casing: a solid line with a dashed
      // inner stripe. Freight branches are a plain thin dash.
      const cased = kind !== 'freight';
      const base = L.geoJSON(feats, {
        renderer: renderers.rail,
        onEachFeature: (f, layer) => layer.bindTooltip(tip(f.properties.line || RAIL_KIND[kind].sub, f.properties.line ? RAIL_KIND[kind].sub : ''), TIP_OPTS),
      }).addTo(group);
      const inner = cased ? L.geoJSON(feats, { renderer: renderers.rail, interactive: false }).addTo(group) : null;

      const style = () => {
        const s = zoomScale();
        if (cased) {
          base.setStyle({ color: cssVar(RAIL_KIND[kind].colour), weight: 3.4 * s, opacity: 0.95, lineCap: 'butt', lineJoin: 'round' });
          inner.setStyle({ color: cssVar('--bg-card'), weight: 1.5 * s, opacity: 1, dashArray: `${5 * s} ${5 * s}`, lineCap: 'butt' });
        } else {
          base.setStyle({ color: cssVar(RAIL_KIND[kind].colour), weight: 1.6 * s, opacity: 0.9, dashArray: '4 4', lineCap: 'butt' });
        }
      };
      style();
      restylers.push(style);

      for (const f of feats.features) {
        if (!f.properties.line) continue;
        const line = longestPart(f.geometry);
        if (line && line.length > 1) label(pointAlong(line, 0.5), f.properties.line, `rail ${kind}`).addTo(labels);
      }
      addPart(group, kind, Z.rail);
      addPart(labels, kind, Z.markers, kind === 'freight' ? MINOR_LABEL_ZOOM + 1 : RAIL_LABEL_ZOOM);
    }
  }

  function addStations(fc) {
    const layer = L.geoJSON(fc, {
      pointToLayer: (f, latlng) => L.circleMarker(latlng, { renderer: renderers.stations }),
      onEachFeature: (f, l) => {
        const p = f.properties;
        const sub = p.crs ? `station · ${p.crs}` : 'station';
        l.bindTooltip(tip(p.name, sub), { ...TIP_OPTS, sticky: false });
      },
    });
    const style = () => layer.eachLayer(m => {
      m.setStyle({
        radius: 3.6 * zoomScale(),
        color: cssVar('--rail'), weight: 1.6,
        fillColor: cssVar('--station-fill'), fillOpacity: 1, opacity: 1,
      });
    });
    style();
    restylers.push(style);
    addPart(layer, 'stations', Z.stations, 9);
    return fc.features.length;
  }

  // ---------------------------------------------------------------- legend
  function addLegend(counts) {
    const ctl = L.control({ position: 'bottomleft' });
    ctl.onAdd = () => {
      const div = L.DomUtil.create('div', 'map-legend');
      const item = (key, swatch, colour, text, extra = '') =>
        `<button type="button" class="legend-item" data-key="${key}" aria-pressed="true">
           <span class="swatch ${swatch}" style="--c: var(${colour})"></span>${esc(text)}${extra}
         </button>`;
      // Collapsed by default on phones, where the full legend hides the map.
      const collapsed = window.matchMedia('(max-width: 640px)').matches;
      div.classList.toggle('collapsed', collapsed);
      div.innerHTML = `
        <button type="button" class="legend-head" aria-expanded="${!collapsed}" aria-controls="legend-body">
          Layers<span class="legend-chevron" aria-hidden="true"></span>
        </button>
        <div class="legend-body" id="legend-body">
        <div class="legend-title">Waterways</div>
        ${item('rivers', 'line', '--water', 'Rivers', `<span class="count">${fmtInt(counts.rivers)}</span>`)}
        ${item('streams', 'line thin', '--water-minor', 'Streams', '<span class="count" id="legend-streams-hint"></span>')}
        <div class="legend-title">Railways</div>
        ${item('passenger', 'line', '--rail', 'Passenger lines')}
        ${item('freight', 'dash', '--rail-freight', 'Freight branches')}
        ${item('stations', 'dot', '--rail', 'Stations', `<span class="count">${fmtInt(counts.stations)}</span>`)}
        <div class="legend-title">Boundary</div>
        ${item('boundary', 'area', '--accent', 'Cornwall')}
        </div>`;
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      div.addEventListener('click', e => {
        const head = e.target.closest('.legend-head');
        if (head) {
          const isCollapsed = div.classList.toggle('collapsed');
          head.setAttribute('aria-expanded', String(!isCollapsed));
          return;
        }
        const btn = e.target.closest('.legend-item');
        if (!btn) return;
        const t = toggles[btn.dataset.key];
        t.on = !t.on;
        btn.classList.toggle('off', !t.on);
        btn.setAttribute('aria-pressed', String(t.on));
        updateVisibility();
      });
      return div;
    };
    ctl.addTo(map);
  }

  // ---------------------------------------------------------------- public
  async function init(id) {
    map = L.map(id, {
      // Whole zoom levels only: fractional zoom leaves seams between tiles.
      zoomSnap: 1, zoomDelta: 1, minZoom: 8, maxZoom: 16,
      // No tile fade: old tiles are only cleared once a fade completes, and
      // the label tiles are transparent, so a stalled fade shows doubled names.
      fadeAnimation: false,
      maxBounds: MAX_BOUNDS, maxBoundsViscosity: 0.8,
    });
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    map.attributionControl.addAttribution('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors');
    map.setView([50.45, -4.95], 9, { animate: false });  // placeholder until the container has a size
    fitIfVisible();
    makePanes();
    setTiles();

    const load = name => getJSON(DATA + name).catch(e => { console.warn(e); return null; });
    const [boundary, rivers, streams, railways, stations] = await Promise.all(
      ['boundary.geojson', 'rivers.geojson', 'streams.geojson', 'railways.geojson', 'stations.geojson'].map(load));

    const counts = { rivers: 0, stations: 0 };
    if (boundary) addBoundary(boundary);
    if (streams) addStreams(streams);
    if (rivers) counts.rivers = addRivers(rivers);
    if (railways) addRailways(railways);
    if (stations) counts.stations = addStations(stations);
    addLegend(counts);
    updateVisibility();

    map.on('zoomend', () => { updateVisibility(); restylers.forEach(fn => fn()); });
  }

  function restyle() {
    if (!map) return;
    setTiles();
    restylers.forEach(fn => fn());
  }

  // Fit Cornwall the first time the map is visible. Fitting while the view is
  // hidden (zero size) would zoom all the way in.
  let fitted = false;
  // Zoom is whole levels only, and flooring the fit leaves Cornwall small, so
  // round to the nearest level instead: a slight crop beats a tiny county.
  function fitIfVisible() {
    if (fitted || !map || map.getSize().x === 0) return;
    const snap = map.options.zoomSnap;
    map.options.zoomSnap = 0;
    const exact = map.getBoundsZoom(CORNWALL_BOUNDS);
    map.options.zoomSnap = snap;
    map.setView(L.latLngBounds(CORNWALL_BOUNDS).getCenter(), Math.round(exact), { animate: false });
    fitted = true;
  }

  function resize() {
    if (!map) return;
    setTimeout(() => { map.invalidateSize(); fitIfVisible(); }, 0);
  }

  // `map` is exposed read-only for debugging from the console.
  return { init, restyle, resize, get map() { return map; } };
})();
