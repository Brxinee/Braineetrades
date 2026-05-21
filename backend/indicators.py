"""
indicators.py — Technical indicators for NIFTY intraday trading.

All functions operate on pandas DataFrames with an IST-aware DatetimeIndex
and columns: open, high, low, close, volume.

Dependencies: pandas, numpy, pytz (no external TA libraries).
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np
import pandas as pd
import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_ohlcv(df: pd.DataFrame, name: str = "df") -> None:
    """Raise ValueError if required OHLCV columns are missing."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"{name} is missing required columns: {missing}. "
            f"Available: {list(df.columns)}"
        )


def _validate_series(series: pd.Series, name: str = "series") -> None:
    if not isinstance(series, pd.Series):
        raise TypeError(f"{name} must be a pandas Series, got {type(series)}")
    if series.empty:
        raise ValueError(f"{name} is empty")


def _ensure_ist_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a timezone-aware IST DatetimeIndex."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex")
    if df.index.tzinfo is None:
        return df.copy().tz_localize(IST)
    if str(df.index.tzinfo) != str(IST):
        return df.copy().tz_convert(IST)
    return df


# ---------------------------------------------------------------------------
# 1. VWAP — cumulative, resets each calendar date
# ---------------------------------------------------------------------------

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative Volume-Weighted Average Price.

    Resets at the start of each trading session (calendar date in IST).
    Typical price = (high + low + close) / 3.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with an IST DatetimeIndex.

    Returns
    -------
    pd.Series
        VWAP values aligned to df's index.
    """
    _validate_ohlcv(df)
    df = _ensure_ist_index(df)

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_vol = typical * df["volume"]

    date_key = df.index.normalize()  # date component for grouping

    cum_tp_vol = tp_vol.groupby(date_key).cumsum()
    cum_vol = df["volume"].groupby(date_key).cumsum()

    result = cum_tp_vol / cum_vol
    result.name = "vwap"
    return result


# ---------------------------------------------------------------------------
# 2. EMA — Exponential Moving Average
# ---------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.

    Parameters
    ----------
    series : pd.Series
    period : int
        Look-back period; determines smoothing factor α = 2 / (period + 1).

    Returns
    -------
    pd.Series
    """
    _validate_series(series)
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    result = series.ewm(span=period, adjust=False, min_periods=period).mean()
    result.name = f"ema_{period}"
    return result


# ---------------------------------------------------------------------------
# 3. SMA — Simple Moving Average
# ---------------------------------------------------------------------------

def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Simple (arithmetic) Moving Average.

    Parameters
    ----------
    series : pd.Series
    period : int

    Returns
    -------
    pd.Series
    """
    _validate_series(series)
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    result = series.rolling(window=period, min_periods=period).mean()
    result.name = f"sma_{period}"
    return result


# ---------------------------------------------------------------------------
# 4. ATR — Wilder's Average True Range
# ---------------------------------------------------------------------------

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Wilder's Average True Range.

    True Range = max(H-L, |H-prevC|, |L-prevC|).
    ATR uses Wilder's smoothing (RMA / EWM with α=1/period).

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame.
    period : int
        Smoothing period (default 14).

    Returns
    -------
    pd.Series
    """
    _validate_ohlcv(df)
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing: α = 1/period, equivalent to EWM with com=period-1
    result = tr.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    result.name = f"atr_{period}"
    return result


# ---------------------------------------------------------------------------
# 5. RSI — Relative Strength Index
# ---------------------------------------------------------------------------

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing.

    Parameters
    ----------
    series : pd.Series
        Typically close prices.
    period : int
        Look-back period (default 14).

    Returns
    -------
    pd.Series
        RSI values in [0, 100].
    """
    _validate_series(series)
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    result.name = f"rsi_{period}"
    return result


# ---------------------------------------------------------------------------
# 6. Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands: upper, middle (SMA), lower.

    Parameters
    ----------
    series : pd.Series
    period : int
        SMA look-back (default 20).
    std : float
        Standard-deviation multiplier (default 2.0).

    Returns
    -------
    tuple[pd.Series, pd.Series, pd.Series]
        (upper, mid, lower)
    """
    _validate_series(series)
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")

    mid = series.rolling(window=period, min_periods=period).mean()
    sigma = series.rolling(window=period, min_periods=period).std(ddof=0)

    upper = mid + std * sigma
    lower = mid - std * sigma

    upper.name = f"bb_upper_{period}"
    mid.name = f"bb_mid_{period}"
    lower.name = f"bb_lower_{period}"

    return upper, mid, lower


