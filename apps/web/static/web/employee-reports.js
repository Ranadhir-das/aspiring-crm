(function () {
  'use strict';

  const TIER_COLORS = {
    HOT: '#d61f3c',
    WARM: '#e5971a',
    INTERESTED: '#2558a8',
    COLD: '#7c3aed',
    INACTIVE: '#8c929d',
  };

  const PALETTE = ['#6366f1', '#10b981', '#0ea5e9', '#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#ef4444'];

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

  // Floating Chart Tooltip
  const tooltip = document.getElementById('er-tooltip');
  function showTip(text, x, y) {
    if (!tooltip || !text) return;
    tooltip.textContent = text;
    tooltip.hidden = false;
    const pad = 12;
    const tw = tooltip.offsetWidth || 160;
    const th = tooltip.offsetHeight || 34;
    tooltip.style.left = `${Math.min(window.innerWidth - tw - pad, Math.max(pad, x + 14))}px`;
    tooltip.style.top = `${Math.max(pad, y - th - 10)}px`;
  }
  function hideTip() {
    if (tooltip) tooltip.hidden = true;
  }
  document.addEventListener('pointermove', (e) => {
    const target = e.target.closest('[data-tip]');
    if (target) showTip(target.getAttribute('data-tip'), e.clientX, e.clientY);
    else hideTip();
  });
  document.addEventListener('pointerleave', hideTip);

  // -------------------------------------------------------------------------
  // Shared Visual Chart Renderers (SVG Donuts, Area Trends, Horizontal Bars)
  // -------------------------------------------------------------------------
  function renderDonut(container, segments, centerLabel, overrideCenter) {
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
          const html =
            `<circle class="er-donut-slice" cx="60" cy="60" r="${radius}" fill="none" ` +
            `stroke="${s.color}" stroke-width="15" ` +
            `stroke-dasharray="${dash.toFixed(2)} ${circ.toFixed(2)}" ` +
            `stroke-dashoffset="${(-offset).toFixed(2)}" ` +
            `data-tip="${esc(s.label)}: ${pct}% (${s.value})"></circle>`;
          offset += dash;
          return html;
        }).join('')
      : `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="var(--er-track)" stroke-width="15"></circle>`;

    const topSeg = segments.slice().sort((a, b) => (b.value || 0) - (a.value || 0))[0];
    const centerVal = overrideCenter && overrideCenter.value != null
      ? String(overrideCenter.value)
      : (total > 0 && topSeg ? `${Math.round((topSeg.value / total) * 100)}%` : '0%');
    const centerSub = overrideCenter && overrideCenter.label != null
      ? String(overrideCenter.label)
      : (total > 0 && topSeg ? topSeg.shortLabel || topSeg.label : centerLabel);

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

    container.setAttribute('aria-label', `${centerLabel}: ` + segments.map((s) => `${s.label} ${s.value}`).join(', '));
    container.innerHTML =
      '<div class="er-donut-layout">' +
        '<div class="er-donut-svg-wrap">' +
          '<svg class="er-donut-svg" viewBox="0 0 120 120" aria-hidden="true">' +
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

  function renderMultiAreaChart(container, labels, seriesList) {
    if (!container) return;
    const n = labels.length || 1;
    const W = 640;
    const H = 215;
    const padL = 34;
    const padR = 14;
    const padT = 16;
    const padB = 28;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    let maxV = 1;
    seriesList.forEach((s) => {
      (s.values || []).forEach((v) => { if (v > maxV) maxV = v; });
    });

    const xAt = (i) => (n === 1 ? padL + plotW / 2 : padL + (i / (n - 1)) * plotW);
    const yAt = (v) => padT + plotH - (Math.max(0, v) / maxV) * plotH;

    let gridHtml = '';
    for (let g = 0; g <= 4; g++) {
      const y = padT + (g / 4) * plotH;
      gridHtml += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--er-border)" stroke-dasharray="3 3" stroke-width="1"/>`;
    }

    const step = Math.max(1, Math.ceil(n / 8));
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
      const gid = `erg-${sIdx}-${Math.random().toString(36).slice(2, 6)}`;
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

    let dotsHtml = '';
    for (let i = 0; i < n; i++) {
      const tip = `${labels[i]} · ` + seriesList.map((s) => `${s.name}: ${(s.values && s.values[i]) || 0}`).join(' · ');
      const primaryY = yAt((seriesList[0].values && seriesList[0].values[i]) || 0);
      dotsHtml += `<circle cx="${xAt(i).toFixed(1)}" cy="${primaryY.toFixed(1)}" r="4" fill="${seriesList[0].color}" stroke="var(--er-surface)" stroke-width="2" style="cursor:pointer;" data-tip="${esc(tip)}"/>`;
    }

    container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${defsHtml}${gridHtml}${pathsHtml}${dotsHtml}${xLabelsHtml}</svg>`;
  }

  function renderHorizontalBars(container, items, emptyText) {
    if (!container) return;
    if (!items || !items.length) {
      container.innerHTML = `<p class="er-chart-sub">${esc(emptyText || 'No data in this window.')}</p>`;
      return;
    }
    const maxV = Math.max(1, ...items.map((it) => Math.abs(Number(it.value || 0))));
    container.innerHTML = items.map((it, i) => {
      const col = it.color || PALETTE[i % PALETTE.length];
      const pct = Math.max(4, Math.round((Math.abs(Number(it.value || 0)) / maxV) * 100));
      return (
        `<div class="er-task-bar-row" data-tip="${esc(it.tip || `${it.label}: ${it.display || it.value}`)}">` +
          `<span title="${esc(it.label)}">${esc(it.label)}</span>` +
          '<div class="er-task-bar-track" aria-hidden="true">' +
            `<div class="er-task-bar-fill" style="width:${pct}%;background:${col};"></div>` +
          '</div>' +
          `<span style="text-align:right;color:${col};font-weight:800;">${esc(it.display != null ? it.display : it.value)}</span>` +
        '</div>'
      );
    }).join('');
  }

  function buildSparklineSvg(values, color, labels, unitLabel) {
    const arr = Array.isArray(values) && values.length ? values : [0, 0];
    const W = 260;
    const H = 40;
    const maxV = Math.max(1, ...arr);
    const n = arr.length;
    const pts = arr.map((v, i) => {
      const x = n === 1 ? W / 2 : (i / (n - 1)) * (W - 8) + 4;
      const y = H - 5 - (Math.max(0, v) / maxV) * (H - 12);
      return [x, y];
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

  // =========================================================================
  // MODE B: SELECTED EMPLOYEE 360° DETAIL VIEW CONTROLLER
  // =========================================================================
  const detailEl = document.getElementById('employee-detail-data');
  if (detailEl) {
    let DETAIL = null;
    try {
      DETAIL = JSON.parse(detailEl.textContent || '{}');
    } catch (e) {
      console.error('Failed to parse employee detail payload:', e);
    }

    // 1. Accessible Tabs (Block layout + pointer-events safe hidden="until-found")
    const tablist = document.getElementById('er-detail-tablist');
    if (tablist) {
      const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
      const panels = tabs
        .map((tab) => document.querySelector(`[role="tabpanel"][aria-labelledby="${CSS.escape(tab.id)}"]`))
        .filter(Boolean);

      function activateTab(selectedTab, focusTab) {
        tabs.forEach((tab, idx) => {
          const isSel = tab === selectedTab;
          tab.setAttribute('aria-selected', isSel ? 'true' : 'false');
          tab.tabIndex = isSel ? 0 : -1;
          tab.toggleAttribute('focusgroupstart', isSel);
          if (panels[idx]) {
            panels[idx].hidden = isSel ? false : 'until-found';
          }
        });
        if (selectedTab && typeof selectedTab.scrollIntoView === 'function') {
          try {
            selectedTab.scrollIntoView({ block: 'nearest', inline: 'nearest', container: 'nearest' });
          } catch (_e) {
            selectedTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          }
        }
        if (focusTab) selectedTab.focus();
      }

      if (!CSS.supports('animation-timeline', 'scroll()') && tabs.length > 1) {
        const firstTab = tabs[0];
        const lastTab = tabs[tabs.length - 1];
        const obs = new IntersectionObserver((entries) => {
          entries.forEach(({ target, intersectionRatio }) => {
            tablist.classList.toggle(target === firstTab ? 'more-start' : 'more-end', intersectionRatio < 1);
          });
        }, { root: tablist, threshold: 1, rootMargin: '1px' });
        obs.observe(firstTab);
        obs.observe(lastTab);
      }

      tabs.forEach((tab) => tab.addEventListener('click', () => activateTab(tab, false)));
      tablist.addEventListener('keydown', (e) => {
        const idx = tabs.indexOf(document.activeElement);
        if (idx === -1) return;
        let next = -1;
        if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabs.length - 1;
        if (next !== -1) {
          e.preventDefault();
          activateTab(tabs[next], true);
        }
      });
      panels.forEach((panel, idx) => {
        panel.addEventListener('beforematch', () => activateTab(tabs[idx], false));
      });
    }

    // 2. Calls Tab Renderer (supports live AJAX filtering!)
    function renderCallsSection(callsData) {
      if (!callsData) return;
      const sum = callsData.summary || {};
      const og = callsData.outcomes_group || {};
      const sent = callsData.sentiments || {};

      const tabBadge = document.getElementById('er-tab-calls-count');
      if (tabBadge) tabBadge.textContent = String(sum.total || 0);
      const totalTag = document.getElementById('er-calls-total-tag');
      if (totalTag) totalTag.textContent = `${sum.total || 0} Calls`;
      const talkTag = document.getElementById('er-calls-talk-tag');
      if (talkTag) talkTag.textContent = sum.talk_display || '0s';
      const aiTag = document.getElementById('er-calls-ai-tag');
      if (aiTag) aiTag.textContent = `${sum.ai_analyzed || 0} AI`;
      const rangeBadge = document.getElementById('er-calls-range-badge');
      if (rangeBadge) rangeBadge.textContent = `${callsData.start} → ${callsData.end}`;
      const trendTitle = document.getElementById('er-calls-trend-title');
      if (trendTitle) {
        trendTitle.textContent = callsData.single_day
          ? `Hourly Calls & Connected (${callsData.start})`
          : `Daily Calls & Connected (${callsData.start} → ${callsData.end})`;
      }

      renderDonut(
        document.getElementById('emp-chart-call-outcomes'),
        [
          { label: 'Interested / Won', shortLabel: 'Won', value: og.interested || 0, color: '#10b981' },
          { label: 'Callback / Follow-up', shortLabel: 'Callback', value: og.callback || 0, color: '#3b82f6' },
          { label: 'No Answer / Busy', shortLabel: 'Unans.', value: og.unanswered || 0, color: '#f59e0b' },
          { label: 'Not Interested', shortLabel: 'Rejected', value: og.rejected || 0, color: '#ef4444' },
          { label: 'Other / Logged', shortLabel: 'Other', value: og.other || 0, color: '#8b5cf6' },
        ],
        'Outcomes'
      );

      renderDonut(
        document.getElementById('emp-chart-call-flow'),
        [
          { label: 'Outgoing Calls', shortLabel: 'Outgoing', value: sum.outbound || 0, color: '#6366f1' },
          { label: 'Incoming Calls', shortLabel: 'Incoming', value: sum.inbound || 0, color: '#06b6d4' },
        ],
        'Flow'
      );

      renderDonut(
        document.getElementById('emp-chart-call-sentiment'),
        [
          { label: 'Positive AI', shortLabel: 'Positive', value: sent.POSITIVE || 0, color: '#10b981' },
          { label: 'Neutral AI', shortLabel: 'Neutral', value: sent.NEUTRAL || 0, color: '#3b82f6' },
          { label: 'Hesitant AI', shortLabel: 'Hesitant', value: sent.HESITANT || 0, color: '#f59e0b' },
          { label: 'Negative AI', shortLabel: 'Negative', value: sent.NEGATIVE || 0, color: '#ef4444' },
        ],
        'Sentiment'
      );

      const tr = callsData.trend || {};
      renderMultiAreaChart(
        document.getElementById('emp-chart-calls-trend'),
        tr.labels || [],
        [
          { name: 'All Calls', values: tr.calls || [], color: '#6366f1' },
          { name: 'Connected', values: tr.connected || [], color: '#10b981' },
        ]
      );

      renderHorizontalBars(
        document.getElementById('emp-chart-top-outcomes'),
        (callsData.top_outcomes || []).map((o) => ({ label: o.label, value: o.count, display: o.count })),
        'No calls recorded in this filter window.'
      );

      const tbody = document.getElementById('er-calls-tbody');
      if (tbody) {
        const rows = callsData.rows || [];
        if (!rows.length) {
          tbody.innerHTML = '<tr><td colspan="7" class="empty">No calls logged for this employee in the selected filter window.</td></tr>';
        } else {
          tbody.innerHTML = rows.map((r) => {
            const leadCell = r.lead_url
              ? `<a class="er-row-link" href="${esc(r.lead_url)}">${esc(r.lead_name)}</a><span class="er-sub-note">${esc(r.phone)}</span>`
              : `<strong>${esc(r.lead_name)}</strong><span class="er-sub-note">${esc(r.phone)}</span>`;
            const dirBadge = r.direction === 'INBOUND'
              ? '<span class="er-status-pill st-full_shift">↙ Incoming</span>'
              : '<span class="er-status-pill st-info">↗ Outgoing</span>';
            const aiBits = [];
            if (r.has_recording && r.recording_url) {
              aiBits.push(`<a class="er-row-link" href="${esc(r.recording_url)}">🎙 Play</a>`);
            }
            if (r.ai_sentiment) {
              aiBits.push(`<span class="er-status-pill st-info">${esc(r.ai_sentiment)}</span>`);
            }
            if (r.objections_count > 0) {
              aiBits.push(`<span class="er-status-pill st-absent">⚠ ${r.objections_count} Obj</span>`);
            }
            const summaryLine = r.ai_summary || r.notes || '—';
            return (
              '<tr>' +
                `<th scope="row" class="er-sticky-col">${leadCell}</th>` +
                `<td>${esc(r.when)}</td>` +
                `<td>${dirBadge}</td>` +
                `<td><span class="er-status-pill">${esc(r.outcome_label)}</span></td>` +
                `<td>${esc(r.course)}</td>` +
                `<td><strong>${esc(r.duration_display)}</strong></td>` +
                `<td>${aiBits.length ? `<div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:4px;">${aiBits.join('')}</div>` : ''}<span>${esc(summaryLine)}</span></td>` +
              '</tr>'
            );
          }).join('');
        }
      }
    }

    if (DETAIL) {
      // Render Calls Tab initially
      renderCallsSection(DETAIL.calls);

      // Wire live AJAX Calls filter toolbar (Today / Yesterday / 7D / 30D / Month / Custom / Dir / Outcome)
      const toolbar = document.getElementById('er-calls-toolbar');
      if (toolbar) {
        const apiUrl = toolbar.getAttribute('data-api-url');
        const rangeBtns = Array.from(toolbar.querySelectorAll('[data-call-range]'));
        const startInp = document.getElementById('er-call-start');
        const endInp = document.getElementById('er-call-end');
        const customBtn = document.getElementById('er-call-custom-apply');
        const dirSel = document.getElementById('er-call-direction');
        const outSel = document.getElementById('er-call-outcome');
        const loadingPill = document.getElementById('er-calls-loading');
        let currentRange = (DETAIL.calls && DETAIL.calls.range) || '30d';

        async function fetchCalls(paramsObj) {
          if (!apiUrl) return;
          if (loadingPill) loadingPill.hidden = false;
          const qs = new URLSearchParams();
          Object.entries(paramsObj).forEach(([k, v]) => {
            if (v) qs.set(k, v);
          });
          try {
            const resp = await fetch(`${apiUrl}?${qs.toString()}`, {
              headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            renderCallsSection(data);
            if (startInp && data.start) startInp.value = data.start;
            if (endInp && data.end) endInp.value = data.end;
          } catch (err) {
            console.error('Failed to filter employee calls:', err);
          } finally {
            if (loadingPill) loadingPill.hidden = true;
          }
        }

        function currentExtraFilters() {
          return {
            direction: dirSel ? dirSel.value : '',
            outcome: outSel ? outSel.value : '',
          };
        }

        rangeBtns.forEach((btn) => {
          btn.addEventListener('click', () => {
            currentRange = btn.getAttribute('data-call-range') || 'today';
            rangeBtns.forEach((b) => b.classList.toggle('active', b === btn));
            fetchCalls(Object.assign({ range: currentRange }, currentExtraFilters()));
          });
        });

        if (customBtn) {
          customBtn.addEventListener('click', () => {
            currentRange = 'custom';
            rangeBtns.forEach((b) => b.classList.remove('active'));
            fetchCalls(Object.assign({
              start: startInp ? startInp.value : '',
              end: endInp ? endInp.value : '',
            }, currentExtraFilters()));
          });
        }

        [dirSel, outSel].forEach((sel) => {
          if (!sel) return;
          sel.addEventListener('change', () => {
            if (currentRange === 'custom') {
              fetchCalls(Object.assign({
                start: startInp ? startInp.value : '',
                end: endInp ? endInp.value : '',
              }, currentExtraFilters()));
            } else {
              fetchCalls(Object.assign({ range: currentRange }, currentExtraFilters()));
            }
          });
        });
      }

      // 3. Render Attendance & Workforce Charts
      const att = DETAIL.attendance || {};
      const ac = att.counts || {};
      renderDonut(
        document.getElementById('emp-chart-att-health'),
        [
          { label: 'Full Shift', shortLabel: 'Full', value: (ac.present_full || 0) + (ac.active || 0), color: '#10b981' },
          { label: 'Short Shift', shortLabel: 'Short', value: ac.short_shift || 0, color: '#f59e0b' },
          { label: 'On Leave', shortLabel: 'Leave', value: ac.leave || 0, color: '#3b82f6' },
          { label: 'Absent', shortLabel: 'Absent', value: ac.absent || 0, color: '#ef4444' },
        ],
        'Shifts'
      );

      const wfCats = (att.activity_categories || []).map((c, i) => ({
        label: c.name,
        shortLabel: c.name.slice(0, 8),
        value: Math.round(c.minutes),
        color: c.color || PALETTE[i % PALETTE.length],
      }));
      renderDonut(
        document.getElementById('emp-chart-workforce-cats'),
        wfCats.length ? wfCats : [{ label: 'No Segments Logged', shortLabel: 'None', value: 0, color: '#64748b' }],
        'Workforce'
      );

      const breakMins = (att.break_reasons || []).reduce((acc, b) => acc + Math.round(b.minutes || 0), 0);
      renderDonut(
        document.getElementById('emp-chart-pc-breaks'),
        [
          { label: 'PC Active (min)', shortLabel: 'Active', value: att.pc_active_minutes || 0, color: '#10b981' },
          { label: 'PC Idle (min)', shortLabel: 'Idle', value: att.pc_idle_minutes || 0, color: '#f59e0b' },
          { label: 'Shift Breaks (min)', shortLabel: 'Breaks', value: breakMins, color: '#6366f1' },
        ],
        'Minutes'
      );

      const acChart = att.chart || {};
      renderMultiAreaChart(
        document.getElementById('emp-chart-att-hours'),
        acChart.labels || [],
        [
          { name: 'Net Work (h)', values: acChart.net_hours || [], color: '#10b981' },
          { name: 'Breaks (h)', values: acChart.break_hours || [], color: '#f59e0b' },
        ]
      );

      // 4. Render Projects & Work Reports Charts
      const proj = DETAIL.projects || {};
      const ps = proj.status_counts || {};
      renderDonut(
        document.getElementById('emp-chart-proj-status'),
        [
          { label: 'Completed', shortLabel: 'Done', value: ps.completed || 0, color: '#10b981' },
          { label: 'In Progress', shortLabel: 'Active', value: ps.in_progress || 0, color: '#3b82f6' },
          { label: 'Pending Review', shortLabel: 'Review', value: ps.pending_review || 0, color: '#8b5cf6' },
          { label: 'Assigned / Hold', shortLabel: 'Queued', value: (ps.assigned || 0) + (ps.on_hold || 0), color: '#f59e0b' },
          { label: 'Needs Changes', shortLabel: 'Changes', value: ps.needs_changes || 0, color: '#ef4444' },
        ],
        'Projects'
      );

      renderHorizontalBars(
        document.getElementById('emp-chart-proj-time'),
        (proj.time_bars || []).map((b) => ({
          label: b.title,
          value: b.minutes,
          display: `${b.hours}h`,
          tip: `${b.title}: ${b.hours}h (${b.status})`,
        })),
        'No project time entries logged yet.'
      );

      renderDonut(
        document.getElementById('emp-chart-reports-ring'),
        [
          { label: 'Reports Filed', shortLabel: 'Filed', value: proj.report_pct || 0, color: '#6366f1' },
          { label: 'Remaining', shortLabel: 'Gap', value: Math.max(0, 100 - (proj.report_pct || 0)), color: '#64748b' },
        ],
        'Filed',
        { value: `${proj.report_pct || 0}%`, label: 'Filed' }
      );

      // 5. Render Points, Outcomes & Leads Charts (only mounts present for this role)
      const pts = DETAIL.points || {};
      renderHorizontalBars(
        document.getElementById('emp-chart-points-events'),
        (pts.breakdown || []).map((b) => ({
          label: b.label,
          value: b.points,
          display: b.points > 0 ? `+${b.points}` : String(b.points),
          color: b.points >= 0 ? '#10b981' : '#ef4444',
        })),
        'No points recorded in this window.'
      );

      const peerAvgNum = Number(pts.peer_avg || 0);
      renderDonut(
        document.getElementById('emp-chart-peer-rating'),
        [
          { label: 'Peer Rating', shortLabel: 'Score', value: Math.round(peerAvgNum * 10), color: '#10b981' },
          { label: 'Headroom', shortLabel: 'Max 10', value: Math.max(0, 100 - Math.round(peerAvgNum * 10)), color: '#64748b' },
        ],
        'Peer Avg',
        { value: `${peerAvgNum.toFixed(1)}/10`, label: 'Peer Avg' }
      );

      const lt = (pts.leads && pts.leads.temps) || {};
      renderDonut(
        document.getElementById('emp-chart-emp-leads'),
        [
          { label: 'Hot (80–100)', shortLabel: 'Hot', value: lt.HOT || 0, color: TIER_COLORS.HOT },
          { label: 'Warm (60–79)', shortLabel: 'Warm', value: lt.WARM || 0, color: TIER_COLORS.WARM },
          { label: 'Interested', shortLabel: 'Interest', value: lt.INTERESTED || 0, color: TIER_COLORS.INTERESTED },
          { label: 'Cold (20–39)', shortLabel: 'Cold', value: lt.COLD || 0, color: TIER_COLORS.COLD },
          { label: 'Inactive', shortLabel: 'Inactive', value: lt.INACTIVE || 0, color: TIER_COLORS.INACTIVE },
        ],
        'Leads'
      );

      const fu = pts.followups || {};
      renderDonut(
        document.getElementById('emp-chart-emp-followups'),
        [
          { label: 'Completed', shortLabel: 'Done', value: fu.completed || 0, color: '#10b981' },
          { label: 'Pending', shortLabel: 'Pending', value: fu.pending || 0, color: '#3b82f6' },
          { label: 'Overdue', shortLabel: 'Overdue', value: fu.overdue || 0, color: '#ef4444' },
          { label: 'Demos / Admit', shortLabel: 'Wins', value: (pts.counsellings || 0) + (pts.admissions || 0), color: '#f59e0b' },
        ],
        'Follow-up'
      );
    }
    return;
  }

  // =========================================================================
  // MODE A: ALL EMPLOYEES TEAM VISUAL OVERVIEW CONTROLLER
  // =========================================================================
  const dataEl = document.getElementById('employee-reports-data');
  if (!dataEl) return;

  let REPORT = null;
  try {
    REPORT = JSON.parse(dataEl.textContent || '{}');
  } catch (e) {
    console.error('Failed to parse employee reports payload:', e);
    return;
  }

  const team = REPORT.team || {};
  const employees = Array.isArray(REPORT.employees) ? REPORT.employees : [];
  const labels = REPORT.range_labels || [];
  const empGrid = document.getElementById('er-employee-grid');
  const emptyState = document.getElementById('er-empty-state');

  function employeeDetailUrl(empId) {
    const base = (empGrid && empGrid.getAttribute('data-base-url')) || '/employee-reports/';
    const range = (empGrid && empGrid.getAttribute('data-range')) || '30d';
    const qs = new URLSearchParams({ employee: String(empId), range });
    if (range === 'custom') {
      const s = empGrid && empGrid.getAttribute('data-start');
      const e = empGrid && empGrid.getAttribute('data-end');
      if (s) qs.set('start', s);
      if (e) qs.set('end', e);
    }
    return `${base}?${qs.toString()}`;
  }

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

  const barsMount = document.getElementById('chart-employee-bars');
  if (barsMount) {
    if (!employees.length) {
      barsMount.innerHTML = '<div class="er-empty-state">No employee data in this range.</div>';
    } else {
      barsMount.innerHTML = employees.map((emp) => {
        const col = scoreColor(emp.overall_score);
        const metricTip = emp.is_caller_like
          ? `Calls: ${emp.calls.total} · Attendance: ${emp.attendance_pct}%`
          : `Reports: ${emp.tasks.work_reports} · Attendance: ${emp.attendance_pct}%`;
        const tip = `${emp.name} (${emp.role_display}) · Score ${emp.overall_score}% · ${metricTip}`;
        return (
          `<a class="er-bar-btn" href="${esc(employeeDetailUrl(emp.id))}" data-tip="${esc(tip)}">` +
            `<span class="er-bar-emp-name">${esc(emp.name)}</span>` +
            '<span class="er-bar-track" aria-hidden="true">' +
              `<span class="er-bar-fill" style="width:${Math.max(4, emp.overall_score)}%;background:${col};"></span>` +
            '</span>' +
            `<span class="er-bar-badge" style="color:${col};">${emp.overall_score}%</span>` +
          '</a>'
        );
      }).join('');
    }
  }

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

      let stackedBarHtml = '';
      if (emp.is_caller_like) {
        const t = emp.leads.temps || {};
        const lTotal = emp.leads.total || 0;
        const segs = lTotal > 0
          ? ['HOT', 'WARM', 'INTERESTED', 'COLD', 'INACTIVE'].map((k) => {
              const v = t[k] || 0;
              if (!v) return '';
              return `<div class="evc-stacked-seg" style="flex-grow:${v};background:${TIER_COLORS[k]};" data-tip="${k}: ${v} leads"></div>`;
            }).join('')
          : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>';
        stackedBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Lead Temperature Pipeline</span><span>Avg AI ${emp.leads.avg_score}/100</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Lead temperatures for ${esc(emp.name)}">${segs}</div>` +
          '</div>';
      } else {
        const pr = emp.tasks.projects || {};
        const pTot = (pr.completed || 0) + (pr.in_progress || 0) + (pr.assigned || 0);
        const segs = pTot > 0
          ? [
              { k: 'Completed', v: pr.completed || 0, c: '#10b981' },
              { k: 'In Progress', v: pr.in_progress || 0, c: '#3b82f6' },
              { k: 'Assigned', v: pr.assigned || 0, c: '#f59e0b' },
            ].filter((s) => s.v > 0).map((s) =>
              `<div class="evc-stacked-seg" style="flex-grow:${s.v};background:${s.c};" data-tip="${s.k}: ${s.v} projects"></div>`
            ).join('')
          : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>';
        stackedBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Projects &amp; Deliverables</span><span>${pr.completed || 0}/${pTot} Done</span></div>` +
            `<div class="evc-stacked-bar" role="img" aria-label="Projects status for ${esc(emp.name)}">${segs}</div>` +
          '</div>';
      }

      let secondBarHtml = '';
      if (emp.is_caller_like) {
        const cTot = emp.calls.total || 0;
        secondBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Calls Flow (Out / In)</span><span>${emp.calls.talk_minutes}m · ${emp.tasks.points} pts</span></div>` +
            '<div class="evc-stacked-bar" role="img" aria-label="Outgoing vs Incoming calls">' +
              (cTot
                ? `<div class="evc-stacked-seg" style="flex-grow:${Math.max(0, emp.calls.outbound)};background:#6366f1;" data-tip="Outgoing: ${emp.calls.outbound}"></div>` +
                  `<div class="evc-stacked-seg" style="flex-grow:${Math.max(0, emp.calls.inbound)};background:#06b6d4;" data-tip="Incoming: ${emp.calls.inbound}"></div>`
                : '<div class="evc-stacked-seg" style="flex-grow:1;background:var(--er-track);"></div>') +
            '</div>' +
          '</div>';
      } else {
        secondBarHtml =
          '<div class="evc-bar-section">' +
            `<div class="evc-bar-label-row"><span>Daily Work Reports</span><span>${emp.tasks.work_reports} filed (${emp.tasks.report_pct}%)</span></div>` +
            '<div class="evc-stacked-bar" role="img" aria-label="Work reports completion">' +
              `<div class="evc-stacked-seg" style="width:${emp.tasks.report_pct}%;background:#6366f1;" data-tip="Reports: ${emp.tasks.work_reports}"></div>` +
            '</div>' +
          '</div>';
      }

      const sparkHtml = buildSparklineSvg(emp.sparkline, mColor, labels, emp.spark_label);
      const url = employeeDetailUrl(emp.id);

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
            `<div class="evc-master-ring" role="img" aria-label="Performance score ${emp.overall_score}%" style="--p:${emp.overall_score}%;--c:${mColor};" data-tip="Overall Visual Score: ${emp.overall_score}%">` +
              `<div class="evc-master-ring-in">${emp.overall_score}</div>` +
            '</div>' +
          '</header>' +

          '<div class="evc-mini-rings">' +
            '<div class="evc-mini-ring-item">' +
              `<div class="evc-ring-sm" role="img" aria-label="Attendance ${emp.attendance_pct}%" style="--p:${emp.attendance_pct}%;--c:${aColor};" data-tip="Attendance: ${emp.attendance_pct}%">` +
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
            `<a class="evc-open-btn" href="${esc(url)}">Open Full 360° Employee Report →</a>` +
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
})();
