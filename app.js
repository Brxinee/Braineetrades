/**
 * app.js — Main application module for NIFTY Intraday Trading Cockpit
 * Exposed as window.APP
 * Depends on: storage.js (window.Storage), ui.js (window.UI)
 */
(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════════════════════════
     STRATEGY REGISTRY
  ═══════════════════════════════════════════════════════════════════════ */

  const STRATEGIES = {
    orb15:                 { label: 'ORB-15 (Opening Range 15min)',    desc: 'Trade breakout of the first 15-min candle high/low.' },
    orb30:                 { label: 'ORB-30 (Opening Range 30min)',    desc: 'Trade breakout of the first 30-min candle high/low.' },
    first_hour_bo:         { label: 'First Hour Breakout',             desc: 'Breakout beyond the first-hour range after 10:15.' },
    vwap_mean_reversion:   { label: 'VWAP Mean Reversion',            desc: 'Fade price when it deviates 0.5% from VWAP with reversal candle.' },
    gap_continuation:      { label: 'Gap Continuation',               desc: 'Enter in gap direction after a 5-min consolidation post-open.' },
    gap_fill:              { label: 'Gap Fill',                        desc: 'Fade the gap expecting price to close back toward prev close.' },
    inside_bar_bo:         { label: 'Inside Bar Breakout',            desc: 'Enter on breakout of an inside bar after trending move.' },
    open_drive:            { label: 'Open Drive / Open Rejection',    desc: 'Identify strong directional open vs. immediate rejection.' },
    afternoon_trend:       { label: 'Afternoon Trend Continuation',   desc: 'Join established trend post-1:00 PM IST on pullbacks.' },
    eod_momentum:          { label: 'End-of-Day Momentum',            desc: 'Ride momentum moves from 2:30 PM with tight stop.' },
    trend_pullback:        { label: 'Trend Pullback',                 desc: 'Enter pullbacks to 20 EMA in trending conditions.' },
    failed_breakout:       { label: 'Failed Breakout Reversal',       desc: 'Fade breakouts that immediately reverse back inside range.' },
    cpr_vwap:              { label: 'CPR + VWAP Confluence',          desc: 'Trade confluence of Central Pivot Range and VWAP levels.' },
    vix_filter:            { label: 'VIX Filter (Meta)',              desc: 'Meta-strategy: enable/disable others based on India VIX level.' },
  };

  const NIFTY_SYMBOLS = ['^NSEI', '^NSEBANK'];

  /* ═══════════════════════════════════════════════════════════════════════
     API CLIENT
  ═══════════════════════════════════════════════════════════════════════ */

  // Auto-detect API base: on Vercel the frontend and API share the same origin.
  // When running locally against a separate backend, override in Settings.
  const _isVercel = window.location.hostname !== 'localhost' &&
                    window.location.hostname !== '127.0.0.1' &&
                    !window.location.hostname.startsWith('192.168');
  const _defaultApiBase = _isVercel ? '' : 'http://localhost:8000';

  const API = {
    base: () => {
      const saved = Storage.getSettings().apiBase;
      return (saved && saved.trim()) ? saved.trim() : _defaultApiBase;
    },

    async _fetch(method, path, body) {
      const url = `${API.base()}${path}`;
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      try {
        const res = await fetch(url, opts);
        if (!res.ok) {
          const err = await res.text().catch(() => res.statusText);
          throw new Error(`API ${res.status}: ${err}`);
        }
        return await res.json();
      } catch (e) {
        if (e.name === 'TypeError') throw new Error(`Cannot reach API at ${url}. Is the server running?`);
        throw e;
      }
    },

    get:  (path)       => API._fetch('GET',  path, null),
    post: (path, body) => API._fetch('POST', path, body),

    health:        ()                    => API.get('/api/health'),
    quotes:        (symbols)             => API.get('/api/quotes' + (symbols && symbols.length ? '?symbols=' + encodeURIComponent(symbols.join(',')) : '')),
    regime:        ()                    => API.get('/api/regime'),
    scan:          (strategy, symbols)   => API.post('/api/scan', { strategy, symbols }),
    backtest:      (params)              => API.post('/api/backtest', params),
    optionChain:   (symbol, expiry)      => API.get(`/api/options/chain?symbol=${encodeURIComponent(symbol)}&expiry=${encodeURIComponent(expiry)}`),
    internals:     ()                    => API.get('/api/internals'),
    heatmap:       ()                    => API.get('/api/heatmap'),
    risk:          (params)              => API.post('/api/risk', params),
    replaySession: (date, symbol)        => API.get(`/api/replay?date=${date}&symbol=${encodeURIComponent(symbol)}`),
    analyzeJournal:(trades)              => API.post('/api/journal/analyze', { trades }),
  };

  /* ═══════════════════════════════════════════════════════════════════════
     ROUTER
  ═══════════════════════════════════════════════════════════════════════ */

  const SCREEN_MAP = {
    '':              'dashboard',
    'scanner':       'scanner',
    'signals':       'signals',
    'strategy-lab':  'strategy-lab',
    'backtest':      'backtest',
    'replay':        'replay',
    'options':       'options',
    'heatmap':       'heatmap',
    'risk':          'risk',
    'journal':       'journal',
    'analytics':     'analytics',
    'settings':      'settings',
  };

  const Router = {
    current: '',

    navigate(hash) {
      window.location.hash = hash ? `#${hash}` : '#';
    },

    _activate(hash) {
      const key      = (hash || '').replace(/^#/, '');
      const screenId = SCREEN_MAP[key] || SCREEN_MAP[''];
      document.querySelectorAll('[data-screen]').forEach(el => {
        el.style.display = el.dataset.screen === screenId ? '' : 'none';
      });
      UI.setActiveNav(screenId);
      Router.current = screenId;
      Router._initScreen(screenId);
    },

    _initScreen(screenId) {
      const map = {
        dashboard:      () => DashboardScreen.init(),
        scanner:        () => ScannerScreen.init(),
        signals:        () => SignalsScreen.init(),
        'strategy-lab': () => StrategyLabScreen.init(),
        backtest:       () => BacktestScreen.init(),
        replay:         () => ReplayScreen.init(),
        options:        () => OptionsScreen.init(),
        heatmap:        () => HeatmapScreen.init(),
        risk:           () => RiskScreen.init(),
        journal:        () => JournalScreen.init(),
        analytics:      () => AnalyticsScreen.init(),
        settings:       () => SettingsScreen.init(),
      };
      if (map[screenId]) map[screenId]();
    },

    init() {
      window.addEventListener('hashchange', () => Router._activate(window.location.hash));
      Router._activate(window.location.hash);
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     DASHBOARD SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const DashboardScreen = {
    _rendered: false,

    async init() {
      if (!this._rendered) {
        this._rendered = true;
        this._buildLayout();
      }
      await this.refresh();
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="dashboard"]');
      if (!el) return;
      el.innerHTML = `
        <div class="screen-header">
          <h2>Dashboard</h2>
          <button onclick="DashboardScreen.refresh()" class="btn-secondary">↻ Refresh</button>
        </div>
        <div id="dash-regime-panel" class="card" style="margin-bottom:16px"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
          <div id="dash-internals-panel" class="card"></div>
          <div id="dash-quotes-panel" class="card"></div>
        </div>
        <div id="dash-signals-panel" class="card"></div>`;
    },

    async refresh() {
      const settings = Storage.getSettings();
      UI.showLoading('dash-regime-panel', 'Fetching market regime…');
      UI.showLoading('dash-quotes-panel', 'Fetching quotes…');
      UI.showLoading('dash-internals-panel', 'Loading internals…');
      UI.showLoading('dash-signals-panel', 'Scanning signals…');

      // Wave 1: regime + quotes (lightweight, 2 yfinance calls each)
      const [regime, quotes] = await Promise.allSettled([
        API.regime(),
        API.quotes(NIFTY_SYMBOLS),
      ]);

      if (regime.status === 'fulfilled') {
        this.renderRegimePanel(regime.value);
        UI.updateRegimeBadge(regime.value.regime);
        const prev = Storage.getLastRegime();
        if (prev && prev !== regime.value.regime) {
          if (settings.alertRegimeChange) UI.toast(`Regime changed: ${prev} → ${regime.value.regime}`, 'warning', 6000);
        }
        Storage.setLastRegime(regime.value.regime);
      } else {
        UI.showCardError('dash-regime-panel', 'Could not load regime: ' + regime.reason.message);
      }

      if (quotes.status === 'fulfilled') {
        this.renderQuotes(quotes.value);
        const list = Array.isArray(quotes.value) ? quotes.value : (quotes.value.quotes || []);
        const nifty     = list.find(x => x.symbol === '^NSEI');
        const banknifty = list.find(x => x.symbol === '^NSEBANK');
        UI.updateMarketBar(nifty, banknifty, AutoRefresh.isMarketHours());
      } else {
        UI.showCardError('dash-quotes-panel', 'Quotes unavailable');
      }

      // Wave 2: internals + scan (heavier — stagger 1s after wave 1 to avoid rate limit)
      await new Promise(r => setTimeout(r, 1000));
      const [internals, scan] = await Promise.allSettled([
        API.internals(),
        API.scan(settings.defaultStrategy, []),
      ]);

      if (internals.status === 'fulfilled') {
        this.renderInternals(internals.value);
      } else {
        UI.showCardError('dash-internals-panel', 'Internals unavailable');
      }

      if (scan.status === 'fulfilled') {
        this.renderSignals(scan.value);
        Storage.setSignalCache({ data: scan.value, ts: Date.now() });
      } else {
        UI.showCardError('dash-signals-panel', 'Signal scan unavailable');
      }
    },

    renderRegimePanel(data) {
      const el = document.getElementById('dash-regime-panel');
      if (!el) return;
      const regime  = data.regime || 'unknown';
      const color   = UI.getRegimeColor(regime);
      const icon    = UI.getRegimeIcon(regime);
      const conf    = data.confidence || 0;
      el.innerHTML  = `
        <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">
          <div style="font-size:48px">${icon}</div>
          <div style="flex:1;min-width:180px">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#64748b;margin-bottom:4px">Market Regime</div>
            <div style="font-size:26px;font-weight:700;color:${color}">${regime.replace(/_/g, ' ').toUpperCase()}</div>
            <div style="margin-top:8px;max-width:280px">${UI.renderConfidenceBar(conf)}</div>
          </div>
          ${data.vix ? `<div style="text-align:right"><div style="font-size:11px;color:#64748b">India VIX</div>
            <div style="font-size:22px;font-weight:600;color:#fb923c">${data.vix.toFixed(2)}</div></div>` : ''}
          ${data.bias ? `<div style="padding:8px 16px;border-radius:6px;border:1px solid ${color}44;background:${color}11;color:${color};font-size:14px;font-weight:600">
            ${data.bias}</div>` : ''}
        </div>
        ${data.notes ? `<p style="margin:12px 0 0;font-size:13px;color:#94a3b8">${data.notes}</p>` : ''}`;
    },

    renderSignals(data) {
      const el = document.getElementById('dash-signals-panel');
      if (!el) return;
      const signals = Array.isArray(data) ? data : (data.signals || []);
      if (!signals.length) {
        UI.showEmpty(el, 'No signals found for this strategy right now.', '📡');
        return;
      }
      el.innerHTML = `<h3 style="margin:0 0 12px;font-size:15px;color:#94a3b8">Signals</h3>` +
        signals.map(s => `
          <div style="border:1px solid #1e293b;border-radius:8px;padding:12px 14px;margin-bottom:10px;background:#0f172a">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
              <div>
                <span style="font-weight:700;font-size:15px;color:#e2e8f0">${s.symbol || '—'}</span>
                <span style="margin-left:10px;font-size:12px;color:#64748b">${s.strategy || ''}</span>
              </div>
              <div style="display:flex;gap:8px;align-items:center">
                ${UI.renderDirectionBadge(s.direction)}
                <span style="font-size:12px;color:#64748b">${UI.formatPrice(s.entry || s.ltp)}</span>
              </div>
            </div>
            ${s.confidence !== undefined ? `<div style="margin-top:8px;max-width:240px">${UI.renderConfidenceBar(s.confidence)}</div>` : ''}
          </div>`).join('');
    },

    renderQuotes(data) {
      const el = document.getElementById('dash-quotes-panel');
      if (!el) return;
      const quotes = Array.isArray(data) ? data : (Array.isArray(data.quotes) ? data.quotes : []);
      el.innerHTML = `<h3 style="margin:0 0 12px;font-size:15px;color:#94a3b8">Live Quotes</h3>` +
        quotes.map(q => {
          const { change, changePct, sign } = UI.formatChange(q.ltp || q.last_price, q.prev_close);
          const color = change >= 0 ? '#4ade80' : '#f87171';
          return `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e293b">
            <span style="color:#e2e8f0;font-size:14px">${q.symbol}</span>
            <span style="color:#e2e8f0;font-weight:600">${(q.ltp || q.last_price || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
            <span style="color:${color};font-size:13px">${sign}${Math.abs(changePct).toFixed(2)}%</span>
          </div>`;
        }).join('');
    },

    renderInternals(data) {
      const el = document.getElementById('dash-internals-panel');
      if (!el) return;
      const b = data.breadth || data;
      const adv = b.advances || data.advances || 0, dec = b.declines || data.declines || 0, unch = b.unchanged || 0;
      const total = adv + dec + unch || 1;
      const advPct = ((adv / total) * 100).toFixed(0);
      const decPct = ((dec / total) * 100).toFixed(0);
      el.innerHTML = `
        <h3 style="margin:0 0 12px;font-size:15px;color:#94a3b8">Market Internals</h3>
        <div style="display:flex;gap:4px;border-radius:4px;overflow:hidden;height:10px;margin-bottom:8px">
          <div style="width:${advPct}%;background:#4ade80"></div>
          <div style="width:${decPct}%;background:#f87171"></div>
          <div style="flex:1;background:#334155"></div>
        </div>
        <div style="display:flex;gap:16px;font-size:13px;margin-bottom:14px">
          <span style="color:#4ade80">▲ ${adv} Adv</span>
          <span style="color:#f87171">▼ ${dec} Dec</span>
          <span style="color:#64748b">— ${unch} Unch</span>
        </div>
        ${data.sectors ? `<div>${Object.entries(data.sectors).slice(0, 6).map(([s, v]) => {
          const c = v >= 0 ? '#4ade80' : '#f87171';
          return `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid #1e293b">
            <span style="color:#94a3b8">${s}</span><span style="color:${c}">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span></div>`;
        }).join('')}</div>` : ''}`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     SCANNER SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const ScannerScreen = {
    _timer: null,
    _built: false,

    init() {
      if (!this._built) { this._built = true; this._buildLayout(); }
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="scanner"]');
      if (!el) return;
      const opts = Object.entries(STRATEGIES).map(([k, v]) =>
        `<option value="${k}">${v.label}</option>`).join('');
      el.innerHTML = `
        <div class="screen-header">
          <h2>Scanner</h2>
          <div style="display:flex;gap:8px;align-items:center">
            <label style="font-size:13px;color:#64748b">Auto-refresh
              <input type="number" id="scan-interval" value="60" min="10" max="600"
                style="width:60px;margin-left:6px;background:#1e293b;border:1px solid #334155;
                  color:#e2e8f0;border-radius:4px;padding:3px 6px">s
            </label>
            <button id="scan-auto-btn" onclick="ScannerScreen.toggleAuto()" class="btn-secondary">▶ Auto</button>
            <button onclick="ScannerScreen.scan()" class="btn-primary">Scan Now</button>
          </div>
        </div>
        <div class="card" style="margin-bottom:16px">
          <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">
            <div style="flex:1;min-width:200px">
              <label class="form-label">Strategy</label>
              <select id="scan-strategy" onchange="ScannerScreen._updateDesc()"
                style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;
                  border-radius:6px;padding:8px;font-size:14px">${opts}</select>
              <div id="scan-strategy-desc" style="font-size:12px;color:#64748b;margin-top:6px"></div>
            </div>
            <div style="flex:1;min-width:200px">
              <label class="form-label">Symbols</label>
              <input id="scan-symbols" value="NIFTY 50,NIFTY BANK"
                style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;
                  border-radius:6px;padding:8px;font-size:14px;box-sizing:border-box"
                placeholder="Comma-separated symbols">
            </div>
          </div>
        </div>
        <div id="scan-results" class="card"></div>`;
      // Set default strategy from settings
      const sel = document.getElementById('scan-strategy');
      if (sel) sel.value = Storage.getSettings().defaultStrategy;
      this._updateDesc();
    },

    _updateDesc() {
      const sel  = document.getElementById('scan-strategy');
      const desc = document.getElementById('scan-strategy-desc');
      if (!sel || !desc) return;
      desc.textContent = STRATEGIES[sel.value] ? STRATEGIES[sel.value].desc : '';
    },

    async scan() {
      const stratEl = document.getElementById('scan-strategy');
      const symEl   = document.getElementById('scan-symbols');
      if (!stratEl || !symEl) return;
      const strategy = stratEl.value;
      const symbols  = symEl.value.split(',').map(s => s.trim()).filter(Boolean);
      const resultsEl = document.getElementById('scan-results');
      UI.showLoading('scan-results', `Running ${STRATEGIES[strategy]?.label || strategy}…`);
      try {
        const data = await API.scan(strategy, symbols);
        const signals = Array.isArray(data) ? data : (data.signals || []);
        Storage.setSignalCache({ data: signals, strategy, ts: Date.now() });
        this.renderResults(signals, strategy);
      } catch (e) {
        UI.showCardError('scan-results', 'Scan failed: ' + e.message);
      }
    },

    renderResults(signals, strategy) {
      const el = document.getElementById('scan-results');
      if (!el) return;
      if (!signals.length) { UI.showEmpty(el, 'No signals found.', '📡'); return; }
      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="margin:0;font-size:15px">${signals.length} Signal${signals.length !== 1 ? 's' : ''} — ${STRATEGIES[strategy]?.label || strategy}</h3>
          <span style="font-size:12px;color:#64748b">${new Date().toLocaleTimeString('en-IN')}</span>
        </div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="color:#64748b;text-align:left;border-bottom:1px solid #1e293b">
                <th style="padding:8px">Symbol</th><th>Direction</th><th>Entry</th>
                <th>Target</th><th>Stop</th><th>Confidence</th><th>Notes</th>
              </tr>
            </thead>
            <tbody>
              ${signals.map(s => `<tr style="border-bottom:1px solid #0f172a">
                <td style="padding:8px;font-weight:600;color:#e2e8f0">${s.symbol || '—'}</td>
                <td>${UI.renderDirectionBadge(s.direction)}</td>
                <td>${UI.formatPrice(s.entry || s.ltp)}</td>
                <td style="color:#4ade80">${s.target ? UI.formatPrice(s.target) : '—'}</td>
                <td style="color:#f87171">${s.stop_loss ? UI.formatPrice(s.stop_loss) : '—'}</td>
                <td style="min-width:120px">${UI.renderConfidenceBar(s.confidence || 0)}</td>
                <td style="color:#94a3b8;font-size:12px;max-width:200px">${s.notes || ''}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    },

    toggleAuto() {
      if (this._timer) { this.stopAutoRefresh(); }
      else {
        const sec = parseInt(document.getElementById('scan-interval')?.value || 60, 10);
        this.startAutoRefresh(sec);
      }
    },

    startAutoRefresh(intervalSecs) {
      this.stopAutoRefresh();
      this._timer = setInterval(() => this.scan(), intervalSecs * 1000);
      const btn = document.getElementById('scan-auto-btn');
      if (btn) { btn.textContent = '■ Stop'; btn.style.background = '#450a0a'; }
      this.scan();
    },

    stopAutoRefresh() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      const btn = document.getElementById('scan-auto-btn');
      if (btn) { btn.textContent = '▶ Auto'; btn.style.background = ''; }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     SIGNALS SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const SignalsScreen = {
    init() {
      const el = document.querySelector('[data-screen="signals"]');
      if (!el) return;
      const cached = Storage.getSignalCache();
      el.innerHTML = `<div class="screen-header"><h2>Signal History</h2></div><div id="signals-list" class="card"></div>`;
      const list   = document.getElementById('signals-list');
      if (cached && cached.data && cached.data.length) {
        const signals = Array.isArray(cached.data) ? cached.data : [];
        if (!signals.length) { UI.showEmpty(list, 'No cached signals. Run a scan first.', '📡'); return; }
        list.innerHTML = signals.map(s => `
          <div style="border-bottom:1px solid #1e293b;padding:12px 0">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
              <div><span style="font-weight:700;color:#e2e8f0">${s.symbol || '—'}</span>
                <span style="margin-left:8px;font-size:12px;color:#64748b">${s.strategy || ''}</span></div>
              <div style="display:flex;gap:8px">${UI.renderDirectionBadge(s.direction)}
                <span style="font-size:12px;color:#64748b">${UI.formatPrice(s.entry || s.ltp)}</span></div>
            </div>
            ${s.confidence !== undefined ? `<div style="margin-top:6px;max-width:220px">${UI.renderConfidenceBar(s.confidence)}</div>` : ''}
          </div>`).join('');
      } else {
        UI.showEmpty(list, 'No cached signals. Run a scan from the Scanner screen.', '📡');
      }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     STRATEGY LAB SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const StrategyLabScreen = {
    init() {
      const el = document.querySelector('[data-screen="strategy-lab"]');
      if (!el) return;
      el.innerHTML = `
        <div class="screen-header"><h2>Strategy Lab</h2></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px">
          ${Object.entries(STRATEGIES).map(([k, v]) => `
            <div class="card" style="cursor:pointer" onclick="window.location.hash='#backtest';BacktestScreen._setStrategy('${k}')">
              <div style="font-weight:700;color:#e2e8f0;margin-bottom:6px">${v.label}</div>
              <div style="font-size:13px;color:#64748b;line-height:1.5">${v.desc}</div>
              <div style="margin-top:12px">
                <button class="btn-primary" style="font-size:12px;padding:5px 12px">Backtest →</button>
              </div>
            </div>`).join('')}
        </div>`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     BACKTEST SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const BacktestScreen = {
    _built: false,

    init() {
      if (!this._built) { this._built = true; this._buildLayout(); }
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="backtest"]');
      if (!el) return;
      const opts = Object.entries(STRATEGIES).map(([k, v]) =>
        `<option value="${k}">${v.label}</option>`).join('');
      el.innerHTML = `
        <div class="screen-header"><h2>Backtest</h2></div>
        <div class="card" style="margin-bottom:16px">
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">
            <div>
              <label class="form-label">Strategy</label>
              <select id="bt-strategy" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">${opts}</select>
            </div>
            <div>
              <label class="form-label">Symbol</label>
              <input id="bt-symbol" value="NIFTY 50" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
            <div>
              <label class="form-label">From Date</label>
              <input type="date" id="bt-from" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
            <div>
              <label class="form-label">To Date</label>
              <input type="date" id="bt-to" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
            <div>
              <label class="form-label">Capital (₹)</label>
              <input type="number" id="bt-capital" value="500000" style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
          </div>
          <div style="margin-top:14px">
            <button onclick="BacktestScreen.runBacktest()" class="btn-primary">▶ Run Backtest</button>
          </div>
        </div>
        <div id="bt-results"></div>`;
      // Set default dates
      const now = new Date();
      const toEl = document.getElementById('bt-to');
      const frEl = document.getElementById('bt-from');
      if (toEl) toEl.value = now.toISOString().split('T')[0];
      if (frEl) { const f = new Date(now); f.setMonth(f.getMonth() - 3); frEl.value = f.toISOString().split('T')[0]; }
    },

    _setStrategy(key) {
      if (!this._built) { this._built = true; this._buildLayout(); }
      const sel = document.getElementById('bt-strategy');
      if (sel) { sel.value = key; }
    },

    async runBacktest() {
      const params = {
        strategy: document.getElementById('bt-strategy')?.value,
        symbol:   document.getElementById('bt-symbol')?.value,
        from:     document.getElementById('bt-from')?.value,
        to:       document.getElementById('bt-to')?.value,
        capital:  parseFloat(document.getElementById('bt-capital')?.value || 500000),
      };
      if (!params.strategy || !params.symbol || !params.from || !params.to) {
        UI.toast('Please fill all fields.', 'warning'); return;
      }
      const results = document.getElementById('bt-results');
      if (results) results.innerHTML = '';
      UI.showLoading('bt-results', 'Running backtest…');
      try {
        const data = await API.backtest(params);
        this.renderResults(data);
      } catch (e) {
        UI.showCardError('bt-results', 'Backtest failed: ' + e.message);
      }
    },

    renderResults(data) {
      const el = document.getElementById('bt-results');
      if (!el) return;
      const m = data.metrics || data;
      const tiles = [
        { label: 'Total Trades',     value: m.total_trades || 0,                              color: '#e2e8f0' },
        { label: 'Win Rate',         value: UI.formatPct(m.win_rate),                          color: '#4ade80' },
        { label: 'Net P&L',          value: UI.formatPnL(m.net_pnl),                          color: m.net_pnl >= 0 ? '#4ade80' : '#f87171' },
        { label: 'Max Drawdown',     value: UI.formatPnL(m.max_drawdown),                     color: '#f87171' },
        { label: 'Profit Factor',    value: m.profit_factor ? m.profit_factor.toFixed(2) : '—', color: '#fb923c' },
        { label: 'Avg R-Multiple',   value: m.avg_r ? m.avg_r.toFixed(2) + 'R' : '—',         color: '#a78bfa' },
        { label: 'Sharpe Ratio',     value: m.sharpe ? m.sharpe.toFixed(2) : '—',             color: '#38bdf8' },
        { label: 'Expectancy',       value: m.expectancy ? UI.formatPrice(m.expectancy) : '—', color: '#94a3b8' },
      ];
      const trades = data.trades || [];
      el.innerHTML = `
        <div class="card" style="margin-bottom:16px">
          <h3 style="margin:0 0 14px;font-size:15px">Performance Summary</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px">
            ${tiles.map(t => `<div style="background:#0f172a;border-radius:8px;padding:12px">
              <div style="font-size:11px;color:#64748b;margin-bottom:4px">${t.label}</div>
              <div style="font-size:18px;font-weight:700;color:${t.color}">${t.value}</div>
            </div>`).join('')}
          </div>
        </div>
        ${trades.length ? `<div class="card">
          <h3 style="margin:0 0 12px;font-size:15px">Trade Log (${trades.length})</h3>
          <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:13px">
              <thead><tr style="color:#64748b;text-align:left;border-bottom:1px solid #1e293b">
                <th style="padding:8px">Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>R</th>
              </tr></thead>
              <tbody>
                ${trades.slice(0, 100).map(t => `<tr style="border-bottom:1px solid #0f172a">
                  <td style="padding:8px;color:#94a3b8">${t.date || '—'}</td>
                  <td>${UI.renderDirectionBadge(t.direction)}</td>
                  <td>${UI.formatPrice(t.entry)}</td>
                  <td>${UI.formatPrice(t.exit)}</td>
                  <td>${UI.formatPnL(t.pnl)}</td>
                  <td style="color:${(t.r || 0) >= 0 ? '#4ade80' : '#f87171'}">${t.r ? t.r.toFixed(2) + 'R' : '—'}</td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>` : ''}`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     OPTIONS SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const OptionsScreen = {
    _built: false,

    init() {
      if (!this._built) { this._built = true; this._buildLayout(); }
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="options"]');
      if (!el) return;
      el.innerHTML = `
        <div class="screen-header"><h2>Option Chain</h2></div>
        <div class="card" style="margin-bottom:16px">
          <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap">
            <div>
              <label class="form-label">Symbol</label>
              <select id="opt-symbol" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
                <option>NIFTY</option><option>BANKNIFTY</option><option>FINNIFTY</option><option>MIDCPNIFTY</option>
              </select>
            </div>
            <div>
              <label class="form-label">Expiry</label>
              <input type="date" id="opt-expiry" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
            <button onclick="OptionsScreen.loadChain()" class="btn-primary">Load Chain</button>
          </div>
        </div>
        <div id="opt-chain-container"></div>`;
      // Default expiry = next Thursday
      const expEl = document.getElementById('opt-expiry');
      if (expEl) {
        const d = new Date(); const day = d.getDay();
        d.setDate(d.getDate() + ((4 - day + 7) % 7 || 7));
        expEl.value = d.toISOString().split('T')[0];
      }
    },

    async loadChain() {
      const symbol = document.getElementById('opt-symbol')?.value || 'NIFTY';
      const expiry = document.getElementById('opt-expiry')?.value || '';
      const cont   = document.getElementById('opt-chain-container');
      if (!cont) return;
      cont.innerHTML = '';
      UI.showLoading('opt-chain-container', 'Fetching option chain…');
      try {
        const data = await API.optionChain(symbol, expiry);
        this.renderChain(data);
      } catch (e) {
        UI.showCardError('opt-chain-container', 'Failed to load chain: ' + e.message);
      }
    },

    renderChain(data) {
      const cont = document.getElementById('opt-chain-container');
      if (!cont) return;
      const rows  = data.chain || data.data || data || [];
      const atm   = data.atm_strike || null;
      const maxOI = Math.max(1, ...rows.map(r => Math.max(r.ce_oi || 0, r.pe_oi || 0)));
      cont.innerHTML = `<div class="card" style="overflow-x:auto">
        <div style="display:flex;justify-content:space-between;margin-bottom:12px">
          <h3 style="margin:0;font-size:15px">${data.symbol || 'Option Chain'} — ${data.expiry || ''}</h3>
          <span style="font-size:12px;color:#64748b">Spot: ${data.spot ? data.spot.toFixed(2) : '—'}</span>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:700px">
          <thead><tr style="color:#64748b;text-align:center;border-bottom:1px solid #1e293b">
            <th colspan="4" style="color:#4ade80;padding:6px">CALLS</th>
            <th style="color:#e2e8f0">STRIKE</th>
            <th colspan="4" style="color:#f87171">PUTS</th>
          </tr>
          <tr style="color:#64748b;font-size:11px;border-bottom:1px solid #1e293b">
            <th style="padding:4px 6px;text-align:left">OI</th><th>Chg OI</th><th>IV</th><th>LTP</th>
            <th style="color:#e2e8f0;font-weight:700">Strike</th>
            <th>LTP</th><th>IV</th><th>Chg OI</th><th style="text-align:right">OI</th>
          </tr></thead>
          <tbody>
            ${rows.map(r => {
              const isATM = r.strike === atm;
              const ceOIW = ((r.ce_oi || 0) / maxOI * 100).toFixed(0);
              const peOIW = ((r.pe_oi || 0) / maxOI * 100).toFixed(0);
              return `<tr style="border-bottom:1px solid #0f172a;${isATM ? 'background:#1a2740;' : ''}text-align:center">
                <td style="padding:4px 6px;text-align:left">
                  <div style="display:flex;align-items:center;gap:4px">
                    <div style="width:${ceOIW}px;max-width:80px;height:4px;background:#4ade8066;border-radius:2px"></div>
                    <span>${UI.formatCompact(r.ce_oi)}</span>
                  </div>
                </td>
                <td style="color:${(r.ce_chg_oi||0)>=0?'#4ade80':'#f87171'}">${UI.formatCompact(r.ce_chg_oi)}</td>
                <td style="color:#fb923c">${r.ce_iv ? r.ce_iv.toFixed(1) + '%' : '—'}</td>
                <td style="color:#4ade80;font-weight:600">${r.ce_ltp ? r.ce_ltp.toFixed(2) : '—'}</td>
                <td style="font-weight:700;color:${isATM?'#38bdf8':'#e2e8f0'}">${r.strike}</td>
                <td style="color:#f87171;font-weight:600">${r.pe_ltp ? r.pe_ltp.toFixed(2) : '—'}</td>
                <td style="color:#fb923c">${r.pe_iv ? r.pe_iv.toFixed(1) + '%' : '—'}</td>
                <td style="color:${(r.pe_chg_oi||0)>=0?'#4ade80':'#f87171'}">${UI.formatCompact(r.pe_chg_oi)}</td>
                <td style="text-align:right">
                  <div style="display:flex;align-items:center;justify-content:flex-end;gap:4px">
                    <span>${UI.formatCompact(r.pe_oi)}</span>
                    <div style="width:${peOIW}px;max-width:80px;height:4px;background:#f8717166;border-radius:2px"></div>
                  </div>
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     RISK SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const RiskScreen = {
    _built: false,

    init() {
      if (!this._built) { this._built = true; this._buildLayout(); }
      this.calculate();
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="risk"]');
      if (!el) return;
      const s = Storage.getSettings();
      el.innerHTML = `
        <div class="screen-header"><h2>Risk Calculator</h2></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <div class="card">
            <h3 style="margin:0 0 14px;font-size:15px">Position Sizing</h3>
            <div style="display:flex;flex-direction:column;gap:10px">
              ${[
                ['risk-capital',  'Capital (₹)',         'number', s.capital],
                ['risk-pct',      'Risk per Trade (%)',   'number', s.riskPct],
                ['risk-entry',    'Entry Price (₹)',      'number', ''],
                ['risk-stop',     'Stop Loss Price (₹)', 'number', ''],
                ['risk-target',   'Target Price (₹)',    'number', ''],
                ['risk-lot-size', 'Lot Size',            'number', 50],
              ].map(([id, lbl, type, val]) => `
                <div><label class="form-label">${lbl}</label>
                  <input type="${type}" id="${id}" value="${val}"
                    oninput="RiskScreen.calculate()"
                    style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;
                      color:#e2e8f0;border-radius:6px;padding:8px;font-size:14px">
                </div>`).join('')}
            </div>
          </div>
          <div id="risk-result" class="card"></div>
        </div>`;
    },

    calculate() {
      const get = id => parseFloat(document.getElementById(id)?.value || 0);
      const capital  = get('risk-capital');
      const riskPct  = get('risk-pct');
      const entry    = get('risk-entry');
      const stop     = get('risk-stop');
      const target   = get('risk-target');
      const lotSize  = get('risk-lot-size') || 1;
      const result   = document.getElementById('risk-result');
      if (!result) return;

      if (!capital || !riskPct || !entry || !stop) {
        result.innerHTML = '<p style="color:#64748b;font-size:14px">Fill in the fields to calculate position size.</p>';
        return;
      }
      const riskRs    = (capital * riskPct) / 100;
      const stopPts   = Math.abs(entry - stop);
      const stopPct   = (stopPts / entry) * 100;
      const qtyRaw    = stopPts > 0 ? riskRs / stopPts : 0;
      const lots      = Math.floor(qtyRaw / lotSize);
      const qty       = lots * lotSize;
      const margin    = qty * entry;
      const riskAmt   = qty * stopPts;
      const rewardPts = target && entry ? Math.abs(target - entry) : 0;
      const rrRatio   = rewardPts && stopPts ? rewardPts / stopPts : 0;
      const profitPot = qty * rewardPts;

      const row = (lbl, val, color = '#e2e8f0') =>
        `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e293b">
          <span style="color:#64748b;font-size:13px">${lbl}</span>
          <span style="color:${color};font-weight:600">${val}</span>
        </div>`;

      result.innerHTML = `
        <h3 style="margin:0 0 14px;font-size:15px">Result</h3>
        ${row('Risk Amount',          UI.formatPrice(riskAmt),             '#fb923c')}
        ${row('Stop Loss Points',     `${stopPts.toFixed(2)} (${stopPct.toFixed(2)}%)`, '#f87171')}
        ${row('Suggested Qty',        qty,                                 '#38bdf8')}
        ${row('Lots',                 lots,                                '#38bdf8')}
        ${row('Margin Required',      UI.formatPrice(margin),              '#94a3b8')}
        ${rrRatio ? row('R:R Ratio', `1 : ${rrRatio.toFixed(2)}`,         rrRatio >= 2 ? '#4ade80' : '#fb923c') : ''}
        ${profitPot ? row('Profit Potential', UI.formatPrice(profitPot),  '#4ade80') : ''}
        <div style="margin-top:14px;padding:12px;border-radius:6px;background:${riskAmt <= riskRs ? '#14532d22' : '#450a0a22'};
          border:1px solid ${riskAmt <= riskRs ? '#4ade8044' : '#f8717144'}">
          <div style="font-size:12px;color:#64748b">Max allowed risk</div>
          <div style="font-weight:700;color:${riskAmt <= riskRs ? '#4ade80' : '#f87171'}">${UI.formatPrice(riskRs)}</div>
        </div>`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     REPLAY SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const ReplayScreen = {
    _session: null, _bars: [], _idx: 0, _playing: false, _timer: null, _speed: 1,
    _built: false,

    init() {
      if (!this._built) { this._built = true; this._buildLayout(); }
    },

    _buildLayout() {
      const el = document.querySelector('[data-screen="replay"]');
      if (!el) return;
      el.innerHTML = `
        <div class="screen-header"><h2>Session Replay</h2></div>
        <div class="card" style="margin-bottom:16px">
          <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap">
            <div><label class="form-label">Date</label>
              <input type="date" id="replay-date" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
            </div>
            <div><label class="form-label">Symbol</label>
              <select id="replay-symbol" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
                <option>NIFTY 50</option><option>NIFTY BANK</option><option>FINNIFTY</option>
              </select>
            </div>
            <button onclick="ReplayScreen.loadSession()" class="btn-primary">Load Session</button>
          </div>
        </div>
        <div id="replay-controls" class="card" style="display:none;margin-bottom:16px">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <button id="replay-play-btn" onclick="ReplayScreen.togglePlay()" class="btn-primary">▶ Play</button>
            <button onclick="ReplayScreen.step()" class="btn-secondary">⟶ Step</button>
            <label style="font-size:13px;color:#64748b">Speed:
              <select id="replay-speed" onchange="ReplayScreen._setSpeed()" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:4px;padding:3px">
                <option value="1">1×</option><option value="2">2×</option>
                <option value="5">5×</option><option value="10">10×</option>
              </select>
            </label>
            <span id="replay-progress" style="font-size:13px;color:#64748b"></span>
          </div>
        </div>
        <div id="replay-display" class="card"></div>`;
      const d = new Date(); d.setDate(d.getDate() - 1);
      const el2 = document.getElementById('replay-date');
      if (el2) el2.value = d.toISOString().split('T')[0];
    },

    async loadSession() {
      const date   = document.getElementById('replay-date')?.value;
      const symbol = document.getElementById('replay-symbol')?.value || 'NIFTY 50';
      UI.showLoading('replay-display', 'Loading session data…');
      try {
        const data    = await API.replaySession(date, symbol);
        this._bars    = data.bars || data.candles || [];
        this._idx     = 0;
        this._playing = false;
        if (this._timer) clearInterval(this._timer);
        document.getElementById('replay-controls').style.display = this._bars.length ? '' : 'none';
        this._renderBar();
      } catch (e) {
        UI.showCardError('replay-display', 'Failed to load session: ' + e.message);
      }
    },

    _renderBar() {
      const el = document.getElementById('replay-display');
      const prog = document.getElementById('replay-progress');
      if (!this._bars.length) { UI.showEmpty(el, 'No bar data.', '📊'); return; }
      const bar = this._bars[this._idx];
      if (prog) prog.textContent = `Bar ${this._idx + 1} / ${this._bars.length} — ${bar.time || bar.timestamp || ''}`;
      el.innerHTML = `
        <h3 style="margin:0 0 12px">Bar ${this._idx + 1} of ${this._bars.length}</h3>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px">
          ${[['Time', bar.time || bar.timestamp || '—'],['Open', UI.formatPrice(bar.open)],
             ['High', UI.formatPrice(bar.high)],['Low', UI.formatPrice(bar.low)],
             ['Close', UI.formatPrice(bar.close)],['Volume', UI.formatCompact(bar.volume)],
             ['VWAP', UI.formatPrice(bar.vwap)],['OI', UI.formatCompact(bar.oi)]].map(([l, v]) =>
            `<div style="background:#0f172a;border-radius:6px;padding:10px">
              <div style="font-size:11px;color:#64748b">${l}</div>
              <div style="font-weight:600;color:#e2e8f0;font-size:15px">${v}</div>
            </div>`).join('')}
        </div>`;
    },

    step() {
      if (this._idx < this._bars.length - 1) { this._idx++; this._renderBar(); }
      else { this._playing = false; clearInterval(this._timer); }
    },

    togglePlay() {
      this._playing = !this._playing;
      const btn = document.getElementById('replay-play-btn');
      if (this._playing) {
        if (btn) btn.textContent = '⏸ Pause';
        this._timer = setInterval(() => this.step(), Math.max(100, 1000 / this._speed));
      } else {
        if (btn) btn.textContent = '▶ Play';
        clearInterval(this._timer);
      }
    },

    _setSpeed() {
      this._speed = parseInt(document.getElementById('replay-speed')?.value || 1, 10);
      if (this._playing) { clearInterval(this._timer); this._timer = setInterval(() => this.step(), Math.max(100, 1000 / this._speed)); }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     HEATMAP SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const HeatmapScreen = {
    async init() {
      const el = document.querySelector('[data-screen="heatmap"]');
      if (!el) return;
      el.innerHTML = `<div class="screen-header"><h2>Market Heatmap</h2><button onclick="HeatmapScreen.init()" class="btn-secondary">↻ Refresh</button></div>
        <div id="heatmap-container" class="card"></div>`;
      UI.showLoading('heatmap-container', 'Loading heatmap…');
      try {
        const data = await API.heatmap();
        this.render(data);
      } catch (e) {
        UI.showCardError('heatmap-container', 'Heatmap unavailable: ' + e.message);
      }
    },

    render(data) {
      const el = document.getElementById('heatmap-container');
      if (!el) return;
      const items = Array.isArray(data) ? data : (data.sectors || data.stocks || []);
      if (!items.length) { UI.showEmpty(el, 'No heatmap data.', '🗺'); return; }
      el.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:6px;">
        ${items.map(item => {
          const chg   = item.change_pct || item.change || 0;
          const abs   = Math.min(Math.abs(chg), 5);
          const alpha = 0.2 + (abs / 5) * 0.7;
          const bg    = chg >= 0 ? `rgba(74,222,128,${alpha})` : `rgba(248,113,113,${alpha})`;
          const size  = Math.max(60, Math.min(160, 60 + (item.weight || 1) * 20));
          return `<div style="width:${size}px;height:${size}px;background:${bg};border-radius:6px;
            display:flex;flex-direction:column;align-items:center;justify-content:center;
            font-size:11px;padding:4px;text-align:center;border:1px solid ${chg>=0?'#4ade8044':'#f8717144'}">
            <span style="font-weight:700;color:#e2e8f0;font-size:12px">${item.symbol || item.name || '—'}</span>
            <span style="color:${chg>=0?'#4ade80':'#f87171'};font-weight:600">${chg>=0?'+':''}${chg.toFixed(2)}%</span>
          </div>`;
        }).join('')}
      </div>`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     JOURNAL SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const JournalScreen = {
    _sortCol: 'createdAt', _sortAsc: false, _filter: '',

    init() {
      const el = document.querySelector('[data-screen="journal"]');
      if (!el) return;
      el.innerHTML = `
        <div class="screen-header">
          <h2>Trade Journal</h2>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button onclick="JournalScreen.openAddForm()" class="btn-primary">+ Add Trade</button>
            <button onclick="Storage.exportJournalCSV(Storage.getJournalTrades())" class="btn-secondary">↓ Export CSV</button>
            <button onclick="JournalScreen.importCSV()" class="btn-secondary">↑ Import CSV</button>
          </div>
        </div>
        <div class="card" style="margin-bottom:16px">
          <input id="journal-filter" placeholder="Filter by symbol, strategy, date…"
            oninput="JournalScreen._filter=this.value;JournalScreen.renderTradesTable()"
            style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;
              color:#e2e8f0;border-radius:6px;padding:8px;font-size:14px">
        </div>
        <div id="journal-table-container" class="card"></div>`;
      this.renderTradesTable();
    },

    openAddForm(tradeToEdit) {
      const isEdit = !!tradeToEdit;
      const t      = tradeToEdit || {};
      UI.showModal(isEdit ? 'Edit Trade' : 'Add Trade', `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          ${[
            ['journal-date',      'Date',         'date',   t.date || new Date().toISOString().split('T')[0]],
            ['journal-symbol',    'Symbol',       'text',   t.symbol || 'NIFTY 50'],
            ['journal-entry',     'Entry (₹)',    'number', t.entry || ''],
            ['journal-exit',      'Exit (₹)',     'number', t.exit || ''],
            ['journal-qty',       'Qty',          'number', t.qty || ''],
            ['journal-charges',   'Charges (₹)',  'number', t.charges || 0],
          ].map(([id, lbl, type, val]) => `
            <div><label class="form-label">${lbl}</label>
              <input type="${type}" id="${id}" value="${val}"
                style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;
                  color:#e2e8f0;border-radius:6px;padding:7px;font-size:13px">
            </div>`).join('')}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
          <div><label class="form-label">Direction</label>
            <select id="journal-direction" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
              <option ${t.direction==='BUY'?'selected':''}>BUY</option>
              <option ${t.direction==='SELL'?'selected':''}>SELL</option>
            </select>
          </div>
          <div><label class="form-label">Strategy</label>
            <select id="journal-strategy" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
              ${Object.entries(STRATEGIES).map(([k, v]) => `<option value="${k}" ${t.strategy===k?'selected':''}>${v.label}</option>`).join('')}
            </select>
          </div>
        </div>
        <div style="margin-top:10px"><label class="form-label">Notes</label>
          <textarea id="journal-notes" style="width:100%;box-sizing:border-box;height:70px;background:#0f172a;
            border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:7px;font-size:13px;resize:vertical">${t.notes || ''}</textarea>
        </div>`,
        [
          { label: 'Cancel', action: () => UI.closeModal() },
          {
            label: isEdit ? 'Save Changes' : 'Add Trade',
            style: 'background:#3b82f6;color:#fff;',
            action: () => {
              const entry    = parseFloat(document.getElementById('journal-entry')?.value || 0);
              const exit     = parseFloat(document.getElementById('journal-exit')?.value || 0);
              const qty      = parseFloat(document.getElementById('journal-qty')?.value || 0);
              const charges  = parseFloat(document.getElementById('journal-charges')?.value || 0);
              const dir      = document.getElementById('journal-direction')?.value || 'BUY';
              const pnl      = dir === 'BUY' ? (exit - entry) * qty - charges : (entry - exit) * qty - charges;
              const trade    = {
                date:      document.getElementById('journal-date')?.value,
                symbol:    document.getElementById('journal-symbol')?.value,
                direction: dir,
                strategy:  document.getElementById('journal-strategy')?.value,
                entry, exit, qty, charges,
                pnl: parseFloat(pnl.toFixed(2)),
                notes: document.getElementById('journal-notes')?.value,
              };
              if (isEdit) { Storage.updateJournalTrade(t.id, trade); }
              else { Storage.addJournalTrade(trade); Storage.addTodayPnL(pnl); LossLock.check(); }
              UI.closeModal();
              JournalScreen.renderTradesTable();
              UI.toast(isEdit ? 'Trade updated.' : 'Trade added.', 'success');
            },
          },
        ]
      );
    },

    renderTradesTable() {
      const el = document.getElementById('journal-table-container');
      if (!el) return;
      let trades = Storage.getJournalTrades();
      if (this._filter) {
        const q = this._filter.toLowerCase();
        trades = trades.filter(t =>
          (t.symbol || '').toLowerCase().includes(q) ||
          (t.strategy || '').toLowerCase().includes(q) ||
          (t.date || '').includes(q)
        );
      }
      trades = trades.slice().sort((a, b) => {
        const av = a[this._sortCol] ?? 0, bv = b[this._sortCol] ?? 0;
        return this._sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
      });
      if (!trades.length) { UI.showEmpty(el, 'No trades found.', '📒'); return; }
      const th = (col, lbl) => {
        const active = this._sortCol === col;
        return `<th style="padding:8px;cursor:pointer;${active?'color:#38bdf8':''}" onclick="JournalScreen._sortBy('${col}')">${lbl}${active?(this._sortAsc?'▲':'▼'):''}</th>`;
      };
      const totalPnL = trades.reduce((s, t) => s + (t.pnl || 0), 0);
      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;margin-bottom:12px;align-items:center">
          <span style="font-size:13px;color:#64748b">${trades.length} trade${trades.length!==1?'s':''}</span>
          <span style="font-weight:700">${UI.formatPnL(totalPnL)}</span>
        </div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="color:#64748b;text-align:left;border-bottom:1px solid #1e293b">
              ${th('date','Date')}${th('symbol','Symbol')}${th('direction','Dir')}
              ${th('strategy','Strategy')}${th('entry','Entry')}${th('exit','Exit')}
              ${th('qty','Qty')}${th('pnl','P&L')}<th style="padding:8px">Actions</th>
            </tr></thead>
            <tbody>
              ${trades.map(t => `<tr style="border-bottom:1px solid #0f172a">
                <td style="padding:8px;color:#94a3b8">${t.date || '—'}</td>
                <td style="font-weight:600;color:#e2e8f0">${t.symbol || '—'}</td>
                <td>${UI.renderDirectionBadge(t.direction)}</td>
                <td style="color:#94a3b8;font-size:12px">${STRATEGIES[t.strategy]?.label || t.strategy || '—'}</td>
                <td>${UI.formatPrice(t.entry)}</td>
                <td>${UI.formatPrice(t.exit)}</td>
                <td style="color:#e2e8f0">${t.qty || 0}</td>
                <td>${UI.formatPnL(t.pnl)}</td>
                <td>
                  <button onclick="JournalScreen.openAddForm(${JSON.stringify(t).replace(/"/g,'&quot;')})"
                    style="background:none;border:1px solid #334155;color:#94a3b8;border-radius:4px;
                      padding:2px 8px;cursor:pointer;font-size:11px;margin-right:4px">Edit</button>
                  <button onclick="JournalScreen._deleteTrade(${t.id})"
                    style="background:none;border:1px solid #450a0a;color:#f87171;border-radius:4px;
                      padding:2px 8px;cursor:pointer;font-size:11px">Del</button>
                </td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    },

    _sortBy(col) {
      if (this._sortCol === col) { this._sortAsc = !this._sortAsc; }
      else { this._sortCol = col; this._sortAsc = false; }
      this.renderTradesTable();
    },

    async _deleteTrade(id) {
      const ok = await UI.confirm('Delete this trade? This cannot be undone.');
      if (!ok) return;
      Storage.deleteJournalTrade(id);
      this.renderTradesTable();
      UI.toast('Trade deleted.', 'info');
    },

    importCSV() {
      const input = document.createElement('input');
      input.type  = 'file';
      input.accept = '.csv,text/csv';
      input.onchange = (e) => {
        const file   = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
          const result = Storage.importJournalCSV(ev.target.result);
          UI.toast(`Imported ${result.imported} trade${result.imported !== 1 ? 's' : ''}.`, 'success');
          JournalScreen.renderTradesTable();
        };
        reader.readAsText(file);
      };
      input.click();
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     ANALYTICS SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const AnalyticsScreen = {
    async init() {
      const el = document.querySelector('[data-screen="analytics"]');
      if (!el) return;
      el.innerHTML = `<div class="screen-header"><h2>Analytics</h2></div><div id="analytics-body"></div>`;
      await this.refresh();
    },

    async refresh() {
      const trades = Storage.getJournalTrades();
      const body   = document.getElementById('analytics-body');
      if (!body) return;
      if (!trades.length) { UI.showEmpty(body, 'Add trades to your journal to see analytics.', '📈'); return; }
      let analytics;
      try { analytics = await API.analyzeJournal(trades); }
      catch (_) { analytics = this._computeLocally(trades); }
      this.renderAll(analytics, trades);
    },

    _computeLocally(trades) {
      const wins    = trades.filter(t => (t.pnl || 0) > 0);
      const losses  = trades.filter(t => (t.pnl || 0) < 0);
      const totalPnL = trades.reduce((s, t) => s + (t.pnl || 0), 0);
      const grossW  = wins.reduce((s, t) => s + (t.pnl || 0), 0);
      const grossL  = Math.abs(losses.reduce((s, t) => s + (t.pnl || 0), 0));
      const byStrat = {};
      trades.forEach(t => {
        if (!byStrat[t.strategy]) byStrat[t.strategy] = { count: 0, pnl: 0, wins: 0 };
        byStrat[t.strategy].count++;
        byStrat[t.strategy].pnl += (t.pnl || 0);
        if ((t.pnl || 0) > 0) byStrat[t.strategy].wins++;
      });
      return {
        total_trades:  trades.length,
        wins:          wins.length,
        losses:        losses.length,
        win_rate:      trades.length ? (wins.length / trades.length) * 100 : 0,
        net_pnl:       totalPnL,
        avg_win:       wins.length ? grossW / wins.length : 0,
        avg_loss:      losses.length ? -(grossL / losses.length) : 0,
        profit_factor: grossL > 0 ? grossW / grossL : grossW > 0 ? Infinity : 0,
        by_strategy:   byStrat,
      };
    },

    renderAll(a, trades) {
      const body = document.getElementById('analytics-body');
      if (!body) return;
      const tiles = [
        ['Total Trades', a.total_trades, '#e2e8f0'],
        ['Win Rate',     UI.formatPct(a.win_rate), a.win_rate >= 50 ? '#4ade80' : '#f87171'],
        ['Net P&L',      UI.formatPnL(a.net_pnl), ''],
        ['Avg Win',      UI.formatPrice(a.avg_win), '#4ade80'],
        ['Avg Loss',     UI.formatPrice(Math.abs(a.avg_loss || 0)), '#f87171'],
        ['Profit Factor',a.profit_factor === Infinity ? '∞' : (a.profit_factor || 0).toFixed(2), '#fb923c'],
      ];
      const byStrat = a.by_strategy || {};
      body.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px;margin-bottom:16px">
          ${tiles.map(([lbl, val, col]) => `<div class="card">
            <div style="font-size:11px;color:#64748b;margin-bottom:4px">${lbl}</div>
            <div style="font-size:18px;font-weight:700;color:${col||'#e2e8f0'}">${val}</div>
          </div>`).join('')}
        </div>
        ${Object.keys(byStrat).length ? `<div class="card" style="margin-bottom:16px">
          <h3 style="margin:0 0 12px;font-size:15px">Performance by Strategy</h3>
          <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="color:#64748b;text-align:left;border-bottom:1px solid #1e293b">
              <th style="padding:8px">Strategy</th><th>Trades</th><th>Win Rate</th><th>Net P&L</th>
            </tr></thead>
            <tbody>
              ${Object.entries(byStrat).map(([k, v]) => `<tr style="border-bottom:1px solid #0f172a">
                <td style="padding:8px;color:#e2e8f0">${STRATEGIES[k]?.label || k}</td>
                <td style="color:#94a3b8">${v.count}</td>
                <td style="color:${v.count>0&&(v.wins/v.count)>=0.5?'#4ade80':'#f87171'}">${v.count>0?((v.wins/v.count)*100).toFixed(1)+'%':'—'}</td>
                <td>${UI.formatPnL(v.pnl)}</td>
              </tr>`).join('')}
            </tbody>
          </table></div>
        </div>` : ''}
        <div class="card">
          <h3 style="margin:0 0 12px;font-size:15px">Cumulative P&L</h3>
          <div id="analytics-equity-chart" style="height:160px;position:relative;overflow:hidden">
            ${this._renderMiniEquity(trades)}
          </div>
        </div>`;
    },

    _renderMiniEquity(trades) {
      const sorted = trades.slice().sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
      let cum = 0;
      const points = sorted.map(t => { cum += (t.pnl || 0); return cum; });
      if (points.length < 2) return '<p style="color:#64748b;font-size:13px;padding:16px">Need more trades for chart.</p>';
      const min = Math.min(0, ...points), max = Math.max(1, ...points);
      const range = max - min || 1;
      const w = 600, h = 140;
      const xs = points.map((_, i) => (i / (points.length - 1)) * w);
      const ys = points.map(p => h - ((p - min) / range) * h);
      const path = xs.map((x, i) => `${i===0?'M':'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
      const lastY = ys[ys.length - 1];
      const fillPath = `${path} L${w},${h} L0,${h} Z`;
      const color = points[points.length - 1] >= 0 ? '#4ade80' : '#f87171';
      return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:100%;display:block" preserveAspectRatio="none">
        <defs><linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.3"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0.0"/>
        </linearGradient></defs>
        <path d="${fillPath}" fill="url(#eq-fill)"/>
        <path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>
        <line x1="0" y1="${h - ((0 - min) / range) * h}" x2="${w}" y2="${h - ((0 - min) / range) * h}"
          stroke="#334155" stroke-width="1" stroke-dasharray="4,4"/>
      </svg>`;
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     SETTINGS SCREEN
  ═══════════════════════════════════════════════════════════════════════ */

  const SettingsScreen = {
    init() {
      const el = document.querySelector('[data-screen="settings"]');
      if (!el) return;
      const s = Storage.getSettings();
      el.innerHTML = `
        <div class="screen-header"><h2>Settings</h2></div>
        <div class="card" style="max-width:520px">
          <h3 style="margin:0 0 16px;font-size:15px">General</h3>
          <div style="display:flex;flex-direction:column;gap:12px">
            ${[
              ['set-api-base',    'API Base URL',               'text',   s.apiBase],
              ['set-capital',     'Capital (₹)',                'number', s.capital],
              ['set-risk-pct',    'Risk per Trade (%)',          'number', s.riskPct],
              ['set-loss-limit',  'Daily Loss Limit (%)',        'number', s.dailyLossLimitPct],
              ['set-interval',    'Auto-Refresh Interval (sec)', 'number', s.autoRefreshInterval],
            ].map(([id, lbl, type, val]) => `
              <div><label class="form-label">${lbl}</label>
                <input type="${type}" id="${id}" value="${val}"
                  style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;
                    color:#e2e8f0;border-radius:6px;padding:8px;font-size:14px">
              </div>`).join('')}
            <div style="display:flex;align-items:center;gap:10px">
              <input type="checkbox" id="set-paper-mode" ${s.paperMode?'checked':''} style="width:16px;height:16px">
              <label for="set-paper-mode" class="form-label" style="margin:0">Paper Trading Mode</label>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
              <input type="checkbox" id="set-alert-signals" ${s.alertSignals?'checked':''} style="width:16px;height:16px">
              <label for="set-alert-signals" class="form-label" style="margin:0">Alert on new signals</label>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
              <input type="checkbox" id="set-alert-regime" ${s.alertRegimeChange?'checked':''} style="width:16px;height:16px">
              <label for="set-alert-regime" class="form-label" style="margin:0">Alert on regime change</label>
            </div>
            <div style="display:flex;align-items:center;gap:10px">
              <input type="checkbox" id="set-alert-vix" ${s.alertVolatilitySpike?'checked':''} style="width:16px;height:16px">
              <label for="set-alert-vix" class="form-label" style="margin:0">Alert on VIX spike</label>
            </div>
            <div>
              <label class="form-label">Default Strategy</label>
              <select id="set-default-strategy" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:8px">
                ${Object.entries(STRATEGIES).map(([k, v]) => `<option value="${k}" ${s.defaultStrategy===k?'selected':''}>${v.label}</option>`).join('')}
              </select>
            </div>
          </div>
          <div style="display:flex;gap:10px;margin-top:20px">
            <button onclick="SettingsScreen.save()" class="btn-primary">Save Settings</button>
            <button onclick="SettingsScreen.testConnection()" class="btn-secondary">Test Connection</button>
          </div>
          <div id="set-conn-result" style="margin-top:12px"></div>
        </div>`;
    },

    save() {
      const settings = {
        apiBase:              document.getElementById('set-api-base')?.value?.trim(),
        capital:              parseFloat(document.getElementById('set-capital')?.value || 500000),
        riskPct:              parseFloat(document.getElementById('set-risk-pct')?.value || 2),
        dailyLossLimitPct:    parseFloat(document.getElementById('set-loss-limit')?.value || 3),
        autoRefreshInterval:  parseInt(document.getElementById('set-interval')?.value || 60, 10),
        paperMode:            document.getElementById('set-paper-mode')?.checked || false,
        alertSignals:         document.getElementById('set-alert-signals')?.checked || false,
        alertRegimeChange:    document.getElementById('set-alert-regime')?.checked || false,
        alertVolatilitySpike: document.getElementById('set-alert-vix')?.checked || false,
        defaultStrategy:      document.getElementById('set-default-strategy')?.value,
      };
      Storage.saveSettings(settings);
      UI.toast('Settings saved.', 'success');
      _applyPaperModeBadge(settings.paperMode);
    },

    async testConnection() {
      const resultEl = document.getElementById('set-conn-result');
      if (resultEl) resultEl.innerHTML = '<span style="color:#64748b;font-size:13px">Testing…</span>';
      try {
        const data = await API.health();
        if (resultEl) resultEl.innerHTML = `<span style="color:#4ade80;font-size:13px">✓ Connected — ${JSON.stringify(data)}</span>`;
      } catch (e) {
        if (resultEl) resultEl.innerHTML = `<span style="color:#f87171;font-size:13px">✕ ${e.message}</span>`;
      }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     AUTO-REFRESH
  ═══════════════════════════════════════════════════════════════════════ */

  const AutoRefresh = {
    _timer: null,

    isMarketHours() {
      const now = new Date();
      const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
      const day = ist.getDay(); // 0=Sun, 6=Sat
      if (day === 0 || day === 6) return false;
      const hh = ist.getHours(), mm = ist.getMinutes();
      const mins = hh * 60 + mm;
      return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 45;
    },

    start() {
      this.stop();
      const sec = Storage.getSettings().autoRefreshInterval || 60;
      this._timer = setInterval(() => {
        if (!this.isMarketHours()) return;
        if (Router.current === 'dashboard') DashboardScreen.refresh();
        LossLock.check();
      }, sec * 1000);
    },

    stop() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     WEBSOCKET ALERTS
  ═══════════════════════════════════════════════════════════════════════ */

  const AlertWS = {
    ws:             null,
    _stopped:       false,
    _usingPoll:     false,   // true when WS fails and we fall back to polling
    _pollTimer:     null,
    _lastAlertTs:   null,
    _reconnectMs:   3000,
    _currentDelay:  3000,
    _maxReconnect:  30000,
    _wsFailCount:   0,

    connect() {
      this._stopped = false;
      if (_isVercel) {
        // On Vercel, WebSocket is not supported — go straight to polling
        this._startPolling();
        return;
      }
      const base = API.base().replace(/^http/, 'ws');
      try {
        this.ws = new WebSocket(`${base}/api/alerts/ws`);
        this.ws.onopen    = () => { this._wsFailCount = 0; this._currentDelay = this._reconnectMs; };
        this.ws.onmessage = (ev) => {
          try { this.onMessage(JSON.parse(ev.data)); }
          catch (_) { this.onMessage({ type: 'info', message: ev.data }); }
        };
        this.ws.onclose = () => {
          if (this._stopped) return;
          this._wsFailCount++;
          // After 3 WS failures, fall back silently to polling
          if (this._wsFailCount >= 3) { this._startPolling(); return; }
          this.reconnect();
        };
        this.ws.onerror = () => { /* onclose will fire */ };
      } catch (_) { this._startPolling(); }
    },

    _startPolling() {
      if (this._usingPoll || this._stopped) return;
      this._usingPoll = true;
      this._poll();
      this._pollTimer = setInterval(() => this._poll(), 30000);  // every 30 s
    },

    async _poll() {
      if (this._stopped) return;
      try {
        const params = this._lastAlertTs ? `?since=${encodeURIComponent(this._lastAlertTs)}` : '';
        const data   = await API.get(`/api/alerts/poll${params}`);
        if (data && data.alerts && data.alerts.length) {
          data.alerts.forEach(a => this.onMessage(a));
          this._lastAlertTs = data.timestamp;
        }
      } catch (_) { /* silent — no alerts is fine */ }
    },

    onMessage(msg) {
      const type      = msg.type || 'info';
      const message   = msg.message || msg.title || msg.text || JSON.stringify(msg);
      const toastType = { signal: 'signal', regime_change: 'warning', vix_spike: 'warning', error: 'error' }[type] || 'info';
      const settings  = Storage.getSettings();
      if (type === 'signal'        && !settings.alertSignals)       return;
      if (type === 'regime_change' && !settings.alertRegimeChange)  return;
      if (type === 'vix_spike'     && !settings.alertVolatilitySpike) return;
      if (type === 'heartbeat' || type === 'welcome' || type === 'pong') return;
      UI.toast(message, toastType, 6000);
      Storage.addAlert({ type, message, raw: msg });
    },

    reconnect() {
      setTimeout(() => { if (!this._stopped) this.connect(); }, this._currentDelay);
      this._currentDelay = Math.min(this._currentDelay * 1.5, this._maxReconnect);
    },

    disconnect() {
      this._stopped = true;
      if (this.ws)         { this.ws.close(); this.ws = null; }
      if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     LOSS LOCK
  ═══════════════════════════════════════════════════════════════════════ */

  const LossLock = {
    _dismissed: false,

    check() {
      if (this._dismissed) return;
      const settings   = Storage.getSettings();
      const todayPnL   = Storage.getTodayPnL();
      const limitRs    = (settings.capital * settings.dailyLossLimitPct) / 100;
      if (todayPnL <= -limitRs) {
        UI.showLossLock(todayPnL, limitRs);
      }
    },

    dismiss() {
      this._dismissed = true;
      const overlay   = document.getElementById('bt-loss-lock-overlay');
      if (overlay) overlay.style.display = 'none';
      UI.toast('Loss lock dismissed. Trade carefully.', 'warning', 8000);
    },
  };

  /* ═══════════════════════════════════════════════════════════════════════
     DAILY RESET
  ═══════════════════════════════════════════════════════════════════════ */

  function _checkDailyReset() {
    // getTodayPnL() internally auto-resets if date changed; call it to trigger
    Storage.getTodayPnL();
    // Schedule a midnight IST reset
    const now  = new Date();
    const ist  = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const msToMidnight = (
      (23 - ist.getHours()) * 3600000 +
      (59 - ist.getMinutes()) * 60000 +
      (60 - ist.getSeconds()) * 1000
    );
    setTimeout(() => { Storage.resetTodayPnL(); LossLock._dismissed = false; _checkDailyReset(); }, msToMidnight);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     PAPER MODE BADGE
  ═══════════════════════════════════════════════════════════════════════ */

  function _applyPaperModeBadge(isPaper) {
    let badge = document.getElementById('bt-paper-badge');
    if (isPaper) {
      if (!badge) {
        badge           = document.createElement('div');
        badge.id        = 'bt-paper-badge';
        badge.style.cssText = [
          'position:fixed', 'top:0', 'left:50%', 'transform:translateX(-50%)',
          'background:#f59e0b', 'color:#000', 'font-size:11px', 'font-weight:700',
          'padding:2px 16px', 'z-index:9999', 'letter-spacing:1px',
          'border-radius:0 0 6px 6px',
        ].join(';');
        badge.textContent = 'PAPER TRADING MODE';
        document.body.appendChild(badge);
      }
    } else if (badge) {
      badge.remove();
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
     GLOBAL CSS INJECTION (minimal base styles if no external CSS)
  ═══════════════════════════════════════════════════════════════════════ */

  function _injectBaseStyles() {
    if (document.getElementById('bt-base-styles')) return;
    const s = document.createElement('style');
    s.id    = 'bt-base-styles';
    s.textContent = `
      .card { background:#1e293b; border:1px solid #334155; border-radius:10px; padding:16px; }
      .screen-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:8px; }
      .screen-header h2 { margin:0; font-size:20px; color:#e2e8f0; }
      .form-label { display:block; font-size:12px; color:#64748b; margin-bottom:4px; font-weight:500; }
      .btn-primary { background:#3b82f6; color:#fff; border:none; border-radius:6px; padding:8px 18px;
        font-size:14px; font-weight:600; cursor:pointer; }
      .btn-primary:hover { background:#2563eb; }
      .btn-secondary { background:#1e293b; color:#e2e8f0; border:1px solid #334155; border-radius:6px;
        padding:8px 16px; font-size:14px; cursor:pointer; }
      .btn-secondary:hover { background:#334155; }
      .pnl-positive { color:#4ade80; font-weight:600; }
      .pnl-negative { color:#f87171; font-weight:600; }
      .nav-active { color:#38bdf8 !important; }
    `;
    document.head.appendChild(s);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     INITIALIZATION
  ═══════════════════════════════════════════════════════════════════════ */

  function _init() {
    _injectBaseStyles();
    _checkDailyReset();
    const settings = Storage.getSettings();
    _applyPaperModeBadge(settings.paperMode);
    Router.init();
    if (AutoRefresh.isMarketHours()) AutoRefresh.start();
    AlertWS.connect();
    LossLock.check();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }

  /* ═══════════════════════════════════════════════════════════════════════
     PUBLIC API
  ═══════════════════════════════════════════════════════════════════════ */

  window.APP = {
    navigate:        (hash) => Router.navigate(hash),
    dismissLossLock: ()     => LossLock.dismiss(),
    openAddTrade:    ()     => JournalScreen.openAddForm(),
    scanNow:         ()     => { Router.navigate('scanner'); ScannerScreen.scan(); },
    API,
    // Expose screens for onclick= attributes
    DashboardScreen,
    ScannerScreen,
    BacktestScreen,
    OptionsScreen,
    RiskScreen,
    ReplayScreen,
    HeatmapScreen,
    JournalScreen,
    AnalyticsScreen,
    SettingsScreen,
  };

  // Also expose individual screens globally for onclick= convenience
  window.DashboardScreen  = DashboardScreen;
  window.ScannerScreen    = ScannerScreen;
  window.SignalsScreen    = SignalsScreen;
  window.StrategyLabScreen = StrategyLabScreen;
  window.BacktestScreen   = BacktestScreen;
  window.OptionsScreen    = OptionsScreen;
  window.RiskScreen       = RiskScreen;
  window.ReplayScreen     = ReplayScreen;
  window.HeatmapScreen    = HeatmapScreen;
  window.JournalScreen    = JournalScreen;
  window.AnalyticsScreen  = AnalyticsScreen;
  window.SettingsScreen   = SettingsScreen;
})();
