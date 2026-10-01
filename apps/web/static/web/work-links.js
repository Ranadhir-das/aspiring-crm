document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-work-links]').forEach(group => {
    const rows = group.querySelector('[data-link-rows]');
    const add = group.querySelector('[data-add-link]');
    function refresh() {
      Array.from(rows.children).forEach((row, index) => {
        const input = row.querySelector('input');
        input.id = `${group.dataset.id}_${index + 1}`;
        const label = row.querySelector('label');
        label.htmlFor = input.id;
        label.textContent = `Work Link ${index + 1}`;
        row.querySelector('button').hidden = rows.children.length === 1;
      });
      add.disabled = rows.children.length >= 30;
    }
    add.addEventListener('click', () => {
      if (rows.children.length >= 30) return;
      const row = rows.firstElementChild.cloneNode(true);
      row.querySelector('input').value = '';
      rows.append(row);
      refresh();
      row.querySelector('input').focus();
    });
    rows.addEventListener('click', event => {
      if (event.target.matches('[data-remove-link]') && rows.children.length > 1) {
        event.target.closest('[data-link-row]').remove();
        refresh();
      }
    });
    refresh();
  });
});
