with open('apps/web/templates/web/sidebar.html', 'r', encoding='utf-8') as f:
    content = f.read()

target = """  <!-- Quick Access Pills -->
  <div class="quick-pills" id="quick-pills">
    {% if can_call %}
    <a href="{% url 'web:dashboard' %}" class="quick-pill {% if section == 'dashboard' %}active{% endif %}" title="Overview">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect></svg>
      <span>Overview</span>
    </a>
    <a href="{% url 'web:performance' %}" class="quick-pill {% if section == 'performance' %}active{% endif %}" title="Performance">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
      <span>Performance</span>
    </a>
    <a href="{% url 'web:leads' %}" class="quick-pill {% if section == 'leads' %}active{% endif %}" title="Leads">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M22 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
      <span>Leads</span>
    </a>
    <a href="{% url 'web:calls' %}" class="quick-pill {% if section == 'calls' %}active{% endif %}" title="Calls">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 7 23 1 17 1"></polyline><line x1="16" y1="8" x2="23" y2="1"></line><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>
      <span>Calls</span>
    </a>
    {% endif %}
  </div>"""

replacement = """  <!-- Quick Access Pills -->
  <div class="quick-pills" id="quick-pills">
    <a href="{% url 'web:dashboard' %}" class="quick-pill {% if section == 'dashboard' %}active{% endif %}" title="Overview">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect></svg>
      <span>Overview</span>
    </a>
    <a href="{% url 'web:performance' %}" class="quick-pill {% if section == 'performance' %}active{% endif %}" title="Performance">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
      <span>Performance</span>
    </a>
    {% if can_call %}
    <a href="{% url 'web:leads' %}" class="quick-pill {% if section == 'leads' %}active{% endif %}" title="Leads">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M22 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
      <span>Leads</span>
    </a>
    <a href="{% url 'web:calls' %}" class="quick-pill {% if section == 'calls' %}active{% endif %}" title="Calls">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 7 23 1 17 1"></polyline><line x1="16" y1="8" x2="23" y2="1"></line><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path></svg>
      <span>Calls</span>
    </a>
    {% endif %}
  </div>"""

if target in content:
    content = content.replace(target, replacement)
    with open('apps/web/templates/web/sidebar.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Updated quick pills in sidebar.html")
else:
    print("Target not found in sidebar.html")

