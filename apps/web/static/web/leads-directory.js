(function () {
  'use strict';

  const TIERS = [
    { key: 'HOT', label: 'Hot', range: '80–100', color: '#d61f3c' },
    { key: 'WARM', label: 'Warm', range: '60–79', color: '#e5971a' },
    { key: 'INTERESTED', label: 'Interested', range: '40–59', color: '#2558a8' },
    { key: 'COLD', label: 'Cold', range: '20–39', color: '#7c3aed' },
    { key: 'INACTIVE', label: 'Inactive', range: '0–19', color: '#8c929d' },
  ];

  const CORE_RUBRIC = [
    { code: 'ASKED_FEES', label: 'Asked about fees', delta: 6, hint: 'Fee structure, tuition, or scholarship asked on call' },
    { code: 'BUDGET_DISCLOSED', label: 'Budget disclosed', delta: 8, hint: 'Specific lakh/INR/USD budget range shared' },
    { code: 'MENTIONS_TIMELINE', label: 'Mentions timeline', delta: 5, hint: 'Admission year, intake month, or urgency shared' },
    { code: 'RAISED_OBJECTION', label: 'Raised an objection', delta: -3, hint: 'Each AI-flagged objection deducts -3 points' },
    { code: 'ATTENDED_DEMO', label: 'Attended a demo', delta: 8, hint: 'Counselling session, walk-in, or Google Meet demo' },
  ];

  function getCookie(name) {
    if (!document.cookie) return '';
    const parts = document.cookie.split(';');
    for (let i = 0; i < parts.length; i++) {
      const c = parts[i].trim();
      if (c.startsWith(name + '=')) {
        return decodeURIComponent(c.slice(name.length + 1));
      }
    }
    return '';
  }

  function getCsrfToken() {
    const inp = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return (inp && inp.value) || getCookie('csrftoken') || '';
  }

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function tierMeta(key) {
    return TIERS.find((t) => t.key === key) || TIERS[TIERS.length - 1];
  }

  // Load server-embedded scores map
  let scoreMap = {};
  const dataEl = document.getElementById('lead-scores-data');
  if (dataEl && dataEl.textContent) {
    try {
      scoreMap = JSON.parse(dataEl.textContent) || {};
    } catch (e) {
      scoreMap = {};
    }
  }

  // Bulk select-all checkbox handler
  const selectAll = document.getElementById('select-all');
  const countEl = document.getElementById('selected-count');
  function updateSelectedCount() {
    if (!countEl) return;
    const boxes = document.querySelectorAll('input[name="lead_ids"]');
    let checked = 0;
    boxes.forEach((b) => { if (b.checked) checked++; });
    countEl.textContent = String(checked);
    if (selectAll && boxes.length) {
      selectAll.checked = checked === boxes.length;
      selectAll.indeterminate = checked > 0 && checked < boxes.length;
    }
  }
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      document.querySelectorAll('input[name="lead_ids"]').forEach((b) => {
        b.checked = selectAll.checked;
      });
      updateSelectedCount();
    });
  }
  document.addEventListener('change', (e) => {
    if (e.target && e.target.name === 'lead_ids') updateSelectedCount();
  });

  // Modal Dialog Controller
  const dialog = document.getElementById('lead-score-dialog');
  const subtitleEl = document.getElementById('score-dialog-subtitle');
  const bodyEl = document.getElementById('score-dialog-body');
  const recalcModalBtn = document.getElementById('score-dialog-recalc');
  let activeLeadId = null;

  if (dialog) {
    // Reset returnValue on close (modern-web-guidance: dialog)
    dialog.addEventListener('close', () => {
      dialog.returnValue = '';
    });

    // Fallback click-outside light dismiss ONLY when native closedby is absent
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      dialog.addEventListener('pointerdown', (e) => {
        pressedOutside = outside(e);
      });
      dialog.addEventListener('pointerup', (e) => {
        if (pressedOutside && outside(e)) dialog.close();
      });
    }
  }

  function renderTierScale(activeKey) {
    return (
      '<div class="tier-scale-bar" role="list" aria-label="Lead temperature ranges">' +
      TIERS.map((t) => {
        const activeCls = t.key === activeKey ? ' is-active' : '';
        return (
          `<div class="tier-scale-col${activeCls}" role="listitem" style="--tier-color:${t.color};">` +
            '<span class="tier-scale-swatch" aria-hidden="true"></span>' +
            `<span class="tier-scale-name">${escapeHtml(t.label)}</span>` +
            `<span class="tier-scale-range">${escapeHtml(t.range)}</span>` +
          '</div>'
        );
      }).join('') +
      '</div>'
    );
  }

  function renderRubricGuide() {
    activeLeadId = null;
    if (subtitleEl) {
      subtitleEl.textContent = 'Core buying signals from AI call summaries, key points, objections, and demos';
    }
    if (recalcModalBtn) recalcModalBtn.style.display = 'none';
    if (!bodyEl) return;

    const coreRows = CORE_RUBRIC.map((sig) => {
      const sign = sig.delta > 0 ? `+${sig.delta}` : String(sig.delta);
      const cls = sig.delta > 0 ? 'pos' : 'neg';
      return (
        '<li class="rubric-signal-row">' +
          '<div class="rubric-signal-left">' +
            `<span class="rubric-signal-label">${escapeHtml(sig.label)}</span>` +
            `<span class="rubric-signal-detail">${escapeHtml(sig.hint)}</span>` +
          '</div>' +
          `<span class="rubric-signal-delta ${cls}">${sign}</span>` +
        '</li>'
      );
    }).join('');

    bodyEl.innerHTML =
      `<ul class="rubric-signal-list">${coreRows}</ul>` +
      '<p class="rubric-section-label">Plus conversation &amp; CRM engagement signals (capped 0–100)</p>' +
      '<p class="score-hero-summary" style="margin-bottom:4px;">Live connected talk time (+16 to +24), inbound callback (+10), positive AI sentiment (+10), NEET/academic profile (+10), course clarity (+7), follow-up/WhatsApp engagement (+8), and CRM stage milestones.</p>' +
      renderTierScale('');
  }

  function renderLeadBreakdown(leadData) {
    activeLeadId = leadData.id;
    const meta = tierMeta(leadData.temperature);
    const score = Number(leadData.score || 0);
    const breakdown = Array.isArray(leadData.breakdown) ? leadData.breakdown : [];
    const byCode = {};
    breakdown.forEach((item) => {
      if (item && item.code) byCode[item.code] = item;
    });

    if (subtitleEl) {
      subtitleEl.textContent = `${leadData.name || 'Lead'} · ${leadData.phone || ''} · ${leadData.status_display || ''}`;
    }
    if (recalcModalBtn) {
      recalcModalBtn.style.display = 'inline-flex';
      recalcModalBtn.dataset.leadId = String(leadData.id);
    }

    const coreRows = CORE_RUBRIC.map((sig) => {
      const matched = byCode[sig.code];
      if (matched) {
        const d = Number(matched.delta || 0);
        const sign = d > 0 ? `+${d}` : String(d);
        const cls = d > 0 ? 'pos' : 'neg';
        const icon = d > 0 ? '✓' : '⚠';
        return (
          '<li class="rubric-signal-row">' +
            '<div class="rubric-signal-left">' +
              `<span class="rubric-signal-label"><span aria-hidden="true">${icon}</span> ${escapeHtml(sig.label)}</span>` +
              `<span class="rubric-signal-detail">${escapeHtml(matched.detail || sig.hint)}</span>` +
            '</div>' +
            `<span class="rubric-signal-delta ${cls}">${sign}</span>` +
          '</li>'
        );
      }
      const refSign = sig.delta > 0 ? `+${sig.delta}` : String(sig.delta);
      return (
        '<li class="rubric-signal-row untriggered">' +
          '<div class="rubric-signal-left">' +
            `<span class="rubric-signal-label">${escapeHtml(sig.label)}</span>` +
            `<span class="rubric-signal-detail">Not detected yet · ${escapeHtml(sig.hint)}</span>` +
          '</div>' +
          `<span class="rubric-signal-delta muted">0 <small style="font-weight:600;opacity:.75;">(${refSign})</small></span>` +
        '</li>'
      );
    }).join('');

    const coreCodes = new Set(CORE_RUBRIC.map((c) => c.code));
    const extras = breakdown.filter((b) => b && !coreCodes.has(b.code));
    const extraHtml = extras.length
      ? '<p class="rubric-section-label">Additional AI &amp; CRM Engagement Signals</p>' +
        '<ul class="rubric-signal-list">' +
        extras.map((item) => {
          const d = Number(item.delta || 0);
          const sign = d > 0 ? `+${d}` : String(d);
          const cls = d > 0 ? 'pos' : 'neg';
          return (
            '<li class="rubric-signal-row">' +
              '<div class="rubric-signal-left">' +
                `<span class="rubric-signal-label">${escapeHtml(item.label || item.code)}</span>` +
                `<span class="rubric-signal-detail">${escapeHtml(item.detail || '')}</span>` +
              '</div>' +
              `<span class="rubric-signal-delta ${cls}">${sign}</span>` +
            '</li>'
          );
        }).join('') +
        '</ul>'
      : '';

    const aiSnippet = leadData.summary
      ? `<p class="score-hero-summary">${escapeHtml(leadData.summary)}</p>`
      : '<p class="score-hero-summary">Score computed from call transcripts, AI summaries, objections, demos, and CRM activity.</p>';

    bodyEl.innerHTML =
      `<div class="score-hero-banner" style="--tier-color:${meta.color};">` +
        `<div class="score-hero-ring" role="img" aria-label="Lead score ${score} out of 100, ${meta.label}" style="--p:${score}%;--tier-color:${meta.color};">` +
          `<div class="score-hero-ring-inner">${score}</div>` +
        '</div>' +
        '<div class="score-hero-info">' +
          '<div class="score-hero-tier-row">' +
            `<span class="score-hero-tier-label">${escapeHtml(meta.label)} Lead (${score}/100)</span>` +
            `<span class="temp-pill" style="--tier-color:${meta.color};">${escapeHtml(meta.range)}</span>` +
          '</div>' +
          aiSnippet +
        '</div>' +
      '</div>' +
      `<ul class="rubric-signal-list">${coreRows}</ul>` +
      extraHtml +
      renderTierScale(leadData.temperature);
  }

  function updateRowUI(leadId, updated) {
    const meta = tierMeta(updated.temperature);
    const score = Number(updated.score || 0);
    const row = document.querySelector(`tr[data-lead-id="${leadId}"]`);
    if (!row) return;

    const btn = row.querySelector('.score-trigger-btn');
    if (btn) {
      btn.style.setProperty('--tier-color', meta.color);
      const numEl = btn.querySelector('.score-num');
      if (numEl) numEl.textContent = String(score);
      const pillEl = btn.querySelector('.temp-pill');
      if (pillEl) {
        pillEl.style.setProperty('--tier-color', meta.color);
        pillEl.textContent = meta.label;
      }
      const barEl = btn.querySelector('.score-bar-fill');
      if (barEl) {
        barEl.style.width = `${score}%`;
        barEl.style.background = meta.color;
      }
      const tagsEl = btn.querySelector('.score-signal-tags');
      if (tagsEl && Array.isArray(updated.breakdown)) {
        const top = updated.breakdown.slice(0, 3);
        tagsEl.innerHTML = top.length
          ? top.map((sig) => {
              const d = Number(sig.delta || 0);
              const cls = d >= 0 ? 'pos' : 'neg';
              const s = d > 0 ? `+${d}` : String(d);
              return `<span class="sig-tag ${cls}">${s} ${escapeHtml((sig.label || '').split(' ')[0])}</span>`;
            }).join('')
          : '<span class="sig-tag hint">Click for breakdown</span>';
      }
    }
    const avatar = row.querySelector('.lead-temp-avatar');
    if (avatar) avatar.style.setProperty('--tier-color', meta.color);
  }

  async function recalculateLead(leadId, triggerBtn) {
    if (triggerBtn) triggerBtn.classList.add('spinning');
    try {
      const resp = await fetch(`/leads/${leadId}/recalculate-score/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': getCsrfToken(),
          'X-Requested-With': 'XMLHttpRequest',
        },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const existing = scoreMap[String(leadId)] || { id: leadId };
      const merged = Object.assign({}, existing, {
        score: data.score,
        temperature: data.temperature,
        breakdown: data.breakdown || [],
        scored_at: data.scored_at || '',
      });
      scoreMap[String(leadId)] = merged;
      updateRowUI(leadId, merged);
      if (dialog && dialog.open && activeLeadId === leadId) {
        renderLeadBreakdown(merged);
      }
    } catch (err) {
      console.error('Score recalculation failed:', err);
    } finally {
      if (triggerBtn) triggerBtn.classList.remove('spinning');
    }
  }

  document.addEventListener('click', (e) => {
    const guideBtn = e.target.closest('[data-open-rubric-guide]');
    if (guideBtn && dialog) {
      renderRubricGuide();
      dialog.showModal();
      return;
    }

    const scoreBtn = e.target.closest('[data-open-lead-score]');
    if (scoreBtn && dialog) {
      const lid = scoreBtn.getAttribute('data-open-lead-score');
      const data = scoreMap[String(lid)];
      if (data) {
        renderLeadBreakdown(data);
        dialog.showModal();
      }
      return;
    }

    const recalcBtn = e.target.closest('[data-recalc-lead]');
    if (recalcBtn) {
      const lid = parseInt(recalcBtn.getAttribute('data-recalc-lead'), 10);
      if (lid) recalculateLead(lid, recalcBtn);
      return;
    }
  });

  if (recalcModalBtn) {
    recalcModalBtn.addEventListener('click', () => {
      const lid = parseInt(recalcModalBtn.dataset.leadId || '0', 10);
      if (lid) recalculateLead(lid, recalcModalBtn);
    });
  }
})();
