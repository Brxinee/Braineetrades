"""
CLI runner for backtests.

Usage:
  python -m backtest.runner --strategy all
  python -m backtest.runner --strategy vwap_reversal --days 30
  python -m backtest.runner --strategy opening_range_breakout --capital 500000
  python -m backtest.runner --strategy all --quick   # 5 liquid symbols, 14 days
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies import REGISTRY
from backtest.engine import run_backtest

IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = ROOT / "public" / "data"
RESULTS_DIR = DATA_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
NIFTY50_JSON = DATA_DIR / "nifty50.json"

# Liquid large-caps used in --quick mode (fast CI runs)
QUICK_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "INFY.NS", "SBIN.NS", "BHARTIARTL.NS", "LT.NS",
    "BAJFINANCE.NS", "HCLTECH.NS",
]


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
    print(f"Strategy : {strategy.name}")
    print(f"Period   : {start} → {end}")
    print(f"Symbols  : {len(symbols)}")
    print(f"Capital  : ₹{capital:,.0f}")
    print(f"{'='*60}")

    result = run_backtest(strategy, symbols, start, end, capital)

    out_path = RESULTS_DIR / f"{strategy_key}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    s = result["summary"]
    print(f"\n  Saved   → {out_path.relative_to(ROOT)}")
    print(f"  Trades  : {s['total_trades']}")
    print(f"  Win rate: {s['win_rate']:.1%}")
    print(f"  Sharpe  : {s['sharpe']:.2f}")
    print(f"  Max DD  : {s['max_drawdown']:.1%}")
    print(f"  P&L     : ₹{s['total_pnl']:,.0f}")
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
            {"leaderboard": board, "generated_at": datetime.now(IST).isoformat()},
            f,
            indent=2,
        )
    print(f"\n  Leaderboard → {lb_path.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description="Brainee Trades Backtest Runner")
    parser.add_argument(
        "--strategy",
        default="all",
        choices=list(REGISTRY.keys()) + ["all"],
        help="Strategy to run (default: all)",
    )
    parser.add_argument("--days", type=int, default=60,
                        help="Lookback days (max 60 for 5m via yfinance, default: 60)")
    parser.add_argument("--start", default=None, help="Override start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Override end date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100_000.0,
                        help="Starting capital INR (default: 100000)")
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Override symbol list (space-separated .NS tickers)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: 10 liquid symbols, 14 days (for CI)")
    args = parser.parse_args()

    now = datetime.now(IST)
    if args.quick:
        symbols = QUICK_SYMBOLS
        days = 14
    else:
        symbols = args.symbols or load_symbols()
        days = args.days

    end = args.end or now.strftime("%Y-%m-%d")
    start = args.start or (now - timedelta(days=days)).strftime("%Y-%m-%d")

    strategies_to_run = (
        list(REGISTRY.keys()) if args.strategy == "all" else [args.strategy]
    )

    all_results: dict[str, dict] = {}
    for key in strategies_to_run:
        result = run_and_save(key, symbols, start, end, args.capital)
        all_results[key] = result

    build_leaderboard(all_results)
    print("\nDone.\n")


if __name__ == "__main__":
    main()
