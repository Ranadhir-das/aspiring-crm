/**
 * Vaani Call Recordings AI Insights & Interactive Conversation Drawer Controller (v4)
 * - Spacious native <dialog> Call Detail Inspector with custom studio top audio player
 * - Native <input type="range"> seek bar with reliable pre-metadata timestamp jump
 * - 1-click Transcript Language Switcher (English | Hinglish | हिंदी) with roving-tabindex fallback
 * - Live in-call transcript keyword filter & 1-click Copy Transcript (active language)
 * - Priority AJAX Analyze / Re-analyze + fast 2.5s live polling
 * - Progressive fallbacks for Safari light-dismiss (closedby) & Firefox/Safari top-layer exit
 */
(function () {
  'use strict';

  const dialog = document.getElementById('rec-detail-dialog');
  const dialogTitle = document.getElementById('rec-dialog-title');
  const dialogSubtitle = document.getElementById('rec-dialog-subtitle');
  const dialogBadges = document.getElementById('rec-dialog-badges');
  const dialogBody = document.getElementById('rec-dialog-body');
  const dialogAudio = document.getElementById('rec-dialog-audio');
  const dialogPlayBtn = document.getElementById('rec-dialog-play');
  const dialogPlayIcon = document.getElementById('rec-dialog-play-icon');
  const dialogBackBtn = document.getElementById('rec-dialog-back');
  const dialogFwdBtn = document.getElementById('rec-dialog-fwd');
  const dialogSeek = document.getElementById('rec-dialog-seek');
  const dialogTimeCur = document.getElementById('rec-dialog-time-cur');
  const dialogTimeDur = document.getElementById('rec-dialog-time-dur');
  const dialogAudioMsg = document.getElementById('rec-dialog-audio-msg');
  const dialogMuteBtn = document.getElementById('rec-dialog-mute');
  const dialogSpeedBtns = Array.from(document.querySelectorAll('[data-dialog-speed]'));
  const dialogDownload = document.getElementById('rec-dialog-download');
  const dialogLeadLink = document.getElementById('rec-dialog-lead-link');
  const dialogReanalyze = document.getElementById('rec-dialog-reanalyze');

  const LANG_META = {
    en: { label: 'English', title: 'English' },
    hinglish: { label: 'Hinglish', title: 'Hinglish' },
    hi: { label: 'हिंदी', title: 'Hindi (हिंदी)' },
  };

  let activeDialogCallId = null;
  let activeDialogStatusUrl = null;
  let activeDialogData = null;
  let activeTranscriptLang = 'en';
  let activeSegments = [];
  let pendingSeekSeconds = null;
  let isUserScrubbing = false;

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

  function fmtTime(sec) {
    const total = Math.max(0, Math.floor(Number(sec) || 0));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function sentimentSymbol(sent) {
    const s = String(sent || '').toUpperCase();
    if (s === 'POSITIVE') return '★ ';
    if (s === 'HESITANT') return '◐ ';
    if (s === 'NEGATIVE') return '✕ ';
    return '● ';
  }

  /* ------------------------------------------------------------------------
     Custom Popup Studio Audio Controller (reliable seeking + native range slider)
     ------------------------------------------------------------------------ */
  function setAudioMsg(text, isError) {
    if (!dialogAudioMsg) return;
    dialogAudioMsg.textContent = text || '';
    dialogAudioMsg.classList.toggle('is-error', Boolean(isError));
  }

  function syncDialogSlider(current, duration) {
    if (!dialogSeek) return;
    const maxVal = Number.isFinite(duration) && duration > 0 ? duration : Number(dialogSeek.max) || 100;
    const curVal = Math.max(0, Math.min(maxVal, Number(current) || 0));
    dialogSeek.min = '0';
    dialogSeek.max = String(maxVal);
    if (!isUserScrubbing) {
      dialogSeek.value = String(curVal);
    }
    dialogSeek.style.setProperty('--attr-min', '0');
    dialogSeek.style.setProperty('--attr-max', String(maxVal));
    dialogSeek.style.setProperty('--control-value', dialogSeek.value);
    dialogSeek.setAttribute('aria-valuetext', fmtTime(curVal) + ' of ' + fmtTime(maxVal));
    if (dialogTimeCur) dialogTimeCur.textContent = fmtTime(curVal);
    if (dialogTimeDur && Number.isFinite(duration) && duration > 0) {
      dialogTimeDur.textContent = fmtTime(duration);
    }
  }

  function syncPlayButtonState() {
    if (!dialogAudio || !dialogPlayIcon || !dialogPlayBtn) return;
    const playing = !dialogAudio.paused && !dialogAudio.ended;
    dialogPlayIcon.textContent = playing ? '⏸' : '▶';
    dialogPlayBtn.setAttribute('aria-label', playing ? 'Pause recording' : 'Play recording');
  }

  function loadDialogAudio(url, fallbackDurationSec) {
    if (!dialogAudio) return;
    pendingSeekSeconds = null;
    try {
      dialogAudio.pause();
    } catch (_) {}
    setAudioMsg('', false);
    const dur = Math.max(1, Number(fallbackDurationSec) || 100);
    if (dialogTimeDur) dialogTimeDur.textContent = fmtTime(fallbackDurationSec || 0);
    syncDialogSlider(0, dur);
    syncPlayButtonState();
    if (url) {
      dialogAudio.src = url;
      dialogAudio.playbackRate = getActiveDialogSpeed();
      dialogAudio.load();
    } else {
      dialogAudio.removeAttribute('src');
    }
  }

  function getActiveDialogSpeed() {
    const active = dialogSpeedBtns.find((b) => b.getAttribute('aria-pressed') === 'true');
    return active ? parseFloat(active.dataset.dialogSpeed || '1') || 1 : 1;
  }

  function seekAndPlayDialogAudio(seconds) {
    if (!dialogAudio || !dialogAudio.getAttribute('src')) return;
    const target = Math.max(0, Number(seconds) || 0);
    setAudioMsg('', false);
    if (dialogAudio.readyState >= 1) {
      const clamped = Number.isFinite(dialogAudio.duration) && dialogAudio.duration > 0
        ? Math.min(target, Math.max(0, dialogAudio.duration - 0.1))
        : target;
      dialogAudio.currentTime = clamped;
      syncDialogSlider(clamped, dialogAudio.duration);
      dialogAudio.play().catch(() => {
        setAudioMsg('Tap Play to start audio', false);
      });
    } else {
      pendingSeekSeconds = target;
      setAudioMsg('Loading audio…', false);
      dialogAudio.load();
      dialogAudio.play().catch(() => {});
    }
  }

  if (dialogAudio) {
    dialogAudio.addEventListener('loadedmetadata', () => {
      setAudioMsg('', false);
      if (pendingSeekSeconds !== null) {
        const clamped = Number.isFinite(dialogAudio.duration) && dialogAudio.duration > 0
          ? Math.min(pendingSeekSeconds, Math.max(0, dialogAudio.duration - 0.1))
          : pendingSeekSeconds;
        pendingSeekSeconds = null;
        dialogAudio.currentTime = clamped;
      }
      syncDialogSlider(dialogAudio.currentTime, dialogAudio.duration);
    });

    dialogAudio.addEventListener('waiting', () => {
      setAudioMsg('Buffering…', false);
    });

    dialogAudio.addEventListener('playing', () => {
      setAudioMsg('', false);
      syncPlayButtonState();
    });

    dialogAudio.addEventListener('play', syncPlayButtonState);
    dialogAudio.addEventListener('pause', syncPlayButtonState);

    dialogAudio.addEventListener('ended', () => {
      syncPlayButtonState();
      syncDialogSlider(0, dialogAudio.duration);
    });

    dialogAudio.addEventListener('error', () => {
      syncPlayButtonState();
      setAudioMsg('Unable to play recording — try Download', true);
    });

    dialogAudio.addEventListener('timeupdate', () => {
      syncDialogSlider(dialogAudio.currentTime, dialogAudio.duration);
      if (!activeSegments.length || !dialogBody) return;
      const now = dialogAudio.currentTime;
      dialogBody.querySelectorAll('.rec-seg-row').forEach((row) => {
        const start = Number(row.dataset.segStart || 0);
        const end = Number(row.dataset.segEnd || start + 3);
        row.classList.toggle('is-active', now >= start && now <= Math.max(end, start + 1));
      });
    });
  }

  if (dialogPlayBtn) {
    dialogPlayBtn.addEventListener('click', () => {
      if (!dialogAudio || !dialogAudio.getAttribute('src')) return;
      if (dialogAudio.paused) {
        setAudioMsg(dialogAudio.readyState < 2 ? 'Loading audio…' : '', false);
        dialogAudio.play().catch(() => {
          setAudioMsg('Unable to play audio', true);
        });
      } else {
        dialogAudio.pause();
      }
    });
  }

  if (dialogBackBtn) {
    dialogBackBtn.addEventListener('click', () => {
      if (!dialogAudio) return;
      const next = Math.max(0, (dialogAudio.currentTime || 0) - 10);
      dialogAudio.currentTime = next;
      syncDialogSlider(next, dialogAudio.duration);
    });
  }

  if (dialogFwdBtn) {
    dialogFwdBtn.addEventListener('click', () => {
      if (!dialogAudio) return;
      const maxDur = Number.isFinite(dialogAudio.duration) ? dialogAudio.duration : (Number(dialogSeek?.max) || 0);
      const next = Math.min(maxDur, (dialogAudio.currentTime || 0) + 10);
      dialogAudio.currentTime = next;
      syncDialogSlider(next, dialogAudio.duration);
    });
  }

  if (dialogSeek) {
    dialogSeek.addEventListener('pointerdown', () => { isUserScrubbing = true; });
    dialogSeek.addEventListener('pointerup', () => { isUserScrubbing = false; });
    dialogSeek.addEventListener('input', () => {
      dialogSeek.style.setProperty('--control-value', dialogSeek.value);
      const val = parseFloat(dialogSeek.value) || 0;
      if (dialogTimeCur) dialogTimeCur.textContent = fmtTime(val);
      if (dialogAudio && dialogAudio.readyState >= 1) {
        dialogAudio.currentTime = val;
      }
    });
    dialogSeek.addEventListener('change', () => {
      isUserScrubbing = false;
      const val = parseFloat(dialogSeek.value) || 0;
      if (dialogAudio && dialogAudio.readyState >= 1) {
        dialogAudio.currentTime = val;
      } else {
        pendingSeekSeconds = val;
      }
    });
  }

  dialogSpeedBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const rate = parseFloat(btn.dataset.dialogSpeed || '1') || 1;
      dialogSpeedBtns.forEach((b) => b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'));
      if (dialogAudio) dialogAudio.playbackRate = rate;
    });
  });

  if (dialogMuteBtn) {
    dialogMuteBtn.addEventListener('click', () => {
      if (!dialogAudio) return;
      dialogAudio.muted = !dialogAudio.muted;
      dialogMuteBtn.setAttribute('aria-pressed', dialogAudio.muted ? 'true' : 'false');
      dialogMuteBtn.textContent = dialogAudio.muted ? '🔇' : '🔊';
    });
  }

  /* ------------------------------------------------------------------------
     Top-Layer Exit Animation Fallback (modern-web-guidance: animate-to-from-top-layer)
     ------------------------------------------------------------------------ */
  let supportsDisplayTransition;
  function canTransitionDisplay() {
    if (supportsDisplayTransition !== undefined) return supportsDisplayTransition;
    if (!window.CSS?.supports?.('transition-behavior', 'allow-discrete') || !document.body) {
      return false;
    }
    const probe = document.createElement('div');
    probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;';
    document.body.appendChild(probe);
    getComputedStyle(probe).display;
    probe.style.display = 'none';
    supportsDisplayTransition = getComputedStyle(probe).display === 'block';
    probe.remove();
    return supportsDisplayTransition;
  }

  function supportsTopLayerExit() {
    return Boolean(window.CSS?.supports?.('overlay', 'auto') && canTransitionDisplay());
  }

  async function closeDialogCleanly(targetDialog) {
    if (!targetDialog || !targetDialog.open) return;
    if (!supportsTopLayerExit()) {
      targetDialog.setAttribute('data-closing', '');
      const animations = targetDialog.getAnimations ? targetDialog.getAnimations({ subtree: true }) : [];
      if (animations.length > 0) {
        await Promise.race([
          Promise.allSettled(animations.map((a) => a.finished)),
          new Promise((r) => setTimeout(r, 400)),
        ]);
      }
      if (!targetDialog.hasAttribute('data-closing')) return;
      targetDialog.removeAttribute('data-closing');
    }
    targetDialog.close();
  }

  if (dialog) {
    dialog.addEventListener('cancel', (event) => {
      if (!supportsTopLayerExit()) {
        event.preventDefault();
        closeDialogCleanly(dialog);
      }
    });

    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const isOutside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      dialog.addEventListener('pointerdown', (e) => {
        pressedOutside = isOutside(e);
      });
      dialog.addEventListener('pointerup', (e) => {
        if (pressedOutside && isOutside(e)) {
          closeDialogCleanly(dialog);
        }
      });
    }

    dialog.addEventListener('close', () => {
      dialog.returnValue = '';
      if (dialogAudio) {
        try {
          dialogAudio.pause();
          dialogAudio.currentTime = 0;
        } catch (_) {}
      }
      syncPlayButtonState();
      activeDialogCallId = null;
      activeDialogData = null;
    });
  }

  /* ------------------------------------------------------------------------
     Toolbar Roving-Tabindex Fallback (modern-web-guidance: toolbar)
     ------------------------------------------------------------------------ */
  function wireToolbarKeyboardFallback(toolbar) {
    if (!toolbar || 'focusGroup' in HTMLElement.prototype) return;
    const items = Array.from(toolbar.querySelectorAll('button'));
    if (!items.length) return;
    items.forEach((b, i) => {
      b.tabIndex = b.getAttribute('aria-pressed') === 'true' || (i === 0 && !items.some((x) => x.getAttribute('aria-pressed') === 'true')) ? 0 : -1;
    });
    toolbar.addEventListener('focusin', (e) => {
      if (items.includes(e.target)) {
        items.forEach((b) => { b.tabIndex = b === e.target ? 0 : -1; });
      }
    });
    toolbar.addEventListener('keydown', (e) => {
      const idx = items.indexOf(document.activeElement);
      if (idx === -1) return;
      const rtl = getComputedStyle(toolbar).direction === 'rtl';
      let nextIdx = null;
      if (e.key === 'ArrowRight') nextIdx = (idx + (rtl ? -1 : 1) + items.length) % items.length;
      else if (e.key === 'ArrowLeft') nextIdx = (idx + (rtl ? 1 : -1) + items.length) % items.length;
      else if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = items.length - 1;
      if (nextIdx !== null) {
        e.preventDefault();
        items[nextIdx].focus();
      }
    });
  }

  /* ------------------------------------------------------------------------
     Table Cell AI Brief Renderer (updates .rec-ai-panel live)
     ------------------------------------------------------------------------ */
  function renderCard(panel, data) {
    const status = String(data.transcript_status || 'NONE').toUpperCase();
    const objections = Array.isArray(data.ai_objections) ? data.ai_objections : [];
    const keyPoints = Array.isArray(data.ai_key_points) ? data.ai_key_points : [];
    const sentLower = String(data.ai_sentiment || '').toLowerCase();

    panel.dataset.aiStatus = status;
    panel.className = 'rec-ai-panel status-' + status.toLowerCase();

    const row = panel.closest('tr.recording-row');
    if (row) {
      row.classList.toggle('row-has-objections', objections.length > 0);
      row.classList.toggle('row-positive', objections.length === 0 && sentLower === 'positive');
    }

    const badgesWrap = panel.querySelector('.rec-ai-badges');
    const actionsWrap = panel.querySelector('.rec-ai-actions');
    const bodyWrap = panel.querySelector('.rec-ai-body');
    if (!badgesWrap || !actionsWrap || !bodyWrap) return;

    badgesWrap.replaceChildren();
    actionsWrap.replaceChildren();
    bodyWrap.replaceChildren();

    const callId = panel.dataset.callId || data.call_id || '';

    if (status === 'COMPLETED') {
      if (data.ai_language) {
        badgesWrap.appendChild(el('span', 'rec-ai-chip lang', '🌐 ' + data.ai_language));
      }
      if (data.ai_sentiment) {
        badgesWrap.appendChild(
          el(
            'span',
            'rec-ai-chip sentiment-' + sentLower,
            sentimentSymbol(data.ai_sentiment) + (data.ai_sentiment_display || data.ai_sentiment)
          )
        );
      }
      if (objections.length > 0) {
        badgesWrap.appendChild(
          el(
            'span',
            'rec-ai-chip objection-alert',
            '⚠ ' + objections.length + ' Objection' + (objections.length === 1 ? '' : 's')
          )
        );
      } else {
        badgesWrap.appendChild(el('span', 'rec-ai-chip clean', '✓ Clean'));
      }

      const inspectBtn = el('button', 'rec-btn-inspect');
      inspectBtn.type = 'button';
      inspectBtn.dataset.openCallDetail = String(callId);
      inspectBtn.append(el('span', '', 'View Conversation'), el('span', '', '→'));
      const reBtn = el('button', 'rec-ai-btn subtle', '↻');
      reBtn.type = 'button';
      reBtn.dataset.aiTrigger = 'reanalyze';
      reBtn.title = 'Re-run AI analysis at top priority';
      actionsWrap.append(inspectBtn, reBtn);

      if (data.ai_summary) {
        bodyWrap.appendChild(el('p', 'rec-ai-summary', data.ai_summary));
      }

      if (objections.length > 0) {
        const objWrap = el('div', 'rec-obj-compact');
        objections.slice(0, 2).forEach((obj) => {
          const item = el('div', 'rec-obj-compact-item');
          item.appendChild(el('strong', '', '⚠ ' + (obj.label || 'Objection')));
          if (obj.evidence) {
            item.appendChild(el('span', 'rec-obj-quote', '“' + String(obj.evidence).slice(0, 78) + '”'));
          }
          objWrap.appendChild(item);
        });
        bodyWrap.appendChild(objWrap);
      } else if (keyPoints.length > 0) {
        const ul = el('ul', 'rec-kp-compact');
        keyPoints.slice(0, 2).forEach((pt) => ul.appendChild(el('li', '', String(pt).slice(0, 95))));
        bodyWrap.appendChild(ul);
      }
    } else if (status === 'PENDING' || status === 'PROCESSING') {
      const chip = el('span', 'rec-ai-chip pending');
      const spin = el('span', 'rec-ai-spinner');
      spin.setAttribute('aria-hidden', 'true');
      chip.append(spin, document.createTextNode(status === 'PROCESSING' ? ' Analyzing…' : ' Queued…'));
      badgesWrap.appendChild(chip);

      const detBtn = el('button', 'rec-ai-btn subtle', 'View Details');
      detBtn.type = 'button';
      detBtn.dataset.openCallDetail = String(callId);
      actionsWrap.appendChild(detBtn);

      bodyWrap.appendChild(
        el('p', 'rec-ai-pending-note', 'Extracting English summary, key points & objections…')
      );
    } else if (status === 'FAILED') {
      badgesWrap.appendChild(el('span', 'rec-ai-chip failed', '⚠ Failed'));
      const retryBtn = el('button', 'rec-ai-btn primary', '↻ Retry');
      retryBtn.type = 'button';
      retryBtn.dataset.aiTrigger = 'reanalyze';
      actionsWrap.appendChild(retryBtn);
      if (data.transcript_error) {
        bodyWrap.appendChild(el('p', 'rec-ai-error-note', String(data.transcript_error).slice(0, 120)));
      }
    } else {
      badgesWrap.appendChild(el('span', 'rec-ai-chip muted', '○ Not Analyzed'));
      const btn = el('button', 'rec-ai-btn primary', '✨ Analyze');
      btn.type = 'button';
      btn.dataset.aiTrigger = 'analyze';
      const detBtn = el('button', 'rec-ai-btn subtle', 'Details');
      detBtn.type = 'button';
      detBtn.dataset.openCallDetail = String(callId);
      actionsWrap.append(btn, detBtn);
      bodyWrap.appendChild(
        el('p', 'rec-ai-pending-note', 'Click Analyze to generate English summary & transcript.')
      );
    }
  }

  /* ------------------------------------------------------------------------
     Multilingual Transcript Variant Helper (English | Hinglish | Hindi)
     ------------------------------------------------------------------------ */
  function getVariantPayload(data, langCode) {
    const variants = data && typeof data.transcript_variants === 'object' ? data.transcript_variants : {};
    const v = variants && variants[langCode];
    if (v && (Array.isArray(v.segments) && v.segments.length > 0 || v.text)) {
      return {
        text: v.text || '',
        segments: Array.isArray(v.segments) ? v.segments : [],
      };
    }
    if (langCode === 'en') {
      return {
        text: (data && data.transcript_text) || '',
        segments: Array.isArray(data && data.transcript_segments) ? data.transcript_segments : [],
      };
    }
    return null;
  }

  function renderTranscriptLines(linesWrap, variant, searchTerm) {
    linesWrap.replaceChildren();
    const segments = (variant && Array.isArray(variant.segments)) ? variant.segments : [];
    activeSegments = segments;
    const term = (searchTerm || '').trim().toLowerCase();

    if (segments.length > 0) {
      segments.forEach((seg, idx) => {
        const row = el('div', 'rec-seg-row');
        row.dataset.segIndex = String(idx);
        row.dataset.segStart = String(seg.start || 0);
        row.dataset.segEnd = String(seg.end || 0);
        const rawText = String(seg.text || '');
        row.dataset.segSearch = rawText.toLowerCase();
        if (term && !row.dataset.segSearch.includes(term)) {
          row.classList.add('is-hidden');
        }

        const timeBtn = el('button', 'rec-seg-time', fmtTime(seg.start));
        timeBtn.type = 'button';
        timeBtn.title = 'Jump audio to ' + fmtTime(seg.start);
        timeBtn.addEventListener('click', () => {
          seekAndPlayDialogAudio(seg.start || 0);
        });

        row.append(timeBtn, el('span', 'rec-seg-text', rawText));
        linesWrap.appendChild(row);
      });
    } else if (variant && variant.text) {
      linesWrap.appendChild(el('pre', 'rec-transcript-pre', variant.text));
    } else {
      linesWrap.appendChild(el('p', 'rec-ai-pending-note', 'No speech detected in this recording.'));
    }
  }

  /* ------------------------------------------------------------------------
     Spacious Call Detail <dialog> Renderer
     ------------------------------------------------------------------------ */
  function renderDialog(data, preserveAudio) {
    if (!dialog || !dialogBody) return;
    activeDialogData = data;
    const status = String(data.transcript_status || 'NONE').toUpperCase();
    const objections = Array.isArray(data.ai_objections) ? data.ai_objections : [];
    const keyPoints = Array.isArray(data.ai_key_points) ? data.ai_key_points : [];

    const contactName = data.lead_name || data.phone_number || ('Call #' + data.call_id);
    dialogTitle.textContent = contactName;

    const subParts = [];
    if (data.phone_number && data.phone_number !== contactName) subParts.push(data.phone_number);
    if (data.caller_name) subParts.push('Caller: ' + data.caller_name);
    if (data.started_at_display) subParts.push(data.started_at_display);
    if (data.duration_display) subParts.push(data.duration_display);
    dialogSubtitle.textContent = subParts.join(' · ');

    dialogBadges.replaceChildren();
    if (data.direction === 'INBOUND') {
      dialogBadges.appendChild(el('span', 'rec-dir-badge inbound', '↙ Incoming'));
    } else {
      dialogBadges.appendChild(el('span', 'rec-dir-badge outbound', '↗ Outgoing'));
    }
    if (data.outcome_display) {
      dialogBadges.appendChild(el('span', 'rec-ai-chip muted', data.outcome_display));
    }
    if (status === 'COMPLETED') {
      if (data.ai_language) dialogBadges.appendChild(el('span', 'rec-ai-chip lang', '🌐 ' + data.ai_language));
      if (data.ai_sentiment) {
        dialogBadges.appendChild(
          el(
            'span',
            'rec-ai-chip sentiment-' + String(data.ai_sentiment).toLowerCase(),
            sentimentSymbol(data.ai_sentiment) + (data.ai_sentiment_display || data.ai_sentiment)
          )
        );
      }
    }

    if (dialogLeadLink) {
      if (data.lead_url) {
        dialogLeadLink.href = data.lead_url;
        dialogLeadLink.hidden = false;
      } else {
        dialogLeadLink.hidden = true;
      }
    }
    if (dialogReanalyze) {
      dialogReanalyze.hidden = status === 'PENDING' || status === 'PROCESSING';
      dialogReanalyze.textContent = status === 'COMPLETED' ? '↻ Re-analyze' : '✨ Analyze Now';
      dialogReanalyze.disabled = false;
    }

    if (!preserveAudio) {
      loadDialogAudio(data.audio_url || '', data.duration_seconds || 0);
    }
    if (dialogDownload && data.audio_url) {
      dialogDownload.href = data.audio_url;
      dialogDownload.setAttribute('download', data.download_filename || 'recording.m4a');
    }

    dialogBody.replaceChildren();

    if (status === 'PENDING' || status === 'PROCESSING') {
      const waitBox = el('section', 'rec-dialog-section summary-box');
      waitBox.appendChild(el('h2', 'rec-dialog-sec-title', 'AI Analysis In Progress'));
      waitBox.appendChild(
        el(
          'p',
          'rec-ai-summary',
          'This call is prioritized in our fast AI worker. Insights and multilingual transcript will appear here automatically in seconds…'
        )
      );
      dialogBody.appendChild(waitBox);
    } else if (status === 'COMPLETED') {
      // 1. Executive Summary + Next Step
      const sumBox = el('section', 'rec-dialog-section summary-box');
      sumBox.appendChild(el('h2', 'rec-dialog-sec-title', 'Executive Summary (English)'));
      sumBox.appendChild(el('p', 'rec-ai-summary', data.ai_summary || 'Summary generated.'));
      if (data.ai_next_action) {
        const nextDiv = el('div', 'rec-ai-next');
        nextDiv.append(el('strong', '', 'Recommended Next Step: '), el('span', '', data.ai_next_action));
        sumBox.appendChild(nextDiv);
      }
      dialogBody.appendChild(sumBox);

      // 2. Key Points (English)
      if (keyPoints.length > 0) {
        const kpBox = el('section', 'rec-dialog-section');
        kpBox.appendChild(el('h2', 'rec-dialog-sec-title', 'Key Points (English)'));
        const ul = el('ul', 'rec-kp-strip');
        keyPoints.forEach((pt) => ul.appendChild(el('li', '', pt)));
        kpBox.appendChild(ul);
        dialogBody.appendChild(kpBox);
      }

      // 3. Objections Raised
      const objBox = el('section', 'rec-dialog-section' + (objections.length ? ' objections-box' : ''));
      objBox.appendChild(
        el(
          'h2',
          'rec-dialog-sec-title',
          objections.length ? 'Objections Raised (' + objections.length + ')' : 'Objections Check'
        )
      );
      if (objections.length > 0) {
        const wrap = el('div', 'rec-obj-inline');
        objections.forEach((obj) => {
          const item = el('div', 'rec-obj-inline-item');
          item.appendChild(el('strong', '', '⚠ ' + (obj.label || 'Objection') + ':'));
          if (obj.detail) item.appendChild(el('span', '', ' ' + obj.detail));
          if (obj.evidence) item.appendChild(el('q', '', obj.evidence));
          wrap.appendChild(item);
        });
        objBox.appendChild(wrap);
      } else {
        objBox.appendChild(el('span', 'rec-ai-chip clean', '✓ No objections detected in this conversation'));
      }
      dialogBody.appendChild(objBox);

      // 4. Interactive Timestamped Transcript with Language Switcher (English | Hinglish | हिंदी)
      const trBox = el('section', 'rec-dialog-section');
      const trHead = el('div', 'rec-dialog-sec-head');
      const trTitle = el(
        'h2',
        'rec-dialog-sec-title',
        'Conversation Transcript (' + (LANG_META[activeTranscriptLang]?.title || 'English') + ')'
      );
      trHead.appendChild(trTitle);

      // Language Switcher Toolbar (modern-web-guidance: toolbar)
      const langToolbar = el('div', 'rec-lang-toolbar');
      langToolbar.setAttribute('role', 'toolbar');
      langToolbar.setAttribute('focusgroup', 'toolbar wrap');
      langToolbar.setAttribute('aria-label', 'Transcript language');
      langToolbar.setAttribute('aria-controls', 'rec-dialog-transcript-lines');

      const langStatus = el('span', 'rec-lang-status');
      langStatus.setAttribute('role', 'status');
      langStatus.setAttribute('aria-live', 'polite');

      const linesWrap = el('div', 'rec-transcript-lines');
      linesWrap.id = 'rec-dialog-transcript-lines';

      const searchInput = el('input', 'rec-dialog-search');
      searchInput.type = 'search';
      searchInput.placeholder = 'Search word inside this call (e.g. NEET, fees, Patna, budget)…';
      searchInput.setAttribute('aria-label', 'Filter transcript lines');

      ['en', 'hinglish', 'hi'].forEach((code) => {
        const btn = el('button', 'rec-lang-btn', LANG_META[code].label);
        btn.type = 'button';
        btn.dataset.transcriptLang = code;
        btn.setAttribute('aria-pressed', activeTranscriptLang === code ? 'true' : 'false');
        btn.addEventListener('click', async () => {
          if (btn.getAttribute('aria-disabled') === 'true') return;
          activeTranscriptLang = code;
          langToolbar.querySelectorAll('.rec-lang-btn').forEach((b) => {
            b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
          });
          trTitle.textContent = 'Conversation Transcript (' + LANG_META[code].title + ')';

          let variant = getVariantPayload(activeDialogData, code);
          if (!variant && activeDialogStatusUrl) {
            langStatus.textContent = 'Generating ' + LANG_META[code].title + '…';
            langToolbar.querySelectorAll('.rec-lang-btn').forEach((b) => b.setAttribute('aria-disabled', 'true'));
            try {
              const sep = activeDialogStatusUrl.includes('?') ? '&' : '?';
              const res = await fetch(activeDialogStatusUrl + sep + 'lang=' + encodeURIComponent(code), {
                headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
              });
              if (res.ok) {
                const updated = await res.json();
                activeDialogData = updated;
                variant = getVariantPayload(updated, code);
              }
            } catch (_) {
              langStatus.textContent = 'Translation failed; showing English.';
            } finally {
              langToolbar.querySelectorAll('.rec-lang-btn').forEach((b) => b.removeAttribute('aria-disabled'));
            }
          }
          if (variant) {
            langStatus.textContent = '';
            renderTranscriptLines(linesWrap, variant, searchInput.value);
          } else {
            renderTranscriptLines(linesWrap, getVariantPayload(activeDialogData, 'en'), searchInput.value);
          }
        });
        langToolbar.appendChild(btn);
      });
      wireToolbarKeyboardFallback(langToolbar);

      const copyBtn = el('button', 'rec-ai-btn subtle', '📋 Copy');
      copyBtn.type = 'button';
      copyBtn.addEventListener('click', async () => {
        if (!navigator.clipboard) return;
        const activeVar = getVariantPayload(activeDialogData, activeTranscriptLang) || getVariantPayload(activeDialogData, 'en');
        try {
          await navigator.clipboard.writeText((activeVar && activeVar.text) || data.transcript_text || '');
          copyBtn.textContent = '✓ Copied';
          setTimeout(() => { copyBtn.textContent = '📋 Copy'; }, 1600);
        } catch (_) {
          langStatus.textContent = 'Copy blocked by browser.';
        }
      });

      const rightControls = el('div', 'rec-dialog-header-actions');
      rightControls.append(langToolbar, copyBtn);
      trHead.appendChild(rightControls);
      trBox.appendChild(trHead);
      trBox.appendChild(langStatus);

      const tools = el('div', 'rec-dialog-transcript-tools');
      tools.appendChild(searchInput);
      trBox.appendChild(tools);

      const initialVariant = getVariantPayload(data, activeTranscriptLang) || getVariantPayload(data, 'en');
      renderTranscriptLines(linesWrap, initialVariant, '');

      searchInput.addEventListener('input', () => {
        const term = searchInput.value.trim().toLowerCase();
        linesWrap.querySelectorAll('.rec-seg-row').forEach((row) => {
          row.classList.toggle('is-hidden', Boolean(term && !(row.dataset.segSearch || '').includes(term)));
        });
      });

      trBox.appendChild(linesWrap);
      dialogBody.appendChild(trBox);
    } else {
      const emptyBox = el('section', 'rec-dialog-section');
      emptyBox.appendChild(el('h2', 'rec-dialog-sec-title', 'Not Analyzed Yet'));
      emptyBox.appendChild(
        el(
          'p',
          'rec-ai-pending-note',
          data.transcript_error || 'Click "Analyze Now" above to transcribe this call and extract English key points & objections.'
        )
      );
      dialogBody.appendChild(emptyBox);
    }

    if (data.call_notes) {
      const noteBox = el('section', 'rec-dialog-section');
      noteBox.appendChild(el('h2', 'rec-dialog-sec-title', 'Caller Notes'));
      noteBox.appendChild(el('p', 'rec-ai-summary', data.call_notes));
      dialogBody.appendChild(noteBox);
    }
  }

  async function openCallDetail(card) {
    if (!dialog || !card || !card.dataset.statusUrl) return;
    document.querySelectorAll('.vaani-audio-player audio').forEach((a) => {
      if (!a.paused) a.pause();
    });

    activeDialogCallId = card.dataset.callId || null;
    activeDialogStatusUrl = card.dataset.statusUrl;
    activeTranscriptLang = 'en';

    dialogTitle.textContent = 'Loading conversation…';
    dialogSubtitle.textContent = '';
    dialogBadges.replaceChildren();
    dialogBody.replaceChildren(el('p', 'rec-ai-pending-note', 'Loading AI summary & multilingual transcript…'));
    loadDialogAudio('', 0);
    dialog.showModal();

    try {
      const res = await fetch(activeDialogStatusUrl, {
        headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      renderDialog(data, false);
    } catch (_) {
      dialogBody.replaceChildren(el('p', 'rec-ai-error-note', 'Could not load call details. Please refresh and try again.'));
    }
  }

  /* ------------------------------------------------------------------------
     Priority AJAX Trigger + Fast 2.5s Live Polling
     ------------------------------------------------------------------------ */
  let pollTimer = null;

  function activePendingCards() {
    return Array.from(document.querySelectorAll('.rec-ai-panel')).filter((p) => {
      const st = (p.dataset.aiStatus || '').toUpperCase();
      return st === 'PENDING' || st === 'PROCESSING';
    });
  }

  function ensurePolling() {
    if (pollTimer || activePendingCards().length === 0) return;
    pollTimer = window.setInterval(async () => {
      const cards = activePendingCards();
      if (cards.length === 0) {
        window.clearInterval(pollTimer);
        pollTimer = null;
        return;
      }
      for (const card of cards.slice(0, 8)) {
        const url = card.dataset.statusUrl;
        if (!url) continue;
        try {
          const res = await fetch(url, {
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
          });
          if (!res.ok) continue;
          const data = await res.json();
          if (data.transcript_status !== card.dataset.aiStatus) {
            renderCard(card, data);
            if (dialog && dialog.open && String(activeDialogCallId) === String(card.dataset.callId)) {
              renderDialog(data, true);
            }
            if (data.transcript_status === 'COMPLETED') {
              announce('✨ AI summary & transcript ready for ' + (data.lead_name || ('Call #' + card.dataset.callId)) + '.');
            }
          }
        } catch (_) {}
      }
    }, 2500);
  }

  async function triggerAnalyze(card, force) {
    if (!card || !card.dataset.analyzeUrl) return;
    const body = new URLSearchParams();
    if (force) body.set('force', '1');
    const res = await fetch(card.dataset.analyzeUrl, {
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
    renderCard(card, data);
    if (dialog && dialog.open && String(activeDialogCallId) === String(card.dataset.callId)) {
      renderDialog(data, true);
    }
    ensurePolling();
    announce('Prioritized Call #' + (card.dataset.callId || '') + ' at front of AI queue.');
  }

  if (dialogReanalyze) {
    dialogReanalyze.addEventListener('click', async () => {
      const card = document.querySelector('.rec-ai-panel[data-call-id="' + activeDialogCallId + '"]');
      if (!card) return;
      dialogReanalyze.disabled = true;
      try {
        await triggerAnalyze(card, true);
      } catch (_) {
        dialogReanalyze.disabled = false;
        announce('Could not queue AI analysis.', true);
      }
    });
  }

  document.addEventListener('click', async (event) => {
    const inspectBtn = event.target.closest('[data-open-call-detail]');
    if (inspectBtn) {
      const card = inspectBtn.closest('.rec-ai-panel');
      if (card) openCallDetail(card);
      return;
    }

    const trigger = event.target.closest('[data-ai-trigger]');
    if (trigger) {
      const card = trigger.closest('.rec-ai-panel');
      if (!card) return;
      trigger.disabled = true;
      try {
        await triggerAnalyze(card, trigger.dataset.aiTrigger === 'reanalyze');
      } catch (_) {
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
        announce(data.message || 'Queued pending calls for fast AI analysis.');
        document.querySelectorAll('.rec-ai-panel').forEach((card) => {
          const st = (card.dataset.aiStatus || '').toUpperCase();
          if (st === 'NONE' || st === 'FAILED') {
            renderCard(card, { call_id: card.dataset.callId, transcript_status: 'PENDING' });
          }
        });
        ensurePolling();
      } catch (_) {
        bulkBtn.disabled = false;
        announce('Could not start bulk AI analysis. Please try again.', true);
      }
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensurePolling);
  } else {
    ensurePolling();
  }
})();
