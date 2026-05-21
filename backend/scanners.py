"""
scanners.py — Real-time and scheduled signal scanner for the Brainee Trades backend.

Responsibilities:
  - scan_symbol : run one strategy on one symbol's 5-min OHLCV → list[Signal]
  - scan_all    : scan the full universe in parallel via ThreadPoolExecutor
  - get_active_signals : filter signals to only those fired within max_bars_old bars

Signal format is defined in strategies.py (dataclass Signal).

Market regime classification:
  Regime is derived from NIFTY daily data:
    - VIX bucketing (LOW / ELEVATED / HIGH / EXTREME)
    - 20-day EMA trend direction
    - Intraday NIFTY 5m trend (last 12 bars = 1 hour)

All timestamps are IST. All errors on individual symbols are caught and logged;
they never abort the broader scan.
"""

from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytz

# ---------------------------------------------------------------------------
# Project-level imports — paths resolved relative to repo root
# ---------------------------------------------------------------------------
# Allow running from any working directory
import os as _os
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.data_loader import loader  # singleton DataLoader
from backend.strategies import STRATEGY_REGISTRY as REGISTRY  # dict[str, type[Strategy]]

IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal dataclass (canonical definition — imported by other modules)
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """One trading signal produced by a strategy scanner pass."""
    symbol: str                          # e.g. "RELIANCE.NS"
    name: str                            # human-readable display name
    strategy: str                        # strategy key from REGISTRY
    direction: str                       # "LONG" | "SHORT"
    entry: float                         # suggested entry price (bar close)
    sl: float                            # stop-loss price
    target1: float                       # first target price
    target2: float                       # second target price (1.5× risk)
    rr: float                            # reward-to-risk ratio at target1
    confidence: float                    # 1–10 composite score
    confidence_factors: dict[str, Any]   # sub-scores feeding confidence
    reasoning: str                       # human-readable rationale string
    signal_time: str                     # ISO-8601 timestamp (IST)
    option_suggestion: dict[str, Any]    # {"type":"CE"/"PE","strike":int,...}
    tag: str                             # e.g. "ORB_BULL", "VWAP_REV_BEAR"
    bars_ago: int                        # how many 5-min bars ago signal fired

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "strategy": self.strategy,
            "direction": self.direction,
            "entry": round(self.entry, 2),
            "sl": round(self.sl, 2),
            "target1": round(self.target1, 2),
            "target2": round(self.target2, 2),
            "rr": round(self.rr, 2),
            "confidence": round(self.confidence, 1),
            "confidence_factors": self.confidence_factors,
            "reasoning": self.reasoning,
            "signal_time": self.signal_time,
            "option_suggestion": self.option_suggestion,
            "tag": self.tag,
            "bars_ago": self.bars_ago,
        }


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------

_VIX_THRESHOLDS = {
    "LOW": 13.0,
    "ELEVATED": 18.0,
    "HIGH": 25.0,
    # above 25 → EXTREME
}

# Regime keys (used in backtest breakdown)
REGIME_TRENDING_BULL = "TRENDING_BULL"
REGIME_TRENDING_BEAR = "TRENDING_BEAR"
REGIME_SIDEWAYS_LOW_VIX = "SIDEWAYS_LOW_VIX"
REGIME_SIDEWAYS_HIGH_VIX = "SIDEWAYS_HIGH_VIX"
REGIME_VOLATILE = "VOLATILE"


