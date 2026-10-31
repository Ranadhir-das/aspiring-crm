with open('apps/web/static/web/caller-detail.css', 'r', encoding='utf-8') as f:
    caller_css = f.read()

# 1. Add hero telemetry strip styling right after .caller-profile-card
hero_css_target = """.caller-profile-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}"""

hero_css_replacement = """.caller-profile-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 24px 28px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.04);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.caller-hero-telemetry-strip {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 16px;
  margin-top: 10px;
  border-top: 1px solid var(--border);
  font-size: 0.85rem;
  color: var(--text-muted);
}

.hero-call-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.hero-call-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.hero-call-dot.blue { background: #2563eb; box-shadow: 0 0 6px #2563eb; }
.hero-call-dot.emerald { background: #10b981; box-shadow: 0 0 6px #10b981; }
.hero-call-dot.cyan { background: #06b6d4; box-shadow: 0 0 6px #06b6d4; }
.hero-call-dot.amber { background: #f59e0b; box-shadow: 0 0 6px #f59e0b; }

.hero-call-lbl {
  font-weight: 600;
  color: var(--text-muted);
}

.hero-call-item strong {
  font-size: 0.96rem;
  font-weight: 800;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.hero-call-rate {
  font-size: 0.78rem;
  color: #10b981;
  font-weight: 700;
}

.hero-call-divider {
  color: var(--border);
  user-select: none;
}"""

if hero_css_target in caller_css:
    caller_css = caller_css.replace(hero_css_target, hero_css_replacement)
    print("Added hero telemetry strip CSS to caller-detail.css")
else:
    print("Hero CSS target not found in caller-detail.css")

# 2. Refine .assigned-leads-pipeline-card and .status-summary-grid
pipeline_css_target = """.assigned-leads-pipeline-card {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 22px 24px !important;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.04) !important;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pipeline-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}

.pipeline-card-header h2 {
  font-size: 1.22rem;
  font-weight: 800;
  color: var(--text);
  margin: 0 0 2px 0;
  letter-spacing: -0.01em;
}

.pipeline-card-header p {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin: 0;
}

.status-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.status-summary-item {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-decoration: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}"""

pipeline_css_replacement = """.assigned-leads-pipeline-card {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 24px 28px !important;
  margin: 24px 0 !important;
  box-shadow: 0 6px 24px -4px rgba(0, 0, 0, 0.06), 0 2px 8px rgba(0, 0, 0, 0.03) !important;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.pipeline-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.pipeline-card-header h2 {
  font-size: 1.25rem;
  font-weight: 800;
  color: var(--text);
  margin: 0 0 4px 0;
  letter-spacing: -0.01em;
}

.pipeline-card-header p {
  font-size: 0.86rem;
  color: var(--text-muted);
  margin: 0;
}

.status-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 16px;
  margin-top: 6px;
}

.status-summary-item {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  text-decoration: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03);
}"""

if pipeline_css_target in caller_css:
    caller_css = caller_css.replace(pipeline_css_target, pipeline_css_replacement)
    print("Updated pipeline CSS in caller-detail.css")
else:
    print("Pipeline CSS target not found in caller-detail.css")

with open('apps/web/static/web/caller-detail.css', 'w', encoding='utf-8') as f:
    f.write(caller_css)
print("Saved caller-detail.css successfully!")

