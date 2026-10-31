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

  // 3. File selection & staged preview state handling
  const fileInput = form.querySelector('[name="file"]');
  const importTokenInput = form.querySelector('[name="import_token"]');
  const fileSelectedInfo = document.getElementById('file-selected-info');
  const stagedBanner = document.getElementById('file-staged-banner');
  const previewSection = document.getElementById('import-preview-section');

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      if (fileInput.files && fileInput.files.length > 0) {
        const f = fileInput.files[0];
        const sizeKb = (f.size / 1024).toFixed(1);
        if (fileSelectedInfo) {
          fileSelectedInfo.textContent = `Selected: ${f.name} (${sizeKb} KB) · Click Preview or Import Leads`;
          fileSelectedInfo.style.display = 'block';
        }
        // When user picks a new local file, clear previously staged server token
        if (importTokenInput) importTokenInput.value = '';
        if (stagedBanner) stagedBanner.style.display = 'none';
      } else {
        if (fileSelectedInfo) fileSelectedInfo.style.display = 'none';
      }
    });
  }

  // Initialize method based on staged banner, preview section, hidden input, or file errors
  const hasFilePreview = !!stagedBanner || !!previewSection || (importTokenInput && importTokenInput.value.trim().length > 0);
  const initialMethod = hasFilePreview ? 'file' : (methodInput?.value || (fileSection?.querySelector('.errorlist') ? 'file' : 'paste'));
  setMethod(initialMethod);

  // 4. Double-click & in-flight submission prevention
  let isSubmitting = false;
  let clickedAction = 'import';

  document.querySelectorAll('button[type="submit"]').forEach(btn => {
    if (btn.form === form || btn.closest('.course-import-form') || btn.getAttribute('form') === form.id) {
      btn.addEventListener('click', () => {
        clickedAction = btn.value || 'import';
      });
    }
  });

  form.addEventListener('submit', (e) => {
    if (isSubmitting) {
      e.preventDefault();
      return false;
    }

    isSubmitting = true;
    const submitBtn = document.getElementById('submit-import-btn');
    const previewImportBtn = document.getElementById('preview-import-btn');

    if (clickedAction === 'preview') {
      if (submitBtn) submitBtn.textContent = 'Preparing preview...';
      if (previewBtn) previewBtn.textContent = 'Preparing preview...';
    } else {
      if (submitBtn) submitBtn.innerHTML = '<span>Importing leads...</span>';
      if (previewImportBtn) previewImportBtn.innerHTML = '<span>Importing leads...</span>';
    }

    if (submitBtn) submitBtn.disabled = true;
    if (previewBtn) previewBtn.disabled = true;
    if (previewImportBtn) previewImportBtn.disabled = true;

    // Safety timeout in case the submit is blocked by browser validation or aborted
    setTimeout(() => {
      isSubmitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>Import Leads</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>';
      }
      if (previewBtn) {
        previewBtn.disabled = false;
        previewBtn.textContent = 'Preview file';
      }
      if (previewImportBtn) {
        previewImportBtn.disabled = false;
        previewImportBtn.innerHTML = '<span>Import Leads Now</span><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>';
      }
    }, 15000);
  });
})();
