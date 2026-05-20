"""
RSI Divergence (Bullish)
Setup:   Price makes a Lower Low while RSI makes a Higher Low on 5m bars.
Entry:   First confirmation candle after divergence (close > open, above prior close).
Stop:    Below the swing low that formed the price LL.
Target:  1.5 × risk (1.5 R).
Lookback: 5-20 bars to find divergence swing.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from .base import Strategy, SignalResult


class RSIDivergence(Strategy):
    name = "RSI Divergence"
    description = "Bullish divergence: price LL, RSI HL — enter on confirmation candle."
    params = {
        "rsi_period": 14,
        "lookback_min": 5,    # min bars between swing lows
        "lookback_max": 20,   # max bars between swing lows
        "rr_ratio": 1.5,      # target = entry + rr_ratio × (entry - sl)
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"]
        rsi = self.rsi(close, cfg["rsi_period"])
        squareoff = self.squareoff_mask(df)

        entries = pd.Series(False, index=df.index)
        sl_series = pd.Series(np.nan, index=df.index)
        target_series = pd.Series(np.nan, index=df.index)

        closes = close.values
        rsi_vals = rsi.values
        lows = df["low"].values

        lb_min = cfg["lookback_min"]
        lb_max = cfg["lookback_max"]

        for i in range(lb_max + 2, len(df) - 1):
            if squareoff.iloc[i]:
                continue

            # Look for previous swing low within lookback window
            window_lows = lows[i - lb_max: i - lb_min + 1]
            if len(window_lows) == 0:
                continue
            prev_idx_offset = int(np.argmin(window_lows))
            prev_idx = i - lb_max + prev_idx_offset

            # Bullish divergence: price LL, RSI HL
            price_ll = closes[i] < closes[prev_idx]
            rsi_hl = rsi_vals[i] > rsi_vals[prev_idx]

            if not (price_ll and rsi_hl):
                continue

            # Confirmation candle: next bar closes bullish
            if i + 1 >= len(df):
                continue
            conf_bar = i + 1
            if closes[conf_bar] > df["open"].iloc[conf_bar]:  # green candle
                entries.iloc[conf_bar] = True
                swing_low = lows[i]
                sl_price = swing_low - self.atr(df, 14).iloc[i] * 0.25
                risk = closes[conf_bar] - sl_price
                tgt_price = closes[conf_bar] + cfg["rr_ratio"] * risk
                sl_series.iloc[conf_bar] = sl_price
                target_series.iloc[conf_bar] = tgt_price

        exits = squareoff.copy().fillna(False)

        return SignalResult(
            entries=entries.fillna(False),
            exits=exits,
            sl=sl_series,
            target=target_series,
            signal_meta={"indicator": "RSI Divergence", "rsi_period": cfg["rsi_period"]},
        )
