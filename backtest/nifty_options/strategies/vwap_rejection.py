"""VWAP Rejection: trade sharp reversals when price touches VWAP and fails.

Bullish rejection (buy CE):
  - Previous bar's close was above VWAP.
  - Current bar's low touches or crosses VWAP but close is back above VWAP.
  - Stop: current bar's low (VWAP touch low).
  - Target: entry + 1.5 × risk.

Bearish rejection (buy PE):
  - Previous bar's close was below VWAP.
  - Current bar's high touches or crosses VWAP but close is back below VWAP.
  - Stop: current bar's high (VWAP touch high).
  - Target: entry − 1.5 × risk.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import vwap
from .base import Signal, Strategy

_MIN_RISK_PTS = 5.0
_RR = 1.5   # risk-reward ratio


class VWAPRejectionStrategy(Strategy):
    name = "VWAP-Rejection"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or len(df_5m) < 2:
            return []

        vwap_vals = vwap(df_5m).values
        closes = df_5m["close"].values
        highs = df_5m["high"].values
        lows = df_5m["low"].values
        timestamps = df_5m.index

        signals: list[Signal] = []

        for i in range(1, len(df_5m)):
            v = vwap_vals[i]
            prev_c = closes[i - 1]
            prev_v = vwap_vals[i - 1]
            c = closes[i]
            h = highs[i]
            lo = lows[i]
            ts = timestamps[i]

            prev_above = prev_c > prev_v

            if prev_above and lo <= v and c > v:
                # Bullish rejection: dipped to VWAP, closed back above
                sl = lo
                risk = c - sl
                if risk >= _MIN_RISK_PTS:
                    signals.append(
                        Signal(
                            time=ts,
                            direction="CE",
                            spot_sl=sl,
                            spot_target=c + _RR * risk,
                            tag="VWAP-Rej-Bull",
                        )
                    )

            elif not prev_above and h >= v and c < v:
                # Bearish rejection: spiked to VWAP, closed back below
                sl = h
                risk = sl - c
                if risk >= _MIN_RISK_PTS:
                    signals.append(
                        Signal(
                            time=ts,
                            direction="PE",
                            spot_sl=sl,
                            spot_target=c - _RR * risk,
                            tag="VWAP-Rej-Bear",
                        )
                    )

        return signals
