'use strict';
/* Sidebar Mobile Drawer & Backdrop */
const menu = document.querySelector('.menu-toggle');
const sidebar = document.querySelector('.sidebar');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');
const sidebarClose = document.getElementById('sidebar-close');

function openSidebar() {
  sidebar?.classList.add('open');
  sidebarBackdrop?.classList.add('active');
  menu?.setAttribute('aria-expanded', 'true');
}

function closeSidebar() {
  sidebar?.classList.remove('open');
  sidebarBackdrop?.classList.remove('active');
  menu?.setAttribute('aria-expanded', 'false');
}

menu?.addEventListener('click', () => {
  if (sidebar?.classList.contains('open')) {
    closeSidebar();
  } else {
    openSidebar();
  }
});

sidebarClose?.addEventListener('click', closeSidebar);
sidebarBackdrop?.addEventListener('click', closeSidebar);

sidebar?.querySelectorAll('.nav-group-items a, .quick-pill').forEach(link => {
  link.addEventListener('click', () => {
    if (window.innerWidth <= 760) closeSidebar();
  });
});

/* Collapsible Navigation Groups with localStorage persistence */
const NAV_STATE_KEY = 'vaani_crm_sidebar_groups';

function getNavState() {
  try {
    return JSON.parse(localStorage.getItem(NAV_STATE_KEY) || '{}');
  } catch {
    return {};
  }
}

function saveNavState(state) {
  try {
    localStorage.setItem(NAV_STATE_KEY, JSON.stringify(state));
  } catch {}
}

const navGroups = document.querySelectorAll('.nav-group');
const navState = getNavState();

navGroups.forEach(group => {
  const groupId = group.getAttribute('data-group-id');
  const headerBtn = group.querySelector('.nav-group-header');
  const hasActiveChild = group.querySelector('[aria-current="page"]') !== null;

  if (groupId && navState[groupId] === false && !hasActiveChild) {
    group.classList.add('collapsed');
    headerBtn?.setAttribute('aria-expanded', 'false');
  } else {
    group.classList.remove('collapsed');
    headerBtn?.setAttribute('aria-expanded', 'true');
  }

  headerBtn?.addEventListener('click', () => {
    const searchInput = document.getElementById('nav-search-input');
    if (searchInput && searchInput.value.trim()) return;

    const isCollapsed = group.classList.toggle('collapsed');
    headerBtn.setAttribute('aria-expanded', String(!isCollapsed));
    if (groupId) {
      const current = getNavState();
      current[groupId] = !isCollapsed;
      saveNavState(current);
    }
  });
});

/* Real-time Navigation Search */
const navSearchInput = document.getElementById('nav-search-input');
const navSearchClear = document.getElementById('nav-search-clear');
const quickPills = document.getElementById('quick-pills');
const navSearchEmpty = document.getElementById('nav-search-empty');

function filterNavigation() {
  if (!navSearchInput) return;
  const q = navSearchInput.value.trim().toLowerCase();

  if (navSearchClear) {
    navSearchClear.style.display = q ? 'flex' : 'none';
  }

  if (!q) {
    if (quickPills) quickPills.style.display = '';
    if (navSearchEmpty) navSearchEmpty.style.display = 'none';

    const saved = getNavState();
    navGroups.forEach(group => {
      group.style.display = '';
      const groupId = group.getAttribute('data-group-id');
      const hasActiveChild = group.querySelector('[aria-current="page"]') !== null;
      if (groupId && saved[groupId] === false && !hasActiveChild) {
        group.classList.add('collapsed');
        group.querySelector('.nav-group-header')?.setAttribute('aria-expanded', 'false');
      } else {
        group.classList.remove('collapsed');
        group.querySelector('.nav-group-header')?.setAttribute('aria-expanded', 'true');
      }
      group.querySelectorAll('.nav-group-items a').forEach(a => {
        a.style.display = '';
      });
    });
    return;
  }

  if (quickPills) quickPills.style.display = 'none';
  let totalMatches = 0;

  navGroups.forEach(group => {
    const links = group.querySelectorAll('.nav-group-items a');
    let groupMatches = 0;

    links.forEach(a => {
      const text = a.textContent.toLowerCase();
      if (text.includes(q)) {
        a.style.display = '';
        groupMatches++;
        totalMatches++;
      } else {
        a.style.display = 'none';
      }
    });

    if (groupMatches > 0) {
      group.style.display = '';
      group.classList.remove('collapsed');
      group.querySelector('.nav-group-header')?.setAttribute('aria-expanded', 'true');
    } else {
      group.style.display = 'none';
    }
  });

  if (navSearchEmpty) {
    navSearchEmpty.style.display = totalMatches === 0 ? 'block' : 'none';
  }
}

