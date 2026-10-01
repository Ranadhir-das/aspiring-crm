/**
 * Vaani CRM - Live Analytics & Reports Dashboard Controller
 * Implements silent 1-second live polling, interactive Chart.js visualizations with direct-click navigation,
 * multi-dimension filtering with zero page reload, and full deep-linking for all metrics & reports.
 */

(function () {
  'use strict';

  // Global references & state
  let charts = {};
  let isPolling = false;
  let pollingInterval = null;
  let currentFilters = {
    days: 7,
    date_from: '',
    date_to: '',
    service: '',
    caller: '',
    source: '',
    status: ''
  };

  // Helper: Detect Dark Mode
  function isDarkMode() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  }

  function getGridColor() {
    return isDarkMode() ? 'rgba(38, 43, 59, 0.5)' : 'rgba(226, 232, 240, 0.6)';
  }

  function getTextColor() {
    return isDarkMode() ? '#94a3b8' : '#64748b';
  }

  function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }

  // --------------------------------------------------------------------------
  // Chart Initializers (With Click-to-Filter Navigation)
  // --------------------------------------------------------------------------

  function initDonutChart(canvasId, labels, values, colors, urls) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels || [],
        datasets: [{
          data: values || [],
          backgroundColor: colors || ['#5b5ce2', '#06b6d4', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6'],
          borderWidth: 2,
          borderColor: isDarkMode() ? '#141722' : '#ffffff',
          hoverOffset: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '72%',
        plugins: {
          legend: { display: false }, // Rendered as interactive HTML pills below canvas
          tooltip: {
            padding: 10,
            cornerRadius: 8,
            bodyFont: { family: "'DM Sans', sans-serif", size: 12 },
            callbacks: {
              afterLabel: function () {
                return 'Click slice to view exact records ↗';
              }
            }
          }
        },
        onClick: function (evt, elements) {
          if (elements && elements.length > 0) {
            const idx = elements[0].index;
            if (chart._urls && chart._urls[idx]) {
              window.location.href = chart._urls[idx];
            }
          }
        },
        onHover: function (evt, elements) {
          if (evt.native && evt.native.target) {
            evt.native.target.style.cursor = elements && elements.length ? 'pointer' : 'default';
          }
        }
      }
    });

    chart._urls = urls || [];
    return chart;
  }

  function initLineChart(canvasId, labels, values, color, label) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    // Create soft gradient fill
    const canvasContext = ctx.getContext('2d');
    const gradient = canvasContext.createLinearGradient(0, 0, 0, 200);
    gradient.addColorStop(0, color + '33'); // ~20% opacity
    gradient.addColorStop(1, color + '00'); // 0% opacity

    return new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels || [],
        datasets: [{
          label: label || 'Count',
          data: values || [],
          borderColor: color,
          backgroundColor: gradient,
          borderWidth: 2.5,
          tension: 0.35,
          fill: true,
          pointRadius: 3,
          pointHoverRadius: 6,
          pointBackgroundColor: color,
          pointBorderColor: '#ffffff',
          pointBorderWidth: 1.5
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: getTextColor(),
              font: { family: "'DM Sans', sans-serif", size: 10 },
              maxRotation: 45
            }
          },
          y: {
            beginAtZero: true,
            grid: { color: getGridColor() },
            ticks: {
              color: getTextColor(),
              font: { family: "'DM Sans', sans-serif", size: 10 },
              precision: 0
            }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            padding: 10,
            cornerRadius: 8,
            bodyFont: { family: "'DM Sans', sans-serif", size: 12 }
          }
        }
      }
    });
  }

  function initBarChart(canvasId, labels, values, color, label, urls) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels || [],
        datasets: [{
          label: label || 'Total',
          data: values || [],
          backgroundColor: color,
          borderRadius: 6,
          maxBarThickness: 28
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: getTextColor(),
              font: { family: "'DM Sans', sans-serif", size: 10 },
              maxRotation: 45
            }
          },
          y: {
            beginAtZero: true,
            grid: { color: getGridColor() },
            ticks: {
              color: getTextColor(),
              font: { family: "'DM Sans', sans-serif", size: 10 },
              precision: 0
            }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            padding: 10,
            cornerRadius: 8,
            bodyFont: { family: "'DM Sans', sans-serif", size: 12 },
            callbacks: {
              afterLabel: function () {
                return 'Click bar to view filtered records ↗';
              }
            }
          }
        },
        onClick: function (evt, elements) {
          if (elements && elements.length > 0) {
            const idx = elements[0].index;
            if (chart._urls && chart._urls[idx]) {
              window.location.href = chart._urls[idx];
            }
          }
        },
        onHover: function (evt, elements) {
          if (evt.native && evt.native.target) {
            evt.native.target.style.cursor = elements && elements.length ? 'pointer' : 'default';
          }
        }
      }
    });

    chart._urls = urls || [];
    return chart;
  }

  // --------------------------------------------------------------------------
  // Update Chart Data In-Place (No Flicker)
  // --------------------------------------------------------------------------

  function updateChartData(chart, labels, values, colors, urls) {
    if (!chart) return;
    chart.data.labels = labels || [];
    if (chart.data.datasets && chart.data.datasets.length > 0) {
      chart.data.datasets[0].data = values || [];
      if (colors && chart.config.type === 'doughnut') {
        chart.data.datasets[0].backgroundColor = colors;
      }
    }
    if (urls) {
      chart._urls = urls;
    }
    chart.update('none'); // zero animation delay for high-frequency polling
  }

  // --------------------------------------------------------------------------
  // DOM Renderers for Metrics, Legends, Funnel, Matrix & Scoreboard
  // --------------------------------------------------------------------------

  function updateKPI(cardId, kpiData) {
    if (!kpiData) return;
    const card = document.getElementById(cardId);
    if (!card) return;

    if (kpiData.url) {
      card.setAttribute('href', kpiData.url);
    }

    const valEl = card.querySelector('.an-kpi-value');
    if (valEl) valEl.textContent = kpiData.value.toLocaleString();

    const badgeEl = card.querySelector('.an-trend-badge');
    if (badgeEl) {
      badgeEl.className = 'an-trend-badge ' + (kpiData.trend || 'flat');
      const arrow = kpiData.trend === 'up' ? '↑ ' : kpiData.trend === 'down' ? '↓ ' : '';
      const sign = kpiData.diff > 0 ? '+' : '';
      badgeEl.textContent = arrow + sign + kpiData.diff_pct + '%';
    }

    const subEl = card.querySelector('.an-kpi-subtext');
    if (subEl) {
      const sign = kpiData.diff > 0 ? '+' : '';
      subEl.textContent = sign + kpiData.diff + ' vs prior';
    }
  }

  function updateLiveStatus(live) {
    if (!live) return;
    const setVal = (id, val, url) => {
      const el = document.getElementById(id);
      if (el) {
        const valSpan = el.querySelector('.an-chip-value') || el;
        valSpan.textContent = (val !== undefined && val !== null) ? Number(val).toLocaleString() : '0';
        if (url && el.tagName === 'A') {
          el.setAttribute('href', url);
        }
      }
    };
    setVal('ls-active-callers', live.active_callers, live.url_callers);
    setVal('ls-active-calls', live.active_calls, live.url_calls);
    setVal('ls-website-waiting', live.website_leads_waiting, live.url_website);
    setVal('ls-due-followups', live.followups_due, live.url_followups);
    setVal('ls-claimed-leads', live.claimed_leads, live.url_claimed);
    setVal('ls-unassigned-leads', live.pending_unassigned_leads, live.url_unassigned);
  }

  function updateDonutLegend(containerId, items) {
    const container = document.getElementById(containerId);
    if (!container || !items) return;

    let html = '';
    items.forEach(it => {
      const label = it.label || it.source || it.service || '';
      html += `
        <a href="${it.url || '#'}" class="an-legend-pill" title="Click to view ${escapeHtml(label)}">
          <span class="an-legend-dot" style="background-color: ${it.color || '#5b5ce2'};"></span>
          <span class="an-legend-text">${escapeHtml(label)}</span>
          <span class="an-legend-count">${(it.count || 0).toLocaleString()}</span>
          <span class="an-legend-pct">${it.percent || 0}%</span>
        </a>
      `;
    });
    container.innerHTML = html;
  }

  function updateConversionFunnel(funnel) {
    const container = document.getElementById('an-funnel-steps');
    if (!container || !funnel) return;

    let html = '';
    funnel.forEach((st, idx) => {
      html += `
        <a href="${st.url || '#'}" class="an-funnel-step" data-step="${st.step}" title="Click to view ${escapeHtml(st.label)}">
          <div class="an-funnel-step-header">
            <span class="an-funnel-step-icon" style="color: ${st.color};">${st.icon || '●'}</span>
            <span class="an-funnel-step-badge">Stage ${st.step}</span>
          </div>
          <div class="an-funnel-step-count" style="color: ${st.color};">${(st.count || 0).toLocaleString()}</div>
          <div class="an-funnel-step-label">${escapeHtml(st.label)}</div>
          <div class="an-funnel-step-meta">
            <span class="an-funnel-rate">Conv: <strong>${st.rate}%</strong></span>
            ${st.dropoff > 0 ? `<span class="an-funnel-dropoff">Drop: ${st.dropoff}%</span>` : ''}
          </div>
          <div class="an-funnel-step-link">View records ↗</div>
        </a>
      `;
      if (idx < funnel.length - 1) {
        html += `
          <div class="an-funnel-connector">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </div>
        `;
      }
    });
    container.innerHTML = html;
  }

  function updateSourceMatrix(matrix) {
    const tbody = document.getElementById('an-source-matrix-tbody');
    if (!tbody || !matrix) return;

    if (matrix.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 20px; color: var(--an-text-dim);">No source records found for this period.</td></tr>';
      return;
    }

    let html = '';
    matrix.forEach(sm => {
      html += `
        <tr>
          <td>
            <span class="an-source-tag" style="border-left: 3px solid ${sm.color};">
              ${escapeHtml(sm.source)}
            </span>
          </td>
          <td><a href="${sm.url_leads || '#'}" class="an-table-link"><strong>${sm.leads.toLocaleString()}</strong> ↗</a></td>
          <td><a href="${sm.url_calls || '#'}" class="an-table-link">${sm.calls.toLocaleString()} ↗</a></td>
          <td><a href="${sm.url_interested || '#'}" class="an-table-link"><strong style="color: #10b981;">${sm.interested.toLocaleString()}</strong> ↗</a></td>
          <td><a href="${sm.url_admissions || '#'}" class="an-table-link"><strong style="color: #059669;">${sm.admissions.toLocaleString()}</strong> ↗</a></td>
          <td><span class="an-rate-pill">${sm.conversion_rate}%</span></td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
  }

  function updateFollowupHealth(health) {
    const container = document.getElementById('an-followup-health-grid');
    if (!container || !health || !health.items) return;

    let html = '';
    health.items.forEach(item => {
      html += `
        <a href="${item.url || '#'}" class="an-followup-chip" style="border-left-color: ${item.color};" title="Click to view ${escapeHtml(item.label)}">
          <div class="an-followup-chip-info">
            <span class="an-followup-chip-val" style="color: ${item.color};">${(item.count || 0).toLocaleString()}</span>
            <span class="an-followup-chip-lbl">${escapeHtml(item.label)}</span>
          </div>
          <span class="an-followup-arrow">↗</span>
        </a>
      `;
    });
    container.innerHTML = html;
  }

  function updateCourseList(programs) {
    const container = document.getElementById('an-course-list');
    if (!container || !programs) return;

    if (programs.length === 0) {
      container.innerHTML = '<div style="text-align: center; padding: 14px; color: var(--an-text-dim); font-size: 12px;">No course enrollments recorded in this range.</div>';
      return;
    }

    let html = '';
    programs.forEach(prog => {
      html += `
        <a href="${prog.url || '#'}" class="an-course-item" title="Click to filter admissions by ${escapeHtml(prog.program)}">
          <span class="an-course-name">${escapeHtml(prog.program)}</span>
          <div class="an-course-meta">
            <span class="an-course-badge">🎓 ${prog.count} enrolled</span>
            <span class="an-course-arrow">↗</span>
          </div>
        </a>
      `;
    });
    container.innerHTML = html;
  }

  function updateEmployeeTable(employees) {
    const tbody = document.getElementById('an-employee-tbody');
    if (!tbody || !employees) return;

    if (employees.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 24px; color: var(--an-text-dim);">No employee performance records in this range.</td></tr>';
      return;
    }

    let rowsHtml = '';
    employees.forEach(emp => {
      const rankClass = emp.rank <= 3 ? `rank-${emp.rank}` : '';
      const initial = (emp.name || emp.username || '?').charAt(0).toUpperCase();
      const appScore = emp.peer_appreciation_score !== null ? `★ ${emp.peer_appreciation_score} / 10` : '—';
      const appCount = emp.peer_appreciation_count ? `(${emp.peer_appreciation_count})` : '';

      rowsHtml += `
        <tr>
          <td><span class="an-rank-badge ${rankClass}">${emp.rank}</span></td>
          <td>
            <a href="${emp.url_profile || '#'}" class="an-emp-cell an-table-link" title="View profile for ${escapeHtml(emp.name)}">
              <span class="an-emp-avatar">${initial}</span>
              <div>
                <span class="an-emp-name">${escapeHtml(emp.name)} ↗</span>
                <span class="an-emp-user">@${escapeHtml(emp.username)}</span>
              </div>
            </a>
          </td>
          <td><span class="badge ${emp.role === 'CALLER' ? 'called' : 'interested'}">${escapeHtml(emp.role)}</span></td>
          <td><a href="${emp.url_calls || '#'}" class="an-table-link" title="View calls logged by ${escapeHtml(emp.name)}"><strong>${emp.calls}</strong> ↗</a></td>
          <td><a href="${emp.url_interested || '#'}" class="an-table-link" title="View interested leads assigned to ${escapeHtml(emp.name)}"><strong style="color: #10b981;">${emp.interested}</strong> ↗</a></td>
          <td><a href="${emp.url_counselling || '#'}" class="an-table-link" title="View consultations">${emp.counselling} ↗</a></td>
          <td><a href="${emp.url_admissions || '#'}" class="an-table-link" title="View admissions attributed to ${escapeHtml(emp.name)}"><strong style="color: #059669;">${emp.admissions}</strong> ↗</a></td>
          <td><a href="${emp.url_points || '#'}" class="an-points-pill" title="View performance ledger for ${escapeHtml(emp.name)}">💎 ${emp.performance_points} pts ↗</a></td>
          <td><span class="an-appreciation-pill">${appScore} <small>${appCount}</small></span></td>
        </tr>
      `;
    });

    tbody.innerHTML = rowsHtml;
  }

  function updateRecentActivity(activities) {
    const list = document.getElementById('an-activity-list');
    if (!list || !activities) return;

    if (activities.length === 0) {
      list.innerHTML = '<div style="text-align:center; padding: 24px; color: var(--an-text-dim);">No recent activity recorded yet.</div>';
      return;
    }

    let itemsHtml = '';
    activities.forEach(act => {
      const leadLink = act.lead_id ? `<a href="/leads/${act.lead_id}/">${escapeHtml(act.lead_name || 'Lead #' + act.lead_id)}</a>` : '';
      itemsHtml += `
        <div class="an-activity-item">
          <div class="an-activity-icon">${act.icon || '●'}</div>
          <div class="an-activity-content">
            <div class="an-activity-desc">
              <strong>${escapeHtml(act.actor)}</strong> ${escapeHtml(act.description)} ${leadLink}
            </div>
            <div class="an-activity-meta">
              <span class="an-activity-actor">${escapeHtml(act.type_label || '')}</span>
              <span>•</span>
              <time title="${act.timestamp}">${act.relative_time}</time>
            </div>
          </div>
        </div>
      `;
    });

    list.innerHTML = itemsHtml;
  }

  // --------------------------------------------------------------------------
  // Apply Full Analytics Data Payload
  // --------------------------------------------------------------------------

  function applyAnalyticsData(data) {
    if (!data) return;

    // 1. Updated timestamp
    const updatedEl = document.getElementById('an-last-updated-time');
    if (updatedEl && data.updated_at) {
      updatedEl.textContent = data.updated_at;
    }

    // 2. Live Telemetry
    if (data.live_status) updateLiveStatus(data.live_status);

    // 3. KPIs
    if (data.kpis) {
      updateKPI('kpi-total-leads', data.kpis.total_leads);
      updateKPI('kpi-new-leads', data.kpis.new_leads);
      updateKPI('kpi-calls', data.kpis.calls);
      updateKPI('kpi-interested', data.kpis.interested_leads);
      updateKPI('kpi-followups', data.kpis.followups);
      updateKPI('kpi-missed-followups', data.kpis.missed_followups);
      updateKPI('kpi-counselling', data.kpis.counselling_demo);
      updateKPI('kpi-admissions', data.kpis.verified_admissions);
    }

    // 4. Conversion Funnel (Pipeline Velocity)
    if (data.conversion_funnel) updateConversionFunnel(data.conversion_funnel);

    // 5. 4 Donut Charts & Legends
    if (data.lead_status) {
      updateChartData(
        charts.leadStatus,
        data.lead_status.labels,
        data.lead_status.values,
        data.lead_status.colors,
        data.lead_status.items?.map(it => it.url)
      );
      const total = (data.lead_status.values || []).reduce((a, b) => a + b, 0);
      const centerEl = document.getElementById('donut-status-center-val');
      if (centerEl) centerEl.textContent = total.toLocaleString();
      updateDonutLegend('legend-lead-status', data.lead_status.items);
    }

    if (data.lead_sources) {
      updateChartData(
        charts.leadSources,
        data.lead_sources.labels,
        data.lead_sources.values,
        data.lead_sources.colors,
        data.lead_sources.items?.map(it => it.url)
      );
      const total = (data.lead_sources.values || []).reduce((a, b) => a + b, 0);
      const centerEl = document.getElementById('donut-source-center-val');
      if (centerEl) centerEl.textContent = total.toLocaleString();
      updateDonutLegend('legend-lead-sources', data.lead_sources.items);
    }

    if (data.services) {
      updateChartData(
        charts.services,
        data.services.labels,
        data.services.values,
        data.services.colors,
        data.services.items?.map(it => it.url)
      );
      const total = (data.services.values || []).reduce((a, b) => a + b, 0);
      const centerEl = document.getElementById('donut-service-center-val');
      if (centerEl) centerEl.textContent = total.toLocaleString();
      updateDonutLegend('legend-services', data.services.items);
    }

    if (data.call_outcomes) {
      updateChartData(
        charts.callOutcomes,
        data.call_outcomes.labels,
        data.call_outcomes.values,
        data.call_outcomes.colors,
        data.call_outcomes.items?.map(it => it.url)
      );
      const total = (data.call_outcomes.values || []).reduce((a, b) => a + b, 0);
      const centerEl = document.getElementById('donut-outcomes-center-val');
      if (centerEl) centerEl.textContent = total.toLocaleString();
      updateDonutLegend('legend-call-outcomes', data.call_outcomes.items);
    }

    // 6. Source Matrix & Follow-up Health & Courses
    if (data.source_matrix) updateSourceMatrix(data.source_matrix);
    if (data.followup_health) updateFollowupHealth(data.followup_health);
    if (data.admissions_by_program) updateCourseList(data.admissions_by_program);

    // 7. Line Trends
    if (data.lead_trend) updateChartData(charts.leadTrend, data.lead_trend.labels, data.lead_trend.values);
    if (data.call_trend) updateChartData(charts.callTrend, data.call_trend.labels, data.call_trend.values);
    if (data.interested_trend) updateChartData(charts.interestedTrend, data.interested_trend.labels, data.interested_trend.values);
    if (data.admission_trend) updateChartData(charts.admissionTrend, data.admission_trend.labels, data.admission_trend.values);

    // 8. Bar Charts
    if (data.calls_by_employee) {
      updateChartData(
        charts.empCalls,
        data.calls_by_employee.labels,
        data.calls_by_employee.values,
        null,
        data.calls_by_employee.items?.map(it => it.url)
      );
    }
    if (data.leads_by_employee) {
      updateChartData(
        charts.empLeads,
        data.leads_by_employee.labels,
        data.leads_by_employee.values,
        null,
        data.leads_by_employee.items?.map(it => it.url)
      );
    }
    if (data.admissions_by_employee) {
      updateChartData(
        charts.empAdmissions,
        data.admissions_by_employee.labels,
        data.admissions_by_employee.values,
        null,
        data.admissions_by_employee.items?.map(it => it.url)
      );
    }
    if (data.points_by_employee) {
      updateChartData(
        charts.empPoints,
        data.points_by_employee.labels,
        data.points_by_employee.values,
        null,
        data.points_by_employee.items?.map(it => it.url)
      );
    }

    // 9. Scoreboard Table & Feed
    if (data.employee_performance) updateEmployeeTable(data.employee_performance);
    if (data.recent_activity) updateRecentActivity(data.recent_activity);
  }

  // --------------------------------------------------------------------------
  // Fetch / Polling Loop
  // --------------------------------------------------------------------------

  function buildQueryString() {
    const params = new URLSearchParams();
    if (currentFilters.date_from) params.set('date_from', currentFilters.date_from);
    if (currentFilters.date_to) params.set('date_to', currentFilters.date_to);
    if (!currentFilters.date_from && !currentFilters.date_to && currentFilters.days) {
      params.set('days', currentFilters.days);
    }
    if (currentFilters.service) params.set('service', currentFilters.service);
    if (currentFilters.caller) params.set('caller', currentFilters.caller);
    if (currentFilters.source) params.set('source', currentFilters.source);
    if (currentFilters.status) params.set('status', currentFilters.status);
    return params.toString();
  }

  async function pollAnalytics() {
    if (isPolling) return; // avoid overlapping requests
    isPolling = true;

    try {
      const qs = buildQueryString();
      const response = await fetch(`/api/v1/analytics/overview/?${qs}`, {
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        },
        cache: 'no-store'
      });

      if (response.ok) {
        const data = await response.json();
        applyAnalyticsData(data);
      }
    } catch (err) {
      // Silently retry on next poll tick
      console.warn('[Live Analytics] Refresh error:', err);
    } finally {
      isPolling = false;
    }
  }

  function startLivePolling() {
    if (pollingInterval) clearInterval(pollingInterval);
    pollingInterval = setInterval(pollAnalytics, 1000);
  }

  // --------------------------------------------------------------------------
  // Filter Handlers
  // --------------------------------------------------------------------------

  function initFilters() {
    // Quick Date Pills
    const pills = document.querySelectorAll('.an-pill-btn');
    pills.forEach(btn => {
      btn.addEventListener('click', function () {
        pills.forEach(p => p.classList.remove('active'));
        this.classList.add('active');

        const pillDays = this.getAttribute('data-days');
        const pillType = this.getAttribute('data-type');
        const fromInput = document.getElementById('an-date-from');
        const toInput = document.getElementById('an-date-to');

        const today = new Date();
        const formatDate = d => d.toISOString().split('T')[0];

        if (pillType === 'today') {
          currentFilters.date_from = formatDate(today);
          currentFilters.date_to = formatDate(today);
          currentFilters.days = 1;
        } else if (pillType === 'yesterday') {
          const y = new Date(today);
          y.setDate(today.getDate() - 1);
          currentFilters.date_from = formatDate(y);
          currentFilters.date_to = formatDate(y);
          currentFilters.days = 1;
        } else if (pillType === 'this_month') {
          const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
          currentFilters.date_from = formatDate(firstDay);
          currentFilters.date_to = formatDate(today);
          currentFilters.days = Math.ceil((today - firstDay) / (1000 * 60 * 60 * 24)) + 1;
        } else if (pillDays) {
          const d = parseInt(pillDays, 10);
          const past = new Date(today);
          past.setDate(today.getDate() - d + 1);
          currentFilters.date_from = formatDate(past);
          currentFilters.date_to = formatDate(today);
          currentFilters.days = d;
        }

        if (fromInput) fromInput.value = currentFilters.date_from;
        if (toInput) toInput.value = currentFilters.date_to;

        pollAnalytics();
      });
    });

    // Apply Button
    const applyBtn = document.getElementById('an-btn-apply');
    if (applyBtn) {
      applyBtn.addEventListener('click', function (e) {
        e.preventDefault();
        const fromInput = document.getElementById('an-date-from');
        const toInput = document.getElementById('an-date-to');
        const serviceSelect = document.getElementById('an-filter-service');
        const callerSelect = document.getElementById('an-filter-caller');
        const sourceSelect = document.getElementById('an-filter-source');
        const statusSelect = document.getElementById('an-filter-status');

        if (fromInput) currentFilters.date_from = fromInput.value;
        if (toInput) currentFilters.date_to = toInput.value;
        if (serviceSelect) currentFilters.service = serviceSelect.value;
        if (callerSelect) currentFilters.caller = callerSelect.value;
        if (sourceSelect) currentFilters.source = sourceSelect.value;
        if (statusSelect) currentFilters.status = statusSelect.value;

        // Deselect quick pills if custom date picked
        pills.forEach(p => p.classList.remove('active'));

        pollAnalytics();
      });
    }

    // Reset Button
    const resetBtn = document.getElementById('an-btn-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', function (e) {
        e.preventDefault();
        const fromInput = document.getElementById('an-date-from');
        const toInput = document.getElementById('an-date-to');
        const serviceSelect = document.getElementById('an-filter-service');
        const callerSelect = document.getElementById('an-filter-caller');
        const sourceSelect = document.getElementById('an-filter-source');
        const statusSelect = document.getElementById('an-filter-status');

        const today = new Date();
        const past7 = new Date(today);
        past7.setDate(today.getDate() - 6);
        const formatDate = d => d.toISOString().split('T')[0];

        currentFilters = {
          days: 7,
          date_from: formatDate(past7),
          date_to: formatDate(today),
          service: '',
          caller: '',
          source: '',
          status: ''
        };

        if (fromInput) fromInput.value = currentFilters.date_from;
        if (toInput) toInput.value = currentFilters.date_to;
        if (serviceSelect) serviceSelect.value = '';
        if (callerSelect) callerSelect.value = '';
        if (sourceSelect) sourceSelect.value = '';
        if (statusSelect) statusSelect.value = '';

        pills.forEach(p => {
          if (p.getAttribute('data-days') === '7') p.classList.add('active');
          else p.classList.remove('active');
        });

        pollAnalytics();
      });
    }
  }

  // --------------------------------------------------------------------------
  // Initialization
  // --------------------------------------------------------------------------

  function initAnalyticsDashboard() {
    const rawDataScript = document.getElementById('analytics-initial-data');
    let initialData = null;
    if (rawDataScript) {
      try {
        initialData = JSON.parse(rawDataScript.textContent);
      } catch (e) {
        console.error('Failed to parse analytics initial data:', e);
      }
    }

    // Set initial filter state from server payload
    if (initialData && initialData.filters) {
      currentFilters.date_from = initialData.filters.date_from || '';
      currentFilters.date_to = initialData.filters.date_to || '';
      currentFilters.days = initialData.filters.days || 7;
      currentFilters.service = initialData.filters.service || '';
      currentFilters.caller = initialData.filters.caller || '';
      currentFilters.source = initialData.filters.source || '';
      currentFilters.status = initialData.filters.status || '';
    }

    const d = initialData || {};

    // 4 Donut Charts (with clickable slice navigation)
    charts.leadStatus = initDonutChart(
      'chart-lead-status',
      d.lead_status?.labels,
      d.lead_status?.values,
      d.lead_status?.colors,
      d.lead_status?.items?.map(it => it.url)
    );

    charts.leadSources = initDonutChart(
      'chart-lead-sources',
      d.lead_sources?.labels,
      d.lead_sources?.values,
      d.lead_sources?.colors,
      d.lead_sources?.items?.map(it => it.url)
    );

    charts.services = initDonutChart(
      'chart-services',
      d.services?.labels,
      d.services?.values,
      d.services?.colors,
      d.services?.items?.map(it => it.url)
    );

    charts.callOutcomes = initDonutChart(
      'chart-call-outcomes',
      d.call_outcomes?.labels,
      d.call_outcomes?.values,
      d.call_outcomes?.colors,
      d.call_outcomes?.items?.map(it => it.url)
    );

    // 4 Line/Area Trends
    charts.leadTrend = initLineChart(
      'chart-lead-trend',
      d.lead_trend?.labels,
      d.lead_trend?.values,
      '#5b5ce2',
      'Leads'
    );

    charts.callTrend = initLineChart(
      'chart-call-trend',
      d.call_trend?.labels,
      d.call_trend?.values,
      '#06b6d4',
      'Calls'
    );

    charts.interestedTrend = initLineChart(
      'chart-interested-trend',
      d.interested_trend?.labels,
      d.interested_trend?.values,
      '#10b981',
      'Interested'
    );

    charts.admissionTrend = initLineChart(
      'chart-admission-trend',
      d.admission_trend?.labels,
      d.admission_trend?.values,
      '#8b5cf6',
      'Admissions'
    );

    // 4 Employee Bar Charts (with clickable bar navigation)
    charts.empCalls = initBarChart(
      'chart-emp-calls',
      d.calls_by_employee?.labels,
      d.calls_by_employee?.values,
      '#06b6d4',
      'Calls',
      d.calls_by_employee?.items?.map(it => it.url)
    );

    charts.empLeads = initBarChart(
      'chart-emp-leads',
      d.leads_by_employee?.labels,
      d.leads_by_employee?.values,
      '#5b5ce2',
      'Leads',
      d.leads_by_employee?.items?.map(it => it.url)
    );

    charts.empAdmissions = initBarChart(
      'chart-emp-admissions',
      d.admissions_by_employee?.labels,
      d.admissions_by_employee?.values,
      '#8b5cf6',
      'Admissions',
      d.admissions_by_employee?.items?.map(it => it.url)
    );

    charts.empPoints = initBarChart(
      'chart-emp-points',
      d.points_by_employee?.labels,
      d.points_by_employee?.values,
      '#f59e0b',
      'Points',
      d.points_by_employee?.items?.map(it => it.url)
    );

    // Setup filter listeners
    initFilters();

    // Start 1-second background polling
    startLivePolling();
  }

  // Boot when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAnalyticsDashboard);
  } else {
    initAnalyticsDashboard();
  }

})();

