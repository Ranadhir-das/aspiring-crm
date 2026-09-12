(() => {
  const key = 'aspiring-crm-theme';
  let theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch {}
  const apply = value => {
    theme = value === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      const next = theme === 'dark' ? 'light' : 'dark';
      button.textContent = next === 'light' ? '☀ Light mode' : '☾ Dark mode';
      button.setAttribute('aria-label', `Switch to ${next} mode`);
      button.title = `Switch to ${next} mode`;
    });
  };
  apply(theme);
  document.addEventListener('DOMContentLoaded', () => {
    apply(theme);
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        apply(theme === 'dark' ? 'light' : 'dark');
        try { localStorage.setItem(key, theme); } catch {}
      });
    });
  });
  window.addEventListener('storage', event => {
    if (event.key === key) apply(event.newValue);
  });
})();
