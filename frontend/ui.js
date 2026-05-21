/**
 * ui.js — UI utility module for Braineetrades cockpit
 * Exposed as window.UI
 * Depends on: storage.js (window.Storage)
 */
(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════════════════════════
     REGIME CONFIG
  ═══════════════════════════════════════════════════════════════════════ */

  const REGIME_META = {
    trending_up:        { color: '#00c853', icon: '↑', label: 'Trending Up' },
    trending_down:      { color: '#f44336', icon: '↓', label: 'Trending Down' },
    ranging:            { color: '#ff9800', icon: '↔', label: 'Ranging' },
    breakout:           { color: '#2196f3', icon: '⚡', label: 'Breakout' },
    reversal:           { color: '#9c27b0', icon: '↩', label: 'Reversal' },
    volatile:           { color: '#ff5722', icon: '⚠', label: 'Volatile' },
    choppy:             { color: '#795548', icon: '~', label: 'Choppy' },
    gap_up:             { color: '#00bcd4', icon: '▲', label: 'Gap Up' },
    gap_down:           { color: '#e91e63', icon: '▼', label: 'Gap Down' },
    neutral:            { color: '#9e9e9e', icon: '—', label: 'Neutral' },
    unknown:            { color: '#616161', icon: '?', label: 'Unknown' },
  };

  function regimeMeta(regime) {
    const key = (regime || 'unknown').toLowerCase().replace(/ /g, '_');
    return REGIME_META[key] || REGIME_META.unknown;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     TOAST SYSTEM
  ═══════════════════════════════════════════════════════════════════════ */

  let toastContainer = null;

  function ensureToastContainer() {
    if (toastContainer && document.body.contains(toastContainer)) return toastContainer;
    toastContainer = document.createElement('div');
    toastContainer.id = 'bt-toast-container';
    toastContainer.style.cssText = [
      'position:fixed', 'bottom:24px', 'right:24px', 'z-index:99999',
      'display:flex', 'flex-direction:column', 'gap:8px',
      'pointer-events:none', 'max-width:360px',
    ].join(';');
    document.body.appendChild(toastContainer);
    return toastContainer;
  }

  const TOAST_COLORS = {
    info:    { bg: '#1e293b', border: '#38bdf8', icon: 'ℹ' },
    success: { bg: '#14532d', border: '#4ade80', icon: '✓' },
    warning: { bg: '#451a03', border: '#fb923c', icon: '⚠' },
    error:   { bg: '#450a0a', border: '#f87171', icon: '✕' },
    signal:  { bg: '#1e1b4b', border: '#a78bfa', icon: '⚡' },
  };

  function toast(message, type = 'info', duration = 4000) {
    const container = ensureToastContainer();
    const meta      = TOAST_COLORS[type] || TOAST_COLORS.info;
    const el        = document.createElement('div');
    el.style.cssText = [
      `background:${meta.bg}`,
      `border-left:4px solid ${meta.border}`,
      'color:#f1f5f9',
      'padding:12px 16px',
      'border-radius:6px',
      'font-size:14px',
      'font-family:system-ui,sans-serif',
      'box-shadow:0 4px 20px rgba(0,0,0,.5)',
      'pointer-events:auto',
      'cursor:pointer',
      'display:flex',
      'align-items:flex-start',
      'gap:10px',
      'opacity:0',
      'transform:translateX(40px)',
      'transition:opacity .25s,transform .25s',
    ].join(';');
    el.innerHTML = `<span style="font-size:16px;flex-shrink:0">${meta.icon}</span><span>${message}</span>`;
    el.addEventListener('click', () => dismissToast(el));
    container.appendChild(el);

    requestAnimationFrame(() => {
      el.style.opacity  = '1';
      el.style.transform = 'translateX(0)';
    });

    const timer = setTimeout(() => dismissToast(el), duration);
    el._toastTimer = timer;
  }

  function dismissToast(el) {
    clearTimeout(el._toastTimer);
    el.style.opacity   = '0';
    el.style.transform = 'translateX(40px)';
    setTimeout(() => el.parentNode && el.parentNode.removeChild(el), 280);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     MODAL SYSTEM
  ═══════════════════════════════════════════════════════════════════════ */

  let modalEl = null;

  function ensureModal() {
    if (modalEl && document.body.contains(modalEl)) return modalEl;
    modalEl = document.createElement('div');
    modalEl.id = 'bt-modal-overlay';
    modalEl.style.cssText = [
      'display:none', 'position:fixed', 'inset:0', 'z-index:9000',
      'background:rgba(0,0,0,.7)', 'align-items:center', 'justify-content:center',
      'font-family:system-ui,sans-serif',
    ].join(';');
    modalEl.innerHTML = `
      <div id="bt-modal-box" style="background:#1e293b;border:1px solid #334155;border-radius:10px;
        padding:24px;max-width:520px;width:94vw;max-height:85vh;overflow-y:auto;
        color:#f1f5f9;box-shadow:0 20px 60px rgba(0,0,0,.6)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 id="bt-modal-title" style="margin:0;font-size:18px;font-weight:600"></h3>
          <button id="bt-modal-close" style="background:none;border:none;color:#94a3b8;
            font-size:22px;cursor:pointer;line-height:1;padding:0 4px">&times;</button>
        </div>
        <div id="bt-modal-content"></div>
        <div id="bt-modal-buttons" style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px"></div>
      </div>`;
    modalEl.addEventListener('click', (e) => { if (e.target === modalEl) closeModal(); });
    modalEl.querySelector('#bt-modal-close').addEventListener('click', closeModal);
    document.body.appendChild(modalEl);
    return modalEl;
  }

  function showModal(title, content, buttons = []) {
    const overlay  = ensureModal();
    overlay.querySelector('#bt-modal-title').textContent   = title;
    const contentEl = overlay.querySelector('#bt-modal-content');
    if (typeof content === 'string') {
      contentEl.innerHTML = content;
    } else if (content instanceof HTMLElement) {
      contentEl.innerHTML = '';
      contentEl.appendChild(content);
    }
    const btnsEl = overlay.querySelector('#bt-modal-buttons');
    btnsEl.innerHTML = '';
    buttons.forEach(btn => {
      const b = document.createElement('button');
      b.textContent = btn.label;
      b.style.cssText = `padding:8px 18px;border-radius:6px;border:none;cursor:pointer;
        font-size:14px;font-weight:500;${btn.style || 'background:#334155;color:#f1f5f9;'}`;
      b.addEventListener('click', () => { if (btn.action) btn.action(); });
      btnsEl.appendChild(b);
    });
    overlay.style.display = 'flex';
  }

  function closeModal() {
    if (modalEl) modalEl.style.display = 'none';
  }

  function confirm(message) {
    return new Promise((resolve) => {
      showModal('Confirm', `<p style="margin:0;line-height:1.6">${message}</p>`, [
        {
          label: 'Cancel',
          style: 'background:#334155;color:#f1f5f9;',
          action: () => { closeModal(); resolve(false); },
        },
        {
          label: 'Confirm',
          style: 'background:#ef4444;color:#fff;',
          action: () => { closeModal(); resolve(true); },
        },
      ]);
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     LOADING STATES
  ═══════════════════════════════════════════════════════════════════════ */

  const _loadingOriginals = {};

  function showLoading(elementId, message = 'Loading…') {
    const el = document.getElementById(elementId);
    if (!el) return;
    _loadingOriginals[elementId] = el.innerHTML;
    el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;
      gap:10px;padding:24px;color:#94a3b8;font-size:14px">
      <span class="bt-spinner" style="width:18px;height:18px;border:2px solid #334155;
        border-top-color:#38bdf8;border-radius:50%;animation:bt-spin .7s linear infinite;
        flex-shrink:0"></span>${message}</div>`;
    _injectSpinnerCSS();
  }

  function hideLoading(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (_loadingOriginals[elementId] !== undefined) {
      el.innerHTML = _loadingOriginals[elementId];
      delete _loadingOriginals[elementId];
    }
  }

  let _spinnerCSSInjected = false;
  function _injectSpinnerCSS() {
    if (_spinnerCSSInjected) return;
    _spinnerCSSInjected = true;
    const s = document.createElement('style');
    s.textContent = `@keyframes bt-spin{to{transform:rotate(360deg)}}`;
    document.head.appendChild(s);
  }

  /* ═══════════════════════════════════════════════════════════════════════
     SKELETON SCREENS
  ═══════════════════════════════════════════════════════════════════════ */

  function showSkeleton(container, rows = 5) {
    if (!container) return;
    const skels = Array.from({ length: rows }, (_, i) => {
      const w = [80, 60, 90, 70, 50][i % 5];
      return `<div style="background:linear-gradient(90deg,#1e293b 25%,#293548 50%,#1e293b 75%);
        background-size:200% 100%;animation:bt-shimmer 1.4s infinite;height:18px;
        border-radius:4px;margin-bottom:10px;width:${w}%"></div>`;
    }).join('');
    container.innerHTML = `<div style="padding:16px">${skels}</div>`;
    const s = document.createElement('style');
    s.textContent = `@keyframes bt-shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}`;
    document.head.appendChild(s);
  }

  function hideSkeleton(container) {
    if (!container) return;
    container.innerHTML = '';
  }

  /* ═══════════════════════════════════════════════════════════════════════
     NUMBER FORMATTERS
  ═══════════════════════════════════════════════════════════════════════ */

  const INR = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 });
  const NUM2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function formatPrice(n) {
    if (n === null || n === undefined || isNaN(n)) return '₹—';
    return INR.format(n);
  }

  function formatPnL(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const abs  = Math.abs(n);
    const sign = n >= 0 ? '+' : '-';
    const cls  = n >= 0 ? 'pnl-positive' : 'pnl-negative';
    const str  = `${sign}₹${NUM2.format(abs)}`;
    return `<span class="${cls}">${str}</span>`;
  }

  function formatPct(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const sign = n >= 0 ? '+' : '';
    return `${sign}${n.toFixed(2)}%`;
  }

  function formatCompact(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const abs = Math.abs(n);
    const sign = n < 0 ? '-' : '';
    if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(1)}L`;
    if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`;
    return `${sign}${abs.toFixed(0)}`;
  }

  function formatChange(ltp, prevClose) {
    if (!ltp || !prevClose) return { change: 0, changePct: 0, sign: '' };
    const change    = ltp - prevClose;
    const changePct = (change / prevClose) * 100;
    const sign      = change >= 0 ? '+' : '';
    return { change, changePct, sign };
  }

  /* ═══════════════════════════════════════════════════════════════════════
     REGIME HELPERS
  ═══════════════════════════════════════════════════════════════════════ */

  function getRegimeColor(regime) {
    return regimeMeta(regime).color;
  }

  function getRegimeIcon(regime) {
    return regimeMeta(regime).icon;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     CONFIDENCE
  ═══════════════════════════════════════════════════════════════════════ */

  function getConfidenceColor(score) {
    if (score >= 80) return '#4ade80';
    if (score >= 60) return '#fb923c';
    return '#f87171';
  }

  function renderConfidenceBar(score) {
    const pct   = Math.min(100, Math.max(0, score || 0));
    const color = getConfidenceColor(pct);
    return `<div style="display:flex;align-items:center;gap:8px">
      <div style="flex:1;height:6px;background:#1e293b;border-radius:3px;overflow:hidden">
        <div style="width:${pct}%;height:100%;background:${color};border-radius:3px;
          transition:width .4s ease"></div>
      </div>
      <span style="font-size:12px;color:${color};min-width:32px;text-align:right">${pct}%</span>
    </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     SIGNAL DIRECTION BADGE
  ═══════════════════════════════════════════════════════════════════════ */

  function renderDirectionBadge(direction) {
    const d   = (direction || '').toUpperCase();
    const map = {
      BUY:     { bg: '#14532d', color: '#4ade80', label: '▲ BUY' },
      LONG:    { bg: '#14532d', color: '#4ade80', label: '▲ LONG' },
      SELL:    { bg: '#450a0a', color: '#f87171', label: '▼ SELL' },
      SHORT:   { bg: '#450a0a', color: '#f87171', label: '▼ SHORT' },
      NEUTRAL: { bg: '#1e293b', color: '#94a3b8', label: '— NEUTRAL' },
    };
    const meta = map[d] || map.NEUTRAL;
    return `<span style="display:inline-block;padding:2px 10px;border-radius:4px;
      font-size:12px;font-weight:700;background:${meta.bg};color:${meta.color}">${meta.label}</span>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     TOP MARKET BAR
  ═══════════════════════════════════════════════════════════════════════ */

  function updateMarketBar(niftyQuote, bankniftyQuote, isOpen) {
    _updateQuote('bt-nifty-price',    'bt-nifty-change',    niftyQuote);
    _updateQuote('bt-bnk-price',      'bt-bnk-change',      bankniftyQuote);
    const statusEl = document.getElementById('bt-market-status');
    if (statusEl) {
      statusEl.textContent  = isOpen ? 'OPEN' : 'CLOSED';
      statusEl.style.color  = isOpen ? '#4ade80' : '#f87171';
    }
  }

  function _updateQuote(priceId, changeId, quote) {
    if (!quote) return;
    const priceEl  = document.getElementById(priceId);
    const changeEl = document.getElementById(changeId);
    if (priceEl)  priceEl.textContent  = NUM2.format(quote.ltp || quote.last_price || 0);
    if (changeEl && quote.ltp && quote.prev_close) {
      const { change, changePct, sign } = formatChange(quote.ltp, quote.prev_close);
      const color = change >= 0 ? '#4ade80' : '#f87171';
      changeEl.textContent  = `${sign}${Math.abs(changePct).toFixed(2)}%`;
      changeEl.style.color  = color;
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
     REGIME BADGE
  ═══════════════════════════════════════════════════════════════════════ */

  function updateRegimeBadge(regime) {
    const el = document.getElementById('bt-regime-badge');
    if (!el) return;
    const meta    = regimeMeta(regime);
    el.textContent = `${meta.icon} ${meta.label}`;
    el.style.background = meta.color + '22';
    el.style.color      = meta.color;
    el.style.border     = `1px solid ${meta.color}55`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     LOSS LOCK OVERLAY
  ═══════════════════════════════════════════════════════════════════════ */

  function showLossLock(todayPnL, limitRs) {
    let overlay = document.getElementById('bt-loss-lock-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'bt-loss-lock-overlay';
      overlay.style.cssText = [
        'position:fixed', 'inset:0', 'z-index:99998',
        'background:rgba(10,0,0,.92)',
        'display:flex', 'flex-direction:column',
        'align-items:center', 'justify-content:center',
        'color:#f1f5f9', 'font-family:system-ui,sans-serif',
        'text-align:center', 'padding:32px',
      ].join(';');
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
      <div style="font-size:64px;margin-bottom:16px">🔒</div>
      <h1 style="color:#f87171;margin:0 0 8px;font-size:28px">DAILY LOSS LIMIT REACHED</h1>
      <p style="color:#94a3b8;margin:0 0 4px;font-size:16px">
        Today's P&amp;L: <span style="color:#f87171;font-weight:700">-₹${Math.abs(todayPnL).toLocaleString('en-IN')}</span>
      </p>
      <p style="color:#94a3b8;margin:0 0 28px;font-size:16px">
        Limit: <span style="color:#fb923c">₹${Math.abs(limitRs).toLocaleString('en-IN')}</span>
      </p>
      <p style="color:#64748b;font-size:14px;max-width:420px;margin:0 0 28px;line-height:1.6">
        Trading has been locked for today to protect your capital.
        Step away, review your journal, and return tomorrow.
      </p>
      <button onclick="window.APP && window.APP.dismissLossLock()" style="
        padding:10px 28px;border-radius:6px;border:1px solid #ef4444;
        background:transparent;color:#ef4444;font-size:14px;cursor:pointer;
        font-weight:600">Override (I understand the risk)</button>`;
    overlay.style.display = 'flex';
  }

  /* ═══════════════════════════════════════════════════════════════════════
     NAV HELPERS
  ═══════════════════════════════════════════════════════════════════════ */

  function setActiveNav(screenId) {
    document.querySelectorAll('[data-nav]').forEach(el => {
      const active = el.dataset.nav === screenId;
      el.classList.toggle('nav-active', active);
      el.setAttribute('aria-current', active ? 'page' : '');
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════
     CARD ERROR STATE
  ═══════════════════════════════════════════════════════════════════════ */

  function showCardError(elementId, message) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.innerHTML = `<div style="display:flex;align-items:center;gap:10px;padding:16px;
      color:#f87171;font-size:14px;background:#450a0a22;border-radius:6px;border:1px solid #450a0a">
      <span style="font-size:20px">⚠</span>
      <span>${message}</span>
    </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     EMPTY STATE
  ═══════════════════════════════════════════════════════════════════════ */

  function showEmpty(container, message, icon = '📭') {
    if (!container) return;
    container.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;
      justify-content:center;padding:48px 24px;color:#64748b;text-align:center;gap:12px">
      <span style="font-size:40px">${icon}</span>
      <p style="margin:0;font-size:14px;line-height:1.6">${message}</p>
    </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════
     PUBLIC API
  ═══════════════════════════════════════════════════════════════════════ */

  window.UI = {
    toast,
    showModal,
    closeModal,
    confirm,

    showLoading,
    hideLoading,

    showSkeleton,
    hideSkeleton,

    formatPrice,
    formatPnL,
    formatPct,
    formatCompact,
    formatChange,

    getRegimeColor,
    getRegimeIcon,

    getConfidenceColor,
    renderConfidenceBar,

    renderDirectionBadge,

    updateMarketBar,
    updateRegimeBadge,

    showLossLock,

    setActiveNav,
    showCardError,
    showEmpty,
  };
})();
