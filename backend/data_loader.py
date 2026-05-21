"""
DataLoader — singleton data layer for the Brainee Trades backend.

Responsibilities:
  - Fetch 5-minute and daily OHLCV from yfinance with Parquet disk cache.
  - Fetch live quotes with a 15-second in-memory cache (thread-safe).
  - Load the Nifty-50 universe from the project JSON file.
  - Fetch VIX daily series with Parquet cache.
  - Batch-quote symbols in parallel via ThreadPoolExecutor.
  - Purge stale Parquet files older than 7 days.

All data is returned in Asia/Kolkata (IST) timezone.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz
import yfinance as yf

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

NIFTY_SYMBOL    = "^NSEI"
BANKNIFTY_SYMBOL = "^NSEBANK"
VIX_SYMBOL      = "^INDIAVIX"

CACHE_DIR = Path("/home/user/Braineetrades/data/cache")
NIFTY50_JSON = Path("/home/user/Braineetrades/public/data/nifty50.json")

IST = pytz.timezone("Asia/Kolkata")

_LIVE_CACHE_TTL = 15          # seconds for in-memory live quote cache
_PARQUET_STALE_DAYS = 7       # purge Parquet files older than this
_MAX_WORKERS = 8              # ThreadPoolExecutor for batch quotes

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# In-memory cache helper
# ─────────────────────────────────────────────────────────────────────────────

class _MemCache:
    """
    Simple dict-backed in-memory cache with per-key TTL.
    All operations are thread-safe via a single lock.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}   # key → (value, expire_time)
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if time.monotonic() > expire_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]


# ─────────────────────────────────────────────────────────────────────────────
# DataLoader
# ─────────────────────────────────────────────────────────────────────────────

