"""Composite ranking engine and top5_config.json writer.

Ranking formula (applied on normalised metrics):
  score = 0.30 × Sharpe  + 0.20 × Calmar  + 0.15 × profit_factor
        + 0.10 × win_rate − 0.15 × max_dd  + 0.10 × expectancy_pct

Each metric is clipped and scaled to [0, 1] before weighting so that
metrics with different natural units (₹, ratio, %) contribute equally.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytz

from .engine.trade import Trade

IST = pytz.timezone("Asia/Kolkata")

# ── normalisation ranges (calibrated for NIFTY intraday options) ─────────────
_NORM: dict[str, tuple[float, float]] = {
    "sharpe":         (-1.0, 3.0),   # good intraday Sharpe ~0.5–2.5
    "calmar":         ( 0.0, 5.0),   # good Calmar ~0.5–3.0
    "profit_factor":  ( 0.0, 3.0),   # good PF ~1.2–2.5
    "win_rate":       ( 0.0, 1.0),   # already [0, 1]
    "max_dd":         ( 0.0, 0.50),  # 50 % drawdown = worst end of scale
    "exp_pct":        (-5.0, 10.0),  # expectancy as % of initial capital
}

_WEIGHTS: dict[str, float] = {
    "sharpe":        0.30,
    "calmar":        0.20,
    "profit_factor": 0.15,
    "win_rate":      0.10,
    "max_dd":       -0.15,   # subtracted — higher DD = lower score
    "exp_pct":       0.10,
}


def _norm(value: float, lo: float, hi: float) -> float:
    """Clip-and-normalise `value` to [0, 1] within [lo, hi]."""
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


# ── metric computation ────────────────────────────────────────────────────────

def compute_metrics(
    trades: list[Trade],
    initial_capital: float,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Return a metrics dict for a completed strategy run.

    Keys
    ----
    total_trades, win_trades, loss_trades, win_rate,
    avg_win, avg_loss, expectancy, profit_factor,
    sharpe, calmar, max_dd, cagr,
    final_capital, total_return_pct,
    equity_curve, equity_dates
    """
    if not trades:
        return _empty_metrics()

    pnls = [t.pnl for t in trades]
    n = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / n
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else float("inf")

    # ── daily P&L → equity curve ──────────────────────────────────────────────
    df_t = pd.DataFrame({"date": pd.to_datetime([t.date for t in trades]), "pnl": pnls})
    daily_pnl = df_t.groupby("date")["pnl"].sum()
    bdays = pd.bdate_range(start=str(start), end=str(end))
    daily_pnl = daily_pnl.reindex(bdays, fill_value=0.0)
    equity = initial_capital + daily_pnl.cumsum()
    final_capital = float(equity.iloc[-1])

    # ── drawdown ──────────────────────────────────────────────────────────────
    peak = equity.cummax()
    dd = (peak - equity) / peak.where(peak > 0, other=np.nan)
    max_dd = float(dd.max(skipna=True))

    # ── Sharpe (annualised, using daily returns) ──────────────────────────────
    daily_ret = daily_pnl / initial_capital
    std = float(daily_ret.std())
    sharpe = float(daily_ret.mean() / std * np.sqrt(252)) if std > 0 else 0.0

    # ── Calmar ────────────────────────────────────────────────────────────────
    n_years = max((end - start).days / 365.0, 0.1)
    cagr = (final_capital / initial_capital) ** (1.0 / n_years) - 1.0
    calmar = cagr / max_dd if max_dd > 1e-6 else cagr * 10.0  # cap at 10× if no DD

    return {
        "total_trades":    n,
        "win_trades":      len(wins),
        "loss_trades":     len(losses),
        "win_rate":        round(win_rate, 4),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "expectancy":      round(expectancy, 2),
        "profit_factor":   round(min(profit_factor, 99.9), 3),
        "sharpe":          round(sharpe, 4),
        "calmar":          round(calmar, 4),
        "max_dd":          round(max_dd, 4),
        "cagr":            round(cagr, 4),
        "final_capital":   round(final_capital, 2),
        "total_return_pct": round((final_capital / initial_capital - 1) * 100, 2),
        # weekly-sampled equity to keep JSON compact (one point per 5 bdays)
        "equity_curve":    [round(v, 2) for v in equity.iloc[::5].values.tolist()],
        "equity_dates":    [d.strftime("%Y-%m-%d") for d in equity.iloc[::5].index],
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_trades": 0, "win_trades": 0, "loss_trades": 0,
        "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "expectancy": 0.0, "profit_factor": 0.0,
        "sharpe": 0.0, "calmar": 0.0, "max_dd": 0.0, "cagr": 0.0,
        "final_capital": 0.0, "total_return_pct": 0.0,
        "equity_curve": [], "equity_dates": [],
    }


