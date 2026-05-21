"""
replay.py — Bar-by-bar trade replay engine for NIFTY intraday sessions.

Allows the frontend chart to step through a historical day one 5-minute bar
at a time, with all indicators and strategy signals recomputed incrementally
so the user can practice reading setups without look-ahead bias.

Public surface:
  get_session_data(symbol, trade_date, df_5m)  → full session dict
  replay_signals(bars_so_far, strategy_key, vix, symbol) → list[signal dict]
  list_available_dates(symbol) → list[str]

This module has no FastAPI dependency — it is pure computation.  Wire it into
an API layer (e.g. api/replay.py) to expose it over HTTP/WS.

Dependencies: pandas, numpy, pathlib, sys-path-relative strategy imports.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytz

# ── Project path bootstrap ────────────────────────────────────────────────────
# Ensure the project root is on sys.path so that `strategies` and `backend`
# packages are importable regardless of how this module is invoked.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.strategies import STRATEGY_REGISTRY as REGISTRY, Strategy  # noqa: E402

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

IST = pytz.timezone("Asia/Kolkata")

_CACHE_DIR = _ROOT / "data" / "cache" / "5m"

# Market session window (IST)
_SESSION_START = datetime.strptime("09:15", "%H:%M").time()
_SESSION_END   = datetime.strptime("15:30", "%H:%M").time()

# Indicator parameters (match the defaults used everywhere in the project)
_EMA_FAST   = 9
_EMA_SLOW   = 21
_ST_PERIOD  = 7
_ST_MULT    = 3.0
_RSI_PERIOD = 14
_ATR_PERIOD = 14
_ORB_BARS   = 3   # 3 × 5-min = 15-min opening range

# ---------------------------------------------------------------------------
# Internal indicator helpers (self-contained — no import from indicators.py
# so that this module can run independently of the PYTHONPATH configuration
# at import time for the frontend replay API).
# ---------------------------------------------------------------------------


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False, min_periods=period).mean()


def _rsi(series: pd.Series, period: int = _RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _vwap_series(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP, reset at the start of each calendar date."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = tp * df["volume"]
    date_key = df.index.normalize()
    cum_tpv = tpv.groupby(date_key).cumsum()
    cum_vol = df["volume"].groupby(date_key).cumsum()
    return cum_tpv / cum_vol.replace(0, np.nan)


def _supertrend(
    df: pd.DataFrame,
    period: int = _ST_PERIOD,
    multiplier: float = _ST_MULT,
) -> tuple[pd.Series, pd.Series]:
    """
    Supertrend indicator.

    Returns
    -------
    (st_line, direction)
        st_line   : float series — the current supertrend value per bar.
        direction : int series  — +1 bullish, -1 bearish.
    """
    atr_vals = _atr(df, period).values
    close = df["close"].values
    hl2 = ((df["high"] + df["low"]) / 2.0).values

    n = len(df)
    basic_ub = hl2 + multiplier * atr_vals
    basic_lb = hl2 - multiplier * atr_vals

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        # Tighten upper band — never widen
        if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i - 1]

        # Tighten lower band — never widen
        if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i - 1]

        if np.isnan(st[i - 1]):
            st[i] = final_lb[i]
            direction[i] = 1
        elif st[i - 1] == final_ub[i - 1]:
            # Bearish bar
            if close[i] > final_ub[i]:
                st[i] = final_lb[i]
                direction[i] = 1
            else:
                st[i] = final_ub[i]
                direction[i] = -1
        else:
            # Bullish bar
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


def _compute_orb(day_df: pd.DataFrame) -> dict:
    """
    Compute the Opening Range Breakout levels for a single-day DataFrame.

    Uses the first _ORB_BARS bars (i.e. first 15 minutes on 5-min data).
    Returns {"high": float, "low": float, "width": float}.
    """
    if len(day_df) < _ORB_BARS:
        logger.debug("_compute_orb: fewer than %d bars — using all bars", _ORB_BARS)
        or_bars = day_df
    else:
        or_bars = day_df.iloc[:_ORB_BARS]

    orb_high = float(or_bars["high"].max())
    orb_low = float(or_bars["low"].min())
    return {
        "high": round(orb_high, 2),
        "low": round(orb_low, 2),
        "width": round(orb_high - orb_low, 2),
    }


def _df_to_bar_dicts(
    day_df: pd.DataFrame,
    vwap_s: pd.Series,
    ema9_s: pd.Series,
    ema21_s: pd.Series,
    st_line: pd.Series,
    st_dir: pd.Series,
    rsi_s: pd.Series,
    atr_s: pd.Series,
) -> list[dict]:
    """
    Convert aligned indicator series into the bar-dict format expected by the
    frontend replay chart.
    """
    bars: list[dict] = []
    for ts, row in day_df.iterrows():
        try:
            unix_ms = int(ts.timestamp() * 1000)
        except Exception:
            unix_ms = 0

        idx = ts

        def _fv(series: pd.Series) -> float | None:
            """Safe float value from series at index, NaN → None."""
            try:
                v = series.at[idx]
                return None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), 4)
            except (KeyError, TypeError):
                return None

        bars.append(
            {
                "time": unix_ms,
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row["volume"]),
                "vwap": _fv(vwap_s),
                "ema9": _fv(ema9_s),
                "ema21": _fv(ema21_s),
                "supertrend": _fv(st_line),
                "supertrend_dir": (
                    "UP" if _fv(st_dir) == 1 else "DOWN"
                    if _fv(st_dir) == -1 else None
                ),
                "rsi": _fv(rsi_s),
                "atr": _fv(atr_s),
            }
        )
    return bars


# ---------------------------------------------------------------------------
# Public API — ReplayEngine
# ---------------------------------------------------------------------------


class ReplayEngine:
    """
    Stateless replay engine.

    Call ``get_session_data`` to initialise a replay session (used when the
    user selects a date in the UI).  Then call ``replay_signals`` repeatedly
    as the user steps forward bar by bar.
    """

    # ------------------------------------------------------------------
    # Session initialisation
    # ------------------------------------------------------------------

    def get_session_data(
        self,
        symbol: str,
        trade_date: date,
        df_5m: pd.DataFrame,
    ) -> dict:
        """
        Extract one day's 5-minute data, compute all indicators, and return a
        complete session dict ready for the frontend replay player.

        Parameters
        ----------
        symbol     : str            Ticker (e.g. "NIFTY" or "RELIANCE.NS").
        trade_date : date           Session date to replay.
        df_5m      : pd.DataFrame   Multi-day 5-minute OHLCV (IST-indexed).
                                    Must contain ``trade_date`` rows.

        Returns
        -------
        dict
            {
                "date": str,
                "symbol": str,
                "bars": [...],       # all bars with indicators
                "orb": {...},
                "daily_stats": {...},
                "total_bars": int
            }
        """
        if df_5m is None or df_5m.empty:
            raise ValueError(f"get_session_data: df_5m is empty for {symbol}")

        if not isinstance(df_5m.index, pd.DatetimeIndex):
            raise TypeError("df_5m must have a DatetimeIndex")

        # Ensure IST-aware index
        if df_5m.index.tzinfo is None:
            df_5m = df_5m.copy()
            df_5m.index = df_5m.index.tz_localize(IST)
        else:
            df_5m = df_5m.copy()
            df_5m.index = df_5m.index.tz_convert(IST)

        # Slice to the requested session date
        date_start = IST.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 0, 0, 0)
        )
        date_end = IST.localize(
            datetime(trade_date.year, trade_date.month, trade_date.day, 23, 59, 59)
        )
        day_df = df_5m.loc[date_start:date_end].copy()

        if day_df.empty:
            raise ValueError(
                f"get_session_data: no data found for {symbol} on {trade_date}"
            )

        logger.info(
            "ReplayEngine.get_session_data: %s %s — %d bars",
            symbol,
            trade_date,
            len(day_df),
        )

        # ── Compute indicators (full-day context for warm-up) ───────────────
        # Use as much preceding context as available for indicator warm-up,
        # but only return bars from this session date.
        context_start = IST.localize(
            datetime(
                (trade_date - timedelta(days=30)).year,
                (trade_date - timedelta(days=30)).month,
                (trade_date - timedelta(days=30)).day,
                0, 0, 0,
            )
        )
        context_df = df_5m.loc[context_start:date_end].copy()
        if context_df.empty:
            context_df = day_df.copy()

        vwap_s   = _vwap_series(context_df)
        ema9_s   = _ema(context_df["close"], _EMA_FAST)
        ema21_s  = _ema(context_df["close"], _EMA_SLOW)
        st_line, st_dir = _supertrend(context_df)
        rsi_s    = _rsi(context_df["close"])
        atr_s    = _atr(context_df)

        # Slice all indicator series to the session day
        vwap_day  = vwap_s.reindex(day_df.index)
        ema9_day  = ema9_s.reindex(day_df.index)
        ema21_day = ema21_s.reindex(day_df.index)
        st_day    = st_line.reindex(day_df.index)
        std_day   = st_dir.reindex(day_df.index)
        rsi_day   = rsi_s.reindex(day_df.index)
        atr_day   = atr_s.reindex(day_df.index)

        # ── ORB ──────────────────────────────────────────────────────────
        orb = _compute_orb(day_df)

        # ── Daily stats ───────────────────────────────────────────────────
        # Look for the previous session's closing bar
        prev_context = df_5m.loc[:date_start - timedelta(seconds=1)]
        if not prev_context.empty:
            prev_close = float(prev_context["close"].iloc[-1])
        else:
            prev_close = float(day_df["open"].iloc[0])

        first_open = float(day_df["open"].iloc[0])
        gap_pct = (
            round((first_open - prev_close) / prev_close * 100.0, 4)
            if prev_close > 0
            else 0.0
        )

        daily_stats = {
            "prev_close": round(prev_close, 2),
            "gap_pct": gap_pct,
        }

        # ── Build bar list ────────────────────────────────────────────────
        bars = _df_to_bar_dicts(
            day_df,
            vwap_day,
            ema9_day,
            ema21_day,
            st_day,
            std_day,
            rsi_day,
            atr_day,
        )

        return {
            "date": trade_date.isoformat(),
            "symbol": symbol,
            "bars": bars,
            "orb": orb,
            "daily_stats": daily_stats,
            "total_bars": len(bars),
        }

    # ------------------------------------------------------------------
    # Incremental signal replay
    # ------------------------------------------------------------------

    def replay_signals(
        self,
        bars_so_far: list[dict],
        strategy_key: str,
        vix: float,
        symbol: str,
    ) -> list[dict]:
        """
        Reconstruct a partial DataFrame from the bars seen so far and run the
        requested strategy to check for signals on the LATEST bar only.

        This function is called by the frontend on every bar-advance event
        during replay.  It deliberately avoids any look-ahead by operating only
        on ``bars_so_far``.

        Parameters
        ----------
        bars_so_far  : list[dict]   Bar dicts as emitted by get_session_data.
                                    Must have at least 2 bars.
        strategy_key : str          Key into strategies.REGISTRY.
        vix          : float        Current VIX reading (used for regime gating
                                    if the strategy supports it).
        symbol       : str          Ticker (for signal dict annotation).

        Returns
        -------
        list[dict]
            Zero or more signal dicts generated on the last bar:
            {
                "bar_index": int,
                "time": int,       # unix_ms of the bar
                "symbol": str,
                "strategy": str,
                "type": str,       # "ENTRY" | "EXIT"
                "direction": str,  # "LONG" | "SHORT"
                "price": float,
                "sl": float | None,
                "target": float | None,
                "meta": dict
            }
        """
        if len(bars_so_far) < 2:
            return []

        strategy_cls = REGISTRY.get(strategy_key)
        if strategy_cls is None:
            logger.warning(
                "replay_signals: unknown strategy_key '%s'. Available: %s",
                strategy_key,
                list(REGISTRY.keys()),
            )
            return []

        # ── Reconstruct DataFrame ─────────────────────────────────────────
        try:
            df = _bars_to_dataframe(bars_so_far)
        except Exception as exc:
            logger.error("replay_signals: failed to build DataFrame: %s", exc)
            return []

        if df.empty:
            return []

        # ── Run strategy ──────────────────────────────────────────────────
        try:
            strategy: Strategy = strategy_cls()
            result = strategy.generate_signals(df)
        except Exception as exc:
            logger.error(
                "replay_signals: strategy %s raised: %s", strategy_key, exc
            )
            return []

        # ── Inspect the last bar ──────────────────────────────────────────
        last_idx = df.index[-1]
        bar_index = len(bars_so_far) - 1
        time_ms = bars_so_far[-1].get("time", 0)
        close_price = float(df["close"].iloc[-1])

        signals_out: list[dict] = []

        # Entry signal
        if result.entries.iloc[-1]:
            sl_val = result.sl.iloc[-1]
            tgt_val = result.target.iloc[-1]
            signals_out.append(
                {
                    "bar_index": bar_index,
                    "time": time_ms,
                    "symbol": symbol,
                    "strategy": strategy_key,
                    "type": "ENTRY",
                    "direction": "LONG",   # all current strategies are long-only
                    "price": round(close_price, 2),
                    "sl": round(float(sl_val), 2) if not _is_nan(sl_val) else None,
                    "target": round(float(tgt_val), 2) if not _is_nan(tgt_val) else None,
                    "meta": result.signal_meta,
                    "vix": vix,
                }
            )

        # Exit signal
        if result.exits.iloc[-1]:
            signals_out.append(
                {
                    "bar_index": bar_index,
                    "time": time_ms,
                    "symbol": symbol,
                    "strategy": strategy_key,
                    "type": "EXIT",
                    "direction": "LONG",
                    "price": round(close_price, 2),
                    "sl": None,
                    "target": None,
                    "meta": result.signal_meta,
                    "vix": vix,
                }
            )

        return signals_out

    # ------------------------------------------------------------------
    # Available dates
    # ------------------------------------------------------------------

    def list_available_dates(self, symbol: str) -> list[str]:
        """
        Return sorted list of session dates for which cached 5-minute Parquet
        files exist for ``symbol``.

        Looks in ``data/cache/5m/<safe_symbol>/`` for files named
        ``YYYY-MM.parquet``, reads them, and extracts all unique dates.

        Parameters
        ----------
        symbol : str   Ticker (e.g. "NIFTY" or "RELIANCE.NS").

        Returns
        -------
        list[str]   ISO date strings ("YYYY-MM-DD"), sorted ascending.
        """
        safe_sym = _safe_symbol(symbol)
        sym_dir = _CACHE_DIR / safe_sym

        if not sym_dir.exists():
            logger.debug(
                "list_available_dates: cache dir not found for %s at %s",
                symbol,
                sym_dir,
            )
            return []

        parquet_files = sorted(sym_dir.glob("*.parquet"))
        if not parquet_files:
            return []

        dates: set[str] = set()
        for pf in parquet_files:
            try:
                df = pd.read_parquet(pf, columns=None)
                if df.index.tzinfo is None:
                    df.index = df.index.tz_localize("UTC").tz_convert(IST)
                else:
                    df.index = df.index.tz_convert(IST)
                for ts in df.index:
                    dates.add(ts.date().isoformat())
            except Exception as exc:
                logger.warning(
                    "list_available_dates: failed to read %s: %s", pf.name, exc
                )

        return sorted(dates)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_symbol(symbol: str) -> str:
    """Convert ticker to filesystem-safe name (mirrors DataLoader convention)."""
    return symbol.replace("^", "_").replace("/", "_").replace(":", "_")


def _is_nan(value: Any) -> bool:
    """Return True if value is None or float NaN."""
    if value is None:
        return True
    try:
        return np.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _bars_to_dataframe(bars: list[dict]) -> pd.DataFrame:
    """
    Reconstruct an OHLCV DataFrame from a list of bar dicts.

    The ``time`` field in each bar is expected to be unix milliseconds.
    Returns a DataFrame with an IST-aware DatetimeIndex.
    """
    rows = []
    for b in bars:
        ts_ms = b.get("time", 0)
        dt = datetime.utcfromtimestamp(ts_ms / 1000).replace(tzinfo=pytz.utc).astimezone(IST)
        rows.append(
            {
                "timestamp": dt,
                "open": float(b.get("open", 0)),
                "high": float(b.get("high", 0)),
                "low": float(b.get("low", 0)),
                "close": float(b.get("close", 0)),
                "volume": float(b.get("volume", 0)),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index)

    # Drop degenerate rows
    df = df[(df["close"] > 0) & (df["volume"] >= 0)].copy()
    return df


# ---------------------------------------------------------------------------
# Module-level singleton and convenience functions
# ---------------------------------------------------------------------------

_engine = ReplayEngine()


def get_session_data(
    symbol: str,
    trade_date: date,
    df_5m: pd.DataFrame,
) -> dict:
    """Module-level convenience wrapper — see ReplayEngine.get_session_data."""
    return _engine.get_session_data(symbol, trade_date, df_5m)


def replay_signals(
    bars_so_far: list[dict],
    strategy_key: str,
    vix: float,
    symbol: str,
) -> list[dict]:
    """Module-level convenience wrapper — see ReplayEngine.replay_signals."""
    return _engine.replay_signals(bars_so_far, strategy_key, vix, symbol)


def list_available_dates(symbol: str) -> list[str]:
    """Module-level convenience wrapper — see ReplayEngine.list_available_dates."""
    return _engine.list_available_dates(symbol)
