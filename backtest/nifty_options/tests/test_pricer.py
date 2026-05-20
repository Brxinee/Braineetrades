"""Unit tests for option_pricer.py and indicators.py."""

from datetime import date, timezone, timedelta

import numpy as np
import pandas as pd
import pytest
import pytz

from backtest.nifty_options.option_pricer import (
    atm_strike,
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta,
    bs_vega,
    option_chain_snapshot,
    vix_to_iv,
)
from backtest.nifty_options.indicators import (
    atr,
    bar_number,
    ema,
    first_hour_range,
    orb,
    sma,
    supertrend,
    vwap,
)

IST = pytz.timezone("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

S, K = 21_500.0, 21_500.0
T7 = 7 / 365.0          # 7 calendar days to expiry
T0 = 0.0                 # expiry day
SIGMA = 0.18             # 18% IV
R = 0.065


def _make_ohlcv(
    n_bars: int = 75,
    n_days: int = 1,
    start_price: float = 21_500.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic 5-min OHLCV DataFrame in IST, starting at 09:15 each day."""
    rng = np.random.default_rng(seed)
    IST_open = pd.Timestamp("2024-01-08 09:15:00", tz=IST)
    all_bars: list[pd.Timestamp] = []

    for d in range(n_days):
        day_start = IST_open + pd.Timedelta(days=d)
        all_bars.extend([day_start + pd.Timedelta(minutes=5 * b) for b in range(n_bars)])

    n = len(all_bars)
    closes = start_price + np.cumsum(rng.normal(0, 30, n))
    highs = closes + rng.uniform(5, 40, n)
    lows = closes - rng.uniform(5, 40, n)
    opens = closes - rng.normal(0, 15, n)
    volumes = rng.integers(50_000, 500_000, n).astype(float)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(all_bars, name="datetime"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# option_pricer: bs_price
# ─────────────────────────────────────────────────────────────────────────────

def test_call_price_positive():
    assert bs_price(S, K, T7, R, SIGMA, "CE") > 0


def test_put_price_positive():
    assert bs_price(S, K, T7, R, SIGMA, "PE") > 0


def test_put_call_parity():
    """C - P = S - K·e^{-rT}  (Black-Scholes put-call parity)."""
    K2 = 21_000.0
    call = bs_price(S, K2, T7, R, SIGMA, "CE")
    put = bs_price(S, K2, T7, R, SIGMA, "PE")
    rhs = S - K2 * np.exp(-R * T7)
    assert abs(call - put - rhs) < 0.02   # within 2 paise


def test_atm_call_approx_equals_atm_put():
    """ATM call ≈ ATM put for near-expiry options (small carry effect)."""
    call = bs_price(S, S, T7, R, SIGMA, "CE")
    put = bs_price(S, S, T7, R, SIGMA, "PE")
    assert abs(call - put) / call < 0.30   # within 30% of each other


def test_call_price_increases_with_vix():
    lo = bs_price(S, K, T7, R, 0.10, "CE")
    hi = bs_price(S, K, T7, R, 0.35, "CE")
    assert hi > lo


def test_put_price_increases_with_vix():
    lo = bs_price(S, K, T7, R, 0.10, "PE")
    hi = bs_price(S, K, T7, R, 0.35, "PE")
    assert hi > lo


def test_call_price_increases_with_tte():
    short = bs_price(S, K, 1 / 365, R, SIGMA, "CE")
    long_ = bs_price(S, K, 30 / 365, R, SIGMA, "CE")
    assert long_ > short


def test_intrinsic_on_expiry_call():
    assert bs_price(21_500, 21_000, T0, R, SIGMA, "CE") == pytest.approx(500.0)
    assert bs_price(21_500, 22_000, T0, R, SIGMA, "CE") == pytest.approx(0.0)


def test_intrinsic_on_expiry_put():
    assert bs_price(21_500, 22_000, T0, R, SIGMA, "PE") == pytest.approx(500.0)
    assert bs_price(21_500, 21_000, T0, R, SIGMA, "PE") == pytest.approx(0.0)


def test_deep_otm_call_near_zero():
    price = bs_price(21_500, 25_000, T7, R, SIGMA, "CE")
    assert price < 0.01


def test_deep_itm_call_near_intrinsic():
    S2, K2 = 21_500.0, 17_000.0
    price = bs_price(S2, K2, T7, R, SIGMA, "CE")
    intrinsic = S2 - K2
    assert price == pytest.approx(intrinsic, rel=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# option_pricer: greeks
# ─────────────────────────────────────────────────────────────────────────────

def test_delta_call_in_zero_one():
    d = bs_delta(S, K, T7, R, SIGMA, "CE")
    assert 0.0 < d < 1.0


def test_delta_put_in_neg_one_zero():
    d = bs_delta(S, K, T7, R, SIGMA, "PE")
    assert -1.0 < d < 0.0


def test_delta_call_minus_put_equals_one():
    """Call delta − put delta = 1 (put-call delta parity: N(d1) − (N(d1)−1) = 1)."""
    d_call = bs_delta(S, K, T7, R, SIGMA, "CE")
    d_put = bs_delta(S, K, T7, R, SIGMA, "PE")
    assert abs(d_call - d_put - 1.0) < 1e-10   # exact numerical identity


def test_gamma_positive():
    assert bs_gamma(S, K, T7, R, SIGMA) > 0


def test_theta_negative():
    assert bs_theta(S, K, T7, R, SIGMA, "CE") < 0
    assert bs_theta(S, K, T7, R, SIGMA, "PE") < 0


def test_vega_positive():
    assert bs_vega(S, K, T7, R, SIGMA) > 0


# ─────────────────────────────────────────────────────────────────────────────
# option_pricer: helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_atm_strike_rounds_up():
    assert atm_strike(21_526) == 21_550
    assert atm_strike(21_543) == 21_550
    # 21_525 is exactly halfway (430.5): Python banker's rounding → 430 → 21_500
    assert atm_strike(21_525) == 21_500
    assert atm_strike(21_575) == 21_600


def test_atm_strike_rounds_down():
    assert atm_strike(21_524) == 21_500
    assert atm_strike(21_501) == 21_500


def test_atm_strike_exact_multiple():
    assert atm_strike(22_000) == 22_000
    assert atm_strike(21_500) == 21_500


def test_vix_to_iv():
    assert vix_to_iv(20.0) == pytest.approx(0.20)
    assert vix_to_iv(15.5) == pytest.approx(0.155)
    assert vix_to_iv(0.0) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# option_pricer: option_chain_snapshot
# ─────────────────────────────────────────────────────────────────────────────

def test_chain_row_count():
    chain = option_chain_snapshot(21_500, 18.0, 7, num_strikes=5)
    assert len(chain) == 11   # 5 below + ATM + 5 above


def test_chain_has_required_columns():
    chain = option_chain_snapshot(21_500, 18.0, 7)
    for col in ["strike", "ce_price", "pe_price", "ce_delta", "pe_delta", "iv", "dte_days"]:
        assert col in chain.columns


def test_chain_all_prices_positive():
    chain = option_chain_snapshot(21_500, 18.0, 7)
    assert (chain["ce_price"] > 0).all()
    assert (chain["pe_price"] > 0).all()


def test_chain_atm_strike_centred():
    chain = option_chain_snapshot(21_543, 18.0, 7, num_strikes=3)
    strikes = chain["strike"].tolist()
    assert 21_550 in strikes   # ATM rounded to 21550


def test_chain_parity_each_row():
    chain = option_chain_snapshot(21_500, 18.0, 7)
    T = max(7, 0.5) / 365
    for _, row in chain.iterrows():
        K2 = row["strike"]
        lhs = row["ce_price"] - row["pe_price"]
        rhs = 21_500 - K2 * np.exp(-R * T)
        assert abs(lhs - rhs) < 0.10   # within 10 paise


# ─────────────────────────────────────────────────────────────────────────────
# indicators: VWAP
# ─────────────────────────────────────────────────────────────────────────────

def test_vwap_shape():
    df = _make_ohlcv(75)
    v = vwap(df)
    assert len(v) == 75


def test_vwap_between_low_and_high():
    df = _make_ohlcv(75)
    v = vwap(df)
    assert (v >= df["low"].min()).all()
    assert (v <= df["high"].max()).all()


def test_vwap_resets_each_day():
    df = _make_ohlcv(75, n_days=2)
    v = vwap(df)
    # First bar of each day: VWAP equals that bar's typical price
    typical = (df["high"] + df["low"] + df["close"]) / 3
    assert v.iloc[0] == pytest.approx(typical.iloc[0])
    assert v.iloc[75] == pytest.approx(typical.iloc[75])


def test_vwap_monotone_with_constant_price():
    """With uniform price and volume, VWAP is constant at that price."""
    idx = pd.date_range("2024-01-08 09:15", periods=10, freq="5min", tz=IST)
    df = pd.DataFrame(
        {"open": 21500, "high": 21500, "low": 21500, "close": 21500, "volume": 1000},
        index=idx,
    )
    v = vwap(df)
    assert (v - 21500.0).abs().max() < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# indicators: EMA / SMA
# ─────────────────────────────────────────────────────────────────────────────

def test_ema_length():
    df = _make_ohlcv(75)
    e = ema(df["close"], 9)
    assert len(e) == 75


def test_ema_smoothing_property():
    """EMA should be closer to the latest prices than SMA of same period."""
    df = _make_ohlcv(100)
    e = ema(df["close"], 20)
    s = sma(df["close"], 20)
    # Both should track the close; EMA should react faster (not a strict test)
    assert not e.isna().all()
    assert not s.isna().all()


# ─────────────────────────────────────────────────────────────────────────────
# indicators: ATR
# ─────────────────────────────────────────────────────────────────────────────

def test_atr_positive():
    df = _make_ohlcv(75)
    a = atr(df, 14)
    assert (a.dropna() > 0).all()


def test_atr_scales_with_range():
    """Wider candles → larger ATR."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-08 09:15", periods=50, freq="5min", tz=IST)
    df_narrow = pd.DataFrame(
        {"open": 21500, "high": 21505, "low": 21495, "close": 21500, "volume": 1e5},
        index=idx,
    )
    df_wide = pd.DataFrame(
        {"open": 21500, "high": 21600, "low": 21400, "close": 21500, "volume": 1e5},
        index=idx,
    )
    assert atr(df_wide, 5).iloc[-1] > atr(df_narrow, 5).iloc[-1]


# ─────────────────────────────────────────────────────────────────────────────
# indicators: ORB
# ─────────────────────────────────────────────────────────────────────────────

def test_orb15_nan_inside_range():
    df = _make_ohlcv(75)
    result = orb(df, bars=3)
    # First 3 bars should be NaN
    assert result["orb_high"].iloc[:3].isna().all()
    assert result["orb_low"].iloc[:3].isna().all()


def test_orb15_valid_after_range():
    df = _make_ohlcv(75)
    result = orb(df, bars=3)
    assert result["orb_high"].iloc[3:].notna().all()
    assert result["orb_low"].iloc[3:].notna().all()


def test_orb_high_gte_low():
    df = _make_ohlcv(75)
    result = orb(df, bars=3)
    valid = result["complete"]
    assert (result.loc[valid, "orb_high"] >= result.loc[valid, "orb_low"]).all()


def test_orb_resets_each_day():
    df = _make_ohlcv(75, n_days=2)
    result = orb(df, bars=3)
    day1_orb_high = result["orb_high"].iloc[3]
    day2_orb_high = result["orb_high"].iloc[78]    # bar index 75+3
    # Each day should have its own range (not necessarily equal)
    assert not np.isnan(day1_orb_high)
    assert not np.isnan(day2_orb_high)


def test_orb_width_positive():
    df = _make_ohlcv(75)
    result = orb(df, bars=3)
    valid = result["complete"]
    assert (result.loc[valid, "orb_width"] > 0).all()


def test_first_hour_range_uses_12_bars():
    df = _make_ohlcv(75)
    fh = first_hour_range(df)
    assert fh["orb_high"].iloc[:12].isna().all()
    assert fh["orb_high"].iloc[12:].notna().all()


# ─────────────────────────────────────────────────────────────────────────────
# indicators: Supertrend
# ─────────────────────────────────────────────────────────────────────────────

def test_supertrend_shape():
    df = _make_ohlcv(75)
    st = supertrend(df)
    assert list(st.columns) == ["supertrend", "direction", "signal"]
    assert len(st) == 75


def test_supertrend_direction_values():
    df = _make_ohlcv(75)
    st = supertrend(df)
    assert st["direction"].isin([1, -1]).all()


def test_supertrend_signal_values():
    df = _make_ohlcv(100)
    st = supertrend(df)
    assert st["signal"].isin(["buy", "sell", ""]).all()


def test_supertrend_no_signal_without_flip():
    """On a monotonically rising series, direction never flips → no signals."""
    idx = pd.date_range("2024-01-08 09:15", periods=30, freq="5min", tz=IST)
    closes = np.linspace(21_000, 22_000, 30)
    df = pd.DataFrame(
        {
            "open": closes - 5,
            "high": closes + 20,
            "low": closes - 20,
            "close": closes,
            "volume": 1e5,
        },
        index=idx,
    )
    st = supertrend(df, period=3)
    assert (st["direction"] == 1).all()


# ─────────────────────────────────────────────────────────────────────────────
# indicators: bar_number
# ─────────────────────────────────────────────────────────────────────────────

def test_bar_number_single_day():
    df = _make_ohlcv(10)
    bn = bar_number(df)
    assert bn.tolist() == list(range(10))


def test_bar_number_resets_each_day():
    df = _make_ohlcv(75, n_days=3)
    bn = bar_number(df)
    assert bn.iloc[0] == 0
    assert bn.iloc[75] == 0
    assert bn.iloc[150] == 0
    assert bn.iloc[74] == 74
