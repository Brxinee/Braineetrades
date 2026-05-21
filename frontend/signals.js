/**
 * signals.js — Signal card rendering, filtering, sorting, and journal integration
 * Exposed as window.Signals
 * Depends on: ui.js (window.UI), storage.js (window.Storage)
 */
(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════════════════════════════
     FORMATTERS
  ═══════════════════════════════════════════════════════════════════════════ */

  function fmtInr(v) {
    if (v == null || v === '' || isNaN(Number(v))) return '—';
    return '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtTime(isoStr) {
    if (!isoStr) return '—';
    try {
      return new Date(isoStr).toLocaleString('en-IN', {
        timeZone:  'Asia/Kolkata',
        day:       '2-digit',
        month:     'short',
        hour:      '2-digit',
        minute:    '2-digit',
        hour12:    false,
      });
    } catch { return String(isoStr).slice(0, 16); }
  }

  function fmtAgo(isoStr) {
    if (!isoStr) return '';
    const ms   = Date.now() - new Date(isoStr).getTime();
    if (isNaN(ms) || ms < 0) return '';
    const mins = Math.floor(ms / 60000);
    if (mins < 1)  return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m ago`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     DIRECTION CONFIG
     Left-border colors: CE=#B8FF57, PE=#FF4D4D, SELL_PREMIUM=#FFB347
  ═══════════════════════════════════════════════════════════════════════════ */

  const DIR_CFG = {
    'BUY CE':       { border: '#B8FF57', bg: 'rgba(184,255,87,.10)', color: '#B8FF57' },
    'BUY PE':       { border: '#FF4D4D', bg: 'rgba(255,77,77,.10)',  color: '#FF4D4D' },
    'SELL PREMIUM': { border: '#FFB347', bg: 'rgba(255,179,71,.10)', color: '#FFB347' },
    'LONG':         { border: '#B8FF57', bg: 'rgba(184,255,87,.10)', color: '#B8FF57' },
    'SHORT':        { border: '#FF4D4D', bg: 'rgba(255,77,77,.10)',  color: '#FF4D4D' },
    'BUY':          { border: '#B8FF57', bg: 'rgba(184,255,87,.10)', color: '#B8FF57' },
    'SELL':         { border: '#FF4D4D', bg: 'rgba(255,77,77,.10)',  color: '#FF4D4D' },
  };

  function getDirCfg(direction) {
    const key = (direction || '').toUpperCase().trim();
    return DIR_CFG[key] || { border: '#4A4A4A', bg: 'rgba(74,74,74,.10)', color: '#4A4A4A' };
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     CONFIDENCE BAR  (0–10 scale)
  ═══════════════════════════════════════════════════════════════════════════ */

  function _confColor(score) {
    const s = Number(score) || 0;
    if (s >= 7) return '#B8FF57';
    if (s >= 5) return '#FFB347';
    return '#FF4D4D';
  }

  function _confidenceBarHtml(score) {
    const s   = Math.min(10, Math.max(0, Number(score) || 0));
    const pct = s * 10;
    const col = _confColor(s);
    return `
      <div class="sgl-conf-wrap" title="Confidence ${s}/10">
        <span class="sgl-conf-lbl">Conf</span>
        <div class="sgl-conf-track">
          <div class="sgl-conf-fill" style="width:${pct}%;background:${col}"></div>
        </div>
        <span class="sgl-conf-val" style="color:${col}">${s.toFixed(1)}</span>
      </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     OPTION SUGGESTION
  ═══════════════════════════════════════════════════════════════════════════ */

  function _optionHtml(opt) {
    if (!opt) return '';
    const { strike, type, expiry, estimated_premium } = opt;
    const typeColor = type === 'CE' ? '#B8FF57' : type === 'PE' ? '#FF4D4D' : '#E2E2E2';
    return `
      <div class="sgl-option-row">
        <span class="sgl-dim">Option&nbsp;</span>
        <span style="font-family:monospace;color:#E2E2E2">
          ${strike || '—'}&nbsp;
          <span style="color:${typeColor};font-weight:700">${type || ''}</span>
          &nbsp;${expiry || ''}
          ${estimated_premium != null
            ? `&nbsp;·&nbsp;<span style="color:#4A4A4A">est.&nbsp;</span>${fmtInr(estimated_premium)}`
            : ''}
        </span>
      </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     REASONING LIST
  ═══════════════════════════════════════════════════════════════════════════ */

  function _reasoningHtml(reasons) {
    if (!reasons) return '';
    const list = Array.isArray(reasons) ? reasons : (reasons ? [reasons] : []);
    if (!list.length) return '';
    return `
      <ul class="sgl-reasons">
        ${list.map(r => `<li>${r}</li>`).join('')}
      </ul>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     REGIME WARNING
  ═══════════════════════════════════════════════════════════════════════════ */

  function renderRegimeWarning(regime, strategy) {
    if (!regime || !strategy) return '';
    const compat = Array.isArray(regime.compatible_strategies)
      ? regime.compatible_strategies : [];
    if (compat.includes(strategy)) return '';
    return `
      <div class="sgl-regime-warn">
        <span>&#9888;</span>
        <span>
          <strong>${strategy}</strong> is not recommended in
          <strong>${regime.name || regime.type || 'current'}</strong> regime.
          Compatible: ${compat.length ? compat.join(', ') : 'none listed'}.
        </span>
      </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     SINGLE CARD HTML
  ═══════════════════════════════════════════════════════════════════════════ */

  function renderCard(signal) {
    const s         = signal;
    const dir       = s.direction || s.signal_type || 'LONG';
    const cfg       = getDirCfg(dir);
    const sym       = (s.symbol || '').replace('.NS', '');
    const strategy  = s.strategy_name || s.strategy || s.indicator || '';
    const conf      = s.confidence != null ? s.confidence : s.score;
    const entry     = fmtInr(s.entry   != null ? s.entry   : s.entry_price);
    const slVal     = fmtInr(s.sl      != null ? s.sl      : s.stop_loss);
    const t1        = fmtInr(s.t1      != null ? s.t1      : s.target1 != null ? s.target1 : s.target);
    const t2        = fmtInr(s.t2      != null ? s.t2      : s.target2);
    const rr        = s.rr != null ? Number(s.rr).toFixed(2) : '—';
    const rrColor   = s.rr >= 2 ? '#B8FF57' : s.rr >= 1 ? '#FFB347' : '#4A4A4A';
    const ago       = fmtAgo(s.signal_time || s.ts || s.time);
    const barsAgo   = s.bars_ago != null
      ? `${s.bars_ago} bar${s.bars_ago !== 1 ? 's' : ''} ago` : '';
    const reasoning = s.reasoning || s.reasons || s.indicators || [];
    const optSug    = s.option_suggestion || s.option || null;
    const sigJson   = JSON.stringify(s).replace(/"/g, '&quot;');

    return `
<div class="sig-card" style="border-left-color:${cfg.border}" data-sigid="${s.id || sym}">
  <!-- Direction badge + symbol -->
  <div class="sig-top">
    <div style="flex:1;min-width:0">
      <span class="sgl-dir-badge" style="background:${cfg.bg};color:${cfg.color};border-color:${cfg.color}66">
        ${dir}
      </span>
      <div class="sig-sym" style="margin-top:5px">${sym}</div>
      <div class="sig-meta">${strategy}</div>
    </div>
    <div class="sig-ltp" style="text-align:right;flex-shrink:0">
      <div class="sig-price">${fmtInr(s.ltp)}</div>
      <div class="sgl-dim" style="font-size:11px">LTP</div>
    </div>
  </div>

  <!-- Confidence bar -->
  ${conf != null ? _confidenceBarHtml(conf) : ''}

  <!-- Entry / SL / T1 / T2 price boxes -->
  <div class="sig-prices" style="grid-template-columns:repeat(4,1fr)">
    <div class="sig-p">
      <div class="lbl">Entry</div>
      <div class="val" style="font-family:monospace">${entry}</div>
    </div>
    <div class="sig-p">
      <div class="lbl">Stop Loss</div>
      <div class="val neg" style="font-family:monospace">${slVal}</div>
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

  <!-- R:R -->
  ${rr !== '—' ? `
  <div style="margin-top:8px;font-size:12px;color:#4A4A4A">
    R&nbsp;:&nbsp;R &nbsp;<span style="font-family:monospace;font-size:14px;
    font-weight:700;color:${rrColor}">${rr}</span>
  </div>` : ''}

  <!-- Option suggestion -->
  ${_optionHtml(optSug)}

  <!-- Reasoning bullets -->
  ${_reasoningHtml(reasoning)}

  <!-- Footer: strategy badge · time · bars ago · Log button -->
  <div class="sig-foot">
    <span class="sig-badge">${strategy || 'SIGNAL'}</span>
    <span class="sig-time">${fmtTime(s.signal_time || s.ts || s.time)}</span>
    ${ago     ? `<span class="sig-time">${ago}</span>` : ''}
    ${barsAgo ? `<span class="sig-time">(${barsAgo})</span>` : ''}
    <button class="log-btn" data-sig="${sigJson}"
      onclick="window.Signals.logSignalToJournal(JSON.parse(this.dataset.sig.replace(/&quot;/g,'&quot;')))">
      + Log Trade
    </button>
  </div>
</div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     RENDER CARDS ARRAY INTO CONTAINER
  ═══════════════════════════════════════════════════════════════════════════ */

  function renderCards(signals, containerId) {
    const el = document.getElementById(containerId);
    if (!el) { console.warn('[Signals] container not found:', containerId); return; }

    if (!signals || signals.length === 0) {
      renderEmpty(containerId);
      return;
    }

    _ensureStyles();
    el.innerHTML = '<div class="sig-grid">' + signals.map(renderCard).join('') + '</div>';

    // Bind log-trade buttons safely (encoded JSON in data attribute)
    el.querySelectorAll('.log-btn[data-sig]').forEach(btn => {
      btn.addEventListener('click', () => {
        try {
          const sig = JSON.parse(btn.dataset.sig);
          logSignalToJournal(sig);
        } catch (e) {
          console.error('[Signals] Could not parse signal for journal:', e);
        }
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     EMPTY STATE
  ═══════════════════════════════════════════════════════════════════════════ */

  function renderEmpty(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `
      <div class="no-sig">
        <div class="no-sig-h">NO SIGNALS</div>
        <div style="font-size:13px;color:var(--muted)">
          No signals detected for the active filters and time window.
        </div>
      </div>`;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     FILTER
     filters: { direction, strategy, minConfidence, maxBarsOld }
  ═══════════════════════════════════════════════════════════════════════════ */

  function filterSignals(signals, filters) {
    if (!signals || !signals.length) return [];
    const f = filters || {};
    return signals.filter(s => {
      const dir   = (s.direction || s.signal_type || '').toUpperCase();
      const strat = s.strategy_name || s.strategy || '';
      const conf  = s.confidence != null ? Number(s.confidence) : (s.score != null ? Number(s.score) : 0);
      const bars  = s.bars_ago != null ? Number(s.bars_ago) : 0;

      if (f.direction    && dir   !== f.direction.toUpperCase()) return false;
      if (f.strategy     && strat !== f.strategy)                return false;
      if (f.minConfidence != null && conf < f.minConfidence)     return false;
      if (f.maxBarsOld   != null && bars > f.maxBarsOld)         return false;
      return true;
    });
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     SORT
  ═══════════════════════════════════════════════════════════════════════════ */

  function sortSignals(signals, by) {
    if (!signals || !signals.length) return [];
    const arr = signals.slice();
    const mode = by || 'confidence';

    if (mode === 'time') {
      arr.sort((a, b) => {
        const ta = new Date(a.signal_time || a.ts || a.time || 0).getTime();
        const tb = new Date(b.signal_time || b.ts || b.time || 0).getTime();
        return tb - ta;
      });
    } else {
      // confidence desc
      arr.sort((a, b) => {
        const ca = a.confidence != null ? Number(a.confidence) : (a.score != null ? Number(a.score) : 0);
        const cb = b.confidence != null ? Number(b.confidence) : (b.score != null ? Number(b.score) : 0);
        return cb - ca;
      });
    }
    return arr;
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     LOG SIGNAL TO JOURNAL
  ═══════════════════════════════════════════════════════════════════════════ */

  function logSignalToJournal(signal) {
    const s = typeof signal === 'string' ? JSON.parse(signal) : signal;

    const prefill = {
      date:      (s.signal_time || s.ts || s.time || new Date().toISOString()).slice(0, 10),
      symbol:    (s.symbol || '').replace('.NS', '').toUpperCase(),
      direction: s.direction  || s.signal_type  || 'LONG',
      strategy:  s.strategy_name || s.strategy  || s.indicator || '',
      entry:     s.entry     != null ? String(s.entry)     : s.entry_price != null ? String(s.entry_price) : '',
      sl:        s.sl        != null ? String(s.sl)        : s.stop_loss   != null ? String(s.stop_loss)   : '',
      target:    s.t1        != null ? String(s.t1)        : s.target1     != null ? String(s.target1)
               : s.target    != null ? String(s.target)    : '',
      qty:       s.qty       != null ? String(s.qty) : '',
      notes: [
        s.direction   || '',
        s.rr          ? `R:R ${Number(s.rr).toFixed(2)}` : '',
        s.strategy_name || s.strategy || '',
        Array.isArray(s.reasoning) && s.reasoning.length ? s.reasoning[0] : (s.indicator || ''),
      ].filter(Boolean).join(' · '),
      _source: 'signals',
    };

    // Use Journal module if available
    if (window.Journal && typeof window.Journal.openAddForm === 'function') {
      window.Journal.openAddForm(prefill);
      return;
    }

    // Fallback: store in localStorage and navigate
    try {
      localStorage.setItem('brainee_pending_trade', JSON.stringify(prefill));
    } catch (e) {
      console.warn('[Signals] localStorage write failed:', e);
    }

    const tab = document.querySelector('[data-tab="journal"]');
    if (tab) tab.click();

    setTimeout(() => {
      _applyPrefillToForm(prefill);
      const form = document.getElementById('jnl-form-wrap') || document.getElementById('jnl-add-form');
      if (form) form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 150);

    const sym = prefill.symbol || 'Signal';
    if (window.UI && typeof window.UI.toast === 'function') {
      window.UI.toast(`${sym} pre-filled in Journal — add exit when done`, 'success');
    } else {
      _legacyToast(`${sym} pre-filled — fill exit details in Journal`);
    }
  }

  function _applyPrefillToForm(data) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el && val != null && val !== '') el.value = val;
    };
    set('jnl-date',     data.date);
    set('jnl-symbol',   data.symbol);
    set('jnl-strategy', data.strategy);
    set('jnl-entry',    data.entry);
    set('jnl-exit',     '');
    set('jnl-sl',       data.sl);
    set('jnl-target',   data.target);
    set('jnl-qty',      data.qty);
    set('jnl-notes',    data.notes);
  }

  function _legacyToast(msg) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3500);
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     INLINE STYLES FOR NEW CSS CLASSES
  ═══════════════════════════════════════════════════════════════════════════ */

  function _ensureStyles() {
    if (document.getElementById('sgl-styles')) return;
    const style = document.createElement('style');
    style.id = 'sgl-styles';
    style.textContent = `
      /* Direction badge */
      .sgl-dir-badge {
        display: inline-block;
        font-family: var(--fh, 'Barlow Condensed', sans-serif);
        font-size: 11px;
        font-weight: 900;
        letter-spacing: .10em;
        text-transform: uppercase;
        padding: 3px 10px;
        border-radius: 3px;
        border: 1px solid;
      }
      /* Confidence bar */
      .sgl-conf-wrap {
        display: flex;
        align-items: center;
        gap: 6px;
        margin: 8px 0 4px;
      }
      .sgl-conf-lbl {
        font-size: 10px;
        color: var(--muted, #4A4A4A);
        text-transform: uppercase;
        letter-spacing: .08em;
        width: 32px;
        flex-shrink: 0;
      }
      .sgl-conf-track {
        flex: 1;
        height: 4px;
        background: var(--bg3, #181818);
        border-radius: 2px;
        overflow: hidden;
      }
      .sgl-conf-fill {
        height: 100%;
        border-radius: 2px;
        transition: width .4s ease;
      }
      .sgl-conf-val {
        font-family: var(--fh, 'Barlow Condensed', sans-serif);
        font-size: 13px;
        font-weight: 900;
        width: 26px;
        text-align: right;
        flex-shrink: 0;
      }
      /* Option row */
      .sgl-option-row {
        display: flex;
        align-items: baseline;
        gap: 6px;
        font-size: 12px;
        margin-top: 8px;
      }
      /* Reasoning */
      .sgl-reasons {
        list-style: none;
        margin: 9px 0 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .sgl-reasons li {
        font-size: 11px;
        color: var(--muted, #4A4A4A);
        padding-left: 12px;
        position: relative;
        line-height: 1.5;
      }
      .sgl-reasons li::before {
        content: '·';
        position: absolute;
        left: 2px;
      }
      /* Regime warning */
      .sgl-regime-warn {
        background: rgba(255,179,71,.07);
        border: 1px solid rgba(255,179,71,.28);
        border-radius: 4px;
        padding: 8px 12px;
        font-size: 12px;
        color: #FFB347;
        margin-top: 10px;
        display: flex;
        gap: 8px;
        align-items: flex-start;
        line-height: 1.5;
      }
      .sgl-dim { color: var(--muted, #4A4A4A); }
    `;
    document.head.appendChild(style);
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     PUBLIC API
  ═══════════════════════════════════════════════════════════════════════════ */

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
