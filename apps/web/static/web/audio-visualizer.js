/**
 * Vaani Audio Frequency Visualizer & Cyber Player Engine
 * Real-time Web Audio API FFT spectrum analyzer with Low / Mid / High energy metrics,
 * animated HTML5 canvas waveform, and modern scrub/speed controls.
 */
(function() {
  'use strict';

  // Shared AudioContext pool
  let audioCtx = null;
  function getAudioContext() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    return audioCtx;
  }

  // Active playing player tracker
  let activePlayer = null;

  function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return '00:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
  }

  class VaaniAudioPlayer {
    constructor(element) {
      this.container = element;
      this.audio = element.querySelector('audio');
      if (!this.audio) return;

      this.canvas = element.querySelector('.visualizer-canvas');
      this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
      this.playBtn = element.querySelector('.btn-play');
      this.playIcon = element.querySelector('.play-icon');
      this.progressTrack = element.querySelector('.timeline-track');
      this.progressBar = element.querySelector('.timeline-fill');
      this.timeCurrent = element.querySelector('.time-current');
      this.timeDuration = element.querySelector('.time-duration');
      this.speedBtns = element.querySelectorAll('.speed-chip');
      this.meterLow = element.querySelector('.meter-low-fill');
      this.meterMid = element.querySelector('.meter-mid-fill');
      this.meterHigh = element.querySelector('.meter-high-fill');
      this.valLow = element.querySelector('.val-low');
      this.valMid = element.querySelector('.val-mid');
      this.valHigh = element.querySelector('.val-high');

      this.analyser = null;
      this.sourceNode = null;
      this.dataArray = null;
      this.animationId = null;
      this.peaks = [];
      this.isSeeking = false;
      this.hasAudioSource = false;

      this.initEvents();
      this.resizeCanvas();
      this.drawIdleWaveform();
    }

    initEvents() {
      // Audio element lifecycle
      this.audio.addEventListener('loadedmetadata', () => {
        if (this.timeDuration) {
          this.timeDuration.textContent = formatTime(this.audio.duration);
        }
      });

      this.audio.addEventListener('timeupdate', () => {
        if (!this.isSeeking) {
          this.updateProgress();
        }
      });

      this.audio.addEventListener('play', () => {
        if (activePlayer && activePlayer !== this) {
          activePlayer.pause();
        }
        activePlayer = this;
        this.container.classList.add('is-playing');
        if (this.playIcon) this.playIcon.textContent = '⏸';
        this.setupWebAudio();
        this.startVisualizer();
      });

      this.audio.addEventListener('pause', () => {
        this.container.classList.remove('is-playing');
        if (this.playIcon) this.playIcon.textContent = '▶';
        this.stopVisualizer();
      });

      this.audio.addEventListener('ended', () => {
        this.container.classList.remove('is-playing');
        if (this.playIcon) this.playIcon.textContent = '▶';
        this.stopVisualizer();
        this.resetMeters();
        if (this.progressBar) this.progressBar.style.width = '0%';
        if (this.timeCurrent) this.timeCurrent.textContent = '00:00';
      });

      // Play/Pause button
      if (this.playBtn) {
        this.playBtn.addEventListener('click', (e) => {
          e.preventDefault();
          this.togglePlay();
        });
      }

      // Seek timeline track
      if (this.progressTrack) {
        this.progressTrack.addEventListener('click', (e) => {
          this.seekTo(e);
        });

        const handleDrag = (e) => {
          if (!this.isSeeking) return;
          this.seekTo(e);
        };

        this.progressTrack.addEventListener('mousedown', () => { this.isSeeking = true; });
        window.addEventListener('mousemove', handleDrag);
        window.addEventListener('mouseup', () => { this.isSeeking = false; });
      }

      // Playback speed chips
      this.speedBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          const speed = parseFloat(btn.getAttribute('data-speed') || '1');
          this.audio.playbackRate = speed;
          this.speedBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        });
      });

      window.addEventListener('resize', () => {
        this.resizeCanvas();
        if (!this.container.classList.contains('is-playing')) {
          this.drawIdleWaveform();
        }
      });
    }

    setupWebAudio() {
      if (this.hasAudioSource) return;
      try {
        const ctx = getAudioContext();
        if (!ctx) return;
        this.analyser = ctx.createAnalyser();
        this.analyser.fftSize = 128; // 64 frequency bins
        this.analyser.smoothingTimeConstant = 0.82;
        this.dataArray = new Uint8Array(this.analyser.frequencyBinCount);
        this.peaks = new Array(this.analyser.frequencyBinCount).fill(0);

        this.sourceNode = ctx.createMediaElementSource(this.audio);
        this.sourceNode.connect(this.analyser);
        this.analyser.connect(ctx.destination);
        this.hasAudioSource = true;
      } catch (err) {
        // Fallback for CORS or browser security limits
        console.warn('Web Audio node fallback mode:', err);
      }
    }

    togglePlay() {
      getAudioContext();
      if (this.audio.paused) {
        this.audio.play().catch(e => console.warn('Audio play error:', e));
      } else {
        this.audio.pause();
      }
    }

    pause() {
      if (!this.audio.paused) {
        this.audio.pause();
      }
    }

    seekTo(e) {
      const rect = this.progressTrack.getBoundingClientRect();
      const pos = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      if (!isNaN(this.audio.duration)) {
        this.audio.currentTime = pos * this.audio.duration;
        this.updateProgress();
      }
    }

    updateProgress() {
      const current = this.audio.currentTime;
      const duration = this.audio.duration;
      if (this.timeCurrent) {
        this.timeCurrent.textContent = formatTime(current);
      }
      if (duration && !isNaN(duration) && this.progressBar) {
        const pct = Math.min(100, Math.max(0, (current / duration) * 100));
        this.progressBar.style.width = `${pct}%`;
      }
    }

    resizeCanvas() {
      if (!this.canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = this.canvas.getBoundingClientRect();
      this.canvas.width = (rect.width || 340) * dpr;
      this.canvas.height = (rect.height || 72) * dpr;
      if (this.ctx) {
        this.ctx.scale(dpr, dpr);
      }
    }

    startVisualizer() {
      if (this.animationId) cancelAnimationFrame(this.animationId);
      const render = () => {
        this.drawFrequencyFrame();
        this.animationId = requestAnimationFrame(render);
      };
      this.animationId = requestAnimationFrame(render);
    }

    stopVisualizer() {
      if (this.animationId) {
        cancelAnimationFrame(this.animationId);
        this.animationId = null;
      }
      this.drawIdleWaveform();
      this.resetMeters();
    }

    resetMeters() {
      if (this.meterLow) this.meterLow.style.width = '0%';
      if (this.meterMid) this.meterMid.style.width = '0%';
      if (this.meterHigh) this.meterHigh.style.width = '0%';
      if (this.valLow) this.valLow.textContent = '0%';
      if (this.valMid) this.valMid.textContent = '0%';
      if (this.valHigh) this.valHigh.textContent = '0%';
    }

    drawFrequencyFrame() {
      if (!this.ctx || !this.canvas) return;
      const width = this.canvas.getBoundingClientRect().width;
      const height = this.canvas.getBoundingClientRect().height;
      this.ctx.clearRect(0, 0, width, height);

      let frequencies = [];
      if (this.analyser && this.dataArray) {
        this.analyser.getByteFrequencyData(this.dataArray);
        frequencies = Array.from(this.dataArray);
      } else {
        // Reactive procedural visualizer for fallback
        const now = Date.now() / 150;
        frequencies = Array.from({ length: 48 }, (_, i) => {
          const v = Math.sin(now + i * 0.4) * 0.5 + Math.cos(now * 0.7 + i * 0.2) * 0.5;
          return Math.max(10, Math.min(255, Math.floor((v + 1) * 90)));
        });
      }

      // Calculate 3 frequency spectrum bands
      // 1. Low / Bass (bins 0 to 8) ~20Hz-250Hz
      // 2. Mid / Speech (bins 9 to 32) ~250Hz-4000Hz
      // 3. High / Treble (bins 33 to 64) ~4000Hz-16000Hz
      const lowSlice = frequencies.slice(0, Math.max(4, Math.floor(frequencies.length * 0.15)));
      const midSlice = frequencies.slice(Math.floor(frequencies.length * 0.15), Math.floor(frequencies.length * 0.60));
      const highSlice = frequencies.slice(Math.floor(frequencies.length * 0.60));

      const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
      const lowEnergy = Math.min(100, Math.round((avg(lowSlice) / 255) * 100));
      const midEnergy = Math.min(100, Math.round((avg(midSlice) / 255) * 100));
      const highEnergy = Math.min(100, Math.round((avg(highSlice) / 255) * 100));

      if (this.meterLow) this.meterLow.style.width = `${lowEnergy}%`;
      if (this.meterMid) this.meterMid.style.width = `${midEnergy}%`;
      if (this.meterHigh) this.meterHigh.style.width = `${highEnergy}%`;
      if (this.valLow) this.valLow.textContent = `${lowEnergy}%`;
      if (this.valMid) this.valMid.textContent = `${midEnergy}%`;
      if (this.valHigh) this.valHigh.textContent = `${highEnergy}%`;

      // Render Equalizer Bars
      const numBars = 42;
      const barWidth = (width / numBars) * 0.65;
      const gap = (width / numBars) * 0.35;
      const step = Math.max(1, Math.floor(frequencies.length / numBars));

      for (let i = 0; i < numBars; i++) {
        const val = frequencies[i * step] || 0;
        const norm = val / 255;
        const barHeight = Math.max(4, norm * (height - 8));
        const x = i * (barWidth + gap);
        const y = height - barHeight;

        // Smooth peak drops
        if (!this.peaks[i] || barHeight > this.peaks[i]) {
          this.peaks[i] = barHeight;
        } else {
          this.peaks[i] = Math.max(4, this.peaks[i] - 0.7);
        }

        // Futuristic gradient: Cyan (low) -> Royal Blue (mid) -> Violet (high)
        const t = i / numBars;
        const grad = this.ctx.createLinearGradient(0, height, 0, y);
        if (t < 0.33) {
          // Low freq
          grad.addColorStop(0, '#0062ff');
          grad.addColorStop(1, '#00f2fe');
        } else if (t < 0.7) {
          // Mid speech freq
          grad.addColorStop(0, '#0062ff');
          grad.addColorStop(1, '#38bdf8');
        } else {
          // High treble freq
          grad.addColorStop(0, '#7c3aed');
          grad.addColorStop(1, '#c084fc');
        }

        // Draw rounded bar
        this.ctx.fillStyle = grad;
        this.ctx.beginPath();
        this.ctx.roundRect(x, y, barWidth, barHeight, [3, 3, 0, 0]);
        this.ctx.fill();

        // Draw peak dot
        this.ctx.fillStyle = '#ffffff';
        this.ctx.beginPath();
        this.ctx.arc(x + barWidth / 2, Math.max(3, height - this.peaks[i] - 2), barWidth / 2.5, 0, Math.PI * 2);
        this.ctx.fill();
      }
    }

    drawIdleWaveform() {
      if (!this.ctx || !this.canvas) return;
      const width = this.canvas.getBoundingClientRect().width;
      const height = this.canvas.getBoundingClientRect().height;
      this.ctx.clearRect(0, 0, width, height);

      const numBars = 42;
      const barWidth = (width / numBars) * 0.65;
      const gap = (width / numBars) * 0.35;

      for (let i = 0; i < numBars; i++) {
        // Natural speech wave profile: tapered at edges, richer in the voice center
        const center = numBars / 2;
        const dist = Math.abs(i - center) / center;
        const factor = Math.cos(dist * Math.PI * 0.45);
        const barHeight = Math.max(4, factor * (height * 0.65) * (0.4 + (i % 3) * 0.25));
        const x = i * (barWidth + gap);
        const y = height - barHeight;

        const grad = this.ctx.createLinearGradient(0, height, 0, y);
        grad.addColorStop(0, 'rgba(0, 98, 255, 0.2)');
        grad.addColorStop(1, 'rgba(0, 242, 254, 0.45)');

        this.ctx.fillStyle = grad;
        this.ctx.beginPath();
        this.ctx.roundRect(x, y, barWidth, barHeight, [3, 3, 0, 0]);
        this.ctx.fill();
      }
    }
  }

  // Auto initialize on DOM ready
  function initAllPlayers() {
    document.querySelectorAll('.vaani-audio-player').forEach(el => {
      if (!el._vaaniPlayer) {
        el._vaaniPlayer = new VaaniAudioPlayer(el);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllPlayers);
  } else {
    initAllPlayers();
  }

  window.initVaaniAudioPlayers = initAllPlayers;
})();
