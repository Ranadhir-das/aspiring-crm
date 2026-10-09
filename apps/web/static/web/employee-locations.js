/* global L */
(() => {
  'use strict';
  const root = document.getElementById('employee-location-workspace');
  if (!root) return;
  const el = id => document.getElementById(id);
  const config = JSON.parse(el('employee-map-config').textContent);
  const map = L.map('employee-location-map', {preferCanvas:true}).setView([22.57,88.36],5);
  const tiles = L.tileLayer(config.tiles, {attribution:config.attribution, maxZoom:19,
    referrerPolicy:'strict-origin-when-cross-origin'});
  tiles.on('tileerror', () => { el('location-map-status').textContent = 'Base map tiles are unavailable. Recorded points and the timeline remain usable. Check the configured provider and browser Network panel.'; });
  tiles.addTo(map);
  const markers = L.layerGroup().addTo(map), route = L.layerGroup().addTo(map), playback = L.layerGroup().addTo(map);
  let page=1, selected=null, mode='live', liveVersion=0, liveRequest, lastRefresh=0, socket, closed=false;
  let routeNext=null, routePoints=[], routeRequest, routeVersion=0, routeKey='', playIndex=0, playTimer=null;
  let activeTimelineRow=null;
  let socketTimer, refreshTimer, searchTimer;
  const text = value => value == null ? 'Unknown' : String(value);
  const date = value => value ? new Date(value).toLocaleString(undefined,{timeZone:config.timezone}) : 'Not reported';
  const cell = value => { const td=document.createElement('td'); td.textContent=text(value); return td; };
  const label = value => { const span=document.createElement('span'); span.textContent=value; return span; };
  const read = async (url, signal) => {
    const target=new URL(url,window.location.origin);
    if (target.origin !== window.location.origin) throw new Error('Invalid location data address.');
    const response=await fetch(target,{credentials:'same-origin',cache:'no-store',signal});
    if (!response.ok) {
      if ([401,403].includes(response.status)) {
        resetRoute(); markers.clearLayers(); selected=null; el('location-employees').replaceChildren();
        el('location-selected').textContent='Administrator access required'; el('location-detail').textContent='';
        el('location-activity').hidden=true; el('location-route-status').textContent='Administrator access required.';
        throw new Error('Administrator access is required. Please sign in again.');
      }
      if (response.status===400) throw new Error('Invalid date/time range. Check the start and end times (ambiguous daylight-saving times require explicit API offsets).');
      throw new Error('Could not load locations. Please try again.');
    }
    return response.json();
  };
  function pause() { clearTimeout(playTimer); playTimer=null; el('location-play').textContent='Play'; }
  function resetRoute() {
    pause(); routeRequest?.abort(); ++routeVersion; routeNext=null; routePoints=[]; routeKey=''; playIndex=0; activeTimelineRow=null;
    route.clearLayers(); playback.clearLayers(); el('location-route-points').replaceChildren();
    el('location-route-count').textContent=''; el('location-distance').textContent=''; el('location-playback-detail').textContent='';
    el('location-route-more').hidden=true; el('location-play').disabled=true; el('location-scrub').disabled=true;
    el('location-scrub').value='0'; el('location-scrub').max='0';
  }
  function details() {
    if (!selected) return;
    el('location-selected').textContent=selected.name;
    el('location-activity').href=selected.activity_url; el('location-activity').hidden=false;
    if (mode==='history') {
      el('location-detail').textContent=`Historical observations for ${selected.name}. ${config.timezone}. Live updates do not change this path.`;
      return;
    }
    el('location-detail').textContent=`${selected.role} | App ${selected.employee_online?'online':'not recently foregrounded'} | Tracking: ${selected.location_state} ${selected.tracking_reason||''} (reported ${date(selected.state_reported_at)}) | Position: ${selected.status}${selected.current_session_point?'':' — last known, previous/no active session'} | Captured: ${date(selected.last_seen_at)} | Age: ${selected.age_seconds==null?'unknown':Math.floor(selected.age_seconds)+' s'} | Accuracy: ${text(selected.accuracy)} m | Last server sync: ${date(selected.last_sync_at)}. Network/GPS state cannot be inferred from silence.`;
  }
  function setMode(next) {
    if (mode!==next) resetRoute();
    mode=next; markers.clearLayers();
    el('location-live').setAttribute('aria-pressed',String(mode==='live'));
    el('location-history').setAttribute('aria-pressed',String(mode==='history'));
    el('location-route-status').textContent=mode==='live'?'Live view: positions are last observations, not guaranteed current locations.':'Select an employee, date and optional times, then View recorded path.';
    details(); if (mode==='live') refresh();
  }
  function select(row) {
    resetRoute(); selected=row; details();
    if (mode==='live') { if(row.latitude!=null) map.setView([row.latitude,row.longitude],15); }
    else loadRoute(false);
  }
  async function refresh() {
    if (document.hidden || closed) return;
    liveRequest?.abort(); liveRequest=new AbortController(); const version=++liveVersion;
    try {
      const url=new URL(root.dataset.liveUrl,window.location.origin);
      url.searchParams.set('page',page); url.searchParams.set('search',el('location-search').value);
      url.searchParams.set('tracking',el('location-tracking').value); url.searchParams.set('status',el('location-status-filter').value);
      const data=await read(url,liveRequest.signal); if (version!==liveVersion) return;
      el('location-employees').replaceChildren(); markers.clearLayers();
      data.results.forEach(row=>{
        const tr=document.createElement('tr'), name=document.createElement('td'), button=document.createElement('button');
        button.type='button'; button.textContent=row.name; button.onclick=()=>select(row); name.append(button);
        tr.append(name,cell(`${row.status} / ${row.tracking_reason||row.location_state}`),cell(date(row.last_seen_at)));
        el('location-employees').append(tr);
        if (mode==='live' && row.latitude!=null) L.circleMarker([row.latitude,row.longitude],{radius:7,color:row.status==='LIVE'?'#078566':'#797e89'})
          .bindTooltip(label(`${row.name}: ${row.status}, observed ${date(row.last_seen_at)}, accuracy ${row.accuracy} m`)).on('click',()=>select(row)).addTo(markers);
        if (mode==='live' && selected?.employee===row.employee) {selected=row; details();}
      });
      el('location-prev').disabled=!data.previous; el('location-next').disabled=!data.next;
      el('location-page-number').textContent=`Page ${page} (${data.count} employees)`;
      el('location-page-status').textContent=data.count?`Live list refreshed ${date(new Date())}. History stays locked to its selected range.`:'No employees match these filters.';
      lastRefresh=Date.now();
    } catch(error) {if(error.name!=='AbortError' && version===liveVersion) el('location-page-status').textContent=error.message;}
  }
  function showPoint(index) {
    if (!routePoints.length) return;
    playIndex=Math.max(0,Math.min(index,routePoints.length-1)); const point=routePoints[playIndex];
    playback.clearLayers();
    if(point.display_accepted) L.circleMarker([point.latitude,point.longitude],{radius:9,color:'#076ce2'}).bindTooltip(label(date(point.recorded_at))).addTo(playback);
    el('location-scrub').value=String(playIndex);
    el('location-playback-detail').textContent=`Point ${playIndex+1}: ${date(point.recorded_at)} | ${point.latitude}, ${point.longitude} | Accuracy ${point.accuracy} m | ${point.segment_reason.replaceAll('_',' ')}${point.display_accepted?'':' — retained raw point, excluded from map'}`;
    activeTimelineRow?.setAttribute('aria-current','false');
    activeTimelineRow=el('location-route-points').children[playIndex];
    activeTimelineRow?.setAttribute('aria-current','true');
  }
  function tick() {
    if (mode!=='history' || playIndex>=routePoints.length-1) {pause(); return;}
    el('location-play').textContent='Pause';
    const next=routePoints[playIndex+1], current=routePoints[playIndex];
    const gap=next.segment_reason!=='CONTINUOUS';
    const delay=gap?1500:Math.max(100,(Date.parse(next.recorded_at)-Date.parse(current.recorded_at))/Number(el('location-speed').value));
    if(gap) el('location-playback-detail').textContent+=` | Next: ${next.segment_reason.replaceAll('_',' ')} — no measured journey across this interval.`;
    playTimer=setTimeout(()=>{showPoint(playIndex+1);tick();},delay);
  }
  function drawRoute() {
    pause(); route.clearLayers(); playback.clearLayers(); el('location-route-points').replaceChildren();
    let segment=[], distance=0, breaks=0, filtered=0; const accepted=[];
    const draw=()=>{if(segment.length>1) L.polyline(segment,{color:'#7566e8',weight:4}).addTo(route); segment=[];};
    routePoints.forEach((point,index)=>{
      if(point.segment_reason!=='CONTINUOUS') {draw(); if(index) breaks++;}
      if(point.display_accepted) {
        segment.push([point.latitude,point.longitude]); accepted.push(point);
        L.circleMarker([point.latitude,point.longitude],{radius:3,color:'#7566e8'}).bindTooltip(label(`${date(point.recorded_at)} | ${point.accuracy} m`)).on('click',()=>{pause();showPoint(index);}).addTo(route);
        if (point.segment_reason === 'CONTINUOUS') {
            distance += point.distance_from_previous_m || 0;
        }
      } else {draw(); filtered++;}
      const tr=document.createElement('tr'), action=document.createElement('td'), button=document.createElement('button');
      button.type='button'; button.textContent='View point'; button.onclick=()=>{pause();showPoint(index);}; action.append(button);
      tr.append(cell(date(point.recorded_at)),cell(point.latitude),cell(point.longitude),cell(`${point.accuracy} m`),cell(point.speed),cell(point.segment_reason.replaceAll('_',' ')),action);
      el('location-route-points').append(tr);
    }); draw();
    if(accepted.length) {
      const first=accepted[0], last=accepted.at(-1);
      [[first,'#078566','Start'],[last,'#be3b45','End']].forEach(([p,color,name])=>L.circleMarker([p.latitude,p.longitude],{radius:7,color}).bindTooltip(label(`${name}: ${date(p.recorded_at)}`)).addTo(route));
      map.fitBounds(L.latLngBounds(accepted.map(p=>[p.latitude,p.longitude])),{maxZoom:16,padding:[20,20]});
    }
    el('location-route-count').textContent=`${routePoints.length} raw / ${accepted.length} displayed points`;
    el('location-distance').textContent=`Loaded recorded segments: ${(distance/1000).toFixed(2)} km. ${breaks} breaks, ${filtered} excluded raw points. Partial history until every page is loaded; no distance inferred across gaps.`;
    el('location-play').disabled=!routePoints.length; el('location-scrub').disabled=!routePoints.length;
    el('location-scrub').max=String(Math.max(0,routePoints.length-1)); showPoint(0);
  }
  function queryKey() {return JSON.stringify([selected?.employee,el('location-date').value,el('location-time-from').value,el('location-time-to').value]);}
  async function loadRoute(more) {
    if(!selected) {el('location-route-status').textContent='Select an employee first.'; return;}
    if(!more) {setMode('history'); resetRoute(); routeKey=queryKey();}
    if(mode!=='history' || routeKey!==queryKey() || (more&&!routeNext) || routePoints.length>=10000) return;
    pause(); routeRequest?.abort(); routeRequest=new AbortController(); const version=++routeVersion, key=routeKey;
    el('location-route-more').hidden=true; el('location-route-status').textContent='Loading recorded path…';
    try {
      const url=new URL(root.dataset.historyUrl,window.location.origin);
      url.searchParams.set('employee',selected.employee); url.searchParams.set('date',el('location-date').value); url.searchParams.set('page_size','500');
      if(el('location-time-from').value) url.searchParams.set('time_from',el('location-time-from').value);
      if(el('location-time-to').value) url.searchParams.set('time_to',el('location-time-to').value);
      const data=await read(more?routeNext:url,routeRequest.signal);
      if(version!==routeVersion || mode!=='history' || key!==queryKey()) return;
      const ids=new Set(routePoints.map(p=>p.id)); routePoints.push(...data.results.filter(p=>!ids.has(p.id)));
      routePoints.sort((a,b)=>Date.parse(a.recorded_at)-Date.parse(b.recorded_at)||a.id-b.id);
      routeNext=data.next; drawRoute();
      el('location-route-more').hidden=!routeNext || routePoints.length>=10000;
      el('location-route-status').textContent=data.count?`${date(data.start)} to ${date(data.end)} (${data.timezone}). ${routePoints.length} of ${data.count} points. ${routeNext?(routePoints.length>=10000?'10,000 point display limit; narrow the time range.':'Load more for the rest of the recorded path.'):'Complete selected range.'}`:'No recorded history for this employee and range.';
    } catch(error) {if(error.name!=='AbortError' && version===routeVersion) {el('location-route-status').textContent=error.message; el('location-route-more').hidden=!routeNext;}}
  }
  el('location-timezone').textContent=`Times: ${config.timezone}`;
  el('location-route-form').onsubmit=event=>{event.preventDefault();loadRoute(false);};
  ['location-date','location-time-from','location-time-to'].forEach(id=>{el(id).oninput=()=>{resetRoute();el('location-route-status').textContent='Range changed. Choose View recorded path to load it.';};});
  el('location-route-more').onclick=()=>loadRoute(true);
  el('location-live').onclick=()=>setMode('live'); el('location-history').onclick=()=>setMode('history');
  el('location-prev').onclick=()=>{page=Math.max(1,page-1);refresh();}; el('location-next').onclick=()=>{page++;refresh();};
  el('location-refresh').onclick=()=>refresh();
  el('location-search').oninput=()=>{liveRequest?.abort();++liveVersion;clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;refresh();},300);};
  el('location-tracking').onchange=()=>{page=1;refresh();};
  el('location-status-filter').onchange=()=>{page=1;refresh();};
  el('location-play').onclick=()=>{if(playTimer) pause();else {if(playIndex===routePoints.length-1) showPoint(0);tick();}};
  el('location-scrub').oninput=()=>{pause();showPoint(Number(el('location-scrub').value));};
  el('location-speed').onchange=()=>{if(playTimer){pause();tick();}};
  function connect() {
    if(closed) return;
    el('location-connection').textContent='Connecting live updates…';
    socket=new WebSocket(`${window.location.protocol==='https:'?'wss':'ws'}://${window.location.host}/ws/employee-locations/`);
    socket.onopen=()=>{el('location-connection').textContent='Live updates connected';refresh();};
    socket.onmessage=()=>{clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,Math.max(0,5000-(Date.now()-lastRefresh)));};
    socket.onclose=()=>{el('location-connection').textContent='Live updates disconnected; polling continues. Device GPS state unknown.';if(!closed)socketTimer=setTimeout(connect,10000);};
  }
  connect(); refresh();
  const fallbackTimer=setInterval(()=>{if(Date.now()-lastRefresh>=10000)refresh();},10000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();else pause();});
  window.addEventListener('pagehide',()=>{closed=true;pause();clearInterval(fallbackTimer);clearTimeout(socketTimer);clearTimeout(refreshTimer);clearTimeout(searchTimer);routeRequest?.abort();liveRequest?.abort();socket.onclose=null;socket.close();});
  window.addEventListener('pageshow',event=>{if(event.persisted)window.location.reload();});
})();
