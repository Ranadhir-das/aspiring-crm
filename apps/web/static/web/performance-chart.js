/**
 * High-End Multi-Caller Call Telemetry Graph
 * Smooth cubic Bezier spline curves, luminous area gradients, crosshair tracking,
 * interactive multi-series glassmorphism tooltip (clickable, scrollable, theme-adaptive),
 * and caller focus/toggle controls.
 */

document.addEventListener('DOMContentLoaded', () => {
  const chartDataNode = document.getElementById('all-callers-chart-data');
  if (!chartDataNode) return;

  let presetsData;
  try {
    presetsData = JSON.parse(chartDataNode.textContent);
  } catch (err) {
    console.error('Failed to parse all-callers chart data:', err);
    return;
  }

  const chartContainer = document.getElementById('all-callers-telemetry-chart');
  const legendContainer = document.getElementById('telemetry-chart-legend');
  const tooltip = document.getElementById('chart-tooltip');
  const rangeButtons = document.querySelectorAll('.chart-range-btn');
  const selectAllBtn = document.getElementById('chart-select-all');
  const deselectAllBtn = document.getElementById('chart-deselect-all');

  if (!chartContainer || !legendContainer) return;

  // Track hidden caller IDs and highlighted caller ID
  const hiddenCallers = new Set();
  let focusedCallerId = null;
  let currentPreset = '7d';
  let hideTimeout = null;
  let activeCrosshair = null;

  function cancelHide() {
    if (hideTimeout) {
      clearTimeout(hideTimeout);
      hideTimeout = null;
    }
  }

  function scheduleHide() {
    cancelHide();
    hideTimeout = setTimeout(() => {
      if (activeCrosshair) activeCrosshair.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';
    }, 180);
  }

  if (tooltip) {
    tooltip.addEventListener('mouseenter', () => {
      cancelHide();
    });
    tooltip.addEventListener('mouseleave', e => {
      if (e.relatedTarget && chartContainer.contains(e.relatedTarget)) {
        return;
      }
      scheduleHide();
    });
  }

  // Check URL param or default
  const urlParams = new URLSearchParams(window.location.search);
  const paramRange = urlParams.get('range');
  if (paramRange && presetsData[paramRange]) {
    currentPreset = paramRange;
  }

  // Smooth Catmull-Rom to Cubic Bezier path generator
  function generateSmoothPath(points) {
    if (!points || points.length === 0) return '';
    if (points.length === 1) return `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
    if (points.length === 2) {
      return `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)} L ${points[1].x.toFixed(1)} ${points[1].y.toFixed(1)}`;
    }

    let path = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p0 = i > 0 ? points[i - 1] : points[i];
      const p1 = points[i];
      const p2 = points[i + 1];
      const p3 = i < points.length - 2 ? points[i + 2] : p2;

      const cp1x = p1.x + (p2.x - p0.x) * 0.18;
      const cp1y = p1.y + (p2.y - p0.y) * 0.18;
      const cp2x = p2.x - (p3.x - p1.x) * 0.18;
      const cp2y = p2.y - (p3.y - p1.y) * 0.18;

      path += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return path;
  }

  function renderChart(presetKey) {
    currentPreset = presetKey;
    const data = presetsData[presetKey];
    if (!data) return;

    // Update active button state
    rangeButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.preset === presetKey);
    });

    const categories = data.categories || [];
    const series = data.series || [];
    const ceiling = Math.max(4, data.ceiling || 4);

    // High-resolution SVG Coordinates
    const totalWidth = 960;
    const totalHeight = 350;
    const padLeft = 70;
    const padRight = 35;
    const padTop = 30;
    const padBottom = 50;

    const plotWidth = totalWidth - padLeft - padRight;
    const plotHeight = totalHeight - padTop - padBottom;
    const baselineY = padTop + plotHeight;

    const numPoints = Math.max(1, categories.length);
    const getX = i => padLeft + (numPoints === 1 ? plotWidth / 2 : (i * plotWidth) / (numPoints - 1));
    const getY = val => Math.min(baselineY, Math.max(padTop, baselineY - (Math.max(0, val) / ceiling) * plotHeight));

    // Build SVG
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${totalWidth} ${totalHeight}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Multi-caller telemetry chart for ${data.label}`);
    svg.classList.add('premium-telemetry-svg');

    // 1. Gradients Defs
    const defs = document.createElementNS(svgNS, 'defs');
    series.forEach(s => {
      const grad = document.createElementNS(svgNS, 'linearGradient');
      grad.setAttribute('id', `area-grad-${s.id}`);
      grad.setAttribute('x1', '0');
      grad.setAttribute('y1', '0');
      grad.setAttribute('x2', '0');
      grad.setAttribute('y2', '1');

      const stop1 = document.createElementNS(svgNS, 'stop');
      stop1.setAttribute('offset', '0%');
      stop1.setAttribute('stop-color', s.color);
      stop1.setAttribute('stop-opacity', '0.22');

      const stop2 = document.createElementNS(svgNS, 'stop');
      stop2.setAttribute('offset', '100%');
      stop2.setAttribute('stop-color', s.color);
      stop2.setAttribute('stop-opacity', '0.0');

      grad.appendChild(stop1);
      grad.appendChild(stop2);
      defs.appendChild(grad);
    });
    svg.appendChild(defs);

    // 2. Cartesian Grid & Ticks
    const gridGroup = document.createElementNS(svgNS, 'g');
    gridGroup.setAttribute('class', 'chart-grid-layer');

    // Horizontal Lines & Y-ticks (5 steps: 0, 25%, 50%, 75%, 100%)
    const ySteps = 4;
    for (let step = 0; step <= ySteps; step++) {
      const stepVal = (ceiling / ySteps) * step;
      const yPos = getY(stepVal);

      const hLine = document.createElementNS(svgNS, 'line');
      hLine.setAttribute('x1', padLeft);
      hLine.setAttribute('x2', totalWidth - padRight);
      hLine.setAttribute('y1', yPos.toFixed(1));
      hLine.setAttribute('y2', yPos.toFixed(1));
      hLine.setAttribute('class', step === 0 ? 'chart-baseline' : 'chart-grid-line');
      gridGroup.appendChild(hLine);

      const yText = document.createElementNS(svgNS, 'text');
      yText.setAttribute('x', padLeft - 12);
      yText.setAttribute('y', (yPos + 4).toFixed(1));
      yText.setAttribute('text-anchor', 'end');
      yText.setAttribute('class', 'chart-tick-text');
      yText.textContent = stepVal.toFixed(2);
      gridGroup.appendChild(yText);
    }

    // Vertical Lines & X-ticks
    const labelStep = categories.length > 20 ? 4 : categories.length > 10 ? 2 : 1;
    categories.forEach((cat, idx) => {
      const xPos = getX(idx);

      const vLine = document.createElementNS(svgNS, 'line');
      vLine.setAttribute('x1', xPos.toFixed(1));
      vLine.setAttribute('x2', xPos.toFixed(1));
      vLine.setAttribute('y1', padTop);
      vLine.setAttribute('y2', baselineY);
      vLine.setAttribute('class', 'chart-grid-line v-grid');
      gridGroup.appendChild(vLine);

      if (idx % labelStep === 0 || idx === categories.length - 1) {
        const xText = document.createElementNS(svgNS, 'text');
        xText.setAttribute('x', xPos.toFixed(1));
        xText.setAttribute('y', (baselineY + 22).toFixed(1));
        xText.setAttribute('text-anchor', idx === 0 ? 'start' : idx === categories.length - 1 ? 'end' : 'middle');
        xText.setAttribute('class', 'chart-tick-text x-tick');
        xText.textContent = cat;
        gridGroup.appendChild(xText);
      }
    });

    svg.appendChild(gridGroup);

    // 3. Series Lines, Area Fills & Data Halo Rings
    const seriesLayer = document.createElementNS(svgNS, 'g');
    seriesLayer.setAttribute('class', 'chart-series-layer');

    const activeSeries = series.filter(s => !hiddenCallers.has(s.id));

    activeSeries.forEach(s => {
      const ptsData = s.data || [];
      const coordPoints = ptsData.map((val, i) => ({ x: getX(i), y: getY(val), val }));
      const splinePathStr = generateSmoothPath(coordPoints);

      // Area fill underneath curve
      if (coordPoints.length > 1) {
        const firstPt = coordPoints[0];
        const lastPt = coordPoints[coordPoints.length - 1];
        const areaD = `${splinePathStr} L ${lastPt.x.toFixed(1)} ${baselineY} L ${firstPt.x.toFixed(1)} ${baselineY} Z`;

        const areaPath = document.createElementNS(svgNS, 'path');
        areaPath.setAttribute('d', areaD);
        areaPath.setAttribute('fill', `url(#area-grad-${s.id})`);
        areaPath.setAttribute('class', `chart-area-fill series-${s.id}`);
        if (focusedCallerId && focusedCallerId !== s.id) {
          areaPath.classList.add('dimmed');
        }
        seriesLayer.appendChild(areaPath);
      }

      // Smooth Stroke Polyline
      const strokePath = document.createElementNS(svgNS, 'path');
      strokePath.setAttribute('d', splinePathStr);
      strokePath.setAttribute('fill', 'none');
      strokePath.setAttribute('stroke', s.color);
      strokePath.setAttribute('class', `chart-spline-path series-${s.id}`);
      if (focusedCallerId === s.id) strokePath.classList.add('highlighted');
      else if (focusedCallerId && focusedCallerId !== s.id) strokePath.classList.add('dimmed');
      seriesLayer.appendChild(strokePath);

      // Stylized concentric data dots
      coordPoints.forEach(pt => {
        const dotGroup = document.createElementNS(svgNS, 'g');
        dotGroup.setAttribute('class', `chart-dot-group dot-series-${s.id}`);

        // Outer glowing halo
        const halo = document.createElementNS(svgNS, 'circle');
        halo.setAttribute('cx', pt.x.toFixed(1));
        halo.setAttribute('cy', pt.y.toFixed(1));
        halo.setAttribute('r', categories.length > 15 ? '4' : '5');
        halo.setAttribute('fill', s.color);
        halo.setAttribute('opacity', '0.35');
        halo.setAttribute('class', 'chart-dot-halo');

        // Inner solid core
        const core = document.createElementNS(svgNS, 'circle');
        core.setAttribute('cx', pt.x.toFixed(1));
        core.setAttribute('cy', pt.y.toFixed(1));
        core.setAttribute('r', categories.length > 15 ? '2.5' : '3');
        core.setAttribute('fill', '#ffffff');
        core.setAttribute('stroke', s.color);
        core.setAttribute('stroke-width', '1.5');
        core.setAttribute('class', 'chart-dot-core');

        dotGroup.appendChild(halo);
        dotGroup.appendChild(core);
        seriesLayer.appendChild(dotGroup);
      });
    });

    svg.appendChild(seriesLayer);

    // 4. Interactive Crosshair Scanner Line
    const crosshair = document.createElementNS(svgNS, 'line');
    crosshair.setAttribute('y1', padTop);
    crosshair.setAttribute('y2', baselineY);
    crosshair.setAttribute('class', 'chart-crosshair-guide');
    crosshair.style.display = 'none';
    activeCrosshair = crosshair;
    svg.appendChild(crosshair);

    // 5. Interactive Scanning Overlay
    const overlay = document.createElementNS(svgNS, 'rect');
    overlay.setAttribute('x', padLeft);
    overlay.setAttribute('y', padTop);
    overlay.setAttribute('width', plotWidth);
    overlay.setAttribute('height', plotHeight);
    overlay.setAttribute('fill', 'transparent');
    overlay.setAttribute('cursor', 'crosshair');

    overlay.addEventListener('mousemove', e => {
      cancelHide();
      if (!tooltip) return;

      const rect = chartContainer.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const scaleX = totalWidth / rect.width;
      const svgX = mouseX * scaleX;

      // Find closest category index
      let closestIdx = 0;
      let minDiff = Infinity;
      categories.forEach((_, i) => {
        const diff = Math.abs(getX(i) - svgX);
        if (diff < minDiff) {
          minDiff = diff;
          closestIdx = i;
        }
      });

      const snappedX = getX(closestIdx);
      crosshair.setAttribute('x1', snappedX.toFixed(1));
      crosshair.setAttribute('x2', snappedX.toFixed(1));
      crosshair.style.display = 'block';

      // Build rich multi-series tooltip HTML
      const timeLabel = categories[closestIdx];
      const activeRows = activeSeries
        .map(s => ({ id: s.id, name: s.name, color: s.color, val: (s.data || [])[closestIdx] || 0 }))
        .sort((a, b) => b.val - a.val);

      const totalInBucket = activeRows.reduce((acc, r) => acc + r.val, 0);

      let tooltipHtml = `
        <div class="hud-tooltip-header">
          <span style="display:flex;align-items:center;gap:6px;">
            <span class="hud-time-icon">⏱</span>
            <span>${timeLabel}</span>
          </span>
          <span class="hud-total-pill">${totalInBucket} calls</span>
        </div>
        <div class="hud-tooltip-rows" tabindex="0">
      `;

      activeRows.forEach(r => {
        tooltipHtml += `
          <a href="/team/${r.id}/" class="hud-tooltip-row" title="Click to view ${r.name}'s profile">
            <span class="hud-row-left">
              <span class="hud-dot" style="background:${r.color};"></span>
              <span class="hud-name">${r.name}</span>
            </span>
            <strong class="hud-val">${r.val}</strong>
          </a>
        `;
      });
      tooltipHtml += `</div>`;

      tooltip.innerHTML = tooltipHtml;
      tooltip.style.display = 'flex';

      // Smart boundary clamping (Never clips off-screen or above/below card)
      const parentCard = chartContainer.parentElement || chartContainer;
      const cardRect = parentCard.getBoundingClientRect();

      const cursorX = e.clientX - cardRect.left;
      const cursorY = e.clientY - cardRect.top;

      const ttWidth = tooltip.offsetWidth || 230;
      const ttHeight = tooltip.offsetHeight || 190;

      // Position to the right by default; flip to left if near right edge
      let leftPos = cursorX + 16;
      if (leftPos + ttWidth > cardRect.width - 12) {
        leftPos = cursorX - ttWidth - 16;
      }
      if (leftPos < 12) {
        leftPos = 12;
      }

      // Center vertically on cursor, clamped within top and bottom
      let topPos = cursorY - ttHeight / 2;
      if (topPos < 12) {
        topPos = 12;
      } else if (topPos + ttHeight > cardRect.height - 12) {
        topPos = Math.max(12, cardRect.height - ttHeight - 12);
      }

      tooltip.style.left = `${Math.round(leftPos)}px`;
      tooltip.style.top = `${Math.round(topPos)}px`;
      tooltip.style.transform = 'none';
    });

    overlay.addEventListener('mouseleave', e => {
      if (e.relatedTarget && (e.relatedTarget === tooltip || tooltip.contains(e.relatedTarget))) {
        return;
      }
      scheduleHide();
    });

    svg.appendChild(overlay);

    // Render into DOM
    chartContainer.innerHTML = '';
    chartContainer.appendChild(svg);

    // 6. Render Modern Legend Chips
    renderLegend(series);
  }

  function renderLegend(series) {
    legendContainer.innerHTML = '';

    series.forEach(s => {
      const isHidden = hiddenCallers.has(s.id);
      const isFocused = focusedCallerId === s.id;

      const chip = document.createElement('div');
      chip.className = `legend-chip ${isHidden ? 'is-hidden' : ''} ${isFocused ? 'is-focused' : ''}`;
      chip.title = `Click to toggle · Hover to focus ${s.name}`;

      const dot = document.createElement('span');
      dot.className = 'chip-color-dot';
      dot.style.backgroundColor = s.color;
      dot.style.boxShadow = `0 0 8px ${s.color}80`;

      const label = document.createElement('span');
      label.className = 'chip-caller-name';
      label.textContent = s.name;

      const badge = document.createElement('span');
      badge.className = 'chip-call-count';
      badge.textContent = `${s.total} calls`;

      chip.appendChild(dot);
      chip.appendChild(label);
      chip.appendChild(badge);

      // Focus on hover
      chip.addEventListener('mouseenter', () => {
        if (hiddenCallers.has(s.id)) return;
        focusedCallerId = s.id;
        document.querySelectorAll('.chart-spline-path').forEach(p => {
          p.classList.toggle('highlighted', p.classList.contains(`series-${s.id}`));
          p.classList.toggle('dimmed', !p.classList.contains(`series-${s.id}`));
        });
        document.querySelectorAll('.chart-area-fill').forEach(a => {
          a.classList.toggle('dimmed', !a.classList.contains(`series-${s.id}`));
        });
      });

      chip.addEventListener('mouseleave', () => {
        focusedCallerId = null;
        document.querySelectorAll('.chart-spline-path').forEach(p => {
          p.classList.remove('highlighted', 'dimmed');
        });
        document.querySelectorAll('.chart-area-fill').forEach(a => {
          a.classList.remove('dimmed');
        });
      });

      // Toggle on click
      chip.addEventListener('click', () => {
        if (hiddenCallers.has(s.id)) {
          hiddenCallers.delete(s.id);
        } else {
          hiddenCallers.add(s.id);
        }
        renderChart(currentPreset);
      });

      legendContainer.appendChild(chip);
    });
  }

  // Range switch button events
  rangeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const preset = btn.dataset.preset;
      if (preset && presetsData[preset]) {
        renderChart(preset);
      }
    });
  });

  // Select all / Deselect all
  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', () => {
      hiddenCallers.clear();
      renderChart(currentPreset);
    });
  }

  if (deselectAllBtn) {
    deselectAllBtn.addEventListener('click', () => {
      const data = presetsData[currentPreset];
      if (data && data.series) {
        data.series.forEach(s => hiddenCallers.add(s.id));
        renderChart(currentPreset);
      }
    });
  }

  // Initial render
  renderChart(currentPreset);
});
