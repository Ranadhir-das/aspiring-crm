const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup() {
  const elements=new Map(), requests=[], timers=new Map(), groups=[], tileHandlers={}, sockets=[];let timerId=0;
  class Element {
    constructor(){this.children=[];this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.attrs={};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    setAttribute(key,value){this.attrs[key]=value;}
  }
  const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  el('employee-location-workspace').dataset={liveUrl:'/live/',historyUrl:'/history/'};
  el('employee-map-config').textContent=JSON.stringify({tiles:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',attribution:'OSM',timezone:'Asia/Kolkata'});
  el('location-date').value='2026-10-08';el('location-speed').value='60';
  const layer=(kind,coords)=>({kind,coords,bindTooltip(){return this;},on(){return this;},addTo(group){group.layers?.push(this);return this;}});
  const map={setView(){return this;},fitBounds(){return this;}};
  const L={map:()=>map,tileLayer:(url,options)=>({on(event,handler){tileHandlers[event]=handler;return this;},addTo(){return this;}}),
    layerGroup:()=>{const group={layers:[],addTo(){return this;},clearLayers(){this.layers=[];}};groups.push(group);return group;},
    circleMarker:coords=>layer('point',coords),polyline:coords=>layer('line',coords),latLngBounds:points=>points};
  class Socket {constructor(){sockets.push(this);}close(){}}
  const window={location:{origin:'https://crm.example',protocol:'https:',host:'crm.example',reload(){}},addEventListener(){}};
  const document={getElementById:el,createElement:()=>new Element(),hidden:false,addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(`${__dirname}/../apps/web/static/web/employee-locations.js`,'utf8'),{
    document,window,L,URL,AbortController,WebSocket:Socket,console,
    setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    setInterval:()=>++timerId,clearInterval(){},fetch:(url,args)=>new Promise(resolve=>requests.push({url:new URL(url),args,resolve})),
  });
  const answer=async(index,data,status=200)=>{requests[index].resolve({ok:status===200,status,json:async()=>data});await settle();};
  const employee=(id=1)=>({employee:id,name:`Employee ${id}`,role:'EMPLOYEE',status:'LIVE',latitude:22,longitude:88,accuracy:5,
    location_state:'ACTIVE',current_session_point:true,last_seen_at:'2026-10-09T06:00:00Z',last_sync_at:'2026-10-09T06:00:01Z',activity_url:'/activity/'});
  const list=(rows=[employee()])=>({results:rows,count:rows.length,next:null,previous:null});
  const select=index=>el('location-employees').children[index].children[0].children[0].onclick();
  const point=(id,reason='CONTINUOUS')=>({id,latitude:22+id/10000,longitude:88,accuracy:5,speed:0,recorded_at:`2026-10-08T06:0${id}:00Z`,
    session_id:'session',display_accepted:true,segment_reason:reason,distance_from_previous_m:reason==='CONTINUOUS'?10:0});
  const history=(points=[point(1,'START'),point(2)])=>({results:points,count:points.length,next:null,start:'2026-10-07T18:30:00Z',end:'2026-10-08T18:30:00Z',timezone:'Asia/Kolkata'});
  return {el,requests,answer,employee,list,select,point,history,timers,groups,tileHandlers,sockets};
}
test('history query uses selected employee/calendar date/time, separate from live',async()=>{
  const app=setup();await app.answer(0,app.list());app.select(0);
  assert.equal(app.requests.length,1);app.el('location-time-from').value='09:00';app.el('location-time-to').value='17:00';
  app.el('location-route-form').onsubmit({preventDefault(){}});
  const query=app.requests[1].url.searchParams;
  assert.equal(query.get('employee'),'1');assert.equal(query.get('date'),'2026-10-08');assert.equal(query.get('time_from'),'09:00');
  assert.equal(query.get('time_to'),'17:00');assert.match(app.el('location-route-status').textContent,/Loading/);
  await app.answer(1,app.history());assert.match(app.el('location-route-status').textContent,/Complete selected range/);
  assert.equal(app.groups[0].layers.length,0);
});
test('rapid date changes abort and ignore old history and pagination results',async()=>{
  const app=setup();await app.answer(0,app.list());app.select(0);app.el('location-route-form').onsubmit({preventDefault(){}});
  app.el('location-date').value='2026-10-07';app.el('location-date').oninput();
  assert.equal(app.requests[1].args.signal.aborted,true);await app.answer(1,app.history());
  assert.equal(app.el('location-route-points').children.length,0);assert.equal(app.el('location-route-more').hidden,true);
  app.el('location-route-form').onsubmit({preventDefault(){}});await app.answer(2,app.history([]));
  assert.match(app.el('location-route-status').textContent,/No recorded history/);assert.equal(app.el('location-play').disabled,true);
});
test('changing employee prevents stale route from previous employee',async()=>{
  const app=setup();await app.answer(0,app.list([app.employee(1),app.employee(2)]));
  app.el('location-history').onclick();app.select(0);app.select(1);
  assert.equal(app.requests[1].args.signal.aborted,true);await app.answer(2,app.history([app.point(3,'START')]));
  await app.answer(1,app.history());assert.match(app.el('location-selected').textContent,/Employee 2/);
  assert.equal(app.el('location-route-points').children.length,1);
});
test('live websocket refresh cannot overwrite historical path or playback',async()=>{
  const app=setup();await app.answer(0,app.list());app.select(0);app.el('location-route-form').onsubmit({preventDefault(){}});
  await app.answer(1,app.history());app.el('location-scrub').value='1';app.el('location-scrub').oninput();
  const before=app.el('location-playback-detail').textContent;
  app.sockets[0].onmessage();const pending=[...app.timers.values()].at(-1);pending.fn();
  await app.answer(2,app.list([{...app.employee(),latitude:50}]));
  assert.equal(app.el('location-playback-detail').textContent,before);assert.equal(app.el('location-route-points').children.length,2);
  assert.equal(app.groups[0].layers.length,0);assert.match(app.el('location-detail').textContent,/Historical/);
});
test('playback visits actual samples, gaps are visible, scrub and query reset pause',async()=>{
  const app=setup();await app.answer(0,app.list());app.select(0);app.el('location-route-form').onsubmit({preventDefault(){}});
  await app.answer(1,app.history([app.point(1,'START'),app.point(2,'TIME_GAP')]));app.el('location-play').onclick();
  assert.equal(app.el('location-play').textContent,'Pause');assert.match(app.el('location-playback-detail').textContent,/no measured journey/);
  const timer=[...app.timers.values()].at(-1);assert.equal(timer.delay,1500);timer.fn();
  assert.match(app.el('location-playback-detail').textContent,/Point 2/);
  assert.deepEqual(Array.from(app.groups[2].layers[0].coords),[22.0002,88]);
  app.el('location-date').oninput();assert.equal(app.el('location-play').disabled,true);assert.equal(app.groups[2].layers.length,0);
});
test('poor-quality points are retained in timeline; gaps not connected or counted',async()=>{
  const app=setup();await app.answer(0,app.list());app.select(0);app.el('location-route-form').onsubmit({preventDefault(){}});
  const points=[app.point(1,'START'),app.point(2),{...app.point(3,'POOR_ACCURACY'),display_accepted:false},app.point(4,'QUALITY_GAP')];
  await app.answer(1,app.history(points));assert.equal(app.el('location-route-points').children.length,4);
  assert.equal(app.groups[1].layers.filter(l=>l.kind==='line').length,1);assert.match(app.el('location-distance').textContent,/0.01 km/);
  assert.match(app.el('location-route-count').textContent,/4 raw \/ 3 displayed/);
});
test('tile failures show a usable fallback and API failure clears old history',async()=>{
  const app=setup();await app.answer(0,app.list());app.tileHandlers.tileerror();assert.match(app.el('location-map-status').textContent,/timeline remain usable/);
  app.select(0);app.el('location-route-form').onsubmit({preventDefault(){}});await app.answer(1,{},500);
  assert.match(app.el('location-route-status').textContent,/Could not load/);assert.equal(app.el('location-route-points').children.length,0);
});
test('permission denial removes displayed coordinates, socket reconnect refreshes API',async()=>{
  const app=setup();await app.answer(0,app.list());app.sockets[0].onopen();await app.answer(1,{},403);
  assert.equal(app.groups[0].layers.length,0);assert.equal(app.el('location-employees').children.length,0);
  assert.match(app.el('location-page-status').textContent,/Administrator access/);
});
