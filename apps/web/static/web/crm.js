'use strict';
const menu = document.querySelector('.menu-toggle');
menu?.addEventListener('click', () => {
  const open = document.querySelector('.sidebar').classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    document.querySelector('.sidebar')?.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
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

const dataNode = document.getElementById('dashboard-data');
if (dataNode) {
  const data = JSON.parse(dataNode.textContent);
  const ns = 'http://www.w3.org/2000/svg';
  const element = (tag, attrs, text) => {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
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
