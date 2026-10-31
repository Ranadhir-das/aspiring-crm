from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/templates/web/employee_attendance_history.html")
content = path.read_text(encoding="utf-8")

old_exports = """      <div class="export-group">
        <a class="btn-export" href="?date_from={{ selected_date_from_str }}&date_to={{ selected_date_to_str }}&export=csv">
          CSV Export
        </a>
        <a class="btn-export excel" href="?date_from={{ selected_date_from_str }}&date_to={{ selected_date_to_str }}&export=xlsx">
          Excel (.xlsx)
        </a>
      </div>"""

new_exports = """      <div class="export-group">
        <a class="btn-export" href="?{{ export_query_string }}&export=csv">
          CSV Export
        </a>
        <a class="btn-export excel" href="?{{ export_query_string }}&export=xlsx">
          Excel (.xlsx)
        </a>
      </div>"""

content = content.replace(old_exports.replace('\r\n', '\n'), new_exports.replace('\r\n', '\n'))
content = content.replace(old_exports, new_exports)

old_inputs = """      <div class="att-field">
        <label for="id_date_to">To Date</label>
        <input type="date" id="id_date_to" name="date_to" value="{{ selected_date_to_str }}">
      </div>"""

new_inputs = """      <div class="att-field">
        <label for="id_date_to">To Date</label>
        <input type="date" id="id_date_to" name="date_to" value="{{ selected_date_to_str }}">
      </div>
      <div class="att-field">
        <label for="id_status">Status</label>
        <select id="id_status" name="status">
          <option value="">All Statuses</option>
          {% for code, label in status_choices %}
          <option value="{{ code }}" {% if selected_status == code %}selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
      </div>"""

content = content.replace(old_inputs.replace('\r\n', '\n'), new_inputs.replace('\r\n', '\n'))
content = content.replace(old_inputs, new_inputs)

path.write_text(content, encoding="utf-8")
print("Successfully updated employee_attendance_history.html")

