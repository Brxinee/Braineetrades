"""
Performance metrics computed from a trade-log DataFrame.

Expected columns (all produced by engine._simulate):
  symbol, entry_time, exit_time, entry_price, exit_price,
  qty, pnl, pnl_pct, sl, target, exit_reason, hold_bars, side
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(trades: pd.DataFrame, capital: float = 100_000.0) -> dict:
    """
    Returns a flat dict of strategy-level performance metrics.
    trades: one row per closed trade.
    capital: starting capital in INR (for return/expectancy calculations).
    """
    if trades.empty or "pnl" not in trades.columns:
        return _empty_metrics()

    pnl = trades["pnl"].astype(float).values
    if len(pnl) == 0:
        return _empty_metrics()

    wins = pnl > 0
    losses = pnl <= 0

    total_trades = len(pnl)
    win_rate = float(wins.sum()) / total_trades
    avg_win = float(pnl[wins].mean()) if wins.any() else 0.0
    avg_loss = float(pnl[losses].mean()) if losses.any() else 0.0

    gross_profit = float(pnl[wins].sum()) if wins.any() else 0.0
    gross_loss = float(abs(pnl[losses].sum())) if losses.any() else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    expectancy_per_lakh = (expectancy / capital) * 100_000

    # Equity curve (cumulative P&L from starting capital)
    equity = capital + pd.Series(pnl).cumsum()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd = float(drawdown.min())

    # Annualised Sharpe on daily P&L grouping
    sharpe = _daily_sharpe(trades, pnl)

    # Exit reason breakdown
    if "exit_reason" in trades.columns:
        reason_counts = trades["exit_reason"].value_counts().to_dict()
    else:
        reason_counts = {}

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(min(profit_factor, 999.0), 4),
        "expectancy": round(expectancy, 2),
        "expectancy_per_lakh": round(expectancy_per_lakh, 2),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "total_pnl": round(float(pnl.sum()), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "equity_curve": [round(v, 2) for v in equity.tolist()],
        "exit_reasons": reason_counts,
    }


def _daily_sharpe(trades: pd.DataFrame, pnl: np.ndarray) -> float:
    """Annualised Sharpe ratio from daily grouped P&L."""
    if "exit_time" not in trades.columns or len(pnl) < 2:
        return 0.0
    try:
        exit_dates = pd.to_datetime(trades["exit_time"], utc=True).dt.normalize()
        daily = pd.Series(pnl, index=exit_dates).groupby(level=0).sum()
        if len(daily) < 2 or daily.std() == 0:
            return 0.0
        return float(daily.mean() / daily.std() * np.sqrt(252))
    except Exception:
        return 0.0


def _empty_metrics() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": 0.0,
        "expectancy": 0.0,
        "expectancy_per_lakh": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "total_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "equity_curve": [],
        "exit_reasons": {},
    }
