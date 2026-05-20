"""
Supertrend + EMA(20) Trend Follow — long-only
──────────────────────────────────────────────
Entry:
  Bar where Supertrend flips from bearish (−1) to bullish (+1)
  AND close > EMA(20) on that bar.
  Only one entry per flip event (no re-entry until next flip cycle).

Stop-loss:  Supertrend line value on the entry bar.
Target:     None fixed — exit is signal-driven (trail via ST flip).

Exit:
  Supertrend flips from bullish to bearish (−1),
  OR 15:15 IST square-off.

Timeframe: 5-minute bars, IST-indexed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, SignalResult


class SupertrendEMA(Strategy):
    name = "Supertrend + EMA"
    description = "Enter on Supertrend bullish flip when price is above EMA(20); trail ST."
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

        # Flip detection: direction changes from −1 to +1
        prev_dir = direction.shift(1)
        bullish_flip = (direction == 1) & (prev_dir == -1)

        # Only enter if close is above EMA on the flip bar
        above_ema = close > ema20

        entries = (bullish_flip & above_ema & ~squareoff).fillna(False)

        # Exit: bearish flip OR square-off
        bearish_flip = (direction == -1) & (prev_dir == 1)
        exits = (bearish_flip | squareoff).fillna(False)

        # SL = ST line at entry; no fixed target (trail)
        sl = st_line.where(entries, np.nan)
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
