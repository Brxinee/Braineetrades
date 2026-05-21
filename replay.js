/**
 * replay.js — Historical session replay engine (bar-by-bar)
 * Exposes: window.Replay
 */
window.Replay = (() => {
  let _sessionData  = null;
  let _currentBar   = 0;
  let _playing      = false;
  let _speed        = 1;
  let _timer        = null;
  let _symbol       = '^NSEI';
  let _date         = '';

  const SPEED_MS = { 1: 1000, 5: 200, 20: 50 };

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    _bindControls();
    _populateDatePicker();
  }

  function _bindControls() {
    const playBtn    = document.getElementById('replay-play-btn');
    const rewindBtn  = document.getElementById('replay-rewind-btn');
    const stepBtn    = document.getElementById('replay-step-btn');
    const loadBtn    = document.getElementById('replay-load-btn');
    const dateInput  = document.getElementById('replay-date-input');
    const symbolSel  = document.getElementById('replay-symbol-select');

    if (playBtn)   playBtn.addEventListener('click',   () => _playing ? pause() : play());
    if (rewindBtn) rewindBtn.addEventListener('click', rewind);
    if (stepBtn)   stepBtn.addEventListener('click',   stepForward);
    if (loadBtn)   loadBtn.addEventListener('click', () => {
      const d = dateInput?.value;
      const s = symbolSel?.value || '^NSEI';
      if (d) loadSession(d, s);
      else window.UI.toast('Select a date first', 'warning');
    });

    document.querySelectorAll('[data-speed]').forEach(btn => {
      btn.addEventListener('click', () => {
        const s = parseInt(btn.dataset.speed);
        setSpeed(s);
        document.querySelectorAll('[data-speed]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });
  }

  async function _populateDatePicker() {
    const el = document.getElementById('replay-date-input');
    if (!el) return;
    // Default to last 30 trading days
    const today = new Date();
    const max   = today.toISOString().slice(0, 10);
    const min30 = new Date(today - 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    el.max = max;
    el.min = min30;
    el.value = max;
  }

  // ── Load session ──────────────────────────────────────────────────────────
  async function loadSession(date, symbol) {
    reset();
    _date   = date;
    _symbol = symbol || '^NSEI';

    const statusEl = document.getElementById('replay-status');
    if (statusEl) statusEl.textContent = `Loading ${date}…`;
    window.UI.showLoading('replay-chart', 'Fetching session data…');

    try {
      _sessionData = await window.APP.API.replaySession(date, symbol);
    } catch (e) {
      window.UI.hideLoading('replay-chart');
      window.UI.toast('Failed to load session: ' + e.message, 'error');
      return;
    }

    window.UI.hideLoading('replay-chart');

    if (!_sessionData || !_sessionData.bars || _sessionData.bars.length === 0) {
      window.UI.toast('No data for ' + date, 'warning');
      return;
    }

    _currentBar = 0;
    _renderControls();
    _renderBar(_currentBar);
    if (statusEl) statusEl.textContent = `${_sessionData.bars.length} bars — ${_sessionData.symbol || symbol}`;
    window.UI.toast('Session loaded: ' + date, 'success');
  }

  // ── Playback ──────────────────────────────────────────────────────────────
  function play() {
    if (!_sessionData) return;
    _playing = true;
    _renderControls();
    _timer = setInterval(() => {
      if (_currentBar >= _sessionData.bars.length - 1) { pause(); return; }
      _currentBar++;
      _renderBar(_currentBar);
    }, SPEED_MS[_speed] || 1000);
  }

  function pause() {
    _playing = false;
    clearInterval(_timer);
    _timer = null;
    _renderControls();
  }

  function setSpeed(speed) {
    _speed = speed;
    if (_playing) { pause(); play(); }
  }

  function stepForward() {
    if (!_sessionData) return;
    pause();
    if (_currentBar < _sessionData.bars.length - 1) {
      _currentBar++;
      _renderBar(_currentBar);
    }
  }

  function rewind() {
    pause();
    _currentBar = 0;
    if (_sessionData) _renderBar(_currentBar);
  }

  // ── Render bar ────────────────────────────────────────────────────────────
  function _renderBar(idx) {
    if (!_sessionData) return;
    const barsSlice = _sessionData.bars.slice(0, idx + 1);

    const overlays = {
      vwap:  barsSlice.map(b => b.vwap),
      ema9:  barsSlice.map(b => b.ema9),
      ema21: barsSlice.map(b => b.ema21),
    };

    window.Charts.renderCandlestick('replay-chart', barsSlice, overlays);
    _renderBarInfo(barsSlice[barsSlice.length - 1]);
    _checkSignals(idx);
    _updateProgress(idx);
  }

  function _renderBarInfo(bar) {
    const el = document.getElementById('replay-bar-info');
    if (!el || !bar) return;

    const ts      = new Date(bar.time * (bar.time < 1e12 ? 1000 : 1));
    const timeStr = ts.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
    const isGreen = bar.close >= bar.open;
    const col     = isGreen ? '#B8FF57' : '#FF4D4D';
    const stDir   = bar.supertrend_dir === 'buy' ? '▲ BULL' : bar.supertrend_dir === 'sell' ? '▼ BEAR' : '—';
    const stCol   = bar.supertrend_dir === 'buy' ? '#B8FF57' : '#FF4D4D';

    el.innerHTML = `
      <div class="replay-bar-header">
        <span class="mono" style="color:var(--accent);font-size:13px">${timeStr} IST</span>
        <span style="font-size:11px;color:var(--muted)">Bar ${_currentBar + 1} / ${_sessionData?.bars?.length || '—'}</span>
      </div>
      <div class="replay-ohlcv">
        <div class="replay-ohlcv-item"><span class="label">O</span><span class="mono">${bar.open?.toFixed(1) || '—'}</span></div>
        <div class="replay-ohlcv-item"><span class="label">H</span><span class="mono" style="color:#B8FF57">${bar.high?.toFixed(1) || '—'}</span></div>
        <div class="replay-ohlcv-item"><span class="label">L</span><span class="mono" style="color:#FF4D4D">${bar.low?.toFixed(1) || '—'}</span></div>
        <div class="replay-ohlcv-item"><span class="label">C</span><span class="mono" style="color:${col};font-weight:600">${bar.close?.toFixed(1) || '—'}</span></div>
      </div>
      <div class="replay-indicators">
        ${bar.vwap   ? `<div><span class="label">VWAP</span> <span class="mono">${bar.vwap.toFixed(1)}</span></div>` : ''}
        ${bar.ema9   ? `<div><span class="label">EMA9</span> <span class="mono">${bar.ema9.toFixed(1)}</span></div>` : ''}
        ${bar.rsi    ? `<div><span class="label">RSI</span> <span class="mono" style="color:${bar.rsi > 70 ? '#FF4D4D' : bar.rsi < 30 ? '#B8FF57' : '#E8E8E8'}">${bar.rsi.toFixed(0)}</span></div>` : ''}
        ${bar.supertrend_dir ? `<div><span class="label">ST</span> <span style="color:${stCol};font-size:11px;font-weight:700">${stDir}</span></div>` : ''}
        ${bar.atr    ? `<div><span class="label">ATR</span> <span class="mono">${bar.atr.toFixed(1)}</span></div>` : ''}
      </div>`;
  }

  function _checkSignals(idx) {
    if (!_sessionData || !_sessionData.signals) return;
    const bar     = _sessionData.bars[idx];
    if (!bar) return;
    const barTime = bar.time;
    const signals = _sessionData.signals.filter(s => {
      const st = typeof s.signal_bar === 'number' ? s.signal_bar : idx;
      return st === idx;
    });
    if (!signals.length) return;

    const el = document.getElementById('replay-signals');
    if (!el) return;
    signals.forEach(sig => {
      const card = document.createElement('div');
      card.className = 'replay-signal-flash';
      const dir   = sig.direction || 'SIGNAL';
      const col   = dir.includes('CE') ? '#B8FF57' : '#FF4D4D';
      card.innerHTML = `
        <span style="color:${col};font-weight:700">${dir}</span>
        <span class="mono" style="font-size:11px">Entry: ${sig.entry?.toFixed(1) || '—'} | SL: ${sig.sl?.toFixed(1) || '—'}</span>
        <span style="font-size:10px;color:var(--muted)">${sig.strategy || ''}</span>`;
      el.prepend(card);
      setTimeout(() => card.classList.add('visible'), 50);
    });
  }

  function _updateProgress(idx) {
    const el = document.getElementById('replay-progress');
    if (!el || !_sessionData) return;
    const pct = (idx / Math.max(_sessionData.bars.length - 1, 1)) * 100;
    el.style.width = pct.toFixed(1) + '%';

    const timeEl = document.getElementById('replay-current-time');
    if (timeEl && _sessionData.bars[idx]) {
      const bar = _sessionData.bars[idx];
      const ts  = new Date(bar.time * (bar.time < 1e12 ? 1000 : 1));
      timeEl.textContent = ts.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
    }
  }

  // ── Controls render ───────────────────────────────────────────────────────
  function _renderControls() {
    const playBtn = document.getElementById('replay-play-btn');
    if (playBtn) {
      playBtn.textContent = _playing ? '⏸ Pause' : '▶ Play';
      playBtn.classList.toggle('btn-primary', !_playing);
    }
  }

  // ── Reset ──────────────────────────────────────────────────────────────────
  function reset() {
    pause();
    _sessionData  = null;
    _currentBar   = 0;
    const el = document.getElementById('replay-chart');
    if (el) el.innerHTML = '<div class="chart-empty" style="height:200px;display:flex;align-items:center;justify-content:center;color:var(--muted)">Select a date and click Load Session</div>';
    const sigEl = document.getElementById('replay-signals');
    if (sigEl) sigEl.innerHTML = '';
    const infoEl = document.getElementById('replay-bar-info');
    if (infoEl) infoEl.innerHTML = '';
    _updateProgress(0);
  }

  return { init, loadSession, play, pause, setSpeed, stepForward, rewind, reset };
})();
