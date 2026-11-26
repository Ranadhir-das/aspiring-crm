(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // 1. Accessible Responsive Tabbed Workspace (modern-web-guidance: tabs)
  // -------------------------------------------------------------------------
  const tablist = document.getElementById('ld-tablist');
  if (tablist) {
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    const panels = tabs
      .map((tab) => document.querySelector(`[role="tabpanel"][aria-labelledby="${CSS.escape(tab.id)}"]`))
      .filter(Boolean);

    function activateTab(selectedTab, { focusTab = false } = {}) {
      if (!selectedTab) return;
      tabs.forEach((tab, idx) => {
        const isSel = tab === selectedTab;
        tab.setAttribute('aria-selected', isSel ? 'true' : 'false');
        tab.tabIndex = isSel ? 0 : -1;
        tab.toggleAttribute('focusgroupstart', isSel);
        const panel = panels[idx];
        if (panel) {
          if (isSel) {
            panel.hidden = false;
          } else {
            panel.hidden = 'until-found';
          }
        }
      });
      if (focusTab) selectedTab.focus();
      try {
        selectedTab.scrollIntoView({ block: 'nearest', inline: 'nearest', container: 'nearest' });
      } catch (e) {
        selectedTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => activateTab(tab));
    });

    // Keyboard navigation (roving fallback when native focusgroup is unavailable)
    tablist.addEventListener('keydown', (e) => {
      const idx = tabs.indexOf(document.activeElement);
      if (idx === -1) return;
      let nextIdx = -1;
      if (e.key === 'ArrowRight') nextIdx = (idx + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = tabs.length - 1;
      if (nextIdx !== -1) {
        e.preventDefault();
        activateTab(tabs[nextIdx], { focusTab: true });
      }
    });

    // Sync tab selection when browser Find-in-Page reveals a hidden="until-found" panel
    panels.forEach((panel, idx) => {
      panel.addEventListener('beforematch', () => {
        activateTab(tabs[idx]);
      });
    });

    // Jump triggers inside top snapshot (e.g., clicking Calls tile opens Calls tab)
    document.addEventListener('click', (e) => {
      const jump = e.target.closest('[data-jump-tab]');
      if (!jump) return;
      const targetKey = jump.getAttribute('data-jump-tab');
      const targetTab = tabs.find((t) => t.dataset.tabKey === targetKey);
      if (targetTab) {
        activateTab(targetTab);
        tablist.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    });

    // Fallback scrollability fade hints when scroll-driven animations are unsupported
    if (!CSS.supports('animation-timeline', 'scroll()') && tabs.length > 1) {
      const first = tabs[0];
      const last = tabs[tabs.length - 1];
      const observer = new IntersectionObserver(
        (entries) => {
          for (const { target, intersectionRatio } of entries) {
            tablist.classList.toggle(target === first ? 'more-start' : 'more-end', intersectionRatio < 1);
          }
        },
        { root: tablist, threshold: 1, rootMargin: '1px' }
      );
      observer.observe(first);
      observer.observe(last);
    }
  }

  // -------------------------------------------------------------------------
  // 2. Native WhatsApp Handoff Modal <dialog> (modern-web-guidance: dialog)
  // -------------------------------------------------------------------------
  const waDialog = document.getElementById('wa-modal');
  if (waDialog) {
    waDialog.addEventListener('close', () => {
      waDialog.returnValue = '';
    });

    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e) => {
        const r = waDialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      waDialog.addEventListener('pointerdown', (e) => { pressedOutside = outside(e); });
      waDialog.addEventListener('pointerup', (e) => { if (pressedOutside && outside(e)) waDialog.close(); });
    }
  }

  function getCookie(name) {
    if (!document.cookie) return '';
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const c = cookies[i].trim();
      if (c.startsWith(name + '=')) return decodeURIComponent(c.slice(name.length + 1));
    }
    return '';
  }

  window.openWhatsAppModal = function openWhatsAppModal() {
    if (!waDialog) return;
    if (typeof waDialog.showModal === 'function') {
      if (!waDialog.open) waDialog.showModal();
    } else {
      waDialog.setAttribute('open', '');
    }
    const select = document.getElementById('wa-template-select');
    if (select && select.options.length > 1 && !select.value) {
      select.selectedIndex = 1;
      window.onTemplateChange(select);
    }
  };

  window.closeWhatsAppModal = function closeWhatsAppModal() {
    if (!waDialog) return;
    if (typeof waDialog.close === 'function') waDialog.close();
    else waDialog.removeAttribute('open');
  };

  window.onTemplateChange = function onTemplateChange(select) {
    if (!select) return;
    const opt = select.options[select.selectedIndex];
    let body = (opt && opt.getAttribute('data-body')) || '';
    const leadNameEl = document.getElementById('wa-lead-name');
    let parsedName = '';
    if (leadNameEl && leadNameEl.textContent) {
      try {
        parsedName = JSON.parse(leadNameEl.textContent);
      } catch (e) {
        parsedName = leadNameEl.textContent.trim();
      }
    }
    body = body.replace(/\{name\}/g, parsedName);
    const textarea = document.getElementById('wa-message-body');
    if (textarea) textarea.value = body;
  };

  window.submitWhatsAppInitiate = async function submitWhatsAppInitiate() {
    const btn = document.getElementById('wa-submit-btn');
    const select = document.getElementById('wa-template-select');
    const textarea = document.getElementById('wa-message-body');
    const leadId = waDialog ? waDialog.getAttribute('data-lead-id') : '';
    const templateId = select && select.value ? parseInt(select.value, 10) : null;
    const message = textarea ? textarea.value.trim() : '';
    const csrf =
      document.querySelector('#wa-modal [name=csrfmiddlewaretoken]')?.value ||
      document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
      getCookie('csrftoken') ||
      '';

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Preparing...';
    }
    try {
      const res = await fetch(`/api/v1/leads/${leadId}/whatsapp/initiate/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf,
        },
        body: JSON.stringify({
          template_id: templateId,
          message: message,
          source: 'CRM',
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail || 'Could not initiate WhatsApp. Check phone number format.');
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Open WhatsApp ↗';
        }
        return;
      }
      if (data.web_link) window.open(data.web_link, '_blank');
      window.closeWhatsAppModal();
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Open WhatsApp ↗';
      }
      setTimeout(() => { window.location.reload(); }, 1000);
    } catch (err) {
      alert('Network error while preparing WhatsApp.');
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Open WhatsApp ↗';
      }
    }
  };
})();
