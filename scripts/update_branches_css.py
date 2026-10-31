# Script to update branches.css with bulletproof light/dark theme support
import re

css_path = r"E:\react\aspiring-crm\apps\web\static\web\branches.css"

with open(css_path, "r", encoding="utf-8") as f:
    content = f.read()

# Let's replace the top variable block and ensure bulletproof light/dark support
top_block_old = """/* Branch Management and Employee Assignments CSS */

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

[data-theme="light"],
html[data-theme="light"],
body[data-theme="light"] {
  --text-muted: #5d6880;
  --card-subtle-bg: #ffffff;
  --card-subtle-border: #dce1ec;
  --card-subtle-hover: #f8fafc;
  --card-checked-bg: rgba(103, 81, 185, 0.06);
  --input-bg-clean: #ffffff;
  --callout-bg: #f5f3ff;
  --callout-border: #ddd6fe;
  --callout-text: #4338ca;
}"""

top_block_new = """/* Branch Management and Employee Assignments CSS */

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

if top_block_old in content:
    content = content.replace(top_block_old, top_block_new, 1)
else:
    print("WARNING: top_block_old not found exactly")

# Now append comprehensive light theme overrides with maximum specificity
comprehensive_light_rules = """

/* ==========================================================================
   BULLETPROOF LIGHT THEME RULES FOR WORKPLACE & SHIFT POLICY ELEMENTS
   ========================================================================== */

:root[data-theme="light"] .branch-assign-card,
html[data-theme="light"] .branch-assign-card,
[data-theme="light"] .branch-assign-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #e2e8f0 !important;
}

:root[data-theme="light"] .branch-assign-card strong,
html[data-theme="light"] .branch-assign-card strong,
[data-theme="light"] .branch-assign-card strong {
  color: #1e293b !important;
}

:root[data-theme="light"] .branch-assign-card p,
:root[data-theme="light"] .branch-assign-card .muted,
html[data-theme="light"] .branch-assign-card p,
html[data-theme="light"] .branch-assign-card .muted,
[data-theme="light"] .branch-assign-card p,
[data-theme="light"] .branch-assign-card .muted {
  color: #64748b !important;
}

:root[data-theme="light"] .branches-checklist-box,
html[data-theme="light"] .branches-checklist-box,
[data-theme="light"] .branches-checklist-box {
  background: #f8fafc !important;
  background-color: #f8fafc !important;
  border-color: #dce1ec !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

:root[data-theme="light"] .branches-checklist-box label,
html[data-theme="light"] .branches-checklist-box label,
[data-theme="light"] .branches-checklist-box label {
  color: #1e293b !important;
}

:root[data-theme="light"] .policy-option-card,
html[data-theme="light"] .policy-option-card,
[data-theme="light"] .policy-option-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #dce1ec !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

:root[data-theme="light"] .policy-option-card:hover,
html[data-theme="light"] .policy-option-card:hover,
[data-theme="light"] .policy-option-card:hover {
  background: #f8fafc !important;
  background-color: #f8fafc !important;
  border-color: var(--accent) !important;
}

:root[data-theme="light"] .policy-option-card:has(input:checked),
html[data-theme="light"] .policy-option-card:has(input:checked),
[data-theme="light"] .policy-option-card:has(input:checked) {
  background: #f5f3ff !important;
  background-color: #f5f3ff !important;
  border-color: #8b5cf6 !important;
}

:root[data-theme="light"] .policy-option-card label,
html[data-theme="light"] .policy-option-card label,
[data-theme="light"] .policy-option-card label {
  color: #1e293b !important;
}

:root[data-theme="light"] .policy-option-card p,
:root[data-theme="light"] .policy-option-card .muted,
html[data-theme="light"] .policy-option-card p,
html[data-theme="light"] .policy-option-card .muted,
[data-theme="light"] .policy-option-card p,
[data-theme="light"] .policy-option-card .muted {
  color: #64748b !important;
}

:root[data-theme="light"] .policy-callout-box,
html[data-theme="light"] .policy-callout-box,
[data-theme="light"] .policy-callout-box {
  background: #f5f3ff !important;
  background-color: #f5f3ff !important;
  border-color: #ddd6fe !important;
  color: #4338ca !important;
}

:root[data-theme="light"] .branches-filter-card,
:root[data-theme="light"] .branches-metric-card,
html[data-theme="light"] .branches-filter-card,
html[data-theme="light"] .branches-metric-card,
[data-theme="light"] .branches-filter-card,
[data-theme="light"] .branches-metric-card {
  background: #ffffff !important;
  background-color: #ffffff !important;
  border-color: #dce1ec !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

:root[data-theme="light"] .map-picker-container,
html[data-theme="light"] .map-picker-container,
[data-theme="light"] .map-picker-container {
  background: #f8fafc !important;
  background-color: #f8fafc !important;
  border-color: #dce1ec !important;
}
"""

content += comprehensive_light_rules

with open(css_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Updated branches.css successfully!")

