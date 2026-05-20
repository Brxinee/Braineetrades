"""
CLI runner for backtests.

Usage:
  python -m backtest.runner --strategy all
  python -m backtest.runner --strategy vwap_reversal --start 2025-03-01 --end 2025-05-01
  python -m backtest.runner --strategy opening_range_breakout --capital 500000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

# Ensure repo root is on path when run as __main__
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import REGISTRY
from backtest.engine import run_backtest

IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = ROOT / "public" / "data"
RESULTS_DIR = DATA_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

NIFTY50_JSON = DATA_DIR / "nifty50.json"


def load_symbols() -> list[str]:
    with open(NIFTY50_JSON) as f:
        data = json.load(f)
    return [s["symbol"] for s in data["universe"]]


def run_and_save(
    strategy_key: str,
    symbols: list[str],
    start: str,
    end: str,
    capital: float,
) -> dict:
    StrategyClass = REGISTRY[strategy_key]
    strategy = StrategyClass()
    print(f"\n{'='*60}")
    print(f"Running: {strategy.name} | {start} → {end} | {len(symbols)} symbols")
    print(f"{'='*60}")

    result = run_backtest(strategy, symbols, start, end, capital)

    out_path = RESULTS_DIR / f"{strategy_key}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n[OK] Saved → {out_path}")

    s = result["summary"]
    print(
        f"  Trades: {s['total_trades']} | "
        f"Win Rate: {s['win_rate']:.1%} | "
        f"Sharpe: {s['sharpe']:.2f} | "
        f"Max DD: {s['max_drawdown']:.1%} | "
        f"P&L: ₹{s['total_pnl']:,.0f}"
    )
    return result


def build_leaderboard(results: dict[str, dict]) -> None:
    board = []
    for key, result in results.items():
        s = result.get("summary", {})
        board.append({
            "strategy_key": key,
            "strategy_name": result.get("strategy", key),
            "sharpe": s.get("sharpe", 0),
            "win_rate": s.get("win_rate", 0),
            "profit_factor": s.get("profit_factor", 0),
            "max_drawdown": s.get("max_drawdown", 0),
            "expectancy_per_lakh": s.get("expectancy_per_lakh", 0),
            "total_trades": s.get("total_trades", 0),
            "total_pnl": s.get("total_pnl", 0),
        })
    board.sort(key=lambda x: x["sharpe"], reverse=True)

    lb_path = DATA_DIR / "leaderboard.json"
    with open(lb_path, "w") as f:
        json.dump(
            {
                "leaderboard": board,
                "generated_at": datetime.now(IST).isoformat(),
            },
            f,
            indent=2,
        )
    print(f"\n[OK] Leaderboard → {lb_path}")


def main():
    parser = argparse.ArgumentParser(description="Brainee Trades Backtest Runner")
    parser.add_argument(
        "--strategy",
        default="all",
        choices=list(REGISTRY.keys()) + ["all"],
        help="Strategy to run (default: all)",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Start date YYYY-MM-DD (default: 60 days ago)",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Starting capital in INR (default: 100000)",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Override symbol list (space-separated .NS tickers)",
    )
    args = parser.parse_args()

    now_ist = datetime.now(IST)
    end = args.end or now_ist.strftime("%Y-%m-%d")
    start = args.start or (now_ist - timedelta(days=60)).strftime("%Y-%m-%d")
    symbols = args.symbols or load_symbols()

    strategies_to_run = list(REGISTRY.keys()) if args.strategy == "all" else [args.strategy]

    all_results: dict[str, dict] = {}
    for key in strategies_to_run:
        result = run_and_save(key, symbols, start, end, args.capital)
        all_results[key] = result

    build_leaderboard(all_results)
    print("\nDone.")


if __name__ == "__main__":
    main()
