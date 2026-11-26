/**
 * Vaani Call Recordings AI Insights Controller
 * - Triggers single & bulk AI transcription/summarization via AJAX (with CSRF)
 * - Polls PENDING / PROCESSING panels every 5s and updates DOM safely (XSS-safe textContent)
 * - Supports 1-click transcript copy and polite screen-reader status announcements
 */
(function () {
  'use strict';

  function getCsrfToken() {
    const page = document.querySelector('.recordings-page');
    if (page && page.dataset.csrfToken) return page.dataset.csrfToken;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function announce(message, isError) {
    const banner = document.getElementById('recordings-ai-live');
    if (!banner) return;
    banner.textContent = message;
    banner.hidden = false;
    banner.classList.toggle('is-error', Boolean(isError));
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function renderPanel(panel, data) {
    const status = String(data.transcript_status || 'NONE').toUpperCase();
    panel.dataset.aiStatus = status;
    panel.className = 'rec-ai-panel status-' + status.toLowerCase();

    const badgesWrap = panel.querySelector('.rec-ai-badges');
    const actionsWrap = panel.querySelector('.rec-ai-actions');
    const bodyWrap = panel.querySelector('.rec-ai-body');
    if (!badgesWrap || !actionsWrap || !bodyWrap) return;

    badgesWrap.replaceChildren();
    actionsWrap.replaceChildren();
    bodyWrap.replaceChildren();

    const brand = el('span', 'rec-ai-chip brand');
    const spark = el('span', '', '✨');
    spark.setAttribute('aria-hidden', 'true');
    brand.append(spark, el('span', '', 'AI Brief'));
    badgesWrap.appendChild(brand);

    if (status === 'COMPLETED') {
      if (data.ai_language) {
        badgesWrap.appendChild(el('span', 'rec-ai-chip lang', data.ai_language));
      }
      if (data.ai_sentiment) {
        badgesWrap.appendChild(
          el(
            'span',
            'rec-ai-chip sentiment-' + String(data.ai_sentiment).toLowerCase(),
            data.ai_sentiment_display || data.ai_sentiment
          )
        );
      }
      const objections = Array.isArray(data.ai_objections) ? data.ai_objections : [];
      if (objections.length > 0) {
        badgesWrap.appendChild(
          el(
            'span',
            'rec-ai-chip objection-alert',
            '⚠ ' + objections.length + ' Objection' + (objections.length === 1 ? '' : 's')
          )
        );
      } else {
        badgesWrap.appendChild(el('span', 'rec-ai-chip clean', '✓ No Objections'));
      }

      const reBtn = el('button', 'rec-ai-btn subtle', '↻ Re-analyze');
      reBtn.type = 'button';
      reBtn.dataset.aiTrigger = 'reanalyze';
      actionsWrap.appendChild(reBtn);

      if (data.ai_summary) {
        bodyWrap.appendChild(el('p', 'rec-ai-summary', data.ai_summary));
      }

      const keyPoints = Array.isArray(data.ai_key_points) ? data.ai_key_points : [];
      if (keyPoints.length > 0 || objections.length > 0) {
        const grid = el('div', 'rec-ai-grid');
        if (keyPoints.length > 0) {
          const kpBox = el('div', 'rec-ai-box');
          kpBox.appendChild(el('span', 'rec-ai-box-title', 'Key Points'));
          const ul = el('ul', 'rec-ai-list');
          keyPoints.forEach((pt) => ul.appendChild(el('li', '', pt)));
          kpBox.appendChild(ul);
          grid.appendChild(kpBox);
        }
        if (objections.length > 0) {
          const objBox = el('div', 'rec-ai-box objections');
          objBox.appendChild(el('span', 'rec-ai-box-title', 'Objections Raised'));
          const ul = el('ul', 'rec-objection-list');
          objections.forEach((obj) => {
            const li = el('li', 'rec-objection-item');
            li.appendChild(el('strong', 'rec-objection-label', obj.label || 'Objection'));
            if (obj.detail) li.appendChild(el('span', 'rec-objection-detail', obj.detail));
            if (obj.evidence) li.appendChild(el('q', 'rec-objection-quote', obj.evidence));
            ul.appendChild(li);
          });
          objBox.appendChild(ul);
          grid.appendChild(objBox);
        }
        bodyWrap.appendChild(grid);
      }

      if (data.ai_next_action) {
        const nextDiv = el('div', 'rec-ai-next');
        nextDiv.append(el('strong', '', 'Next Step: '), el('span', '', data.ai_next_action));
        bodyWrap.appendChild(nextDiv);
      }

      if (data.transcript_text) {
        const details = el('details', 'rec-transcript-details');
        const summary = el('summary', 'rec-transcript-summary');
        summary.appendChild(
          el('span', '', 'Full Transcript (' + (data.ai_language || 'Hindi / English / Hinglish') + ')')
        );
        const content = el('div', 'rec-transcript-content');
        const toolbar = el('div', 'rec-transcript-toolbar');
        toolbar.appendChild(el('span', 'rec-ai-provider-note', data.ai_provider || 'AI Engine'));
        const copyBtn = el('button', 'rec-ai-btn subtle', 'Copy Transcript');
        copyBtn.type = 'button';
        copyBtn.setAttribute('data-copy-transcript', '');
        toolbar.appendChild(copyBtn);
        content.append(toolbar, el('pre', 'rec-transcript-pre', data.transcript_text));
        details.append(summary, content);
        bodyWrap.appendChild(details);
      }
    } else if (status === 'PENDING' || status === 'PROCESSING') {
      const chip = el('span', 'rec-ai-chip pending');
      const spin = el('span', 'rec-ai-spinner');
      spin.setAttribute('aria-hidden', 'true');
      chip.append(
        spin,
        document.createTextNode(
          status === 'PROCESSING' ? ' Transcribing & Summarizing…' : ' Queued for AI Analysis…'
        )
      );
      badgesWrap.appendChild(chip);
      bodyWrap.appendChild(
        el(
          'p',
          'rec-ai-pending-note',
          'Automatically transcribing Hindi, English & Hinglish speech and extracting key points & objections…'
        )
      );
    } else if (status === 'FAILED') {
      badgesWrap.appendChild(el('span', 'rec-ai-chip failed', '⚠ AI Analysis Failed'));
      const retryBtn = el('button', 'rec-ai-btn primary', '↻ Retry AI Analysis');
      retryBtn.type = 'button';
      retryBtn.dataset.aiTrigger = 'reanalyze';
      actionsWrap.appendChild(retryBtn);
      if (data.transcript_error) {
        bodyWrap.appendChild(el('p', 'rec-ai-error-note', String(data.transcript_error).slice(0, 180)));
      }
    } else {
      badgesWrap.appendChild(el('span', 'rec-ai-chip muted', 'Not Analyzed Yet'));
      const btn = el('button', 'rec-ai-btn primary', '✨ Transcribe & Summarize');
      btn.type = 'button';
      btn.dataset.aiTrigger = 'analyze';
      actionsWrap.appendChild(btn);
    }
  }

  let pollTimer = null;

  function activePendingPanels() {
    return Array.from(document.querySelectorAll('.rec-ai-panel')).filter((p) => {
      const st = (p.dataset.aiStatus || '').toUpperCase();
      return st === 'PENDING' || st === 'PROCESSING';
    });
  }

  function ensurePolling() {
    if (pollTimer || activePendingPanels().length === 0) return;
    pollTimer = window.setInterval(async () => {
      const panels = activePendingPanels();
      if (panels.length === 0) {
        window.clearInterval(pollTimer);
        pollTimer = null;
        return;
      }
      for (const panel of panels.slice(0, 8)) {
        const url = panel.dataset.statusUrl;
        if (!url) continue;
        try {
          const res = await fetch(url, {
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
          });
          if (!res.ok) continue;
          const data = await res.json();
          if (data.transcript_status !== panel.dataset.aiStatus) {
            renderPanel(panel, data);
            if (data.transcript_status === 'COMPLETED') {
              announce('AI transcript & summary ready for Call #' + (panel.dataset.callId || '') + '.');
            }
          }
        } catch (_) {
          // ignore transient network glitches while polling
        }
      }
    }, 5000);
  }

  document.addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-ai-trigger]');
    if (trigger) {
      const panel = trigger.closest('.rec-ai-panel');
      if (!panel || !panel.dataset.analyzeUrl) return;
      const force = trigger.dataset.aiTrigger === 'reanalyze';
      trigger.disabled = true;
      try {
        const body = new URLSearchParams();
        if (force) body.set('force', '1');
        const res = await fetch(panel.dataset.analyzeUrl, {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': getCsrfToken(),
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: body.toString(),
          credentials: 'same-origin',
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        renderPanel(panel, data);
        ensurePolling();
        announce('Queued Call #' + (panel.dataset.callId || '') + ' for AI transcription & summarization.');
      } catch (err) {
        trigger.disabled = false;
        announce('Could not queue AI analysis. Please refresh and try again.', true);
      }
      return;
    }

    const bulkBtn = event.target.closest('[data-analyze-pending-url]');
    if (bulkBtn) {
      bulkBtn.disabled = true;
      try {
        const res = await fetch(bulkBtn.dataset.analyzePendingUrl, {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': getCsrfToken(),
          },
          credentials: 'same-origin',
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        announce(data.message || 'Queued pending calls for AI analysis.');
        // Mark visible NONE/FAILED panels as PENDING and begin polling
        document.querySelectorAll('.rec-ai-panel').forEach((panel) => {
          const st = (panel.dataset.aiStatus || '').toUpperCase();
          if (st === 'NONE' || st === 'FAILED') {
            renderPanel(panel, { transcript_status: 'PENDING' });
          }
        });
        ensurePolling();
      } catch (err) {
        bulkBtn.disabled = false;
        announce('Could not start bulk AI analysis. Please try again.', true);
      }
      return;
    }

    const copyBtn = event.target.closest('[data-copy-transcript]');
    if (copyBtn) {
      const content = copyBtn.closest('.rec-transcript-content');
      const pre = content && content.querySelector('.rec-transcript-pre');
      if (!pre || !navigator.clipboard) return;
      try {
        await navigator.clipboard.writeText(pre.textContent || '');
        const prev = copyBtn.textContent;
        copyBtn.textContent = '✓ Copied';
        window.setTimeout(() => {
          copyBtn.textContent = prev || 'Copy Transcript';
        }, 1600);
      } catch (_) {
        // clipboard denied
      }
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensurePolling);
  } else {
    ensurePolling();
  }
})();
