import re
from pathlib import Path

# 1. Update branches.css
branches_css_path = Path("E:/react/aspiring-crm/apps/web/static/web/branches.css")
b_css = branches_css_path.read_text(encoding="utf-8")

# Replace header variable declarations
old_vars = """/* Branch Management and Employee Assignments CSS */

:root {
  --text-muted: var(--muted, #929bb0);
  --card-subtle-bg: #141722;
  --card-subtle-border: #262b39;
  --card-subtle-hover: #191d2a;
  --card-checked-bg: rgba(165, 154, 255, 0.1);
  --input-bg-clean: #10131b;
  --callout-bg: rgba(139, 92, 246, 0.12);
  --callout-border: rgba(139, 92, 246, 0.3);
  --callout-text: #c4bbff;
}

/* Modern light-dark() CSS function support */
@supports (background-color: light-dark(#fff, #000)) {
  :root {
    --card-subtle-bg: light-dark(#ffffff, #141722);
    --card-subtle-border: light-dark(#dce1ec, #262b39);
    --card-subtle-hover: light-dark(#f8fafc, #191d2a);
    --card-checked-bg: light-dark(#f5f3ff, rgba(165, 154, 255, 0.1));
    --input-bg-clean: light-dark(#ffffff, #10131b);
    --callout-bg: light-dark(#f5f3ff, rgba(139, 92, 246, 0.12));
    --callout-border: light-dark(#ddd6fe, rgba(139, 92, 246, 0.3));
    --callout-text: light-dark(#4338ca, #c4bbff);
  }
}

:root[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"],
[data-theme="light"] {
  --text-muted: #5d6880 !important;
  --card-subtle-bg: #ffffff !important;
  --card-subtle-border: #dce1ec !important;
  --card-subtle-hover: #f8fafc !important;
  --card-checked-bg: #f5f3ff !important;
  --input-bg-clean: #ffffff !important;
  --callout-bg: #f5f3ff !important;
  --callout-border: #ddd6fe !important;
  --callout-text: #4338ca !important;
}"""

new_vars = """/* Branch Management and Employee Assignments CSS */

/* Base & Light Theme Variables (Default to Light) */
:root,
:root[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"],
[data-theme="light"] {
  --text-muted: #5d6880;
  --card-subtle-bg: #ffffff;
  --card-subtle-border: #dce1ec;
  --card-subtle-hover: #f8fafc;
  --card-checked-bg: #f5f3ff;
  --input-bg-clean: #ffffff;
  --callout-bg: #f5f3ff;
  --callout-border: #ddd6fe;
  --callout-text: #4338ca;
}

/* Explicit Dark Theme Variables */
:root[data-theme="dark"],
html[data-theme="dark"],
body[data-theme="dark"],
[data-theme="dark"] {
  --text-muted: #929bb0;
  --card-subtle-bg: #141722;
  --card-subtle-border: #262b39;
  --card-subtle-hover: #191d2a;
  --card-checked-bg: rgba(165, 154, 255, 0.1);
  --input-bg-clean: #10131b;
  --callout-bg: rgba(139, 92, 246, 0.12);
  --callout-border: rgba(139, 92, 246, 0.3);
  --callout-text: #c4bbff;
}"""

if old_vars.replace('\r\n', '\n') in b_css.replace('\r\n', '\n'):
    b_css = b_css.replace(old_vars.replace('\r\n', '\n'), new_vars).replace(old_vars, new_vars)
    branches_css_path.write_text(b_css, encoding="utf-8")
    print("Updated branches.css root variables")
else:
    print("Warning: old_vars not found verbatim in branches.css")

# 2. Update employee-manage.css
em_css_path = Path("E:/react/aspiring-crm/apps/web/static/web/employee-manage.css")
e_css = em_css_path.read_text(encoding="utf-8")

old_em_vars = """/* Employee Management Polished UI Stylesheet */

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

new_em_vars = """/* Employee Management Polished UI Stylesheet */