# ---------------------------------------------------------------------------
# 7. Supertrend
# ---------------------------------------------------------------------------

def supertrend(
    df: pd.DataFrame,
    period: int = 7,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """
    Supertrend indicator.

    Uses ATR-based dynamic bands around a HL/2 midpoint.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame.
    period : int
        ATR period (default 7).
    multiplier : float
        ATR multiplier for band width (default 3.0).

    Returns
    -------
    pd.DataFrame
        Original df columns plus:
        - ``supertrend``: float — the current supertrend line value
        - ``signal``: str — 'buy', 'sell', or '' (no change yet)
    """
    _validate_ohlcv(df)

    hl2 = (df["high"] + df["low"]) / 2.0
    atr_val = atr(df, period)

    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    n = len(df)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    st = pd.Series(np.nan, index=df.index, dtype=float)
    signal = pd.Series("", index=df.index, dtype=object)

    close = df["close"].values
    fu = final_upper.values.copy()
    fl = final_lower.values.copy()
    ub = upper_band.values
    lb = lower_band.values

    st_vals = np.full(n, np.nan)

    # Iterate from first valid ATR bar
    first_valid = atr_val.first_valid_index()
    if first_valid is None:
        logger.warning("supertrend: ATR produced no valid values.")
        result = df.copy()
        result["supertrend"] = np.nan
        result["signal"] = ""
        return result

    start = df.index.get_loc(first_valid)

    # Initialise first bar
    fu[start] = ub[start]
    fl[start] = lb[start]
    st_vals[start] = fu[start]  # default to upper (bearish start)

    for i in range(start + 1, n):
        # Adjust upper band
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        # Adjust lower band
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]

        if st_vals[i - 1] == fu[i - 1]:
            # Was bearish
            st_vals[i] = fl[i] if close[i] > fu[i] else fu[i]
        else:
            # Was bullish
            st_vals[i] = fu[i] if close[i] < fl[i] else fl[i]

    # Derive signals from transitions
    sig_vals = np.full(n, "", dtype=object)
    for i in range(start + 1, n):
        prev_bearish = st_vals[i - 1] == fu[i - 1]
        curr_bullish = st_vals[i] == fl[i]
        if prev_bearish and curr_bullish:
            sig_vals[i] = "buy"
        elif (not prev_bearish) and (not curr_bullish):
            sig_vals[i] = "sell"
        else:
            sig_vals[i] = ""

    result = df.copy()
    result["supertrend"] = st_vals
    result["signal"] = sig_vals
    return result


# ---------------------------------------------------------------------------
# 8. CPR — Central Pivot Range (daily)
# ---------------------------------------------------------------------------

