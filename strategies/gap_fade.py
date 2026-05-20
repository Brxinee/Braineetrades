"""
Gap Fade — long-only (gap-down fades only)
───────────────────────────────────────────
Setup:
  At market open (09:15 IST), if today's open is below yesterday's close
  by gap_min_pct–gap_max_pct, the stock is likely to mean-revert.

  Gap-up fades are skipped (would require shorting).
  Gap > gap_max_pct = trend-gap, skip (too dangerous to fade).

Entry:
  On the OPEN of the first 5-min bar (09:15–09:20 IST).
  We record the entry at that bar's open price.

Stop-loss:  open − sl_gap_mult × gap_amount
            (if gap is 1%, SL is 1.5% below open = 0.5% extra cushion)

Target:     previous day's close (full gap fill)

Exit:
  Target hit (price reaches prev close),
  OR price closes below stop-loss bar,
  OR 15:15 IST square-off.

Filter: Skip days where the broad market (index) is in a strong trend
        (not implemented here — can be added via a universe-level filter).

Timeframe: 5-minute bars, IST-indexed.
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from .base import Strategy, SignalResult

_OPEN_TIME = datetime.time(9, 15)


class GapFade(Strategy):
    name = "Gap Fade"
    description = "Fade 0.8–2% gap-down opens; target previous close."
    params = {
        "gap_min_pct": 0.008,       # 0.8% minimum gap to trade
        "gap_max_pct": 0.020,       # 2.0% maximum — beyond this it's a trend gap
        "sl_gap_mult": 1.5,         # SL = open − mult × gap_amount
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        squareoff = self.squareoff_mask(df)

        entries = pd.Series(False, index=df.index)
        sl_series = pd.Series(np.nan, index=df.index)
        tgt_series = pd.Series(np.nan, index=df.index)

        # Group by date; carry previous day's close
        prev_close: float | None = None
        date_groups = list(df.groupby(df.index.normalize()))

        for _date, day_df in date_groups:
            if prev_close is None:
                prev_close = float(day_df["close"].iloc[-1])
                continue

            first_bar = day_df.iloc[0]
            bar_time = first_bar.name.time()

            # Only act on the first bar of the session
            if bar_time != _OPEN_TIME:
                prev_close = float(day_df["close"].iloc[-1])
                continue

            bar_open = float(first_bar["open"])
            gap_pct = (bar_open - prev_close) / prev_close   # negative = gap down
            abs_gap = abs(gap_pct)

            if (
                gap_pct < 0                           # gap down only
                and cfg["gap_min_pct"] <= abs_gap <= cfg["gap_max_pct"]
                and not squareoff.at[first_bar.name]
            ):
                gap_amount = prev_close - bar_open    # positive number
                sl_price = bar_open - cfg["sl_gap_mult"] * gap_amount
                tgt_price = prev_close

                entries.at[first_bar.name] = True
                sl_series.at[first_bar.name] = sl_price
                tgt_series.at[first_bar.name] = tgt_price

            prev_close = float(day_df["close"].iloc[-1])

        exits = squareoff.fillna(False)

        return SignalResult(
            entries=entries.fillna(False),
            exits=exits,
            sl=sl_series,
            target=tgt_series,
            signal_meta={
                "indicator": "Gap Fade",
                "gap_min_pct": cfg["gap_min_pct"],
                "gap_max_pct": cfg["gap_max_pct"],
            },
        )
