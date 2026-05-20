"""
POST /api/scan
──────────────────────────────────────────────────────────────────────────────
Request body (JSON):
  {
    "strategy": "vwap_reversal",          // required; one of REGISTRY keys
    "symbols": ["RELIANCE.NS", "TCS.NS"]  // optional; defaults to Nifty 50
  }

Response shape:
  {
    "strategy": "VWAP Reversal",
    "market_open": bool,
    "scanned_at": "<ISO IST>",
    "symbols_scanned": 50,
    "signals": [
      {
        "symbol": "HCLTECH.NS",
        "name": "HCL Technologies",
        "sector": "IT",
        "ltp": 1612.40,
        "signal_type": "LONG",
        "entry": 1612.40,
        "sl": 1598.20,
        "target": 1628.80,
        "rr": 1.14,
        "signal_time": "2026-05-20T14:35:00+05:30",
        "bars_ago": 2,
        "indicator": "VWAP"
      }, …
    ]
  }

Signal detection:
  - Pulls last 3 days of 5m data per symbol (enough for intraday indicators).
  - Runs strategy.generate_signals() on the full DataFrame.
  - A signal is "active" if there was an entry in the last SIGNAL_LOOKBACK bars
    and no exit has fired between that entry and the current bar.
  - Outside market hours: scans last session's data, marks market_open=false.
"""

from __future__ import annotations

import json
import sys
import datetime
import warnings
from pathlib import Path

import pytz
import yfinance as yf
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import REGISTRY
from backtest.engine import fetch_5m_data

# ── Constants ─────────────────────────────────────────────────────────────────
IST          = pytz.timezone("Asia/Kolkata")
NIFTY_JSON   = ROOT / "public" / "data" / "nifty50.json"
SIGNAL_LOOKBACK = 6   # bars: signal valid if entry fired within last 6 bars (~30 min)
DATA_DAYS       = 5   # days of 5m data to pull (enough for VWAP/ORB intraday context)

_MARKET_OPEN  = datetime.time(9, 15)
_MARKET_CLOSE = datetime.time(15, 30)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI()


class ScanRequest(BaseModel):
    strategy: str
    symbols: list[str] | None = None


def _load_universe() -> list[dict]:
    with open(NIFTY_JSON) as f:
        return json.load(f)["universe"]


def _is_market_open() -> bool:
    now = datetime.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def _date_range() -> tuple[str, str]:
    """Return (start, end) covering last DATA_DAYS calendar days."""
    now = datetime.datetime.now(IST)
    end = now.strftime("%Y-%m-%d")
    start = (now - datetime.timedelta(days=DATA_DAYS)).strftime("%Y-%m-%d")
    return start, end


def _scan_symbol(
    strategy,
    sym: str,
    meta: dict,
    start: str,
    end: str,
) -> dict | None:
    """
    Run strategy on one symbol. Return signal dict if active, else None.
    A signal is active when:
      1. entries[i] is True within the last SIGNAL_LOOKBACK bars
      2. No exits[j] fired between that entry bar and the last bar
    """
    try:
        df = fetch_5m_data(sym, start, end)
    except RuntimeError:
        return None

    if len(df) < 30:
        return None

    try:
        result = strategy.generate_signals(df)
    except Exception:
        return None

    entries = result.entries.values.astype(bool)
    exits   = result.exits.values.astype(bool)
    sl_arr  = result.sl.values
    tgt_arr = result.target.values
    close   = df["close"].values
    idx     = df.index

    n = len(df)
    lookback_start = max(0, n - SIGNAL_LOOKBACK)

    # Find the most recent entry in the lookback window
    entry_bar = None
    for i in range(n - 1, lookback_start - 1, -1):
        if entries[i]:
            entry_bar = i
            break

    if entry_bar is None:
        return None

    # Check that no exit fired between entry and now
    for j in range(entry_bar + 1, n):
        if exits[j]:
            return None

    # Build signal
    ltp    = float(close[-1])
    entry  = float(close[entry_bar])
    sl     = float(sl_arr[entry_bar]) if not np.isnan(sl_arr[entry_bar]) else None
    target = float(tgt_arr[entry_bar]) if not np.isnan(tgt_arr[entry_bar]) else None

    rr = None
    if sl is not None and target is not None and sl < entry:
        risk   = entry - sl
        reward = target - entry
        rr = round(reward / risk, 2) if risk > 0 else None

    bars_ago = n - 1 - entry_bar

    return {
        "symbol":      sym,
        "name":        meta.get("name", sym),
        "sector":      meta.get("sector", ""),
        "ltp":         round(ltp, 2),
        "signal_type": "LONG",
        "entry":       round(entry, 2),
        "sl":          round(sl, 2) if sl is not None else None,
        "target":      round(target, 2) if target is not None else None,
        "rr":          rr,
        "signal_time": idx[entry_bar].isoformat(),
        "bars_ago":    bars_ago,
        "indicator":   result.signal_meta.get("indicator", strategy.name),
    }


@app.post("/api/scan")
async def scan(body: ScanRequest):
    strategy_key = body.strategy.lower().strip()
    if strategy_key not in REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{strategy_key}'. Valid: {list(REGISTRY.keys())}",
        )

    universe    = _load_universe()
    sym_meta    = {s["symbol"]: s for s in universe}
    all_symbols = [s["symbol"] for s in universe]

    # Use requested symbols if provided; validate each
    if body.symbols:
        symbols = [s for s in body.symbols if s in sym_meta]
        if not symbols:
            raise HTTPException(status_code=400, detail="None of the requested symbols are in the Nifty 50 universe.")
    else:
        symbols = all_symbols

    strategy = REGISTRY[strategy_key]()
    start, end = _date_range()
    is_open = _is_market_open()

    signals: list[dict] = []
    failed: list[str]   = []

    for sym in symbols:
        sig = _scan_symbol(strategy, sym, sym_meta.get(sym, {}), start, end)
        if sig is not None:
            signals.append(sig)
        # If sig is None it means: no current signal (not a failure)

    # Sort by strongest R:R first; symbols with no RR go last
    signals.sort(key=lambda s: s["rr"] if s["rr"] is not None else -999, reverse=True)

    return JSONResponse({
        "strategy":        strategy.name,
        "strategy_key":    strategy_key,
        "market_open":     is_open,
        "scanned_at":      datetime.datetime.now(IST).isoformat(),
        "symbols_scanned": len(symbols),
        "signal_count":    len(signals),
        "signals":         signals,
    })


@app.options("/api/scan")
async def scan_options():
    return JSONResponse({}, status_code=200)
