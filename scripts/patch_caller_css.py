with open('apps/web/static/web/caller-detail.css', 'r', encoding='utf-8') as f:
    caller_css = f.read()

target_caller = """/* Glassmorphism HUD Tooltip for Caller Chart */
.caller-chart-tooltip {
  position: absolute;
  pointer-events: none;
  background: rgba(15, 23, 42, 0.92);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 12px;
  padding: 12px 16px;
  color: #ffffff;
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.4), 0 4px 12px rgba(0, 0, 0, 0.25);
  z-index: 100;
  transform: translate(-50%, -115%);
  min-width: 190px;
  white-space: nowrap;
  transition: left 0.06s ease-out, top 0.06s ease-out;
}"""

replacement_caller = """/* Glassmorphism HUD Tooltip for Caller Chart (Theme-Adaptive, Interactive) */
.caller-chart-tooltip {
  position: absolute;
  pointer-events: auto;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.25), 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  min-width: 210px;
  max-width: 280px;
  max-height: 240px;
  display: flex;
  flex-direction: column;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  transition: opacity 0.15s ease;
  user-select: auto;
}

[data-theme="light"] .caller-chart-tooltip {
  background: rgba(255, 255, 255, 0.96);
  color: #0f172a;
  border-color: #cbd5e1;
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.14), 0 4px 12px rgba(0, 0, 0, 0.08);
}

[data-theme="dark"] .caller-chart-tooltip {
  background: rgba(15, 23, 42, 0.94);
  color: #f8fafc;
  border-color: rgba(255, 255, 255, 0.14);
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.45), 0 4px 12px rgba(0, 0, 0, 0.25);
}"""

if target_caller in caller_css:
    caller_css = caller_css.replace(target_caller, replacement_caller)
    with open('apps/web/static/web/caller-detail.css', 'w', encoding='utf-8') as f:
        f.write(caller_css)
    print("Successfully patched caller-detail.css")
else:
    print("Target not found in caller-detail.css!")

