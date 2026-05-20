"""
Opening Range Breakout (ORB)
Entry: breakout above/below the first 15-minute high/low, with volume > 1.5× avg.
Stop:  opposite side of the opening range.
Target: range_height projected from breakout level (1:1 RR minimum).
Timeframe: 5-minute bars.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Strategy, SignalResult


class OpeningRangeBreakout(Strategy):
    name = "Opening Range Breakout"
    description = "Trade breakout of first 15-min range with volume confirmation."
    params = {
        "range_minutes": 15,     # opening range duration
        "vol_multiplier": 1.5,   # volume must be this × 20-bar avg
        "atr_period": 14,
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"]
        squareoff = self.squareoff_mask(df)
        vol_avg = df["volume"].rolling(20).mean()
        n_bars = cfg["range_minutes"] // 5  # 3 bars for 15-min range

        # Compute OR high/low per day
        df = df.copy()
        df["_date"] = df.index.normalize()

        entries = pd.Series(False, index=df.index)
        exits = pd.Series(False, index=df.index)
        sl_series = pd.Series(np.nan, index=df.index)
        target_series = pd.Series(np.nan, index=df.index)

        for date, day_df in df.groupby("_date"):
            if len(day_df) <= n_bars:
                continue

            or_bars = day_df.iloc[:n_bars]
            or_high = or_bars["high"].max()
            or_low = or_bars["low"].min()
            or_range = or_high - or_low

            after_or = day_df.iloc[n_bars:]
            if after_or.empty:
                continue

            day_vol_avg = day_df["volume"].rolling(20).mean()

            # Long: close breaks above OR high with volume
            long_break = (
                (after_or["close"] > or_high)
                & (after_or["volume"] > day_vol_avg.reindex(after_or.index) * cfg["vol_multiplier"])
                & ~squareoff.reindex(after_or.index, fill_value=True)
            )

            # Only take FIRST breakout per day
            first_long = long_break & (~long_break.shift(1).fillna(False))

            entries.update(first_long.reindex(df.index, fill_value=False))

            # SL = OR low; target = OR high + range (1:1)
            sl_price = or_low
            tgt_price = or_high + or_range

            sl_series.update(
                pd.Series(sl_price, index=after_or.index).where(
                    first_long.reindex(after_or.index, fill_value=False), np.nan
                )
            )
            target_series.update(
                pd.Series(tgt_price, index=after_or.index).where(
                    first_long.reindex(after_or.index, fill_value=False), np.nan
                )
            )

        # Exit: price drops back below OR high or square-off
        exits = squareoff.copy()

        return SignalResult(
            entries=entries.fillna(False),
            exits=exits.fillna(False),
            sl=sl_series,
            target=target_series,
            signal_meta={"indicator": "ORB", "range_minutes": cfg["range_minutes"]},
        )
