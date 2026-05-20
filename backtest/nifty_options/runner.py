"""CLI entry point for the NIFTY Options backtest suite.

Usage
-----
  python -m backtest.nifty_options.runner
  python -m backtest.nifty_options.runner --start 2022-01-01 --end 2024-12-31
  python -m backtest.nifty_options.runner --capital 500000 --out public/data/top5_config.json

All 10 strategy candidates are run; results ranked and written to --out.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .engine.simulator import run_strategy
from .ranking import rank_strategies, write_top5_config
from .strategies.first_hour_bo import FirstHourBOStrategy
from .strategies.gap_fade import GapFadeStrategy
from .strategies.gap_go import GapGoStrategy
from .strategies.inside_bar_bo import InsideBarBOStrategy
from .strategies.orb15 import ORB15Strategy
from .strategies.orb30 import ORB30Strategy
from .strategies.supertrend_ema import SupertrendEMAStrategy
from .strategies.vix_regime_orb import VIXRegimeORBStrategy
from .strategies.vwap_reclaim import VWAPReclaimStrategy
from .strategies.vwap_rejection import VWAPRejectionStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_STRATEGIES = [
    ORB15Strategy(),
    ORB30Strategy(),
    FirstHourBOStrategy(),
    VIXRegimeORBStrategy(),
    GapFadeStrategy(),
    GapGoStrategy(),
    VWAPReclaimStrategy(),
    VWAPRejectionStrategy(),
    SupertrendEMAStrategy(),
    InsideBarBOStrategy(),
]

_DEFAULT_START = date(2020, 1, 1)
_DEFAULT_END   = date(2025, 12, 31)
_DEFAULT_CAP   = 100_000.0
_DEFAULT_OUT   = Path("public/data/top5_config.json")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NIFTY Options backtest runner")
    p.add_argument("--start",   default=str(_DEFAULT_START), help="YYYY-MM-DD start date")
    p.add_argument("--end",     default=str(_DEFAULT_END),   help="YYYY-MM-DD end date")
    p.add_argument("--capital", type=float, default=_DEFAULT_CAP, help="Initial capital (₹)")
    p.add_argument("--out",     default=str(_DEFAULT_OUT),   help="Output JSON path")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    capital = args.capital
    out_path = Path(args.out)

    log.info("Backtest period : %s → %s", start, end)
    log.info("Initial capital : ₹%.0f", capital)
    log.info("Strategies      : %d", len(_STRATEGIES))

    results: dict[str, tuple[list, float]] = {}

    for strat in _STRATEGIES:
        log.info("── Running: %s", strat.name)
        try:
            trades, final_cap = run_strategy(strat, start, end, capital)
            results[strat.name] = (trades, final_cap)
            log.info(
                "   %s → %d trades, final_capital=₹%.0f",
                strat.name, len(trades), final_cap,
            )
        except Exception:
            log.exception("   %s failed — skipped", strat.name)

    if not results:
        log.error("All strategies failed — nothing to write.")
        sys.exit(1)

    ranked = rank_strategies(results, capital, start, end)

    log.info("── Rankings ──")
    for r in ranked:
        m = r["metrics"]
        log.info(
            "  #%d %-22s  score=%.4f  sharpe=%.2f  calmar=%.2f  dd=%.1f%%  ret=%.1f%%",
            r["rank"], r["name"], r["score"],
            m["sharpe"], m["calmar"], m["max_dd"] * 100, m["total_return_pct"],
        )

    write_top5_config(ranked, capital, start, end, out_path)
    log.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()
