"""
main.py — Brainee Trades FastAPI backend.

Runs as a standalone ASGI application on Render/Railway.
Start with: python backend/main.py  (or uvicorn backend.main:app)

All routes are private-tool only — no auth, no multi-user.
CORS is fully open (localhost + any deployed frontend origin).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytz
import uvicorn
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Pure-Python normal distribution helpers (no scipy dependency)
def _norm_cdf(x: float) -> float:
    return math.erfc(-x / math.sqrt(2.0)) / 2.0

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

# ── path setup so we can import root-level packages ─────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_loader import (
    CACHE_DIR,
    IST,
    NIFTY_SYMBOL,
    BANKNIFTY_SYMBOL,
    VIX_SYMBOL,
    DataLoader,
    loader,
)
from backend.strategies import STRATEGY_REGISTRY
from backend.backtest import BacktestEngine
from backend.regime import classify_regime
from backend.scanners import ScanEngine
from backend.options import OptionsEngine
from backend.risk import RiskEngine, RiskParams
from backend.market_internals import MarketInternals
from backend.analytics import AnalyticsEngine
from backend.alerts import AlertManager
from backend.replay import ReplayEngine

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("braineetrades.main")

# ── constants ─────────────────────────────────────────────────────────────────
VERSION = "2.0"
NIFTY50_JSON = ROOT / "public" / "data" / "nifty50.json"

# Market session times (IST)
_MARKET_OPEN  = (9, 15)
_MARKET_CLOSE = (15, 30)

# Option chain simulation parameters
_OPTION_STRIKES_AROUND = 10   # strikes above and below ATM


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """Return True if current IST time is a weekday within 09:15–15:30."""
    now = datetime.now(IST)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now.time()
    open_t  = now.replace(hour=_MARKET_OPEN[0],  minute=_MARKET_OPEN[1],  second=0, microsecond=0).time()
    close_t = now.replace(hour=_MARKET_CLOSE[0], minute=_MARKET_CLOSE[1], second=0, microsecond=0).time()
    return open_t <= t <= close_t


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default on None/NaN/inf."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _json_safe(obj: Any) -> Any:
    """Recursively make an object JSON-serialisable."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _error_response(message: str, status_code: int = 500) -> dict:
    return {"error": message, "timestamp": datetime.now(IST).isoformat()}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic request models
# ─────────────────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    strategy: str = Field(
        ...,
        description="Strategy key from REGISTRY (e.g. 'orb15', 'opening_range_breakout')",
    )
    symbols: list[str] = Field(default_factory=list)
    lookback_days: int = Field(default=5, ge=1, le=60)


class BacktestRequest(BaseModel):
    strategy: str
    symbols: list[str] = Field(default_factory=list)
    start: str = Field(..., description="YYYY-MM-DD")
    end: str   = Field(..., description="YYYY-MM-DD")
    capital: float = Field(default=100_000.0, gt=0)
    params: dict = Field(default_factory=dict)


class RiskRequest(BaseModel):
    entry_price: float = Field(..., gt=0)
    stop_loss: float   = Field(..., gt=0)
    capital: float     = Field(default=100_000.0, gt=0)
    risk_pct: float    = Field(default=1.0, gt=0, le=10, description="% of capital to risk per trade")
    target_price: float | None = Field(default=None, gt=0)


class JournalEntry(BaseModel):
    date: str | None = None
    symbol: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    qty: int | None = None
    side: str = "long"
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class JournalAnalyzeRequest(BaseModel):
    entries: list[JournalEntry]


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket alert manager
# ─────────────────────────────────────────────────────────────────────────────

