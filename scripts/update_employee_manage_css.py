# Script to update employee-manage.css with bulletproof light/dark theme support
import re

css_path = r"E:\react\aspiring-crm\apps\web\static\web\employee-manage.css"

with open(css_path, "r", encoding="utf-8") as f:
    content = f.read()

# Let's replace the top variable block
top_block_old = """/* Employee Management Polished UI Stylesheet */

:root {
  --text-muted: var(--muted, #929bb0);
  --manage-card-bg: var(--panel, #14171f);
  --manage-card-border: var(--border, #262b39);
  --toggle-card-bg: rgba(255, 255, 255, 0.02);
  --toggle-card-border: var(--border, #262b39);
  --toggle-card-hover: rgba(165, 154, 255, 0.04);
  --toggle-card-checked: rgba(165, 154, 255, 0.1);
  --pill-bg: rgba(255, 255, 255, 0.02);
  --pill-border: var(--border, #262b39);
  --pill-hover: rgba(165, 154, 255, 0.04);
  --pill-checked: rgba(165, 154, 255, 0.12);
  --input-bg-clean: #10131b;
  --input-border-clean: #303647;
  --callout-bg: rgba(165, 154, 255, 0.08);
  --callout-border: rgba(165, 154, 255, 0.2);
  --callout-text: var(--text, #edf0f7);
  --subtle-box-bg: rgba(255, 255, 255, 0.02);
  --subtle-box-border: var(--border, #262b39);
}

[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"] {
  --text-muted: #5d6880;
  --manage-card-bg: #ffffff;
  --manage-card-border: #e2e8f0;
  --toggle-card-bg: #ffffff;
  --toggle-card-border: #dce1ec;
  --toggle-card-hover: #f8fafc;
  --toggle-card-checked: rgba(103, 81, 185, 0.06);
  --pill-bg: #ffffff;
  --pill-border: #dce1ec;
  --pill-hover: #f8fafc;
  --pill-checked: rgba(103, 81, 185, 0.08);
  --input-bg-clean: #ffffff;
  --input-border-clean: #cdd4e2;
  --callout-bg: #f5f3ff;
  --callout-border: #ddd6fe;
  --callout-text: #3730a3;
  --subtle-box-bg: #f8fafc;
  --subtle-box-border: #e2e8f0;
}"""

top_block_new = """/* Employee Management Polished UI Stylesheet */

:root {
  --text-muted: var(--muted, #929bb0);
  --manage-card-bg: var(--panel, #14171f);
  --manage-card-border: var(--border, #262b39);
  --toggle-card-bg: rgba(255, 255, 255, 0.02);
  --toggle-card-border: var(--border, #262b39);
  --toggle-card-hover: rgba(165, 154, 255, 0.04);
  --toggle-card-checked: rgba(165, 154, 255, 0.1);
  --pill-bg: rgba(255, 255, 255, 0.02);
  --pill-border: var(--border, #262b39);
  --pill-hover: rgba(165, 154, 255, 0.04);
  --pill-checked: rgba(165, 154, 255, 0.12);
  --input-bg-clean: #10131b;
  --input-border-clean: #303647;
  --callout-bg: rgba(165, 154, 255, 0.08);
  --callout-border: rgba(165, 154, 255, 0.2);
  --callout-text: var(--text, #edf0f7);
  --subtle-box-bg: rgba(255, 255, 255, 0.02);
  --subtle-box-border: var(--border, #262b39);
}

/* Modern light-dark() CSS function support */
@supports (background-color: light-dark(#fff, #000)) {
  :root {
    --manage-card-bg: light-dark(#ffffff, var(--panel, #14171f));
    --manage-card-border: light-dark(#e2e8f0, var(--border, #262b39));
    --toggle-card-bg: light-dark(#ffffff, rgba(255, 255, 255, 0.02));
    --toggle-card-border: light-dark(#dce1ec, var(--border, #262b39));
    --toggle-card-hover: light-dark(#f8fafc, rgba(165, 154, 255, 0.04));
    --toggle-card-checked: light-dark(#f5f3ff, rgba(165, 154, 255, 0.1));
    --pill-bg: light-dark(#ffffff, rgba(255, 255, 255, 0.02));
    --pill-border: light-dark(#dce1ec, var(--border, #262b39));
    --pill-hover: light-dark(#f8fafc, rgba(165, 154, 255, 0.04));
    --pill-checked: light-dark(#f5f3ff, rgba(165, 154, 255, 0.12));
    --input-bg-clean: light-dark(#ffffff, #10131b);
    --input-border-clean: light-dark(#cdd4e2, #303647);
    --callout-bg: light-dark(#f5f3ff, rgba(165, 154, 255, 0.08));
    --callout-border: light-dark(#ddd6fe, rgba(165, 154, 255, 0.2));
    --callout-text: light-dark(#3730a3, var(--text, #edf0f7));
    --subtle-box-bg: light-dark(#f8fafc, rgba(255, 255, 255, 0.02));
    --subtle-box-border: light-dark(#e2e8f0, var(--border, #262b39));
  }
}

:root[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"],
[data-theme="light"] {
  --text-muted: #5d6880 !important;
  --manage-card-bg: #ffffff !important;
  --manage-card-border: #e2e8f0 !important;
  --toggle-card-bg: #ffffff !important;
  --toggle-card-border: #dce1ec !important;
  --toggle-card-hover: #f8fafc !important;
  --toggle-card-checked: #f5f3ff !important;
  --pill-bg: #ffffff !important;
  --pill-border: #dce1ec !important;
  --pill-hover: #f8fafc !important;
  --pill-checked: #f5f3ff !important;
  --input-bg-clean: #ffffff !important;
  --input-border-clean: #cdd4e2 !important;
  --callout-bg: #f5f3ff !important;
  --callout-border: #ddd6fe !important;
  --callout-text: #3730a3 !important;
  --subtle-box-bg: #f8fafc !important;
  --subtle-box-border: #e2e8f0 !important;
}"""

