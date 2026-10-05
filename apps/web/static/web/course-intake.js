(() => {
  const form = document.querySelector('.course-import-form');
  if (!form) return;
  const custom = form.querySelector('.custom-course-field');
  const input = form.querySelector('[name="preferred_course_custom"]');
  function update() {
    const others = form.querySelector('[name="preferred_course"]:checked')?.value === 'OTHERS';
    custom.hidden = !others; input.required = others;
    if (!others) input.value = '';
  }
  form.querySelectorAll('[name="preferred_course"]').forEach(el => el.addEventListener('change', update));
  update();
})();
