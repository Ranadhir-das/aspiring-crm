(() => {
  const form = document.querySelector('.course-import-form');
  if (!form) return;

  // 1. Custom course toggle
  const custom = form.querySelector('.custom-course-field');
  const customInput = form.querySelector('[name="preferred_course_custom"]');
  function updateCustomCourse() {
    const others = form.querySelector('[name="preferred_course"]:checked')?.value === 'OTHERS';
    if (custom) custom.hidden = !others;
    if (customInput) {
      customInput.required = others;
      if (!others) customInput.value = '';
    }
  }
  form.querySelectorAll('[name="preferred_course"]').forEach(el => el.addEventListener('change', updateCustomCourse));
  updateCustomCourse();

  // 2. Import method tabs (Paste vs File)
  const methodInput = form.querySelector('[name="import_method"]');
  const pasteSection = document.getElementById('method-section-paste');
  const fileSection = document.getElementById('method-section-file');
  const previewBtn = document.getElementById('preview-file-btn');
  const tabs = form.querySelectorAll('.method-tab');

  function setMethod(method) {
    if (methodInput) methodInput.value = method;
    tabs.forEach(tab => {
      const isActive = tab.getAttribute('data-method') === method;
      tab.classList.toggle('active', isActive);
      tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    if (method === 'file') {
      if (pasteSection) pasteSection.style.display = 'none';
      if (fileSection) fileSection.style.display = 'grid';
      if (previewBtn) previewBtn.style.display = 'inline-block';
    } else {
      if (pasteSection) pasteSection.style.display = 'grid';
      if (fileSection) fileSection.style.display = 'none';
      if (previewBtn) previewBtn.style.display = 'none';
    }
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      e.preventDefault();
      const method = tab.getAttribute('data-method');
      setMethod(method);
    });
  });

  // Initialize method based on hidden input or presence of file input errors
  const initialMethod = methodInput?.value || (fileSection?.querySelector('.errorlist') ? 'file' : 'paste');
  setMethod(initialMethod);

  // 3. Double-click & in-flight submission prevention
  let isSubmitting = false;
  let clickedAction = 'import';

  form.querySelectorAll('button[type="submit"]').forEach(btn => {
    btn.addEventListener('click', () => {
      clickedAction = btn.value || 'import';
    });
  });

  form.addEventListener('submit', (e) => {
    if (isSubmitting) {
      e.preventDefault();
      return false;
    }

    isSubmitting = true;
    const submitBtn = document.getElementById('submit-import-btn');
    if (submitBtn) {
      submitBtn.disabled = true;
      if (clickedAction === 'preview') {
        submitBtn.textContent = 'Preparing preview...';
      } else {
        submitBtn.textContent = 'Importing leads...';
      }
    }
    if (previewBtn) previewBtn.disabled = true;

    // Safety timeout in case the submit is blocked by browser validation or aborted
    setTimeout(() => {
      isSubmitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Import Leads ↗';
      }
      if (previewBtn) previewBtn.disabled = false;
    }, 15000);
  });
})();
