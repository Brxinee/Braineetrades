"""
Performance metrics computed from a trade log DataFrame.

Expected input: DataFrame with columns:
  entry_time, exit_time, symbol, entry_price, exit_price,
  qty, pnl, pnl_pct, side (always 'long' for now)
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def compute_metrics(trades: pd.DataFrame, capital: float = 100_000.0) -> dict:
    """
    Returns a flat dict of strategy-level metrics.
    trades: one row per closed trade.
    capital: starting capital in INR (used for return % calculations).
    """
    if trades.empty:
        return _empty_metrics()

    pnl = trades["pnl"].values
    wins = pnl > 0
    losses = pnl <= 0

    total_trades = len(trades)
    win_rate = float(wins.sum()) / total_trades
    avg_win = float(pnl[wins].mean()) if wins.any() else 0.0
    avg_loss = float(pnl[losses].mean()) if losses.any() else 0.0
    profit_factor = (
        float(pnl[wins].sum()) / abs(float(pnl[losses].sum()))
        if losses.any() and pnl[losses].sum() != 0
        else float("inf")
    )
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    # Equity curve
    equity = capital + pd.Series(pnl).cumsum()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd = float(drawdown.min())

    # Annualised Sharpe on daily P&L
    if "exit_time" in trades.columns:
        trades = trades.copy()
        trades["exit_date"] = pd.to_datetime(trades["exit_time"]).dt.normalize()
        daily_pnl = trades.groupby("exit_date")["pnl"].sum()
        if len(daily_pnl) > 1:
            ann_factor = np.sqrt(252)
            sharpe = float(daily_pnl.mean() / daily_pnl.std() * ann_factor)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Expectancy per ₹1L capital
    expectancy_per_lakh = expectancy / capital * 100_000

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 4),
        "expectancy": round(expectancy, 2),
        "expectancy_per_lakh": round(expectancy_per_lakh, 2),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "total_pnl": round(float(pnl.sum()), 2),
        "equity_curve": [round(v, 2) for v in equity.tolist()],
    }


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
        "equity_curve": [],
    }
