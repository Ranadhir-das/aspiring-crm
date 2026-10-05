/**
 * Vaani SaaS Marketing Interactive Scripts
 * Handles Theme Sync, Mobile Menu, Custom Accessible Video Player with Rock-Solid Play/Pause,
 * Hero Quick-Tour Trigger, Showcase Tabs, FAQ Accordion, and AJAX Demo Booking Form.
 */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", initMarketing);

  function initMarketing() {
    initTheme();
    initMobileNav();
    initVideoPlayer();
    initHeroTourTrigger();
    initHeroParallax();
    initStickyPhoneStory();
    initPointsCounter();
    initPipelineProgress();
    initLiveTimers();
    initShowcaseTabs();
    initFaqAccordion();
    initDemoForm();
    initSmoothScroll();
  }

  /* ========================================================================
     1. THEME SWITCHER
     ======================================================================== */
  function initTheme() {
    var themeBtn = document.getElementById("theme-toggle-btn");
    var mobileThemeBtn = document.getElementById("mobile-theme-toggle-btn");
    var html = document.documentElement;

    function getPreferredTheme() {
      var saved = localStorage.getItem("vaani-theme") || localStorage.getItem("theme");
      if (saved) return saved;
      return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }

    function applyTheme(theme) {
      html.setAttribute("data-theme", theme);
      if (theme === "light") {
        document.body.classList.add("theme-light");
        document.body.classList.remove("theme-dark");
      } else {
        document.body.classList.add("theme-dark");
        document.body.classList.remove("theme-light");
      }
      localStorage.setItem("vaani-theme", theme);
      localStorage.setItem("theme", theme);
      updateThemeIcons(theme);
    }

    function updateThemeIcons(theme) {
      var icon = theme === "light" ? "🌙" : "☀️";
      var title = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
      if (themeBtn) {
        themeBtn.setAttribute("data-theme-state", theme);
        themeBtn.setAttribute("aria-checked", theme === "dark" ? "true" : "false");
        themeBtn.setAttribute("title", title);
        themeBtn.setAttribute("aria-label", title);
      }
      if (mobileThemeBtn) {
        mobileThemeBtn.textContent = icon + " " + (theme === "light" ? "Dark Mode" : "Light Mode");
      }
    }

    var currentTheme = getPreferredTheme();
    applyTheme(currentTheme);

    function toggleTheme() {
      var newTheme = html.getAttribute("data-theme") === "light" ? "dark" : "light";
      applyTheme(newTheme);
    }

    if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
    if (mobileThemeBtn) mobileThemeBtn.addEventListener("click", toggleTheme);
  }

  /* ========================================================================
     2. MOBILE NAVIGATION
     ======================================================================== */
  function initMobileNav() {
    var toggleBtn = document.getElementById("mobile-toggle");
    var menu = document.getElementById("mobile-menu");
    if (!toggleBtn || !menu) return;

    toggleBtn.addEventListener("click", function () {
      var isOpen = menu.classList.contains("open");
      if (isOpen) {
        menu.classList.remove("open");
        toggleBtn.setAttribute("aria-expanded", "false");
      } else {
        menu.classList.add("open");
        toggleBtn.setAttribute("aria-expanded", "true");
      }
    });

    var links = menu.querySelectorAll("a");
    links.forEach(function (link) {
      link.addEventListener("click", function () {
        menu.classList.remove("open");
        toggleBtn.setAttribute("aria-expanded", "false");
      });
    });
  }

  /* ========================================================================
     3. PRODUCT VIDEO PLAYER (39-second Intro Video)
     Robust Event-Driven Play / Pause with Single Source of Truth
     ======================================================================== */
  function initVideoPlayer() {
    var wrapper = document.getElementById("video-wrapper");
    var video = document.getElementById("vaani-video");
    var overlay = document.getElementById("video-overlay");
    var playOverlayBtn = document.getElementById("video-play-overlay-btn");
    var playBtn = document.getElementById("v-play-btn");
    var muteBtn = document.getElementById("v-mute-btn");
    var progressContainer = document.getElementById("v-progress-container");
    var progressBar = document.getElementById("v-progress-bar");
    var timeDisplay = document.getElementById("v-time-display");
    var fullscreenBtn = document.getElementById("v-fullscreen-btn");

    if (!video || !wrapper) return;

    var playIconSvg = '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"></polygon></svg>';
    var pauseIconSvg = '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>';

    function formatTime(seconds) {
      var s = Math.floor(seconds || 0);
      var m = Math.floor(s / 60);
      s = s % 60;
      return (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
    }

    // Toggle Play/Pause smoothly without double-triggers
    function togglePlay(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      if (video.paused || video.ended) {
        var p = video.play();
        if (p !== undefined) {
          p.catch(function (err) {
            console.warn("Video playback was interrupted or prevented:", err);
          });
        }
      } else {
        video.pause();
      }
    }

    // Single source of truth: Listen directly to HTML5 video state changes
    video.addEventListener("play", function () {
      if (overlay) overlay.classList.add("hidden");
      if (playBtn) {
        playBtn.innerHTML = pauseIconSvg;
        playBtn.setAttribute("aria-label", "Pause video");
      }
    });

    video.addEventListener("pause", function () {
      if (overlay) overlay.classList.remove("hidden");
      if (playBtn) {
        playBtn.innerHTML = playIconSvg;
        playBtn.setAttribute("aria-label", "Play video");
      }
    });

    video.addEventListener("ended", function () {
      if (overlay) overlay.classList.remove("hidden");
      if (playBtn) {
        playBtn.innerHTML = playIconSvg;
        playBtn.setAttribute("aria-label", "Replay video");
      }
    });

    // Clicking anywhere on the video element itself toggles play/pause
    video.addEventListener("click", togglePlay);

    // Clicking on the overlay toggles play/pause
    if (overlay) {
      overlay.addEventListener("click", togglePlay);
    }
    if (playOverlayBtn) {
      playOverlayBtn.addEventListener("click", function (e) {
        e.stopPropagation(); // Prevent duplicate bubbling to overlay
        togglePlay(e);
      });
    }

    // Controls bar play/pause button
    if (playBtn) {
      playBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        togglePlay(e);
      });
    }

    // Mute / Unmute
    function toggleMute(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      video.muted = !video.muted;
      updateMuteIcon();
    }

    function updateMuteIcon() {
      if (!muteBtn) return;
      if (video.muted) {
        muteBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"></path></svg>';
        muteBtn.setAttribute("aria-label", "Unmute audio");
      } else {
        muteBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"></path></svg>';
        muteBtn.setAttribute("aria-label", "Mute audio");
      }
    }

    if (muteBtn) muteBtn.addEventListener("click", toggleMute);

    // Progress bar & time display
    video.addEventListener("timeupdate", function () {
      if (video.duration) {
        var pct = (video.currentTime / video.duration) * 100;
        if (progressBar) progressBar.style.width = pct + "%";
        if (timeDisplay) timeDisplay.textContent = formatTime(video.currentTime) + " / " + formatTime(video.duration);
      }
    });

    video.addEventListener("loadedmetadata", function () {
      if (timeDisplay) timeDisplay.textContent = "00:00 / " + formatTime(video.duration || 39);
    });

    // Scrubber click
    if (progressContainer) {
      progressContainer.addEventListener("click", function (e) {
        e.stopPropagation();
        var rect = progressContainer.getBoundingClientRect();
        var pos = (e.clientX - rect.left) / rect.width;
        if (video.duration) {
          video.currentTime = Math.max(0, Math.min(pos * video.duration, video.duration));
        }
      });
    }

    // Fullscreen toggle
    if (fullscreenBtn) {
      fullscreenBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (!document.fullscreenElement) {
          if (wrapper.requestFullscreen) wrapper.requestFullscreen();
          else if (video.requestFullscreen) video.requestFullscreen();
          else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
        } else {
          if (document.exitFullscreen) document.exitFullscreen();
        }
      });
    }

    // Keyboard navigation (Space, K, M, F)
    wrapper.addEventListener("keydown", function (e) {
      if (e.code === "Space" || e.key === "k" || e.key === "K") {
        e.preventDefault();
        togglePlay(e);
      } else if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        toggleMute(e);
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        if (fullscreenBtn) fullscreenBtn.click();
      }
    });
  }

  /* ========================================================================
     4. HERO QUICK-TOUR TRIGGER
     ======================================================================== */
  function initHeroTourTrigger() {
    var tourBtn = document.getElementById("hero-watch-tour-btn");
    var video = document.getElementById("vaani-video");
    var wrapper = document.getElementById("video-wrapper");

    if (!tourBtn || !video || !wrapper) return;

    tourBtn.addEventListener("click", function (e) {
      e.preventDefault();
      var pos = wrapper.getBoundingClientRect().top + window.pageYOffset - 90;
      window.scrollTo({ top: pos, behavior: "smooth" });

      setTimeout(function () {
        video.muted = false;
        var p = video.play();
        if (p !== undefined) {
          p.catch(function () {
            // If browser blocks unmuted play, fallback to muted
            video.muted = true;
            video.play();
          });
        }
      }, 500);
    });
  }

  /* ========================================================================
     5. PRODUCT SHOWCASE TABS
     ======================================================================== */
  function initShowcaseTabs() {
    var tabs = document.querySelectorAll(".showcase-tab");
    var panes = document.querySelectorAll(".showcase-pane");
    if (!tabs.length || !panes.length) return;

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var targetId = tab.getAttribute("data-tab");
        tabs.forEach(function (t) { t.classList.remove("active"); });
        panes.forEach(function (p) { p.classList.remove("active"); });

        tab.classList.add("active");
        var activePane = document.getElementById(targetId);
        if (activePane) activePane.classList.add("active");
      });
    });
  }

  /* ========================================================================
     6. FAQ ACCORDION
     ======================================================================== */
  function initFaqAccordion() {
    var items = document.querySelectorAll(".faq-item");
    if (!items.length) return;

    items.forEach(function (item) {
      var trigger = item.querySelector(".faq-trigger");
      if (!trigger) return;

      trigger.addEventListener("click", function () {
        var isActive = item.classList.contains("active");

        items.forEach(function (other) {
          if (other !== item) {
            other.classList.remove("active");
            var otherTrig = other.querySelector(".faq-trigger");
            if (otherTrig) otherTrig.setAttribute("aria-expanded", "false");
          }
        });

        if (isActive) {
          item.classList.remove("active");
          trigger.setAttribute("aria-expanded", "false");
        } else {
          item.classList.add("active");
          trigger.setAttribute("aria-expanded", "true");
        }
      });
    });
  }

  /* ========================================================================
     7. BOOK A DEMO FORM (AJAX + VALIDATION + CSRF + HONEYPOT)
     ======================================================================== */
  function initDemoForm() {
    var form = document.getElementById("demo-form");
    if (!form) return;

    var nameInput = document.getElementById("demo-name");
    var companyInput = document.getElementById("demo-company");
    var phoneInput = document.getElementById("demo-phone");
    var emailInput = document.getElementById("demo-email");
    var empSelect = document.getElementById("demo-employees");
    var msgInput = document.getElementById("demo-message");
    var submitBtn = document.getElementById("demo-submit-btn");
    var alertBox = document.getElementById("demo-form-alert");

    function showAlert(type, message) {
      if (!alertBox) return;
      alertBox.className = "form-status-alert " + type;
      alertBox.textContent = message;
      alertBox.style.display = "flex";
      alertBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function clearErrors() {
      if (alertBox) alertBox.style.display = "none";
      var errs = form.querySelectorAll(".field-error");
      errs.forEach(function (e) { e.classList.remove("visible"); });
      var inputs = form.querySelectorAll(".form-input, .form-select, .form-textarea");
      inputs.forEach(function (i) { i.classList.remove("error"); });
    }

    function showFieldError(inputId, message) {
      var input = document.getElementById(inputId);
      var errEl = document.getElementById(inputId + "-error");
      if (input) input.classList.add("error");
      if (errEl) {
        errEl.textContent = message;
        errEl.classList.add("visible");
      }
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      clearErrors();

      // Honeypot spam check
      var hp = form.querySelector("input[name='website_url']");
      if (hp && hp.value) {
        showAlert("success", "Thank you! Your demo request has been received. Our team will contact you shortly.");
        form.reset();
        return;
      }

      var isValid = true;
      var nameVal = (nameInput ? nameInput.value : "").trim();
      var companyVal = (companyInput ? companyInput.value : "").trim();
      var phoneVal = (phoneInput ? phoneInput.value : "").trim();
      var emailVal = (emailInput ? emailInput.value : "").trim();
      var empVal = empSelect ? empSelect.value : "";
      var msgVal = (msgInput ? msgInput.value : "").trim();

      if (!nameVal || nameVal.length < 2) {
        showFieldError("demo-name", "Please enter your full name.");
        isValid = false;
      }
      if (!companyVal || companyVal.length < 2) {
        showFieldError("demo-company", "Please enter your company or organization name.");
        isValid = false;
      }
      if (!phoneVal || phoneVal.replace(/[^0-9]/g, "").length < 8) {
        showFieldError("demo-phone", "Please provide a valid phone number (at least 8 digits).");
        isValid = false;
      }
      var emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailVal || !emailRegex.test(emailVal)) {
        showFieldError("demo-email", "Please provide a valid corporate or work email address.");
        isValid = false;
      }
      if (!empVal) {
        showFieldError("demo-employees", "Please select your team or employee size.");
        isValid = false;
      }

      if (!isValid) {
        showAlert("error", "Please correct the highlighted fields above.");
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.setAttribute("data-orig-text", submitBtn.innerHTML);
        submitBtn.innerHTML = '<span>⏳</span> Submitting Request...';
      }

      var csrfTokenEl = form.querySelector("input[name='csrfmiddlewaretoken']");
      var csrfToken = csrfTokenEl ? csrfTokenEl.value : "";

      var payload = {
        name: nameVal,
        company: companyVal,
        phone: phoneVal,
        email: emailVal,
        employees: empVal,
        message: msgVal,
      };

      fetch(form.action || "/book-demo/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { status: res.status, data: data };
          });
        })
        .then(function (result) {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.getAttribute("data-orig-text") || "Request a Demo";
          }
          if (result.status === 200 || result.status === 201) {
            showAlert("success", result.data.message || "Thank you! Your demo request has been received. Our team will contact you shortly.");
            form.reset();
            var formFields = form.querySelector(".demo-form-fields");
            if (formFields) formFields.style.display = "none";
          } else {
            var errors = result.data.errors || {};
            var topMsg = result.data.message || "Please correct the errors below.";
            showAlert("error", topMsg);
            Object.keys(errors).forEach(function (field) {
              showFieldError("demo-" + field, errors[field]);
            });
          }
        })
        .catch(function (err) {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.getAttribute("data-orig-text") || "Request a Demo";
          }
          showAlert("error", "Unable to submit your request right now. Please try again or email us directly.");
          console.error("Demo submission error:", err);
        });
    });
  }

  /* ========================================================================
     8. SMOOTH SCROLL FOR ANCHOR LINKS
     ======================================================================== */
  function initSmoothScroll() {
    var anchors = document.querySelectorAll('a[href^="#"]');
    anchors.forEach(function (a) {
      a.addEventListener("click", function (e) {
        var href = a.getAttribute("href");
        if (href === "#" || href === "#top") {
          e.preventDefault();
          window.scrollTo({ top: 0, behavior: "smooth" });
          return;
        }
        var target = document.querySelector(href);
        if (target) {
          e.preventDefault();
          var offset = 80;
          var pos = target.getBoundingClientRect().top + window.pageYOffset - offset;
          window.scrollTo({ top: pos, behavior: "smooth" });
        }
      });
    });
  }

  /* ========================================================================
     9. HERO COCKPIT PARALLAX & BADGE MOVEMENT (Crisp & Vector-Sharp)
     ======================================================================== */
  function initHeroParallax() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (window.innerWidth < 768) return;

    var hero = document.getElementById("hero");
    var mockup = document.querySelector(".hero-mockup-wrapper");
    var badgePoints = document.querySelector(".badge-points");
    var badgeWa = document.querySelector(".badge-whatsapp");
    var badgeFollowup = document.querySelector(".badge-followup");
    if (!hero || !mockup) return;

    var rafId = null;
    var targetX = 0;
    var targetY = 0;
    var currentX = 0;
    var currentY = 0;

    function onMouseMove(e) {
      var rect = hero.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      var centerX = rect.width / 2;
      var centerY = rect.height / 2;

      var normX = (x - centerX) / centerX;
      var normY = (y - centerY) / centerY;
      targetX = normX * 8;
      targetY = normY * 6;

      if (!rafId) {
        rafId = requestAnimationFrame(updateParallax);
      }
    }

    function updateParallax() {
      currentX += (targetX - currentX) * 0.08;
      currentY += (targetY - currentY) * 0.08;

      mockup.style.transform = "translate3d(" + currentX.toFixed(2) + "px, " + currentY.toFixed(2) + "px, 0)";

      if (badgePoints) {
        badgePoints.style.transform = "translate3d(" + (currentX * 1.6).toFixed(2) + "px, " + (currentY * 1.6).toFixed(2) + "px, 0)";
      }
      if (badgeWa) {
        badgeWa.style.transform = "translate3d(" + (currentX * 1.3).toFixed(2) + "px, " + (currentY * 1.3).toFixed(2) + "px, 0)";
      }
      if (badgeFollowup) {
        badgeFollowup.style.transform = "translate3d(" + (currentX * 1.5).toFixed(2) + "px, " + (currentY * 1.5).toFixed(2) + "px, 0)";
      }

      if (Math.abs(targetX - currentX) > 0.05 || Math.abs(targetY - currentY) > 0.05) {
        rafId = requestAnimationFrame(updateParallax);
      } else {
        rafId = null;
      }
    }

    function onMouseLeave() {
      targetX = 0;
      targetY = 0;
      if (!rafId) {
        rafId = requestAnimationFrame(updateParallax);
      }
    }

    hero.addEventListener("mousemove", onMouseMove);
    hero.addEventListener("mouseleave", onMouseLeave);
  }

  /* ========================================================================
     10. STICKY 3D PHONE STORYTELLER (Caller App 4-Stage Morph)
     ======================================================================== */
  function initStickyPhoneStory() {
    var phoneSection = document.getElementById("caller-app-story");
    if (!phoneSection) return;

    var steps = phoneSection.querySelectorAll(".story-step");
    var phone = document.getElementById("interactive-phone-mockup");
    var stageTabs = phoneSection.querySelectorAll(".stage-tab-btn");
    if (!steps.length || !phone) return;

    function setStage(stageNum) {
      phone.setAttribute("data-active-stage", stageNum);

      steps.forEach(function (step) {
        var s = step.getAttribute("data-stage");
        if (s === String(stageNum)) {
          step.classList.add("is-active");
        } else {
          step.classList.remove("is-active");
        }
      });

      stageTabs.forEach(function (tab) {
        var s = tab.getAttribute("data-stage");
        if (s === String(stageNum)) {
          tab.classList.add("active");
          tab.setAttribute("aria-selected", "true");
        } else {
          tab.classList.remove("active");
          tab.setAttribute("aria-selected", "false");
        }
      });
    }

    // Scroll-driven trigger using IntersectionObserver
    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              var stage = entry.target.getAttribute("data-stage");
              if (stage) {
                setStage(stage);
              }
            }
          });
        },
        {
          rootMargin: "-20% 0px -40% 0px",
          threshold: 0.2,
        }
      );

      steps.forEach(function (step) {
        observer.observe(step);
      });
    }

    // Interactive clicks on story steps and tabs
    steps.forEach(function (step) {
      step.addEventListener("click", function () {
        var stage = step.getAttribute("data-stage");
        if (stage) setStage(stage);
      });
    });

    stageTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var stage = tab.getAttribute("data-stage");
        if (stage) setStage(stage);
      });
    });
  }

  /* ========================================================================
     11. ANIMATED POINTS COUNTER TICKER
     ======================================================================== */
  function initPointsCounter() {
    var pointsEl = document.getElementById("animated-points-total");
    var section = document.getElementById("performance-points-hud");
    if (!pointsEl || !section) return;

    var hasAnimated = false;
    var target = parseInt(pointsEl.getAttribute("data-target") || "620", 10);

    function animatePoints() {
      if (hasAnimated) return;
      hasAnimated = true;

      var duration = 1600;
      var startTime = null;

      function step(timestamp) {
        if (!startTime) startTime = timestamp;
        var progress = Math.min((timestamp - startTime) / duration, 1);
        var easeOut = 1 - Math.pow(1 - progress, 3);
        var current = Math.floor(easeOut * target);
        pointsEl.textContent = current;

        if (progress < 1) {
          requestAnimationFrame(step);
        } else {
          pointsEl.textContent = target;
          pointsEl.classList.add("counter-complete");
        }
      }

      requestAnimationFrame(step);
    }

    if ("IntersectionObserver" in window) {
      var obs = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              animatePoints();
              obs.disconnect();
            }
          });
        },
        { threshold: 0.25 }
      );
      obs.observe(section);
    } else {
      animatePoints();
    }
  }

  /* ========================================================================
     12. WORKFLOW PIPELINE PROGRESSION
     ======================================================================== */
  function initPipelineProgress() {
    var pipeline = document.getElementById("how-it-works");
    if (!pipeline) return;

    var nodes = pipeline.querySelectorAll(".pipeline-node");
    if (!nodes.length) return;

    if ("IntersectionObserver" in window) {
      var obs = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("node-active");
            }
          });
        },
        { threshold: 0.2 }
      );

      nodes.forEach(function (node) {
        obs.observe(node);
      });
    } else {
      nodes.forEach(function (node) {
        node.classList.add("node-active");
      });
    }
  }

  /* ========================================================================
     13. LIVE TELEMETRY TIMERS (HERO & PHONE MOCKUP)
     ======================================================================== */
  function initLiveTimers() {
    var heroTimer = document.getElementById("hero-call-timer");
    var phoneTimer = document.getElementById("phone-active-timer");
    var seconds = 167; // 02:47

    function formatTime(s) {
      var m = Math.floor(s / 60);
      var sec = s % 60;
      return (m < 10 ? "0" + m : m) + ":" + (sec < 10 ? "0" + sec : sec);
    }

    setInterval(function () {
      seconds++;
      if (seconds > 3599) seconds = 120;
      var str = formatTime(seconds);
      if (heroTimer) heroTimer.textContent = str;
      if (phoneTimer) phoneTimer.textContent = str;
    }, 1000);
  }
})();
