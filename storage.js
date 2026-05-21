/**
 * storage.js — localStorage wrapper for Braineetrades cockpit
 * Exposed as window.Storage
 */
(function () {
  'use strict';

  const KEYS = {
    SETTINGS:      'bt_settings',
    JOURNAL:       'bt_journal',
    PAPER_TRADES:  'bt_paper_trades',
    SIGNAL_CACHE:  'bt_signal_cache',
    TODAY_PNL:     'bt_today_pnl',
    PNL_DATE:      'bt_pnl_date',
    ALERTS:        'bt_alerts',
    LAST_REGIME:   'bt_last_regime',
  };

  const DEFAULT_SETTINGS = {
    apiBase:              '',  // empty = same origin (Vercel); set to http://localhost:8000 for local dev
    capital:              500000,
    riskPct:              2,
    dailyLossLimitPct:    3,
    paperMode:            false,
    autoRefreshInterval:  60,
    alertSignals:         true,
    alertRegimeChange:    true,
    alertVolatilitySpike: true,
    defaultStrategy:      'orb15',
  };

  /* ── helpers ─────────────────────────────────────────────────────────── */

  function read(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return fallback;
      return JSON.parse(raw);
    } catch {
      return fallback;
    }
  }

  function write(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.warn('[Storage] write failed:', key, e);
    }
  }

  function todayIST() {
    // Returns 'YYYY-MM-DD' in IST regardless of client timezone
    const now = new Date();
    const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
    const y   = ist.getFullYear();
    const m   = String(ist.getMonth() + 1).padStart(2, '0');
    const d   = String(ist.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }

  /* ── Settings ────────────────────────────────────────────────────────── */

  function getSettings() {
    const stored = read(KEYS.SETTINGS, {});
    return Object.assign({}, DEFAULT_SETTINGS, stored);
  }

  function saveSettings(settings) {
    const merged = Object.assign({}, DEFAULT_SETTINGS, settings);
    write(KEYS.SETTINGS, merged);
  }

  /* ── Journal ─────────────────────────────────────────────────────────── */

  function getJournalTrades() {
    return read(KEYS.JOURNAL, []);
  }

  function addJournalTrade(trade) {
    const trades = getJournalTrades();
    const entry  = Object.assign({}, trade, { id: Date.now(), createdAt: new Date().toISOString() });
    trades.unshift(entry);
    write(KEYS.JOURNAL, trades);
    return entry;
  }

  function updateJournalTrade(id, updates) {
    const trades  = getJournalTrades();
    const idx     = trades.findIndex(t => t.id === id);
    if (idx === -1) return false;
    trades[idx]   = Object.assign({}, trades[idx], updates, { updatedAt: new Date().toISOString() });
    write(KEYS.JOURNAL, trades);
    return true;
  }

  function deleteJournalTrade(id) {
    const trades = getJournalTrades().filter(t => t.id !== id);
    write(KEYS.JOURNAL, trades);
  }

  /* ── Paper Trades ────────────────────────────────────────────────────── */

  function getPaperTrades() {
    return read(KEYS.PAPER_TRADES, []);
  }

  function addPaperTrade(trade) {
    const trades = getPaperTrades();
    const entry  = Object.assign({}, trade, { id: Date.now(), createdAt: new Date().toISOString() });
    trades.unshift(entry);
    write(KEYS.PAPER_TRADES, trades);
    return entry;
  }

  function clearPaperTrades() {
    write(KEYS.PAPER_TRADES, []);
  }

  /* ── Signal Cache (session only — sessionStorage) ────────────────────── */

  function getSignalCache() {
    try {
      const raw = sessionStorage.getItem(KEYS.SIGNAL_CACHE);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  function setSignalCache(data) {
    try {
      sessionStorage.setItem(KEYS.SIGNAL_CACHE, JSON.stringify(data));
    } catch (e) {
      console.warn('[Storage] sessionStorage write failed:', e);
    }
  }

  /* ── Daily P&L ───────────────────────────────────────────────────────── */

  function _ensurePnLDate() {
    const today   = todayIST();
    const stored  = read(KEYS.PNL_DATE, null);
    if (stored !== today) {
      write(KEYS.PNL_DATE,   today);
      write(KEYS.TODAY_PNL,  0);
    }
  }

  function getTodayPnL() {
    _ensurePnLDate();
    return read(KEYS.TODAY_PNL, 0);
  }

  function addTodayPnL(amount) {
    _ensurePnLDate();
    const current = read(KEYS.TODAY_PNL, 0);
    write(KEYS.TODAY_PNL, current + amount);
  }

  function resetTodayPnL() {
    write(KEYS.PNL_DATE,  todayIST());
    write(KEYS.TODAY_PNL, 0);
  }

  /* ── Alerts History ──────────────────────────────────────────────────── */

  function getAlerts() {
    return read(KEYS.ALERTS, []);
  }

  function addAlert(alert) {
    const alerts = getAlerts();
    const entry  = Object.assign({}, alert, { id: Date.now(), ts: new Date().toISOString() });
    alerts.unshift(entry);
    // keep last 200
    if (alerts.length > 200) alerts.splice(200);
    write(KEYS.ALERTS, alerts);
    return entry;
  }

  function clearAlerts() {
    write(KEYS.ALERTS, []);
  }

  /* ── Last Known Regime ───────────────────────────────────────────────── */

  function getLastRegime() {
    return read(KEYS.LAST_REGIME, null);
  }

  function setLastRegime(regime) {
    write(KEYS.LAST_REGIME, regime);
  }

  /* ── CSV Export ──────────────────────────────────────────────────────── */

  function exportJournalCSV(trades) {
    if (!trades || trades.length === 0) {
      alert('No trades to export.');
      return;
    }
    const headers = [
      'id', 'date', 'symbol', 'direction', 'strategy',
      'entry', 'exit', 'qty', 'pnl', 'charges', 'netPnl',
      'rMultiple', 'setup', 'outcome', 'tags', 'notes', 'createdAt',
    ];
    const escape = (val) => {
      if (val === undefined || val === null) return '';
      const s = String(val);
      if (s.includes(',') || s.includes('"') || s.includes('\n')) {
        return '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    };
    const rows = trades.map(t =>
      headers.map(h => escape(Array.isArray(t[h]) ? t[h].join(';') : t[h])).join(',')
    );
    const csv  = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `braineetrades_journal_${todayIST()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /* ── CSV Import ──────────────────────────────────────────────────────── */

  function importJournalCSV(csvText) {
    const lines   = csvText.trim().split('\n');
    if (lines.length < 2) return { imported: 0, errors: ['Empty file'] };

    const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, ''));
    const numericFields = ['entry', 'exit', 'qty', 'pnl', 'charges', 'netPnl', 'rMultiple'];
    const errors  = [];
    let   imported = 0;

    for (let i = 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      // Handle quoted fields with commas
      const cols = [];
      let inQuote = false, cur = '';
      for (let c = 0; c < line.length; c++) {
        if (line[c] === '"') { inQuote = !inQuote; continue; }
        if (line[c] === ',' && !inQuote) { cols.push(cur); cur = ''; continue; }
        cur += line[c];
      }
      cols.push(cur);

      const trade = {};
      headers.forEach((h, idx) => {
        const val = (cols[idx] || '').trim();
        if (h === 'tags') {
          trade[h] = val ? val.split(';') : [];
        } else if (numericFields.includes(h)) {
          trade[h] = val !== '' ? parseFloat(val) : null;
        } else {
          trade[h] = val;
        }
      });

      // skip if already has an id from another import
      delete trade.id;
      delete trade.createdAt;
      try {
        addJournalTrade(trade);
        imported++;
      } catch (e) {
        errors.push(`Row ${i}: ${e.message}`);
      }
    }
    return { imported, errors };
  }

  /* ── Public API ──────────────────────────────────────────────────────── */

  window.Storage = {
    getSettings,
    saveSettings,

    getJournalTrades,
    addJournalTrade,
    updateJournalTrade,
    deleteJournalTrade,

    getPaperTrades,
    addPaperTrade,
    clearPaperTrades,

    getSignalCache,
    setSignalCache,

    getTodayPnL,
    addTodayPnL,
    resetTodayPnL,

    getAlerts,
    addAlert,
    clearAlerts,

    getLastRegime,
    setLastRegime,

    exportJournalCSV,
    importJournalCSV,

    // expose for debugging
    KEYS,
    DEFAULT_SETTINGS,
  };
})();
