/* global L */
(() => {
  'use strict';
  const root = document.getElementById('employee-location-workspace');
  if (!root) return;
  const el = id => document.getElementById(id);
  const config = JSON.parse(el('employee-map-config').textContent);
  const map = L.map('employee-location-map').setView([22.57, 88.36], 5);
  L.tileLayer(config.tiles, {attribution: config.attribution, maxZoom: 19}).addTo(map);
  const markers = L.layerGroup().addTo(map), route = L.layerGroup().addTo(map);
  let page = 1, selected = null, rows = [], fetching = false, lastRefresh = 0, socket;
  let routeNext = null, routePoints = [], routeRequest = null, routeVersion = 0;
  let fallbackTimer, socketTimer, refreshTimer;
  const text = value => value == null ? 'Not available' : String(value);
  const date = value => value ? new Date(value).toLocaleString() : 'Not shared';
  const cell = value => { const td = document.createElement('td'); td.textContent = text(value); return td; };
  const read = async (url, signal) => {
    // Pagination URLs are accepted only from our own authenticated API origin.
    const target = new URL(url, window.location.origin);
    if (target.origin !== window.location.origin) throw new Error('Invalid location data address.');
    const response = await fetch(target, {credentials:'same-origin', cache:'no-store', signal});
    if (!response.ok) {
      const denied = response.status === 403 || response.status === 401;
      if (denied) {
        markers.clearLayers(); route.clearLayers(); rows = []; routePoints = []; selected = null;
        el('location-employees').replaceChildren(); el('location-route-points').replaceChildren();
        el('location-selected').textContent = 'Administrator access required'; el('location-detail').textContent = '';
        el('location-activity').hidden = true; el('location-route-more').hidden = true;
      }
      throw new Error(denied ? 'Administrator access is required. Please sign in again.' : 'Could not load locations. Please try again.');
    }
    return response.json();
  };
  function showDetails() {
    if (!selected) return;
    el('location-selected').textContent = selected.name;
    el('location-activity').href = selected.activity_url;
    el('location-activity').hidden = false;
    el('location-detail').textContent = `${selected.role} | ${selected.status} | ${selected.location_state} | Last seen: ${date(selected.last_seen_at)} | Accuracy: ${text(selected.accuracy)} m | Speed: ${text(selected.speed)} m/s`;
  }
  function select(row) {
    selected = row; showDetails();
    if (row.latitude != null) map.setView([row.latitude, row.longitude], 15);
    loadRoute(false);
  }
  async function refresh() {
    if (fetching || document.hidden) return;
    fetching = true;
    try {
      const url = new URL(root.dataset.liveUrl, window.location.origin);
      url.searchParams.set('page', page);
      const data = await read(url);
      rows = data.results;
      el('location-employees').replaceChildren(); markers.clearLayers();
      rows.forEach(row => {
        const tr = document.createElement('tr'), name = document.createElement('td'), button = document.createElement('button');
        button.type = 'button'; button.textContent = row.name; button.onclick = () => select(row); name.append(button);
        tr.append(name, cell(`${row.status} / ${row.location_state}`), cell(date(row.last_seen_at)));
        el('location-employees').append(tr);
        if (row.latitude != null) {
          const label = document.createElement('span'); label.textContent = `${row.name} (${row.status})`;
          L.circleMarker([row.latitude, row.longitude], {radius:7, color:row.status === 'LIVE' ? '#078566' : '#797e89'}).bindTooltip(label).on('click', () => select(row)).addTo(markers);
        }
        if (selected?.employee === row.employee) { selected = row; showDetails(); }
      });
      el('location-prev').disabled = !data.previous; el('location-next').disabled = !data.next;
      el('location-page-number').textContent = `Page ${page} (${data.count} employees)`;
      el('location-page-status').textContent = `Updated ${new Date().toLocaleTimeString()}. No recent point does not establish whether GPS or connectivity is unavailable.`;
      lastRefresh = Date.now();
    } catch (error) { el('location-page-status').textContent = error.message; }
    finally { fetching = false; }
  }
  function drawRoute() {
    route.clearLayers(); el('location-route-points').replaceChildren();
    let segment = [], previous = null;
    const draw = () => {
      if (segment.length > 1) L.polyline(segment, {color:'#7566e8', weight:4}).addTo(route);
      else if (segment.length) L.circleMarker(segment[0], {radius:4, color:'#7566e8'}).addTo(route);
      segment = [];
    };
    routePoints.forEach(point => {
      if (previous && (previous.session_id !== point.session_id || Date.parse(point.recorded_at) - Date.parse(previous.recorded_at) > 300000)) draw();
      segment.push([point.latitude, point.longitude]); previous = point;
      const tr = document.createElement('tr');
      tr.append(cell(date(point.recorded_at)), cell(point.latitude), cell(point.longitude), cell(`${point.accuracy} m`), cell(point.speed == null ? null : `${point.speed} m/s`));
      el('location-route-points').append(tr);
    });
    draw();
    if (routePoints.length) map.fitBounds(L.latLngBounds(routePoints.map(p => [p.latitude,p.longitude])), {maxZoom:16, padding:[20,20]});
    el('location-route-count').textContent = `${routePoints.length} points loaded`;
  }
  async function loadRoute(more) {
    if (!selected) { el('location-route-status').textContent = 'Select an employee first.'; return; }
    routeRequest?.abort(); routeRequest = new AbortController();
    const version = ++routeVersion;
    if (!more) { routePoints = []; routeNext = null; drawRoute(); }
    if (routePoints.length >= 10000) return;
    el('location-route-more').hidden = true;
    el('location-route-status').textContent = 'Loading recorded route...';
    try {
      const url = new URL(root.dataset.historyUrl, window.location.origin);
      url.searchParams.set('employee', selected.employee); url.searchParams.set('date', el('location-date').value); url.searchParams.set('page_size', '500');
      const data = await read(more ? routeNext : url, routeRequest.signal);
      if (version !== routeVersion) return;
      routePoints.push(...data.results); routeNext = data.next; drawRoute();
      el('location-route-more').hidden = !routeNext || routePoints.length >= 10000;
      el('location-route-status').textContent = `${routePoints.length} of ${data.count} recorded points. ${routePoints.length >= 10000 && routeNext ? 'Map limit reached; use a shorter time range in the history API.' : routeNext ? 'Load more to extend the displayed route.' : 'All points for this date are shown.'}`;
    } catch (error) { if (error.name !== 'AbortError') el('location-route-status').textContent = error.message; }
  }
  el('location-route-form').onsubmit = event => { event.preventDefault(); loadRoute(false); };
  el('location-route-more').onclick = () => loadRoute(true);
  el('location-prev').onclick = () => { page = Math.max(1,page-1); refresh(); };
  el('location-next').onclick = () => { page++; refresh(); };
  function connect() {
    socket = new WebSocket(`${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/employee-locations/`);
    socket.onmessage = () => {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(refresh, Math.max(0, 5000 - (Date.now() - lastRefresh)));
    };
    socket.onclose = () => { socketTimer = setTimeout(connect, 10000); };
  }
  connect(); refresh();
  // Refresh age labels even without new GPS data; polling also covers a disconnected socket.
  fallbackTimer = setInterval(() => { if (Date.now() - lastRefresh >= 10000) refresh(); }, 10000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('pagehide', () => { clearInterval(fallbackTimer); clearTimeout(socketTimer); clearTimeout(refreshTimer); routeRequest?.abort(); socket.onclose = null; socket.close(); });
})();
