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
  const CSO_QUIET_ZOOM = 11;     // quiet and offline storm overflows appear from here
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
  let legendReady = false;
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
    roads:     { on: true },
    levels:    { on: true },
    tidal:     { on: true },
    rain:      { on: true },
    csoNow:    { on: true },
    csoRecent: { on: true },
    csoQuiet:  { on: true },
    floods:    { on: true },
  };
  // Legend sections whose heading switches the whole group.
  const GROUPS = {
    floods: ['floods'],
    live: ['levels', 'tidal', 'rain'],
    overflows: ['csoNow', 'csoRecent', 'csoQuiet'],
    waterways: ['rivers', 'streams'],
    railways: ['passenger', 'freight', 'stations'],
    roads: ['roads'],
  };
  const parts = [];       // { layer, toggle, minZoom, z }
  const restylers = [];   // functions re-applying colours/weights
  const renderers = {};

  // Draw order on the shared canvas, bottom to top. Labels and arrows are
  // DOM markers in their own pane, so their order doesn't matter.
  const Z = { mask: 0, streams: 1, rivers: 2, floods: 2.5, roads: 3, boundary: 4, rail: 5, stations: 6, markers: 9 };

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
    const quiet = $('#legend-cso-quiet-hint');
    if (quiet) quiet.style.display = zoom < CSO_QUIET_ZOOM ? '' : 'none';
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
    for (const name of ['streams', 'rivers', 'roads', 'boundary', 'rail', 'stations']) renderers[name] = shared;
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

  // Marker size multiplier, read by the stylesheet as --mk: markers grow as
  // the map zooms in, where there are fewer of them on screen at once.
  const markerScale = z => (z >= 14 ? 2 : z >= 13 ? 1.75 : z >= 12 ? 1.5 : z >= 11 ? 1.3 : z >= 10 ? 1.15 : 1);
  function sizeMarkers() {
    map.getContainer().style.setProperty('--mk', markerScale(map.getZoom()));
  }

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

  // ---------------------------------------------------------------- roads
  const ROAD_LABEL_SPACING_KM = 18;

  function addRoads(fc) {
    const group = L.layerGroup();
    const opts = {
      renderer: renderers.roads,
      onEachFeature: (f, layer) => layer.bindTooltip(tip(f.properties.ref, 'major road'), TIP_OPTS),
    };
    // A darker edge under a lighter fill, so the road reads against the
    // basemap without competing with the railways drawn above it.
    const edge = L.geoJSON(fc, opts).addTo(group);
    const fill = L.geoJSON(fc, { renderer: renderers.roads, interactive: false }).addTo(group);
    const style = () => {
      const s = zoomScale();
      edge.setStyle({ color: cssVar('--road-edge'), weight: 4 * s, opacity: 0.9, lineCap: 'round', lineJoin: 'round' });
      fill.setStyle({ color: cssVar('--road'), weight: 2.2 * s, opacity: 1, lineCap: 'round', lineJoin: 'round' });
    };
    style();
    restylers.push(style);

    // Number badges at the midpoints of the longer stretches, spaced out so
    // each road is labelled every ROAD_LABEL_SPACING_KM or so.
    const labels = L.layerGroup();
    for (const f of fc.features) {
      const placed = [];
      const stretches = f.geometry.coordinates
        .map(c => ({ c, km: partLength(c) }))
        .filter(p => p.km >= 2)
        .sort((a, b) => b.km - a.km);
      for (const { c } of stretches) {
        const at = pointAlong(c, 0.5);
        const pt = [at[1], at[0]];
        if (placed.some(q => kmBetween(q, pt) < ROAD_LABEL_SPACING_KM)) continue;
        placed.push(pt);
        label(at, f.properties.ref, 'road').addTo(labels);
      }
    }
    addPart(group, 'roads', Z.roads);
    addPart(labels, 'roads', Z.markers, 9);
  }

  // ---------------------------------------------------------------- live stations
  // DOM markers, so their shapes and colours come straight from the
  // stylesheet: circles for river levels, diamonds for tidal gauges, and
  // drops for rain gauges.
  const LIVE_REFRESH_MS = 5 * 60 * 1000;
  const liveGroups = { level: L.layerGroup(), tidal: L.layerGroup(), rain: L.layerGroup() };
  let liveRegistered = false;

  function renderLive(live) {
    if (!liveRegistered) {
      addPart(liveGroups.level, 'levels', Z.markers);
      addPart(liveGroups.tidal, 'tidal', Z.markers);
      addPart(liveGroups.rain, 'rain', Z.markers);
      liveRegistered = true;
    }
    Object.values(liveGroups).forEach(g => g.clearLayers());
    const counts = { level: 0, tidal: 0, rain: 0 };
    for (const s of live.stations) {
      if (!liveGroups[s.kind]) continue;
      counts[s.kind]++;
      const icon = L.divIcon({
        className: '', iconSize: [14, 14], iconAnchor: [7, 7],
        html: `<span class="stn stn-${s.kind} is-${esc(s.state)}"></span>`,
      });
      L.marker([s.lat, s.lon], { icon, title: s.name, riseOnHover: true })
        .bindTooltip(tip(s.name, StationPopup.summary(s)), { ...TIP_OPTS, sticky: false, offset: [0, -10] })
        .on('click', () => StationPopup.open(map, s))
        .addTo(liveGroups[s.kind]);
    }
    setCount('levels', counts.level);
    setCount('tidal', counts.tidal);
    setCount('rain', counts.rain);
    StationPopup.invalidate();
    if (window.onLiveUpdate) window.onLiveUpdate(live);
  }

  // Storm overflows: spilling now or recently shows at every zoom; quiet and
  // offline overflows only once zoomed in, since there are hundreds of them.
  const csoGroups = { now: L.layerGroup(), recent: L.layerGroup(), quiet: L.layerGroup() };
  let csoRegistered = false;
  const CSO_GROUP = { spilling: 'now', recent: 'recent', quiet: 'quiet', offline: 'quiet' };

  function renderOverflows(data) {
    if (!csoRegistered) {
      addPart(csoGroups.quiet, 'csoQuiet', Z.markers, CSO_QUIET_ZOOM);
      addPart(csoGroups.recent, 'csoRecent', Z.markers);
      addPart(csoGroups.now, 'csoNow', Z.markers);
      csoRegistered = true;
    }
    Object.values(csoGroups).forEach(g => g.clearLayers());
    const counts = { now: 0, recent: 0, quiet: 0 };
    for (const o of data.overflows) {
      const group = CSO_GROUP[o.state];
      if (!group) continue;
      counts[group]++;
      const icon = L.divIcon({
        className: '', iconSize: [12, 12], iconAnchor: [6, 6],
        html: `<span class="cso is-${esc(o.state)}"></span>`,
      });
      L.marker([o.lat, o.lon], { icon, title: o.name || o.id, riseOnHover: true,
        zIndexOffset: group === 'now' ? 400 : group === 'recent' ? 200 : 0 })
        .bindTooltip(tip(o.name || `Storm overflow ${o.id}`, StationPopup.overflowSummary(o)),
          { ...TIP_OPTS, sticky: false, offset: [0, -8] })
        .on('click', () => StationPopup.openOverflow(map, o))
        .addTo(csoGroups[group]);
    }
    setCount('csoNow', counts.now);
    setCount('csoRecent', counts.recent);
    setCount('csoQuiet', counts.quiet);
  }

  function loadOverflows() {
    return getJSON(DATA + 'overflows.json')
      .then(d => { renderOverflows(d); updateVisibility(); })
      .catch(e => console.warn('No storm overflow data:', e));
  }

  function loadLive() {
    return getJSON(DATA + 'live.json')
      .then(live => { renderLive(live); updateVisibility(); })
      .catch(e => console.warn('No live readings:', e));
  }

  // ---------------------------------------------------------------- flood warnings
  // Areas with an Environment Agency flood warning or alert in force, tinted
  // by severity. Warning areas are mostly narrow strips of floodplain, so they
  // draw above the rivers, which show through the fill. FloodWarnings (floods.js) loads
  // the data, which may arrive before or after the map starts.
  const FLOOD_CLASS = { 1: 'severe', 2: 'warning', 3: 'alert' };
  let floodWarnings = [];
  let floodLayer = null;
  const floodBounds = {};

  function floodStyle(w) {
    const colour = cssVar(`--flood-${FLOOD_CLASS[w.severity]}`);
    return { color: colour, fillColor: colour, weight: 2 * zoomScale(), opacity: 0.95,
      fillOpacity: w.severity === 3 ? 0.2 : 0.35, lineJoin: 'round' };
  }

  function drawFloods() {
    if (!floodLayer) {
      floodLayer = L.layerGroup();
      addPart(floodLayer, 'floods', Z.floods);
      // Each warning is a GeoJSON group; its polygons carry the warning.
      restylers.push(() => floodLayer.eachLayer(group => group.eachLayer(l => l.setStyle(floodStyle(l.warning)))));
    }
    floodLayer.clearLayers();
    // Least severe first, so the most severe draws on top.
    for (const w of [...floodWarnings].sort((a, b) => b.severity - a.severity)) {
      if (!w.geometry) continue;
      const area = L.geoJSON({ type: 'Feature', geometry: w.geometry, properties: {} },
        { renderer: renderers.rivers, style: floodStyle(w) });
      area.eachLayer(l => { l.warning = w; });
      area.bindTooltip(tip(w.area || w.id, w.label), TIP_OPTS);
      area.on('click', e => L.popup({ className: 'stn-popup', maxWidth: 380, minWidth: 300, autoPanPadding: [24, 24] })
        .setLatLng(e.latlng).setContent(FloodWarnings.detail(w, false)).openOn(map));
      area.addTo(floodLayer);
      floodBounds[w.id] = area.getBounds();
    }
    setCount('floods', floodWarnings.length);
    updateVisibility();
  }

  function setFloodWarnings(list) {
    floodWarnings = list || [];
    if (map && legendReady) drawFloods();
  }

  function focusFlood(id) {
    if (!map || !floodBounds[id]) return;
    if (!toggles.floods.on) {
      toggles.floods.on = true;
      syncLegend($('.map-legend'));
      updateVisibility();
    }
    map.fitBounds(floodBounds[id], { padding: [40, 40], maxZoom: 13 });
  }

  // ---------------------------------------------------------------- legend
  function setCount(key, n) {
    const span = document.getElementById(`legend-count-${key}`);
    if (span) span.textContent = fmtInt(n);
  }

  function addLegend() {
    const ctl = L.control({ position: 'bottomleft' });
    ctl.onAdd = () => {
      const div = L.DomUtil.create('div', 'map-legend');
      const item = (key, swatch, colour, text, extra = '') =>
        `<button type="button" class="legend-item" data-key="${key}" aria-pressed="true">
           <span class="swatch ${swatch}" style="--c: var(${colour})"></span>${esc(text)}${extra}
         </button>`;
      const title = (group, text) =>
        `<button type="button" class="legend-title legend-group" data-group="${group}"
           title="Show or hide all ${esc(text.toLowerCase())}">${esc(text)}</button>`;
      // Collapsed by default on phones, where the full legend hides the map.
      const collapsed = window.matchMedia('(max-width: 640px)').matches;
      div.classList.toggle('collapsed', collapsed);
      div.innerHTML = `
        <button type="button" class="legend-head" aria-expanded="${!collapsed}" aria-controls="legend-body">
          Layers<span class="legend-chevron" aria-hidden="true"></span>
        </button>
        <div class="legend-body" id="legend-body">
        ${title('floods', 'Flood warnings')}
        ${item('floods', 'area flood-swatch', '--flood-warning', 'Warning and alert areas', '<span class="count" id="legend-count-floods"></span>')}
        ${title('live', 'Live readings')}
        ${item('levels', 'gauge level', '--level-normal', 'River levels', '<span class="count" id="legend-count-levels"></span>')}
        ${item('tidal', 'gauge tidal', '--tidal', 'Tidal levels', '<span class="count" id="legend-count-tidal"></span>')}
        ${item('rain', 'gauge rain', '--rain', 'Rain gauges', '<span class="count" id="legend-count-rain"></span>')}
        ${title('overflows', 'Storm overflows')}
        ${item('csoNow', 'cso-swatch is-spilling', '--cso', 'Spilling now', '<span class="count" id="legend-count-csoNow"></span>')}
        ${item('csoRecent', 'cso-swatch is-recent', '--cso-recent', 'Spilled in last 48 h', '<span class="count" id="legend-count-csoRecent"></span>')}
        ${item('csoQuiet', 'cso-swatch is-quiet', '--cso-quiet', 'Quiet or offline', '<span class="count"><span id="legend-cso-quiet-hint">zoom in · </span><span id="legend-count-csoQuiet"></span></span>')}
        ${title('waterways', 'Waterways')}
        ${item('rivers', 'line', '--water', 'Rivers', '<span class="count" id="legend-count-rivers"></span>')}
        ${item('streams', 'line thin', '--water-minor', 'Streams', '<span class="count" id="legend-streams-hint"></span>')}
        ${title('railways', 'Railways')}
        ${item('passenger', 'line', '--rail', 'Passenger lines')}
        ${item('freight', 'dash', '--rail-freight', 'Freight branches')}
        ${item('stations', 'dot', '--rail', 'Stations', '<span class="count" id="legend-count-stations"></span>')}
        ${title('roads', 'Roads')}
        ${item('roads', 'road', '--road', 'Major roads')}
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
        // A group heading turns the whole group off if any of it is showing,
        // otherwise back on.
        const groupBtn = e.target.closest('.legend-group');
        if (groupBtn) {
          const keys = GROUPS[groupBtn.dataset.group];
          const on = !keys.some(k => toggles[k].on);
          keys.forEach(k => { toggles[k].on = on; });
        } else {
          const btn = e.target.closest('.legend-item');
          if (!btn) return;
          const t = toggles[btn.dataset.key];
          t.on = !t.on;
        }
        syncLegend(div);
        updateVisibility();
      });
      return div;
    };
    ctl.addTo(map);
  }

  function syncLegend(div) {
    div.querySelectorAll('.legend-item').forEach(btn => {
      const on = toggles[btn.dataset.key].on;
      btn.classList.toggle('off', !on);
      btn.setAttribute('aria-pressed', String(on));
    });
    div.querySelectorAll('.legend-group').forEach(btn => {
      btn.classList.toggle('off', !GROUPS[btn.dataset.group].some(k => toggles[k].on));
    });
  }

  // ---------------------------------------------------------------- public
  function init(id) {
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
    map.attributionControl.addAttribution('Readings: <a href="https://environment.data.gov.uk/flood-monitoring/doc/reference">Environment Agency</a>');
    map.attributionControl.addAttribution('Overflows: <a href="https://www.arcgis.com/home/item.html?id=cabfce76b72a4a278a33d737c0708d42">South West Water</a>');
    map.setView([50.45, -4.95], 9, { animate: false });  // placeholder until the container has a size
    fitIfVisible();
    makePanes();
    setTiles();

    addLegend();
    legendReady = true;
    fitLegend();
    map.on('resize', fitLegend);
    drawFloods();
    map.on('zoomend', () => { loadStreamsIfNeeded(); sizeMarkers(); updateVisibility(); restylers.forEach(fn => fn()); });
    sizeMarkers();

    // Each layer draws as soon as its file arrives; nothing waits for the
    // rest. Live readings are a single small file, so markers appear first.
    loadLive();
    loadOverflows();
    setInterval(() => { loadLive(); loadOverflows(); }, LIVE_REFRESH_MS);
    const layer = (name, add) => getJSON(DATA + name)
      .then(fc => { add(fc); updateVisibility(); })
      .catch(e => console.warn(e));
    layer('boundary.geojson', addBoundary);
    layer('rivers.geojson', fc => setCount('rivers', addRivers(fc)));
    layer('roads.geojson', addRoads);
    layer('railways.geojson', addRailways);
    layer('stations.geojson', fc => setCount('stations', addStations(fc)));
    loadStreamsIfNeeded();
  }

  // Streams are the largest overlay and only show from STREAM_MIN_ZOOM, so
  // they load the first time the map reaches that zoom.
  let streamsRequested = false;
  function loadStreamsIfNeeded() {
    if (streamsRequested || map.getZoom() < STREAM_MIN_ZOOM) return;
    streamsRequested = true;
    getJSON(DATA + 'streams.geojson')
      .then(fc => { addStreams(fc); updateVisibility(); })
      .catch(e => { streamsRequested = false; console.warn(e); });
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
    setTimeout(() => { map.invalidateSize(); fitIfVisible(); fitLegend(); }, 0);
  }

  // Keep the legend clear of the zoom buttons: no taller than the map, less
  // the zoom control and the margins around both.
  const LEGEND_CLEARANCE = 100;
  function fitLegend() {
    const legend = map && map.getContainer().querySelector('.map-legend');
    if (legend) legend.style.setProperty('--legend-max', `${Math.max(120, map.getSize().y - LEGEND_CLEARANCE)}px`);
  }

  // `map` is exposed read-only for debugging from the console.
  return { init, restyle, resize, setFloodWarnings, focusFlood, get map() { return map; } };
})();
