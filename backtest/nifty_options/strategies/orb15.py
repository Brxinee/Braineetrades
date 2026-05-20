"""ORB-15: Opening Range Breakout (first 15 minutes = first 3 × 5-min bars)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import orb
from .base import Signal, Strategy


class ORB15Strategy(Strategy):
    """15-minute Opening Range Breakout on NIFTY 50 weekly options.

    Rules
    -----
    Opening range : high and low of the first 3 × 5-min bars (9:15–9:30 IST).
    Entry         : first bar (from 9:30 onward) that closes above the ORB
                    high (buy CE) or below the ORB low (buy PE).
    Stop-loss     : opposite end of the opening range.
    Target        : entry_spot ± ORB_width  (1:1 risk-reward in spot space).
    Frequency     : at most ONE signal per day (first breakout only).
    """

    name = "ORB-15"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or len(df_5m) < 4:
            return []

        orb_df = orb(df_5m, bars=3)
        signals: list[Signal] = []
        emitted = False

        for ts, row in df_5m.iterrows():
            if not orb_df.loc[ts, "complete"]:
                continue          # still inside the 3-bar opening range
            if emitted:
                break             # only the first breakout fires

            orb_high = orb_df.loc[ts, "orb_high"]
            orb_low = orb_df.loc[ts, "orb_low"]
            orb_width = orb_high - orb_low
            close = row["close"]

            if close > orb_high:
                signals.append(
                    Signal(
                        time=ts,
                        direction="CE",
                        spot_sl=orb_low,
                        spot_target=close + orb_width,
                        tag=f"ORB15-CE range={orb_low:.0f}/{orb_high:.0f}",
                    )
                )
                emitted = True
            elif close < orb_low:
                signals.append(
                    Signal(
                        time=ts,
                        direction="PE",
                        spot_sl=orb_high,
                        spot_target=close - orb_width,
                        tag=f"ORB15-PE range={orb_low:.0f}/{orb_high:.0f}",
                    )
                )
                emitted = True

        return signals
