"""
RSI Divergence (bullish hidden — long-only)
───────────────────────────────────────────
Setup:
  Find two swing lows in a lookback window.
  Bullish divergence: price makes Lower Low (LL) but RSI makes Higher Low (HL).
  This means momentum is improving even as price retests lows — a reversal signal.

Swing low detection:
  A bar at index i is a swing low if low[i] < low[i−1] and low[i] < low[i+1]
  (simple 1-bar pivot). We scan the lookback window for the most recent pair.

Entry:
  The bar AFTER the divergence pivot if it is a bullish confirmation candle
  (close > open, close > prior close).
  Max one entry per detected divergence swing.

Stop-loss:  swing_low − 0.25 × ATR(14)
Target:     entry_price + rr_ratio × (entry_price − stop_loss)

Exit:
  Target hit (tracked by engine), OR 15:15 IST square-off.

Timeframe: 5-minute bars, IST-indexed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, SignalResult

_WARMUP = 30   # bars needed before we start scanning


class RSIDivergence(Strategy):
    name = "RSI Divergence"
    description = "Bullish divergence: price LL + RSI HL; enter on confirmation candle."
    params = {
        "rsi_period": 14,
        "lookback": 20,      # bars to look back for the first swing low
        "min_gap": 5,        # minimum bars between the two swing lows
        "rr_ratio": 1.5,     # target = entry + rr × risk
    }

    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        cfg = self.cfg
        close = df["close"].values
        low = df["low"].values
        open_ = df["open"].values
        rsi_vals = self.rsi(df["close"], cfg["rsi_period"]).values
        atr_vals = self.atr(df, 14).values
        squareoff = self.squareoff_mask(df)

        n = len(df)
        entries = np.zeros(n, dtype=bool)
        sl_arr = np.full(n, np.nan)
        tgt_arr = np.full(n, np.nan)

        lb = cfg["lookback"]
        min_gap = cfg["min_gap"]
        rr = cfg["rr_ratio"]

        # Pre-compute pivot swing lows: low[i] < low[i-1] and low[i] < low[i+1]
        pivot_low = np.zeros(n, dtype=bool)
        for i in range(1, n - 1):
            if low[i] < low[i - 1] and low[i] < low[i + 1]:
                pivot_low[i] = True

        used_pivots: set[int] = set()

        for i in range(_WARMUP + lb, n - 1):
            if squareoff.iloc[i]:
                continue

            # Current bar must be a pivot low
            if not pivot_low[i]:
                continue
            if i in used_pivots:
                continue

            # Search for a prior pivot low in [i - lb, i - min_gap]
            window_start = max(_WARMUP, i - lb)
            window_end = i - min_gap

            if window_end <= window_start:
                continue

            # Find all prior pivot lows in window
            prior_pivots = [
                j for j in range(window_start, window_end + 1)
                if pivot_low[j] and j not in used_pivots
            ]
            if not prior_pivots:
                continue

            # Use the most recent prior pivot
            j = prior_pivots[-1]

            # Bullish divergence: price LL, RSI HL
            price_ll = low[i] < low[j]
            rsi_hl = rsi_vals[i] > rsi_vals[j]

            if not (price_ll and rsi_hl):
                continue

            # Confirmation bar = i + 1 must be a bullish candle
            conf = i + 1
            if conf >= n:
                continue
            if squareoff.iloc[conf]:
                continue

            is_bullish_candle = close[conf] > open_[conf] and close[conf] > close[i]
            if not is_bullish_candle:
                continue

            sl_price = low[i] - 0.25 * atr_vals[i]
            risk = close[conf] - sl_price
            if risk <= 0:
                continue

            entries[conf] = True
            sl_arr[conf] = sl_price
            tgt_arr[conf] = close[conf] + rr * risk

            used_pivots.add(i)
            used_pivots.add(j)

        sq_arr = squareoff.values
        exits = sq_arr.copy()   # always exit at square-off

        return SignalResult(
            entries=pd.Series(entries, index=df.index),
            exits=pd.Series(exits, index=df.index),
            sl=pd.Series(sl_arr, index=df.index),
            target=pd.Series(tgt_arr, index=df.index),
            signal_meta={
                "indicator": "RSI Divergence",
                "rsi_period": cfg["rsi_period"],
            },
        )