def classify_regime(df_nifty_5m: pd.DataFrame, vix: float) -> dict:
    """
    Classify current market regime from NIFTY 5m data and India VIX.

    Parameters
    ----------
    df_nifty_5m : pd.DataFrame
        5-minute OHLCV for NIFTY index, IST-indexed. Should be at least
        30 bars (2.5 hours) for meaningful classification.
    vix : float
        Current India VIX value (e.g. 14.5).

    Returns
    -------
    dict with keys:
        regime    : str — one of the REGIME_* constants
        vix_band  : str — "LOW" / "ELEVATED" / "HIGH" / "EXTREME"
        trend     : str — "UP" / "DOWN" / "FLAT"
        vix       : float
        adx_proxy : float — ADX-proxy from 14-bar DM
        trend_strength : str — "STRONG" / "MODERATE" / "WEAK"
    """
    if df_nifty_5m is None or df_nifty_5m.empty:
        return {
            "regime": REGIME_SIDEWAYS_LOW_VIX,
            "vix_band": "ELEVATED",
            "trend": "FLAT",
            "vix": vix,
            "adx_proxy": 0.0,
            "trend_strength": "WEAK",
        }

    # VIX band
    if vix < _VIX_THRESHOLDS["LOW"]:
        vix_band = "LOW"
    elif vix < _VIX_THRESHOLDS["ELEVATED"]:
        vix_band = "ELEVATED"
    elif vix < _VIX_THRESHOLDS["HIGH"]:
        vix_band = "HIGH"
    else:
        vix_band = "EXTREME"

    close = df_nifty_5m["close"]
    n = len(close)

    # EMA(20) vs EMA(50) trend
    if n >= 20:
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1] if n >= 50 else ema20
        last_close = float(close.iloc[-1])
        if last_close > ema20 and ema20 > ema50:
            trend = "UP"
        elif last_close < ema20 and ema20 < ema50:
            trend = "DOWN"
        else:
            trend = "FLAT"
    else:
        # Simplified: compare first half avg to second half avg
        mid = n // 2
        trend = (
            "UP" if close.iloc[mid:].mean() > close.iloc[:mid].mean()
            else "DOWN"
        )

    # ADX proxy: ratio of directional move to ATR over last 14 bars
    adx_proxy = 0.0
    if n >= 15:
        window = min(14, n - 1)
        high_w = df_nifty_5m["high"].iloc[-window:]
        low_w = df_nifty_5m["low"].iloc[-window:]
        close_w = df_nifty_5m["close"].iloc[-window:]
        prev_close_w = df_nifty_5m["close"].shift(1).iloc[-window:]

        tr = pd.concat([
            high_w - low_w,
            (high_w - prev_close_w).abs(),
            (low_w - prev_close_w).abs(),
        ], axis=1).max(axis=1)
        atr_val = float(tr.mean()) if not tr.isna().all() else 1.0

        dm_up = float((high_w.diff().clip(lower=0)).mean())
        dm_dn = float(((-low_w.diff()).clip(lower=0)).mean())
        di_plus = dm_up / atr_val * 100.0 if atr_val > 0 else 0.0
        di_minus = dm_dn / atr_val * 100.0 if atr_val > 0 else 0.0
        di_sum = di_plus + di_minus
        adx_proxy = abs(di_plus - di_minus) / di_sum * 100.0 if di_sum > 0 else 0.0

    if adx_proxy >= 30:
        trend_strength = "STRONG"
    elif adx_proxy >= 18:
        trend_strength = "MODERATE"
    else:
        trend_strength = "WEAK"

    # Final regime label
    if vix_band == "EXTREME" or vix > 22:
        regime = REGIME_VOLATILE
    elif trend == "UP" and trend_strength in ("STRONG", "MODERATE"):
        regime = REGIME_TRENDING_BULL
    elif trend == "DOWN" and trend_strength in ("STRONG", "MODERATE"):
        regime = REGIME_TRENDING_BEAR
    elif vix_band in ("LOW", "ELEVATED"):
        regime = REGIME_SIDEWAYS_LOW_VIX
    else:
        regime = REGIME_SIDEWAYS_HIGH_VIX

    return {
        "regime": regime,
        "vix_band": vix_band,
        "trend": trend,
        "vix": round(vix, 2),
        "adx_proxy": round(adx_proxy, 2),
        "trend_strength": trend_strength,
    }


# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------

