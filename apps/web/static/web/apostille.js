(() => {
  'use strict';
  const form = document.getElementById('apostille-form');
  if (!form) return;
  const external = document.getElementById('apostille-external');
  const existing = document.getElementById('apostille-lead');
  const lead = document.getElementById('id_lead');
  const toggle = () => {
    const isExternal = form.querySelector('[name="candidate_type"]:checked')?.value === 'EXTERNAL';
    form.querySelectorAll('.source-card').forEach(card => card.classList.toggle('active', card.querySelector('input').checked));
    external.hidden = !isExternal;
    existing.hidden = isExternal;
    external.querySelectorAll('input').forEach(input => {
      input.disabled = !isExternal;
      input.required = isExternal;
    });
    lead.disabled = isExternal;
    lead.required = !isExternal;
  };
  form.querySelectorAll('[name="candidate_type"]').forEach(input => input.addEventListener('change', toggle));
  toggle();

  const amount = document.getElementById('id_amount_received');
  const preview = document.getElementById('apostille-points');
  let timer;
  let previewRequest;
  const updatePreview = async () => {
    previewRequest?.abort();
    previewRequest = new AbortController();
    if (!amount.value) { preview.textContent = 'Enter an amount'; return; }
    preview.textContent = 'Calculating...';
    try {
      const url = new URL(form.dataset.previewUrl, window.location.origin);
      url.searchParams.set('amount', amount.value);
      const response = await fetch(url, {signal: previewRequest.signal, credentials: 'same-origin'});
      const data = await response.json();
      preview.textContent = response.ok ? String(data.points) : 'Enter a valid amount';
    } catch (error) {
      if (error.name !== 'AbortError') preview.textContent = 'Calculated when saved';
    }
  };
  amount.addEventListener('input', () => {
    previewRequest?.abort();
    clearTimeout(timer);
    preview.textContent = 'Calculating...';
    timer = setTimeout(updatePreview, 250);
  });
  updatePreview();

  const searchButton = document.getElementById('apostille-search');
  const searchInput = document.getElementById('apostille-lead-query');
  const status = document.getElementById('apostille-search-status');
  const search = async () => {
    if (searchButton.disabled) return;
    const query = searchInput.value.trim();
    if (!query) { status.textContent = 'Enter a name, phone, email or lead ID.'; return; }
    searchButton.disabled = true;
    status.textContent = 'Searching...';
    try {
      const url = new URL(form.dataset.searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      const response = await fetch(url, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('Search failed');
      const data = await response.json();
      const selected = lead.selectedOptions[0];
      const options = [new Option('Select a lead', '')];
      if (selected?.value) options.push(new Option(selected.text, selected.value, true, true));
      data.results.forEach(item => {
        if (String(item.id) !== selected?.value) options.push(new Option(item.label, String(item.id)));
      });
      lead.replaceChildren(...options);
      status.textContent = data.results.length ? 'Select a matching lead from the list (up to 30 results).' : 'No matching leads found.';
    } catch (_) { status.textContent = 'Could not search leads. Please try again.'; }
    finally { searchButton.disabled = false; }
  };
  searchButton.addEventListener('click', search);
  searchInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); search(); }
  });
})();