/* Base & Light Theme Variables (Default to Light) */
:root,
:root[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"],
[data-theme="light"] {
  --text-muted: #5d6880;
  --manage-card-bg: #ffffff;
  --manage-card-border: #e2e8f0;
  --toggle-card-bg: #ffffff;
  --toggle-card-border: #dce1ec;
  --toggle-card-hover: #f8fafc;
  --toggle-card-checked: #f5f3ff;
  --pill-bg: #ffffff;
  --pill-border: #dce1ec;
  --pill-hover: #f8fafc;
  --pill-checked: #f5f3ff;
  --input-bg-clean: #ffffff;
  --input-border-clean: #cdd4e2;
  --callout-bg: #f5f3ff;
  --callout-border: #ddd6fe;
  --callout-text: #3730a3;
  --subtle-box-bg: #f8fafc;
  --subtle-box-border: #e2e8f0;
}

/* Explicit Dark Theme Variables */
:root[data-theme="dark"],
html[data-theme="dark"],
body[data-theme="dark"],
[data-theme="dark"] {
  --text-muted: #929bb0;
  --manage-card-bg: #14171f;
  --manage-card-border: #262b39;
  --toggle-card-bg: rgba(255, 255, 255, 0.02);
  --toggle-card-border: #262b39;
  --toggle-card-hover: rgba(165, 154, 255, 0.04);
  --toggle-card-checked: rgba(165, 154, 255, 0.1);
  --pill-bg: rgba(255, 255, 255, 0.02);
  --pill-border: #262b39;
  --pill-hover: rgba(165, 154, 255, 0.04);
  --pill-checked: rgba(165, 154, 255, 0.12);
  --input-bg-clean: #10131b;
  --input-border-clean: #303647;
  --callout-bg: rgba(165, 154, 255, 0.08);
  --callout-border: rgba(165, 154, 255, 0.2);
  --callout-text: #edf0f7;
  --subtle-box-bg: rgba(255, 255, 255, 0.02);
  --subtle-box-border: #262b39;
}"""

if old_em_vars.replace('\r\n', '\n') in e_css.replace('\r\n', '\n'):
    e_css = e_css.replace(old_em_vars.replace('\r\n', '\n'), new_em_vars).replace(old_em_vars, new_em_vars)
    em_css_path.write_text(e_css, encoding="utf-8")
    print("Updated employee-manage.css root variables")
else:
    print("Warning: old_em_vars not found verbatim in employee-manage.css")

# 3. Update attendance.css
att_css_path = Path("E:/react/aspiring-crm/apps/web/static/web/attendance.css")
a_css = att_css_path.read_text(encoding="utf-8")

old_att_vars = """:root {
  --att-card-bg: var(--panel, #14171f);
  --att-card-border: var(--border, #262b39);
  --att-subtle-bg: #141722;
  --att-subtle-border: #262b39;
  --att-subtle-hover: #191d2a;
  --att-input-bg: #10131b;
  --att-input-border: #303647;
  --att-text: var(--text, #edf0f7);
  --att-muted: var(--muted, #929bb0);
  --att-accent: var(--accent, #a59aff);
  --att-mint: #53e0b7;
  --att-green: #4ade80;
  --att-amber: #fbbf24;
  --att-red: #f87171;
  --att-blue: #60a5fa;
  --att-purple: #c084fc;
}

[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"] {
  --att-card-bg: #ffffff;
  --att-card-border: #e2e8f0;
  --att-subtle-bg: #ffffff;
  --att-subtle-border: #dce1ec;
  --att-subtle-hover: #f8fafc;
  --att-input-bg: #ffffff;
  --att-input-border: #cdd4e2;
  --att-text: #20283b;
  --att-muted: #5d6880;
  --att-accent: #6751b9;
  --att-mint: #10b981;
  --att-green: #16a34a;
  --att-amber: #d97706;
  --att-red: #dc2626;
  --att-blue: #2563eb;
  --att-purple: #7c3aed;
}"""

new_att_vars = """/* Base & Light Theme Variables (Default to Light) */
:root,
:root[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"],
[data-theme="light"] {
  --att-card-bg: #ffffff;
  --att-card-border: #e2e8f0;
  --att-subtle-bg: #ffffff;
  --att-subtle-border: #dce1ec;
  --att-subtle-hover: #f8fafc;
  --att-input-bg: #ffffff;
  --att-input-border: #cdd4e2;
  --att-text: #20283b;
  --att-muted: #5d6880;
  --att-accent: #6751b9;
  --att-mint: #10b981;
  --att-green: #16a34a;
  --att-amber: #d97706;
  --att-red: #dc2626;
  --att-blue: #2563eb;
  --att-purple: #7c3aed;
}

/* Explicit Dark Theme Variables */
:root[data-theme="dark"],
html[data-theme="dark"],
body[data-theme="dark"],
[data-theme="dark"] {
  --att-card-bg: #14171f;
  --att-card-border: #262b39;
  --att-subtle-bg: #141722;
  --att-subtle-border: #262b39;
  --att-subtle-hover: #191d2a;
  --att-input-bg: #10131b;
  --att-input-border: #303647;
  --att-text: #edf0f7;
  --att-muted: #929bb0;
  --att-accent: #a59aff;
  --att-mint: #53e0b7;
  --att-green: #4ade80;
  --att-amber: #fbbf24;
  --att-red: #f87171;
  --att-blue: #60a5fa;
  --att-purple: #c084fc;
}"""

if old_att_vars.replace('\r\n', '\n') in a_css.replace('\r\n', '\n'):
    a_css = a_css.replace(old_att_vars.replace('\r\n', '\n'), new_att_vars).replace(old_att_vars, new_att_vars)
    att_css_path.write_text(a_css, encoding="utf-8")
    print("Updated attendance.css root variables")
else:
    print("Warning: old_att_vars not found verbatim in attendance.css")

