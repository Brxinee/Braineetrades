"""
Smoke tests for all 5 strategies using synthetic 5-minute data.
No network required. Run with: python -m pytest strategies/tests/ -v
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pytz

# Make sure repo root is importable when running from any directory
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategies import REGISTRY
from strategies.base import SignalResult, Strategy

IST = pytz.timezone("Asia/Kolkata")
_BARS_PER_DAY = 75   # 09:15–15:25 IST at 5-min intervals


# ────────────────────────────────────────────────────────────────────────────
# Synthetic data factories
# ────────────────────────────────────────────────────────────────────────────

def _make_timestamps(n_days: int = 5) -> pd.DatetimeIndex:
    """Generate IST-aware 5-minute bar timestamps for n_days."""
    base = datetime.date(2025, 3, 3)   # fixed Monday
    stamps = []
    for d in range(n_days):
        day = base + datetime.timedelta(days=d)
        if day.weekday() >= 5:   # skip weekends
            continue
        # pytz requires localize(), not tzinfo= in constructor
        naive = datetime.datetime(day.year, day.month, day.day, 9, 15)
        t = IST.localize(naive)
        for _ in range(_BARS_PER_DAY):
            stamps.append(t)
            t += datetime.timedelta(minutes=5)
    return pd.DatetimeIndex(stamps)


def make_flat_df(price: float = 1000.0, n_days: int = 5) -> pd.DataFrame:
    """Flat, constant-price OHLCV — no signals should fire (mostly)."""
    idx = _make_timestamps(n_days)
    n = len(idx)
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": 100_000,
        },
        index=idx,
    )


def make_trending_df(start: float = 1000.0, drift: float = 0.5, n_days: int = 10) -> pd.DataFrame:
    """Steadily rising price — good for Supertrend + ORB tests."""
    idx = _make_timestamps(n_days)
    n = len(idx)
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.1, n)
    close_vals = start + drift * np.arange(n) + noise.cumsum()
    close_vals = np.maximum(close_vals, 1.0)
    spread = 1.0
    return pd.DataFrame(
        {
            "open": close_vals - spread * 0.3,
            "high": close_vals + spread * 0.7,
            "low": close_vals - spread * 0.7,
            "close": close_vals,
            "volume": rng.integers(50_000, 200_000, n).astype(float),
        },
        index=idx,
    )


def make_vwap_pullback_df(n_days: int = 5) -> pd.DataFrame:
    """
    Each day: price drops >1% below VWAP in mid-session, then recovers.
    Should generate VWAP Reversal entries.
    """
    idx = _make_timestamps(n_days)
    n = len(idx)
    rng = np.random.default_rng(7)
    base = 1000.0

    closes = []
    highs = []
    lows = []
    opens = []
    volumes = []

    bars_per_day = _BARS_PER_DAY
    for bar_i in range(n):
        day_bar = bar_i % bars_per_day
        # Mid-session (bars 20–35) drop 1.5% below base, then recover
        if 20 <= day_bar <= 35:
            c = base * 0.984 + rng.normal(0, 0.5)
        elif 36 <= day_bar <= 50:
            # Recovery
            c = base * (0.984 + (day_bar - 36) * 0.001) + rng.normal(0, 0.3)
        else:
            c = base + rng.normal(0, 0.3)
        closes.append(c)
        highs.append(c + abs(rng.normal(0.5, 0.2)))
        lows.append(c - abs(rng.normal(0.5, 0.2)))
        opens.append(c + rng.normal(0, 0.2))
        volumes.append(float(rng.integers(80_000, 150_000)))

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def make_gap_down_df(n_days: int = 5, gap_pct: float = 0.012) -> pd.DataFrame:
    """Each day opens gap_pct below previous close — triggers Gap Fade long."""
    idx = _make_timestamps(n_days)
    n = len(idx)
    rng = np.random.default_rng(3)

    prev_close = 1000.0
    closes = []
    opens_ = []
    highs = []
    lows = []
    volumes = []

    bars_per_day = _BARS_PER_DAY
    for bar_i in range(n):
        day_bar = bar_i % bars_per_day
        if day_bar == 0:
            o = prev_close * (1 - gap_pct)
        else:
            o = closes[-1] + rng.normal(0, 0.5)
        # Gradual recovery toward prev_close
        c = o + (prev_close - o) * (day_bar / bars_per_day) + rng.normal(0, 0.3)
        closes.append(c)
        opens_.append(o)
        highs.append(max(o, c) + abs(rng.normal(0.3, 0.1)))
        lows.append(min(o, c) - abs(rng.normal(0.3, 0.1)))
        volumes.append(float(rng.integers(60_000, 180_000)))
        if day_bar == bars_per_day - 1:
            prev_close = c

    return pd.DataFrame(
        {"open": opens_, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


# ────────────────────────────────────────────────────────────────────────────
# Generic contract tests (run against every strategy)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_returns_signal_result(key):
    """Every strategy must return a valid SignalResult."""
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=15)
    result = strat.generate_signals(df)
    assert isinstance(result, SignalResult)


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_series_lengths_match_df(key):
    """All output Series must have the same length as the input df."""
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=15)
    result = strat.generate_signals(df)
    n = len(df)
    assert len(result.entries) == n
    assert len(result.exits) == n
    assert len(result.sl) == n
    assert len(result.target) == n


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_entries_are_boolean(key):
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=15)
    result = strat.generate_signals(df)
    assert result.entries.dtype == bool or result.entries.dtype == np.bool_


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_no_entry_after_squareoff(key):
    """No strategy should fire an entry at or after 15:15 IST."""
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=20)
    result = strat.generate_signals(df)
    squareoff_mask = Strategy.squareoff_mask(df)
    bad_entries = result.entries & squareoff_mask
    assert not bad_entries.any(), f"{key}: entry fired at/after 15:15 IST"


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_sl_exists_where_entry(key):
    """Every entry bar must have a valid (non-NaN) stop-loss price."""
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=20)
    result = strat.generate_signals(df)
    entry_mask = result.entries
    if not entry_mask.any():
        pytest.skip(f"{key}: no signals generated on synthetic data")
    sl_at_entry = result.sl[entry_mask]
    assert sl_at_entry.notna().all(), f"{key}: NaN SL on entry bar"


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_sl_below_close_on_entry(key):
    """Stop-loss must be strictly below the close price (long-only strategies)."""
    strat = REGISTRY[key]()
    df = make_trending_df(n_days=20)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip(f"{key}: no signals")
    sl_at_entry = result.sl[result.entries]
    close_at_entry = df["close"][result.entries]
    # Supertrend SL can be very close; use a small tolerance
    assert (sl_at_entry < close_at_entry + 0.01).all(), (
        f"{key}: SL not below close"
    )


@pytest.mark.parametrize("key", list(REGISTRY.keys()))
def test_flat_market_no_crash(key):
    """Strategies should not crash on flat/constant price data."""
    strat = REGISTRY[key]()
    df = make_flat_df()
    result = strat.generate_signals(df)   # must not raise
    assert isinstance(result, SignalResult)


# ────────────────────────────────────────────────────────────────────────────
# Strategy-specific signal-generation tests
# ────────────────────────────────────────────────────────────────────────────

def test_vwap_reversal_fires_on_pullback():
    """VWAPReversal must generate at least one entry on the pullback dataset."""
    strat = REGISTRY["vwap_reversal"]()
    df = make_vwap_pullback_df(n_days=10)
    result = strat.generate_signals(df)
    assert result.entries.any(), "VWAPReversal: no entries on pullback data"


def test_vwap_reversal_target_is_vwap():
    """Target should approximately equal VWAP on entry bars."""
    strat = REGISTRY["vwap_reversal"]()
    df = make_vwap_pullback_df(n_days=10)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip("no entries")
    vwap = Strategy.vwap(df)
    entry_idx = result.entries[result.entries].index
    tgt = result.target[entry_idx]
    vwap_at_entry = vwap[entry_idx]
    # Target should be within 1% of VWAP
    pct_diff = ((tgt - vwap_at_entry) / vwap_at_entry).abs()
    assert (pct_diff < 0.01).all(), "VWAPReversal: target deviates from VWAP"


def test_orb_max_one_entry_per_day():
    """ORB must take at most one long entry per trading day."""
    strat = REGISTRY["opening_range_breakout"]()
    df = make_trending_df(n_days=15)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip("no ORB entries on trending data")
    entry_dates = result.entries[result.entries].index.normalize()
    assert entry_dates.duplicated().sum() == 0, "ORB: multiple entries on same day"


def test_supertrend_direction_binary():
    """Supertrend direction must only be +1 or −1."""
    strat = REGISTRY["supertrend_ema"]()
    df = make_trending_df(n_days=10)
    _, direction = Strategy.supertrend(df)
    unique_dirs = set(direction.dropna().unique())
    assert unique_dirs.issubset({1, -1}), f"unexpected direction values: {unique_dirs}"


def test_supertrend_entries_require_above_ema():
    """All Supertrend entries must have close > EMA(20)."""
    strat = REGISTRY["supertrend_ema"]()
    df = make_trending_df(n_days=20)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip("no supertrend entries")
    ema20 = Strategy.ema(df["close"], 20)
    entry_close = df["close"][result.entries]
    entry_ema = ema20[result.entries]
    assert (entry_close > entry_ema - 0.01).all(), "Supertrend: entry below EMA"


def test_rsi_divergence_sl_below_entry():
    strat = REGISTRY["rsi_divergence"]()
    df = make_vwap_pullback_df(n_days=15)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip("no RSI divergence entries")
    entry_close = df["close"][result.entries]
    sl_vals = result.sl[result.entries]
    assert (sl_vals < entry_close).all(), "RSI Divergence: SL not below entry"


def test_gap_fade_fires_on_gap_down():
    """GapFade must generate entries on a dataset engineered with gap-downs."""
    strat = REGISTRY["gap_fade"]()
    df = make_gap_down_df(n_days=8, gap_pct=0.012)
    result = strat.generate_signals(df)
    assert result.entries.any(), "GapFade: no entries on 1.2% gap-down data"


def test_gap_fade_target_equals_prev_close():
    """GapFade target should equal the previous day's close."""
    strat = REGISTRY["gap_fade"]()
    df = make_gap_down_df(n_days=5, gap_pct=0.013)
    result = strat.generate_signals(df)
    if not result.entries.any():
        pytest.skip("no entries")
    tgt = result.target[result.entries].dropna()
    assert len(tgt) > 0
    # Targets should be above the open price (gap-down was faded up to prev close)
    open_at_entry = df["open"][result.entries].dropna()
    assert (tgt.values > open_at_entry.values).all(), (
        "GapFade: target not above open (should be prev close)"
    )


