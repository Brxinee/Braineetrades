"""
strategies.py — Intraday trading strategy engine for NIFTY options.

Contains:
  - Signal dataclass
  - compute_confidence() utility
  - Strategy ABC
  - 14 concrete strategy implementations
  - STRATEGY_REGISTRY mapping
  - run_all_strategies() orchestrator

Dependencies: pandas, numpy, pytz (no external TA libraries).
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pytz

from backend.indicators import (
    atr,
    ema,
    orb,
    rsi,
    supertrend,
    vwap,
)

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# VIX thresholds for premium buying/selling decisions
VIX_PREMIUM_BUY_MAX = 25.0
VIX_FILTER_MIN = 11.0
VIX_FILTER_MAX = 22.0

# Volume confirmation threshold
VOLUME_CONFIRM_RATIO = 1.2

# EMA periods
EMA9 = 9
EMA21 = 21
EMA50 = 50

# ATR multiplier for VWAP mean reversion trigger
VWAP_MR_ATR_MULT = 1.5


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """Represents a single trading signal produced by a strategy."""

    symbol: str
    name: str
    strategy: str
    direction: str          # BUY_CE / BUY_PE / SELL_PREMIUM / HOLD / AVOID
    entry: float
    sl: float               # Stop-loss price
    target1: float
    target2: float
    rr: float               # Reward-to-risk ratio (T1 basis)
    confidence: float       # 1-10
    confidence_factors: dict = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)
    signal_time: str = ""   # ISO 8601
    option_suggestion: dict = field(default_factory=dict)
    tag: str = ""
    bars_ago: int = 0

    def __post_init__(self) -> None:
        if not self.signal_time:
            self.signal_time = datetime.now(tz=timezone.utc).isoformat()
        # Compute RR if not provided
        if self.rr == 0.0 and self.entry != self.sl:
            risk = abs(self.entry - self.sl)
            reward = abs(self.target1 - self.entry)
            self.rr = round(reward / risk, 2) if risk > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "strategy": self.strategy,
            "direction": self.direction,
            "entry": self.entry,
            "sl": self.sl,
            "target1": self.target1,
            "target2": self.target2,
            "rr": self.rr,
            "confidence": self.confidence,
            "confidence_factors": self.confidence_factors,
            "reasoning": self.reasoning,
            "signal_time": self.signal_time,
            "option_suggestion": self.option_suggestion,
            "tag": self.tag,
            "bars_ago": self.bars_ago,
        }


# ---------------------------------------------------------------------------
# Confidence computation
# ---------------------------------------------------------------------------


def compute_confidence(
    signal_data: dict[str, Any],
    regime: dict[str, Any],
    breadth: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """
    Score a signal against regime and breadth conditions.

    Parameters
    ----------
    signal_data : dict
        Must contain at minimum:
          - strategy      : str — strategy name
          - direction     : str — BUY_CE / BUY_PE / ...
          - entry         : float
          - volume        : float — bar volume
          - avg_volume    : float — rolling average volume
          - ema20         : float — current EMA-20 value
          - vwap          : float — current VWAP value
          - event_risk    : bool  — True if within 1 day of known event
    regime : dict
        Output from classify_regime().
    breadth : dict
        Market breadth dict; expected key: pct_above_vwap (float, 0-100).

    Returns
    -------
    tuple[float, dict]
        (raw_score_scaled_1_to_10, breakdown_dict)
    """
    factors: dict[str, float] = {}

    strategy_name = signal_data.get("strategy", "")
    direction = signal_data.get("direction", "")
    entry = float(signal_data.get("entry", 0.0))
    volume = float(signal_data.get("volume", 0.0))
    avg_volume = float(signal_data.get("avg_volume", 1.0))
    ema20 = float(signal_data.get("ema20", 0.0))
    vwap_price = float(signal_data.get("vwap", 0.0))
    event_risk = bool(signal_data.get("event_risk", False))

    pct_above_vwap = float(breadth.get("pct_above_vwap", 50.0))
    vix = float(regime.get("vix", 15.0))
    compatible_strategies: list[str] = regime.get("compatible_strategies", [])

    score = 0.0

    # 1. Regime match (+2)
    regime_match = strategy_name in compatible_strategies
    factors["regime_match"] = 2.0 if regime_match else 0.0
    score += factors["regime_match"]

    # 2. Trend alignment with EMA20 (+1.5)
    if ema20 > 0 and entry > 0:
        price_above_ema = entry > ema20
        if (direction == "BUY_CE" and price_above_ema) or (
            direction == "BUY_PE" and not price_above_ema
        ):
            factors["trend_align"] = 1.5
        else:
            factors["trend_align"] = 0.0
    else:
        factors["trend_align"] = 0.75  # neutral when data missing
    score += factors["trend_align"]

    # 3. VWAP alignment (+1.5)
    if vwap_price > 0 and entry > 0:
        price_above_vwap = entry > vwap_price
        if (direction == "BUY_CE" and price_above_vwap) or (
            direction == "BUY_PE" and not price_above_vwap
        ):
            factors["vwap_align"] = 1.5
        else:
            factors["vwap_align"] = 0.0
    else:
        factors["vwap_align"] = 0.75
    score += factors["vwap_align"]

    # 4. Market breadth (+1)
    if direction == "BUY_CE":
        factors["breadth"] = 1.0 if pct_above_vwap > 60.0 else 0.0
    elif direction == "BUY_PE":
        factors["breadth"] = 1.0 if pct_above_vwap < 40.0 else 0.0
    else:
        factors["breadth"] = 0.5  # neutral for SELL_PREMIUM etc.
    score += factors["breadth"]

    # 5. Volume confirmation (+1.5)
    if avg_volume > 0 and volume >= VOLUME_CONFIRM_RATIO * avg_volume:
        factors["volume"] = 1.5
    else:
        factors["volume"] = 0.0
    score += factors["volume"]

    # 6. VIX risk penalty (−2 if VIX > 25 for premium buying)
    if direction in ("BUY_CE", "BUY_PE") and vix > VIX_PREMIUM_BUY_MAX:
        factors["vix_risk"] = -2.0
    else:
        factors["vix_risk"] = 0.0
    score += factors["vix_risk"]

    # 7. Event risk penalty (−1)
    if event_risk:
        factors["event_risk"] = -1.0
    else:
        factors["event_risk"] = 0.0
    score += factors["event_risk"]

    # Scale from raw [−2, 9.5] to [1, 10]
    raw_min = -2.0
    raw_max = 9.5
    scaled = 1.0 + 9.0 * (score - raw_min) / (raw_max - raw_min)
    scaled = max(1.0, min(10.0, round(scaled, 2)))

    return scaled, factors


# ---------------------------------------------------------------------------
# Option suggestion helper
# ---------------------------------------------------------------------------


def _option_suggestion(
    direction: str,
    spot_price: float,
    vix: float,
    expiry_days: int = 7,
) -> dict[str, Any]:
    """
    Generate a rough option selection suggestion.

    Uses a simplified ATM strike rounding to nearest 50 for NIFTY.
    IV estimate is derived from VIX; premium estimate uses a rough
    Black-Scholes approximation (not production-accurate).
    """
    opt_type = "CE" if direction == "BUY_CE" else "PE"
    # Round to nearest 50 for NIFTY
    strike = round(spot_price / 50) * 50

    iv_est = vix / 100.0  # annualised IV estimate from VIX

    # Very rough premium estimate: spot * iv * sqrt(T) * 0.4  (ATM approx)
    T = max(expiry_days, 1) / 365.0
    premium_est = round(spot_price * iv_est * math.sqrt(T) * 0.4, 1)

    return {
        "type": opt_type,
        "strike": strike,
        "expiry_days": expiry_days,
        "iv_est": round(iv_est * 100, 1),
        "premium_est": premium_est,
    }


# ---------------------------------------------------------------------------
# Volume average helper
# ---------------------------------------------------------------------------


def _avg_volume(df: pd.DataFrame, window: int = 20) -> float:
    """Rolling average volume over the last ``window`` bars."""
    vol = df["volume"]
    if len(vol) <= window:
        return float(vol.mean()) if len(vol) > 0 else 1.0
    return float(vol.iloc[-(window + 1):-1].mean())


def _latest_vwap(df: pd.DataFrame) -> float:
    try:
        v = vwap(df)
        valid = v.dropna()
        return float(valid.iloc[-1]) if not valid.empty else float(df["close"].iloc[-1])
    except Exception:
        return float(df["close"].iloc[-1])


def _latest_ema(df: pd.DataFrame, period: int) -> float:
    try:
        e = ema(df["close"], period)
        valid = e.dropna()
        return float(valid.iloc[-1]) if not valid.empty else float(df["close"].iloc[-1])
    except Exception:
        return float(df["close"].iloc[-1])


def _now_ist_iso() -> str:
    return datetime.now(tz=IST).isoformat()


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------


class Strategy(ABC):
    """Abstract base class for all intraday strategies."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        """
        Evaluate current market data and return a list of Signal objects.

        Parameters
        ----------
        df_5m : pd.DataFrame
            Current-session 5-minute OHLCV bars with IST DatetimeIndex.
        symbol : str
            Trading symbol (e.g. 'NIFTY50').
        name : str
            Human-readable instrument name.
        regime : dict
            Output of classify_regime().
        vix : float
            Current India VIX.

        Returns
        -------
        list[Signal]
        """

    # ------------------------------------------------------------------
    # Shared helpers available to all subclasses
    # ------------------------------------------------------------------

    def _base_signal_data(
        self,
        df: pd.DataFrame,
        direction: str,
        entry: float,
    ) -> dict[str, Any]:
        """Build the common signal_data dict for compute_confidence."""
        return {
            "strategy": self.name,
            "direction": direction,
            "entry": entry,
            "volume": float(df["volume"].iloc[-1]),
            "avg_volume": _avg_volume(df),
            "ema20": _latest_ema(df, 20),
            "vwap": _latest_vwap(df),
            "event_risk": False,
        }

    def _guard(self, df: pd.DataFrame, min_bars: int = 10) -> bool:
        """Return False and log if data is insufficient."""
        if df is None or len(df) < min_bars:
            logger.debug("%s: insufficient data (%d bars)", self.name, len(df) if df is not None else 0)
            return False
        return True


# ---------------------------------------------------------------------------
# 1. ORB-15 Strategy
# ---------------------------------------------------------------------------


class ORB15Strategy(Strategy):
    """Opening Range Breakout — 15-minute range (first 3 × 5-min candles)."""

    name = "orb15"
    description = "Trades breakouts beyond the first 15-minute opening range."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=6):
            return []
        try:
            df_orb = orb(df_5m, bars=3)
        except Exception:
            logger.exception("%s: orb() failed", self.name)
            return []

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)

        # Scan completed bars (orb_complete == True) for breakout signals
        completed = df_orb[df_orb["orb_complete"]].copy()
        if completed.empty:
            return []

        for i in range(len(completed)):
            row = completed.iloc[i]
            prev_close_bar = float(row["close"])
            orb_high = float(row["orb_high"])
            orb_low = float(row["orb_low"])
            orb_width = float(row["orb_width"])
            bar_vol = float(row["volume"])

            if orb_width <= 0:
                continue

            # Bullish breakout
            if prev_close_bar > orb_high and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                entry = prev_close_bar
                sl = orb_low
                t1 = entry + orb_width
                t2 = entry + 2 * orb_width

                sd = self._base_signal_data(df_5m, "BUY_CE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                sig = Signal(
                    symbol=symbol,
                    name=name,
                    strategy=self.name,
                    direction="BUY_CE",
                    entry=round(entry, 2),
                    sl=round(sl, 2),
                    target1=round(t1, 2),
                    target2=round(t2, 2),
                    rr=0.0,
                    confidence=conf,
                    confidence_factors=factors,
                    reasoning=[
                        f"Close {entry:.2f} broke ORB-high {orb_high:.2f} with volume {bar_vol:.0f} (avg {avg_vol:.0f})",
                        f"ORB width: {orb_width:.2f}, T1={t1:.2f}, T2={t2:.2f}",
                    ],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                    tag="ORB15_BULL",
                    bars_ago=len(completed) - 1 - i,
                )
                sig.rr = round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2)
                signals.append(sig)

            # Bearish breakdown
            elif prev_close_bar < orb_low and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                entry = prev_close_bar
                sl = orb_high
                t1 = entry - orb_width
                t2 = entry - 2 * orb_width

                sd = self._base_signal_data(df_5m, "BUY_PE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                sig = Signal(
                    symbol=symbol,
                    name=name,
                    strategy=self.name,
                    direction="BUY_PE",
                    entry=round(entry, 2),
                    sl=round(sl, 2),
                    target1=round(t1, 2),
                    target2=round(t2, 2),
                    rr=0.0,
                    confidence=conf,
                    confidence_factors=factors,
                    reasoning=[
                        f"Close {entry:.2f} broke ORB-low {orb_low:.2f} with volume {bar_vol:.0f}",
                        f"ORB width: {orb_width:.2f}, T1={t1:.2f}, T2={t2:.2f}",
                    ],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                    tag="ORB15_BEAR",
                    bars_ago=len(completed) - 1 - i,
                )
                sig.rr = round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2)
                signals.append(sig)

        # Keep only the most recent signal per direction
        return _dedupe_by_direction(signals)


# ---------------------------------------------------------------------------
# 2. ORB-30 Strategy
# ---------------------------------------------------------------------------


class ORB30Strategy(ORB15Strategy):
    """Opening Range Breakout — 30-minute range (first 6 × 5-min candles)."""

    name = "orb30"
    description = "Trades breakouts beyond the first 30-minute opening range."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        # Reuse ORB15 logic but with bars=6 (30 min on 5-min data)
        if not self._guard(df_5m, min_bars=10):
            return []
        try:
            df_orb = orb(df_5m, bars=6)
        except Exception:
            logger.exception("%s: orb() failed", self.name)
            return []

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)
        completed = df_orb[df_orb["orb_complete"]].copy()
        if completed.empty:
            return []

        for i in range(len(completed)):
            row = completed.iloc[i]
            prev_close_bar = float(row["close"])
            orb_high = float(row["orb_high"])
            orb_low = float(row["orb_low"])
            orb_width = float(row["orb_width"])
            bar_vol = float(row["volume"])
            if orb_width <= 0:
                continue

            if prev_close_bar > orb_high and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                entry = prev_close_bar
                sl = orb_low
                t1 = entry + orb_width
                t2 = entry + 2 * orb_width
                sd = self._base_signal_data(df_5m, "BUY_CE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                sig = Signal(
                    symbol=symbol, name=name, strategy=self.name,
                    direction="BUY_CE", entry=round(entry, 2),
                    sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                    rr=0.0, confidence=conf, confidence_factors=factors,
                    reasoning=[f"ORB30 bull breakout at {entry:.2f} with volume confirmation"],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                    tag="ORB30_BULL", bars_ago=len(completed) - 1 - i,
                )
                sig.rr = round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2)
                signals.append(sig)

            elif prev_close_bar < orb_low and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                entry = prev_close_bar
                sl = orb_high
                t1 = entry - orb_width
                t2 = entry - 2 * orb_width
                sd = self._base_signal_data(df_5m, "BUY_PE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                sig = Signal(
                    symbol=symbol, name=name, strategy=self.name,
                    direction="BUY_PE", entry=round(entry, 2),
                    sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                    rr=0.0, confidence=conf, confidence_factors=factors,
                    reasoning=[f"ORB30 bear breakdown at {entry:.2f} with volume confirmation"],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                    tag="ORB30_BEAR", bars_ago=len(completed) - 1 - i,
                )
                sig.rr = round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2)
                signals.append(sig)

        return _dedupe_by_direction(signals)


# ---------------------------------------------------------------------------
# 3. First Hour Breakout Strategy
# ---------------------------------------------------------------------------


class FirstHourBOStrategy(Strategy):
    """Breakout beyond the first 60-minute range (12 × 5-min candles)."""

    name = "first_hour_bo"
    description = "Trades breakouts beyond the first hour's price range."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=14):
            return []
        try:
            df_orb = orb(df_5m, bars=12)
        except Exception:
            logger.exception("%s: orb() failed", self.name)
            return []

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)
        completed = df_orb[df_orb["orb_complete"]].copy()
        if completed.empty:
            return []

        row = completed.iloc[-1]  # only care about the latest bar
        entry = float(row["close"])
        orb_high = float(row["orb_high"])
        orb_low = float(row["orb_low"])
        orb_width = float(row["orb_width"])
        bar_vol = float(row["volume"])

        if orb_width <= 0:
            return []

        if entry > orb_high and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
            sl = orb_low
            t1 = entry + orb_width
            t2 = entry + 2 * orb_width
            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[f"First-hour BO bull: close {entry:.2f} > first-hr high {orb_high:.2f}"],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="FIRST_HOUR_BULL", bars_ago=0,
            ))
        elif entry < orb_low and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
            sl = orb_high
            t1 = entry - orb_width
            t2 = entry - 2 * orb_width
            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[f"First-hour BO bear: close {entry:.2f} < first-hr low {orb_low:.2f}"],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="FIRST_HOUR_BEAR", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 4. VWAP Mean Reversion Strategy
# ---------------------------------------------------------------------------


class VWAPMeanReversionStrategy(Strategy):
    """Fade extreme deviations from VWAP; entry at reversion toward VWAP."""

    name = "vwap_mean_reversion"
    description = "Fades price when it stretches > 1.5 × ATR from VWAP."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=15):
            return []
        try:
            vwap_series = vwap(df_5m)
            atr_series = atr(df_5m, period=14)
        except Exception:
            logger.exception("%s: indicator computation failed", self.name)
            return []

        latest_close = float(df_5m["close"].iloc[-1])
        latest_vwap = float(vwap_series.dropna().iloc[-1]) if not vwap_series.dropna().empty else latest_close
        latest_atr = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else 0.0

        if latest_atr <= 0:
            return []

        threshold = VWAP_MR_ATR_MULT * latest_atr
        deviation = latest_close - latest_vwap

        signals: list[Signal] = []

        if deviation > threshold:
            # Price stretched above VWAP → fade → BUY_PE
            entry = latest_close
            sl = latest_close + 0.5 * latest_atr   # SL above current price
            t1 = latest_vwap                         # Target: reversion to VWAP
            t2 = latest_vwap - 0.5 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Close {latest_close:.2f} is {deviation:.2f} above VWAP {latest_vwap:.2f}",
                    f"Threshold: 1.5×ATR={threshold:.2f} — mean reversion fade setup",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="VWAP_MR_FADE_UP", bars_ago=0,
            ))

        elif deviation < -threshold:
            # Price stretched below VWAP → fade up → BUY_CE
            entry = latest_close
            sl = latest_close - 0.5 * latest_atr
            t1 = latest_vwap
            t2 = latest_vwap + 0.5 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Close {latest_close:.2f} is {abs(deviation):.2f} below VWAP {latest_vwap:.2f}",
                    f"Threshold: 1.5×ATR={threshold:.2f} — mean reversion bounce setup",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="VWAP_MR_FADE_DN", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 5. Gap Continuation Strategy
# ---------------------------------------------------------------------------


class GapContinuationStrategy(Strategy):
    """Follow gap direction after ORB confirms the gap."""

    name = "gap_continuation"
    description = "Enters in gap direction once ORB-15 confirms continuation."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=6):
            return []

        gap_pct = float(regime.get("gap_pct", 0.0))  # already as percentage
        if abs(gap_pct) < 0.8:
            return []

        try:
            df_orb = orb(df_5m, bars=3)
        except Exception:
            logger.exception("%s: orb() failed", self.name)
            return []

        completed = df_orb[df_orb["orb_complete"]]
        if completed.empty:
            return []

        row = completed.iloc[-1]
        entry = float(row["close"])
        orb_high = float(row["orb_high"])
        orb_low = float(row["orb_low"])
        orb_width = float(row["orb_width"])
        bar_vol = float(row["volume"])
        avg_vol = _avg_volume(df_5m)

        if orb_width <= 0:
            return []

        signals: list[Signal] = []

        if gap_pct > 0 and entry > orb_high and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
            # Gap-up continuation
            sl = orb_low
            t1 = entry + orb_width
            t2 = entry + 2 * orb_width
            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Gap up {gap_pct:.2f}% confirmed by ORB-15 breakout at {entry:.2f}",
                    "Gap continuation: riding bullish momentum",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="GAP_CONT_UP", bars_ago=0,
            ))

        elif gap_pct < 0 and entry < orb_low and bar_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
            # Gap-down continuation
            sl = orb_high
            t1 = entry - orb_width
            t2 = entry - 2 * orb_width
            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Gap down {gap_pct:.2f}% confirmed by ORB-15 breakdown at {entry:.2f}",
                    "Gap continuation: riding bearish momentum",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="GAP_CONT_DN", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 6. Gap Fill Strategy
# ---------------------------------------------------------------------------


class GapFillStrategy(Strategy):
    """Fade the opening gap, targeting prev_close as the fill level."""

    name = "gap_fill"
    description = "Fades a gap > 0.8% back toward the previous close."

    # Store prev_close via regime's gap info + first open
    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=6):
            return []

        gap_pct = float(regime.get("gap_pct", 0.0))
        if abs(gap_pct) < 0.8:
            return []

        # Estimate prev_close from first bar's open and gap_pct
        first_open = float(df_5m["open"].iloc[0])
        if abs(gap_pct) < 1e-6:
            return []
        prev_close_est = first_open / (1.0 + gap_pct / 100.0)

        try:
            atr_series = atr(df_5m, period=14)
        except Exception:
            logger.exception("%s: atr() failed", self.name)
            return []

        latest_close = float(df_5m["close"].iloc[-1])
        latest_atr = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else 0.0
        avg_vol = _avg_volume(df_5m)

        signals: list[Signal] = []

        if gap_pct > 0.8:
            # Gap up — fade → BUY_PE
            entry = latest_close
            sl = float(df_5m["high"].iloc[-1]) + 0.25 * latest_atr
            t1 = prev_close_est
            t2 = prev_close_est - 0.5 * latest_atr

            if entry <= sl and (entry - t1) > 0:
                sd = self._base_signal_data(df_5m, "BUY_PE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                signals.append(Signal(
                    symbol=symbol, name=name, strategy=self.name,
                    direction="BUY_PE", entry=round(entry, 2),
                    sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                    rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                    confidence=conf, confidence_factors=factors,
                    reasoning=[
                        f"Gap up {gap_pct:.2f}% — fading back to prev close ~{prev_close_est:.2f}",
                        "Gap fill setup: mean reversion to prior close",
                    ],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                    tag="GAP_FILL_UP", bars_ago=0,
                ))

        elif gap_pct < -0.8:
            # Gap down — fade → BUY_CE
            entry = latest_close
            sl = float(df_5m["low"].iloc[-1]) - 0.25 * latest_atr
            t1 = prev_close_est
            t2 = prev_close_est + 0.5 * latest_atr

            if entry >= sl and (t1 - entry) > 0:
                sd = self._base_signal_data(df_5m, "BUY_CE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                signals.append(Signal(
                    symbol=symbol, name=name, strategy=self.name,
                    direction="BUY_CE", entry=round(entry, 2),
                    sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                    rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                    confidence=conf, confidence_factors=factors,
                    reasoning=[
                        f"Gap down {gap_pct:.2f}% — fading back to prev close ~{prev_close_est:.2f}",
                        "Gap fill setup: bounce back to prior close",
                    ],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                    tag="GAP_FILL_DN", bars_ago=0,
                ))

        return signals


# ---------------------------------------------------------------------------
# 7. Inside Bar Breakout Strategy
# ---------------------------------------------------------------------------


class InsideBarBOStrategy(Strategy):
    """5-minute inside-bar breakout — SL = opposite end of inside bar."""

    name = "inside_bar_bo"
    description = "Trades breakouts of 5-min inside bars with 1:1 spot target."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=4):
            return []

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)

        # Look at the last few bars for inside bar patterns
        window = min(10, len(df_5m) - 1)
        for i in range(len(df_5m) - window, len(df_5m) - 1):
            prev = df_5m.iloc[i]
            curr = df_5m.iloc[i + 1]

            prev_high = float(prev["high"])
            prev_low = float(prev["low"])
            curr_high = float(curr["high"])
            curr_low = float(curr["low"])
            curr_close = float(curr["close"])
            bar_vol = float(curr["volume"])

            # Inside bar: current bar's range entirely within previous bar
            if curr_high <= prev_high and curr_low >= prev_low:
                inside_width = prev_high - prev_low
                if inside_width <= 0:
                    continue

                # Breakout: close of the bar AFTER the inside bar breaks either end
                if i + 2 < len(df_5m):
                    breakout_bar = df_5m.iloc[i + 2]
                    bo_close = float(breakout_bar["close"])
                    bo_vol = float(breakout_bar["volume"])
                    bars_ago = len(df_5m) - (i + 2) - 1

                    if bo_close > prev_high and bo_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                        entry = bo_close
                        sl = curr_low   # opposite end of inside bar
                        t1 = entry + inside_width
                        t2 = entry + 2 * inside_width
                        sd = self._base_signal_data(df_5m, "BUY_CE", entry)
                        conf, factors = compute_confidence(sd, regime, {})
                        signals.append(Signal(
                            symbol=symbol, name=name, strategy=self.name,
                            direction="BUY_CE", entry=round(entry, 2),
                            sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                            rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                            confidence=conf, confidence_factors=factors,
                            reasoning=[
                                f"Inside bar breakout above {prev_high:.2f}, SL={sl:.2f}",
                                f"Inside bar width: {inside_width:.2f}",
                            ],
                            signal_time=_now_ist_iso(),
                            option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                            tag="INSIDE_BAR_BULL", bars_ago=bars_ago,
                        ))
                    elif bo_close < curr_low and bo_vol >= VOLUME_CONFIRM_RATIO * avg_vol:
                        entry = bo_close
                        sl = curr_high
                        t1 = entry - inside_width
                        t2 = entry - 2 * inside_width
                        sd = self._base_signal_data(df_5m, "BUY_PE", entry)
                        conf, factors = compute_confidence(sd, regime, {})
                        signals.append(Signal(
                            symbol=symbol, name=name, strategy=self.name,
                            direction="BUY_PE", entry=round(entry, 2),
                            sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                            rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                            confidence=conf, confidence_factors=factors,
                            reasoning=[
                                f"Inside bar breakdown below {curr_low:.2f}, SL={sl:.2f}",
                                f"Inside bar width: {inside_width:.2f}",
                            ],
                            signal_time=_now_ist_iso(),
                            option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                            tag="INSIDE_BAR_BEAR", bars_ago=bars_ago,
                        ))

        return _dedupe_by_direction(signals)


# ---------------------------------------------------------------------------
# 8. Open Drive Strategy
# ---------------------------------------------------------------------------


class OpenDriveStrategy(Strategy):
    """First 5-min candle > 0.3% range, closes near high → BUY_CE."""

    name = "open_drive"
    description = "Captures explosive first-candle moves that signal directional conviction."

    RANGE_THRESHOLD_PCT = 0.003   # 0.3%
    CLOSE_NEAR_HIGH_PCT = 0.3     # close in top 30% of bar's range

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=2):
            return []

        first_bar = df_5m.iloc[0]
        f_open = float(first_bar["open"])
        f_high = float(first_bar["high"])
        f_low = float(first_bar["low"])
        f_close = float(first_bar["close"])
        f_vol = float(first_bar["volume"])

        bar_range = f_high - f_low
        if bar_range <= 0:
            return []

        range_pct = bar_range / f_open if f_open > 0 else 0.0
        close_position = (f_close - f_low) / bar_range  # 0=at low, 1=at high

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)

        if range_pct >= self.RANGE_THRESHOLD_PCT and close_position >= (1.0 - self.CLOSE_NEAR_HIGH_PCT):
            # Bullish open drive
            try:
                atr_series = atr(df_5m, period=14)
                latest_atr = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else bar_range
            except Exception:
                latest_atr = bar_range

            entry = float(df_5m["close"].iloc[-1])
            sl = f_low
            t1 = entry + latest_atr
            t2 = entry + 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Open drive: first bar range {range_pct*100:.2f}% (>{self.RANGE_THRESHOLD_PCT*100:.1f}%)",
                    f"Close {f_close:.2f} in top {self.CLOSE_NEAR_HIGH_PCT*100:.0f}% of bar (close_pct={close_position:.2f})",
                    "Strong directional conviction from open",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="OPEN_DRIVE_BULL", bars_ago=len(df_5m) - 1,
            ))

        elif range_pct >= self.RANGE_THRESHOLD_PCT and close_position <= self.CLOSE_NEAR_HIGH_PCT:
            # Bearish open drive
            try:
                atr_series = atr(df_5m, period=14)
                latest_atr = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else bar_range
            except Exception:
                latest_atr = bar_range

            entry = float(df_5m["close"].iloc[-1])
            sl = f_high
            t1 = entry - latest_atr
            t2 = entry - 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"Open drive bearish: first bar range {range_pct*100:.2f}%",
                    f"Close in bottom {self.CLOSE_NEAR_HIGH_PCT*100:.0f}% of bar",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="OPEN_DRIVE_BEAR", bars_ago=len(df_5m) - 1,
            ))

        return signals


# ---------------------------------------------------------------------------
# 9. Afternoon Trend Strategy
# ---------------------------------------------------------------------------


class AfternoonTrendStrategy(Strategy):
    """After 1:30 PM IST — enter on pullback to EMA9 if uptrend confirmed."""

    name = "afternoon_trend"
    description = "Afternoon trend continuation via EMA9 pullback entries."

    AFTERNOON_HOUR = 13
    AFTERNOON_MINUTE = 30

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=30):
            return []

        # Filter to afternoon bars only
        try:
            df_ist = df_5m.copy()
            if df_ist.index.tzinfo is None:
                df_ist = df_ist.tz_localize(IST)
            elif str(df_ist.index.tzinfo) != str(IST):
                df_ist = df_ist.tz_convert(IST)
        except Exception:
            df_ist = df_5m

        cutoff_time = pd.Timestamp(
            year=df_ist.index[-1].year,
            month=df_ist.index[-1].month,
            day=df_ist.index[-1].day,
            hour=self.AFTERNOON_HOUR,
            minute=self.AFTERNOON_MINUTE,
            tzinfo=IST,
        )
        afternoon = df_ist[df_ist.index >= cutoff_time]
        if afternoon.empty:
            return []

        try:
            ema9 = ema(df_5m["close"], EMA9)
            ema21_series = ema(df_5m["close"], EMA21)
            vwap_series = vwap(df_5m)
        except Exception:
            logger.exception("%s: indicator error", self.name)
            return []

        e9 = float(ema9.dropna().iloc[-1]) if not ema9.dropna().empty else 0.0
        e21 = float(ema21_series.dropna().iloc[-1]) if not ema21_series.dropna().empty else 0.0
        latest_vwap = float(vwap_series.dropna().iloc[-1]) if not vwap_series.dropna().empty else 0.0
        latest_close = float(df_5m["close"].iloc[-1])

        # EMA slopes (compare last bar to 5 bars ago)
        def _slope_positive(s: pd.Series) -> bool:
            valid = s.dropna()
            if len(valid) < 6:
                return False
            return float(valid.iloc[-1]) > float(valid.iloc[-6])

        ema9_rising = _slope_positive(ema9)
        avg_vol = _avg_volume(df_5m)
        bar_vol = float(df_5m["volume"].iloc[-1])

        signals: list[Signal] = []

        # Bullish afternoon trend: price > VWAP, EMA9 > EMA21, EMA9 rising
        if (
            latest_close > latest_vwap
            and e9 > e21
            and ema9_rising
            and abs(latest_close - e9) / max(e9, 1) < 0.003  # near EMA9 (pullback)
        ):
            entry = latest_close
            try:
                atr_val = float(atr(df_5m, 14).dropna().iloc[-1])
            except Exception:
                atr_val = abs(latest_close - e9) * 2
            sl = e21
            t1 = entry + atr_val
            t2 = entry + 2 * atr_val

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    "Afternoon trend: price > VWAP, EMA9 > EMA21 with positive slope",
                    f"Pullback to EMA9={e9:.2f}, entry at {entry:.2f}",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="AFTERNOON_TREND_BULL", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 10. EOD Momentum Strategy
# ---------------------------------------------------------------------------


class EODMomentumStrategy(Strategy):
    """Last 60 minutes — RSI > 60 + Supertrend bull + range breakout → BUY_CE."""

    name = "eod_momentum"
    description = "End-of-day momentum breakout in the last hour of trading."

    EOD_HOUR = 14
    EOD_MINUTE = 30

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=30):
            return []

        # Check we are in the last hour
        try:
            df_ist = df_5m.copy()
            if df_ist.index.tzinfo is None:
                df_ist = df_ist.tz_localize(IST)
            elif str(df_ist.index.tzinfo) != str(IST):
                df_ist = df_ist.tz_convert(IST)
        except Exception:
            df_ist = df_5m

        last_bar_time = df_ist.index[-1]
        if last_bar_time.hour < self.EOD_HOUR or (
            last_bar_time.hour == self.EOD_HOUR and last_bar_time.minute < self.EOD_MINUTE
        ):
            return []

        try:
            rsi_series = rsi(df_5m["close"], period=14)
            st_df = supertrend(df_5m, period=7, multiplier=3.0)
        except Exception:
            logger.exception("%s: indicator error", self.name)
            return []

        latest_rsi = float(rsi_series.dropna().iloc[-1]) if not rsi_series.dropna().empty else 50.0
        latest_close = float(df_5m["close"].iloc[-1])
        st_signal = str(st_df["signal"].iloc[-1]) if "signal" in st_df.columns else ""
        st_value = float(st_df["supertrend"].iloc[-1]) if "supertrend" in st_df.columns and not np.isnan(st_df["supertrend"].iloc[-1]) else 0.0

        # Afternoon range: use bars from 13:30 onwards
        cutoff = pd.Timestamp(
            year=last_bar_time.year, month=last_bar_time.month, day=last_bar_time.day,
            hour=13, minute=30, tzinfo=IST,
        )
        afternoon_bars = df_ist[df_ist.index >= cutoff]
        if afternoon_bars.empty:
            return []
        afternoon_high = float(afternoon_bars["high"].max())

        signals: list[Signal] = []

        # Bullish EOD: RSI > 60, supertrend bullish, close breaks afternoon high
        if (
            latest_rsi > 60.0
            and st_value > 0
            and latest_close == float(st_df["close"].iloc[-1])
            and latest_close > afternoon_high * 0.999  # at/above afternoon high
        ):
            try:
                atr_val = float(atr(df_5m, 14).dropna().iloc[-1])
            except Exception:
                atr_val = latest_close * 0.005

            entry = latest_close
            sl = st_value if st_value < entry else entry - atr_val
            t1 = entry + atr_val
            t2 = entry + 2 * atr_val

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"EOD momentum: RSI={latest_rsi:.1f} > 60, Supertrend bullish",
                    f"Close {latest_close:.2f} at/above afternoon range high {afternoon_high:.2f}",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix, expiry_days=1),
                tag="EOD_MOMENTUM_BULL", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 11. Trend Pullback Strategy
# ---------------------------------------------------------------------------


class TrendPullbackStrategy(Strategy):
    """EMA9 > EMA21 > EMA50, pullback to EMA9 with RSI 40-55 → BUY_CE."""

    name = "trend_pullback"
    description = "Classic multi-EMA trend pullback entry."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=55):
            return []

        try:
            close = df_5m["close"]
            ema9_s = ema(close, EMA9)
            ema21_s = ema(close, EMA21)
            ema50_s = ema(close, EMA50)
            rsi_s = rsi(close, 14)
            atr_s = atr(df_5m, 14)
        except Exception:
            logger.exception("%s: indicator error", self.name)
            return []

        e9 = float(ema9_s.dropna().iloc[-1]) if not ema9_s.dropna().empty else 0.0
        e21 = float(ema21_s.dropna().iloc[-1]) if not ema21_s.dropna().empty else 0.0
        e50 = float(ema50_s.dropna().iloc[-1]) if not ema50_s.dropna().empty else 0.0
        latest_rsi = float(rsi_s.dropna().iloc[-1]) if not rsi_s.dropna().empty else 50.0
        latest_close = float(df_5m["close"].iloc[-1])
        latest_atr = float(atr_s.dropna().iloc[-1]) if not atr_s.dropna().empty else 0.0

        signals: list[Signal] = []

        # Bullish trend pullback
        if (
            e9 > e21 > e50
            and 40.0 <= latest_rsi <= 55.0
            and abs(latest_close - e9) / max(e9, 1.0) < 0.004  # near EMA9
        ):
            entry = latest_close
            sl = e21
            t1 = entry + latest_atr
            t2 = entry + 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"EMA stack bullish: EMA9={e9:.2f} > EMA21={e21:.2f} > EMA50={e50:.2f}",
                    f"RSI={latest_rsi:.1f} in 40-55 zone — pullback not exhausted",
                    f"Close {latest_close:.2f} near EMA9 — ideal entry",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="TREND_PULLBACK_BULL", bars_ago=0,
            ))

        # Bearish trend pullback
        elif (
            e9 < e21 < e50
            and 45.0 <= latest_rsi <= 60.0
            and abs(latest_close - e9) / max(e9, 1.0) < 0.004
        ):
            entry = latest_close
            sl = e21
            t1 = entry - latest_atr
            t2 = entry - 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"EMA stack bearish: EMA9={e9:.2f} < EMA21={e21:.2f} < EMA50={e50:.2f}",
                    f"RSI={latest_rsi:.1f} pullback in 45-60 zone",
                    "Bearish trend pullback entry near EMA9",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="TREND_PULLBACK_BEAR", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 12. Failed Breakout Strategy
# ---------------------------------------------------------------------------


class FailedBreakoutStrategy(Strategy):
    """Price breaks ORB high but closes back below it → fade, BUY_PE."""

    name = "failed_breakout"
    description = "Fades false breakouts above ORB high that reverse back inside the range."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=6):
            return []

        try:
            df_orb = orb(df_5m, bars=3)
        except Exception:
            logger.exception("%s: orb() failed", self.name)
            return []

        completed = df_orb[df_orb["orb_complete"]].copy()
        if len(completed) < 2:
            return []

        signals: list[Signal] = []
        avg_vol = _avg_volume(df_5m)

        for i in range(1, len(completed)):
            prev_row = completed.iloc[i - 1]
            curr_row = completed.iloc[i]

            orb_high = float(curr_row["orb_high"])
            orb_low = float(curr_row["orb_low"])
            prev_high = float(prev_row["high"])
            curr_close = float(curr_row["close"])
            curr_vol = float(curr_row["volume"])

            # Pattern: previous bar high exceeded ORB high (attempted breakout)
            # but current bar closes back below ORB high
            if prev_high > orb_high and curr_close < orb_high:
                entry = curr_close
                sl = prev_high + (prev_high - orb_high) * 0.5   # above the fake breakout
                t1 = orb_low
                t2 = orb_low - (orb_high - orb_low) * 0.5

                risk = abs(sl - entry)
                if risk <= 0:
                    continue

                sd = self._base_signal_data(df_5m, "BUY_PE", entry)
                conf, factors = compute_confidence(sd, regime, {})
                sig = Signal(
                    symbol=symbol, name=name, strategy=self.name,
                    direction="BUY_PE", entry=round(entry, 2),
                    sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                    rr=round(abs(entry - t1) / risk, 2),
                    confidence=conf, confidence_factors=factors,
                    reasoning=[
                        f"Failed breakout: prev high {prev_high:.2f} > ORB high {orb_high:.2f}, close {curr_close:.2f} reverted below",
                        f"SL above fake breakout: {sl:.2f}, T1=ORB low {t1:.2f}",
                    ],
                    signal_time=_now_ist_iso(),
                    option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                    tag="FAILED_BO", bars_ago=len(completed) - 1 - i,
                )
                signals.append(sig)

        return _dedupe_by_direction(signals)


# ---------------------------------------------------------------------------
# 13. CPR + VWAP Confluence Strategy
# ---------------------------------------------------------------------------


class CPRVWAPStrategy(Strategy):
    """Price reclaims CPR top (tc) with positive VWAP slope → BUY_CE."""

    name = "cpr_vwap_confluence"
    description = "Confluent CPR top reclaim with positive VWAP slope for bullish bias."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not self._guard(df_5m, min_bars=15):
            return []

        # CPR levels must be injected via the DataFrame's attrs or regime dict.
        # Attempt to read from df_5m.attrs; fall back to regime.
        cpr_tc = df_5m.attrs.get("cpr_tc") or regime.get("cpr_tc")
        cpr_bc = df_5m.attrs.get("cpr_bc") or regime.get("cpr_bc")
        cpr_pivot = df_5m.attrs.get("cpr_pivot") or regime.get("cpr_pivot")

        if cpr_tc is None or cpr_bc is None:
            logger.debug("%s: CPR levels not available — skipping", self.name)
            return []

        cpr_tc = float(cpr_tc)
        cpr_bc = float(cpr_bc)

        try:
            vwap_series = vwap(df_5m)
            atr_s = atr(df_5m, 14)
        except Exception:
            logger.exception("%s: indicator error", self.name)
            return []

        # VWAP slope over last 12 bars
        vwap_valid = vwap_series.dropna()
        if len(vwap_valid) < 6:
            return []
        tail = vwap_valid.iloc[-12:]
        x = np.arange(len(tail), dtype=float)
        vwap_slope = float(np.polyfit(x, tail.values.astype(float), 1)[0]) if len(tail) >= 2 else 0.0

        latest_close = float(df_5m["close"].iloc[-1])
        latest_vwap = float(vwap_valid.iloc[-1])
        latest_atr = float(atr_s.dropna().iloc[-1]) if not atr_s.dropna().empty else 0.0

        signals: list[Signal] = []

        # Bullish: price reclaims CPR tc from below with positive VWAP slope
        prev_close_bar = float(df_5m["close"].iloc[-2]) if len(df_5m) >= 2 else latest_close
        if (
            prev_close_bar < cpr_tc
            and latest_close > cpr_tc
            and vwap_slope > 0
            and latest_close > latest_vwap
        ):
            entry = latest_close
            sl = cpr_bc
            t1 = entry + latest_atr
            t2 = entry + 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_CE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_CE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(t1 - entry) / max(abs(entry - sl), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"CPR top reclaim: price crossed {cpr_tc:.2f} (CPR tc) with VWAP slope +{vwap_slope:.3f}",
                    f"Price above VWAP ({latest_vwap:.2f}) — bullish confluence",
                    f"SL at CPR bc = {sl:.2f}",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_CE", entry, vix),
                tag="CPR_VWAP_BULL", bars_ago=0,
            ))

        # Bearish: price loses CPR bc from above with negative VWAP slope
        elif (
            prev_close_bar > cpr_bc
            and latest_close < cpr_bc
            and vwap_slope < 0
            and latest_close < latest_vwap
        ):
            entry = latest_close
            sl = cpr_tc
            t1 = entry - latest_atr
            t2 = entry - 2 * latest_atr

            sd = self._base_signal_data(df_5m, "BUY_PE", entry)
            conf, factors = compute_confidence(sd, regime, {})
            signals.append(Signal(
                symbol=symbol, name=name, strategy=self.name,
                direction="BUY_PE", entry=round(entry, 2),
                sl=round(sl, 2), target1=round(t1, 2), target2=round(t2, 2),
                rr=round(abs(entry - t1) / max(abs(sl - entry), 0.01), 2),
                confidence=conf, confidence_factors=factors,
                reasoning=[
                    f"CPR bottom loss: price broke below {cpr_bc:.2f} with negative VWAP slope",
                    f"Price below VWAP ({latest_vwap:.2f}) — bearish confluence",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion=_option_suggestion("BUY_PE", entry, vix),
                tag="CPR_VWAP_BEAR", bars_ago=0,
            ))

        return signals


# ---------------------------------------------------------------------------
# 14. VIX Filter Meta-Strategy
# ---------------------------------------------------------------------------


class VIXFilterStrategy(Strategy):
    """
    Meta-strategy / gate: only allows signals when VIX is within 11-22.

    When VIX is outside this range, returns [] regardless of market conditions.
    When VIX is in range, returns a single HOLD signal confirming gate is open.
    """

    name = "vix_filter"
    description = "Meta-gate that disables all signals outside VIX 11-22 range."

    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        symbol: str,
        name: str,
        regime: dict[str, Any],
        vix: float,
    ) -> list[Signal]:
        if not (VIX_FILTER_MIN <= vix <= VIX_FILTER_MAX):
            logger.info(
                "%s: VIX=%.1f outside range [%.1f, %.1f] — no signals",
                self.name, vix, VIX_FILTER_MIN, VIX_FILTER_MAX,
            )
            return []

        # Gate is open — emit a single HOLD confirmation signal
        latest_close = float(df_5m["close"].iloc[-1]) if df_5m is not None and len(df_5m) > 0 else 0.0
        return [
            Signal(
                symbol=symbol,
                name=name,
                strategy=self.name,
                direction="HOLD",
                entry=latest_close,
                sl=0.0,
                target1=0.0,
                target2=0.0,
                rr=0.0,
                confidence=5.0,
                confidence_factors={"vix_in_range": 5.0},
                reasoning=[
                    f"VIX={vix:.1f} is within optimal range [{VIX_FILTER_MIN}-{VIX_FILTER_MAX}]",
                    "VIX gate: OPEN — other strategies may generate signals",
                ],
                signal_time=_now_ist_iso(),
                option_suggestion={},
                tag="VIX_GATE_OPEN",
                bars_ago=0,
            )
        ]


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------


def _dedupe_by_direction(signals: list[Signal]) -> list[Signal]:
    """Keep only the most recent (lowest bars_ago) signal per direction."""
    best: dict[str, Signal] = {}
    for sig in signals:
        key = sig.direction
        if key not in best or sig.bars_ago < best[key].bars_ago:
            best[key] = sig
    return list(best.values())


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------


STRATEGY_REGISTRY: dict[str, Strategy] = {
    "orb15":               ORB15Strategy(),
    "orb30":               ORB30Strategy(),
    "first_hour_bo":       FirstHourBOStrategy(),
    "vwap_mean_reversion": VWAPMeanReversionStrategy(),
    "gap_continuation":    GapContinuationStrategy(),
    "gap_fill":            GapFillStrategy(),
    "inside_bar_bo":       InsideBarBOStrategy(),
    "open_drive":          OpenDriveStrategy(),
    "afternoon_trend":     AfternoonTrendStrategy(),
    "eod_momentum":        EODMomentumStrategy(),
    "trend_pullback":      TrendPullbackStrategy(),
    "failed_breakout":     FailedBreakoutStrategy(),
    "cpr_vwap_confluence": CPRVWAPStrategy(),
    "vix_filter":          VIXFilterStrategy(),
}


# ---------------------------------------------------------------------------
# run_all_strategies — Orchestrator
# ---------------------------------------------------------------------------


def run_all_strategies(
    df_5m: pd.DataFrame,
    symbol: str,
    name: str,
    regime: dict[str, Any],
    vix: float,
) -> list[Signal]:
    """
    Run all strategies that are compatible with the current regime and
    return a deduplicated list of signals.

    Parameters
    ----------
    df_5m : pd.DataFrame
        Intraday 5-minute OHLCV with IST DatetimeIndex.
    symbol : str
        Instrument symbol (e.g. 'NIFTY50').
    name : str
        Instrument human name.
    regime : dict
        Output of classify_regime().
    vix : float
        Current India VIX.

    Returns
    -------
    list[Signal]
        Signals sorted by confidence (descending), deduplicated by direction
        across all strategies.
    """
    gate = regime.get("gate", "AVOID")
    if gate == "AVOID":
        logger.info(
            "run_all_strategies: regime gate is AVOID (%s) — skipping all strategies",
            regime.get("regime"),
        )
        return []

    compatible: list[str] = regime.get("compatible_strategies", [])
    # Always run VIX filter; also always run strategies compatible with the regime
    strategies_to_run: list[str] = list(set(compatible) | {"vix_filter"})

    all_signals: list[Signal] = []

    for strat_name in strategies_to_run:
        strategy = STRATEGY_REGISTRY.get(strat_name)
        if strategy is None:
            logger.warning("run_all_strategies: unknown strategy '%s' — skipping", strat_name)
            continue
        try:
            sigs = strategy.generate_signals(df_5m, symbol, name, regime, vix)
            logger.debug("%s generated %d signal(s)", strat_name, len(sigs))
            all_signals.extend(sigs)
        except Exception:
            logger.exception("run_all_strategies: strategy '%s' raised an exception", strat_name)

    # Check VIX gate: if VIX filter returned [] (VIX out of range for premium buying),
    # suppress BUY_CE / BUY_PE signals and only return HOLD / SELL_PREMIUM.
    vix_gate_open = VIX_FILTER_MIN <= vix <= VIX_FILTER_MAX
    if not vix_gate_open:
        logger.info(
            "run_all_strategies: VIX=%.1f outside [%.1f, %.1f] — suppressing premium buy signals",
            vix, VIX_FILTER_MIN, VIX_FILTER_MAX,
        )
        all_signals = [s for s in all_signals if s.direction not in ("BUY_CE", "BUY_PE")]

    # Deduplicate by direction — keep highest-confidence signal per direction
    deduped: dict[str, Signal] = {}
    for sig in all_signals:
        key = sig.direction
        if key not in deduped or sig.confidence > deduped[key].confidence:
            deduped[key] = sig

    result = sorted(deduped.values(), key=lambda s: s.confidence, reverse=True)

    logger.info(
        "run_all_strategies: %d unique signal(s) from %d candidate(s) for regime %s",
        len(result),
        len(all_signals),
        regime.get("regime", "UNKNOWN"),
    )
    return result
