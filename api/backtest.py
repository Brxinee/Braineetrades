"""
POST /api/backtest
──────────────────────────────────────────────────────────────────────────────
Full implementation in Item 7. Stub returns 501 until then.

Request body:
  {
    "strategy": "vwap_reversal",
    "start": "2025-01-01",
    "end": "2025-05-01",
    "symbols": ["RELIANCE.NS"],   // optional, defaults to Nifty 50
    "capital": 100000,
    "risk_pct": 1.0
  }
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

app = FastAPI()


class BacktestRequest(BaseModel):
    strategy: str
    start: str | None = None
    end: str | None = None
    symbols: list[str] | None = None
    capital: float = 100_000.0
    risk_pct: float = 1.0


@app.post("/api/backtest")
async def backtest(body: BacktestRequest):
    return JSONResponse(
        {"error": "Backtest endpoint coming in Item 7."},
        status_code=501,
    )


@app.options("/api/backtest")
async def backtest_options():
    return JSONResponse({}, status_code=200)
