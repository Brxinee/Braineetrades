"""
POST /api/backtest
──────────────────────────────────────────────────────────────────────────────
Request body:
  {
    "strategy": "vwap_reversal",
    "start": "2025-01-01",       // optional; defaults to 14 days before end
    "end": "2025-05-01",         // optional; defaults to today
    "symbols": ["RELIANCE.NS"],  // optional; defaults to all Nifty 50
    "capital": 100000,           // optional; default ₹1,00,000
    "risk_pct": 1.0              // accepted but engine uses fixed 1% risk/trade
  }

Constraints:
  - Date span must be ≤ 60 days (yfinance 5m data limit).
  - Symbols must be from the Nifty 50 universe (nifty50.json).
  - Running 50 symbols can take 30–90 s; Vercel maxDuration should be 300 s.

Response: same shape as backtest/runner.py JSON output.
"""

from __future__ import annotations

import json
import sys
import datetime
import warnings
from pathlib import Path

import pytz
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import REGISTRY
from backtest.engine import run_backtest

IST      = pytz.timezone("Asia/Kolkata")
NIFTY_JSON = ROOT / "public" / "data" / "nifty50.json"
MAX_DAYS = 60   # yfinance 5-minute data limit

app = FastAPI()


class BacktestRequest(BaseModel):
    strategy: str
    start: str | None = None
    end: str | None = None
    symbols: list[str] | None = None
    capital: float = 100_000.0
    risk_pct: float = 1.0


def _load_universe() -> list[dict]:
    with open(NIFTY_JSON) as f:
        return json.load(f)["universe"]


@app.post("/api/backtest")
async def backtest(body: BacktestRequest):
    # ── Strategy validation ───────────────────────────────────────────────────
    strategy_key = body.strategy.lower().strip()
    if strategy_key not in REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{strategy_key}'. Valid: {list(REGISTRY.keys())}",
        )

    # ── Date resolution ───────────────────────────────────────────────────────
    now = datetime.datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    end_str = body.end or today_str
    try:
        end_dt = datetime.datetime.strptime(end_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid end date. Use YYYY-MM-DD.")

    if body.start:
        try:
            start_dt = datetime.datetime.strptime(body.start, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date. Use YYYY-MM-DD.")
        start_str = body.start
    else:
        start_dt = end_dt - datetime.timedelta(days=14)
        start_str = start_dt.strftime("%Y-%m-%d")

    span = (end_dt - start_dt).days
    if span < 1:
        raise HTTPException(status_code=400, detail="end must be after start.")
    if span > MAX_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range {span} days exceeds the {MAX_DAYS}-day limit for 5-minute data.",
        )

    # ── Symbol resolution ─────────────────────────────────────────────────────
    universe   = _load_universe()
    sym_set    = {s["symbol"] for s in universe}
    all_symbols = [s["symbol"] for s in universe]

    if body.symbols:
        symbols = [s for s in body.symbols if s in sym_set]
        if not symbols:
            raise HTTPException(
                status_code=400,
                detail="None of the requested symbols are in the Nifty 50 universe.",
            )
    else:
        symbols = all_symbols

    # ── Run backtest ──────────────────────────────────────────────────────────
    strategy = REGISTRY[strategy_key]()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = run_backtest(
                strategy=strategy,
                symbols=symbols,
                start=start_str,
                end=end_str,
                capital=body.capital,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest engine error: {e}")

    return JSONResponse(result)


@app.options("/api/backtest")
async def backtest_options():
    return JSONResponse({}, status_code=200)
