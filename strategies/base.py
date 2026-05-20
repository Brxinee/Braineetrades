"""
Strategy base class. All intraday strategies inherit from this.

Each strategy must implement:
  generate_signals(df) -> SignalResult

The DataFrame passed in has these guaranteed columns (all lowercase):
  open, high, low, close, volume   (5-minute OHLCV, IST-indexed)

generate_signals must return a SignalResult with:
  entries  – boolean Series, True on the bar to ENTER long
  exits    – boolean Series, True on the bar to EXIT long
  sl       – float Series, stop-loss price per bar (NaN where no entry)
  target   – float Series, target price per bar (NaN where no entry)
  signal_meta – dict with any extra info surfaced in the live scanner

Position sizing, concurrent-position cap (max 3), and 3:15 PM IST square-off
are handled by the backtest engine, not the strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class SignalResult:
    entries: pd.Series   # bool, True = buy signal on that bar
    exits: pd.Series     # bool, True = exit signal on that bar
    sl: pd.Series        # float, stop-loss price (NaN where no active signal)
    target: pd.Series    # float, target price   (NaN where no active signal)
    signal_meta: dict = field(default_factory=dict)

    def __post_init__(self):
        for attr in ("entries", "exits", "sl", "target"):
            s = getattr(self, attr)
            if not isinstance(s, pd.Series):
                raise TypeError(f"{attr} must be a pd.Series, got {type(s)}")


class Strategy(ABC):
    """
    Base class for all Nifty 50 intraday strategies.

    Subclasses set class-level attributes for display:
      name        – human-readable strategy name
      description – one-line description shown in UI
      params      – dict of default hyper-params (overridable from backtest API)
    """

    name: str = "Base Strategy"
    description: str = ""
    params: dict = {}

    def __init__(self, **kwargs):
        self.cfg = {**self.__class__.params, **kwargs}

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> SignalResult:
        """
        Given a 5-minute OHLCV DataFrame for a single symbol, return signals.
        df.index is DatetimeTzAware in Asia/Kolkata.
        """
        ...

    # ------------------------------------------------------------------ #
    #  Shared indicator helpers available to all strategies                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Exponential ATR (Wilder's smoothing)."""
        high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        """
        Intraday VWAP, reset at the start of each calendar day.
        df.index must be tz-aware (Asia/Kolkata).
        """
        tp = (df["high"] + df["low"] + df["close"]) / 3
        tpv = tp * df["volume"]
        date_key = df.index.normalize()
        cum_tpv = tpv.groupby(date_key).cumsum()
        cum_vol = df["volume"].groupby(date_key).cumsum()
        return cum_tpv / cum_vol.replace(0, np.nan)

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def supertrend(
        df: pd.DataFrame, period: int = 7, multiplier: float = 3.0
    ) -> tuple[pd.Series, pd.Series]:
        """
        Vectorised Supertrend.
        Returns (supertrend_line, direction) where direction == 1 is bullish,
        direction == -1 is bearish.
        """
        atr_vals = Strategy.atr(df, period).values
        close = df["close"].values
        hl2 = ((df["high"] + df["low"]) / 2).values

        basic_ub = hl2 + multiplier * atr_vals
        basic_lb = hl2 - multiplier * atr_vals

        n = len(df)
        final_ub = basic_ub.copy()
        final_lb = basic_lb.copy()
        st = np.full(n, np.nan)
        direction = np.ones(n, dtype=int)

        for i in range(1, n):
            # Final upper band: only tighten (pull down) — reset if price breaks above
            if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]:
                final_ub[i] = basic_ub[i]
            else:
                final_ub[i] = final_ub[i - 1]

            # Final lower band: only tighten (push up) — reset if price breaks below
            if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]:
                final_lb[i] = basic_lb[i]
            else:
                final_lb[i] = final_lb[i - 1]

            # Determine direction from prev supertrend
            if np.isnan(st[i - 1]):
                # Initialise
                st[i] = final_lb[i]
                direction[i] = 1
            elif st[i - 1] == final_ub[i - 1]:
                # Was bearish
                if close[i] > final_ub[i]:
                    st[i] = final_lb[i]
                    direction[i] = 1
                else:
                    st[i] = final_ub[i]
                    direction[i] = -1
            else:
                # Was bullish
                if close[i] < final_lb[i]:
                    st[i] = final_ub[i]
                    direction[i] = -1
                else:
                    st[i] = final_lb[i]
                    direction[i] = 1

        return (
            pd.Series(st, index=df.index),
            pd.Series(direction, index=df.index),
        )

    @staticmethod
    def swing_lows(series: pd.Series, window: int = 3) -> pd.Series:
        """
        Boolean mask: True where series[i] is the minimum of the surrounding window.
        Useful for divergence detection.
        """
        rolled_min = series.rolling(2 * window + 1, center=True).min()
        return series == rolled_min

    @staticmethod
    def squareoff_mask(df: pd.DataFrame) -> pd.Series:
        """
        True on any bar at or after 15:15 IST — strategy must exit.
        """
        import datetime
        cutoff = datetime.time(15, 15)
        return pd.Series(
            [t >= cutoff for t in df.index.time],
            index=df.index,
            dtype=bool,
        )
