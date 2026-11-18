/**
 * Vaani Employee Web Shift Management Controller
 * Handles real-time shift timers, state updates, modal toggling, and AJAX shift actions.
 */
(function() {
  'use strict';

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === (name + '=')) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function formatHMS(totalSeconds) {
    if (totalSeconds == null || totalSeconds < 0) totalSeconds = 0;
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = Math.floor(totalSeconds % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function formatHM(totalSeconds) {
    if (totalSeconds == null || totalSeconds < 0) totalSeconds = 0;
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    return `${h}h ${String(m).padStart(2, '0')}m`;
  }

  const ShiftController = {
    state: null,
    tickerInterval: null,
    syncInterval: null,
    inFlight: false,

    init: function() {
      this.bindEvents();
      this.fetchState();

      // Poll periodically (every 60 seconds) to ensure synchronization with mobile actions
      this.syncInterval = setInterval(() => {
        if (!document.hidden) {
          this.fetchState(false);
        }
      }, 60000);

      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
          this.fetchState(false);
        }
      });
    },

    bindEvents: function() {
      // Topbar pill toggle
      const topbarPill = document.getElementById('shift-topbar-pill');
      const modalBackdrop = document.getElementById('shift-modal-backdrop');
      const modalClose = document.getElementById('shift-modal-close');

      if (topbarPill && modalBackdrop) {
        topbarPill.addEventListener('click', (e) => {
          e.preventDefault();
          modalBackdrop.classList.toggle('open');
        });
      }

      if (modalClose && modalBackdrop) {
        modalClose.addEventListener('click', () => {
          modalBackdrop.classList.remove('open');
        });
      }

      if (modalBackdrop) {
        modalBackdrop.addEventListener('click', (e) => {
          if (e.target === modalBackdrop) {
            modalBackdrop.classList.remove('open');
          }
        });
      }

      // Close modal on Escape
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modalBackdrop && modalBackdrop.classList.contains('open')) {
          modalBackdrop.classList.remove('open');
        }
      });

      // Delegate actions for both modal and inline cards
      document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-shift-action]');
        if (!btn) return;
        e.preventDefault();
        const action = btn.getAttribute('data-shift-action');
        const container = btn.closest('.shift-controls-scope') || document;
        this.handleAction(action, container);
      });
    },

    fetchState: function(triggerRender = true) {
      fetch('/shift/current/', {
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
        }
      })
      .then(res => {
        if (!res.ok) throw new Error('Failed to load shift status');
        return res.json();
      })
      .then(data => {
        this.state = data;
        this.startTicker();
        if (triggerRender) {
          this.render();
        }
      })
      .catch(err => {
        console.warn('[ShiftController] State fetch failed:', err);
      });
    },

    startTicker: function() {
      if (this.tickerInterval) {
        clearInterval(this.tickerInterval);
        this.tickerInterval = null;
      }

      if (!this.state || !this.state.session) return;

      this.tickerInterval = setInterval(() => {
        if (!this.state || !this.state.session) return;

        if (this.state.session.status === 'ACTIVE') {
          this.state.session.elapsed_gross_seconds += 1;
          this.state.session.elapsed_net_seconds += 1;
          if (this.state.current_segment) {
            this.state.current_segment.elapsed_seconds += 1;
          }
          this.updateDynamicTimers();
        } else if (this.state.session.status === 'PAUSED') {
          this.state.session.elapsed_gross_seconds += 1;
          this.state.session.total_break_seconds += 1;
          if (this.state.current_break) {
            this.state.current_break.elapsed_seconds += 1;
            const limit = (this.state.current_break.warning_threshold_minutes || 45) * 60;
            this.state.current_break.warning_exceeded = (this.state.current_break.elapsed_seconds > limit);
          }
          this.updateDynamicTimers();
        }
      }, 1000);
    },

    updateDynamicTimers: function() {
      if (!this.state || !this.state.session) return;

      const netSec = this.state.session.elapsed_net_seconds || 0;
      const breakSec = this.state.session.total_break_seconds || 0;
      const grossSec = this.state.session.elapsed_gross_seconds || 0;

      // Update text in topbar pill
      const topbarText = document.getElementById('topbar-shift-text');
      if (topbarText) {
        if (this.state.session.status === 'ACTIVE') {
          topbarText.textContent = `Shift: ${formatHM(netSec)}`;
        } else if (this.state.session.status === 'PAUSED') {
          topbarText.textContent = `Break: ${formatHM(breakSec)}`;
        }
      }

      // Update all elements with data-shift-timer attribute
      document.querySelectorAll('[data-shift-timer="net"]').forEach(el => {
        el.textContent = formatHMS(netSec);
      });
      document.querySelectorAll('[data-shift-timer="break"]').forEach(el => {
        el.textContent = formatHMS(breakSec);
      });
      document.querySelectorAll('[data-shift-timer="gross"]').forEach(el => {
        el.textContent = formatHMS(grossSec);
      });

      // Update progress bar towards 8 hours (28,800s)
      const targetSec = this.state.min_net_work_seconds || 28800;
      const pct = Math.min(100, Math.round((netSec / targetSec) * 100));
      document.querySelectorAll('.shift-progress-fill').forEach(el => {
        el.style.width = `${pct}%`;
        if (pct >= 100) {
          el.classList.add('satisfied');
        } else {
          el.classList.remove('satisfied');
        }
      });
      document.querySelectorAll('[data-shift-progress-pct]').forEach(el => {
        el.textContent = `${pct}%`;
      });
      document.querySelectorAll('[data-shift-remaining]').forEach(el => {
        const remaining = Math.max(0, targetSec - netSec);
        el.textContent = remaining > 0 ? `${formatHM(remaining)} remaining` : '8 hours satisfied';
      });
    },

    render: function() {
      if (!this.state) return;

      const hasActive = this.state.has_active_session && this.state.session;
      const session = this.state.session;
      const status = hasActive ? session.status : (this.state.has_completed_today ? 'COMPLETED' : 'INACTIVE');

      // 1. Topbar Pill updates
      const topbarPill = document.getElementById('shift-topbar-pill');
      const topbarDot = document.getElementById('topbar-shift-dot');
      const topbarText = document.getElementById('topbar-shift-text');

      if (topbarDot && topbarText) {
        topbarDot.className = 'shift-status-dot';
        if (status === 'ACTIVE') {
          topbarDot.classList.add('active');
          topbarText.textContent = `Shift: ${formatHM(session.elapsed_net_seconds)}`;
        } else if (status === 'PAUSED') {
          topbarDot.classList.add('paused');
          topbarText.textContent = `Break: ${formatHM(session.total_break_seconds)}`;
        } else if (status === 'COMPLETED') {
          topbarDot.classList.add('completed');
          topbarText.textContent = 'Shift Completed';
        } else {
          topbarDot.classList.add('inactive');
          topbarText.textContent = 'Start Shift';
        }
      }

      // 2. Render all shift control scopes (both modal and hero card)
      document.querySelectorAll('.shift-controls-scope').forEach(scope => {
        this.renderScope(scope, status);
      });

      this.updateDynamicTimers();
    },

    renderScope: function(scope, status) {
      // Badges
      const badge = scope.querySelector('.shift-badge');
      if (badge) {
        badge.className = 'shift-badge ' + status.toLowerCase();
        badge.textContent = status === 'ACTIVE' ? 'Shift Active' :
                            status === 'PAUSED' ? 'On Break' :
                            status === 'COMPLETED' ? 'Shift Completed' : 'Not Started';
      }

      // Sections visibility
      const notStartedSection = scope.querySelector('[data-shift-view="not-started"]');
      const activeSection = scope.querySelector('[data-shift-view="active"]');
      const pausedSection = scope.querySelector('[data-shift-view="paused"]');
      const completedSection = scope.querySelector('[data-shift-view="completed"]');

      if (notStartedSection) notStartedSection.style.display = (status === 'INACTIVE') ? 'block' : 'none';
      if (activeSection) activeSection.style.display = (status === 'ACTIVE') ? 'block' : 'none';
      if (pausedSection) pausedSection.style.display = (status === 'PAUSED') ? 'block' : 'none';
      if (completedSection) completedSection.style.display = (status === 'COMPLETED') ? 'block' : 'none';

      // Clear alerts
      const alertBox = scope.querySelector('.shift-alert');
      if (alertBox) {
        alertBox.className = 'shift-alert';
        alertBox.textContent = '';
      }

      // Populate categories dropdown if empty
      const catSelect = scope.querySelector('select[name="category_id"]');
      if (catSelect && this.state.categories && catSelect.options.length <= 1) {
        this.state.categories.forEach(cat => {
          const opt = document.createElement('option');
          opt.value = cat.id;
          opt.textContent = cat.name;
          catSelect.appendChild(opt);
        });
      }

      // Populate break reasons dropdown if empty
      const breakSelect = scope.querySelector('select[name="break_reason_id"]');
      if (breakSelect && this.state.break_reasons && breakSelect.options.length <= 1) {
        this.state.break_reasons.forEach(br => {
          const opt = document.createElement('option');
          opt.value = br.id;
          opt.textContent = `${br.name} (${br.warning_threshold_minutes}m)`;
          breakSelect.appendChild(opt);
        });
      }

      // Populate current break reason label
      if (status === 'PAUSED' && this.state.current_break) {
        const breakReasonLabel = scope.querySelector('[data-current-break-name]');
        if (breakReasonLabel) {
          breakReasonLabel.textContent = this.state.current_break.reason.name;
        }
      }

      // Shift start time display
      if (this.state.session && this.state.session.started_at) {
        const startedAtDate = new Date(this.state.session.started_at);
        const timeStr = startedAtDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        scope.querySelectorAll('[data-shift-start-time]').forEach(el => {
          el.textContent = timeStr;
        });
      }
    },

    showError: function(scope, message, code, details) {
      const alertBox = scope.querySelector('.shift-alert');
      if (!alertBox) return;

      alertBox.className = 'shift-alert shift-alert-error visible';
      alertBox.innerHTML = '';

      const msgSpan = document.createElement('span');
      msgSpan.textContent = message || 'An error occurred while processing your shift action.';
      alertBox.appendChild(msgSpan);

      // Add direct link to attendance correction if duration blocked
      if (code === 'INSUFFICIENT_WORK_DURATION' || (details && details.can_request_early_end)) {
        const reqLink = document.createElement('a');
        reqLink.className = 'shift-early-request-link';
        reqLink.href = '/attendance/corrections/request/';
        reqLink.textContent = 'Request Early Departure Approval ↗';
        alertBox.appendChild(reqLink);
      }
    },

    setLoading: function(scope, isLoading) {
      this.inFlight = isLoading;
      scope.querySelectorAll('.shift-btn').forEach(btn => {
        btn.disabled = isLoading;
      });
      scope.querySelectorAll('.shift-spinner').forEach(sp => {
        if (isLoading) sp.classList.add('loading');
        else sp.classList.remove('loading');
      });
    },

    acquireGpsPosition: function() {
      return new Promise((resolve, reject) => {
        if (!navigator.geolocation) {
          reject(new Error('Browser geolocation is not supported by your desktop browser.'));
          return;
        }

        navigator.geolocation.getCurrentPosition(
          (pos) => {
            const crd = pos.coords;
            resolve({
              latitude: crd.latitude,
              longitude: crd.longitude,
              accuracy: crd.accuracy,
              timestamp: new Date(pos.timestamp).toISOString(),
              is_mocked: false,
            });
          },
          (err) => {
            let msg = 'Failed to acquire location.';
            if (err.code === 1) msg = 'Location access permission was denied in your browser settings.';
            else if (err.code === 2) msg = 'Location position unavailable from desktop network.';
            else if (err.code === 3) msg = 'Location request timed out.';
            reject(new Error(msg));
          },
          {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 0,
          }
        );
      });
    },

    handleAction: async function(action, scope) {
      if (this.inFlight) return;

      const csrfToken = getCookie('csrftoken') || (document.querySelector('[name=csrfmiddlewaretoken]')?.value);
      let endpoint = '';
      const payload = {};

      if (action === 'start') {
        endpoint = '/shift/start/';
        const catSelect = scope.querySelector('select[name="category_id"]');
        if (catSelect && catSelect.value) payload.category_id = catSelect.value;
        const noteInput = scope.querySelector('input[name="start_note"]');
        if (noteInput && noteInput.value) payload.note = noteInput.value.trim();
      } else if (action === 'pause') {
        endpoint = '/shift/pause/';
        const breakSelect = scope.querySelector('select[name="break_reason_id"]');
        if (breakSelect && breakSelect.value) payload.break_reason_id = breakSelect.value;
        const noteInput = scope.querySelector('input[name="break_note"]');
        if (noteInput && noteInput.value) payload.note = noteInput.value.trim();

        if (!payload.break_reason_id) {
          this.showError(scope, 'Please select a break reason before taking a break.');
          return;
        }
      } else if (action === 'resume') {
        endpoint = '/shift/resume/';
        const catSelect = scope.querySelector('select[name="category_id"]');
        if (catSelect && catSelect.value) payload.category_id = catSelect.value;
      } else if (action === 'end') {
        endpoint = '/shift/end/';
        const noteInput = scope.querySelector('input[name="end_note"]');
        if (noteInput && noteInput.value) payload.note = noteInput.value.trim();
      } else {
        return;
      }

      this.setLoading(scope, true);

      // Check if geofence is enforced for actions that require GPS (start, resume, end)
      const requiresGps = (this.state && this.state.geofence_enforced && action !== 'pause');
      if (requiresGps) {
        try {
          const gps = await this.acquireGpsPosition();
          payload.latitude = gps.latitude;
          payload.longitude = gps.longitude;
          payload.accuracy = gps.accuracy;
          payload.recorded_at = gps.timestamp;
          payload.is_mocked = gps.is_mocked;
        } catch (gpsErr) {
          this.setLoading(scope, false);
          this.showError(
            scope,
            `Office Geofence Enforcement: Your account requires verified office GPS (within 50m of your branch). ${gpsErr.message} Please use the approved Vaani Caller mobile app to complete this action.`,
            'MISSING_GPS_READING'
          );
          return;
        }
      }

      fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
        },
        body: JSON.stringify(payload),
      })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          const error = new Error(data.detail || 'Action failed');
          error.code = data.code;
          error.details = data;
          error.status = res.status;
          throw error;
        }
        return data;
      })
      .then((data) => {
        this.setLoading(scope, false);
        this.state = data;
        this.startTicker();
        this.render();
      })
      .catch((err) => {
        this.setLoading(scope, false);
        this.showError(scope, err.message, err.code, err.details);
      });
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    ShiftController.init();
  });

  window.ShiftController = ShiftController;
})();

