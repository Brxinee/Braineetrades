/**
 * signals.js — Signal card rendering, filtering, and journal integration
 * Exposed as window.Signals
 */
(function () {
  'use strict';

  /* ── Formatters ─────────────────────────────────────────────────────────── */

  function fmtInr(v) {
    if (v == null || isNaN(v)) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtAgo(signalTime) {
    if (!signalTime) return '';
    const now    = Date.now();
    const ts     = new Date(signalTime).getTime();
    const diffMs = now - ts;
    if (isNaN(diffMs) || diffMs < 0) return '';
    const mins   = Math.floor(diffMs / 60000);
    if (mins < 1)  return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m ago`;
  }

  function fmtTime(isoStr) {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
    } catch { return isoStr.slice(11, 16); }
  }

  function fmtBarsAgo(barsAgo) {
    if (barsAgo == null) return '';
    if (barsAgo === 0)   return 'current bar';
    return `${barsAgo} bar${barsAgo !== 1 ? 's' : ''} ago`;
  }

  /* ── Direction config ───────────────────────────────────────────────────── */

  const DIR_CONFIG = {
    'BUY CE':       { color: '#B8FF57', textColor: '#000', bg: 'rgba(184,255,87,.12)' },
    'BUY PE':       { color: '#FF4D4D', textColor: '#fff', bg: 'rgba(255,77,77,.12)'  },
    'SELL PREMIUM': { color: '#FFB347', textColor: '#000', bg: 'rgba(255,179,71,.12)' },
    'LONG':         { color: '#B8FF57', textColor: '#000', bg: 'rgba(184,255,87,.12)' },
    'SHORT':        { color: '#FF4D4D', textColor: '#fff', bg: 'rgba(255,77,77,.12)'  },
  };

  function getDirConfig(direction) {
    return DIR_CONFIG[direction] || { color: '#888', textColor: '#fff', bg: 'rgba(136,136,136,.12)' };
  }

  /* ── Confidence bar ─────────────────────────────────────────────────────── */

  function confidenceBarHtml(score) {
    const pct    = Math.min(10, Math.max(0, Number(score) || 0)) * 10;
    let   color  = '#FF4D4D';
    if (score >= 7) color = '#B8FF57';
    else if (score >= 5) color = '#FFB347';
    return `
      <div class="sig-conf-wrap" title="Confidence ${score}/10">
        <div class="sig-conf-label">Confidence</div>
        <div class="sig-conf-track">
          <div class="sig-conf-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="sig-conf-val" style="color:${color}">${Number(score).toFixed(1)}</div>
      </div>`;
  }

  /* ── Option suggestion ──────────────────────────────────────────────────── */

  function optionSuggestionHtml(opt) {
    if (!opt) return '';
    const { strike, type, expiry, estimated_premium } = opt;
    return `
      <div class="sig-option-row">
        <span class="sig-option-label">Option</span>
        <span class="sig-option-val">
          ${strike || '—'} ${type || ''} ${expiry || ''}
          ${estimated_premium != null ? '&nbsp;·&nbsp;<span style="font-family:monospace">~' + fmtInr(estimated_premium) + '</span>' : ''}
        </span>
      </div>`;
  }

  /* ── Reasoning list ─────────────────────────────────────────────────────── */

  function reasoningHtml(reasons) {
    if (!reasons || !reasons.length) return '';
    const items = (Array.isArray(reasons) ? reasons : [reasons])
      .map(r => `<li class="sig-reason-item">${r}</li>`)
      .join('');
    return `<ul class="sig-reason-list">${items}</ul>`;
  }

  /* ── Regime warning ─────────────────────────────────────────────────────── */

  function renderRegimeWarning(regime, strategy) {
    if (!regime || !strategy) return '';
    const compatible = Array.isArray(regime.compatible_strategies) ? regime.compatible_strategies : [];
    if (compatible.includes(strategy)) return '';
    return `
      <div class="sig-regime-warn">
        <span class="sig-regime-icon">&#9888;</span>
        Strategy <strong>${strategy}</strong> not recommended in
        <strong>${regime.name || regime.type || 'current'}</strong> regime.
        Compatible: ${compatible.length ? compatible.join(', ') : 'none'}.
      </div>`;
  }

  /* ── Single card HTML ───────────────────────────────────────────────────── */

  function renderCard(signal) {
    const s         = signal;
    const dir       = s.direction || s.signal_type || 'LONG';
    const cfg       = getDirConfig(dir);
    const sym       = (s.symbol || '').replace('.NS', '');
    const strategy  = s.strategy || s.strategy_name || '';
    const conf      = s.confidence != null ? s.confidence : (s.score != null ? s.score : '—');
    const rr        = s.rr != null ? Number(s.rr).toFixed(2) : '—';
    const ago       = fmtAgo(s.signal_time || s.ts);
    const barsAgo   = fmtBarsAgo(s.bars_ago);
    const sigId     = s.id || (sym + '_' + (s.signal_time || Date.now()));

    const entry  = fmtInr(s.entry  || s.entry_price);
    const sl     = fmtInr(s.sl     || s.stop_loss);
    const t1     = fmtInr(s.t1     || s.target1);
    const t2     = fmtInr(s.t2     || s.target2);

    const reasonArr  = s.reasoning || s.reasons || s.indicators || [];
    const optSuggest = s.option_suggestion || s.option || null;

    return `
<div class="sig-card" style="border-left-color:${cfg.color}" data-sigid="${sigId}">
  <div class="sig-top">
    <div>
      <div class="sig-sym">${sym}</div>
      <div class="sig-meta">${strategy}</div>
    </div>
    <div class="sig-ltp">
      <div class="sig-dir-badge" style="background:${cfg.bg};color:${cfg.color};border-color:${cfg.color}">${dir}</div>
    </div>
  </div>

  ${conf !== '—' ? confidenceBarHtml(conf) : ''}

  <div class="sig-prices">
    <div class="sig-p">
      <div class="lbl">Entry</div>
      <div class="val" style="font-family:monospace">${entry}</div>
    </div>
    <div class="sig-p">
      <div class="lbl">Stop Loss</div>
      <div class="val neg" style="font-family:monospace">${sl}</div>
    </div>
    <div class="sig-p">
      <div class="lbl">Target 1</div>
      <div class="val pos" style="font-family:monospace">${t1}</div>
    </div>
    <div class="sig-p">
      <div class="lbl">Target 2</div>
      <div class="val pos" style="font-family:monospace">${t2}</div>
    </div>
  </div>

  ${rr !== '—' ? `<div class="sig-rr">R:R &nbsp;<strong>${rr}</strong></div>` : ''}

  ${optionSuggestionHtml(optSuggest)}

  ${reasoningHtml(reasonArr)}

  <div class="sig-foot">
    <span class="sig-badge">${s.indicator || strategy || 'SIGNAL'}</span>
    <span class="sig-time">${fmtTime(s.signal_time || s.ts)} IST</span>
    ${ago   ? `<span class="sig-time">${ago}</span>` : ''}
    ${barsAgo ? `<span class="sig-time">(${barsAgo})</span>` : ''}
    <button class="log-btn" onclick="window.Signals.logSignalToJournal(${JSON.stringify(s).replace(/"/g, '&quot;')})">
      + Log Trade
    </button>
  </div>
</div>`;
  }

  /* ── Render many cards ──────────────────────────────────────────────────── */

  function renderCards(signals, containerId) {
    const el = document.getElementById(containerId);
    if (!el) { console.warn('[Signals] container not found:', containerId); return; }

    if (!signals || !signals.length) {
      renderEmpty(containerId);
      return;
    }

    el.innerHTML = '<div class="sig-grid">' + signals.map(renderCard).join('') + '</div>';

    // Inject minimal inline styles if not already present
    _ensureStyles();
  }

  /* ── Empty state ────────────────────────────────────────────────────────── */

  function renderEmpty(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `
      <div class="no-sig">
        <div class="no-sig-h">NO SIGNALS</div>
        <div style="font-size:12px;margin-top:4px;color:var(--muted)">
          No signals detected for the current filters and time window.
        </div>
      </div>`;
  }

  /* ── Filter ─────────────────────────────────────────────────────────────── */

  function filterSignals(signals, filters) {
    if (!signals || !signals.length) return [];
    filters = filters || {};

    return signals.filter(s => {
      const dir     = s.direction || s.signal_type || '';
      const strat   = s.strategy  || s.strategy_name || '';
      const conf    = s.confidence != null ? s.confidence : (s.score != null ? s.score : 0);
      const barsAgo = s.bars_ago != null ? s.bars_ago : 0;

      if (filters.direction && dir !== filters.direction) return false;
      if (filters.strategy  && strat !== filters.strategy)   return false;
      if (filters.minConfidence != null && conf < filters.minConfidence) return false;
      if (filters.maxBarsOld    != null && barsAgo > filters.maxBarsOld) return false;
      return true;
    });
  }

  /* ── Sort ───────────────────────────────────────────────────────────────── */

  function sortSignals(signals, by) {
    if (!signals || !signals.length) return [];
    const arr = signals.slice();
    by = by || 'confidence';

    if (by === 'confidence') {
      arr.sort((a, b) => {
        const ca = a.confidence != null ? a.confidence : (a.score != null ? a.score : 0);
        const cb = b.confidence != null ? b.confidence : (b.score != null ? b.score : 0);
        return cb - ca;
      });
    } else if (by === 'time') {
      arr.sort((a, b) => {
        const ta = new Date(a.signal_time || a.ts || 0).getTime();
        const tb = new Date(b.signal_time || b.ts || 0).getTime();
        return tb - ta;
      });
    }
    return arr;
  }

  /* ── Log to Journal ─────────────────────────────────────────────────────── */

  function logSignalToJournal(signal) {
    const sig = typeof signal === 'string' ? JSON.parse(signal) : signal;

    const prefill = {
      date:      (sig.signal_time || new Date().toISOString()).slice(0, 10),
      symbol:    (sig.symbol || '').replace('.NS', ''),
      direction: sig.direction || sig.signal_type || 'LONG',
      strategy:  sig.strategy  || sig.strategy_name || '',
      entry:     sig.entry     || sig.entry_price   || '',
      sl:        sig.sl        || sig.stop_loss      || '',
      t1:        sig.t1        || sig.target1        || '',
      t2:        sig.t2        || sig.target2        || '',
      rr:        sig.rr        || '',
      notes:     [
        sig.strategy || '',
        sig.direction || '',
        sig.rr ? `R:R ${sig.rr}` : '',
        ...(Array.isArray(sig.reasoning) ? sig.reasoning : [sig.indicator || '']),
      ].filter(Boolean).join(' · '),
    };

    // Try Journal module first
    if (window.Journal && typeof window.Journal.openAddForm === 'function') {
      window.Journal.openAddForm(prefill);
      return;
    }

    // Fallback: navigate to journal tab and fill form fields
    const journalTab = document.querySelector('[data-tab="journal"]');
    if (journalTab) journalTab.click();

    setTimeout(() => {
      _prefillForm(prefill);
      // Scroll to form
      const formWrap = document.getElementById('jnl-form-wrap') || document.getElementById('jnl-add-form');
      if (formWrap) formWrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 120);

    // Toast
    if (window.UI && typeof window.UI.toast === 'function') {
      window.UI.toast((prefill.symbol || 'Signal') + ' pre-filled in journal', 'success');
    } else {
      _showToastFallback((prefill.symbol || 'Signal') + ' pre-filled — fill exit details');
    }
  }

  function _prefillForm(data) {
    const map = {
      'jnl-date':      data.date,
      'jnl-symbol':    data.symbol,
      'jnl-direction': data.direction,
      'jnl-strategy':  data.strategy,
      'jnl-entry':     data.entry,
      'jnl-sl':        data.sl,
      'jnl-notes':     data.notes,
    };
    Object.entries(map).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el && val != null) el.value = val;
    });
  }

  function _showToastFallback(msg) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
  }

  /* ── Inline styles for new classes ─────────────────────────────────────── */

  function _ensureStyles() {
    if (document.getElementById('signals-styles')) return;
    const style = document.createElement('style');
    style.id = 'signals-styles';
    style.textContent = `
      .sig-dir-badge {
        display: inline-block;
        font-family: var(--fh, sans-serif);
        font-size: 12px;
        font-weight: 900;
        letter-spacing: .08em;
        padding: 4px 11px;
        border-radius: 4px;
        border: 1px solid;
        text-transform: uppercase;
      }
      .sig-conf-wrap {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 10px 0 6px;
      }
      .sig-conf-label {
        font-size: 10px;
        color: var(--muted, #4A4A4A);
        text-transform: uppercase;
        letter-spacing: .08em;
        white-space: nowrap;
        width: 72px;
        flex-shrink: 0;
      }
      .sig-conf-track {
        flex: 1;
        height: 4px;
        background: var(--bg3, #181818);
        border-radius: 2px;
        overflow: hidden;
      }
      .sig-conf-fill {
        height: 100%;
        border-radius: 2px;
        transition: width .4s ease;
      }
      .sig-conf-val {
        font-family: var(--fh, sans-serif);
        font-size: 14px;
        font-weight: 900;
        width: 28px;
        text-align: right;
        flex-shrink: 0;
      }
      .sig-rr {
        font-size: 12px;
        color: var(--muted, #4A4A4A);
        margin-top: 8px;
      }
      .sig-rr strong {
        color: var(--text, #E2E2E2);
        font-family: monospace;
      }
      .sig-option-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin-top: 8px;
        font-size: 12px;
      }
      .sig-option-label {
        color: var(--muted, #4A4A4A);
        text-transform: uppercase;
        letter-spacing: .08em;
        font-size: 10px;
        white-space: nowrap;
      }
      .sig-option-val {
        color: var(--text, #E2E2E2);
        font-family: monospace;
      }
      .sig-reason-list {
        list-style: none;
        margin: 10px 0 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 3px;
      }
      .sig-reason-item {
        font-size: 11px;
        color: var(--muted, #4A4A4A);
        padding-left: 12px;
        position: relative;
      }
      .sig-reason-item::before {
        content: '·';
        position: absolute;
        left: 2px;
        color: var(--muted, #4A4A4A);
      }
      .sig-regime-warn {
        background: rgba(255,179,71,.08);
        border: 1px solid rgba(255,179,71,.3);
        border-radius: 5px;
        padding: 8px 12px;
        font-size: 12px;
        color: #FFB347;
        margin-top: 10px;
        display: flex;
        align-items: flex-start;
        gap: 8px;
      }
      .sig-regime-icon {
        flex-shrink: 0;
        font-size: 14px;
      }
    `;
    document.head.appendChild(style);
  }

  /* ── Public API ─────────────────────────────────────────────────────────── */

  window.Signals = {
    renderCards,
    renderCard,
    filterSignals,
    sortSignals,
    renderEmpty,
    renderRegimeWarning,
    logSignalToJournal,
  };

})();
