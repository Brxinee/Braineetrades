/**
 * heatmap.js — Canvas-based Nifty 50 market heatmap with squarified treemap
 * Exposes: window.Heatmap
 */
window.Heatmap = (() => {
  let _canvas   = null;
  let _ctx      = null;
  let _data     = null;
  let _tooltip  = null;
  let _cells    = [];   // [{item, rx, ry, rw, rh}] after layout
  let _view     = 'stocks';  // 'stocks' | 'sector'

  // ── Init ──────────────────────────────────────────────────────────────────
  function init(canvasId, tooltipId) {
    _canvas  = document.getElementById(canvasId);
    _tooltip = document.getElementById(tooltipId || 'heatmap-tooltip');
    if (!_canvas) return;
    _ctx = _canvas.getContext('2d');

    _canvas.addEventListener('mousemove', _onHover);
    _canvas.addEventListener('mouseleave', _hideTooltip);
    _canvas.addEventListener('click', _onClick);
    _canvas.addEventListener('touchstart', _onTouch, { passive: true });
    window.addEventListener('resize', _onResize);
  }

  // ── Render ────────────────────────────────────────────────────────────────
  function render(data) {
    _data = data;
    if (!_canvas || !data || !data.stocks) return;
    _resize();
    _layout();
    _draw();
  }

  function renderSectorView(data) {
    _data  = data;
    _view  = 'sector';
    if (!_canvas || !data) return;
    _resize();
    _layout();
    _draw();
  }

  function setView(view) {
    _view = view;
    if (_data) { _layout(); _draw(); }
  }

  // ── Layout (squarified treemap) ───────────────────────────────────────────
  function _layout() {
    if (!_data) return;
    const items = _view === 'sector'
      ? _buildSectorItems()
      : (_data.stocks || []).map(s => ({ ...s, value: s.weight || 1 }));

    if (!items.length) return;

    const W = _canvas.width, H = _canvas.height;
    _cells = [];
    _squarify(items, { x: 0, y: 0, w: W, h: H });
  }

  function _buildSectorItems() {
    const sectorMap = {};
    (_data.stocks || []).forEach(s => {
      if (!sectorMap[s.sector]) sectorMap[s.sector] = { name: s.sector, weight: 0, change_pct: 0, count: 0 };
      sectorMap[s.sector].weight   += (s.weight || 1);
      sectorMap[s.sector].change_pct += (s.change_pct || 0);
      sectorMap[s.sector].count++;
    });
    return Object.values(sectorMap).map(s => ({
      ...s, value: s.weight, change_pct: s.change_pct / (s.count || 1), symbol: s.name,
    }));
  }

  function _squarify(items, bounds) {
    if (!items.length) return;
    const total = items.reduce((s, i) => s + Math.abs(i.value || 1), 0);
    _squarifyRow(items, total, bounds);
  }

  function _squarifyRow(items, total, { x, y, w, h }) {
    if (!items.length || total <= 0) return;
    if (items.length === 1) {
      _cells.push({ item: items[0], rx: x, ry: y, rw: w, rh: h });
      return;
    }

    const isWide   = w >= h;
    const minSide  = Math.min(w, h);
    const row      = [];
    let   rowArea  = 0;
    let   bestRatio = Infinity;
    let   i        = 0;

    while (i < items.length) {
      const item = items[i];
      const area = ((item.value || 1) / total) * (w * h);
      rowArea += area;
      row.push(item);

      const rowW  = rowArea / minSide;
      const worst = _worst(row, rowW, rowArea);
      if (worst > bestRatio && row.length > 1) {
        row.pop();
        rowArea -= area;
        break;
      }
      bestRatio = worst;
      i++;
    }

    // Place row cells
    const rowLen = isWide ? rowArea / h : rowArea / w;
    let   cursor = isWide ? x : y;
    row.forEach(item => {
      const a   = (item.value || 1) / total * (w * h);
      const dim = a / rowLen;
      const rx  = isWide ? cursor      : x;
      const ry  = isWide ? y           : cursor;
      const rw  = isWide ? rowLen      : dim;
      const rh  = isWide ? dim         : rowLen;
      _cells.push({ item, rx, ry, rw: Math.max(rw, 2), rh: Math.max(rh, 2) });
      cursor += dim;
    });

    // Recurse on remainder
    const remaining = items.slice(i === 0 ? row.length : i);
    const remainBounds = isWide
      ? { x: x + rowLen, y, w: w - rowLen, h }
      : { x, y: y + rowLen, w, h: h - rowLen };
    const remTotal = remaining.reduce((s, it) => s + Math.abs(it.value || 1), 0);
    if (remaining.length && remTotal > 0) _squarify(remaining, remainBounds);
  }

  function _worst(row, rowW, rowArea) {
    if (!rowArea || !rowW) return Infinity;
    return row.reduce((mx, item) => {
      const a = (item.value || 1) / rowArea * rowArea;
      const cellH = a / rowW;
      const r = Math.max(rowW / cellH, cellH / rowW);
      return Math.max(mx, r);
    }, 0);
  }

  // ── Draw ─────────────────────────────────────────────────────────────────
  function _draw() {
    if (!_ctx || !_cells.length) return;
    const W = _canvas.width, H = _canvas.height;
    _ctx.clearRect(0, 0, W, H);
    _ctx.fillStyle = '#0A0A0A';
    _ctx.fillRect(0, 0, W, H);

    _cells.forEach(({ item, rx, ry, rw, rh }) => {
      const col = _changeColor(item.change_pct || 0);

      _ctx.fillStyle   = col;
      _ctx.strokeStyle = '#0A0A0A';
      _ctx.lineWidth   = 1.5;
      _ctx.fillRect(rx, ry, rw, rh);
      _ctx.strokeRect(rx + 0.5, ry + 0.5, rw - 1, rh - 1);

      if (rw < 24 || rh < 16) return;

      const symFontSize  = Math.min(13, rw * 0.2, rh * 0.28);
      const pctFontSize  = Math.min(11, rw * 0.16, rh * 0.22);
      const textColor    = Math.abs(item.change_pct || 0) > 0.5 ? '#000' : '#E8E8E8';
      const cx           = rx + rw / 2;
      const cy           = ry + rh / 2;

      if (rw > 40 && rh > 28) {
        _ctx.fillStyle   = textColor;
        _ctx.font        = `700 ${symFontSize}px 'Barlow Condensed', sans-serif`;
        _ctx.textAlign   = 'center';
        _ctx.textBaseline = 'middle';
        _ctx.fillText((item.symbol || '').slice(0, 8), cx, cy - pctFontSize * 0.6);

        _ctx.font        = `400 ${pctFontSize}px 'JetBrains Mono', monospace`;
        const pctText    = (item.change_pct >= 0 ? '+' : '') + (item.change_pct || 0).toFixed(2) + '%';
        _ctx.fillText(pctText, cx, cy + symFontSize * 0.6);
      } else if (rw > 24 && rh > 18) {
        _ctx.fillStyle   = textColor;
        _ctx.font        = `700 ${Math.min(symFontSize, 11)}px 'Barlow Condensed', sans-serif`;
        _ctx.textAlign   = 'center';
        _ctx.textBaseline = 'middle';
        _ctx.fillText((item.symbol || '').slice(0, 6), cx, cy);
      }
    });
  }

  // ── Color scale ───────────────────────────────────────────────────────────
  function _changeColor(pct) {
    if      (pct >  3.0) return '#B8FF57';
    else if (pct >  1.5) return '#7ACC3C';
    else if (pct >  0.5) return '#4A8A2A';
    else if (pct > -0.5) return '#2A2A2A';
    else if (pct > -1.5) return '#993333';
    else if (pct > -3.0) return '#CC2222';
    else                  return '#8B0000';
  }

  // ── Tooltip ───────────────────────────────────────────────────────────────
  function _showTooltip(item, x, y) {
    if (!_tooltip) return;
    const sign = (item.change_pct || 0) >= 0 ? '+' : '';
    const col  = (item.change_pct || 0) >= 0 ? '#B8FF57' : '#FF4D4D';
    _tooltip.innerHTML = `
      <div style="font-weight:700;font-family:'Barlow Condensed',sans-serif;font-size:14px">${item.symbol}</div>
      ${item.name && item.name !== item.symbol ? `<div style="font-size:11px;color:#8A8A8A">${item.name}</div>` : ''}
      <div style="font-family:'JetBrains Mono',monospace;color:${col};font-size:13px;margin-top:4px">${sign}${(item.change_pct || 0).toFixed(2)}%</div>
      ${item.ltp ? `<div style="font-family:'JetBrains Mono',monospace;font-size:12px;color:#E8E8E8">₹${item.ltp.toLocaleString('en-IN')}</div>` : ''}
      ${item.sector && item.sector !== item.symbol ? `<div style="font-size:10px;color:#8A8A8A">${item.sector}</div>` : ''}`;

    const rect = _canvas.getBoundingClientRect();
    const tooltipW = 140;
    const left = Math.min(x + rect.left + 12, window.innerWidth - tooltipW - 8);
    _tooltip.style.left    = left + 'px';
    _tooltip.style.top     = (y + rect.top - 80) + 'px';
    _tooltip.style.display = 'block';
  }

  function _hideTooltip() {
    if (_tooltip) _tooltip.style.display = 'none';
  }

  // ── Events ────────────────────────────────────────────────────────────────
  function _onHover(e) {
    const { offsetX: mx, offsetY: my } = e;
    const cell = _cells.find(c => mx >= c.rx && mx <= c.rx + c.rw && my >= c.ry && my <= c.ry + c.rh);
    if (cell) _showTooltip(cell.item, mx, my);
    else _hideTooltip();
  }

  function _onTouch(e) {
    if (!e.touches[0]) return;
    const rect = _canvas.getBoundingClientRect();
    const mx   = e.touches[0].clientX - rect.left;
    const my   = e.touches[0].clientY - rect.top;
    const cell = _cells.find(c => mx >= c.rx && mx <= c.rx + c.rw && my >= c.ry && my <= c.ry + c.rh);
    if (cell) _showTooltip(cell.item, mx, my);
  }

  function _onClick(e) {
    const { offsetX: mx, offsetY: my } = e;
    const cell = _cells.find(c => mx >= c.rx && mx <= c.rx + c.rw && my >= c.ry && my <= c.ry + c.rh);
    if (cell && window.APP) {
      // Could navigate to option chain for this symbol
    }
  }

  function _onResize() {
    if (_data) { _resize(); _layout(); _draw(); }
  }

  function _resize() {
    if (!_canvas) return;
    const parent = _canvas.parentElement;
    if (!parent) return;
    _canvas.width  = parent.clientWidth  || 600;
    _canvas.height = parent.clientHeight || 380;
  }

  return {
    init,
    render,
    renderSectorView,
    setView,
    _changeColor,
  };
})();
