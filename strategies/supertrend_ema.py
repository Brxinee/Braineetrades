"""
Supertrend + EMA(20) Trend Follow
Entry:  Supertrend flips to bullish (direction 1→1 first bar) AND close > EMA(20).
Exit:   Supertrend flips to bearish OR square-off at 15:15 IST.
Stop:   Supertrend line value at entry.
Target: Dynamic — trail supertrend line (captured in exits, not fixed).
Timeframe: 5-minute bars.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Strategy, SignalResult


class SupertrendEMA(Strategy):
    name = "Supertrend + EMA"
    description = "Enter on Supertrend bullish flip when price is above EMA(20)."
    params = {
        "st_period": 7,
        "st_multiplier": 3.0,
        "ema_period": 20,
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"]
        ema20 = self.ema(close, cfg["ema_period"])
        st_line, direction = self.supertrend(df, cfg["st_period"], cfg["st_multiplier"])
        squareoff = self.squareoff_mask(df)

        # Entry: direction flips from -1 to 1 (first green bar) AND above EMA
        flipped_bullish = (direction == 1) & (direction.shift(1) == -1)
        above_ema = close > ema20
        entries = (flipped_bullish & above_ema & ~squareoff).fillna(False)

        # Exit: direction flips bearish OR square-off
        flipped_bearish = (direction == -1) & (direction.shift(1) == 1)
        exits = (flipped_bearish | squareoff).fillna(False)

        sl = st_line.where(entries, np.nan)
        # No fixed target — trail with supertrend; set target to NaN
        target = pd.Series(np.nan, index=df.index)

        return SignalResult(
            entries=entries,
            exits=exits,
            sl=sl,
            target=target,
            signal_meta={
                "indicator": "Supertrend",
                "st_period": cfg["st_period"],
                "st_multiplier": cfg["st_multiplier"],
                "ema_period": cfg["ema_period"],
            },
        )
