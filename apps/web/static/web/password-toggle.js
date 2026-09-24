(() => {
  const eye = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="password-eye-slash" d="m3 3 18 18"/></svg>';
  document.querySelectorAll('input[type="password"]').forEach((input, index) => {
    if (input.closest('.password-field')) return;
    if (!input.id) input.id = `password-field-${index}`;
    const wrapper = document.createElement('div');
    wrapper.className = 'password-field';
    input.before(wrapper);
    wrapper.append(input);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-toggle';
    button.setAttribute('aria-controls', input.id);
    button.innerHTML = eye;
    const setVisible = (visible) => {
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      button.title = visible ? 'Hide password' : 'Show password';
      button.classList.toggle('is-visible', visible);
    };
    setVisible(false);
    button.disabled = input.disabled;
    button.addEventListener('click', () => setVisible(input.type === 'password'));
    input.form?.addEventListener('reset', () => setVisible(false));
    wrapper.append(button);
  });
})();
