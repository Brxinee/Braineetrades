"""
regime.py — Market regime classification for NIFTY intraday trading.

Classifies the current intraday market condition into one of 9 regimes and
determines which strategies are compatible with that regime.

Dependencies: pandas, numpy, pytz (no external TA libraries).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from indicators import atr, ema, vwap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VIX regime thresholds
VIX_LOW = 12.0
VIX_NORMAL_HIGH = 18.0
VIX_ELEVATED_HIGH = 25.0
VIX_HIGH_HIGH = 35.0

# Gap classification thresholds (as fractions, e.g. 0.008 = 0.8 %)
GAP_LARGE = 0.008
GAP_SMALL = 0.003

# ATR-as-%-of-price thresholds
ATR_PCT_LOW = 0.004   # < 0.4 % → low volatility
ATR_PCT_HIGH = 0.012  # > 1.2 % → high volatility

# VWAP deviation thresholds (%) for mean-reversion detection
VWAP_DEV_THRESHOLD = 0.8

# EMA period used for trend assessment
EMA_FAST = 9
EMA_SLOW = 21

# Number of bars used to compute VWAP slope
VWAP_SLOPE_BARS = 12

# ---------------------------------------------------------------------------
# Strategy compatibility map
# ---------------------------------------------------------------------------

STRATEGY_COMPATIBILITY: dict[str, list[str]] = {
    "TRENDING_BULL":    ["orb15", "orb30", "gap_continuation", "trend_pullback"],
    "TRENDING_BEAR":    ["orb15", "orb30", "gap_continuation", "trend_pullback"],
    "RANGE":            ["vwap_mean_reversion", "cpr_vwap_confluence"],
    "VOLATILE":         ["orb15", "open_drive"],
    "GAP_TREND":        ["gap_continuation", "orb15"],
    "MEAN_REVERSION":   ["vwap_mean_reversion", "failed_breakout"],
    "EVENT_CHAOS":      [],
    "LOW_VOL_CHOP":     [],
    "NO_TRADE":         [],
}

# Gate assignments per regime
REGIME_GATE: dict[str, str] = {
    "TRENDING_BULL":  "TRADE",
    "TRENDING_BEAR":  "TRADE",
    "RANGE":          "TRADE",
    "VOLATILE":       "CAUTION",
    "GAP_TREND":      "TRADE",
    "MEAN_REVERSION": "TRADE",
    "EVENT_CHAOS":    "AVOID",
    "LOW_VOL_CHOP":   "AVOID",
    "NO_TRADE":       "AVOID",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _vix_regime(vix: float) -> str:
    """Classify VIX into a named regime bucket."""
    if vix < VIX_LOW:
        return "LOW"
    if vix < VIX_NORMAL_HIGH:
        return "NORMAL"
    if vix < VIX_ELEVATED_HIGH:
        return "ELEVATED"
    if vix < VIX_HIGH_HIGH:
        return "HIGH"
    return "EXTREME"


def _gap_type(gap_pct: float) -> str:
    """Classify an opening gap percentage into a named type."""
    if gap_pct >= GAP_LARGE:
        return "GAP_UP_LARGE"
    if gap_pct >= GAP_SMALL:
        return "GAP_UP_SMALL"
    if gap_pct <= -GAP_LARGE:
        return "GAP_DOWN_LARGE"
    if gap_pct <= -GAP_SMALL:
        return "GAP_DOWN_SMALL"
    return "FLAT"


def _compute_gap_pct(df_5m: pd.DataFrame, prev_close: float) -> float:
    """Compute gap % from the first bar's open vs prev_close."""
    if prev_close <= 0:
        return 0.0
    first_open = float(df_5m["open"].iloc[0])
    return (first_open - prev_close) / prev_close


def _compute_atr_pct(df_5m: pd.DataFrame) -> float:
    """ATR-14 as a fraction of the current close price."""
    try:
        atr_series = atr(df_5m, period=14)
        valid = atr_series.dropna()
        if valid.empty:
            return 0.0
        latest_atr = float(valid.iloc[-1])
        latest_close = float(df_5m["close"].iloc[-1])
        if latest_close <= 0:
            return 0.0
        return latest_atr / latest_close
    except Exception:
        logger.exception("_compute_atr_pct: failed to compute ATR")
        return 0.0


def _compute_vwap_slope(df_5m: pd.DataFrame, n_bars: int = VWAP_SLOPE_BARS) -> float:
    """
    Linear slope of VWAP over the last ``n_bars`` bars.

    Returns slope per bar (in price units). Positive → rising VWAP.
    """
    try:
        vwap_series = vwap(df_5m)
        valid = vwap_series.dropna()
        if len(valid) < 2:
            return 0.0
        tail = valid.iloc[-min(n_bars, len(valid)):]
        x = np.arange(len(tail), dtype=float)
        y = tail.values.astype(float)
        if len(x) < 2:
            return 0.0
        slope = float(np.polyfit(x, y, 1)[0])
        return slope
    except Exception:
        logger.exception("_compute_vwap_slope: failed")
        return 0.0


