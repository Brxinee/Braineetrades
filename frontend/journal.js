/**
 * journal.js — Trade journal CRUD, analytics summary, CSV export
 * Exposes: window.Journal
 */
window.Journal = (() => {
  const MISTAKE_TAGS = [
    'FOMO', 'Revenge Trade', 'Oversize', 'No SL', 'Early Exit',
    'Late Entry', 'News Blind', 'Emotion', 'Overtraded', 'Wrong Setup',
  ];

  const SETUPS = [
    'ORB-15', 'ORB-30', 'First Hour BO', 'VWAP Mean Reversion',
    'Gap Continuation', 'Gap Fill', 'Inside Bar BO', 'Open Drive',
    'Afternoon Trend', 'EOD Momentum', 'Trend Pullback',
    'Failed Breakout', 'CPR+VWAP', 'Manual / Other',
  ];

  let _trades    = [];
  let _filtered  = [];
  let _sortBy    = 'date';
  let _sortDir   = 'desc';
  let _filters   = {};

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    _trades   = window.Storage.getJournalTrades();
    _filtered = [..._trades];
    _renderAll();
    _bindEvents();
  }

  function _renderAll() {
    _renderSummary(_filtered);
    _renderTable(_filtered);
  }

  // ── Add form ──────────────────────────────────────────────────────────────
  function openAddForm(prefill = {}) {
    const today = new Date().toISOString().slice(0, 10);
    const modal = `
      <form id="journal-add-form" autocomplete="off">
        <div class="grid-2" style="gap:12px">
          <div>
            <label class="label">Date</label>
            <input class="input" type="date" name="date" value="${prefill.date || today}" required>
          </div>
          <div>
            <label class="label">Symbol</label>
            <input class="input" type="text" name="symbol" placeholder="NIFTY / BANKNIFTY" value="${prefill.symbol || ''}" required>
          </div>
          <div>
            <label class="label">Setup</label>
            <select class="select" name="setup">
              ${SETUPS.map(s => `<option value="${s}" ${prefill.setup === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
          </div>
          <div>
            <label class="label">Direction</label>
            <select class="select" name="direction">
              <option value="BUY_CE" ${prefill.direction === 'BUY_CE' ? 'selected' : ''}>BUY CE</option>
              <option value="BUY_PE" ${prefill.direction === 'BUY_PE' ? 'selected' : ''}>BUY PE</option>
              <option value="SELL_PREMIUM">SELL PREMIUM</option>
            </select>
          </div>
          <div>
            <label class="label">Entry (₹)</label>
            <input class="input mono" type="number" step="0.5" name="entry" value="${prefill.entry || ''}" placeholder="182.50" required>
          </div>
          <div>
            <label class="label">Exit (₹)</label>
            <input class="input mono" type="number" step="0.5" name="exit" value="${prefill.exit || ''}" placeholder="210.00">
          </div>
          <div>
            <label class="label">Contracts / Qty</label>
            <input class="input mono" type="number" name="qty" value="${prefill.qty || 1}" min="1">
          </div>
          <div>
            <label class="label">P&amp;L (₹) — auto-calculated</label>
            <input class="input mono" type="number" step="1" name="pnl" value="${prefill.pnl || ''}" placeholder="Leave blank to auto-calc">
          </div>
        </div>
        <div style="margin-top:12px">
          <label class="label">Mistake Tags</label>
          <div class="mistake-tags-grid">
            ${MISTAKE_TAGS.map(t => `
              <label class="mistake-tag-label">
                <input type="checkbox" name="mistake_tags" value="${t}">
                <span>${t}</span>
              </label>`).join('')}
          </div>
        </div>
        <div style="margin-top:12px">
          <label class="label">Notes</label>
          <textarea class="input" name="notes" rows="3" placeholder="What happened? What would you do differently?" style="resize:vertical">${prefill.notes || ''}</textarea>
        </div>
        <div style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end">
          <button type="button" class="btn btn-ghost btn-sm" onclick="UI.closeModal()">Cancel</button>
          <button type="submit" class="btn btn-primary">Log Trade</button>
        </div>
      </form>`;

    window.UI.showModal('Log Trade', modal, []);
    setTimeout(() => {
      const form = document.getElementById('journal-add-form');
      if (form) form.addEventListener('submit', e => { e.preventDefault(); _submitAddForm(form); });
    }, 50);
  }

  function _submitAddForm(form) {
    const fd = new FormData(form);
    const entry = parseFloat(fd.get('entry') || 0);
    const exit_ = parseFloat(fd.get('exit')  || 0);
    const qty   = parseInt(fd.get('qty') || 1);
    const pnlRaw = fd.get('pnl');
    const pnl   = pnlRaw ? parseFloat(pnlRaw) : (exit_ - entry) * qty;

    const tags = [];
    form.querySelectorAll('[name="mistake_tags"]:checked').forEach(cb => tags.push(cb.value));

    const trade = {
      id:           String(Date.now()),
      date:         fd.get('date'),
      symbol:       fd.get('symbol').toUpperCase(),
      setup:        fd.get('setup'),
      direction:    fd.get('direction'),
      entry,
      exit:         exit_,
      qty,
      pnl,
      mistake_tags: tags,
      notes:        fd.get('notes') || '',
      hold_time_mins: 0,
    };

    window.Storage.addJournalTrade(trade);
    window.Storage.addTodayPnL(pnl);
    _trades   = window.Storage.getJournalTrades();
    _filtered = [..._trades];
    _applyFilters();
    _renderAll();
    window.UI.closeModal();
    window.UI.toast('Trade logged ✓', 'success');
  }

  // ── Edit / Delete ─────────────────────────────────────────────────────────
  function editTrade(id) {
    const t = _trades.find(x => x.id === id);
    if (!t) return;
    openAddForm(t);
    // Override submit to update instead of add
    setTimeout(() => {
      const form = document.getElementById('journal-add-form');
      if (!form) return;
      form.removeEventListener('submit', _submitAddForm);
      form.addEventListener('submit', e => {
        e.preventDefault();
        const fd = new FormData(form);
        const entry = parseFloat(fd.get('entry') || 0);
        const exit_ = parseFloat(fd.get('exit')  || 0);
        const qty   = parseInt(fd.get('qty') || 1);
        const pnlRaw = fd.get('pnl');
        const pnl   = pnlRaw ? parseFloat(pnlRaw) : (exit_ - entry) * qty;
        const tags  = [];
        form.querySelectorAll('[name="mistake_tags"]:checked').forEach(cb => tags.push(cb.value));
        window.Storage.updateJournalTrade(id, { date: fd.get('date'), symbol: fd.get('symbol').toUpperCase(), setup: fd.get('setup'), direction: fd.get('direction'), entry, exit: exit_, qty, pnl, mistake_tags: tags, notes: fd.get('notes') || '' });
        _trades   = window.Storage.getJournalTrades();
        _filtered = [..._trades];
        _applyFilters();
        _renderAll();
        window.UI.closeModal();
        window.UI.toast('Trade updated', 'success');
      });
    }, 50);
  }

  function deleteTrade(id) {
    window.UI.confirm('Delete this trade entry?').then(ok => {
      if (!ok) return;
      window.Storage.deleteJournalTrade(id);
      _trades   = window.Storage.getJournalTrades();
      _filtered = [..._trades];
      _applyFilters();
      _renderAll();
      window.UI.toast('Trade deleted', 'info');
    });
  }

  // ── Render Table ──────────────────────────────────────────────────────────
  function _renderTable(trades) {
    const el = document.getElementById('journal-table-container');
    if (!el) return;

    if (trades.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div style="font-size:2rem">📓</div>
        <p style="color:var(--muted);margin-top:8px">No trades logged yet</p>
        <button class="btn btn-primary btn-sm" style="margin-top:12px" onclick="Journal.openAddForm()">Log First Trade</button>
      </div>`;
      return;
    }

    const sorted = _sortTrades([...trades]);
    el.innerHTML = `
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th data-sort="date"     class="sortable ${_sortBy === 'date' ? 'sort-' + _sortDir : ''}">Date</th>
              <th>Symbol</th>
              <th>Setup</th>
              <th>Dir</th>
              <th data-sort="entry" class="sortable ${_sortBy === 'entry' ? 'sort-' + _sortDir : ''}">Entry</th>
              <th>Exit</th>
              <th>Qty</th>
              <th data-sort="pnl" class="sortable ${_sortBy === 'pnl' ? 'sort-' + _sortDir : ''}">P&amp;L</th>
              <th>Tags</th>
              <th style="width:80px">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${sorted.map(t => _tradeRow(t)).join('')}
          </tbody>
        </table>
      </div>`;

    el.querySelectorAll('th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (_sortBy === col) _sortDir = _sortDir === 'desc' ? 'asc' : 'desc';
        else { _sortBy = col; _sortDir = 'desc'; }
        _renderAll();
      });
    });
  }

  function _tradeRow(t) {
    const isWin  = t.pnl > 0;
    const dirMap = { BUY_CE: 'CE', BUY_PE: 'PE', SELL_PREMIUM: 'SELL' };
    const dirCol = { BUY_CE: 'var(--accent)', BUY_PE: 'var(--danger)', SELL_PREMIUM: 'var(--warning)' };
    const pnlCol = isWin ? 'var(--accent)' : 'var(--danger)';
    const tags   = (t.mistake_tags || []).slice(0, 2).map(tag =>
      `<span class="badge badge-neutral" style="font-size:9px">${tag}</span>`).join('');

    return `
      <tr class="${isWin ? 'row-positive' : t.pnl < 0 ? 'row-negative' : ''}">
        <td class="mono" style="font-size:12px">${t.date}</td>
        <td style="font-weight:600">${t.symbol}</td>
        <td style="font-size:12px;color:var(--text-secondary)">${t.setup}</td>
        <td><span class="badge" style="background:${dirCol[t.direction] || 'var(--surface-elevated)'}22;color:${dirCol[t.direction] || 'var(--text)'};border:1px solid ${dirCol[t.direction] || 'var(--border)'}40">${dirMap[t.direction] || t.direction}</span></td>
        <td class="mono">₹${t.entry.toFixed(1)}</td>
        <td class="mono">${t.exit ? '₹' + t.exit.toFixed(1) : '—'}</td>
        <td class="mono">${t.qty}</td>
        <td class="mono" style="font-weight:600;color:${pnlCol}">${t.pnl >= 0 ? '+' : ''}₹${Math.abs(t.pnl).toLocaleString('en-IN')}</td>
        <td>${tags}</td>
        <td>
          <button class="btn btn-ghost btn-sm" onclick="Journal.editTrade('${t.id}')" title="Edit">✎</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--danger)" onclick="Journal.deleteTrade('${t.id}')" title="Delete">✕</button>
        </td>
      </tr>`;
  }

  function _sortTrades(trades) {
    return trades.sort((a, b) => {
      let va = a[_sortBy], vb = b[_sortBy];
      if (_sortBy === 'date') { va = new Date(va); vb = new Date(vb); }
      if (va < vb) return _sortDir === 'asc' ? -1 : 1;
      if (va > vb) return _sortDir === 'asc' ? 1 : -1;
      return 0;
    });
  }

  // ── Render Summary ────────────────────────────────────────────────────────
  function _renderSummary(trades) {
    const el = document.getElementById('journal-summary');
    if (!el) return;
    const stats = computeQuickStats(trades);
    const pnlCol = stats.totalPnL >= 0 ? 'var(--accent)' : 'var(--danger)';
    el.innerHTML = `
      <div class="metric-tile">
        <div class="metric-label">Total P&amp;L</div>
        <div class="metric-value mono" style="color:${pnlCol}">${stats.totalPnL >= 0 ? '+' : ''}₹${Math.abs(stats.totalPnL).toLocaleString('en-IN')}</div>
      </div>
      <div class="metric-tile">
        <div class="metric-label">Win Rate</div>
        <div class="metric-value mono">${(stats.winRate * 100).toFixed(0)}%</div>
        <div style="font-size:11px;color:var(--muted)">${stats.winTrades}W / ${stats.lossTrades}L</div>
      </div>
      <div class="metric-tile">
        <div class="metric-label">Avg Win</div>
        <div class="metric-value mono positive">₹${Math.abs(stats.avgWin).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
      </div>
      <div class="metric-tile">
        <div class="metric-label">Avg Loss</div>
        <div class="metric-value mono negative">₹${Math.abs(stats.avgLoss).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
      </div>`;
  }

  // ── Filters ───────────────────────────────────────────────────────────────
  function _bindEvents() {
    const fromEl = document.getElementById('journal-filter-from');
    const toEl   = document.getElementById('journal-filter-to');
    const setupEl = document.getElementById('journal-filter-setup');
    const dirEl  = document.getElementById('journal-filter-dir');
    const resetEl = document.getElementById('journal-filter-reset');

    [fromEl, toEl, setupEl, dirEl].forEach(el => {
      if (el) el.addEventListener('change', _applyFilters);
    });
    if (resetEl) resetEl.addEventListener('click', _resetFilters);

    const addBtn = document.getElementById('journal-add-btn');
    if (addBtn) addBtn.addEventListener('click', () => openAddForm());

    const exportBtn = document.getElementById('journal-export-btn');
    if (exportBtn) exportBtn.addEventListener('click', exportCSV);
  }

  function _applyFilters() {
    const from  = document.getElementById('journal-filter-from')?.value;
    const to    = document.getElementById('journal-filter-to')?.value;
    const setup = document.getElementById('journal-filter-setup')?.value;
    const dir   = document.getElementById('journal-filter-dir')?.value;

    _filtered = _trades.filter(t => {
      if (from && t.date < from) return false;
      if (to   && t.date > to)   return false;
      if (setup && t.setup !== setup) return false;
      if (dir   && t.direction !== dir) return false;
      return true;
    });
    _renderAll();
  }

  function _resetFilters() {
    ['journal-filter-from', 'journal-filter-to', 'journal-filter-setup', 'journal-filter-dir']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    _filtered = [..._trades];
    _renderAll();
  }

  // ── CSV Export ────────────────────────────────────────────────────────────
  function exportCSV() {
    window.Storage.exportJournalCSV(_filtered);
    window.UI.toast(`Exported ${_filtered.length} trades`, 'success');
  }

  // ── Quick Stats ───────────────────────────────────────────────────────────
  function computeQuickStats(trades) {
    const n       = trades.length;
    const wins    = trades.filter(t => t.pnl > 0);
    const losses  = trades.filter(t => t.pnl <= 0);
    const totalPnL = trades.reduce((s, t) => s + (t.pnl || 0), 0);
    const avgWin   = wins.length   ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length   : 0;
    const avgLoss  = losses.length ? losses.reduce((s, t) => s + t.pnl, 0) / losses.length : 0;

    const today = new Date().toISOString().slice(0, 10);
    const todayPnL = trades.filter(t => t.date === today).reduce((s, t) => s + (t.pnl || 0), 0);

    return {
      totalTrades: n,
      winTrades:   wins.length,
      lossTrades:  losses.length,
      winRate:     n ? wins.length / n : 0,
      avgWin,
      avgLoss,
      totalPnL,
      todayPnL,
      expectancy: n ? totalPnL / n : 0,
    };
  }

  // ── Refresh (called by analytics screen) ─────────────────────────────────
  function refresh() {
    _trades   = window.Storage.getJournalTrades();
    _filtered = [..._trades];
    _applyFilters();
  }

  return {
    init,
    openAddForm,
    editTrade,
    deleteTrade,
    exportCSV,
    computeQuickStats,
    refresh,
    SETUPS,
    MISTAKE_TAGS,
  };
})();
