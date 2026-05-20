"""VWAP Reclaim: buy CE when spot reclaims VWAP after dipping below it.

Setup   : ≥2 consecutive bars with close below VWAP (meaningful dip).
Entry   : first bar that closes back above VWAP after the dip.
Stop    : lowest low recorded during the below-VWAP phase.
Target  : entry + (entry − SL)  [1:1 risk-reward].
Multiple signals per day are allowed (each reclaim is independent).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..indicators import vwap
from .base import Signal, Strategy

_MIN_BARS_BELOW = 2     # require at least N bars below VWAP before reclaim
_MIN_RISK_PTS = 5.0


class VWAPReclaimStrategy(Strategy):
    name = "VWAP-Reclaim"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or len(df_5m) < _MIN_BARS_BELOW + 1:
            return []

        vwap_vals = vwap(df_5m).values
        closes = df_5m["close"].values
        lows = df_5m["low"].values
        timestamps = df_5m.index

        signals: list[Signal] = []
        was_below = False
        bars_below = 0
        min_low = np.inf

        for i in range(len(df_5m)):
            c = closes[i]
            v = vwap_vals[i]
            lo = lows[i]

            if c < v:
                was_below = True
                bars_below += 1
                min_low = min(min_low, lo)
            elif c > v:
                if was_below and bars_below >= _MIN_BARS_BELOW:
                    sl = min_low
                    risk = c - sl
                    if risk >= _MIN_RISK_PTS:
                        signals.append(
                            Signal(
                                time=timestamps[i],
                                direction="CE",
                                spot_sl=sl,
                                spot_target=c + risk,
                                tag="VWAP-Reclaim",
                            )
                        )
                # Reset whether or not we emitted a signal
                was_below = False
                bars_below = 0
                min_low = np.inf

        return signals
