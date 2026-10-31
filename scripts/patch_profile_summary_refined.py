with open('apps/web/templates/web/profile_summary.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add Quick Call Telemetry Glance to hero card
hero_old = """    <div class="caller-points-box">
      <span style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;">Selected Window Points</span>
      <div class="caller-points-val">{{ stats.total_points }}</div>
      <div class="caller-points-split">
        <span class="pos">+{{ stats.positive_points }} awarded</span>
        <span>·</span>
        <span class="neg">{{ stats.negative_points }} deducted</span>
      </div>
    </div>
  </div>"""

hero_new = """    <div class="caller-points-box">
      <span style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;">Selected Window Points</span>
      <div class="caller-points-val">{{ stats.total_points }}</div>
      <div class="caller-points-split">
        <span class="pos">+{{ stats.positive_points }} awarded</span>
        <span>·</span>
        <span class="neg">{{ stats.negative_points }} deducted</span>
      </div>
    </div>
  </div>
  <!-- Quick Call Metrics Summary Strip in Hero -->
  <div class="caller-hero-telemetry-strip">
    <div class="hero-call-item">
      <span class="hero-call-dot blue"></span>
      <span class="hero-call-lbl">Calls:</span>
      <strong>{{ stats.calls }}</strong>
    </div>
    <div class="hero-call-divider">·</div>
    <div class="hero-call-item">
      <span class="hero-call-dot emerald"></span>
      <span class="hero-call-lbl">Connected:</span>
      <strong>{{ stats.connected_calls }}</strong>
      <span class="hero-call-rate">({% if stats.calls %}{% widthratio stats.connected_calls stats.calls 100 %}%{% else %}0%{% endif %})</span>
    </div>
    <div class="hero-call-divider">·</div>
    <div class="hero-call-item">
      <span class="hero-call-dot cyan"></span>
      <span class="hero-call-lbl">Talk Time:</span>
      <strong>{{ stats.talk_time|duration }}</strong>
    </div>
    <div class="hero-call-divider">·</div>
    <div class="hero-call-item">
      <span class="hero-call-dot amber"></span>
      <span class="hero-call-lbl">Avg Talk Time:</span>
      <strong>{{ stats.avg_duration|duration }}</strong>
    </div>
  </div>"""

if hero_old in content:
    content = content.replace(hero_old, hero_new)
    print("Patched hero card in profile_summary.html")
else:
    print("Hero old target not found!")

# 2. Replace duplicate lower "Calls & Connected" card with "Dialer Conversion Efficiency"
dup_card_old = """  <div class="caller-telemetry-card">
    <div class="caller-telemetry-head">
      <span>Calls &amp; Connected</span>
      <span class="performance-metric-icon cyan">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
      </span>
    </div>
    <div class="caller-telemetry-val">{{ stats.calls }} <small>/ {{ stats.connected_calls }} connected</small></div>
    <div class="caller-telemetry-subtext">Talk time {{ stats.talk_time|duration }} (avg {{ stats.avg_duration|duration }})</div>
  </div>"""

dup_card_new = """  <div class="caller-telemetry-card">
    <div class="caller-telemetry-head">
      <span>Dialer Connection Rate</span>
      <span class="performance-metric-icon cyan">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
      </span>
    </div>
    <div class="caller-telemetry-val">{% if stats.calls %}{% widthratio stats.connected_calls stats.calls 100 %}%{% else %}0%{% endif %} <small>success</small></div>
    <div class="caller-telemetry-subtext">{{ stats.connected_calls }} connected of {{ stats.calls }} total dials</div>
  </div>"""

if dup_card_old in content:
    content = content.replace(dup_card_old, dup_card_new)
    print("Patched duplicate lower card in profile_summary.html")
else:
    print("Duplicate card target not found!")

# 3. Pull Assigned Leads Pipeline out of dashboard-grid spaced into its own full-width section
pipeline_old = """<!-- Telemetry Donuts Grid -->
<div class="dashboard-grid spaced">
  <!-- Donut Chart 1: Call Outcomes -->
  <section class="panel call-outcomes-panel">
    <div class="panel-heading">
      <div>
        <h2>Call Outcomes Donut</h2>
        <p>Distribution of call outcomes in the selected window</p>
      </div>
    </div>
    <div class="donut-wrap">
      <div id="pipeline-chart" class="donut" aria-hidden="true"></div>
      <div class="donut-center">
        <strong>{{ pie_total }}</strong>
        <span>Total Calls</span>
      </div>
    </div>
    <div class="outcome-legend">
      {% for item in pipeline %}
      <a href="{% if item.key %}{% url 'web:calls' %}?outcome={{ item.key }}&caller={{ caller.pk }}{% else %}{% url 'web:calls' %}?caller={{ caller.pk }}{% endif %}" class="outcome-chip" title="View {{ item.value }} {{ item.name }} calls for {{ caller|user_name }}">
        <span style="display:flex;align-items:center;gap:7px">
          <span class="legend-dot" style="background:{{ item.color }};box-shadow:0 0 6px {{ item.color }}"></span>
          <span>{{ item.name }}</span>
        </span>
        <strong>{{ item.value }}</strong>
      </a>
      {% endfor %}
    </div>
  </section>

  <!-- Donut Chart 2: Assigned Leads Pipeline -->
  <section class="panel assigned-leads-pipeline-card">
    <div class="panel-heading pipeline-card-header">
      <div>
        <div class="eyebrow" style="font-size: 0.72rem; margin-bottom: 2px;">LEAD STAGE DISTRIBUTION</div>
        <h2>Assigned Leads Pipeline</h2>
        <p>Current distribution of leads assigned to {{ caller|user_name }}</p>
      </div>
      <span class="pill cyan">{{ caller.lead_count }} total leads</span>
    </div>
    <div class="status-summary-grid">
      {% for item in lead_pipeline %}
      <a href="{% url 'web:leads' %}?status={{ item.key }}&owner={{ caller.pk }}" class="status-summary-item" title="View {{ item.value }} {{ item.name }} leads">
        <div class="status-summary-top">
          <span class="status-summary-label">{{ item.name }}</span>
          <span class="status-summary-dot" style="background:{{ item.color }};box-shadow:0 0 6px {{ item.color }}"></span>
        </div>
        <div class="status-summary-val">{{ item.value }}</div>
        <div class="status-progress-track">
          <span class="status-progress-fill" style="background:{{ item.color }};width:{{ item.percent }}%"></span>
        </div>
        <div class="status-summary-footer">
          <span class="status-percent-pill">{{ item.percent }}%</span>
          <span class="status-view-link">View leads &rarr;</span>
        </div>
      </a>
      {% empty %}
      <div class="empty" style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--text-muted);">
        No leads currently assigned to this caller.
      </div>
      {% endfor %}
    </div>
  </section>
</div>"""

pipeline_new = """<!-- Call Outcomes Donut Section -->
<section class="panel call-outcomes-panel" style="margin: 24px 0;">
  <div class="panel-heading">
    <div>
      <div class="eyebrow" style="font-size: 0.72rem; margin-bottom: 2px;">OUTCOME BREAKDOWN</div>
      <h2>Call Outcomes Donut</h2>
      <p>Distribution of call outcomes in the selected window</p>
    </div>
    <span class="pill violet">{{ pie_total }} total calls</span>
  </div>
  <div class="donut-wrap">
    <div id="pipeline-chart" class="donut" aria-hidden="true"></div>
    <div class="donut-center">
      <strong>{{ pie_total }}</strong>
      <span>Total Calls</span>
    </div>
  </div>
  <div class="outcome-legend">
    {% for item in pipeline %}
    <a href="{% if item.key %}{% url 'web:calls' %}?outcome={{ item.key }}&caller={{ caller.pk }}{% else %}{% url 'web:calls' %}?caller={{ caller.pk }}{% endif %}" class="outcome-chip" title="View {{ item.value }} {{ item.name }} calls for {{ caller|user_name }}">
      <span style="display:flex;align-items:center;gap:7px">
        <span class="legend-dot" style="background:{{ item.color }};box-shadow:0 0 6px {{ item.color }}"></span>
        <span>{{ item.name }}</span>
      </span>
      <strong>{{ item.value }}</strong>
    </a>
    {% endfor %}
  </div>
</section>

<!-- Assigned Leads Pipeline (Full-Width Card with Refined Margin, Borders & Visual Hierarchy) -->
<section class="panel assigned-leads-pipeline-card" style="margin: 24px 0;">
  <div class="panel-heading pipeline-card-header">
    <div>
      <div class="eyebrow" style="font-size: 0.72rem; margin-bottom: 2px;">LEAD STAGE DISTRIBUTION</div>
      <h2>Assigned Leads Pipeline</h2>
      <p>Current distribution of leads assigned to {{ caller|user_name }} across all operational stages</p>
    </div>
    <span class="pill cyan">{{ caller.lead_count }} total leads assigned</span>
  </div>
  <div class="status-summary-grid">
    {% for item in lead_pipeline %}
    <a href="{% url 'web:leads' %}?status={{ item.key }}&owner={{ caller.pk }}" class="status-summary-item" title="View {{ item.value }} {{ item.name }} leads">
      <div class="status-summary-top">
        <span class="status-summary-label">{{ item.name }}</span>
        <span class="status-summary-dot" style="background:{{ item.color }};box-shadow:0 0 6px {{ item.color }}"></span>
      </div>
      <div class="status-summary-val">{{ item.value }}</div>
      <div class="status-progress-track">
        <span class="status-progress-fill" style="background:{{ item.color }};width:{{ item.percent }}%"></span>
      </div>
      <div class="status-summary-footer">
        <span class="status-percent-pill">{{ item.percent }}% of pipeline</span>
        <span class="status-view-link">View leads &rarr;</span>
      </div>
    </a>
    {% empty %}
    <div class="empty" style="grid-column: 1 / -1; padding: 28px; text-align: center; color: var(--text-muted);">
      No leads currently assigned to this caller.
    </div>
    {% endfor %}
  </div>
</section>"""

if pipeline_old in content:
    content = content.replace(pipeline_old, pipeline_new)
    print("Patched pipeline section in profile_summary.html")
else:
    print("Pipeline old target not found!")

with open('apps/web/templates/web/profile_summary.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated profile_summary.html successfully!")

