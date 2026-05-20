"""Technical indicator helpers for the NIFTY options backtest engine.

All functions accept a DataFrame with columns [open, high, low, close, volume]
and a timezone-aware DatetimeIndex in IST.  Each returns a Series (or DataFrame
for multi-output indicators) with the same index.

Session boundary (for intraday resets): 09:15 IST.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytz

IST = pytz.timezone("Asia/Kolkata")


def _date_groups(df: pd.DataFrame) -> pd.Series:
    """Return a Series of date labels aligned to df.index."""
    idx = df.index.tz_convert(IST) if df.index.tz else df.index
    return pd.Series(idx.normalize(), index=df.index)


# ── VWAP ─────────────────────────────────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP, cumulative, resetting at 09:15 IST each session.

    Returns Series of VWAP values (same index as df).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical * df["volume"]
    dates = _date_groups(df)
    cum_tpv = tpv.groupby(dates).cumsum()
    cum_vol = df["volume"].groupby(dates).cumsum()
    result = cum_tpv / cum_vol
    result.name = "vwap"
    return result


# ── Moving averages ───────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    result = series.ewm(span=period, adjust=False).mean()
    result.name = f"ema_{period}"
    return result


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    result = series.rolling(period).mean()
    result.name = f"sma_{period}"
    return result


# ── ATR ───────────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder / EMA smoothing)."""
    prev_c = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result = tr.ewm(span=period, adjust=False).mean()
    result.name = f"atr_{period}"
    return result


# ── Supertrend ────────────────────────────────────────────────────────────────

def supertrend(
    df: pd.DataFrame, period: int = 7, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend indicator (iterative — correct for all bar counts).

    Returns DataFrame with columns:
        supertrend : price level of the trend line
        direction  : +1 (bullish) or -1 (bearish)
        signal     : 'buy' on bullish flip, 'sell' on bearish flip, '' otherwise
    """
    atr_vals = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    raw_upper = hl2 + multiplier * atr_vals
    raw_lower = hl2 - multiplier * atr_vals

    n = len(df)
    close = df["close"].values
    fu = raw_upper.values.copy()    # final upper band (numpy array, integer-indexed)
    fl = raw_lower.values.copy()    # final lower band
    ru = raw_upper.values           # raw bands as numpy for safe loop access
    rl = raw_lower.values
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        # Tighten upper band — never widen while price stays below it
        fu[i] = ru[i] if close[i - 1] > fu[i - 1] else min(ru[i], fu[i - 1])
        # Tighten lower band — never narrow while price stays above it
        fl[i] = rl[i] if close[i - 1] < fl[i - 1] else max(rl[i], fl[i - 1])

        if close[i] > fu[i - 1]:
            direction[i] = 1
        elif close[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    st_vals = np.where(direction == 1, fl, fu)
    dir_series = pd.Series(direction, index=df.index)
    signal = pd.Series("", index=df.index)
    flips = dir_series.diff()
    signal[flips == 2] = "buy"
    signal[flips == -2] = "sell"

    return pd.DataFrame(
        {
            "supertrend": pd.Series(st_vals, index=df.index),
            "direction": dir_series,
            "signal": signal,
        }
    )


# ── Opening Range Breakout ────────────────────────────────────────────────────

def orb(df: pd.DataFrame, bars: int = 3) -> pd.DataFrame:
    """Opening Range Breakout levels from the first `bars` 5-min candles.

    Parameters
    ----------
    bars : number of 5-min bars that define the opening range.
           bars=3  → 15-min ORB (9:15–9:30, signal from 9:30 onward)
           bars=6  → 30-min ORB (9:15–9:45, signal from 9:45 onward)

    Returns
    -------
    DataFrame with columns:
        orb_high  — opening range high (NaN inside the range window)
        orb_low   — opening range low
        orb_width — orb_high - orb_low
        complete  — True once the range is locked in for that session
    """
    dates = _date_groups(df)
    bar_num = df.groupby(dates).cumcount()     # 0-indexed position within session
    in_range = bar_num < bars

    # Max/min of range bars, broadcast to all rows in that session
    range_hi = df["high"].where(in_range).groupby(dates).transform("max")
    range_lo = df["low"].where(in_range).groupby(dates).transform("min")

    confirmed = bar_num >= bars
    orb_high = range_hi.where(confirmed)
    orb_low = range_lo.where(confirmed)

    return pd.DataFrame(
        {
            "orb_high": orb_high,
            "orb_low": orb_low,
            "orb_width": orb_high - orb_low,
            "complete": confirmed,
        }
    )


# ── First Hour Range ──────────────────────────────────────────────────────────

def first_hour_range(df: pd.DataFrame) -> pd.DataFrame:
    """High/low of the first trading hour (9:15–10:15 IST, 12 bars of 5-min).

    Returns same-shaped DataFrame with columns: fh_high, fh_low, fh_width, complete.
    """
    return orb(df, bars=12)


# ── Convenience ───────────────────────────────────────────────────────────────

def bar_number(df: pd.DataFrame) -> pd.Series:
    """0-based bar index within each session (0 = 9:15 bar)."""
    dates = _date_groups(df)
    return df.groupby(dates).cumcount().rename("bar_num")