def cpr(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Central Pivot Range computed from daily OHLCV data.

    pivot = (H + L + C) / 3
    bc    = (H + L) / 2
    tc    = pivot * 2 - bc

    If tc < bc, they are swapped so tc is always the top.

    Parameters
    ----------
    df_daily : pd.DataFrame
        Daily OHLCV DataFrame (one row per trading day).

    Returns
    -------
    pd.DataFrame
        df_daily with additional columns: pivot, bc, tc.
    """
    _validate_ohlcv(df_daily)

    pivot = (df_daily["high"] + df_daily["low"] + df_daily["close"]) / 3.0
    bc = (df_daily["high"] + df_daily["low"]) / 2.0
    tc = pivot * 2.0 - bc

    result = df_daily.copy()
    result["pivot"] = pivot
    result["bc"] = bc
    result["tc"] = tc

    # Ensure tc is always the top of the CPR range
    swap_mask = result["tc"] < result["bc"]
    result.loc[swap_mask, "tc"], result.loc[swap_mask, "bc"] = (
        result.loc[swap_mask, "bc"].values,
        result.loc[swap_mask, "tc"].values,
    )

    return result


# ---------------------------------------------------------------------------
# 9. ORB — Opening Range Breakout
# ---------------------------------------------------------------------------

def orb(df: pd.DataFrame, bars: int = 3) -> pd.DataFrame:
    """
    Opening Range Breakout.

    For each session (calendar date), the opening range is the high and low
    of the first ``bars`` candles.  All subsequent bars in that session carry
    the range forward and include a boolean flag ``orb_complete``.

    Parameters
    ----------
    df : pd.DataFrame
        Intraday OHLCV DataFrame with an IST DatetimeIndex.
    bars : int
        Number of opening candles that define the range (default 3 → 15 min
        on 5-min data).

    Returns
    -------
    pd.DataFrame
        df with additional columns:
        - orb_high   : float
        - orb_low    : float
        - orb_width  : float  (orb_high - orb_low)
        - orb_complete : bool  (True after the opening range candles finish)
    """
    _validate_ohlcv(df)
    if bars < 1:
        raise ValueError(f"bars must be >= 1, got {bars}")

    df = _ensure_ist_index(df)
    date_key = df.index.normalize()

    orb_high = pd.Series(np.nan, index=df.index)
    orb_low = pd.Series(np.nan, index=df.index)
    orb_complete = pd.Series(False, index=df.index)

    for date, group in df.groupby(date_key):
        if len(group) < bars:
            logger.debug("orb: date %s has fewer than %d bars — skipping.", date, bars)
            continue
        opening_candles = group.iloc[:bars]
        high = opening_candles["high"].max()
        low = opening_candles["low"].min()

        orb_high[group.index] = high
        orb_low[group.index] = low
        orb_complete[group.index[bars:]] = True

    result = df.copy()
    result["orb_high"] = orb_high
    result["orb_low"] = orb_low
    result["orb_width"] = orb_high - orb_low
    result["orb_complete"] = orb_complete
    return result


# ---------------------------------------------------------------------------
# 10. Volume Profile
# ---------------------------------------------------------------------------

def volume_profile(df: pd.DataFrame, bins: int = 20) -> pd.DataFrame:
    """
    Fixed-range Volume Profile.

    Divides the price range into ``bins`` equal buckets and aggregates
    cumulative volume in each bucket.  The Point of Control (POC) is the
    bucket with the highest volume.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (intraday or daily).
    bins : int
        Number of price buckets (default 20).

    Returns
    -------
    pd.DataFrame
        Columns:
        - price_low   : lower bound of bucket
        - price_high  : upper bound of bucket
        - price_mid   : midpoint of bucket
        - volume      : total volume in bucket
        - pct_volume  : volume as % of total
        - is_poc      : bool, True for the Point of Control bucket
    """
    _validate_ohlcv(df)
    if bins < 2:
        raise ValueError(f"bins must be >= 2, got {bins}")

    price_min = df["low"].min()
    price_max = df["high"].max()

    if price_min == price_max:
        logger.warning("volume_profile: price range is zero — returning single bucket.")
        bins = 1

    edges = np.linspace(price_min, price_max, bins + 1)

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].values

    bucket_volumes = np.zeros(bins)
    for i in range(len(df)):
        tp = typical.iloc[i]
        v = vol[i]
        # Find bucket index; clip so max price lands in last bucket
        idx = int(np.searchsorted(edges[1:], tp, side="left"))
        idx = min(idx, bins - 1)
        bucket_volumes[idx] += v

    total_vol = bucket_volumes.sum()
    pct_volume = bucket_volumes / total_vol * 100.0 if total_vol > 0 else bucket_volumes * 0.0
    is_poc = bucket_volumes == bucket_volumes.max()

    result = pd.DataFrame(
        {
            "price_low": edges[:-1],
            "price_high": edges[1:],
            "price_mid": (edges[:-1] + edges[1:]) / 2.0,
            "volume": bucket_volumes,
            "pct_volume": pct_volume,
            "is_poc": is_poc,
        }
    )
    return result


# ---------------------------------------------------------------------------
# 11. Gap Detection
# ---------------------------------------------------------------------------

def detect_gaps(df_daily: pd.DataFrame) -> pd.Series:
    """
    Detect opening gaps on a daily OHLCV DataFrame.

    gap_pct = (today's open - yesterday's close) / yesterday's close

    Parameters
    ----------
    df_daily : pd.DataFrame
        Daily OHLCV DataFrame sorted by date ascending.

    Returns
    -------
    pd.Series
        ``gap_pct`` — positive means gap-up, negative means gap-down.
        NaN for the first row (no prior close).
    """
    _validate_ohlcv(df_daily)
    prev_close = df_daily["close"].shift(1)
    gap_pct = (df_daily["open"] - prev_close) / prev_close
    gap_pct.name = "gap_pct"
    return gap_pct


# ---------------------------------------------------------------------------
# 12. VWAP Deviation
# ---------------------------------------------------------------------------

def vwap_deviation(df: pd.DataFrame) -> pd.Series:
    """
    VWAP Deviation: percentage distance of close from VWAP.

    deviation = (close - vwap) / vwap * 100

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with an IST DatetimeIndex.

    Returns
    -------
    pd.Series
        Deviation in percent (+ve above VWAP, -ve below).
    """
    _validate_ohlcv(df)
    vwap_series = vwap(df)
    result = (df["close"] - vwap_series) / vwap_series * 100.0
    result.name = "vwap_deviation"
    return result