navSearchInput?.addEventListener('input', filterNavigation);
navSearchClear?.addEventListener('click', () => {
  if (navSearchInput) {
    navSearchInput.value = '';
    filterNavigation();
    navSearchInput.focus();
  }
});

/* Notifications dropdown */
const notifToggle = document.getElementById('notif-toggle');
const notifPanel = document.getElementById('notif-panel');
function closeNotifications() {
  if (!notifPanel || notifPanel.hidden) return;
  notifPanel.hidden = true;
  notifToggle?.setAttribute('aria-expanded', 'false');
}
notifToggle?.addEventListener('click', event => {
  event.stopPropagation();
  const open = notifPanel.hidden;
  notifPanel.hidden = !open;
  notifToggle.setAttribute('aria-expanded', String(open));
});
document.addEventListener('click', event => {
  if (notifPanel && !notifPanel.hidden && !notifPanel.contains(event.target) && event.target !== notifToggle) closeNotifications();
});

/* Keyboard Shortcuts: Ctrl+K / Cmd+K and Escape */
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    if (navSearchInput) {
      navSearchInput.focus();
      navSearchInput.select();
    }
  } else if (event.key === 'Escape') {
    if (navSearchInput && (document.activeElement === navSearchInput || navSearchInput.value)) {
      navSearchInput.value = '';
      filterNavigation();
      navSearchInput.blur();
    }
    closeSidebar();
    closeNotifications();
  }
});
document.querySelectorAll('[data-auto-submit]').forEach(select => select.addEventListener('change', () => select.form.requestSubmit()));
const boxes = [...document.querySelectorAll('input[name="lead_ids"]')];
const all = document.getElementById('select-all');
function updateSelection() {
  const count = boxes.filter(box => box.checked).length;
  document.getElementById('selected-count').textContent = count;
  all.checked = boxes.length > 0 && count === boxes.length;
  all.indeterminate = count > 0 && count < boxes.length;
}
all?.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); updateSelection(); });
boxes.forEach(box => box.addEventListener('change', updateSelection));

const svgNS = 'http://www.w3.org/2000/svg';
const svgElement = (tag, attrs, text) => {
  const node = document.createElementNS(svgNS, tag);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
  if (text !== undefined) node.textContent = text;
  return node;
};

