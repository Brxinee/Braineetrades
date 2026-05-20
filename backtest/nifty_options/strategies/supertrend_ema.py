"""Supertrend + EMA: enter on Supertrend direction flip confirmed by EMA.

Long (buy CE):
  - Supertrend flips from bearish → bullish  (signal == 'buy')
  - Close is above EMA(9)
  - Stop: Supertrend line value at signal bar
  - Target: entry + 1.5 × (entry − SL)

Short (buy PE):
  - Supertrend flips from bullish → bearish  (signal == 'sell')
  - Close is below EMA(9)
  - Stop: Supertrend line value at signal bar
  - Target: entry − 1.5 × (SL − entry)

Supertrend parameters: period=7, multiplier=3 (default).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import ema, supertrend
from .base import Signal, Strategy

_EMA_PERIOD = 9
_ST_PERIOD = 7
_ST_MULT = 3.0
_RR = 1.5
_MIN_RISK_PTS = 5.0


class SupertrendEMAStrategy(Strategy):
    name = "ST+EMA"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        # Supertrend needs at least period+1 bars to initialise
        if df_5m.empty or len(df_5m) < _ST_PERIOD + 5:
            return []

        st_df = supertrend(df_5m, period=_ST_PERIOD, multiplier=_ST_MULT)
        ema9 = ema(df_5m["close"], period=_EMA_PERIOD)

        closes = df_5m["close"].values
        timestamps = df_5m.index
        st_vals = st_df["supertrend"].values
        st_sigs = st_df["signal"].values
        ema_vals = ema9.values

        signals: list[Signal] = []

        for i in range(len(df_5m)):
            sig = st_sigs[i]
            if sig == "":
                continue

            c = closes[i]
            st = st_vals[i]
            e9 = ema_vals[i]
            ts = timestamps[i]

            if sig == "buy" and c > e9:
                # Bullish flip confirmed above EMA
                sl = st
                risk = c - sl
                if risk >= _MIN_RISK_PTS:
                    signals.append(
                        Signal(
                            time=ts,
                            direction="CE",
                            spot_sl=sl,
                            spot_target=c + _RR * risk,
                            tag="ST+EMA-Bull",
                        )
                    )

            elif sig == "sell" and c < e9:
                # Bearish flip confirmed below EMA
                sl = st
                risk = sl - c
                if risk >= _MIN_RISK_PTS:
                    signals.append(
                        Signal(
                            time=ts,
                            direction="PE",
                            spot_sl=sl,
                            spot_target=c - _RR * risk,
                            tag="ST+EMA-Bear",
                        )
                    )

        return signals
