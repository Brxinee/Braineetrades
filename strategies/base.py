"""
Strategy base class. All intraday strategies inherit from this.

Each strategy must implement:
  generate_signals(df) -> SignalResult

The DataFrame passed in has these guaranteed columns (all lowercase):
  open, high, low, close, volume   (5-minute OHLCV, IST-indexed)

generate_signals must return a SignalResult with:
  entries  – boolean Series, True on the bar to ENTER long
  exits    – boolean Series, True on the bar to EXIT long
  sl       – float Series, stop-loss price per bar (NaN where no signal)
  target   – float Series, target price per bar (NaN where no signal)
  signal_meta – dict with any extra info surfaced in the live scanner

Position sizing, concurrent-position cap (max 3), and 3:15 PM IST square-off
are handled by the backtest engine, not the strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd
import numpy as np


@dataclass
class SignalResult:
    entries: pd.Series          # bool, True = buy signal on that bar
    exits: pd.Series            # bool, True = exit signal on that bar
    sl: pd.Series               # float, stop-loss price
    target: pd.Series           # float, target price
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
        # Merge default params with any runtime overrides
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
        """Average True Range."""
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        """
        Intraday VWAP, reset each session day.
        Requires datetime index with timezone info.
        """
        df = df.copy()
        df["_date"] = df.index.normalize()
        df["_tp"] = (df["high"] + df["low"] + df["close"]) / 3
        df["_tpv"] = df["_tp"] * df["volume"]
        df["_cum_tpv"] = df.groupby("_date")["_tpv"].cumsum()
        df["_cum_vol"] = df.groupby("_date")["volume"].cumsum()
        return df["_cum_tpv"] / df["_cum_vol"]

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
        Returns (supertrend_line, direction) where direction=1 means bullish.
        """
        atr = Strategy.atr(df, period)
        hl2 = (df["high"] + df["low"]) / 2
        upper_band = hl2 + multiplier * atr
        lower_band = hl2 - multiplier * atr

        st = pd.Series(np.nan, index=df.index)
        direction = pd.Series(1, index=df.index)

        for i in range(1, len(df)):
            prev_st = st.iloc[i - 1]
            prev_dir = direction.iloc[i - 1]
            close = df["close"].iloc[i]
            prev_close = df["close"].iloc[i - 1]

            # Lower band logic
            lb = lower_band.iloc[i]
            if lower_band.iloc[i - 1] > lb or prev_close < lower_band.iloc[i - 1]:
                lb = lower_band.iloc[i]
            else:
                lb = max(lb, lower_band.iloc[i - 1]) if not np.isnan(prev_st) else lb

            # Upper band logic
            ub = upper_band.iloc[i]
            if upper_band.iloc[i - 1] < ub or prev_close > upper_band.iloc[i - 1]:
                ub = upper_band.iloc[i]
            else:
                ub = min(ub, upper_band.iloc[i - 1]) if not np.isnan(prev_st) else ub

            if np.isnan(prev_st):
                st.iloc[i] = lb
                direction.iloc[i] = 1
            elif prev_st == upper_band.iloc[i - 1]:
                if close <= ub:
                    st.iloc[i] = ub
                    direction.iloc[i] = -1
                else:
                    st.iloc[i] = lb
                    direction.iloc[i] = 1
            else:
                if close >= lb:
                    st.iloc[i] = lb
                    direction.iloc[i] = 1
                else:
                    st.iloc[i] = ub
                    direction.iloc[i] = -1

        return st, direction

    @staticmethod
    def squareoff_mask(df: pd.DataFrame) -> pd.Series:
        """
        True on any bar at or after 15:15 IST — force exit.
        Assumes df.index is tz-aware Asia/Kolkata.
        """
        t = df.index.time
        cutoff = pd.Timestamp("15:15").time()
        return pd.Series(
            [x >= cutoff for x in t], index=df.index, dtype=bool
        )
