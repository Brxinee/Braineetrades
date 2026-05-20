"""
Opening Range Breakout (ORB) — long-only
─────────────────────────────────────────
Setup:
  Compute the Opening Range = first `range_minutes` of the session
  (09:15–09:30 IST for 15-min range → 3 bars of 5-minute data).

Entry:
  First bar after the range that CLOSES above OR high,
  AND volume on that bar > vol_multiplier × 20-bar rolling average,
  AND only one entry per symbol per session day.

Stop-loss:  OR low (opposite side of the range)
Target:     OR high + (OR high − OR low)  →  1:1 range projection

Exit:
  Fixed target hit (handled by engine via target price),
  OR price closes back below OR high (momentum failed),
  OR 15:15 IST square-off.

Timeframe: 5-minute bars, IST-indexed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, SignalResult

_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MIN = 15


class OpeningRangeBreakout(Strategy):
    name = "Opening Range Breakout"
    description = "Trade the first breakout of the 15-min opening range with volume confirmation."
    params = {
        "range_minutes": 15,      # length of opening range
        "vol_multiplier": 1.5,    # breakout bar volume vs 20-bar avg
        "min_range_pct": 0.002,   # skip if OR is too narrow (<0.2% of price)
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        n_range_bars = cfg["range_minutes"] // 5   # 3 bars for 15-min

        entries = pd.Series(False, index=df.index)
        exits = pd.Series(False, index=df.index)
        sl_series = pd.Series(np.nan, index=df.index)
        tgt_series = pd.Series(np.nan, index=df.index)

        squareoff = self.squareoff_mask(df)
        vol_avg = df["volume"].rolling(20, min_periods=5).mean()

        date_groups = df.groupby(df.index.normalize())

        for _date, day_df in date_groups:
            if len(day_df) <= n_range_bars:
                continue

            # Opening range bars
            or_bars = day_df.iloc[:n_range_bars]
            or_high = or_bars["high"].max()
            or_low = or_bars["low"].min()
            or_range = or_high - or_low

            # Skip degenerate/too-narrow ranges
            mid = (or_high + or_low) / 2
            if mid > 0 and (or_range / mid) < cfg["min_range_pct"]:
                continue

            # Post-range bars for this day
            post_or = day_df.iloc[n_range_bars:]
            if post_or.empty:
                continue

            day_vol_avg = vol_avg.reindex(post_or.index)
            day_squareoff = squareoff.reindex(post_or.index, fill_value=True)

            # Long breakout: close above OR high with volume
            long_break = (
                (post_or["close"] > or_high)
                & (post_or["volume"] > day_vol_avg * cfg["vol_multiplier"])
                & ~day_squareoff
            )

            # Take only the FIRST breakout bar per day
            first_break_idx = long_break[long_break].index
            if first_break_idx.empty:
                continue
            entry_idx = first_break_idx[0]

            entries.at[entry_idx] = True
            sl_series.at[entry_idx] = or_low
            tgt_series.at[entry_idx] = or_high + or_range

            # Intra-day exit: price closes back below OR high after entry
            post_entry = post_or.loc[post_or.index > entry_idx]
            if not post_entry.empty:
                failed = post_entry["close"] < or_high
                if failed.any():
                    exits.update(failed.reindex(df.index, fill_value=False))

        # Always exit at square-off
        exits = (exits | squareoff).fillna(False)

        return SignalResult(
            entries=entries.fillna(False),
            exits=exits,
            sl=sl_series,
            target=tgt_series,
            signal_meta={
                "indicator": "ORB",
                "range_minutes": cfg["range_minutes"],
            },
        )