class DataLoader:
    """
    Central data-access object.  Instantiate once (see module-level `loader`).

    Parquet cache layout:
      CACHE_DIR/5m/<SYMBOL>/<YYYY-MM>.parquet    — one file per symbol per month
      CACHE_DIR/1d/<SYMBOL>.parquet              — full daily history per symbol
      CACHE_DIR/vix/vix_daily.parquet            — VIX daily close
    """

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "5m").mkdir(exist_ok=True)
        (CACHE_DIR / "1d").mkdir(exist_ok=True)
        (CACHE_DIR / "vix").mkdir(exist_ok=True)
        self._mem = _MemCache()
        logger.info("DataLoader initialised — cache dir: %s", CACHE_DIR)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        """Convert ticker to filesystem-safe name (e.g. ^NSEI → _NSEI)."""
        return symbol.replace("^", "_").replace("/", "_").replace(":", "_")

    @staticmethod
    def _to_ist(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure DataFrame index is tz-aware in IST."""
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC").tz_convert(IST)
        else:
            df.index = df.index.tz_convert(IST)
        return df

    @staticmethod
    def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        """Lowercase columns, keep only OHLCV, drop zero-volume artefacts."""
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df[df["volume"] > 0].dropna()
        return df

    # ------------------------------------------------------------------ #
    #  5-minute OHLCV                                                       #
    # ------------------------------------------------------------------ #

    def _5m_cache_path(self, symbol: str, year: int, month: int) -> Path:
        safe = self._safe_symbol(symbol)
        sym_dir = CACHE_DIR / "5m" / safe
        sym_dir.mkdir(parents=True, exist_ok=True)
        return sym_dir / f"{year}-{month:02d}.parquet"

    def _fetch_5m_from_yf(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Raw yfinance pull for 5m data — no caching, no transformation."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                interval="5m",
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=True,
            )
        if df is None or df.empty:
            raise RuntimeError(f"yfinance returned no 5m data for {symbol} ({start}→{end})")
        df = self._to_ist(df)
        df = self._normalise_ohlcv(df)
        return df

    def get_5m_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """
        Return 5-minute OHLCV for `symbol` over [start, end].
        Data is cached per symbol per calendar month in Parquet.

        Args:
            symbol: yfinance ticker (e.g. "RELIANCE.NS", "^NSEI")
            start:  start date (inclusive)
            end:    end date (inclusive)

        Returns:
            pd.DataFrame with IST DatetimeTzAware index and
            columns [open, high, low, close, volume].
        """
        # Enumerate every (year, month) tuple in the requested range
        months: list[tuple[int, int]] = []
        current = date(start.year, start.month, 1)
        while current <= end:
            months.append((current.year, current.month))
            # Advance to next month
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        frames: list[pd.DataFrame] = []

        for year, month in months:
            cache_path = self._5m_cache_path(symbol, year, month)

            # Determine full month window for fetching
            month_start = date(year, month, 1)
            if month == 12:
                month_end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(year, month + 1, 1) - timedelta(days=1)

            # Use cache if it exists and the month is fully in the past
            is_past_month = month_end < date.today()
            if cache_path.exists() and is_past_month:
                try:
                    df = pd.read_parquet(cache_path)
                    logger.debug("5m cache hit: %s", cache_path.name)
                    frames.append(df)
                    continue
                except Exception as exc:
                    logger.warning("Corrupt 5m cache %s, re-fetching: %s", cache_path, exc)
                    cache_path.unlink(missing_ok=True)

            # Fetch from yfinance
            try:
                df = self._fetch_5m_from_yf(symbol, month_start, month_end)
            except RuntimeError as exc:
                logger.warning("5m fetch failed for %s %d-%02d: %s", symbol, year, month, exc)
                continue

            # Cache past months only (current month data changes intraday)
            if is_past_month and not df.empty:
                try:
                    df.to_parquet(cache_path)
                    logger.debug("5m cached: %s", cache_path.name)
                except Exception as exc:
                    logger.warning("Failed to write 5m Parquet %s: %s", cache_path, exc)

            frames.append(df)

        if not frames:
            raise RuntimeError(f"No 5m data available for {symbol} ({start}→{end})")

        result = pd.concat(frames).sort_index()
        # Slice precisely to the requested window
        start_ts = IST.localize(datetime(start.year, start.month, start.day, 0, 0, 0))
        end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
        result = result.loc[start_ts:end_ts]

        if result.empty:
            raise RuntimeError(f"No 5m data in window for {symbol} ({start}→{end})")

        return result

    # ------------------------------------------------------------------ #
    #  Daily OHLCV                                                          #
    # ------------------------------------------------------------------ #

    def _1d_cache_path(self, symbol: str) -> Path:
        safe = self._safe_symbol(symbol)
        return CACHE_DIR / "1d" / f"{safe}.parquet"

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """
        Return daily OHLCV for `symbol` over [start, end].
        Cached as a single Parquet file per symbol; refreshed if today's
        date is beyond the last cached row.

        Returns:
            pd.DataFrame with IST DatetimeTzAware index and
            columns [open, high, low, close, volume].
        """
        cache_path = self._1d_cache_path(symbol)
        today = date.today()
        cached_df: pd.DataFrame | None = None

        if cache_path.exists():
            try:
                cached_df = pd.read_parquet(cache_path)
                last_cached_date = cached_df.index.max().date()
                if last_cached_date >= today:
                    logger.debug("1d cache hit: %s", symbol)
                    # Slice to requested range
                    start_ts = IST.localize(datetime(start.year, start.month, start.day))
                    end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
                    return cached_df.loc[start_ts:end_ts]
            except Exception as exc:
                logger.warning("Corrupt 1d cache %s, re-fetching: %s", symbol, exc)
                cache_path.unlink(missing_ok=True)
                cached_df = None

        # Fetch from yfinance
        logger.debug("Fetching 1d data for %s (%s→%s)", symbol, start, end)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                interval="1d",
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=True,
            )

        if df is None or df.empty:
            if cached_df is not None and not cached_df.empty:
                logger.warning("1d fetch returned empty for %s; using stale cache", symbol)
                start_ts = IST.localize(datetime(start.year, start.month, start.day))
                end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
                return cached_df.loc[start_ts:end_ts]
            raise RuntimeError(f"No daily data for {symbol} ({start}→{end})")

        df = self._to_ist(df)
        df = self._normalise_ohlcv(df)

        # Merge with existing cache (extend history)
        if cached_df is not None and not cached_df.empty:
            df = pd.concat([cached_df, df]).loc[~pd.concat([cached_df, df]).index.duplicated(keep="last")]
            df = df.sort_index()

        try:
            df.to_parquet(cache_path)
            logger.debug("1d cached: %s", symbol)
        except Exception as exc:
            logger.warning("Failed to write 1d Parquet %s: %s", cache_path, exc)

        start_ts = IST.localize(datetime(start.year, start.month, start.day))
        end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
        return df.loc[start_ts:end_ts]

    # ------------------------------------------------------------------ #
    #  Live quote (15s in-memory cache)                                    #
    # ------------------------------------------------------------------ #

    def get_live_quote(self, symbol: str) -> dict:
        """
        Return a live quote dict for `symbol` via yfinance fast_info.
        Results are cached in memory for 15 seconds.

        Returns dict with keys:
            symbol, ltp, open, high, low, prev_close, volume,
            change, change_pct, timestamp
        """
        cache_key = f"live:{symbol}"
        cached = self._mem.get(cache_key)
        if cached is not None:
            return cached

        logger.debug("Fetching live quote: %s", symbol)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ticker = yf.Ticker(symbol)
                fi = ticker.fast_info

            ltp        = float(fi.get("last_price") or fi.get("lastPrice") or 0.0)
            open_      = float(fi.get("open") or 0.0)
            high       = float(fi.get("day_high") or fi.get("dayHigh") or 0.0)
            low        = float(fi.get("day_low") or fi.get("dayLow") or 0.0)
            prev_close = float(fi.get("previous_close") or fi.get("previousClose") or 0.0)
            volume     = int(fi.get("last_volume") or fi.get("lastVolume") or 0)

        except Exception as exc:
            logger.warning("fast_info failed for %s: %s — falling back to history()", symbol, exc)
            # Fallback: grab the most recent daily bar
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(period="2d", interval="1d", auto_adjust=True)

                if hist is None or hist.empty:
                    raise RuntimeError("history() also empty")

                latest     = hist.iloc[-1]
                prev_row   = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
                ltp        = float(latest["Close"])
                open_      = float(latest["Open"])
                high       = float(latest["High"])
                low        = float(latest["Low"])
                prev_close = float(prev_row["Close"])
                volume     = int(latest.get("Volume") or 0)
            except Exception as exc2:
                raise RuntimeError(f"Cannot fetch live quote for {symbol}: {exc2}") from exc2

        change     = round(ltp - prev_close, 2) if prev_close else 0.0
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        quote = {
            "symbol":     symbol,
            "ltp":        round(ltp, 2),
            "open":       round(open_, 2),
            "high":       round(high, 2),
            "low":        round(low, 2),
            "prev_close": round(prev_close, 2),
            "volume":     volume,
            "change":     change,
            "change_pct": change_pct,
            "timestamp":  datetime.now(IST).isoformat(),
        }
        self._mem.set(cache_key, quote, ttl=_LIVE_CACHE_TTL)
        return quote

    # ------------------------------------------------------------------ #
    #  VIX series                                                           #
    # ------------------------------------------------------------------ #

    def get_vix_series(self, start: date, end: date) -> pd.Series:
        """
        Return the India VIX daily close series over [start, end].
        Cached as /data/cache/vix/vix_daily.parquet.

        Returns:
            pd.Series with IST DatetimeTzAware index and name "vix".
        """
        cache_path = CACHE_DIR / "vix" / "vix_daily.parquet"
        today = date.today()
        cached_series: pd.Series | None = None

        if cache_path.exists():
            try:
                cached_df = pd.read_parquet(cache_path)
                cached_series = cached_df["vix"]
                last_date = cached_series.index.max().date()
                if last_date >= today:
                    logger.debug("VIX cache hit")
                    start_ts = IST.localize(datetime(start.year, start.month, start.day))
                    end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
                    return cached_series.loc[start_ts:end_ts]
            except Exception as exc:
                logger.warning("Corrupt VIX cache, re-fetching: %s", exc)
                cache_path.unlink(missing_ok=True)
                cached_series = None

        logger.debug("Fetching VIX from yfinance (%s→%s)", start, end)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ticker = yf.Ticker(VIX_SYMBOL)
            # Fetch max history for a complete cached series
            df = ticker.history(period="max", interval="1d", auto_adjust=True)

        if df is None or df.empty:
            if cached_series is not None:
                logger.warning("VIX fetch returned empty; using stale cache")
                start_ts = IST.localize(datetime(start.year, start.month, start.day))
                end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
                return cached_series.loc[start_ts:end_ts]
            raise RuntimeError("Cannot fetch VIX data")

        df = self._to_ist(df)
        series = df["Close"].rename("vix")

        # Merge with existing cache
        if cached_series is not None and not cached_series.empty:
            series = pd.concat([cached_series, series])
            series = series[~series.index.duplicated(keep="last")].sort_index()

        try:
            series.to_frame().to_parquet(cache_path)
            logger.debug("VIX cached")
        except Exception as exc:
            logger.warning("Failed to write VIX Parquet: %s", exc)

        start_ts = IST.localize(datetime(start.year, start.month, start.day))
        end_ts   = IST.localize(datetime(end.year, end.month, end.day, 23, 59, 59))
        return series.loc[start_ts:end_ts]

    # ------------------------------------------------------------------ #
    #  Nifty-50 universe                                                    #
    # ------------------------------------------------------------------ #

    def get_nifty50_universe(self) -> list[dict]:
        """
        Load the Nifty-50 universe from the static JSON file.

        Returns:
            List of dicts with keys: symbol, name, sector, lot_size.
        """
        cache_key = "nifty50_universe"
        cached = self._mem.get(cache_key)
        if cached is not None:
            return cached

        if not NIFTY50_JSON.exists():
            raise FileNotFoundError(f"nifty50.json not found at {NIFTY50_JSON}")

        with open(NIFTY50_JSON) as f:
            data = json.load(f)

        universe: list[dict] = data["universe"]
        # Cache for 1 hour — the file rarely changes
        self._mem.set(cache_key, universe, ttl=3600)
        logger.debug("Loaded Nifty-50 universe (%d symbols)", len(universe))
        return universe

    # ------------------------------------------------------------------ #
    #  Batch quotes                                                         #
    # ------------------------------------------------------------------ #

    def get_batch_quotes(self, symbols: list[str]) -> list[dict]:
        """
        Fetch live quotes for multiple symbols in parallel.
        Uses ThreadPoolExecutor with up to _MAX_WORKERS threads.

        Returns:
            List of quote dicts (same shape as get_live_quote).
            Symbols that fail are silently omitted.
        """
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(symbols) or 1)) as pool:
            future_to_sym = {pool.submit(self.get_live_quote, sym): sym for sym in symbols}
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.warning("Batch quote failed for %s: %s", sym, exc)

        # Preserve original order
        order = {sym: i for i, sym in enumerate(symbols)}
        results.sort(key=lambda q: order.get(q["symbol"], 9999))
        return results

    # ------------------------------------------------------------------ #
    #  Cache maintenance                                                    #
    # ------------------------------------------------------------------ #

    def clear_cache(self) -> dict:
        """
        Remove Parquet files in CACHE_DIR that are older than _PARQUET_STALE_DAYS days.

        Returns:
            Dict with 'removed' (count) and 'freed_mb' (approximate disk freed).
        """
        cutoff = datetime.now().timestamp() - _PARQUET_STALE_DAYS * 86400
        removed = 0
        freed_bytes = 0

        for parquet_path in CACHE_DIR.rglob("*.parquet"):
            try:
                mtime = parquet_path.stat().st_mtime
                if mtime < cutoff:
                    freed_bytes += parquet_path.stat().st_size
                    parquet_path.unlink()
                    removed += 1
                    logger.debug("Purged stale cache: %s", parquet_path.name)
            except Exception as exc:
                logger.warning("Failed to remove %s: %s", parquet_path, exc)

        # Also evict expired in-memory entries
        self._mem.clear_expired()

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        logger.info("Cache purge: removed %d files (%.2f MB)", removed, freed_mb)
        return {"removed": removed, "freed_mb": freed_mb}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton — import this everywhere
# ─────────────────────────────────────────────────────────────────────────────

loader = DataLoader()
