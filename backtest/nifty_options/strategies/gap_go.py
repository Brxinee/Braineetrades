"""Gap & Go: ride large overnight gaps using ORB-15 direction filter.

Gap up  (>0.5%) → momentum continuation → take CE breakouts of ORB only.
Gap down (<-0.5%) → momentum continuation → take PE breakouts of ORB only.
No trade when gap < 0.5% or prev_close is unavailable.
"""

from datetime import date

import pandas as pd

from .base import Signal
from .orb15 import ORB15Strategy

_GAP_THRESHOLD = 0.005   # 0.5 %


class GapGoStrategy(ORB15Strategy):
    """Follow significant overnight gaps using the ORB-15 framework."""

    name = "Gap-Go"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if df_5m.empty or self.prev_close is None:
            return []

        today_open = float(df_5m.iloc[0]["open"])
        gap_pct = (today_open - self.prev_close) / self.prev_close

        if abs(gap_pct) < _GAP_THRESHOLD:
            return []

        all_sigs = super().generate_signals(df_5m, trade_date, vix)

        # Follow: take signals in the SAME direction as the gap
        if gap_pct > 0:
            return [s for s in all_sigs if s.direction == "CE"]
        else:
            return [s for s in all_sigs if s.direction == "PE"]
