with open('apps/web/static/web/performance-command.css', 'r', encoding='utf-8') as f:
    perf_css = f.read()

target_perf = """/* Glassmorphism HUD Tooltip */
.telemetry-chart-tooltip {
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
  min-width: 200px;
  max-width: 320px;
  white-space: nowrap;
  transition: left 0.06s ease-out, top 0.06s ease-out;
}

.hud-tooltip-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  font-size: 0.76rem;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.hud-time-icon {
  font-size: 0.85rem;
}

.hud-total-pill {
  background: rgba(255, 255, 255, 0.12);
  color: #f8fafc;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
}

.hud-tooltip-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 200px;
  overflow-y: auto;
}

.hud-tooltip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  font-size: 0.82rem;
}

.hud-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 6px currentColor;
}

.hud-name {
  color: #e2e8f0;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 140px;
}

.hud-val {
  color: #ffffff;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}"""

replacement_perf = """/* Glassmorphism HUD Tooltip (Theme-Adaptive, Clickable & Scrollable) */
.telemetry-chart-tooltip {
  position: absolute;
  pointer-events: auto;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.25), 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 100;
  min-width: 220px;
  max-width: 320px;
  max-height: 260px;
  display: flex;
  flex-direction: column;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  transition: opacity 0.15s ease;
  user-select: auto;
}

[data-theme="light"] .telemetry-chart-tooltip {
  background: rgba(255, 255, 255, 0.96);
  color: #0f172a;
  border-color: #cbd5e1;
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.14), 0 4px 12px rgba(0, 0, 0, 0.08);
}

[data-theme="dark"] .telemetry-chart-tooltip {
  background: rgba(15, 23, 42, 0.94);
  color: #f8fafc;
  border-color: rgba(255, 255, 255, 0.14);
  box-shadow: 0 16px 36px -4px rgba(0, 0, 0, 0.45), 0 4px 12px rgba(0, 0, 0, 0.25);
}

.hud-tooltip-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--border);
  font-size: 0.76rem;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

[data-theme="light"] .hud-tooltip-header {
  border-bottom-color: #e2e8f0;
  color: #64748b;
}

.hud-time-icon {
  font-size: 0.85rem;
}

.hud-total-pill {
  background: var(--bg);
  color: var(--text);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
}

[data-theme="light"] .hud-total-pill {
  background: #f1f5f9;
  color: #1e293b;
  border-color: #e2e8f0;
}

.hud-tooltip-rows {
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 180px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.hud-tooltip-rows::-webkit-scrollbar {
  width: 5px;
}

.hud-tooltip-rows::-webkit-scrollbar-thumb {
  background-color: var(--border);
  border-radius: 3px;
}

.hud-tooltip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  font-size: 0.82rem;
  padding: 5px 8px;
  border-radius: 6px;
  text-decoration: none;
  color: inherit;
  transition: all 0.12s ease;
  cursor: pointer;
}

.hud-tooltip-row:hover {
  background: var(--surface-hover, rgba(255, 255, 255, 0.06));
}

[data-theme="light"] .hud-tooltip-row:hover {
  background: #f1f5f9;
}

.hud-row-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.hud-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: 0 0 6px currentColor;
}

.hud-name {
  color: var(--text);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 140px;
}

[data-theme="light"] .hud-name {
  color: #0f172a;
}

.hud-val {
  color: var(--text);
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

[data-theme="light"] .hud-val {
  color: #0f172a;
}"""

if target_perf in perf_css:
    perf_css = perf_css.replace(target_perf, replacement_perf)
    with open('apps/web/static/web/performance-command.css', 'w', encoding='utf-8') as f:
        f.write(perf_css)
    print("Successfully patched performance-command.css")
else:
    print("Target not found in performance-command.css!")

