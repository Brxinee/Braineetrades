"""Historical data loader for NIFTY 50 spot and India VIX.

Priority for 5-min intraday OHLCV:
    1. Upstox Historical API  (set UPSTOX_ACCESS_TOKEN env var)
    2. yfinance               (last 58 days only — warns for older ranges)

India VIX (daily):
    yfinance ^INDIAVIX — works for any historical range.

All DataFrames are returned with a timezone-aware DatetimeIndex in IST.
Downloaded data is cached as Parquet in backtest/nifty_options/data/.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_CACHE = Path(__file__).parent / "data"
_CACHE.mkdir(exist_ok=True)

_NIFTY_YF = "^NSEI"
_VIX_YF = "^INDIAVIX"

# Upstox v2 historical-candle endpoint
# GET /v2/historical-candle/{instrumentKey}/{unit}/{interval}/{toDate}/{fromDate}
_UPSTOX_BASE = "https://api.upstox.com/v2/historical-candle"
_UPSTOX_NIFTY_KEY = "NSE_INDEX|Nifty 50"

# yfinance only serves 5-min data for the last 60 days; keep a safe margin
_YF_5M_MAX_DAYS = 58


# ── internal helpers ──────────────────────────────────────────────────────────

def _cache_path(name: str, year: int, month: int) -> Path:
    return _CACHE / f"{name}_{year:04d}_{month:02d}.parquet"


def _to_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the DatetimeIndex is IST-aware."""
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(IST)
    else:
        df.index = df.index.tz_convert(IST)
    return df


def _months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """Return list of (year, month) tuples spanning [start, end]."""
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns and keep only ohlcv."""
    df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


# ── Upstox loader ─────────────────────────────────────────────────────────────

def _upstox_chunk(start: date, end: date, token: str) -> pd.DataFrame:
    """Single Upstox API call for up to 1 year of 5-min data."""
    import requests  # soft dependency — already in requirements

    key = quote(_UPSTOX_NIFTY_KEY, safe="")
    # interval unit "minutes" with count 5
    url = f"{_UPSTOX_BASE}/{key}/minutes/5/{end.isoformat()}/{start.isoformat()}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 401:
        raise PermissionError("Upstox: invalid or expired access token")
    resp.raise_for_status()

    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(
        candles,
        columns=["datetime", "open", "high", "low", "close", "volume", "oi"],
    )
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()[["open", "high", "low", "close", "volume"]]
    return _to_ist(df)


def _load_upstox(start: date, end: date, token: str) -> pd.DataFrame:
    """Fetch 5-min data from Upstox with per-month Parquet caching."""
    frames: list[pd.DataFrame] = []
    months = _months_in_range(start, end)

    # Determine which months need downloading
    missing: list[tuple[int, int]] = [
        (y, m) for y, m in months if not _cache_path("spot5m", y, m).exists()
    ]

    if missing:
        # Download the smallest range covering all missing months at once
        dl_start = date(missing[0][0], missing[0][1], 1)
        last_y, last_m = missing[-1]
        dl_end_m = last_m % 12 + 1
        dl_end_y = last_y + (1 if last_m == 12 else 0)
        dl_end = date(dl_end_y, dl_end_m, 1) - timedelta(days=1)
        dl_end = min(dl_end, end)

        log.info("Upstox: fetching %s → %s", dl_start, dl_end)
        raw = _upstox_chunk(dl_start, dl_end, token)

        for y, m in missing:
            if raw.empty:
                break
            mask = (raw.index.year == y) & (raw.index.month == m)
            chunk = raw.loc[mask]
            if not chunk.empty:
                chunk.to_parquet(_cache_path("spot5m", y, m))

    for y, m in months:
        p = _cache_path("spot5m", y, m)
        if p.exists():
            frames.append(pd.read_parquet(p))

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames).sort_index()
    start_ts = pd.Timestamp(start, tz=IST)
    end_ts = pd.Timestamp(end, tz=IST) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return out.loc[start_ts:end_ts]


# ── yfinance loader ───────────────────────────────────────────────────────────

def _load_yfinance_5m(start: date, end: date) -> pd.DataFrame:
    """Fetch 5-min OHLCV via yfinance (works only for the last 58 days)."""
    cutoff = date.today() - timedelta(days=_YF_5M_MAX_DAYS)
    if start < cutoff:
        log.warning(
            "yfinance 5-min data unavailable before %s. "
            "Set UPSTOX_ACCESS_TOKEN for full historical coverage. "
            "Clamping start to %s.",
            cutoff,
            cutoff,
        )
        start = cutoff
    if start > end:
        return pd.DataFrame()

    raw = yf.download(
        _NIFTY_YF,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="5m",
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    raw.index = pd.to_datetime(raw.index)
    return _to_ist(_normalise_ohlcv(raw))


# ── public API ────────────────────────────────────────────────────────────────

def load_5m_ohlcv(start: date, end: date) -> pd.DataFrame:
    """Load 5-min NIFTY 50 OHLCV for [start, end].

    Returns a DataFrame with columns [open, high, low, close, volume] and a
    timezone-aware DatetimeIndex in IST.  Market hours: 09:15–15:30 IST.

    Set the UPSTOX_ACCESS_TOKEN environment variable for data beyond 58 days.
    """
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if token:
        try:
            df = _load_upstox(start, end, token)
            if not df.empty:
                return df
            log.warning("Upstox returned empty response, falling back to yfinance")
        except PermissionError:
            log.error("Upstox token invalid — falling back to yfinance")
        except Exception as exc:
            log.warning("Upstox error (%s) — falling back to yfinance", exc)

    return _load_yfinance_5m(start, end)


def load_daily_vix(start: date, end: date) -> pd.Series:
    """Load India VIX daily close for [start, end].

    Returns a float Series indexed by date-only Timestamps.
    Fetches from yfinance (^INDIAVIX) and caches the full history locally.
    """
    cache = _CACHE / "vix_daily.parquet"

    if cache.exists():
        vix = pd.read_parquet(cache).squeeze()
        vix.index = pd.to_datetime(vix.index)
        covered = (
            not vix.empty
            and vix.index[0].date() <= start
            and vix.index[-1].date() >= end
        )
        if covered:
            return vix.loc[pd.Timestamp(start): pd.Timestamp(end)]

    log.info("Fetching India VIX from yfinance")
    # Download full history — daily VIX has no lookback restriction
    raw = yf.download(
        _VIX_YF,
        start="2019-01-01",
        end=(date.today() + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        log.error("Failed to fetch India VIX")
        return pd.Series(dtype=float, name="vix")

    vix = raw["Close"].squeeze().dropna()
    vix.index = pd.to_datetime(vix.index).normalize()
    vix.name = "vix"
    vix.to_frame().to_parquet(cache)

    return vix.loc[pd.Timestamp(start): pd.Timestamp(end)]


def get_spot_at(df_5m: pd.DataFrame, ts: pd.Timestamp) -> float:
    """Return the close price of the 5-min bar containing `ts`.

    Falls back to the previous bar's close if `ts` falls between bars.
    """
    idx = df_5m.index.searchsorted(ts, side="right") - 1
    if idx < 0:
        return float("nan")
    return float(df_5m.iloc[idx]["close"])