def _score_confidence(
    entry: float,
    sl: float,
    target1: float,
    direction: str,
    df: pd.DataFrame,
    strategy_key: str,
    vix: float,
    regime: dict,
    signal_meta: dict,
) -> tuple[float, dict[str, Any]]:
    """
    Produce a composite confidence score (1–10) and factor breakdown.

    Factors (each 0–10, then weighted):
      1. R:R quality      (weight 25%)
      2. Volume spike     (weight 20%)
      3. Regime alignment (weight 20%)
      4. VIX environment  (weight 15%)
      5. Signal freshness from strategy (weight 10%)
      6. ATR / volatility (weight 10%)

    Returns (score_1_to_10, factors_dict)
    """
    factors: dict[str, float] = {}

    # 1. R:R quality — ideal ≥ 2:1
    risk = abs(entry - sl) if sl else 1.0
    rr = abs(target1 - entry) / risk if risk > 0 else 0.0
    rr_score = min(rr / 2.0 * 10.0, 10.0)  # 2:1 = 10, 1:1 = 5
    factors["rr_quality"] = round(rr_score, 1)

    # 2. Volume spike on last bar vs 20-bar average
    vol_score = 5.0  # default neutral
    if len(df) >= 5 and "volume" in df.columns:
        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].rolling(20, min_periods=5).mean().iloc[-1]) or 1.0
        ratio = last_vol / avg_vol
        vol_score = min(ratio / 2.0 * 10.0, 10.0)  # 2× avg = 10
    factors["volume_spike"] = round(vol_score, 1)

    # 3. Regime alignment
    regime_label = regime.get("regime", REGIME_SIDEWAYS_LOW_VIX)
    trend = regime.get("trend", "FLAT")
    if direction == "LONG" and regime_label == REGIME_TRENDING_BULL:
        regime_score = 10.0
    elif direction == "SHORT" and regime_label == REGIME_TRENDING_BEAR:
        regime_score = 10.0
    elif regime_label == REGIME_VOLATILE:
        regime_score = 3.0
    elif (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN"):
        regime_score = 7.0
    else:
        regime_score = 5.0
    factors["regime_alignment"] = round(regime_score, 1)

    # 4. VIX environment
    vix_band = regime.get("vix_band", "ELEVATED")
    vix_scores = {"LOW": 8.0, "ELEVATED": 7.0, "HIGH": 5.0, "EXTREME": 3.0}
    vix_score = vix_scores.get(vix_band, 5.0)
    factors["vix_environment"] = round(vix_score, 1)

    # 5. Strategy signal quality (derived from signal_meta)
    strat_score = 7.0  # base
    if "divergence_confirmed" in signal_meta:
        strat_score = 9.0 if signal_meta["divergence_confirmed"] else 5.0
    elif strategy_key == "opening_range_breakout":
        strat_score = 8.0  # ORB tends to have solid conviction
    elif strategy_key == "gap_fade":
        strat_score = 6.0  # gap fades are counter-trend, lower confidence
    factors["strategy_signal"] = round(strat_score, 1)

    # 6. ATR context — prefer signals with decent volatility
    atr_score = 5.0
    if len(df) >= 15:
        close_series = df["close"]
        prev_close = close_series.shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_val = float(tr.ewm(com=13, adjust=False).mean().iloc[-1])
        last_close = float(df["close"].iloc[-1])
        atr_pct = atr_val / last_close * 100.0 if last_close > 0 else 0.0
        # Ideal ATR% 0.2–0.5 on 5m bars → score 8–10; too low or too high → lower
        if 0.15 <= atr_pct <= 0.6:
            atr_score = 9.0
        elif 0.1 <= atr_pct < 0.15 or 0.6 < atr_pct <= 1.0:
            atr_score = 6.0
        else:
            atr_score = 3.0
    factors["atr_context"] = round(atr_score, 1)

    # Weighted composite
    weights = {
        "rr_quality": 0.25,
        "volume_spike": 0.20,
        "regime_alignment": 0.20,
        "vix_environment": 0.15,
        "strategy_signal": 0.10,
        "atr_context": 0.10,
    }
    composite = sum(factors[k] * weights[k] for k in factors)
    composite = round(max(1.0, min(10.0, composite)), 1)

    return composite, factors


# ---------------------------------------------------------------------------
# Reasoning builder
# ---------------------------------------------------------------------------

def _build_reasoning(
    signal_meta: dict,
    direction: str,
    entry: float,
    sl: float,
    target1: float,
    rr: float,
    confidence: float,
    regime: dict,
    strategy_key: str,
) -> str:
    """Build a human-readable single-paragraph reasoning string."""
    dir_word = "bullish" if direction == "LONG" else "bearish"
    parts: list[str] = []

    strat_names = {
        "opening_range_breakout": "Opening Range Breakout",
        "supertrend_ema": "Supertrend + EMA",
        "vwap_reversal": "VWAP Reversal",
        "rsi_divergence": "RSI Divergence",
        "gap_fade": "Gap Fade",
    }
    strat_label = strat_names.get(strategy_key, strategy_key)
    parts.append(f"{strat_label} generated a {dir_word} signal.")

    # Strategy-specific context
    if strategy_key == "opening_range_breakout":
        parts.append(
            f"Price closed above the opening range high at ₹{entry:.2f} on elevated volume."
        )
    elif strategy_key == "supertrend_ema":
        parts.append(
            f"Supertrend flipped bullish above EMA(20) at ₹{entry:.2f}."
        )
    elif strategy_key == "vwap_reversal":
        parts.append(
            f"Price extended {'below' if direction=='LONG' else 'above'} VWAP and is recovering at ₹{entry:.2f}."
        )
    elif strategy_key == "rsi_divergence":
        parts.append(
            f"RSI divergence detected; price made lower low while RSI made higher low. Confirmation candle at ₹{entry:.2f}."
        )
    elif strategy_key == "gap_fade":
        parts.append(f"Gap-down open faded at ₹{entry:.2f}, targeting previous day's close.")

    # Regime context
    regime_label = regime.get("regime", "").replace("_", " ").title()
    parts.append(f"Market regime: {regime_label} (VIX {regime.get('vix', 0):.1f}).")

    # Risk params
    risk = abs(entry - sl)
    parts.append(
        f"Entry ₹{entry:.2f} | SL ₹{sl:.2f} | Target ₹{target1:.2f} "
        f"(Risk ₹{risk:.2f}, R:R {rr:.1f}:1). Confidence {confidence}/10."
    )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Option suggestion stub (calls OptionsEngine lazily to avoid circular imports)
# ---------------------------------------------------------------------------

def _quick_option_suggestion(
    direction: str,
    entry: float,
    confidence: float,
    vix: float,
) -> dict:
    """
    Lightweight option suggestion without a full OptionsEngine call.
    Returns the strike and type based on direction; the OptionsEngine
    in options.py provides full BS pricing.
    """
    opt_type = "CE" if direction == "LONG" else "PE"
    # ATM strike (50-pt grid)
    atm = int(round(entry / 50.0) * 50)
    # Slightly OTM for defined-risk if confidence < 6
    if confidence < 6.0:
        strike = atm + 50 if opt_type == "CE" else atm - 50
        spread = True
    else:
        strike = atm
        spread = False

    return {
        "type": opt_type,
        "strike": strike,
        "delta_approx": 0.50 if not spread else 0.35,
        "spread": spread,
        "note": (
            "Consider bull-call spread (low confidence)" if spread and opt_type == "CE"
            else "Consider bear-put spread (low confidence)" if spread
            else f"ATM {opt_type} recommended"
        ),
    }


# ---------------------------------------------------------------------------
# ScanEngine
# ---------------------------------------------------------------------------

_NIFTY_SYMBOL = "^NSEI"
_VIX_SYMBOL = "^INDIAVIX"
_MAX_WORKERS = 10
_SCAN_LOOKBACK_DAYS = 5      # days of 5m history to fetch per symbol
_BARS_PER_DAY = 75           # 9:15–15:30 at 5-min = 75 bars

# Market hours (IST)
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MIN = 15
_MARKET_CLOSE_HOUR = 15
_MARKET_CLOSE_MIN = 30


class ScanEngine:
    """
    Multi-symbol signal scanner.

    Usage
    -----
    engine = ScanEngine()
    result = engine.scan_all(symbols=[...], strategy_key="opening_range_breakout", vix=14.5)
    active = engine.get_active_signals(result["signals"], max_bars_old=6)
    """

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._max_workers = max_workers
        logger.info("ScanEngine initialised (max_workers=%d)", max_workers)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_symbol(
        self,
        symbol: str,
        name: str,
        strategy_key: str,
        df_5m: pd.DataFrame,
        vix: float,
        regime: dict,
    ) -> list[Signal]:
        """
        Run a single strategy on one symbol's 5-minute data.

        Parameters
        ----------
        symbol       : yfinance ticker e.g. "RELIANCE.NS"
        name         : human-readable name e.g. "Reliance Industries"
        strategy_key : key into strategies.REGISTRY
        df_5m        : 5-minute OHLCV DataFrame (IST-indexed)
        vix          : current India VIX
        regime       : output of classify_regime()

        Returns
        -------
        list[Signal] — usually 0 or 1 signal; occasionally multiple if
        the strategy fires on several bars.
        """
        if df_5m is None or df_5m.empty:
            logger.debug("scan_symbol: %s — empty DataFrame, skip", symbol)
            return []

        if strategy_key not in REGISTRY:
            logger.warning("scan_symbol: unknown strategy_key '%s'", strategy_key)
            return []

        StrategyClass = REGISTRY[strategy_key]
        strategy = StrategyClass()

        try:
            result = strategy.generate_signals(df_5m)
        except Exception as exc:
            logger.warning("scan_symbol: %s strategy error — %s", symbol, exc, exc_info=True)
            return []

        if not result.entries.any():
            return []

        # Find bars where entries are True
        entry_bars = result.entries[result.entries].index
        now = datetime.now(IST)
        n_bars_total = len(df_5m)

        signals: list[Signal] = []

        for entry_ts in entry_bars:
            try:
                bar_idx = df_5m.index.get_loc(entry_ts)
            except KeyError:
                continue

            bars_ago = n_bars_total - 1 - bar_idx

            entry_px = float(df_5m["close"].iloc[bar_idx])
            sl_px = float(result.sl.iloc[bar_idx]) if not np.isnan(result.sl.iloc[bar_idx]) else entry_px * 0.99
            tgt_px = float(result.target.iloc[bar_idx]) if not np.isnan(result.target.iloc[bar_idx]) else entry_px * 1.01

            # Determine direction from strategy meta or price action
            direction = self._infer_direction(
                strategy_key, entry_px, sl_px, tgt_px, result.signal_meta
            )

            # Compute secondary target (1.5× risk from entry)
            risk = abs(entry_px - sl_px)
            target2 = entry_px + 1.5 * risk if direction == "LONG" else entry_px - 1.5 * risk
            rr = abs(tgt_px - entry_px) / risk if risk > 0 else 0.0

            confidence, factors = _score_confidence(
                entry_px, sl_px, tgt_px, direction, df_5m,
                strategy_key, vix, regime, result.signal_meta,
            )

            reasoning = _build_reasoning(
                result.signal_meta, direction, entry_px, sl_px, tgt_px,
                rr, confidence, regime, strategy_key,
            )

            opt_sug = _quick_option_suggestion(direction, entry_px, confidence, vix)

            tag = self._build_tag(strategy_key, direction)

            signal = Signal(
                symbol=symbol,
                name=name,
                strategy=strategy_key,
                direction=direction,
                entry=round(entry_px, 2),
                sl=round(sl_px, 2),
                target1=round(tgt_px, 2),
                target2=round(target2, 2),
                rr=round(rr, 2),
                confidence=confidence,
                confidence_factors=factors,
                reasoning=reasoning,
                signal_time=entry_ts.isoformat(),
                option_suggestion=opt_sug,
                tag=tag,
                bars_ago=bars_ago,
            )
            signals.append(signal)

        return signals

    def scan_all(
        self,
        symbols: list[str],
        strategy_key: str,
        vix: float,
    ) -> dict:
        """
        Scan all symbols in parallel, classify regime, and return a
        structured result dictionary.

        Parameters
        ----------
        symbols      : list of yfinance tickers (with .NS suffix for NSE)
        strategy_key : key into strategies.REGISTRY
        vix          : current India VIX

        Returns
        -------
        dict with keys:
            signals      : list[Signal]
            regime       : dict (from classify_regime)
            scanned_at   : str (ISO-8601 IST timestamp)
            market_open  : bool
        """
        scan_start = time.perf_counter()
        now = datetime.now(IST)
        market_open = self._is_market_open(now)
        scanned_at = now.isoformat()

        logger.info(
            "scan_all: %d symbols | strategy=%s | vix=%.1f | market_open=%s",
            len(symbols), strategy_key, vix, market_open,
        )

        # Fetch NIFTY 5m for regime classification (last 2 days)
        regime: dict = {}
        try:
            nifty_5m = loader.get_5m_ohlcv(
                _NIFTY_SYMBOL,
                start=(now.date() - pd.Timedelta(days=4)).date() if hasattr(now.date(), 'date') else now.date(),
                end=now.date(),
            )
            regime = classify_regime(nifty_5m, vix)
        except Exception as exc:
            logger.warning("scan_all: regime classification failed — %s", exc)
            regime = {
                "regime": REGIME_SIDEWAYS_LOW_VIX,
                "vix_band": "ELEVATED",
                "trend": "FLAT",
                "vix": round(vix, 2),
                "adx_proxy": 0.0,
                "trend_strength": "WEAK",
            }

        # Load symbol metadata from nifty50 universe (symbol → name mapping)
        name_map: dict[str, str] = {}
        try:
            universe = loader.get_nifty50_universe()
            name_map = {u["symbol"]: u.get("name", u["symbol"]) for u in universe}
        except Exception:
            pass

        # Determine date window for 5m fetch
        today = now.date()
        start_date = (now - pd.Timedelta(days=_SCAN_LOOKBACK_DAYS)).date()

        # Worker: fetch data + scan one symbol
        def _worker(sym: str) -> list[Signal]:
            sym_name = name_map.get(sym, sym)
            try:
                df_5m = loader.get_5m_ohlcv(sym, start_date, today)
            except Exception as exc:
                logger.debug("scan_all: data fetch failed for %s — %s", sym, exc)
                return []
            return self.scan_symbol(sym, sym_name, strategy_key, df_5m, vix, regime)

        all_signals: list[Signal] = []
        n_workers = min(self._max_workers, len(symbols))

        with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="scanner") as pool:
            future_map = {pool.submit(_worker, sym): sym for sym in symbols}
            for future in as_completed(future_map):
                sym = future_map[future]
                try:
                    sigs = future.result()
                    all_signals.extend(sigs)
                    if sigs:
                        logger.debug("scan_all: %s → %d signal(s)", sym, len(sigs))
                except Exception as exc:
                    logger.warning("scan_all: worker exception for %s — %s", sym, exc)

        # Sort by confidence descending, then recency (bars_ago ascending)
        all_signals.sort(key=lambda s: (-s.confidence, s.bars_ago))

        elapsed = time.perf_counter() - scan_start
        logger.info(
            "scan_all done: %d symbols → %d signals in %.2fs",
            len(symbols), len(all_signals), elapsed,
        )

        return {
            "signals": all_signals,
            "regime": regime,
            "scanned_at": scanned_at,
            "market_open": market_open,
        }

    def get_active_signals(
        self,
        all_signals: list[Signal],
        max_bars_old: int = 6,
    ) -> list[Signal]:
        """
        Filter signals to only those fired within max_bars_old 5-minute bars.

        max_bars_old=6 → signals from the last 30 minutes are considered active.

        Parameters
        ----------
        all_signals  : list[Signal] (output of scan_all)
        max_bars_old : maximum age in 5-minute bars (default 6 = 30 min)

        Returns
        -------
        list[Signal] — subset of all_signals where bars_ago <= max_bars_old
        """
        active = [s for s in all_signals if s.bars_ago <= max_bars_old]
        logger.debug(
            "get_active_signals: %d/%d signals within %d bars",
            len(active), len(all_signals), max_bars_old,
        )
        return active

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_market_open(now: datetime) -> bool:
        """True if now is within NSE trading hours (Mon–Fri, 09:15–15:30 IST)."""
        if now.weekday() >= 5:  # Saturday or Sunday
            return False
        t = now.time()
        import datetime as _dt
        open_t = _dt.time(_MARKET_OPEN_HOUR, _MARKET_OPEN_MIN)
        close_t = _dt.time(_MARKET_CLOSE_HOUR, _MARKET_CLOSE_MIN)
        return open_t <= t <= close_t

    @staticmethod
    def _infer_direction(
        strategy_key: str,
        entry: float,
        sl: float,
        target: float,
        signal_meta: dict,
    ) -> str:
        """Determine LONG or SHORT from sl/target relative to entry."""
        # If target is above entry → LONG; otherwise → SHORT
        if target > entry:
            return "LONG"
        if target < entry:
            return "SHORT"
        # Fallback: use sl position
        return "LONG" if sl < entry else "SHORT"

    @staticmethod
    def _build_tag(strategy_key: str, direction: str) -> str:
        """Build a short tag string like 'ORB_BULL' or 'VWAP_REV_BEAR'."""
        abbr = {
            "opening_range_breakout": "ORB",
            "supertrend_ema": "ST_EMA",
            "vwap_reversal": "VWAP_REV",
            "rsi_divergence": "RSI_DIV",
            "gap_fade": "GAP_FADE",
        }
        dir_tag = "BULL" if direction == "LONG" else "BEAR"
        return f"{abbr.get(strategy_key, strategy_key.upper())}_{dir_tag}"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scan_engine = ScanEngine()
