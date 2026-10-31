# Script to patch sidebar searchbar border, area, and interaction in sidebar.html, crm.css, and crm.js

# 1. Patch sidebar.html
with open('apps/web/templates/web/sidebar.html', 'r', encoding='utf-8') as f:
    sidebar_html = f.read()

target_html = """  <!-- Real-time Navigation Search (Ctrl+K) -->
  <div class="sidebar-search-box">
    <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
    <input type="text" id="nav-search-input" placeholder="Search navigation (Ctrl+K)" autocomplete="off" spellcheck="false" aria-label="Search navigation">
    <button type="button" class="search-clear-btn" id="nav-search-clear" style="display:none;" aria-label="Clear search">✕</button>
    <kbd class="search-shortcut">Ctrl+K</kbd>
  </div>"""

repl_html = """  <!-- Real-time Navigation Search (Ctrl+K) -->
  <label class="sidebar-search-box" for="nav-search-input">
    <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
    <input type="text" id="nav-search-input" placeholder="Search navigation..." autocomplete="off" spellcheck="false" aria-label="Search navigation">
    <button type="button" class="search-clear-btn" id="nav-search-clear" style="display:none;" aria-label="Clear search">✕</button>
    <kbd class="search-shortcut" id="nav-search-shortcut">Ctrl+K</kbd>
  </label>"""

# Normalize line endings for replacement
norm_sidebar = sidebar_html.replace('\r\n', '\n')
norm_target = target_html.replace('\r\n', '\n')
norm_repl = repl_html.replace('\r\n', '\n')

if norm_target in norm_sidebar:
    norm_sidebar = norm_sidebar.replace(norm_target, norm_repl)
    with open('apps/web/templates/web/sidebar.html', 'w', encoding='utf-8') as f:
        f.write(norm_sidebar)
    print("[OK] Patched sidebar.html")
else:
    print("[WARN] target_html not matched in sidebar.html")


# 2. Patch crm.css
with open('apps/web/static/web/crm.css', 'r', encoding='utf-8') as f:
    crm_css = f.read()

target_css_dark = """.sidebar-search-box {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  min-height: 38px;
  max-height: 38px;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 9px;
  padding: 0 10px;
  margin: 12px 0 10px;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}

.sidebar-search-box:focus-within {
  border-color: #8b5cf6;
  background: rgba(139, 92, 246, 0.06);
  box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.15);
}

.sidebar-search-box .search-icon {
  stroke: #64748b;
  flex-shrink: 0;
  transition: stroke 0.15s;
}

.sidebar-search-box:focus-within .search-icon {
  stroke: #a78bfa;
}

.sidebar-search-box input {
  flex: 1;
  min-width: 0;
  height: 100%;
  box-sizing: border-box;
  border: none;
  background: none;
  outline: none;
  font-size: 12px;
  color: #f1f5f9;
  padding: 0;
}

.sidebar-search-box input::placeholder {
  color: #64748b;
  font-size: 11.5px;
}"""

repl_css_dark = """.sidebar-search-box {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  min-height: 38px;
  max-height: 38px;
  box-sizing: border-box;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 9px;
  padding: 0 10px;
  margin: 12px 0 10px;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
  flex-shrink: 0;
  cursor: text;
  width: 100%;
}

.sidebar-search-box:hover {
  border-color: rgba(255, 255, 255, 0.24);
  background: rgba(255, 255, 255, 0.06);
}

.sidebar-search-box:focus-within {
  border-color: #8b5cf6 !important;
  background: rgba(139, 92, 246, 0.08) !important;
  box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2) !important;
}

.sidebar-search-box .search-icon {
  stroke: #64748b;
  flex-shrink: 0;
  transition: stroke 0.15s;
  pointer-events: none;
}

.sidebar-search-box:hover .search-icon {
  stroke: #94a3b8;
}

.sidebar-search-box:focus-within .search-icon {
  stroke: #a78bfa;
}

.sidebar-search-box input,
.sidebar-search-box input:focus,
.sidebar-search-box input:hover,
.sidebar-search-box input:active {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
  height: 100% !important;
  line-height: normal !important;
  box-sizing: border-box !important;
  border: 0 !important;
  outline: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
  font-size: 12px !important;
  color: #f1f5f9 !important;
  padding: 0 !important;
  margin: 0 !important;
  border-radius: 0 !important;
  -webkit-appearance: none !important;
  appearance: none !important;
  cursor: text !important;
}

.sidebar-search-box input::placeholder {
  color: #64748b;
  font-size: 11.5px;
  opacity: 0.95;
}"""

