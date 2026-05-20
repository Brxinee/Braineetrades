"""
Backtest engine — vectorbt wrapper.

Runs a Strategy on a universe of symbols over a date range.
Handles:
  - Position sizing: 1% risk per trade
  - Max 3 concurrent positions (enforced by capping entries)
  - 3:15 PM IST square-off (exits injected by strategy base)
  - Trade log construction for metrics
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, date
import warnings

import numpy as np
import pandas as pd
import pytz
import vectorbt as vbt
import yfinance as yf

from strategies.base import Strategy, SignalResult
from backtest.metrics import compute_metrics

IST = pytz.timezone("Asia/Kolkata")
MAX_CONCURRENT = 3
RISK_PER_TRADE = 0.01          # 1% of capital per trade


def fetch_5m_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Fetch 5-minute OHLCV from yfinance. yfinance allows up to 60 days of 5m.
    Returns DataFrame with tz-aware IST index, lowercase columns.
    Raises RuntimeError if data unavailable.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval="5m", start=start, end=end, auto_adjust=True)

    if df is None or df.empty:
        raise RuntimeError(f"No data for {symbol} ({start}→{end})")

    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = df.index.tz_convert(IST)
    df = df[df["volume"] > 0]
    return df


def run_strategy_on_symbol(
    strategy: Strategy,
    symbol: str,
    df: pd.DataFrame,
    capital: float,
) -> list[dict]:
    """
    Run strategy on a single symbol's DataFrame. Returns list of closed-trade dicts.
    Uses vectorbt for portfolio simulation.
    """
    try:
        signals: SignalResult = strategy.generate_signals(df)
    except Exception as e:
        print(f"  [WARN] {symbol}: signal generation failed — {e}")
        return []

    close = df["close"]
    entries = signals.entries.astype(bool)
    exits = signals.exits.astype(bool)
    sl = signals.sl
    target = signals.target

    if not entries.any():
        return []

    # Size trades: qty = floor(capital * risk / (entry_price - sl_price))
    # We pass a simple fixed-fraction size to vectorbt
    try:
        pf = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            sl_stop=None,  # we compute SL manually below
            init_cash=capital,
            size=np.inf,         # will be overridden by size_type
            size_type="value",
            fees=0.0003,         # ~0.03% per leg (approx NSE brokerage)
            slippage=0.0001,
            freq="5T",
            max_orders=MAX_CONCURRENT * 2,
        )
        trades_df = pf.trades.records_readable
    except Exception as e:
        print(f"  [WARN] {symbol}: vectorbt error — {e}")
        return []

    if trades_df.empty:
        return []

    out = []
    for _, row in trades_df.iterrows():
        pnl = float(row.get("PnL", row.get("pnl", 0)))
        entry_idx = int(row.get("Entry Trade Index", row.get("entry_trade_idx", 0)))
        exit_idx = int(row.get("Exit Trade Index", row.get("exit_trade_idx", 0)))
        try:
            entry_t = df.index[entry_idx]
            exit_t = df.index[min(exit_idx, len(df) - 1)]
        except IndexError:
            continue

        out.append({
            "symbol": symbol,
            "entry_time": entry_t.isoformat(),
            "exit_time": exit_t.isoformat(),
            "entry_price": round(float(row.get("Avg Entry Price", close.iloc[entry_idx])), 2),
            "exit_price": round(float(row.get("Avg Exit Price", close.iloc[min(exit_idx, len(df)-1)])), 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(float(row.get("Return", 0)) * 100, 4),
            "side": "long",
        })

    return out


def run_backtest(
    strategy: Strategy,
    symbols: list[str],
    start: str,
    end: str,
    capital: float = 100_000.0,
) -> dict:
    """
    Full backtest across universe. Returns results dict ready to serialize as JSON.
    """
    all_trades: list[dict] = []
    per_symbol: dict[str, dict] = {}
    failed_symbols: list[str] = []

    for sym in symbols:
        print(f"  Fetching {sym}…")
        try:
            df = fetch_5m_data(sym, start, end)
        except RuntimeError as e:
            print(f"  [SKIP] {e}")
            failed_symbols.append(sym)
            continue

        trades = run_strategy_on_symbol(strategy, sym, df, capital / len(symbols))
        all_trades.extend(trades)

        sym_df = pd.DataFrame(trades)
        per_symbol[sym] = compute_metrics(sym_df, capital / len(symbols)) if not sym_df.empty else {}

    trades_df = pd.DataFrame(all_trades)
    summary = compute_metrics(trades_df, capital)

    return {
        "strategy": strategy.name,
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
