"""
VWAP Reversal (long-only)
─────────────────────────
Setup:
  Price closes > deviation_pct below VWAP on the previous bar.
  Current bar's close starts recovering (close > prev close) while
  still below VWAP — we're catching the inflection, not chasing.

Entry bar:  close > prev_close AND was_extended (prev deviation < -dev_pct)
            AND volume ≥ 1× 20-bar rolling average
            AND NOT in square-off window

Stop-loss:  entry_close − 0.5 × ATR(14)
Target:     VWAP value at entry bar (filled forward to track VWAP touch)

Exit:
  Price touches or crosses VWAP (close ≥ VWAP × 0.999)
  OR 15:15 IST square-off

Timeframe: 5-minute bars, IST-indexed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, SignalResult

_MIN_BARS = 20   # need 20 bars of history before firing any signal


class VWAPReversal(Strategy):
    name = "VWAP Reversal"
    description = "Fade price when it extends >1% below VWAP; target VWAP touch."
    params = {
        "deviation_pct": 0.01,    # 1% below VWAP to qualify
        "atr_period": 14,
        "atr_sl_mult": 0.5,       # stop = entry − 0.5 × ATR
        "vol_ratio": 1.0,         # min volume vs 20-bar rolling avg
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"]
        vwap = self.vwap(df)
        atr = self.atr(df, cfg["atr_period"])
        vol_avg = df["volume"].rolling(20, min_periods=5).mean()
        squareoff = self.squareoff_mask(df)

        # Signed deviation: negative = price below VWAP
        deviation = (close - vwap) / vwap.replace(0, np.nan)

        # Previous bar was extended below VWAP
        was_extended = deviation.shift(1) < -cfg["deviation_pct"]

        # Current bar shows recovery (green close, still below or at VWAP)
        recovering = (close > close.shift(1)) & (close <= vwap * 1.001)

        # Volume filter
        vol_ok = df["volume"] >= vol_avg * cfg["vol_ratio"]

        # Require at least _MIN_BARS of history (vol_avg not meaningful before)
        has_history = pd.Series(
            range(len(df)), index=df.index
        ) >= _MIN_BARS

        entries = (
            was_extended & recovering & vol_ok & has_history & ~squareoff
        ).fillna(False)

        # Exit: price reaches VWAP, or square-off
        at_vwap = close >= vwap * 0.999
        exits = (at_vwap | squareoff).fillna(False)

        # SL and target only set on entry bars
        sl = (close - cfg["atr_sl_mult"] * atr).where(entries, np.nan)
        target = vwap.where(entries, np.nan)

        return SignalResult(
            entries=entries,
            exits=exits,
            sl=sl,
            target=target,
            signal_meta={
                "indicator": "VWAP",
                "deviation_pct": cfg["deviation_pct"],
            },
        )
