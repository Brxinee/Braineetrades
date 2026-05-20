"""Inside Bar Breakout: trade breakouts from 5-min inside bars.

An inside bar has a high strictly less than the prior bar's high AND
a low strictly greater than the prior bar's low (fully contained range).

On the bar AFTER an inside bar:
  - If close > inside_bar_high → buy CE
  - If close < inside_bar_low  → buy PE
  - Stop: opposite end of the inside bar's range
  - Target: entry ± inside_bar_width  (1:1 risk-reward in spot space)

Multiple inside bars — and therefore multiple signals — can occur in
a single session.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import Signal, Strategy

_MIN_RISK_PTS = 5.0


class InsideBarBOStrategy(Strategy):
    name = "IB-BO"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or len(df_5m) < 3:
            return []

        highs = df_5m["high"].values
        lows = df_5m["low"].values
        closes = df_5m["close"].values
        timestamps = df_5m.index

        signals: list[Signal] = []

        # i iterates the "breakout" bar; i-1 is the potential inside bar; i-2 is the mother bar
        for i in range(2, len(df_5m)):
            ib_h = highs[i - 1]
            ib_l = lows[i - 1]
            mother_h = highs[i - 2]
            mother_l = lows[i - 2]

            # Confirm i-1 is an inside bar relative to i-2
            if ib_h >= mother_h or ib_l <= mother_l:
                continue

            c = closes[i]
            ts = timestamps[i]
            ib_width = ib_h - ib_l

            if c > ib_h and ib_width >= _MIN_RISK_PTS:
                signals.append(
                    Signal(
                        time=ts,
                        direction="CE",
                        spot_sl=ib_l,
                        spot_target=c + ib_width,
                        tag="IB-BO-CE",
                    )
                )
            elif c < ib_l and ib_width >= _MIN_RISK_PTS:
                signals.append(
                    Signal(
                        time=ts,
                        direction="PE",
                        spot_sl=ib_h,
                        spot_target=c - ib_width,
                        tag="IB-BO-PE",
                    )
                )

        return signals