const dataNode = document.getElementById('dashboard-data');
if (dataNode) {
  const data = JSON.parse(dataNode.textContent);
  const element = svgElement;
  const svg = element('svg', { viewBox: '0 0 600 235', role: 'img', 'aria-label': 'Call volume by report interval. Exact counts are available in the chart data table.' });
  const maximum = Math.max(4, ...data.trend.map(item => item.count));
  const ceiling = Math.ceil(maximum / 4) * 4;
  const x = i => 40 + i * 538 / Math.max(1, data.trend.length - 1);
  const y = value => 190 - value / ceiling * 155;
  const defs = element('defs');
  const gradient = element('linearGradient', { id: 'call-fill', x1: '0', y1: '0', x2: '0', y2: '1' });
  gradient.append(element('stop', { offset: '0%', 'stop-color': '#a795ed', 'stop-opacity': '.24' }), element('stop', { offset: '100%', 'stop-color': '#a795ed', 'stop-opacity': '0' }));
  defs.append(gradient); svg.append(defs);
  for (let i = 0; i <= 4; i++) {
    const value = i * ceiling / 4;
    svg.append(element('line', { x1: 40, x2: 578, y1: y(value), y2: y(value), stroke: '#2a2e3c', 'stroke-dasharray': '3 5' }));
    svg.append(element('text', { x: 26, y: y(value) + 3, fill: '#8390a7', 'font-size': 9, 'text-anchor': 'end' }, value));
  }
  const points = data.trend.map((item, i) => `${x(i)},${y(item.count)}`).join(' ');
  svg.append(element('polygon', { points: `40,190 ${points} 578,190`, fill: 'url(#call-fill)' }));
  svg.append(element('polyline', { points, fill: 'none', stroke: '#b5a0ff', 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  data.trend.forEach((item, i) => {
    const dot = element('circle', { cx: x(i), cy: y(item.count), r: data.trend.length <= 7 ? 4 : 2.5, fill: '#c7baff', stroke: '#14171f', 'stroke-width': 2 });
    dot.append(element('title', {}, `${item.date}: ${item.count} calls`)); svg.append(dot);
    if (i % Math.ceil(data.trend.length / 7) === 0 || i === data.trend.length - 1) {
      svg.append(element('text', { x: x(i), y: 219, fill: '#8c97ab', 'font-size': 9, 'text-anchor': i === 0 ? 'start' : i === data.trend.length - 1 ? 'end' : 'middle' }, item.date));
    }
  });
  if (data.trend.every(item => item.count === 0)) svg.append(element('text', { x: 310, y: 108, fill: '#9fa9be', 'font-size': 12, 'text-anchor': 'middle' }, 'No calls recorded in this period'));
  document.getElementById('activity-chart').append(svg);
  const total = data.pipeline.reduce((sum, item) => sum + item.value, 0);
  let cursor = 0;
  const segments = data.pipeline.filter(item => item.value > 0).map(item => {
    const end = cursor + item.value / total * 100;
    const segment = `${item.color} ${cursor}% ${end}%`;
    cursor = end;
    return segment;
  });
  if (total) document.getElementById('pipeline-chart').style.background = `conic-gradient(${segments.join(',')})`;
}

const performanceNode = document.getElementById('performance-data');
if (performanceNode) {
  const data = JSON.parse(performanceNode.textContent);
  const element = svgElement;
  const chartHost = document.getElementById('compare-chart');
  const trend = data.trend || [], series = data.series || [];
  const svg = element('svg', { viewBox: '0 0 600 235', role: 'img', 'aria-label': "Selected callers' call volume by report interval. Exact counts are in each point's tooltip." });
  const values = trend.flatMap(point => series.map(s => point[s.id] || 0));
  const maximum = Math.max(4, ...values);
  const ceiling = Math.ceil(maximum / 4) * 4;
  const x = i => 40 + i * 538 / Math.max(1, trend.length - 1);
  const y = value => 190 - value / ceiling * 155;
  for (let i = 0; i <= 4; i++) {
    const value = i * ceiling / 4;
    svg.append(element('line', { x1: 40, x2: 578, y1: y(value), y2: y(value), stroke: '#2a2e3c', 'stroke-dasharray': '3 5' }));
    svg.append(element('text', { x: 26, y: y(value) + 3, fill: '#8390a7', 'font-size': 9, 'text-anchor': 'end' }, value));
  }
  series.forEach(s => {
    const points = trend.map((point, i) => `${x(i)},${y(point[s.id] || 0)}`).join(' ');
    svg.append(element('polyline', { points, fill: 'none', stroke: s.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    trend.forEach((point, i) => {
      const dot = element('circle', { cx: x(i), cy: y(point[s.id] || 0), r: trend.length <= 14 ? 3.5 : 2.5, fill: s.color, stroke: '#14171f', 'stroke-width': 1.5 });
      dot.append(element('title', {}, `${s.name} · ${point.date}: ${point[s.id] || 0} calls`));
      svg.append(dot);
    });
  });
  trend.forEach((point, i) => {
    if (i % Math.ceil(trend.length / 7) === 0 || i === trend.length - 1) {
      svg.append(element('text', { x: x(i), y: 219, fill: '#8c97ab', 'font-size': 9, 'text-anchor': i === 0 ? 'start' : i === trend.length - 1 ? 'end' : 'middle' }, point.date));
    }
  });
  if (!values.some(Boolean)) svg.append(element('text', { x: 310, y: 108, fill: '#9fa9be', 'font-size': 12, 'text-anchor': 'middle' }, 'No calls recorded in this period'));
  chartHost?.append(svg);
}

const compareBoxes = [...document.querySelectorAll('input[name="compare"]')];
const COMPARE_LIMIT = 4;
function enforceCompareLimit() {
  const checkedCount = compareBoxes.filter(box => box.checked).length;
  compareBoxes.forEach(box => { box.disabled = !box.checked && checkedCount >= COMPARE_LIMIT; });
}
compareBoxes.forEach(box => box.addEventListener('change', enforceCompareLimit));
enforceCompareLimit();

document.querySelectorAll("[data-print]").forEach(button => button.addEventListener("click", () => window.print()));

document.getElementById('add-invoice-item')?.addEventListener('click', () => {
  const container = document.getElementById('invoice-items');
  const total = document.querySelector('input[name="items-TOTAL_FORMS"]');
  const index = Number(total.value);
  if (index >= 50) return;
  const clone = container.lastElementChild.cloneNode(true);
  clone.querySelectorAll('[name], [id], [for]').forEach(node => {
    ['name', 'id', 'for'].forEach(attr => {
      if (node.hasAttribute(attr)) node.setAttribute(attr, node.getAttribute(attr).replace(/items-\d+-/g, `items-${index}-`));
    });
  });
  clone.querySelectorAll('input').forEach(input => {
    if (input.type === 'checkbox') input.checked = false;
    else input.value = input.name.endsWith('-quantity') ? '1' : '';
  });
  clone.querySelectorAll('.errorlist').forEach(node => node.remove());
  container.append(clone);
  total.value = String(index + 1);
});

// Team chat: live delivery over WebSocket, sending over a normal authenticated POST.
const chatThread = document.getElementById('chat-thread');
if (chatThread) {
  const channelId = chatThread.dataset.channel;
  const myId = chatThread.dataset.user;
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${proto}://${window.location.host}/ws/chat/${channelId}/`);

  const appendMessage = message => {
    if (chatThread.querySelector(`[data-message-id="${message.id}"]`)) return; // already rendered (own optimistic send)
    chatThread.querySelector('.empty')?.remove();
    const mine = String(message.sender_id) === myId;
    const row = document.createElement('div');
    row.className = 'chat-msg' + (mine ? ' mine' : '');
    row.dataset.messageId = message.id;
    const avatar = document.createElement('span');
    avatar.className = 'chat-avatar';
    avatar.textContent = message.sender_name.charAt(0).toUpperCase();
    const bodyWrap = document.createElement('div');
    bodyWrap.className = 'chat-msg-body';
    if (!mine) {
      const allowed = chatThread.dataset.canViewEmployees === 'true';
      const name = document.createElement(allowed ? 'a' : 'strong');
      if (allowed && /^\d+$/.test(String(message.sender_id))) {
        name.className = 'employee-link';
        name.href = chatThread.dataset.profileTemplate.replace('/0/', `/${message.sender_id}/`);
      }
      name.textContent = message.sender_name;
      bodyWrap.append(name);
    }
    const body = document.createElement('p');
    body.textContent = message.text;
    bodyWrap.append(body);

    if (message.attachments && message.attachments.length) {
      const attachWrap = document.createElement('div');
      attachWrap.style.marginTop = '6px';
      attachWrap.style.display = 'flex';
      attachWrap.style.flexDirection = 'column';
      attachWrap.style.gap = '6px';
      message.attachments.forEach(att => {
        const downloadUrl = `/chat/attachments/${att.id}/download/`;
        if (att.mime_type && att.mime_type.includes('image')) {
          const imgLink = document.createElement('a');
          imgLink.href = downloadUrl;
          imgLink.target = '_blank';
          imgLink.rel = 'noopener noreferrer';
          const img = document.createElement('img');
          img.src = downloadUrl;
          img.alt = att.original_name;
          img.style.maxWidth = '240px';
          img.style.maxHeight = '200px';
          img.style.borderRadius = '8px';
          img.style.objectFit = 'cover';
          img.style.display = 'block';
          imgLink.append(img);
          attachWrap.append(imgLink);
        } else if (att.mime_type && att.mime_type.includes('video')) {
          const video = document.createElement('video');
          video.controls = true;
          video.preload = 'metadata';
          video.style.maxWidth = '280px';
          video.style.maxHeight = '200px';
          video.style.borderRadius = '8px';
          video.style.display = 'block';
          const src = document.createElement('source');
          src.src = downloadUrl;
          src.type = att.mime_type;
          video.append(src);
          attachWrap.append(video);
        } else {
          const docLink = document.createElement('a');
          docLink.href = downloadUrl;
          docLink.target = '_blank';
          docLink.rel = 'noopener noreferrer';
          docLink.style.display = 'inline-flex';
          docLink.style.alignItems = 'center';
          docLink.style.gap = '6px';
          docLink.style.background = 'rgba(255,255,255,0.06)';
          docLink.style.padding = '6px 10px';
          docLink.style.borderRadius = '6px';
          docLink.style.color = 'inherit';
          docLink.style.textDecoration = 'none';
          docLink.style.fontSize = '12px';
          docLink.style.border = '1px solid rgba(255,255,255,0.1)';
          const sizeKb = (att.file_size / 1024).toFixed(0);
          docLink.innerHTML = `<span>📄</span><span>${att.original_name}</span><small style="opacity:0.7">(${sizeKb} KB)</small>`;
          attachWrap.append(docLink);
        }
      });
      bodyWrap.append(attachWrap);
    }

    const time = document.createElement('time');
    time.textContent = new Date(message.created_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    bodyWrap.append(time);
    row.append(avatar, bodyWrap);
    chatThread.append(row);
    chatThread.scrollTop = chatThread.scrollHeight;
  };

  socket.addEventListener('message', event => appendMessage(JSON.parse(event.data)));
  chatThread.scrollTop = chatThread.scrollHeight;

  const composeForm = document.getElementById('chat-compose');
  composeForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const input = document.getElementById('chat-input');
    const fileInput = document.getElementById('chat-file-input');
    const text = input.value.trim();
    const hasFiles = fileInput && fileInput.files && fileInput.files.length > 0;
    if (!text && !hasFiles) return;

    const formData = new FormData(composeForm);
    input.value = '';
    if (fileInput) fileInput.value = '';
    const preview = document.getElementById('chat-file-preview');
    if (preview) preview.style.display = 'none';

    const csrf = composeForm.querySelector('[name=csrfmiddlewaretoken]').value;
    try {
      const response = await fetch(composeForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf },
        body: formData,
      });
      if (response.ok) appendMessage(await response.json());
      else {
        input.value = text;
        const err = await response.json().catch(() => ({}));
        alert(err.detail || 'Could not send message.');
      }
    } catch {
      input.value = text;
      alert('Could not send message.');
    }
  });
}

// A signed points trend shares the exact buckets used by the calls chart and data table.
const pointsHost = document.getElementById('points-trend');
if (pointsHost && dataNode) {
  const rows = JSON.parse(dataNode.textContent).trend;
  const low = Math.min(0, ...rows.map(row => row.points));
  const high = Math.max(1, ...rows.map(row => row.points));
  const x = i => 45 + i * 520 / Math.max(1, rows.length - 1);
  const y = n => 185 - (n - low) / (high - low) * 150;
  const svg = svgElement('svg', {viewBox:'0 0 600 230', role:'img', 'aria-label':'Points by selected interval. Exact values are in the interval totals table.'});
  [low, 0, high].filter((n, i, a) => a.indexOf(n) === i).forEach(n => {
    svg.append(svgElement('line', {x1:45,x2:565,y1:y(n),y2:y(n),stroke:'#929bb0','stroke-opacity':'.25'}));
    svg.append(svgElement('text', {x:35,y:y(n)+4,fill:'#929bb0','font-size':10,'text-anchor':'end'}, n));
  });
  svg.append(svgElement('polyline', {points:rows.map((row,i)=>`${x(i)},${y(row.points)}`).join(' '),fill:'none',stroke:'#53c9ff','stroke-width':2.5}));
  rows.forEach((row,i) => {
    const dot=svgElement('circle',{cx:x(i),cy:y(row.points),r:3,fill:row.points<0?'#f287ad':'#53c9ff'});
    dot.append(svgElement('title',{},`${row.date}: ${row.points} points`));svg.append(dot);
    if(i % Math.ceil(rows.length/5) === 0 || i === rows.length-1) svg.append(svgElement('text',{x:x(i),y:215,fill:'#929bb0','font-size':9,'text-anchor':i===0?'start':i===rows.length-1?'end':'middle'},row.date));
  });
  pointsHost.append(svg);
}

const assignedLeadsNode = document.getElementById('assigned-leads-data');
if (assignedLeadsNode) {
  const rows = JSON.parse(assignedLeadsNode.textContent);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  let cursor = 0;
  const segments = rows.filter(row => row.value > 0).map(row => {
    const end = cursor + row.value / total * 100;
    const segment = `${row.color} ${cursor}% ${end}%`;
    cursor = end;
    return segment;
  });
  if (total) document.getElementById('assigned-leads-chart').style.background = `conic-gradient(${segments.join(',')})`;
}

const teamStatusNode = document.getElementById('team-status-data');
if (teamStatusNode) {
  const rows = JSON.parse(teamStatusNode.textContent);
  const total = rows.reduce((sum, row) => sum + (row.value || 0), 0);
  let cursor = 0;
  const segments = rows.filter(row => row.value > 0).map(row => {
    const end = cursor + row.value / total * 100;
    const segment = `${row.color} ${cursor}% ${end}%`;
    cursor = end;
    return segment;
  });
  const donutEl = document.getElementById('team-status-donut');
  if (donutEl && total > 0) donutEl.style.background = `conic-gradient(${segments.join(',')})`;
  const totalEl = document.getElementById('team-status-total');
  if (totalEl) totalEl.textContent = total;
}

const teamCallNode = document.getElementById('team-call-data');
if (teamCallNode) {
  const rows = JSON.parse(teamCallNode.textContent);
  const total = rows.reduce((sum, row) => sum + (row.value || 0), 0);
  let cursor = 0;
  const segments = rows.filter(row => row.value > 0).map(row => {
    const end = cursor + row.value / total * 100;
    const segment = `${row.color} ${cursor}% ${end}%`;
    cursor = end;
    return segment;
  });
  const donutEl = document.getElementById('team-call-donut');
  if (donutEl && total > 0) donutEl.style.background = `conic-gradient(${segments.join(',')})`;
  const totalEl = document.getElementById('team-call-total');
  if (totalEl) totalEl.textContent = total;
}
