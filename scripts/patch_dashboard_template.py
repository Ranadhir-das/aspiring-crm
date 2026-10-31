from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/templates/web/attendance_dashboard.html")
content = path.read_text(encoding="utf-8")

old_switcher = """    <div class="attendance-view-switcher">
      <a class="view-switch-btn {% if view_mode == 'daily' %}active{% endif %}" href="?view=daily&date={{ selected_date_str }}">
        📅 Daily View
      </a>
      <a class="view-switch-btn {% if view_mode == 'range' %}active{% endif %}" href="?view=range&date_from={{ selected_date_from_str }}&date_to={{ selected_date_to_str }}">
        📊 Date Range / Monthly View
      </a>
    </div>"""

new_switcher = """    <div class="attendance-view-switcher">
      <a class="view-switch-btn {% if view_mode == 'daily' %}active{% endif %}" href="?view=daily&date={{ selected_date_str }}{% if selected_employee_id %}&employee={{ selected_employee_id }}{% endif %}{% if selected_branch_id %}&branch={{ selected_branch_id }}{% endif %}{% if selected_status %}&status={{ selected_status }}{% endif %}{% if search_q %}&q={{ search_q }}{% endif %}">
        📅 Daily View
      </a>
      <a class="view-switch-btn {% if view_mode == 'range' %}active{% endif %}" href="?view=range&date_from={{ selected_date_from_str }}&date_to={{ selected_date_to_str }}{% if selected_employee_id %}&employee={{ selected_employee_id }}{% endif %}{% if selected_branch_id %}&branch={{ selected_branch_id }}{% endif %}{% if selected_status %}&status={{ selected_status }}{% endif %}{% if search_q %}&q={{ search_q }}{% endif %}">
        📊 Date Range / Monthly View
      </a>
    </div>"""

content = content.replace(old_switcher.replace('\r\n', '\n'), new_switcher.replace('\r\n', '\n'))
content = content.replace(old_switcher, new_switcher)

old_form_field = """      <div class="att-field">
        <label for="id_status">Status</label>
        <select id="id_status" name="status">
          <option value="">All Statuses</option>
          {% for code, label in status_choices %}
          <option value="{{ code }}" {% if selected_status == code %}selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
      </div>"""

new_form_field = """      <div class="att-field">
        <label for="id_status">Status</label>
        <select id="id_status" name="status">
          <option value="">All Statuses</option>
          {% for code, label in status_choices %}
          <option value="{{ code }}" {% if selected_status == code %}selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
      </div>

      <div class="att-field">
        <label for="id_q">Search</label>
        <input type="text" id="id_q" name="q" value="{{ search_q }}" placeholder="Search name or branch...">
      </div>"""

content = content.replace(old_form_field.replace('\r\n', '\n'), new_form_field.replace('\r\n', '\n'))
content = content.replace(old_form_field, new_form_field)

path.write_text(content, encoding="utf-8")
print("Successfully updated attendance_dashboard.html")