def _price_vs_vwap_deviation(df_5m: pd.DataFrame) -> float:
    """Current close deviation from VWAP as a percentage."""
    try:
        vwap_series = vwap(df_5m)
        latest_close = float(df_5m["close"].iloc[-1])
        latest_vwap = float(vwap_series.dropna().iloc[-1])
        if latest_vwap <= 0:
            return 0.0
        return (latest_close - latest_vwap) / latest_vwap * 100.0
    except Exception:
        logger.exception("_price_vs_vwap_deviation: failed")
        return 0.0


def _trend_direction(df_5m: pd.DataFrame) -> str:
    """
    Returns 'UP', 'DOWN', or 'FLAT' based on EMA9/EMA21 alignment
    and the relationship between the latest close and those EMAs.
    """
    try:
        close = df_5m["close"]
        ema_fast = ema(close, EMA_FAST).dropna()
        ema_slow = ema(close, EMA_SLOW).dropna()
        if ema_fast.empty or ema_slow.empty:
            return "FLAT"
        ef = float(ema_fast.iloc[-1])
        es = float(ema_slow.iloc[-1])
        cl = float(close.iloc[-1])
        if ef > es and cl > ef:
            return "UP"
        if ef < es and cl < ef:
            return "DOWN"
        return "FLAT"
    except Exception:
        logger.exception("_trend_direction: failed")
        return "FLAT"


def _is_erratic(df_5m: pd.DataFrame, atr_pct: float) -> bool:
    """
    True if price action is erratic: consecutive candles flip direction
    multiple times within the last 10 bars, AND ATR is high.
    """
    try:
        if atr_pct < ATR_PCT_HIGH:
            return False
        close = df_5m["close"].iloc[-10:]
        if len(close) < 4:
            return False
        changes = np.sign(np.diff(close.values))
        flips = int(np.sum(np.abs(np.diff(changes)) == 2))
        return flips >= 4
    except Exception:
        return False


