"""VIX Regime ORB: ORB-15 gated by India VIX in the 12–20 range."""

from datetime import date

import pandas as pd

from .orb15 import ORB15Strategy
from .base import Signal

_VIX_MIN = 12.0
_VIX_MAX = 20.0


class VIXRegimeORBStrategy(ORB15Strategy):
    """ORB-15 that only trades when India VIX is between 12 and 20.

    Rationale:
    - VIX < 12  : options too cheap, premium insufficient for 1:1 R:R
    - VIX > 20  : excessive volatility, stop-outs too frequent
    """

    name = "VIX-ORB"

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        if not (_VIX_MIN <= vix <= _VIX_MAX):
            return []
        return super().generate_signals(df_5m, trade_date, vix)