target_css_light = """[data-theme=light] .sidebar-search-box {
  background: #ffffff;
  border-color: #e2e8f0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
}

[data-theme=light] .sidebar-search-box:focus-within {
  border-color: #6366f1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.12);
}

[data-theme=light] .sidebar-search-box input {
  color: #0f172a;
}

[data-theme=light] .sidebar-search-box input::placeholder {
  color: #94a3b8;
}

[data-theme=light] .search-icon {
  stroke: #94a3b8;
}

[data-theme=light] .sidebar-search-box:focus-within .search-icon {
  stroke: #6366f1;
}"""

repl_css_light = """[data-theme=light] .sidebar-search-box {
  background: #ffffff !important;
  border: 1px solid #cbd5e1 !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
}

[data-theme=light] .sidebar-search-box:hover {
  border-color: #94a3b8 !important;
  background: #f8fafc !important;
}

[data-theme=light] .sidebar-search-box:focus-within {
  border-color: #6366f1 !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.16) !important;
}

[data-theme=light] .sidebar-search-box input,
[data-theme=light] .sidebar-search-box input:focus,
[data-theme=light] .sidebar-search-box input:hover,
[data-theme=light] .sidebar-search-box input:active {
  color: #0f172a !important;
  background: transparent !important;
  border: 0 !important;
  outline: 0 !important;
  box-shadow: none !important;
}

[data-theme=light] .sidebar-search-box input::placeholder {
  color: #94a3b8 !important;
}

[data-theme=light] .search-icon {
  stroke: #94a3b8;
}

[data-theme=light] .sidebar-search-box:hover .search-icon {
  stroke: #64748b;
}

[data-theme=light] .sidebar-search-box:focus-within .search-icon {
  stroke: #6366f1 !important;
}"""

norm_css = crm_css.replace('\r\n', '\n')
norm_target_dark = target_css_dark.replace('\r\n', '\n')
norm_repl_dark = repl_css_dark.replace('\r\n', '\n')
norm_target_light = target_css_light.replace('\r\n', '\n')
norm_repl_light = repl_css_light.replace('\r\n', '\n')

if norm_target_dark in norm_css and norm_target_light in norm_css:
    norm_css = norm_css.replace(norm_target_dark, norm_repl_dark).replace(norm_target_light, norm_repl_light)
    with open('apps/web/static/web/crm.css', 'w', encoding='utf-8') as f:
        f.write(norm_css)
    print("[OK] Patched crm.css for dark & light mode")
else:
    print("[WARN] CSS targets not matched in crm.css")


# 3. Patch crm.js
with open('apps/web/static/web/crm.js', 'r', encoding='utf-8') as f:
    crm_js = f.read()

target_js = """  if (navSearchClear) {
    navSearchClear.style.display = q ? 'flex' : 'none';
  }"""

repl_js = """  if (navSearchClear) {
    navSearchClear.style.display = q ? 'flex' : 'none';
  }
  const navShortcut = document.getElementById('nav-search-shortcut');
  if (navShortcut) {
    navShortcut.style.display = q ? 'none' : 'inline-block';
  }"""

norm_js = crm_js.replace('\r\n', '\n')
norm_target_js = target_js.replace('\r\n', '\n')
norm_repl_js = repl_js.replace('\r\n', '\n')

if norm_target_js in norm_js:
    norm_js = norm_js.replace(norm_target_js, norm_repl_js)
    # Also add the container click focus handler
    focus_handler = """
// Ensure clicking anywhere inside .sidebar-search-box area focuses input
document.querySelector('.sidebar-search-box')?.addEventListener('click', (e) => {
  if (e.target && (e.target.id === 'nav-search-clear' || e.target.closest('#nav-search-clear'))) return;
  navSearchInput?.focus();
});
"""
    if 'document.querySelector(\'.sidebar-search-box\')?.addEventListener' not in norm_js:
        norm_js += focus_handler

    with open('apps/web/static/web/crm.js', 'w', encoding='utf-8') as f:
        f.write(norm_js)
    print("[OK] Patched crm.js for search shortcut toggling and click focus")
else:
    print("[WARN] target_js not matched in crm.js")