def _volume_expanding(df_5m: pd.DataFrame, window: int = 20) -> bool:
    """True if the last bar's volume is above the rolling mean."""
    try:
        vol = df_5m["volume"]
        if len(vol) < window + 1:
            return False
        avg = float(vol.iloc[-(window + 1):-1].mean())
        latest = float(vol.iloc[-1])
        return latest > avg
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_regime(
    df_5m: pd.DataFrame,
    vix: float,
    prev_close: float,
) -> dict[str, Any]:
    """
    Classify the current intraday market regime for NIFTY.

    Parameters
    ----------
    df_5m : pd.DataFrame
        Intraday 5-minute OHLCV DataFrame with an IST DatetimeIndex.
        Should contain the current day's bars up to the latest tick.
    vix : float
        Current India VIX value.
    prev_close : float
        Previous session's closing price (used for gap calculation).

    Returns
    -------
    dict with keys:
        regime, confidence, vix, vix_regime, gap_pct, gap_type,
        atr_pct, vwap_slope, signals, gate, compatible_strategies
    """
    # ------------------------------------------------------------------
    # Guard: must have at least a handful of bars to be meaningful
    # ------------------------------------------------------------------
    if df_5m is None or len(df_5m) < 3:
        logger.warning("classify_regime: insufficient data — returning NO_TRADE")
        return _build_result(
            regime="NO_TRADE",
            confidence=0.0,
            vix=vix,
            vix_regime=_vix_regime(vix),
            gap_pct=0.0,
            gap_type="FLAT",
            atr_pct=0.0,
            vwap_slope=0.0,
            signals=["Insufficient data for regime classification"],
        )

    # ------------------------------------------------------------------
    # Step 1: Gather raw metrics
    # ------------------------------------------------------------------
    vix_regime_str = _vix_regime(vix)
    gap_pct = _compute_gap_pct(df_5m, prev_close)
    gap_type_str = _gap_type(gap_pct)
    atr_pct = _compute_atr_pct(df_5m)
    vwap_slope = _compute_vwap_slope(df_5m)
    trend = _trend_direction(df_5m)
    vwap_dev = _price_vs_vwap_deviation(df_5m)
    erratic = _is_erratic(df_5m, atr_pct)
    vol_expanding = _volume_expanding(df_5m)

    signals: list[str] = []

    # ------------------------------------------------------------------
    # Step 2: Regime classification logic
    #   Priority order (highest precedence first):
    #   1. EVENT_CHAOS  — extreme VIX + erratic
    #   2. NO_TRADE     — explicit gate-closure conditions
    #   3. LOW_VOL_CHOP — low VIX + low ATR + no trend
    #   4. GAP_TREND    — large gap in trend direction
    #   5. MEAN_REVERSION — price stretched from VWAP
    #   6. VOLATILE     — elevated VIX + expanding ATR
    #   7. TRENDING_BULL / TRENDING_BEAR
    #   8. RANGE        — default
    # ------------------------------------------------------------------

    # --- EVENT_CHAOS ---
    if vix_regime_str == "EXTREME":
        signals.append(f"VIX EXTREME ({vix:.1f}) — erratic price action likely")
        if erratic:
            signals.append("Price action confirmed erratic (multiple direction flips)")
        regime = "EVENT_CHAOS"
        confidence = _confidence_event_chaos(vix, erratic)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- NO_TRADE: pre-expiry / budget day / other known gate conditions
    # (Callers can inject known event flags; here we use high VIX + erratic
    #  as a proxy for event risk without external calendar.)
    if vix_regime_str == "HIGH" and erratic:
        signals.append(f"VIX HIGH ({vix:.1f}) with erratic price — possible event risk")
        regime = "NO_TRADE"
        confidence = 7.0
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- LOW_VOL_CHOP ---
    if (
        vix_regime_str == "LOW"
        and atr_pct < ATR_PCT_LOW
        and trend == "FLAT"
        and abs(vwap_dev) < VWAP_DEV_THRESHOLD
    ):
        signals.append(f"VIX LOW ({vix:.1f}), ATR_PCT={atr_pct:.4f} — choppy, no trend")
        signals.append("Price hugging VWAP — no directional conviction")
        regime = "LOW_VOL_CHOP"
        confidence = _confidence_low_vol_chop(vix, atr_pct)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- GAP_TREND ---
    # Large gap that aligns with intraday trend direction
    gap_in_trend_direction = (
        (gap_type_str == "GAP_UP_LARGE" and trend == "UP")
        or (gap_type_str == "GAP_DOWN_LARGE" and trend == "DOWN")
    )
    if gap_in_trend_direction:
        signals.append(
            f"Large gap ({gap_pct*100:.2f}%) aligned with trend ({trend}) — gap trend regime"
        )
        if vol_expanding:
            signals.append("Volume expanding — gap conviction confirmed")
        regime = "GAP_TREND"
        confidence = _confidence_gap_trend(gap_pct, trend, vol_expanding, vix)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- MEAN_REVERSION ---
    # Price is stretched significantly from VWAP
    if abs(vwap_dev) >= VWAP_DEV_THRESHOLD * 1.5:
        direction_str = "above" if vwap_dev > 0 else "below"
        signals.append(
            f"Price {direction_str} VWAP by {abs(vwap_dev):.2f}% — mean reversion setup"
        )
        if vix_regime_str in ("NORMAL", "LOW"):
            signals.append("Low VIX supports mean reversion fade trades")
        regime = "MEAN_REVERSION"
        confidence = _confidence_mean_reversion(vwap_dev, vix)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- VOLATILE ---
    if vix_regime_str in ("ELEVATED", "HIGH") and atr_pct >= ATR_PCT_HIGH:
        signals.append(
            f"VIX {vix_regime_str} ({vix:.1f}), ATR_PCT={atr_pct:.4f} — volatile regime"
        )
        if vol_expanding:
            signals.append("Volume expanding — increased activity")
        regime = "VOLATILE"
        confidence = _confidence_volatile(vix, atr_pct)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- TRENDING_BULL ---
    if trend == "UP":
        signals.append("EMA9 > EMA21, price above EMAs — bullish trend")
        if vwap_slope > 0:
            signals.append(f"VWAP slope positive ({vwap_slope:.2f}) — trend confirmed")
        if vol_expanding:
            signals.append("Volume expanding — breadth confirmation")
        regime = "TRENDING_BULL"
        confidence = _confidence_trending(trend, vwap_slope, vol_expanding, atr_pct, vix)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- TRENDING_BEAR ---
    if trend == "DOWN":
        signals.append("EMA9 < EMA21, price below EMAs — bearish trend")
        if vwap_slope < 0:
            signals.append(f"VWAP slope negative ({vwap_slope:.2f}) — downtrend confirmed")
        if vol_expanding:
            signals.append("Volume expanding — selling pressure confirmed")
        regime = "TRENDING_BEAR"
        confidence = _confidence_trending(trend, vwap_slope, vol_expanding, atr_pct, vix)
        return _build_result(
            regime=regime,
            confidence=confidence,
            vix=vix,
            vix_regime=vix_regime_str,
            gap_pct=gap_pct,
            gap_type=gap_type_str,
            atr_pct=atr_pct,
            vwap_slope=vwap_slope,
            signals=signals,
        )

    # --- RANGE (default) ---
    signals.append("No directional trend detected — ranging market")
    if abs(vwap_dev) < VWAP_DEV_THRESHOLD:
        signals.append("Price oscillating near VWAP — range-bound")
    regime = "RANGE"
    confidence = _confidence_range(atr_pct, vwap_dev, vix)
    return _build_result(
        regime=regime,
        confidence=confidence,
        vix=vix,
        vix_regime=vix_regime_str,
        gap_pct=gap_pct,
        gap_type=gap_type_str,
        atr_pct=atr_pct,
        vwap_slope=vwap_slope,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Confidence scorers per regime
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _confidence_event_chaos(vix: float, erratic: bool) -> float:
    base = 8.0
    if vix > 40:
        base += 1.0
    if erratic:
        base += 1.0
    return _clamp(base)


def _confidence_low_vol_chop(vix: float, atr_pct: float) -> float:
    base = 6.0
    if vix < 11:
        base += 1.5
    if atr_pct < 0.003:
        base += 1.5
    return _clamp(base)


def _confidence_gap_trend(
    gap_pct: float, trend: str, vol_expanding: bool, vix: float
) -> float:
    base = 5.0
    gap_abs = abs(gap_pct)
    if gap_abs > 0.015:
        base += 2.0
    elif gap_abs > 0.008:
        base += 1.0
    if vol_expanding:
        base += 1.5
    if vix < VIX_ELEVATED_HIGH:
        base += 1.0
    return _clamp(base)


def _confidence_mean_reversion(vwap_dev: float, vix: float) -> float:
    base = 4.0
    stretch = abs(vwap_dev)
    if stretch > 1.5:
        base += 2.0
    elif stretch > 1.0:
        base += 1.0
    if vix < VIX_NORMAL_HIGH:
        base += 1.5
    if vix > VIX_ELEVATED_HIGH:
        base -= 1.0
    return _clamp(base)


def _confidence_volatile(vix: float, atr_pct: float) -> float:
    base = 5.0
    if vix > 28:
        base += 1.5
    if atr_pct > 0.018:
        base += 1.5
    return _clamp(base)


def _confidence_trending(
    trend: str,
    vwap_slope: float,
    vol_expanding: bool,
    atr_pct: float,
    vix: float,
) -> float:
    base = 5.0
    slope_aligned = (trend == "UP" and vwap_slope > 0) or (
        trend == "DOWN" and vwap_slope < 0
    )
    if slope_aligned:
        base += 1.5
    if vol_expanding:
        base += 1.0
    if VIX_LOW <= vix <= VIX_ELEVATED_HIGH:
        base += 1.0
    if atr_pct > ATR_PCT_LOW:
        base += 0.5  # some volatility is healthy for trends
    return _clamp(base)


def _confidence_range(atr_pct: float, vwap_dev: float, vix: float) -> float:
    base = 4.0
    if atr_pct < ATR_PCT_LOW:
        base += 1.5
    if abs(vwap_dev) < 0.3:
        base += 1.5
    if vix < VIX_NORMAL_HIGH:
        base += 1.0
    return _clamp(base)


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def _build_result(
    regime: str,
    confidence: float,
    vix: float,
    vix_regime: str,
    gap_pct: float,
    gap_type: str,
    atr_pct: float,
    vwap_slope: float,
    signals: list[str],
) -> dict[str, Any]:
    """Assemble and return the standard regime result dict."""
    compatible = STRATEGY_COMPATIBILITY.get(regime, [])
    gate = REGIME_GATE.get(regime, "CAUTION")

    # Override VOLATILE gate when VIX >= 25 (strategies list already empty-ish,
    # but gate should be CAUTION not TRADE)
    if regime == "VOLATILE" and vix >= VIX_ELEVATED_HIGH:
        compatible = []
        gate = "AVOID"

    result: dict[str, Any] = {
        "regime": regime,
        "confidence": round(_clamp(confidence), 2),
        "vix": round(vix, 2),
        "vix_regime": vix_regime,
        "gap_pct": round(gap_pct * 100, 4),   # stored as percentage
        "gap_type": gap_type,
        "atr_pct": round(atr_pct * 100, 4),   # stored as percentage
        "vwap_slope": round(vwap_slope, 4),
        "signals": signals,
        "gate": gate,
        "compatible_strategies": compatible,
    }

    logger.info(
        "Regime classified: %s | confidence=%.1f | gate=%s | VIX=%.1f | gap=%.2f%% | atr=%.2f%%",
        regime,
        result["confidence"],
        gate,
        vix,
        result["gap_pct"],
        result["atr_pct"],
    )

    return result
