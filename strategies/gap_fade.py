"""
Gap Fade
Setup:  Stock gaps up or down at open by 0.8%–2.0% vs previous close.
Entry:  First 5-min bar after open (9:15–9:20 IST) — fade direction.
Filter: Skip if gap > 2% (likely trend gap, not mean-reversion candidate).
Target: Previous close (gap fill).
Stop:   1.5× gap size beyond the open (gives room, avoids early exit).
Timeframe: 5-minute bars, entry only on first bar of session.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Strategy, SignalResult

_MARKET_OPEN = pd.Timestamp("09:15").time()
_FIRST_BAR_END = pd.Timestamp("09:20").time()

GAP_MIN = 0.008   # 0.8%
GAP_MAX = 0.02    # 2.0%


class GapFade(Strategy):
    name = "Gap Fade"
    description = "Fade 0.8–2% gaps at open, target previous close."
    params = {
        "gap_min_pct": GAP_MIN,
        "gap_max_pct": GAP_MAX,
        "sl_gap_multiplier": 1.5,  # SL = open ± (gap_size × mult)
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        entries = pd.Series(False, index=df.index)
        exits = pd.Series(False, index=df.index)
        sl_series = pd.Series(np.nan, index=df.index)
        target_series = pd.Series(np.nan, index=df.index)

        squareoff = self.squareoff_mask(df)

        df_copy = df.copy()
        df_copy["_date"] = df_copy.index.normalize()

        prev_close: float | None = None

        for date, day_df in df_copy.groupby("_date"):
            if prev_close is None:
                # Need previous day's close; take last close of this day for next iteration
                prev_close = day_df["close"].iloc[-1]
                continue

            first_bar = day_df.iloc[0]
            bar_time = first_bar.name.time()

            # Only process the market-open bar
            if bar_time != _MARKET_OPEN:
                prev_close = day_df["close"].iloc[-1]
                continue

            gap_pct = (first_bar["open"] - prev_close) / prev_close  # signed
            abs_gap = abs(gap_pct)

            if cfg["gap_min_pct"] <= abs_gap <= cfg["gap_max_pct"]:
                idx = first_bar.name
                gap_size = abs(first_bar["open"] - prev_close)

                if gap_pct > 0:
                    # Gap UP → fade (go short, but we model longs only; skip)
                    # For long-only backtest, skip gap-up fades
                    pass
                else:
                    # Gap DOWN → fade up (buy at open, target prev close)
                    entries.at[idx] = True
                    sl_series.at[idx] = first_bar["open"] - gap_size * cfg["sl_gap_multiplier"]
                    target_series.at[idx] = prev_close

            prev_close = day_df["close"].iloc[-1]

        exits = squareoff.copy().fillna(False)

        return SignalResult(
            entries=entries.fillna(False),
            exits=exits,
            sl=sl_series,
            target=target_series,
            signal_meta={
                "indicator": "Gap Fade",
                "gap_min_pct": cfg["gap_min_pct"],
                "gap_max_pct": cfg["gap_max_pct"],
            },
        )