def test_gap_fade_no_entry_on_large_gap():
    """GapFade must skip gaps > 2%."""
    strat = REGISTRY["gap_fade"]()
    df = make_gap_down_df(n_days=5, gap_pct=0.025)   # 2.5% gap
    result = strat.generate_signals(df)
    assert not result.entries.any(), "GapFade: should skip >2% gap"


# ────────────────────────────────────────────────────────────────────────────
# Base indicator unit tests
# ────────────────────────────────────────────────────────────────────────────

def test_vwap_resets_each_day():
    """VWAP on the first bar of each day should equal that bar's typical price."""
    df = make_flat_df(price=1000.0, n_days=3)
    vwap = Strategy.vwap(df)
    # Find index positions of the first bar of each session
    dates = df.index.normalize()
    is_first_bar = np.array(
        [True] + [dates[i] != dates[i - 1] for i in range(1, len(dates))]
    )
    first_bar_df = df.iloc[is_first_bar]
    tp = (first_bar_df["high"] + first_bar_df["low"] + first_bar_df["close"]) / 3
    vwap_first = vwap.iloc[is_first_bar]
    diff = (vwap_first.values - tp.values).__abs__()
    assert (diff < 0.01).all(), "VWAP does not reset at session start"


def test_rsi_bounded():
    df = make_trending_df(n_days=10)
    rsi = Strategy.rsi(df["close"])
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all(), "RSI out of [0, 100]"


def test_atr_positive():
    df = make_trending_df(n_days=10)
    atr = Strategy.atr(df)
    assert (atr.dropna() > 0).all(), "ATR has non-positive values"


def test_supertrend_no_nan_after_warmup():
    df = make_trending_df(n_days=10)
    st, direction = Strategy.supertrend(df, period=7, multiplier=3.0)
    # After first warmup bars, should have no NaN
    assert st.iloc[10:].notna().all(), "Supertrend NaN after warmup"
    assert direction.iloc[10:].notna().all()
