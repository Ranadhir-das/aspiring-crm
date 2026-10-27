/**
 * High-End Individual Caller Call Telemetry Chart
 * Features:
 * - Smooth Catmull-Rom cubic Bezier splines
 * - Dual glowing vertical area gradients for Total Calls & Connected Calls
 * - Precision Cartesian grid with formatted ticks & axes
 * - Concentric data dots with glowing halo rings & crisp core nodes
 * - Interactive vertical crosshair guide tracking cursor
 * - Rich glassmorphism HUD tooltip with connection rates (theme-adaptive, clickable, scrollable, clamped bounds)
 * - Live synchronized telemetry stat badges
 */

document.addEventListener('DOMContentLoaded', () => {
  const chartDataNode = document.getElementById('individual-caller-chart-data');
  if (!chartDataNode) return;

  let presetsData;
  try {
    presetsData = JSON.parse(chartDataNode.textContent);
  } catch (err) {
    console.error('Failed to parse caller chart data:', err);
    return;
  }

  const chartContainer = document.getElementById('individual-caller-chart');
  const tooltip = document.getElementById('caller-chart-tooltip');
  const rangeButtons = document.querySelectorAll('.caller-range-btn');
  const totalCallsStat = document.getElementById('caller-stat-total-calls');
  const connectedCallsStat = document.getElementById('caller-stat-connected-calls');
  const talkTimeStat = document.getElementById('caller-stat-talk-time');
  const avgTalkTimeStat = document.getElementById('caller-stat-avg-talk-time');
  const connRateStat = document.getElementById('caller-stat-conn-rate');

  if (!chartContainer) return;

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

  // Catmull-Rom to Cubic Bezier curve path generator
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

    // Update bottom summary stats with subtle animation
    if (totalCallsStat) totalCallsStat.textContent = data.total_calls;
    if (connectedCallsStat) connectedCallsStat.textContent = data.connected_calls;
    if (talkTimeStat) talkTimeStat.textContent = data.talk_time_display;
    if (avgTalkTimeStat) avgTalkTimeStat.textContent = data.avg_talk_time_display || '0s';
    if (connRateStat) connRateStat.textContent = `${data.conn_rate || 0}%`;

    const categories = data.categories || [];
    const totalData = data.data || [];
    const connectedData = data.connected_data || [];
    const ceiling = Math.max(4, data.ceiling || 4);

    // High-resolution SVG Coordinates
    const totalWidth = 920;
    const totalHeight = 320;
    const padLeft = 65;
    const padRight = 35;
    const padTop = 30;
    const padBottom = 48;

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
    svg.setAttribute('aria-label', `Caller call telemetry chart for ${data.label}`);
    svg.classList.add('premium-telemetry-svg');

    // 1. Defs: Linear Gradients for Soft Area Fills
    const defs = document.createElementNS(svgNS, 'defs');

    // Gradient 1: Total Calls (Blue)
    const gradTotal = document.createElementNS(svgNS, 'linearGradient');
    gradTotal.setAttribute('id', 'caller-area-grad-total');
    gradTotal.setAttribute('x1', '0');
    gradTotal.setAttribute('y1', '0');
    gradTotal.setAttribute('x2', '0');
    gradTotal.setAttribute('y2', '1');

    const stopT1 = document.createElementNS(svgNS, 'stop');
    stopT1.setAttribute('offset', '0%');
    stopT1.setAttribute('stop-color', '#2563eb');
    stopT1.setAttribute('stop-opacity', '0.24');

    const stopT2 = document.createElementNS(svgNS, 'stop');
    stopT2.setAttribute('offset', '100%');
    stopT2.setAttribute('stop-color', '#2563eb');
    stopT2.setAttribute('stop-opacity', '0.0');

    gradTotal.appendChild(stopT1);
    gradTotal.appendChild(stopT2);
    defs.appendChild(gradTotal);

    // Gradient 2: Connected Calls (Emerald Green)
    const gradConn = document.createElementNS(svgNS, 'linearGradient');
    gradConn.setAttribute('id', 'caller-area-grad-conn');
    gradConn.setAttribute('x1', '0');
    gradConn.setAttribute('y1', '0');
    gradConn.setAttribute('x2', '0');
    gradConn.setAttribute('y2', '1');

    const stopC1 = document.createElementNS(svgNS, 'stop');
    stopC1.setAttribute('offset', '0%');
    stopC1.setAttribute('stop-color', '#10b981');
    stopC1.setAttribute('stop-opacity', '0.22');

    const stopC2 = document.createElementNS(svgNS, 'stop');
    stopC2.setAttribute('offset', '100%');
    stopC2.setAttribute('stop-color', '#10b981');
    stopC2.setAttribute('stop-opacity', '0.0');

    gradConn.appendChild(stopC1);
    gradConn.appendChild(stopC2);
    defs.appendChild(gradConn);

    svg.appendChild(defs);

    // 2. Cartesian Grid & Ticks Layer
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

    // 3. Series Paths (Curves & Fills)
    const seriesLayer = document.createElementNS(svgNS, 'g');
    seriesLayer.setAttribute('class', 'chart-series-layer');

    const totalPoints = totalData.map((val, i) => ({ x: getX(i), y: getY(val), val }));
    const connPoints = connectedData.map((val, i) => ({ x: getX(i), y: getY(val), val }));

    const totalSpline = generateSmoothPath(totalPoints);
    const connSpline = generateSmoothPath(connPoints);

    // Total Calls Area Fill
    if (totalPoints.length > 1) {
      const firstPt = totalPoints[0];
      const lastPt = totalPoints[totalPoints.length - 1];
      const totalAreaD = `${totalSpline} L ${lastPt.x.toFixed(1)} ${baselineY} L ${firstPt.x.toFixed(1)} ${baselineY} Z`;

      const areaPathTotal = document.createElementNS(svgNS, 'path');
      areaPathTotal.setAttribute('d', totalAreaD);
      areaPathTotal.setAttribute('fill', 'url(#caller-area-grad-total)');
      areaPathTotal.setAttribute('class', 'chart-area-fill');
      seriesLayer.appendChild(areaPathTotal);
    }

    // Connected Calls Area Fill
    if (connPoints.length > 1) {
      const firstPt = connPoints[0];
      const lastPt = connPoints[connPoints.length - 1];
      const connAreaD = `${connSpline} L ${lastPt.x.toFixed(1)} ${baselineY} L ${firstPt.x.toFixed(1)} ${baselineY} Z`;

      const areaPathConn = document.createElementNS(svgNS, 'path');
      areaPathConn.setAttribute('d', connAreaD);
      areaPathConn.setAttribute('fill', 'url(#caller-area-grad-conn)');
      areaPathConn.setAttribute('class', 'chart-area-fill');
      seriesLayer.appendChild(areaPathConn);
    }

    // Total Calls Spline Path (Blue)
    const strokeTotal = document.createElementNS(svgNS, 'path');
    strokeTotal.setAttribute('d', totalSpline);
    strokeTotal.setAttribute('fill', 'none');
    strokeTotal.setAttribute('stroke', '#2563eb');
    strokeTotal.setAttribute('class', 'chart-spline-path series-total');
    seriesLayer.appendChild(strokeTotal);

    // Connected Calls Spline Path (Emerald Green)
    const strokeConn = document.createElementNS(svgNS, 'path');
    strokeConn.setAttribute('d', connSpline);
    strokeConn.setAttribute('fill', 'none');
    strokeConn.setAttribute('stroke', '#10b981');
    strokeConn.setAttribute('class', 'chart-spline-path series-conn');
    seriesLayer.appendChild(strokeConn);

    // Concentric Data Dots
    totalPoints.forEach((pt, i) => {
      const connPt = connPoints[i] || { x: pt.x, y: baselineY, val: 0 };
      const dotGroup = document.createElementNS(svgNS, 'g');
      dotGroup.setAttribute('class', 'chart-dot-group');

      // Total Dot Halo & Core
      const totalHalo = document.createElementNS(svgNS, 'circle');
      totalHalo.setAttribute('cx', pt.x.toFixed(1));
      totalHalo.setAttribute('cy', pt.y.toFixed(1));
      totalHalo.setAttribute('r', categories.length > 15 ? '4' : '5');
      totalHalo.setAttribute('fill', '#2563eb');
      totalHalo.setAttribute('opacity', '0.35');
      totalHalo.setAttribute('class', 'chart-dot-halo');

      const totalCore = document.createElementNS(svgNS, 'circle');
      totalCore.setAttribute('cx', pt.x.toFixed(1));
      totalCore.setAttribute('cy', pt.y.toFixed(1));
      totalCore.setAttribute('r', categories.length > 15 ? '2.5' : '3');
      totalCore.setAttribute('fill', '#ffffff');
      totalCore.setAttribute('stroke', '#2563eb');
      totalCore.setAttribute('stroke-width', '1.5');
      totalCore.setAttribute('class', 'chart-dot-core');

      // Connected Dot Halo & Core
      const connHalo = document.createElementNS(svgNS, 'circle');
      connHalo.setAttribute('cx', connPt.x.toFixed(1));
      connHalo.setAttribute('cy', connPt.y.toFixed(1));
      connHalo.setAttribute('r', categories.length > 15 ? '3.5' : '4.5');
      connHalo.setAttribute('fill', '#10b981');
      connHalo.setAttribute('opacity', '0.35');
      connHalo.setAttribute('class', 'chart-dot-halo');

      const connCore = document.createElementNS(svgNS, 'circle');
      connCore.setAttribute('cx', connPt.x.toFixed(1));
      connCore.setAttribute('cy', connPt.y.toFixed(1));
      connCore.setAttribute('r', categories.length > 15 ? '2' : '2.5');
      connCore.setAttribute('fill', '#ffffff');
      connCore.setAttribute('stroke', '#10b981');
      connCore.setAttribute('stroke-width', '1.5');
      connCore.setAttribute('class', 'chart-dot-core');

      dotGroup.appendChild(totalHalo);
      dotGroup.appendChild(totalCore);
      dotGroup.appendChild(connHalo);
      dotGroup.appendChild(connCore);
      seriesLayer.appendChild(dotGroup);
    });

    svg.appendChild(seriesLayer);

    // 4. Interactive Vertical Crosshair Guide Line
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

      // Build rich glassmorphism HUD tooltip
      const timeLabel = categories[closestIdx];
      const totVal = totalData[closestIdx] || 0;
      const connVal = connectedData[closestIdx] || 0;
      const rate = totVal > 0 ? Math.round((connVal / totVal) * 100) : 0;

      tooltip.innerHTML = `
        <div class="hud-tooltip-header">
          <span style="display:flex;align-items:center;gap:6px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            <span>${timeLabel}</span>
          </span>
          <span class="hud-rate-badge ${rate >= 50 ? 'high' : rate >= 25 ? 'mid' : 'low'}">${rate}% connected</span>
        </div>
        <div class="hud-tooltip-rows" tabindex="0">
          <div class="hud-tooltip-row">
            <span class="hud-row-left">
              <span class="hud-dot" style="background:#2563eb;"></span>
              <span class="hud-name">Total Calls</span>
            </span>
            <strong class="hud-val" style="color:#60a5fa;">${totVal}</strong>
          </div>
          <div class="hud-tooltip-row">
            <span class="hud-row-left">
              <span class="hud-dot" style="background:#10b981;"></span>
              <span class="hud-name">Connected Calls</span>
            </span>
            <strong class="hud-val" style="color:#34d399;">${connVal}</strong>
          </div>
        </div>
      `;

      tooltip.style.display = 'flex';

      // Smart boundary clamping (Never clips off-screen or above/below card)
      const parentCard = chartContainer.parentElement || chartContainer;
      const cardRect = parentCard.getBoundingClientRect();

      const cursorX = e.clientX - cardRect.left;
      const cursorY = e.clientY - cardRect.top;

      const ttWidth = tooltip.offsetWidth || 210;
      const ttHeight = tooltip.offsetHeight || 130;

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
  }

  // Range switcher events
  rangeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const preset = btn.dataset.preset;
      if (preset && presetsData[preset]) {
        renderChart(preset);
      }
    });
  });

  // Initial render
  renderChart(currentPreset);
});