# ── composite score ───────────────────────────────────────────────────────────

def composite_score(metrics: dict[str, Any], initial_capital: float) -> float:
    """Compute weighted composite score.  Higher = better strategy.

    Range is roughly [−0.15, 0.85] (can be negative for bad strategies).
    """
    exp_pct = metrics["expectancy"] / initial_capital * 100
    inputs = {
        "sharpe":        metrics["sharpe"],
        "calmar":        metrics["calmar"],
        "profit_factor": metrics["profit_factor"],
        "win_rate":      metrics["win_rate"],
        "max_dd":        metrics["max_dd"],
        "exp_pct":       exp_pct,
    }
    score = sum(_WEIGHTS[k] * _norm(v, *_NORM[k]) for k, v in inputs.items())
    return round(float(score), 6)


# ── ranking ───────────────────────────────────────────────────────────────────

def rank_strategies(
    results: dict[str, tuple[list[Trade], float]],
    initial_capital: float,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Compute metrics and composite scores; return list sorted descending."""
    ranked = []
    for name, (trades, _) in results.items():
        m = compute_metrics(trades, initial_capital, start, end)
        score = composite_score(m, initial_capital)
        ranked.append({"name": name, "score": score, "metrics": m})

    ranked.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return ranked


# ── JSON output ───────────────────────────────────────────────────────────────

def write_top5_config(
    ranked: list[dict[str, Any]],
    initial_capital: float,
    start: date,
    end: date,
    path: Path,
) -> None:
    """Write top-5 config JSON consumed by the live site leaderboard.

    Structure
    ---------
    {
      generated_at, backtest_period, initial_capital,
      strategies: [ top-5 with full metrics + equity_curve ],
      all_strategies: [ all 10 summary rows for leaderboard table ]
    }
    """
    top5 = ranked[:5]

    payload: dict[str, Any] = {
        "generated_at":    datetime.now(IST).isoformat(),
        "backtest_period": {"start": start.isoformat(), "end": end.isoformat()},
        "initial_capital": initial_capital,
        "strategies": [
            {
                "rank":  r["rank"],
                "name":  r["name"],
                "score": r["score"],
                "metrics": {
                    k: v for k, v in r["metrics"].items()
                    if k not in ("equity_curve", "equity_dates")
                },
                "equity_curve": r["metrics"]["equity_curve"],
                "equity_dates": r["metrics"]["equity_dates"],
            }
            for r in top5
        ],
        "all_strategies": [
            {
                "rank":             r["rank"],
                "name":             r["name"],
                "score":            r["score"],
                "total_trades":     r["metrics"]["total_trades"],
                "win_rate":         r["metrics"]["win_rate"],
                "sharpe":           r["metrics"]["sharpe"],
                "calmar":           r["metrics"]["calmar"],
                "profit_factor":    r["metrics"]["profit_factor"],
                "max_dd":           r["metrics"]["max_dd"],
                "total_return_pct": r["metrics"]["total_return_pct"],
                "expectancy":       r["metrics"]["expectancy"],
            }
            for r in ranked
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
