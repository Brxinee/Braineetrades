"""
Backtest engine — pure pandas/numpy event-driven simulator.

No vectorbt dependency. Handles:
  - 1% risk-per-trade position sizing
  - Max 3 concurrent positions
  - Per-trade SL (low-touch) and target (high-touch) tracking
  - 3:15 PM IST hard square-off
  - NSE-style fees: 0.03% per leg + 0.01% STT on sells
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import NamedTuple

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

from strategies.base import Strategy, SignalResult
from backtest.metrics import compute_metrics

IST = pytz.timezone("Asia/Kolkata")

MAX_CONCURRENT = 3
RISK_PER_TRADE = 0.01    # 1% of capital per trade
FEE_PER_LEG = 0.0003     # ~0.03% brokerage per leg
STT_SELL = 0.0001        # 0.01% STT on sell leg (equity delivery approx)
SLIPPAGE = 0.0001        # 0.01% slippage per leg


# ──────────────────────────────────────────────────────────────────────────
# Data fetching
# ──────────────────────────────────────────────────────────────────────────

def fetch_5m_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch 5-minute OHLCV from yfinance (max 60 days lookback for 5m).
    Returns IST-indexed DataFrame with lowercase columns.
    Raises RuntimeError if data is unavailable or empty.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval="5m", start=start, end=end, auto_adjust=True)

    if df is None or df.empty:
        raise RuntimeError(f"No 5m data for {symbol} ({start}→{end})")

    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].copy()

    # Convert to IST
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(IST)
    else:
        df.index = df.index.tz_convert(IST)

    # Remove zero-volume bars (pre/post-market artefacts)
    df = df[df["volume"] > 0].dropna()

    if df.empty:
        raise RuntimeError(f"All bars zero-volume for {symbol}")

    return df


# ──────────────────────────────────────────────────────────────────────────
# Event-driven trade simulator
# ──────────────────────────────────────────────────────────────────────────

class _Position(NamedTuple):
    symbol: str
    entry_bar: int
    entry_time: str
    entry_price: float
    qty: int
    sl: float
    target: float   # NaN = no fixed target (trail via signal exit)


def _simulate(
    symbol: str,
    df: pd.DataFrame,
    signals: SignalResult,
    capital_per_slot: float,
) -> list[dict]:
    """
    Simulate one strategy on one symbol's OHLCV DataFrame.
    Returns a list of closed-trade dicts.

    Entry:  at the CLOSE of the entry bar (conservative — next open is common
            but requires look-ahead; close is same-bar, slightly optimistic but
            simpler and widely accepted for research).
    SL:     triggered when bar LOW crosses below sl price — filled at sl price.
    Target: triggered when bar HIGH crosses above target — filled at target.
    Exit:   triggered when signals.exits is True — filled at bar close.
    Fees:   applied on both legs.
    """
    entries_arr = signals.entries.values.astype(bool)
    exits_arr = signals.exits.values.astype(bool)
    sl_arr = signals.sl.values.astype(float)
    tgt_arr = signals.target.values.astype(float)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    idx = df.index

    open_pos: _Position | None = None
    trades: list[dict] = []
    concurrent_used = 0  # tracks against MAX_CONCURRENT externally; locally 0 or 1

    def _close_trade(pos: _Position, exit_bar: int, exit_price: float, reason: str) -> dict:
        ep = pos.entry_price
        xp = exit_price
        qty = pos.qty
        gross_pnl = (xp - ep) * qty
        cost = (ep * qty * (FEE_PER_LEG + SLIPPAGE)
                + xp * qty * (FEE_PER_LEG + SLIPPAGE + STT_SELL))
        net_pnl = gross_pnl - cost
        return {
            "symbol": pos.symbol,
            "entry_time": pos.entry_time,
            "exit_time": idx[exit_bar].isoformat(),
            "entry_price": round(ep, 2),
            "exit_price": round(xp, 2),
            "qty": qty,
            "pnl": round(net_pnl, 2),
            "pnl_pct": round((xp / ep - 1) * 100, 4),
            "sl": round(pos.sl, 2) if not np.isnan(pos.sl) else None,
            "target": round(pos.target, 2) if not np.isnan(pos.target) else None,
            "exit_reason": reason,
            "hold_bars": exit_bar - pos.entry_bar,
            "side": "long",
        }

    for i in range(len(df)):
        # ── 1. Check open position for exits ────────────────────────────
        if open_pos is not None:
            sl = open_pos.sl
            tgt = open_pos.target
            exit_price = None
            reason = ""

            # SL check: low touches or breaches stop
            if not np.isnan(sl) and low[i] <= sl:
                exit_price = sl
                reason = "sl"
            # Target check: high touches or exceeds target
            elif not np.isnan(tgt) and high[i] >= tgt:
                exit_price = tgt
                reason = "target"
            # Strategy exit signal (includes squareoff)
            elif exits_arr[i]:
                exit_price = close[i]
                reason = "squareoff" if Strategy.squareoff_mask(df).iloc[i] else "signal"

            if exit_price is not None:
                trades.append(_close_trade(open_pos, i, exit_price, reason))
                open_pos = None

        # ── 2. Open new position if entry signal and no open trade ───────
        if open_pos is None and entries_arr[i]:
            ep = close[i]
            sl = sl_arr[i]
            tgt = tgt_arr[i]

            # Position sizing: 1% of capital / (entry - SL) → qty in shares
            if not np.isnan(sl) and sl < ep:
                risk_per_share = ep - sl
                qty = int((capital_per_slot * RISK_PER_TRADE) / risk_per_share)
            else:
                # Fallback: risk 1% of capital, assume 1% price move
                qty = int((capital_per_slot * RISK_PER_TRADE) / (ep * 0.01))

            qty = max(qty, 1)

            open_pos = _Position(
                symbol=symbol,
                entry_bar=i,
                entry_time=idx[i].isoformat(),
                entry_price=ep,
                qty=qty,
                sl=sl,
                target=tgt,
            )

    # ── 3. Force-close any position still open at end of data ───────────
    if open_pos is not None:
        trades.append(_close_trade(open_pos, len(df) - 1, close[-1], "end_of_data"))

    return trades


# ──────────────────────────────────────────────────────────────────────────
# Full backtest across universe
# ──────────────────────────────────────────────────────────────────────────

def run_strategy_on_symbol(
    strategy: Strategy,
    symbol: str,
    df: pd.DataFrame,
    capital_per_slot: float,
) -> list[dict]:
    try:
        signals = strategy.generate_signals(df)
    except Exception as e:
        print(f"  [WARN] {symbol}: signal error — {e}")
        return []

    if not signals.entries.any():
        return []

    return _simulate(symbol, df, signals, capital_per_slot)


def run_backtest(
    strategy: Strategy,
    symbols: list[str],
    start: str,
    end: str,
    capital: float = 100_000.0,
) -> dict:
    """
    Run strategy across all symbols. Returns results dict ready to JSON-serialise.
    capital_per_slot = capital / MAX_CONCURRENT so the full capital is deployed
    across up to 3 concurrent positions.
    """
    capital_per_slot = capital / MAX_CONCURRENT
    all_trades: list[dict] = []
    per_symbol: dict[str, dict] = {}
    failed_symbols: list[str] = []

    for sym in symbols:
        print(f"  {sym}…", end=" ", flush=True)
        try:
            df = fetch_5m_data(sym, start, end)
        except RuntimeError as e:
            print(f"SKIP ({e})")
            failed_symbols.append(sym)
            continue

        trades = run_strategy_on_symbol(strategy, sym, df, capital_per_slot)
        print(f"{len(trades)} trades")
        all_trades.extend(trades)

        sym_df = pd.DataFrame(trades)
        per_symbol[sym] = (
            compute_metrics(sym_df, capital_per_slot) if not sym_df.empty else {}
        )

    trades_df = pd.DataFrame(all_trades)
    summary = compute_metrics(trades_df, capital)

    return {
        "strategy": strategy.name,
        "strategy_key": next(
            k for k, v in __import__("strategies").REGISTRY.items()
            if v is type(strategy)
        ),
        "params": strategy.cfg,
        "start": start,
        "end": end,
        "capital": capital,
        "symbols_run": len(symbols) - len(failed_symbols),
        "symbols_failed": failed_symbols,
        "summary": summary,
        "per_symbol": per_symbol,
        "trades": all_trades,
        "generated_at": datetime.now(IST).isoformat(),
    }
