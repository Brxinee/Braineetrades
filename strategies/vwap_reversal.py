"""
VWAP Reversal
Entry: price deviates > 1% from VWAP, then reverts back toward VWAP.
Stop:  0.5 × ATR below entry (long) / above entry (short).
Target: VWAP touch.
Timeframe: 5-minute bars, intraday only.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Strategy, SignalResult


class VWAPReversal(Strategy):
    name = "VWAP Reversal"
    description = "Fade price when it extends >1% from VWAP; target VWAP touch."
    params = {
        "deviation_pct": 0.01,   # min distance from VWAP to consider fade
        "atr_period": 14,
        "atr_sl_mult": 0.5,      # stop = 0.5 ATR from entry
        "min_vol_ratio": 1.0,    # volume filter (relative to 20-bar avg)
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"]
        vwap = self.vwap(df)
        atr = self.atr(df, cfg["atr_period"])
        vol_avg = df["volume"].rolling(20).mean()

        dev = (close - vwap) / vwap          # positive = above vwap
        prev_dev = dev.shift(1)

        # Long entry: price was > +dev% above VWAP and now turns back below VWAP
        # (i.e., deviation crosses back through zero from above — catch the reversion)
        # We enter one bar after the extreme, once price starts moving toward VWAP.
        was_extended_below = prev_dev < -cfg["deviation_pct"]  # price was far below
        now_moving_up = close > close.shift(1)                 # current bar up
        vol_ok = df["volume"] >= vol_avg * cfg["min_vol_ratio"]
        squareoff = self.squareoff_mask(df)

        entries = was_extended_below & now_moving_up & vol_ok & ~squareoff
        entries = entries.fillna(False)

        # Exit when price touches VWAP or square-off
        at_vwap = (close >= vwap * 0.999) & (close <= vwap * 1.001)
        exits = (at_vwap | squareoff).fillna(False)

        sl = (close - cfg["atr_sl_mult"] * atr).where(entries, np.nan)
        target = vwap.where(entries, np.nan)

        return SignalResult(
            entries=entries,
            exits=exits,
            sl=sl,
            target=target,
            signal_meta={"indicator": "VWAP", "deviation_pct": cfg["deviation_pct"]},
        )
