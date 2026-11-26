(function () {
  'use strict';

  const dataEl = document.getElementById('employee-reports-data');
  if (!dataEl) return;

  let REPORT = null;
  try {
    REPORT = JSON.parse(dataEl.textContent || '{}');
  } catch (e) {
    console.error('Failed to parse employee reports payload:', e);
    return;
  }

  const TIER_COLORS = {
    HOT: '#d61f3c',
    WARM: '#e5971a',
    INTERESTED: '#2558a8',
    COLD: '#7c3aed',
    INACTIVE: '#8c929d',
  };

  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function scoreColor(val) {
    if (val >= 75) return '#10b981';
    if (val >= 50) return '#3b82f6';
    if (val >= 30) return '#f59e0b';
    return '#ef4444';
  }

  // Interactive Floating Tooltip
  const tooltip = document.getElementById('er-tooltip');
  function showTip(text, x, y) {
    if (!tooltip || !text) return;
    tooltip.textContent = text;
    tooltip.hidden = false;
    const pad = 12;
    const tw = tooltip.offsetWidth || 160;
    const th = tooltip.offsetHeight || 34;
    const left = Math.min(window.innerWidth - tw - pad, Math.max(pad, x + 14));
    const top = Math.max(pad, y - th - 10);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }
  function hideTip() {
    if (tooltip) tooltip.hidden = true;
  }

  document.addEventListener('pointermove', (e) => {
    const target = e.target.closest('[data-tip]');
    if (target) {
      showTip(target.getAttribute('data-tip'), e.clientX, e.clientY);
    } else {
      hideTip();
    }
  });
  document.addEventListener('pointerleave', hideTip);

  // -------------------------------------------------------------------------
  // Reusable Interactive SVG Donut Chart Renderer
  // -------------------------------------------------------------------------
  function renderDonut(container, segments, centerLabel) {
    if (!container) return;
    const total = segments.reduce((acc, s) => acc + Math.max(0, Number(s.value || 0)), 0);
    const radius = 42;
    const circ = 2 * Math.PI * radius;

    let offset = 0;
    const arcs = total > 0
      ? segments.filter((s) => s.value > 0).map((s) => {
          const frac = s.value / total;
          const dash = Math.max(1.5, frac * circ);
          const pct = Math.round(frac * 100);
          const arcHtml =
            `<circle class="er-donut-slice" cx="60" cy="60" r="${radius}" fill="none" ` +
            `stroke="${s.color}" stroke-width="15" ` +
            `stroke-dasharray="${dash.toFixed(2)} ${circ.toFixed(2)}" ` +
            `stroke-dashoffset="${(-offset).toFixed(2)}" ` +
            `data-tip="${esc(s.label)}: ${pct}% (${s.value})"></circle>`;
          offset += dash;
          return arcHtml;
        }).join('')
      : `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="var(--er-track)" stroke-width="15"></circle>`;

    const topSeg = segments.slice().sort((a, b) => (b.value || 0) - (a.value || 0))[0];
    const centerVal = total > 0 && topSeg ? `${Math.round((topSeg.value / total) * 100)}%` : '0%';
    const centerSub = total > 0 && topSeg ? topSeg.shortLabel || topSeg.label : centerLabel;

    const legendRows = segments.map((s) => {
      const pct = total > 0 ? Math.round((s.value / total) * 100) : 0;
      return (
        `<li data-tip="${esc(s.label)}: ${pct}% (${s.value})">` +
          '<span class="er-leg-left">' +
            `<span class="er-leg-swatch" style="background:${s.color};"></span>` +
            `<span class="er-leg-name">${esc(s.label)}</span>` +
          '</span>' +
          '<span class="er-leg-bar-wrap" aria-hidden="true">' +
            `<span class="er-leg-bar-fill" style="width:${pct}%;background:${s.color};"></span>` +
          '</span>' +
        '</li>'
      );
    }).join('');

    container.setAttribute(
      'aria-label',
      `${centerLabel}: ` + segments.map((s) => `${s.label} ${s.value}`).join(', ')
    );
    container.innerHTML =
      '<div class="er-donut-layout">' +
        '<div class="er-donut-svg-wrap">' +
          `<svg class="er-donut-svg" viewBox="0 0 120 120" aria-hidden="true">` +
            `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="var(--er-track)" stroke-width="15"></circle>` +
            arcs +
          '</svg>' +
          '<div class="er-donut-center">' +
            `<span class="er-donut-center-val">${esc(centerVal)}</span>` +
            `<span class="er-donut-center-lbl">${esc(centerSub)}</span>` +
          '</div>' +
        '</div>' +
        `<ul class="er-donut-legend">${legendRows}</ul>` +
      '</div>';
  }

  // -------------------------------------------------------------------------
  // Reusable Multi-Series Interactive SVG Area/Line Trend Chart
  // -------------------------------------------------------------------------
  function renderMultiAreaChart(container, labels, seriesList) {
    if (!container) return;
    const n = labels.length || 1;
    const W = 640;
    const H = 220;
    const padL = 34;
    const padR = 14;
    const padT = 18;
    const padB = 28;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    let maxV = 1;
    seriesList.forEach((s) => {
      (s.values || []).forEach((v) => {
        if (v > maxV) maxV = v;
      });
    });

    const xAt = (i) => (n === 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW);
    const yAt = (v) => padT + plotH - (Math.max(0, v) / maxV) * plotH;

    // Grid lines
    let gridHtml = '';
    for (let g = 0; g <= 4; g++) {
      const y = padT + (g / 4) * plotH;
      gridHtml += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--er-border)" stroke-dasharray="3 3" stroke-width="1"/>`;
    }

    // X-axis labels (at most 7 ticks)
    const step = Math.max(1, Math.ceil(n / 7));
    let xLabelsHtml = '';
    for (let i = 0; i < n; i += step) {
      xLabelsHtml += `<text x="${xAt(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" fill="var(--er-muted)" font-size="10" font-weight="600">${esc(labels[i])}</text>`;
    }
    if (n > 1 && (n - 1) % step !== 0) {
      xLabelsHtml += `<text x="${xAt(n - 1).toFixed(1)}" y="${H - 6}" text-anchor="end" fill="var(--er-muted)" font-size="10" font-weight="600">${esc(labels[n - 1])}</text>`;
    }

    let defsHtml = '<defs>';
    let pathsHtml = '';
    seriesList.forEach((s, sIdx) => {
      const gid = `er-grad-${sIdx}-${Math.random().toString(36).slice(2, 6)}`;
      defsHtml +=
        `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">` +
          `<stop offset="0%" stop-color="${s.color}" stop-opacity="0.34"/>` +
          `<stop offset="100%" stop-color="${s.color}" stop-opacity="0.02"/>` +
        '</linearGradient>';
      const vals = s.values || [];
      if (!vals.length) return;
      const pts = vals.map((v, i) => `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`);
      const lineD = 'M ' + pts.join(' L ');
      const areaD = `${lineD} L ${xAt(vals.length - 1).toFixed(1)},${(padT + plotH).toFixed(1)} L ${xAt(0).toFixed(1)},${(padT + plotH).toFixed(1)} Z`;
      pathsHtml += `<path d="${areaD}" fill="url(#${gid})"/>`;
      pathsHtml += `<path d="${lineD}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`;
    });
    defsHtml += '</defs>';

    // Interactive hover points
    let dotsHtml = '';
    for (let i = 0; i < n; i++) {
      const tipParts = seriesList.map((s) => `${s.name}: ${(s.values && s.values[i]) || 0}`);
      const tip = `${labels[i]} · ${tipParts.join(' · ')}`;
      const primaryY = yAt((seriesList[0].values && seriesList[0].values[i]) || 0);
      dotsHtml +=
        `<circle cx="${xAt(i).toFixed(1)}" cy="${primaryY.toFixed(1)}" r="4.5" fill="${seriesList[0].color}" stroke="var(--er-surface)" stroke-width="2" style="cursor:pointer;" data-tip="${esc(tip)}"/>`;
    }

    container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${defsHtml}${gridHtml}${pathsHtml}${dotsHtml}${xLabelsHtml}</svg>`;
  }

  // -------------------------------------------------------------------------
  // Sparkline SVG for Employee Cards
  // -------------------------------------------------------------------------
  function buildSparklineSvg(values, color, labels, unitLabel) {
    const arr = Array.isArray(values) && values.length ? values : [0, 0];
    const W = 260;
    const H = 40;
    const maxV = Math.max(1, ...arr);
    const n = arr.length;
    const pts = arr.map((v, i) => {
      const x = n === 1 ? W / 2 : (i / (n - 1)) * (W - 8) + 4;
      const y = H - 5 - (Math.max(0, v) / maxV) * (H - 12);
      return [x, y, v, labels[i] || ''];
    });
    const line = 'M ' + pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' L ');
    const area = `${line} L ${pts[pts.length - 1][0].toFixed(1)},${H - 2} L ${pts[0][0].toFixed(1)},${H - 2} Z`;
    const tip = `${unitLabel} peak: ${maxV}`;
    return (
      `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(tip)}" data-tip="${esc(tip)}">` +
        `<path d="${area}" fill="${color}" fill-opacity="0.16"/>` +
        `<path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` +
      '</svg>'
    );
  }

  // -------------------------------------------------------------------------
  // Team Top Charts
  // -------------------------------------------------------------------------
  const team = REPORT.team || {};
  const employees = Array.isArray(REPORT.employees) ? REPORT.employees : [];
  const labels = REPORT.range_labels || [];

  renderDonut(
    document.getElementById('chart-team-temps'),
    [
      { label: 'Hot (80–100)', shortLabel: 'Hot', value: (team.lead_temperatures || {}).HOT || 0, color: TIER_COLORS.HOT },
      { label: 'Warm (60–79)', shortLabel: 'Warm', value: (team.lead_temperatures || {}).WARM || 0, color: TIER_COLORS.WARM },
      { label: 'Interested', shortLabel: 'Interest', value: (team.lead_temperatures || {}).INTERESTED || 0, color: TIER_COLORS.INTERESTED },
      { label: 'Cold (20–39)', shortLabel: 'Cold', value: (team.lead_temperatures || {}).COLD || 0, color: TIER_COLORS.COLD },
      { label: 'Inactive', shortLabel: 'Inactive', value: (team.lead_temperatures || {}).INACTIVE || 0, color: TIER_COLORS.INACTIVE },
    ],
    'Leads'
  );

  renderDonut(
    document.getElementById('chart-team-calls'),
    [
      { label: 'Interested / Won', shortLabel: 'Won', value: (team.call_outcomes || {}).interested || 0, color: '#10b981' },
      { label: 'Callback / Follow-up', shortLabel: 'Follow-up', value: (team.call_outcomes || {}).callback || 0, color: '#3b82f6' },
      { label: 'No Answer / Busy', shortLabel: 'Unans.', value: (team.call_outcomes || {}).unanswered || 0, color: '#f59e0b' },
      { label: 'Not Interested', shortLabel: 'Rejected', value: (team.call_outcomes || {}).rejected || 0, color: '#ef4444' },
      { label: 'Other / Logged', shortLabel: 'Other', value: (team.call_outcomes || {}).other || 0, color: '#8b5cf6' },
    ],
    'Calls'
  );

  renderDonut(
    document.getElementById('chart-team-attendance'),
    [
      { label: 'Full Shift', shortLabel: 'Present', value: (team.attendance || {}).present_full || 0, color: '#10b981' },
      { label: 'Short Shift', shortLabel: 'Short', value: (team.attendance || {}).short_shift || 0, color: '#f59e0b' },
      { label: 'On Leave', shortLabel: 'Leave', value: (team.attendance || {}).leave || 0, color: '#3b82f6' },
      { label: 'Absent', shortLabel: 'Absent', value: (team.attendance || {}).absent || 0, color: '#ef4444' },
    ],
    'Shifts'
  );

  renderDonut(
    document.getElementById('chart-team-sentiment'),
    [
      { label: 'Positive AI', shortLabel: 'Positive', value: (team.ai_sentiment || {}).POSITIVE || 0, color: '#10b981' },
      { label: 'Neutral AI', shortLabel: 'Neutral', value: (team.ai_sentiment || {}).NEUTRAL || 0, color: '#3b82f6' },
      { label: 'Hesitant AI', shortLabel: 'Hesitant', value: (team.ai_sentiment || {}).HESITANT || 0, color: '#f59e0b' },
      { label: 'Negative AI', shortLabel: 'Negative', value: (team.ai_sentiment || {}).NEGATIVE || 0, color: '#ef4444' },
    ],
    'AI Calls'
  );

  const daily = team.daily_trend || {};
  renderMultiAreaChart(
    document.getElementById('chart-daily-trend'),
    daily.labels || labels,
    [
      { name: 'Calls', values: daily.calls || [], color: '#6366f1' },
      { name: 'Connected', values: daily.connected || [], color: '#10b981' },
      { name: 'Active Staff', values: daily.present || [], color: '#f59e0b' },
    ]
  );

  // Employee Horizontal Performance Bar Comparison
  const barsMount = document.getElementById('chart-employee-bars');
  if (barsMount) {
    if (!employees.length) {
      barsMount.innerHTML = '<div class="er-empty-state">No employee data in this range.</div>';
    } else {
      barsMount.innerHTML = employees.map((emp) => {
        const col = scoreColor(emp.overall_score);
        const tip = `${emp.name} (${emp.role_display}) · Visual Index ${emp.overall_score}% · Calls: ${emp.calls.total} · Attendance: ${emp.attendance_pct}%`;
        return (
          `<button type="button" class="er-bar-btn" data-open-emp="${emp.id}" data-tip="${esc(tip)}">` +
            `<span class="er-bar-emp-name">${esc(emp.name)}</span>` +
            '<span class="er-bar-track" aria-hidden="true">' +
              `<span class="er-bar-fill" style="width:${Math.max(4, emp.overall_score)}%;background:${col};"></span>` +
            '</span>' +
            `<span class="er-bar-badge" style="color:${col};">${emp.overall_score}%</span>` +
          '</button>'
        );
      }).join('');
    }
  }

  // -------------------------------------------------------------------------
  // Render Every Employee's 360° Visual Scorecard
  // -------------------------------------------------------------------------
  const empGrid = document.getElementById('er-employee-grid');
  const emptyState = document.getElementById('er-empty-state');

  function renderEmployeeWall(filterQuery) {
    if (!empGrid) return;
    const q = (filterQuery || '').trim().toLowerCase();
    const list = q
      ? employees.filter((e) =>
          (e.name || '').toLowerCase().includes(q)
          || (e.role_display || '').toLowerCase().includes(q)
          || (e.username || '').toLowerCase().includes(q)
        )
      : employees;

    if (!list.length) {
      empGrid.innerHTML = '';
      if (emptyState) emptyState.hidden = false;
      return;
    }
    if (emptyState) emptyState.hidden = true;

    empGrid.innerHTML = list.map((emp) => {
      const mColor = scoreColor(emp.overall_score);
      const aColor = scoreColor(emp.attendance_pct);
      const r2Color = scoreColor(emp.ring2_pct);
      const r3Color = scoreColor(emp.ring3_pct);

      // Lead temperature stacked bar OR work-hour attendance bar
      let stackedBarHtml = '';
      if (emp.is_caller_like) {
        const t = emp.leads.temps || {};
        const lTotal = emp.leads.total || 0;
        const segs = lTotal > 0
          ? ['HOT', 'WARM', 'INTERESTED', 'COLD', 'INACTIVE'].map((k) => {
              const v = t[k] || 0;
              if (!v) return '';
              const pct = Math.round((v / lTotal) * 100);
              return `<div class="evc-stacked-seg" style="flex-grow:${v};background:${TIER_COLORS[k]};" data-tip="${k}: ${v} leads (${pct}%)"></div>`;
            }).join('')
          : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>';
        stackedBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Lead Temperature Pipeline</span><span>Avg AI ${emp.leads.avg_score}/100</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Lead temperatures for ${esc(emp.name)}">${segs}</div>` +
          '</div>';
      } else {
        const att = emp.attendance || {};
        const aTot = (att.present_full || 0) + (att.short_shift || 0) + (att.leave || 0) + (att.absent || 0);
        const segs = aTot > 0
          ? [
              { k: 'Full Shift', v: att.present_full || 0, c: '#10b981' },
              { k: 'Short Shift', v: att.short_shift || 0, c: '#f59e0b' },
              { k: 'Leave', v: att.leave || 0, c: '#3b82f6' },
              { k: 'Absent', v: att.absent || 0, c: '#ef4444' },
            ].filter((s) => s.v > 0).map((s) =>
              `<div class="evc-stacked-seg" style="flex-grow:${s.v};background:${s.c};" data-tip="${s.k}: ${s.v} days"></div>`
            ).join('')
          : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>';
        stackedBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Shift Attendance Mix</span><span>${att.total_hours || 0}h logged</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Attendance distribution for ${esc(emp.name)}">${segs}</div>` +
          '</div>';
      }

      // Secondary visual bar (Outbound/Inbound split OR Reports/Projects progress)
      let secondBarHtml = '';
      if (emp.is_caller_like) {
        const cTot = emp.calls.total || 0;
        const outPct = cTot ? Math.round((emp.calls.outbound / cTot) * 100) : 0;
        const inPct = cTot ? 100 - outPct : 0;
        secondBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Calls Flow (Out / In)</span><span>${emp.calls.talk_minutes}m talk</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Outgoing ${outPct}% and Incoming ${inPct}% calls">` +
              (cTot
                ? `<div class="evc-stacked-seg" style="flex-grow:${Math.max(0, emp.calls.outbound)};background:#6366f1;" data-tip="Outgoing calls: ${emp.calls.outbound} (${outPct}%)"></div>` +
                  `<div class="evc-stacked-seg" style="flex-grow:${Math.max(0, emp.calls.inbound)};background:#06b6d4;" data-tip="Incoming calls: ${emp.calls.inbound} (${inPct}%)"></div>`
                : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>') +
            '</div>' +
          '</div>';
      } else {
        secondBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Daily Work Reports</span><span>${emp.tasks.report_pct}%</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Work reports completion ${emp.tasks.report_pct}%">` +
              `<div class="evc-stacked-seg" style="width:${emp.tasks.report_pct}%;background:#6366f1;" data-tip="Reports submitted: ${emp.tasks.work_reports}"></div>` +
            '</div>' +
          '</div>';
      }

      const sparkHtml = buildSparklineSvg(emp.sparkline, mColor, labels, emp.spark_label);

      return (
        `<article class="emp-visual-card" data-emp-id="${emp.id}">` +
          '<header class="evc-header">' +
            '<div class="evc-id">' +
              `<span class="evc-avatar">${esc(emp.initials)}${emp.active_shift ? '<span class="evc-live-dot" title="Active Shift Now"></span>' : ''}</span>` +
              '<div class="evc-name-wrap">' +
                `<h3 class="evc-name" title="${esc(emp.name)}">${esc(emp.name)}</h3>` +
                `<span class="evc-role-pill">${esc(emp.role_display)}</span>` +
              '</div>' +
            '</div>' +
            `<div class="evc-master-ring" role="img" aria-label="Overall visual score ${emp.overall_score}%" style="--p:${emp.overall_score}%;--c:${mColor};" data-tip="Overall Visual Score: ${emp.overall_score}%">` +
              `<div class="evc-master-ring-in">${emp.overall_score}</div>` +
            '</div>' +
          '</header>' +

          '<div class="evc-mini-rings">' +
            '<div class="evc-mini-ring-item">' +
              `<div class="evc-ring-sm" role="img" aria-label="Attendance ${emp.attendance_pct}%" style="--p:${emp.attendance_pct}%;--c:${aColor};" data-tip="Attendance: ${emp.attendance_pct}% (${emp.attendance.present_full} full, ${emp.attendance.short_shift} short)">` +
                `<div class="evc-ring-sm-in">${emp.attendance_pct}%</div>` +
              '</div>' +
              '<span class="evc-mini-lbl">Attend</span>' +
            '</div>' +
            '<div class="evc-mini-ring-item">' +
              `<div class="evc-ring-sm" role="img" aria-label="${esc(emp.ring2_label)} ${emp.ring2_pct}%" style="--p:${emp.ring2_pct}%;--c:${r2Color};" data-tip="${esc(emp.ring2_label)}: ${emp.ring2_pct}%">` +
                `<div class="evc-ring-sm-in">${emp.ring2_pct}%</div>` +
              '</div>' +
              `<span class="evc-mini-lbl">${esc(emp.ring2_label)}</span>` +
            '</div>' +
            '<div class="evc-mini-ring-item">' +
              `<div class="evc-ring-sm" role="img" aria-label="${esc(emp.ring3_label)} ${emp.ring3_pct}%" style="--p:${emp.ring3_pct}%;--c:${r3Color};" data-tip="${esc(emp.ring3_label)}: ${emp.ring3_pct}%">` +
                `<div class="evc-ring-sm-in">${emp.ring3_pct}%</div>` +
              '</div>' +
              `<span class="evc-mini-lbl">${esc(emp.ring3_label)}</span>` +
            '</div>' +
          '</div>' +

          stackedBarHtml +
          secondBarHtml +

          `<div class="evc-spark-box">${sparkHtml}</div>` +

          '<footer class="evc-footer">' +
            `<button type="button" class="evc-open-btn" data-open-emp="${emp.id}">Inspect Full Visual Charts →</button>` +
          '</footer>' +
        '</article>'
      );
    }).join('');
  }

  renderEmployeeWall('');

  const searchInp = document.getElementById('er-emp-search');
  if (searchInp) {
    searchInp.addEventListener('input', () => renderEmployeeWall(searchInp.value));
  }

  // -------------------------------------------------------------------------
  // In-Page Employee 360° Visual Report Modal (<dialog>)
  // -------------------------------------------------------------------------
  const dialog = document.getElementById('emp-360-dialog');
  const dlgAvatar = document.getElementById('emp-dialog-avatar');
  const dlgTitle = document.getElementById('emp-dialog-title');
  const dlgSub = document.getElementById('emp-dialog-subtitle');
  const dlgBody = document.getElementById('emp-dialog-body');

  if (dialog) {
    dialog.addEventListener('close', () => {
      dialog.returnValue = '';
    });
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      dialog.addEventListener('pointerdown', (e) => { pressedOutside = outside(e); });
      dialog.addEventListener('pointerup', (e) => { if (pressedOutside && outside(e)) dialog.close(); });
    }
  }

  function openEmployeeModal(empId) {
    const emp = employees.find((e) => e.id === empId);
    if (!emp || !dialog || !dlgBody) return;

    if (dlgAvatar) dlgAvatar.textContent = emp.initials;
    if (dlgTitle) dlgTitle.textContent = `${emp.name} — 360° Visual Report`;
    if (dlgSub) {
      dlgSub.textContent = `${emp.role_display} · Visual Performance Index ${emp.overall_score}/100${emp.active_shift ? ' · Active Shift Online' : ''}`;
    }

    const taskBars = [
      { label: 'Attendance', pct: emp.attendance_pct, color: '#10b981', tip: `${emp.attendance.present_full} full / ${emp.attendance.short_shift} short / ${emp.attendance.absent} absent` },
      { label: 'Work Reports', pct: emp.tasks.report_pct, color: '#6366f1', tip: `${emp.tasks.work_reports} reports submitted` },
      { label: 'Call Connect', pct: emp.calls.connect_pct, color: '#0ea5e9', tip: `${emp.calls.connected} connected out of ${emp.calls.total} calls` },
      { label: 'Follow-ups', pct: emp.followups.completion_pct, color: '#f59e0b', tip: `${emp.followups.completed} done, ${emp.followups.overdue} overdue` },
      { label: 'Lead Avg Score', pct: emp.leads.avg_score, color: '#d61f3c', tip: `Average 100-point AI score across ${emp.leads.total} assigned leads` },
      { label: 'Projects Done', pct: emp.tasks.project_pct, color: '#8b5cf6', tip: `${emp.tasks.projects.completed} completed, ${emp.tasks.projects.in_progress} active` },
    ];
    if (emp.tasks.pc_active_pct > 0) {
      taskBars.push({ label: 'PC Activity', pct: emp.tasks.pc_active_pct, color: '#14b8a6', tip: `${emp.tasks.pc_active_minutes}m active vs ${emp.tasks.pc_idle_minutes}m idle` });
    }

    const taskBarsHtml = taskBars.map((b) =>
      `<div class="er-task-bar-row" data-tip="${esc(b.tip)}">` +
        `<span>${esc(b.label)}</span>` +
        '<div class="er-task-bar-track" aria-hidden="true">' +
          `<div class="er-task-bar-fill" style="width:${Math.max(2, b.pct)}%;background:${b.color};"></div>` +
        '</div>' +
        `<span style="text-align:right;color:${b.color};font-weight:800;">${b.pct}%</span>` +
      '</div>'
    ).join('');

    dlgBody.innerHTML =
      '<div class="er-modal-grid">' +
        '<article class="er-chart-card er-modal-wide">' +
          '<header class="er-chart-head">' +
            `<h2>${emp.is_caller_like ? 'Daily Calls &amp; Connected Trend' : 'Daily Work Hours Cadence'}</h2>` +
            `<span class="er-chart-tag">${emp.is_caller_like ? `${emp.calls.talk_minutes}m Talk` : `${emp.attendance.total_hours}h Logged`}</span>` +
          '</header>' +
          '<div id="modal-emp-trend" class="er-svg-chart-mount"></div>' +
        '</article>' +

        '<article class="er-chart-card">' +
          '<header class="er-chart-head"><h2>Attendance Breakdown</h2><span class="er-chart-tag">Shifts</span></header>' +
          '<div id="modal-emp-att" class="er-donut-mount"></div>' +
        '</article>' +

        '<article class="er-chart-card">' +
          `<header class="er-chart-head"><h2>${emp.is_caller_like ? 'Assigned Lead Temperatures' : 'Projects &amp; Deliverables'}</h2><span class="er-chart-tag">Visual</span></header>` +
          '<div id="modal-emp-pipe" class="er-donut-mount"></div>' +
        '</article>' +

        '<article class="er-chart-card">' +
          `<header class="er-chart-head"><h2>${emp.is_caller_like ? 'Call Outcomes &amp; AI Quality' : 'Follow-ups &amp; Support'}</h2><span class="er-chart-tag">AI</span></header>` +
          '<div id="modal-emp-calls" class="er-donut-mount"></div>' +
        '</article>' +

        '<article class="er-chart-card">' +
          '<header class="er-chart-head"><h2>All Dimensions Progress</h2><span class="er-chart-tag">360°</span></header>' +
          `<div class="er-task-bars">${taskBarsHtml}</div>` +
        '</article>' +
      '</div>';

    // Populate modal SVG charts
    if (emp.is_caller_like) {
      renderMultiAreaChart(
        document.getElementById('modal-emp-trend'),
        labels,
        [
          { name: 'Calls', values: emp.calls.daily_calls || [], color: '#6366f1' },
          { name: 'Connected', values: emp.calls.daily_connected || [], color: '#10b981' },
        ]
      );
      renderDonut(
        document.getElementById('modal-emp-pipe'),
        [
          { label: 'Hot (80–100)', shortLabel: 'Hot', value: (emp.leads.temps || {}).HOT || 0, color: TIER_COLORS.HOT },
          { label: 'Warm (60–79)', shortLabel: 'Warm', value: (emp.leads.temps || {}).WARM || 0, color: TIER_COLORS.WARM },
          { label: 'Interested', shortLabel: 'Interest', value: (emp.leads.temps || {}).INTERESTED || 0, color: TIER_COLORS.INTERESTED },
          { label: 'Cold (20–39)', shortLabel: 'Cold', value: (emp.leads.temps || {}).COLD || 0, color: TIER_COLORS.COLD },
          { label: 'Inactive', shortLabel: 'Inactive', value: (emp.leads.temps || {}).INACTIVE || 0, color: TIER_COLORS.INACTIVE },
        ],
        'Leads'
      );
      renderDonut(
        document.getElementById('modal-emp-calls'),
        [
          { label: 'Interested / Won', shortLabel: 'Won', value: (emp.calls.outcomes || {}).interested || 0, color: '#10b981' },
          { label: 'Callback', shortLabel: 'Callback', value: (emp.calls.outcomes || {}).callback || 0, color: '#3b82f6' },
          { label: 'No Answer / Busy', shortLabel: 'Unans.', value: (emp.calls.outcomes || {}).unanswered || 0, color: '#f59e0b' },
          { label: 'Not Interested', shortLabel: 'Rejected', value: (emp.calls.outcomes || {}).rejected || 0, color: '#ef4444' },
          { label: 'Other', shortLabel: 'Other', value: (emp.calls.outcomes || {}).other || 0, color: '#8b5cf6' },
        ],
        'Calls'
      );
    } else {
      renderMultiAreaChart(
        document.getElementById('modal-emp-trend'),
        labels,
        [{ name: 'Work Hours', values: emp.attendance.daily_hours || [], color: '#0ea5e9' }]
      );
      const pr = emp.tasks.projects || {};
      renderDonut(
        document.getElementById('modal-emp-pipe'),
        [
          { label: 'Completed', shortLabel: 'Done', value: pr.completed || 0, color: '#10b981' },
          { label: 'In Progress', shortLabel: 'Active', value: pr.in_progress || 0, color: '#3b82f6' },
          { label: 'Assigned', shortLabel: 'Queued', value: pr.assigned || 0, color: '#f59e0b' },
          { label: 'Overdue', shortLabel: 'Late', value: pr.overdue || 0, color: '#ef4444' },
        ],
        'Projects'
      );
      renderDonut(
        document.getElementById('modal-emp-calls'),
        [
          { label: 'Reports Filed', shortLabel: 'Reports', value: emp.tasks.work_reports || 0, color: '#6366f1' },
          { label: 'WhatsApp Sent', shortLabel: 'WhatsApp', value: emp.tasks.whatsapp || 0, color: '#10b981' },
          { label: 'Demos / Couns.', shortLabel: 'Demos', value: emp.tasks.counsellings || 0, color: '#0ea5e9' },
          { label: 'Admissions', shortLabel: 'Admit', value: emp.tasks.admissions || 0, color: '#f59e0b' },
        ],
        'Output'
      );
    }

    renderDonut(
      document.getElementById('modal-emp-att'),
      [
        { label: 'Full Shift', shortLabel: 'Full', value: emp.attendance.present_full || 0, color: '#10b981' },
        { label: 'Short Shift', shortLabel: 'Short', value: emp.attendance.short_shift || 0, color: '#f59e0b' },
        { label: 'On Leave', shortLabel: 'Leave', value: emp.attendance.leave || 0, color: '#3b82f6' },
        { label: 'Absent', shortLabel: 'Absent', value: emp.attendance.absent || 0, color: '#ef4444' },
      ],
      'Shifts'
    );

    dialog.showModal();
  }

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-open-emp]');
    if (!trigger) return;
    const empId = parseInt(trigger.getAttribute('data-open-emp'), 10);
    if (empId) openEmployeeModal(empId);
  });
})();
