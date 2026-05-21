/**
 * charts.js — uPlot-based and canvas-based chart utilities
 * Exposes: window.Charts
 */
window.Charts = (() => {
  const _instances = {};  // containerId → uPlot instance

  function _destroy(containerId) {
    if (_instances[containerId]) {
      _instances[containerId].destroy();
      delete _instances[containerId];
    }
  }

  // ── uPlot equity curve ────────────────────────────────────────────────────
  function renderEquityCurve(containerId, data, opts = {}) {
    const el = document.getElementById(containerId);
    if (!el) return;
    _destroy(containerId);
    el.innerHTML = '';

    if (!data || !data[0] || data[0].length === 0) {
      el.innerHTML = '<div class="chart-empty">No equity data</div>';
      return;
    }

    const color = opts.color || '#B8FF57';
    const w = el.clientWidth || 600;
    const h = opts.height || 260;

    const uOpts = {
      title: opts.title || '',
      width:  w,
      height: h,
      cursor: { show: true, drag: { x: true, y: false } },
      scales: { x: { time: true }, y: { auto: true } },
      axes: [
        {
          stroke: '#8A8A8A',
          grid:   { stroke: '#1F1F1F', width: 1 },
          ticks:  { stroke: '#1F1F1F' },
          values: (u, vals) => vals.map(v => {
            if (v == null) return '';
            const d = new Date(v * 1000);
            return d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
          }),
        },
        {
          stroke: '#8A8A8A',
          grid:   { stroke: '#1F1F1F', width: 1 },
          ticks:  { stroke: '#1F1F1F' },
          values: (u, vals) => vals.map(v => v == null ? '' : '₹' + _fmtK(v)),
        },
      ],
      series: [
        {},
        {
          label:  opts.label || 'Equity',
          stroke: color,
          fill:   color + '18',
          width:  2,
          points: { show: false },
        },
      ],
      plugins: [],
    };

    try {
      _instances[containerId] = new uPlot(uOpts, data, el);
    } catch (e) {
      console.error('uPlot error:', e);
      el.innerHTML = '<div class="chart-empty">Chart unavailable</div>';
    }
  }

  // ── Mini sparkline (canvas) ───────────────────────────────────────────────
  function renderSparkline(containerId, values, positive = true) {
    const el = document.getElementById(containerId);
    if (!el || !values || values.length < 2) return;

    const canvas = document.createElement('canvas');
    canvas.width  = el.clientWidth  || 80;
    canvas.height = el.clientHeight || 32;
    el.innerHTML = '';
    el.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const pad = 2;

    ctx.clearRect(0, 0, W, H);
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = pad + (i / (values.length - 1)) * (W - pad * 2);
      const y = H - pad - ((v - min) / range) * (H - pad * 2);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = positive ? '#B8FF57' : '#FF4D4D';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // ── Confidence gauge (SVG arc) ────────────────────────────────────────────
  function renderConfidenceGauge(containerId, score) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const clipped = Math.max(0, Math.min(10, score));
    const color = clipped >= 7 ? '#B8FF57' : clipped >= 5 ? '#FFB347' : '#FF4D4D';
    const pct = clipped / 10;
    const R = 36, cx = 44, cy = 44;
    const startAngle = Math.PI;
    const endAngle   = Math.PI + pct * Math.PI;

    const x1 = cx + R * Math.cos(startAngle);
    const y1 = cy + R * Math.sin(startAngle);
    const x2 = cx + R * Math.cos(endAngle);
    const y2 = cy + R * Math.sin(endAngle);
    const largeArc = pct > 0.5 ? 1 : 0;

    el.innerHTML = `
      <svg width="88" height="52" viewBox="0 0 88 52">
        <path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}"
              fill="none" stroke="#1F1F1F" stroke-width="6" stroke-linecap="round"/>
        <path d="M ${x1} ${y1} A ${R} ${R} 0 ${largeArc} 1 ${x2} ${y2}"
              fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"/>
        <text x="${cx}" y="${cy - 4}" text-anchor="middle"
              font-family="JetBrains Mono,monospace" font-size="14" font-weight="500"
              fill="${color}">${clipped.toFixed(1)}</text>
        <text x="${cx}" y="${cy + 8}" text-anchor="middle"
              font-family="DM Sans,sans-serif" font-size="8" fill="#8A8A8A">/ 10</text>
      </svg>`;
  }

  // ── Monthly returns bar chart (canvas) ───────────────────────────────────
  function renderMonthlyReturns(containerId, monthlyData) {
    const el = document.getElementById(containerId);
    if (!el || !monthlyData || monthlyData.length === 0) {
      if (el) el.innerHTML = '<div class="chart-empty">No monthly data</div>';
      return;
    }

    const canvas = document.createElement('canvas');
    const W = el.clientWidth || 600;
    const H = 180;
    canvas.width  = W;
    canvas.height = H;
    el.innerHTML  = '';
    el.appendChild(canvas);

    const ctx    = canvas.getContext('2d');
    const pad    = { top: 16, right: 16, bottom: 40, left: 48 };
    const cW     = W - pad.left - pad.right;
    const cH     = H - pad.top - pad.bottom;
    const values = monthlyData.map(d => d.return_pct || d.pnl || 0);
    const maxV   = Math.max(...values.map(Math.abs), 1);
    const barW   = Math.max(4, cW / values.length * 0.7);
    const gap    = cW / values.length;
    const zeroY  = pad.top + cH / 2;

    ctx.fillStyle = '#0A0A0A';
    ctx.fillRect(0, 0, W, H);

    // Zero line
    ctx.strokeStyle = '#2A2A2A';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, zeroY);
    ctx.lineTo(pad.left + cW, zeroY);
    ctx.stroke();

    values.forEach((v, i) => {
      const x     = pad.left + i * gap + (gap - barW) / 2;
      const barH  = (Math.abs(v) / maxV) * (cH / 2);
      const y     = v >= 0 ? zeroY - barH : zeroY;
      ctx.fillStyle = v >= 0 ? '#B8FF57' : '#FF4D4D';
      ctx.fillRect(x, y, barW, barH);

      // Month label
      const label = (monthlyData[i].month || '').slice(5, 7);
      ctx.fillStyle = '#8A8A8A';
      ctx.font = '10px DM Sans, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(label, x + barW / 2, H - 8);

      // Value label
      if (Math.abs(v) > maxV * 0.1) {
        ctx.fillStyle = v >= 0 ? '#B8FF57' : '#FF4D4D';
        ctx.font = '9px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText((v >= 0 ? '+' : '') + v.toFixed(1) + '%',
          x + barW / 2, v >= 0 ? y - 4 : y + barH + 10);
      }
    });

    // Y axis labels
    ctx.fillStyle = '#8A8A8A';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText('+' + maxV.toFixed(1) + '%', pad.left - 4, pad.top + 10);
    ctx.fillText('-' + maxV.toFixed(1) + '%', pad.left - 4, H - pad.bottom - 4);
  }

  // ── Candlestick chart (canvas) ────────────────────────────────────────────
  function renderCandlestick(containerId, bars, overlays = {}) {
    const el = document.getElementById(containerId);
    if (!el || !bars || bars.length === 0) {
      if (el) el.innerHTML = '<div class="chart-empty">No bars to display</div>';
      return;
    }

    const canvas = el.querySelector('canvas') || document.createElement('canvas');
    const W = el.clientWidth || 700;
    const H = el.clientHeight || 320;
    canvas.width  = W;
    canvas.height = H;
    if (!canvas.parentElement) el.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    const pad = { top: 12, right: 8, bottom: 32, left: 56 };
    const cW  = W - pad.left - pad.right;
    const cH  = H - pad.top - pad.bottom;

    const highs  = bars.map(b => b.high);
    const lows   = bars.map(b => b.low);
    const minP   = Math.min(...lows) * 0.999;
    const maxP   = Math.max(...highs) * 1.001;
    const range  = maxP - minP || 1;
    const n      = bars.length;
    const bw     = Math.max(2, Math.floor(cW / n * 0.7));
    const slot   = cW / n;

    const py = v => pad.top + (1 - (v - minP) / range) * cH;
    const px = i => pad.left + i * slot + slot / 2;

    ctx.fillStyle = '#0A0A0A';
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = '#1A1A1A';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (cH / 4) * i;
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cW, y); ctx.stroke();
      const v = maxP - (range / 4) * i;
      ctx.fillStyle = '#8A8A8A';
      ctx.font = '10px JetBrains Mono, monospace';
      ctx.textAlign = 'right';
      ctx.fillText(v.toFixed(0), pad.left - 4, y + 4);
    }

    // Overlays: VWAP
    if (overlays.vwap && overlays.vwap.length === n) {
      ctx.strokeStyle = '#FFB347';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 2]);
      ctx.beginPath();
      overlays.vwap.forEach((v, i) => {
        if (v == null) return;
        i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v));
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Overlays: EMA9
    if (overlays.ema9 && overlays.ema9.length === n) {
      ctx.strokeStyle = '#5BBFFF';
      ctx.lineWidth = 1;
      ctx.beginPath();
      overlays.ema9.forEach((v, i) => {
        if (v == null) return;
        i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v));
      });
      ctx.stroke();
    }

    // Candles
    bars.forEach((b, i) => {
      const x   = px(i);
      const oY  = py(b.open);
      const cY  = py(b.close);
      const hY  = py(b.high);
      const lY  = py(b.low);
      const bull = b.close >= b.open;
      const col  = bull ? '#B8FF57' : '#FF4D4D';

      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, hY); ctx.lineTo(x, lY); ctx.stroke();

      const bodyTop = Math.min(oY, cY);
      const bodyH   = Math.max(Math.abs(cY - oY), 1);
      ctx.fillStyle = bull ? col + '80' : col;
      ctx.fillRect(x - bw / 2, bodyTop, bw, bodyH);
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.strokeRect(x - bw / 2, bodyTop, bw, bodyH);
    });

    // X-axis time labels (every nth bar)
    const step = Math.ceil(n / 8);
    ctx.fillStyle = '#8A8A8A';
    ctx.font = '9px DM Sans, sans-serif';
    ctx.textAlign = 'center';
    bars.forEach((b, i) => {
      if (i % step !== 0) return;
      const ts = new Date(b.time * (b.time < 1e12 ? 1000 : 1));
      const label = ts.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
      ctx.fillText(label, px(i), H - 8);
    });
  }

  // ── Advance/Decline bar ───────────────────────────────────────────────────
  function renderADBar(containerId, advance, decline, neutral) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const total = advance + decline + neutral || 1;
    const aP = (advance / total * 100).toFixed(0);
    const dP = (decline / total * 100).toFixed(0);
    const nP = (neutral / total * 100).toFixed(0);
    el.innerHTML = `
      <div class="ad-bar-wrapper">
        <div class="ad-bar">
          <div style="width:${aP}%;background:#B8FF57;height:100%;border-radius:4px 0 0 4px"></div>
          <div style="width:${nP}%;background:#2A2A2A;height:100%"></div>
          <div style="width:${dP}%;background:#FF4D4D;height:100%;border-radius:0 4px 4px 0"></div>
        </div>
        <div class="ad-bar-labels">
          <span style="color:#B8FF57">▲ ${advance}</span>
          <span style="color:#8A8A8A">${neutral} flat</span>
          <span style="color:#FF4D4D">▼ ${decline}</span>
        </div>
      </div>`;
  }

  // ── Weekday PnL chart ─────────────────────────────────────────────────────
  function renderWeekdayChart(containerId, byWeekday) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const days  = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
    const items = days.map(d => ({ day: d, data: byWeekday[d] || { pnl: 0, trades: 0 } }));
    const maxV  = Math.max(...items.map(i => Math.abs(i.data.pnl)), 1);

    el.innerHTML = items.map(({ day, data }) => {
      const barW = Math.abs(data.pnl) / maxV * 100;
      const col  = data.pnl >= 0 ? '#B8FF57' : '#FF4D4D';
      return `
        <div class="weekday-row">
          <span class="weekday-label">${day.slice(0, 3)}</span>
          <div class="weekday-bar-track">
            <div style="width:${barW}%;background:${col};height:100%;border-radius:2px"></div>
          </div>
          <span class="mono" style="color:${col};font-size:11px;white-space:nowrap">
            ${data.pnl >= 0 ? '+' : ''}₹${_fmtK(data.pnl)} (${data.trades}t)
          </span>
        </div>`;
    }).join('');
  }

  // ── Drawdown chart ────────────────────────────────────────────────────────
  function renderDrawdown(containerId, equityCurveData) {
    const el = document.getElementById(containerId);
    if (!el || !equityCurveData || !equityCurveData[1]) {
      if (el) el.innerHTML = '<div class="chart-empty">No drawdown data</div>';
      return;
    }

    const equity = equityCurveData[1];
    const times  = equityCurveData[0];
    let peak = equity[0];
    const dd = equity.map((v, i) => {
      if (v > peak) peak = v;
      return peak > 0 ? (v - peak) / peak * 100 : 0;
    });

    const canvas = document.createElement('canvas');
    const W = el.clientWidth || 600;
    const H = 120;
    canvas.width  = W;
    canvas.height = H;
    el.innerHTML  = '';
    el.appendChild(canvas);

    const ctx  = canvas.getContext('2d');
    const pad  = { top: 8, right: 8, bottom: 24, left: 48 };
    const cW   = W - pad.left - pad.right;
    const cH   = H - pad.top - pad.bottom;
    const minDD = Math.min(...dd) * 1.1 || -1;

    const py = v => pad.top + (1 - Math.abs(v) / Math.abs(minDD)) * cH;
    const px = i => pad.left + (i / (dd.length - 1)) * cW;

    ctx.fillStyle = '#0A0A0A';
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = '#1A1A1A'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, pad.top); ctx.lineTo(pad.left + cW, pad.top); ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(px(0), pad.top);
    dd.forEach((v, i) => ctx.lineTo(px(i), py(v)));
    ctx.lineTo(px(dd.length - 1), pad.top);
    ctx.closePath();
    ctx.fillStyle = 'rgba(255,77,77,0.25)';
    ctx.fill();

    ctx.beginPath();
    dd.forEach((v, i) => i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v)));
    ctx.strokeStyle = '#FF4D4D';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.fillStyle = '#8A8A8A';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(minDD.toFixed(1) + '%', pad.left - 4, pad.top + cH - 4);
    ctx.fillText('0%', pad.left - 4, pad.top + 4);
  }

  // ── Helper ────────────────────────────────────────────────────────────────
  function _fmtK(n) {
    if (!n && n !== 0) return '—';
    const abs = Math.abs(n);
    if (abs >= 100000) return (n / 100000).toFixed(1) + 'L';
    if (abs >= 1000)   return (n / 1000).toFixed(1) + 'K';
    return n.toFixed(0);
  }

  function destroy(containerId) { _destroy(containerId); }

  return {
    renderEquityCurve,
    renderSparkline,
    renderConfidenceGauge,
    renderMonthlyReturns,
    renderCandlestick,
    renderADBar,
    renderWeekdayChart,
    renderDrawdown,
    destroy,
  };
})();
