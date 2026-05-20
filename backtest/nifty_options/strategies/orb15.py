"""ORB-15: Opening Range Breakout (first 3 × 5-min bars = 15 minutes).

ORB-30 and FH-BO inherit from this class and just override `_bars`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import orb
from .base import Signal, Strategy

_MIN_RISK_PTS = 5.0   # discard signals with SL width < 5 NIFTY points


class ORB15Strategy(Strategy):
    """15-minute Opening Range Breakout on NIFTY 50 weekly options.

    Opening range : high/low of the first `_bars` × 5-min bars.
    Entry         : first close outside the range (CE above, PE below).
    Stop-loss     : opposite end of the opening range.
    Target        : entry ± ORB_width  (1:1 risk-reward in spot space).
    Frequency     : at most ONE signal per day (first breakout only).
    """

    name = "ORB-15"
    _bars: int = 3   # subclasses override: ORB-30 → 6, FH-BO → 12

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or len(df_5m) < self._bars + 1:
            return []

        orb_df = orb(df_5m, bars=self._bars)
        signals: list[Signal] = []
        emitted = False

        highs = df_5m["high"].values
        lows = df_5m["low"].values
        closes = df_5m["close"].values
        timestamps = df_5m.index
        complete = orb_df["complete"].values
        orb_high_arr = orb_df["orb_high"].values
        orb_low_arr = orb_df["orb_low"].values

        for i in range(len(df_5m)):
            if not complete[i]:
                continue
            if emitted:
                break

            orb_high = orb_high_arr[i]
            orb_low = orb_low_arr[i]
            orb_width = orb_high - orb_low
            close = closes[i]
            ts = timestamps[i]

            if close > orb_high and orb_width >= _MIN_RISK_PTS:
                signals.append(
                    Signal(
                        time=ts,
                        direction="CE",
                        spot_sl=orb_low,
                        spot_target=close + orb_width,
                        tag=f"ORB{self._bars * 5}-CE",
                    )
                )
                emitted = True
            elif close < orb_low and orb_width >= _MIN_RISK_PTS:
                signals.append(
                    Signal(
                        time=ts,
                        direction="PE",
                        spot_sl=orb_high,
                        spot_target=close - orb_width,
                        tag=f"ORB{self._bars * 5}-PE",
                    )
                )
                emitted = True

        return signals