class AlertManager:
    """Manages connected WebSocket clients and pushes alert messages."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            try:
                self._clients.remove(ws)
            except ValueError:
                pass
        logger.info("WebSocket client disconnected (%d remaining)", len(self._clients))

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected clients."""
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    try:
                        self._clients.remove(ws)
                    except ValueError:
                        pass

    async def send_alert(
        self,
        alert_type: str,
        symbol: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        payload = {
            "type": alert_type,
            "symbol": symbol,
            "message": message,
            "data": data or {},
            "timestamp": datetime.now(IST).isoformat(),
        }
        await self.broadcast(payload)


alert_manager = AlertManager()


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create required directories and log startup info."""
    for sub in ("5m", "1d", "vix"):
        (CACHE_DIR / sub).mkdir(parents=True, exist_ok=True)
    # Only create data dirs when filesystem is writable (not on Vercel)
    if not os.environ.get("VERCEL"):
        (ROOT / "data" / "historical").mkdir(parents=True, exist_ok=True)
        (ROOT / "data" / "option_chain").mkdir(parents=True, exist_ok=True)

    market_state = "OPEN" if is_market_open() else "closed"
    logger.info("=" * 60)
    logger.info("Brainee Trades backend v%s starting up", VERSION)
    logger.info("Market is currently: %s", market_state)
    logger.info("Cache dir: %s", CACHE_DIR)
    logger.info("Strategies loaded: %s", list(STRATEGY_REGISTRY.keys()))
    logger.info("=" * 60)

    yield

    logger.info("Brainee Trades backend shutting down.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Brainee Trades",
    description="Private NIFTY intraday trading platform — personal use only.",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "version": VERSION}


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """
    Returns system health, market open flag, and live VIX.
    VIX is fetched from yfinance fast_info; falls back to last cached close.
    """
    try:
        vix_value: float | None = None
        try:
            ticker = yf.Ticker(VIX_SYMBOL)
            fi = ticker.fast_info
            vix_value = _safe_float(
                fi.get("last_price") or fi.get("lastPrice"), default=0.0
            ) or None
        except Exception as exc:
            logger.debug("VIX fast_info failed in /health: %s", exc)

        if not vix_value:
            try:
                end_d   = date.today()
                start_d = end_d - timedelta(days=5)
                vix_series = loader.get_vix_series(start_d, end_d)
                if not vix_series.empty:
                    vix_value = round(float(vix_series.iloc[-1]), 2)
            except Exception:
                vix_value = None

        return {
            "status":      "ok",
            "market_open": is_market_open(),
            "timestamp":   datetime.now(IST).isoformat(),
            "vix":         vix_value,
        }
    except Exception as exc:
        logger.exception("Health check failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/quotes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/quotes")
async def get_quotes(
    symbols: str = Query(
        default="^NSEI,^NSEBANK",
        description="Comma-separated yfinance tickers",
    )
):
    """
    Fetch live quotes for a comma-separated list of symbols.
    Each quote is fetched via yfinance fast_info with a 15s in-memory cache.
    """
    try:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not symbol_list:
            raise HTTPException(status_code=400, detail="No symbols provided")

        quotes = loader.get_batch_quotes(symbol_list)
        return {
            "quotes":    quotes,
            "count":     len(quotes),
            "timestamp": datetime.now(IST).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error fetching quotes for: %s", symbols)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/regime
# ─────────────────────────────────────────────────────────────────────────────

def _classify_regime(
    nifty_daily: pd.DataFrame,
    vix_value: float | None,
) -> dict:
    """
    Classify the current market regime from Nifty daily OHLCV + VIX.

    Regime classes:
        BULL_TREND   — price > EMA20 > EMA50, VIX < 15
        BEAR_TREND   — price < EMA20 < EMA50, VIX > 20
        HIGH_VOL     — VIX >= 20 regardless of trend
        SIDEWAYS     — EMA20 within ±0.5% of EMA50
        RECOVERING   — price > EMA20 but EMA20 < EMA50 (early bull)
        WEAKENING    — price < EMA20 but EMA20 > EMA50 (early bear)
    """
    close = nifty_daily["close"]
    if len(close) < 50:
        return {"regime": "INSUFFICIENT_DATA", "details": {}}

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])

    # 20-day return (momentum)
    ret_20d = (last_close / float(close.iloc[-20]) - 1) * 100 if len(close) >= 20 else 0.0
    # 5-day realised volatility (annualised %)
    daily_ret = close.pct_change().dropna()
    rv5 = float(daily_ret.iloc[-5:].std() * math.sqrt(252) * 100) if len(daily_ret) >= 5 else 0.0

    ema_gap_pct = (last_ema20 / last_ema50 - 1) * 100
    price_vs_ema20 = (last_close / last_ema20 - 1) * 100
    vix = vix_value or 0.0

    if vix >= 20:
        regime = "HIGH_VOL"
    elif abs(ema_gap_pct) <= 0.5:
        regime = "SIDEWAYS"
    elif last_close > last_ema20 and last_ema20 > last_ema50:
        regime = "BULL_TREND" if vix < 15 else "BULL_TREND_ELEVATED_VIX"
    elif last_close < last_ema20 and last_ema20 < last_ema50:
        regime = "BEAR_TREND"
    elif last_close > last_ema20 and last_ema20 <= last_ema50:
        regime = "RECOVERING"
    else:
        regime = "WEAKENING"

    # Recommended strategy bias for the regime
    strategy_bias = {
        "BULL_TREND":               ["opening_range_breakout", "supertrend_ema"],
        "BULL_TREND_ELEVATED_VIX":  ["vwap_reversal", "opening_range_breakout"],
        "BEAR_TREND":               ["gap_fade", "vwap_reversal"],
        "HIGH_VOL":                 ["vwap_reversal", "gap_fade"],
        "SIDEWAYS":                 ["vwap_reversal", "rsi_divergence"],
        "RECOVERING":               ["opening_range_breakout", "rsi_divergence"],
        "WEAKENING":                ["gap_fade", "vwap_reversal"],
        "INSUFFICIENT_DATA":        [],
    }.get(regime, [])

    return {
        "regime": regime,
        "details": {
            "nifty_close":      round(last_close, 2),
            "ema20":            round(last_ema20, 2),
            "ema50":            round(last_ema50, 2),
            "ema_gap_pct":      round(ema_gap_pct, 3),
            "price_vs_ema20":   round(price_vs_ema20, 3),
            "return_20d_pct":   round(ret_20d, 2),
            "realised_vol_5d":  round(rv5, 2),
            "vix":              round(vix, 2),
        },
        "strategy_bias": strategy_bias,
        "timestamp":     datetime.now(IST).isoformat(),
    }


@app.get("/api/regime")
async def get_regime():
    """
    Returns current market regime classification based on Nifty EMA structure
    and India VIX level.
    """
    try:
        end_d   = date.today()
        start_d = end_d - timedelta(days=120)

        nifty_daily = loader.get_daily_ohlcv(NIFTY_SYMBOL, start_d, end_d)

        vix_value: float | None = None
        try:
            ticker = yf.Ticker(VIX_SYMBOL)
            fi = ticker.fast_info
            v = _safe_float(fi.get("last_price") or fi.get("lastPrice"))
            vix_value = v if v > 0 else None
        except Exception:
            pass

        if vix_value is None:
            try:
                vix_series = loader.get_vix_series(start_d, end_d)
                if not vix_series.empty:
                    vix_value = float(vix_series.iloc[-1])
            except Exception:
                pass

        result = _classify_regime(nifty_daily, vix_value)
        return result

    except Exception as exc:
        logger.exception("Regime classification failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /api/scan
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_strategy_key(key: str) -> str:
    """Resolve user-supplied strategy keys to REGISTRY keys."""
    aliases = {
        # ORB variants
        "orb":                  "orb15",
        "orb_15":               "orb15",
        "opening_range_breakout": "orb15",
        "orb_30":               "orb30",
        # VWAP variants
        "vwap":                 "vwap_mean_reversion",
        "vwap_reversal":        "vwap_mean_reversion",
        "vwap_mean_rev":        "vwap_mean_reversion",
        # Supertrend → closest available
        "supertrend":           "cpr_vwap_confluence",
        "supertrend_ema":       "cpr_vwap_confluence",
        "st_ema":               "cpr_vwap_confluence",
        # CPR variants
        "cpr":                  "cpr_vwap_confluence",
        "cpr_vwap":             "cpr_vwap_confluence",
        # Gap variants
        "gap":                  "gap_fill",
        "gap_fade":             "gap_fill",
        # RSI / divergence → trend pullback
        "rsi":                  "trend_pullback",
        "rsi_divergence":       "trend_pullback",
        "divergence":           "trend_pullback",
    }
    normalised = key.strip().lower()
    return aliases.get(normalised, normalised)


@app.post("/api/scan")
async def run_scan(req: ScanRequest):
    """
    Run a strategy scanner over the given symbols for the last `lookback_days`.
    Returns bars that currently show an active entry signal.
    """
    try:
        strategy_key = _resolve_strategy_key(req.strategy)
        if strategy_key not in STRATEGY_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown strategy '{req.strategy}'. "
                       f"Valid keys: {list(STRATEGY_REGISTRY.keys())}",
            )

        StratClass = STRATEGY_REGISTRY[strategy_key]
        strategy   = StratClass()

        # Default to Nifty 50 universe; cap at 20 for Vercel performance
        if req.symbols:
            symbols = req.symbols[:20]  # cap at 20
        else:
            universe = loader.get_nifty50_universe()
            symbols  = [s["symbol"] for s in universe[:20]]  # top 20 only on Vercel

        end_d   = date.today()
        start_d = end_d - timedelta(days=req.lookback_days)

        signals_found: list[dict] = []

        for sym in symbols:
            try:
                df = loader.get_5m_ohlcv(sym, start_d, end_d)
                if df.empty or len(df) < 20:
                    continue

                result = strategy.generate_signals(df)

                # Find the most recent entry signal
                entry_bars = result.entries[result.entries].index
                if entry_bars.empty:
                    continue

                last_entry_ts = entry_bars[-1]
                idx_pos       = df.index.get_loc(last_entry_ts)
                row           = df.iloc[idx_pos]

                sl_val  = result.sl.iloc[idx_pos]
                tgt_val = result.target.iloc[idx_pos]

                signals_found.append({
                    "symbol":       sym,
                    "signal_time":  last_entry_ts.isoformat(),
                    "close":        round(float(row["close"]), 2),
                    "volume":       int(row["volume"]),
                    "stop_loss":    round(float(sl_val), 2) if not math.isnan(sl_val) else None,
                    "target":       round(float(tgt_val), 2) if not math.isnan(tgt_val) else None,
                    "risk_reward":  (
                        round((float(tgt_val) - float(row["close"])) /
                              (float(row["close"]) - float(sl_val)), 2)
                        if (not math.isnan(sl_val) and not math.isnan(tgt_val)
                            and float(row["close"]) > float(sl_val))
                        else None
                    ),
                    "meta":         _json_safe(result.signal_meta),
                })

            except Exception as sym_exc:
                logger.debug("Scan skipped %s: %s", sym, sym_exc)
                continue

        # Sort by most recent signal first
        signals_found.sort(key=lambda x: x["signal_time"], reverse=True)

        return {
            "strategy":      strategy_key,
            "strategy_name": strategy.name,
            "lookback_days": req.lookback_days,
            "symbols_scanned": len(symbols),
            "signals":       signals_found,
            "signal_count":  len(signals_found),
            "timestamp":     datetime.now(IST).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Scan failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/scan/nifty  — scan all 10 strategies on NIFTY in one call
# ─────────────────────────────────────────────────────────────────────────────

# The 10 best intraday strategies for NIFTY 50 options
NIFTY_STRATEGY_KEYS = [
    "orb15",
    "orb30",
    "cpr_vwap_confluence",
    "vwap_mean_reversion",
    "gap_continuation",
    "gap_fill",
    "inside_bar_bo",
    "open_drive",
    "trend_pullback",
    "afternoon_trend",
]

# Time windows when each strategy is relevant (IST hour range, inclusive)
_STRATEGY_WINDOWS: dict[str, tuple[int, int]] = {
    "orb15":               (9, 11),
    "orb30":               (9, 11),
    "open_drive":          (9, 10),
    "gap_continuation":    (9, 10),
    "gap_fill":            (9, 11),
    "cpr_vwap_confluence": (9, 15),
    "vwap_mean_reversion": (10, 15),
    "inside_bar_bo":       (9, 15),
    "trend_pullback":      (10, 14),
    "afternoon_trend":     (13, 15),
}


@app.get("/api/scan/nifty")
async def scan_nifty_all_strategies():
    """
    Scan NIFTY 50 index with all 10 intraday strategies and return
    the best signals from each strategy that fired in the last 2 days.
    """
    try:
        now_ist  = datetime.now(IST)
        ist_hour = now_ist.hour
        end_d    = date.today()
        start_d  = end_d - timedelta(days=2)

        # Load NIFTY data once
        df = loader.get_5m_ohlcv("^NSEI", start_d, end_d)
        if df.empty or len(df) < 20:
            return {"signals": [], "strategies_scanned": 0, "timestamp": now_ist.isoformat()}

        all_signals: list[dict] = []

        for key in NIFTY_STRATEGY_KEYS:
            # Skip strategies outside their time window
            window = _STRATEGY_WINDOWS.get(key, (9, 15))
            if not (window[0] <= ist_hour <= window[1]):
                continue

            strategy = STRATEGY_REGISTRY.get(key)
            if strategy is None:
                continue

            try:
                result     = strategy.generate_signals(df)
                entry_bars = result.entries[result.entries].index
                if entry_bars.empty:
                    continue

                last_ts  = entry_bars[-1]
                idx_pos  = df.index.get_loc(last_ts)
                row      = df.iloc[idx_pos]
                sl_val   = result.sl.iloc[idx_pos]
                tgt_val  = result.target.iloc[idx_pos]

                confidence = getattr(result, "confidence", None)
                if confidence is not None and hasattr(confidence, "iloc"):
                    conf_val = float(confidence.iloc[idx_pos])
                else:
                    conf_val = 65.0

                close = float(row["close"])
                sl    = float(sl_val)   if not math.isnan(sl_val)  else close * 0.99
                tgt   = float(tgt_val)  if not math.isnan(tgt_val) else close * 1.01
                rr    = round((tgt - close) / (close - sl), 2) if close > sl else 0

                all_signals.append({
                    "symbol":     "^NSEI",
                    "strategy":   key,
                    "side":       getattr(result, "side", "LONG"),
                    "bar_time":   last_ts.isoformat(),
                    "close":      round(close, 2),
                    "stop_loss":  round(sl, 2),
                    "target":     round(tgt, 2),
                    "rr":         rr,
                    "confidence": round(conf_val, 1),
                    "score":      round(conf_val, 1),
                    "_strategy":  key,
                })

            except Exception as strat_exc:
                logger.debug("Strategy %s skipped: %s", key, strat_exc)
                continue

        # Sort by confidence descending
        all_signals.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "signals":           all_signals,
            "signal_count":      len(all_signals),
            "strategies_scanned": len([k for k in NIFTY_STRATEGY_KEYS
                                       if _STRATEGY_WINDOWS.get(k, (9,15))[0] <= ist_hour
                                       <= _STRATEGY_WINDOWS.get(k, (9,15))[1]]),
            "timestamp":         now_ist.isoformat(),
        }

    except Exception as exc:
        logger.exception("scan/nifty failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /api/backtest
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/backtest")
async def run_backtest_route(req: BacktestRequest):
    """
    Run a full historical backtest for the given strategy and date range.
    Uses the existing backtest engine (event-driven, 1% risk sizing, NSE fees).
    """
    try:
        strategy_key = _resolve_strategy_key(req.strategy)
        if strategy_key not in STRATEGY_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown strategy '{req.strategy}'. "
                       f"Valid keys: {list(STRATEGY_REGISTRY.keys())}",
            )

        # Validate date strings
        try:
            start_d = datetime.strptime(req.start, "%Y-%m-%d").date()
            end_d   = datetime.strptime(req.end,   "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}")

        if start_d >= end_d:
            raise HTTPException(status_code=400, detail="start must be before end")

        # yfinance 5m data has a ~60-day rolling window; warn but allow longer backtests
        delta_days = (end_d - start_d).days
        if delta_days > 60:
            logger.warning(
                "Backtest window %d days > 60 — yfinance 5m may not return full history",
                delta_days,
            )

        StratClass = STRATEGY_REGISTRY[strategy_key]
        strategy   = StratClass(**req.params)

        if req.symbols:
            symbols = req.symbols
        else:
            universe = loader.get_nifty50_universe()
            symbols  = [s["symbol"] for s in universe]

        # Run via the existing engine (synchronous — wrap in thread to avoid blocking)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_backtest(
                strategy,
                symbols,
                req.start,
                req.end,
                req.capital,
            ),
        )

        return _json_safe(result)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/options
# ─────────────────────────────────────────────────────────────────────────────

def _black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes call price. Returns 0 if inputs are degenerate."""
    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return max(S - K, 0.0)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    except Exception:
        return max(S - K, 0.0)


def _black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put price."""
    try:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return max(K - S, 0.0)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    except Exception:
        return max(K - S, 0.0)


def _bs_greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> dict:
    """Compute delta, gamma, theta (per day), vega for an option."""
    try:
        if T <= 0 or sigma <= 0:
            return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        pdf_d1 = _norm_pdf(d1)
        gamma  = pdf_d1 / (S * sigma * math.sqrt(T))
        vega   = S * pdf_d1 * math.sqrt(T) / 100          # per 1% change in vol
        if is_call:
            delta = _norm_cdf(d1)
            theta = (
                -(S * pdf_d1 * sigma) / (2 * math.sqrt(T))
                - r * K * math.exp(-r * T) * _norm_cdf(d2)
            ) / 365
        else:
            delta = _norm_cdf(d1) - 1
            theta = (
                -(S * pdf_d1 * sigma) / (2 * math.sqrt(T))
                + r * K * math.exp(-r * T) * _norm_cdf(-d2)
            ) / 365
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 2),
            "vega":  round(vega, 2),
        }
    except Exception:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}


@app.get("/api/options")
async def get_options(
    symbol: str  = Query(default="NIFTY", description="NIFTY or BANKNIFTY"),
    expiry: str  = Query(..., description="Expiry date YYYY-MM-DD"),
):
    """
    Return a simulated option chain for NIFTY or BANKNIFTY.
    Uses Black-Scholes with VIX as implied volatility proxy.
    Strikes are rounded to nearest 50 (NIFTY) or 100 (BANKNIFTY).
    """
    try:
        # Map name to yfinance ticker
        symbol_map = {
            "NIFTY":     NIFTY_SYMBOL,
            "BANKNIFTY": BANKNIFTY_SYMBOL,
            "^NSEI":     NIFTY_SYMBOL,
            "^NSEBANK":  BANKNIFTY_SYMBOL,
        }
        yf_sym = symbol_map.get(symbol.upper(), NIFTY_SYMBOL)

        # Parse expiry
        try:
            expiry_d = datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="expiry must be YYYY-MM-DD")

        today = date.today()
        T_days = (expiry_d - today).days
        if T_days < 0:
            raise HTTPException(status_code=400, detail="expiry date is in the past")
        T = max(T_days / 365, 1 / 365)   # at least 1 day

        # Fetch spot
        quote = loader.get_live_quote(yf_sym)
        spot  = quote["ltp"]
        if spot <= 0:
            raise HTTPException(status_code=502, detail=f"Cannot fetch live price for {yf_sym}")

        # Fetch VIX for implied vol
        iv = 0.15   # default 15%
        try:
            end_d   = date.today()
            start_d = end_d - timedelta(days=5)
            vix_series = loader.get_vix_series(start_d, end_d)
            if not vix_series.empty:
                iv = float(vix_series.iloc[-1]) / 100.0
        except Exception:
            pass
        iv = max(iv, 0.05)    # floor at 5%

        r = 0.065    # RBI repo rate approximation

        # Strike step
        strike_step = 100 if "BANK" in symbol.upper() else 50
        atm_strike  = round(spot / strike_step) * strike_step

        strikes = [
            atm_strike + (i - _OPTION_STRIKES_AROUND) * strike_step
            for i in range(_OPTION_STRIKES_AROUND * 2 + 1)
        ]

        chain: list[dict] = []
        for K in strikes:
            if K <= 0:
                continue
            call_price = _black_scholes_call(spot, K, T, r, iv)
            put_price  = _black_scholes_put(spot, K, T, r, iv)
            call_greeks = _bs_greeks(spot, K, T, r, iv, is_call=True)
            put_greeks  = _bs_greeks(spot, K, T, r, iv, is_call=False)

            moneyness = "ATM" if K == atm_strike else ("ITM" if K < spot else "OTM")

            chain.append({
                "strike":     K,
                "moneyness":  moneyness,
                "call": {
                    "ltp":    round(call_price, 2),
                    "iv":     round(iv * 100, 2),
                    **call_greeks,
                },
                "put": {
                    "ltp":    round(put_price, 2),
                    "iv":     round(iv * 100, 2),
                    **put_greeks,
                },
            })

        return {
            "symbol":        symbol.upper(),
            "spot":          round(spot, 2),
            "expiry":        expiry,
            "days_to_expiry": T_days,
            "atm_strike":   atm_strike,
            "iv_pct":       round(iv * 100, 2),
            "chain":        chain,
            "disclaimer":   "Prices are Black-Scholes theoretical values using VIX as IV proxy.",
            "timestamp":    datetime.now(IST).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Option chain failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/internals
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/internals")
async def get_market_internals():
    """
    Market internals / breadth for the Nifty 50 universe.
    Primary: NSE India official API (1 call, no rate limits).
    Fallback: yf.download() batch fetch.
    """
    import warnings
    quotes = []

    # ── Primary: NSE India API ────────────────────────────────────────────────
    try:
        from backend.nse_api import get_nifty50_quotes
        quotes = get_nifty50_quotes()
        logger.info("internals: NSE API returned %d quotes", len(quotes))
    except Exception as e:
        logger.warning("NSE API failed (%s), falling back to yfinance", e)

    # ── Fallback: yf.download() batch ────────────────────────────────────────
    if not quotes:
        try:
            universe = loader.get_nifty50_universe()
            symbols  = [s["symbol"] for s in universe]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(symbols, period="5d", interval="1d",
                                  auto_adjust=True, progress=False, threads=True)
            if not raw.empty:
                close_df = raw["Close"] if "Close" in raw.columns else raw.xs("Close", axis=1, level=0)
                for s in universe:
                    sym = s["symbol"]
                    if sym not in close_df.columns:
                        continue
                    closes = close_df[sym].dropna()
                    if len(closes) < 2:
                        continue
                    ltp  = float(closes.iloc[-1])
                    prev = float(closes.iloc[-2])
                    chg  = round((ltp - prev) / prev * 100, 2) if prev else 0.0
                    quotes.append({"symbol": sym, "name": s.get("name", sym),
                                   "sector": s.get("sector", ""), "ltp": round(ltp, 2),
                                   "change_pct": chg})
        except Exception as e2:
            logger.exception("yfinance fallback also failed: %s", e2)

    if not quotes:
        raise HTTPException(status_code=500, detail="Market internals unavailable — all data sources failed")

    advances = sum(1 for q in quotes if q.get("change_pct", 0) >= 0)
    declines  = len(quotes) - advances

    return {
        "advances": advances,
        "declines": declines,
        "unchanged": 0,
        "breadth": {
            "advances": advances,
            "declines": declines,
            "unchanged": 0,
            "ad_ratio": round(advances / max(declines, 1), 2),
            "above_ema20": 0,
            "above_ema20_pct": 0,
            "avg_rsi": None,
        },
        "stocks": quotes[:20],
        "sectors": {},
        "timestamp": datetime.now(IST).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/heatmap
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/heatmap")
async def get_heatmap():
    """
    Nifty 50 heatmap — returns each stock's daily change % grouped by sector,
    with colour-bucket metadata for the frontend treemap.
    """
    try:
        universe = loader.get_nifty50_universe()
        symbols  = [s["symbol"] for s in universe]

        quotes    = loader.get_batch_quotes(symbols)
        quote_map = {q["symbol"]: q for q in quotes}

        # Group by sector
        sectors: dict[str, list[dict]] = {}
        for stock in universe:
            sym    = stock["symbol"]
            q      = quote_map.get(sym, {})
            change = q.get("change_pct", 0.0)
            ltp    = q.get("ltp", 0.0)

            # Assign colour bucket
            if change >= 2.0:
                colour = "strong_up"
            elif change >= 0.5:
                colour = "up"
            elif change > -0.5:
                colour = "flat"
            elif change > -2.0:
                colour = "down"
            else:
                colour = "strong_down"

            cell = {
                "symbol":     sym,
                "name":       stock.get("name", sym),
                "change_pct": round(change, 2),
                "ltp":        round(ltp, 2),
                "volume":     q.get("volume", 0),
                "colour":     colour,
            }

            sector = stock.get("sector", "Other")
            sectors.setdefault(sector, []).append(cell)

        # Compute sector-level aggregate change (simple average)
        heatmap = []
        for sector_name, cells in sectors.items():
            avg_change = round(
                sum(c["change_pct"] for c in cells) / len(cells), 2
            )
            heatmap.append({
                "sector":     sector_name,
                "avg_change": avg_change,
                "stocks":     sorted(cells, key=lambda x: x["change_pct"], reverse=True),
            })

        heatmap.sort(key=lambda x: x["avg_change"], reverse=True)

        return {
            "heatmap":   heatmap,
            "timestamp": datetime.now(IST).isoformat(),
        }

    except Exception as exc:
        logger.exception("Heatmap failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /api/risk/calculate
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/risk/calculate")
async def calculate_risk(req: RiskRequest):
    """
    Position sizing and risk/reward calculation.

    Given entry, stop-loss, and capital — compute:
    - qty (shares) based on risk_pct of capital
    - capital at risk, R-multiple, recommended lot sizing for F&O
    """
    try:
        entry = req.entry_price
        sl    = req.stop_loss
        cap   = req.capital
        risk_pct_dec = req.risk_pct / 100.0

        if sl >= entry:
            raise HTTPException(
                status_code=400,
                detail="stop_loss must be below entry_price for a long trade",
            )

        risk_per_share = entry - sl
        capital_at_risk = cap * risk_pct_dec
        qty = max(int(capital_at_risk / risk_per_share), 1)

        # Gross exposure
        gross_exposure  = qty * entry
        actual_risk_amt = qty * risk_per_share
        actual_risk_pct = round((actual_risk_amt / cap) * 100, 3)

        # R-multiple and target info
        rr_info: dict = {}
        if req.target_price and req.target_price > entry:
            reward_per_share = req.target_price - entry
            rr_ratio         = round(reward_per_share / risk_per_share, 2)
            rr_info = {
                "target_price":    round(req.target_price, 2),
                "reward_per_share": round(reward_per_share, 2),
                "rr_ratio":        rr_ratio,
                "target_pnl":      round(qty * reward_per_share, 2),
                "breakeven_pct":   round((risk_per_share / entry) * 100, 3),
            }

        # NSE transaction costs estimate (buy + sell legs)
        brokerage   = round(2 * 0.0003 * qty * entry, 2)   # ~0.03% per leg
        stt         = round(0.001 * qty * entry, 2)          # 0.1% on sell side
        total_cost  = round(brokerage + stt, 2)
        net_risk    = round(actual_risk_amt + total_cost, 2)

        return {
            "entry_price":      round(entry, 2),
            "stop_loss":        round(sl, 2),
            "risk_per_share":   round(risk_per_share, 2),
            "qty":              qty,
            "gross_exposure":   round(gross_exposure, 2),
            "capital_at_risk":  round(actual_risk_amt, 2),
            "actual_risk_pct":  actual_risk_pct,
            "estimated_costs": {
                "brokerage":    brokerage,
                "stt":          stt,
                "total":        total_cost,
            },
            "net_risk_incl_costs": net_risk,
            **rr_info,
            "timestamp": datetime.now(IST).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Risk calculation failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/replay/{date}
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/replay/{session_date}")
async def get_replay(
    session_date: str,
    symbol: str = Query(default="^NSEI", description="yfinance ticker"),
):
    """
    Return the full intraday 5-minute OHLCV session for a given date.
    Also includes VWAP, EMA20, and ORB levels for the session.
    """
    try:
        try:
            replay_d = datetime.strptime(session_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")

        if replay_d >= date.today():
            raise HTTPException(status_code=400, detail="Replay only available for past sessions")

        df = loader.get_5m_ohlcv(symbol, replay_d, replay_d)
        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No data available for {symbol} on {session_date}",
            )

        # VWAP (daily reset)
        tp    = (df["high"] + df["low"] + df["close"]) / 3
        tpv   = tp * df["volume"]
        cvol  = df["volume"].cumsum()
        ctpv  = tpv.cumsum()
        vwap_s = ctpv / cvol.replace(0, float("nan"))

        # EMA20 on close
        ema20_s = df["close"].ewm(span=20, adjust=False).mean()

        # ORB (first 3 bars = 15 min)
        orb_high: float | None = None
        orb_low:  float | None = None
        if len(df) >= 3:
            orb_high = float(df["high"].iloc[:3].max())
            orb_low  = float(df["low"].iloc[:3].min())

        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "time":     ts.isoformat(),
                "open":     round(float(row["open"]),   2),
                "high":     round(float(row["high"]),   2),
                "low":      round(float(row["low"]),    2),
                "close":    round(float(row["close"]),  2),
                "volume":   int(row["volume"]),
                "vwap":     round(float(vwap_s.at[ts]), 2) if not math.isnan(vwap_s.at[ts]) else None,
                "ema20":    round(float(ema20_s.at[ts]), 2),
            })

        session_open  = float(df["open"].iloc[0])
        session_high  = float(df["high"].max())
        session_low   = float(df["low"].min())
        session_close = float(df["close"].iloc[-1])
        session_vol   = int(df["volume"].sum())

        return {
            "symbol":    symbol,
            "date":      session_date,
            "session": {
                "open":   round(session_open, 2),
                "high":   round(session_high, 2),
                "low":    round(session_low, 2),
                "close":  round(session_close, 2),
                "volume": session_vol,
                "change_pct": round((session_close / session_open - 1) * 100, 2),
            },
            "levels": {
                "orb_high": round(orb_high, 2) if orb_high else None,
                "orb_low":  round(orb_low, 2)  if orb_low  else None,
                "orb_width": round(orb_high - orb_low, 2) if (orb_high and orb_low) else None,
            },
            "candles":   candles,
            "bar_count": len(candles),
            "timestamp": datetime.now(IST).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Replay failed for %s %s", session_date, symbol)
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /api/journal/analyze
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/journal/analyze")
async def analyze_journal(req: JournalAnalyzeRequest):
    """
    Analyse a list of journal trade entries.
    Computes P&L, win-rate, streak, best/worst trades, tag analysis,
    and symbol breakdown — without storing anything to disk.
    """
    try:
        entries = req.entries
        if not entries:
            raise HTTPException(status_code=400, detail="No journal entries provided")

        trades: list[dict] = []
        for e in entries:
            if e.entry_price and e.exit_price and e.qty:
                direction = 1 if e.side.lower() == "long" else -1
                pnl = direction * (e.exit_price - e.entry_price) * e.qty
            else:
                pnl = None

            trades.append({
                "date":        e.date,
                "symbol":      e.symbol,
                "side":        e.side,
                "entry_price": e.entry_price,
                "exit_price":  e.exit_price,
                "qty":         e.qty,
                "pnl":         pnl,
                "notes":       e.notes,
                "tags":        e.tags,
                "is_win":      pnl > 0 if pnl is not None else None,
            })

        # Only analyse trades that have P&L
        pnl_trades = [t for t in trades if t["pnl"] is not None]

        if not pnl_trades:
            return {
                "trades":   trades,
                "analysis": {"message": "No complete trade data (entry/exit/qty) to analyse"},
            }

        pnl_list  = [t["pnl"] for t in pnl_trades]
        wins      = [p for p in pnl_list if p > 0]
        losses    = [p for p in pnl_list if p <= 0]
        total     = len(pnl_list)
        win_rate  = len(wins) / total

        # Max win streak / loss streak
        streaks: dict[str, int] = {"win": 0, "loss": 0}
        cur_w = cur_l = 0
        for p in pnl_list:
            if p > 0:
                cur_w += 1;  cur_l = 0
            else:
                cur_l += 1;  cur_w = 0
            streaks["win"]  = max(streaks["win"], cur_w)
            streaks["loss"] = max(streaks["loss"], cur_l)

        # Best and worst trades
        best  = max(pnl_trades, key=lambda t: t["pnl"])
        worst = min(pnl_trades, key=lambda t: t["pnl"])

        # Equity curve
        cumulative = 0.0
        equity_curve = []
        for t in pnl_trades:
            cumulative += t["pnl"]
            equity_curve.append(round(cumulative, 2))

        # Tag analysis
        tag_stats: dict[str, dict] = {}
        for t in pnl_trades:
            for tag in (t["tags"] or []):
                if tag not in tag_stats:
                    tag_stats[tag] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
                tag_stats[tag]["trades"] += 1
                if t["pnl"] > 0:
                    tag_stats[tag]["wins"] += 1
                tag_stats[tag]["total_pnl"] += t["pnl"]
        for tag, st in tag_stats.items():
            st["win_rate"] = round(st["wins"] / st["trades"], 3) if st["trades"] else 0
            st["total_pnl"] = round(st["total_pnl"], 2)

        # Symbol breakdown
        sym_stats: dict[str, dict] = {}
        for t in pnl_trades:
            sym = t["symbol"] or "UNKNOWN"
            if sym not in sym_stats:
                sym_stats[sym] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            sym_stats[sym]["trades"] += 1
            if t["pnl"] > 0:
                sym_stats[sym]["wins"] += 1
            sym_stats[sym]["total_pnl"] += t["pnl"]
        for sym, st in sym_stats.items():
            st["win_rate"] = round(st["wins"] / st["trades"], 3)
            st["total_pnl"] = round(st["total_pnl"], 2)

        # Profit factor
        gross_profit = sum(wins) if wins else 0.0
        gross_loss   = abs(sum(losses)) if losses else 0.0
        pf = round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")

        return {
            "trades": trades,
            "analysis": {
                "total_trades":  total,
                "wins":          len(wins),
                "losses":        len(losses),
                "win_rate":      round(win_rate, 3),
                "total_pnl":     round(sum(pnl_list), 2),
                "avg_pnl":       round(sum(pnl_list) / total, 2),
                "avg_win":       round(sum(wins) / len(wins), 2) if wins else 0,
                "avg_loss":      round(sum(losses) / len(losses), 2) if losses else 0,
                "profit_factor": pf if math.isfinite(pf) else None,
                "max_streak":    streaks,
                "best_trade": {
                    "symbol": best["symbol"],
                    "date":   best["date"],
                    "pnl":    round(best["pnl"], 2),
                },
                "worst_trade": {
                    "symbol": worst["symbol"],
                    "date":   worst["date"],
                    "pnl":    round(worst["pnl"], 2),
                },
                "equity_curve": equity_curve,
                "tag_analysis": tag_stats,
                "symbol_breakdown": sym_stats,
            },
            "timestamp": datetime.now(IST).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Journal analysis failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Route: WS /api/alerts/ws
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/api/alerts/ws")
async def alerts_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time alerts.

    On connect, the server immediately sends a welcome message with current
    market status.  Clients can send JSON commands to subscribe to specific
    symbols or trigger an immediate quote push.

    Supported client→server messages:
        {"action": "subscribe",   "symbols": ["^NSEI", "RELIANCE.NS"]}
        {"action": "unsubscribe", "symbols": ["^NSEI"]}
        {"action": "ping"}

    Server pushes:
        {"type": "welcome",   ...}
        {"type": "pong",      ...}
        {"type": "quote",     "symbol": ..., "data": {...}}
        {"type": "alert",     "symbol": ..., "message": ...}
        {"type": "heartbeat", ...}
    """
    await alert_manager.connect(websocket)
    subscribed_symbols: set[str] = set()

    try:
        # Send welcome / market state
        await websocket.send_json({
            "type":        "welcome",
            "message":     "Connected to Brainee Trades alert stream",
            "market_open": is_market_open(),
            "timestamp":   datetime.now(IST).isoformat(),
        })

        # Start a background heartbeat task
        async def heartbeat_loop():
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_json({
                        "type":        "heartbeat",
                        "market_open": is_market_open(),
                        "timestamp":   datetime.now(IST).isoformat(),
                    })
                except Exception:
                    break

        # Start quote push loop (push subscribed symbols every 15s)
        async def quote_push_loop():
            while True:
                await asyncio.sleep(15)
                syms = list(subscribed_symbols)
                if syms:
                    try:
                        quotes = loader.get_batch_quotes(syms)
                        for q in quotes:
                            await websocket.send_json({
                                "type":      "quote",
                                "symbol":    q["symbol"],
                                "data":      q,
                                "timestamp": datetime.now(IST).isoformat(),
                            })
                    except Exception as exc:
                        logger.debug("Quote push failed: %s", exc)

        hb_task = asyncio.create_task(heartbeat_loop())
        qp_task = asyncio.create_task(quote_push_loop())

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type":    "error",
                        "message": "Invalid JSON",
                    })
                    continue

                action = msg.get("action", "")

                if action == "ping":
                    await websocket.send_json({
                        "type":      "pong",
                        "timestamp": datetime.now(IST).isoformat(),
                    })

                elif action == "subscribe":
                    syms = msg.get("symbols", [])
                    subscribed_symbols.update(syms)
                    await websocket.send_json({
                        "type":      "subscribed",
                        "symbols":   list(subscribed_symbols),
                        "timestamp": datetime.now(IST).isoformat(),
                    })
                    # Immediately push first quotes
                    if syms:
                        quotes = loader.get_batch_quotes(syms)
                        for q in quotes:
                            await websocket.send_json({
                                "type":      "quote",
                                "symbol":    q["symbol"],
                                "data":      q,
                                "timestamp": datetime.now(IST).isoformat(),
                            })

                elif action == "unsubscribe":
                    syms = msg.get("symbols", [])
                    subscribed_symbols.difference_update(syms)
                    await websocket.send_json({
                        "type":      "unsubscribed",
                        "symbols":   list(subscribed_symbols),
                        "timestamp": datetime.now(IST).isoformat(),
                    })

                else:
                    await websocket.send_json({
                        "type":    "error",
                        "message": f"Unknown action: '{action}'",
                    })

        finally:
            hb_task.cancel()
            qp_task.cancel()

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected cleanly")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        await alert_manager.disconnect(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /api/alerts/poll  (Vercel-compatible polling fallback)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/alerts/poll")
async def alerts_poll(since: str | None = Query(default=None)):
    """
    Polling fallback for environments that don't support WebSockets (Vercel).

    Returns alerts added after `since` ISO timestamp.
    Frontend uses this when WebSocket connection fails.
    """
    try:
        history = alert_manager.get_history(50)
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                history  = [a for a in history
                            if datetime.fromisoformat(a["timestamp"]) > since_dt]
            except Exception:
                pass
        return {
            "alerts":      history,
            "count":       len(history),
            "market_open": is_market_open(),
            "timestamp":   datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.exception("alerts/poll failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Direct-run entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
    )
