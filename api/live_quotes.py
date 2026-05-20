"""
GET /api/live-quotes
──────────────────────────────────────────────────────────────────────────────
Returns current price + change for all Nifty 50 symbols.
30-second in-memory cache per warm serverless instance — avoids hammering
Yahoo Finance on every page refresh.

Response shape:
  {
    "market_open": bool,
    "timestamp": "<ISO IST>",
    "cached": bool,
    "quotes": [
      {
        "symbol": "RELIANCE.NS",
        "name": "Reliance Industries",
        "sector": "Energy",
        "lot_size": 250,
        "ltp": 1234.55,
        "open": 1220.0,
        "high": 1240.0,
        "low": 1218.0,
        "prev_close": 1225.0,
        "change": 9.55,
        "change_pct": 0.78,
        "volume": 3400000
      }, …
    ]
  }
"""

from __future__ import annotations

import json
import sys
import time
import datetime
import warnings
from pathlib import Path

import pytz
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")
CACHE_TTL = 30          # seconds
NIFTY_JSON = ROOT / "public" / "data" / "nifty50.json"

_MARKET_OPEN  = datetime.time(9, 15)
_MARKET_CLOSE = datetime.time(15, 30)

# ── In-process cache (survives across requests on a warm instance) ─────────────
_cache: dict = {"payload": None, "ts": 0.0}

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI()


def _load_universe() -> list[dict]:
    with open(NIFTY_JSON) as f:
        return json.load(f)["universe"]


def _is_market_open() -> bool:
    now = datetime.datetime.now(IST)
    if now.weekday() >= 5:          # Saturday / Sunday
        return False
    t = now.time()
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def _fetch_quotes(universe: list[dict]) -> list[dict]:
    """
    Batch-download today's OHLCV for all 50 symbols in one yfinance call.
    Falls back to previous close if today's bar isn't available.
    Raises RuntimeError if yfinance returns nothing.
    """
    symbols = [s["symbol"] for s in universe]
    sym_meta = {s["symbol"]: s for s in universe}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(
            tickers=symbols,
            period="2d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )

    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned empty data")

    quotes: list[dict] = []
    now_ist = datetime.datetime.now(IST).isoformat()

    for sym in symbols:
        meta = sym_meta[sym]
        try:
            if len(symbols) == 1:
                df = raw
            else:
                df = raw[sym] if sym in raw.columns.get_level_values(1) else raw[sym]
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue

            latest = df.iloc[-1]
            prev   = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

            ltp        = float(latest["Close"])
            prev_close = float(prev["Close"])
            change     = round(ltp - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

            quotes.append({
                "symbol":     sym,
                "name":       meta["name"],
                "sector":     meta["sector"],
                "lot_size":   meta["lot_size"],
                "ltp":        round(ltp, 2),
                "open":       round(float(latest.get("Open", ltp)), 2),
                "high":       round(float(latest.get("High", ltp)), 2),
                "low":        round(float(latest.get("Low",  ltp)), 2),
                "prev_close": round(prev_close, 2),
                "change":     change,
                "change_pct": change_pct,
                "volume":     int(latest.get("Volume", 0) or 0),
            })
        except Exception:
            continue

    return quotes


@app.get("/api/live-quotes")
async def live_quotes():
    now = time.monotonic()
    is_open = _is_market_open()

    # Serve from cache if still fresh
    if _cache["payload"] and (now - _cache["ts"]) < CACHE_TTL:
        payload = dict(_cache["payload"])
        payload["cached"] = True
        return JSONResponse(payload)

    universe = _load_universe()
    try:
        quotes = _fetch_quotes(universe)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Data unavailable: {e}")

    payload = {
        "market_open": is_open,
        "timestamp":   datetime.datetime.now(IST).isoformat(),
        "cached":      False,
        "quotes":      quotes,
    }
    _cache["payload"] = payload
    _cache["ts"]      = now
    return JSONResponse(payload)


@app.options("/api/live-quotes")
async def live_quotes_options():
    return JSONResponse({}, status_code=200)