if top_block_old in content:
    content = content.replace(top_block_old, top_block_new, 1)
else:
    print("WARNING: top_block_old not found in employee-manage.css")

comprehensive_employee_light_rules = """

/* ==========================================================================
   BULLETPROOF LIGHT THEME RULES FOR EMPLOYEE MANAGEMENT
   ========================================================================== */

:root[data-theme="light"] .manage-card,
html[data-theme="light"] .manage-card,
[data-theme="light"] .manage-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #e2e8f0 !important;
}

:root[data-theme="light"] .manage-card-header h2,
html[data-theme="light"] .manage-card-header h2,
[data-theme="light"] .manage-card-header h2 {
  color: #1e293b !important;
}

:root[data-theme="light"] .manage-card-header p,
html[data-theme="light"] .manage-card-header p,
[data-theme="light"] .manage-card-header p {
  color: #64748b !important;
}

:root[data-theme="light"] .form-field label,
html[data-theme="light"] .form-field label,
[data-theme="light"] .form-field label {
  color: #1e293b !important;
}

:root[data-theme="light"] .form-field .field-help,
html[data-theme="light"] .form-field .field-help,
[data-theme="light"] .form-field .field-help {
  color: #64748b !important;
}

:root[data-theme="light"] .form-field input,
:root[data-theme="light"] .form-field select,
html[data-theme="light"] .form-field input,
html[data-theme="light"] .form-field select,
[data-theme="light"] .form-field input,
[data-theme="light"] .form-field select {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #cdd4e2 !important;
  color: #1e293b !important;
}

:root[data-theme="light"] .toggle-card,
html[data-theme="light"] .toggle-card,
[data-theme="light"] .toggle-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #dce1ec !important;
}

:root[data-theme="light"] .toggle-card:hover,
html[data-theme="light"] .toggle-card:hover,
[data-theme="light"] .toggle-card:hover {
  background: #f8fafc !important;
  background-color: #f8fafc !important;
}

:root[data-theme="light"] .toggle-card:has(input:checked),
html[data-theme="light"] .toggle-card:has(input:checked),
[data-theme="light"] .toggle-card:has(input:checked) {
  background: #f5f3ff !important;
  background-color: #f5f3ff !important;
  border-color: #8b5cf6 !important;
}

:root[data-theme="light"] .toggle-title,
html[data-theme="light"] .toggle-title,
[data-theme="light"] .toggle-title {
  color: #1e293b !important;
}

:root[data-theme="light"] .toggle-desc,
html[data-theme="light"] .toggle-desc,
[data-theme="light"] .toggle-desc {
  color: #64748b !important;
}

:root[data-theme="light"] .service-pill-item,
html[data-theme="light"] .service-pill-item,
[data-theme="light"] .service-pill-item {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #dce1ec !important;
}

:root[data-theme="light"] .service-pill-item:hover,
html[data-theme="light"] .service-pill-item:hover,
[data-theme="light"] .service-pill-item:hover {
  background: #f8fafc !important;
  background-color: #f8fafc !important;
}

:root[data-theme="light"] .service-pill-item:has(input:checked),
html[data-theme="light"] .service-pill-item:has(input:checked),
[data-theme="light"] .service-pill-item:has(input:checked) {
  background: #f5f3ff !important;
  background-color: #f5f3ff !important;
  border-color: #8b5cf6 !important;
}

:root[data-theme="light"] .service-pill-item span,
html[data-theme="light"] .service-pill-item span,
[data-theme="light"] .service-pill-item span {
  color: #1e293b !important;
}

:root[data-theme="light"] .policy-highlight-card,
html[data-theme="light"] .policy-highlight-card,
[data-theme="light"] .policy-highlight-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #8b5cf6 !important;
}

:root[data-theme="light"] .sidebar-profile-card,
:root[data-theme="light"] .sidebar-branch-card,
html[data-theme="light"] .sidebar-profile-card,
html[data-theme="light"] .sidebar-branch-card,
[data-theme="light"] .sidebar-profile-card,
[data-theme="light"] .sidebar-branch-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #e2e8f0 !important;
}

:root[data-theme="light"] .profile-name,
:root[data-theme="light"] .sidebar-branch-card h3,
html[data-theme="light"] .profile-name,
html[data-theme="light"] .sidebar-branch-card h3,
[data-theme="light"] .profile-name,
[data-theme="light"] .sidebar-branch-card h3 {
  color: #1e293b !important;
}

:root[data-theme="light"] .profile-sub,
:root[data-theme="light"] .branch-coords,
:root[data-theme="light"] .sidebar-notice,
html[data-theme="light"] .profile-sub,
html[data-theme="light"] .branch-coords,
html[data-theme="light"] .sidebar-notice,
[data-theme="light"] .profile-sub,
[data-theme="light"] .branch-coords,
[data-theme="light"] .sidebar-notice {
  color: #64748b !important;
}

:root[data-theme="light"] .meta-label,
html[data-theme="light"] .meta-label,
[data-theme="light"] .meta-label {
  color: #64748b !important;
}

:root[data-theme="light"] .meta-value,
html[data-theme="light"] .meta-value,
[data-theme="light"] .meta-value {
  color: #1e293b !important;
}
"""

content += comprehensive_employee_light_rules

with open(css_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Updated employee-manage.css successfully!")

