'use strict';
const menu = document.querySelector('.menu-toggle');
menu?.addEventListener('click', () => {
  const open = document.querySelector('.sidebar').classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
const notifToggle = document.getElementById('notif-toggle');
const notifPanel = document.getElementById('notif-panel');
function closeNotifications() {
  if (!notifPanel || notifPanel.hidden) return;
  notifPanel.hidden = true;
  notifToggle.setAttribute('aria-expanded', 'false');
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
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    document.querySelector('.sidebar')?.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
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
  const svg = element('svg', { viewBox: '0 0 600 235', role: 'img', 'aria-label': 'Daily calls. Exact counts are available in View chart data.' });
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
  const svg = element('svg', { viewBox: '0 0 600 235', role: 'img', 'aria-label': "Selected callers' daily call volume. Exact counts are in each point's tooltip." });
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
      const name = document.createElement('strong');
      name.textContent = message.sender_name;
      bodyWrap.append(name);
    }
    const body = document.createElement('p');
    body.textContent = message.text;
    const time = document.createElement('time');
    time.textContent = new Date(message.created_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    bodyWrap.append(body, time);
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
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    const csrf = composeForm.querySelector('[name=csrfmiddlewaretoken]').value;
    try {
      const response = await fetch(composeForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf },
        body: new URLSearchParams({ text }),
      });
      if (response.ok) appendMessage(await response.json());
      else input.value = text;
    } catch {
      input.value = text;
    }
  });
}
