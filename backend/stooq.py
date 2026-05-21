"""
stooq.py — Stooq free historical data (no API key, no auth).

Daily OHLCV: https://stooq.com/q/d/l/?s={symbol}&i=d
Symbol format: ^nsei (NIFTY 50), ^nsebank (BANKNIFTY), reliance.ns (NSE stocks)
"""

import io
import logging
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_STOOQ_BASE = "https://stooq.com/q/d/l/"


def _yf_to_stooq(symbol: str) -> Optional[str]:
    """
    Convert a yfinance-style symbol to Stooq format.

    Conversions
    -----------
    ``^NSEI``     → ``^nsei``
    ``^NSEBANK``  → ``^nsebank``
    ``^VIX``      → ``None``  (India VIX not reliably available on Stooq; caller
                               should fall back to yfinance)
    ``RELIANCE.NS`` → ``reliance.ns``
    Everything else → lowercased as-is.

    Returns
    -------
    str or None
        Stooq symbol, or ``None`` when the caller must use an alternative source.
    """
    if not symbol:
        return None

    upper = symbol.upper()

    # Explicit VIX exclusion — India VIX on Stooq uses a different, unreliable ticker.
    if upper in ("^VIX", "INDIAVIX", "^INDIAVIX"):
        return None

    # Just lowercase everything; Stooq accepts this format for both index
    # tickers (^nsei) and equity tickers (reliance.ns).
    return symbol.lower()


def get_daily_ohlcv(
    symbol: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV data from Stooq for the given symbol and date range.

    Parameters
    ----------
    symbol : str
        yfinance-style symbol (will be converted via :func:`_yf_to_stooq`).
    start : str
        Start date in ``YYYY-MM-DD`` format.
    end : str
        End date in ``YYYY-MM-DD`` format.

    Returns
    -------
    pd.DataFrame or None
        DataFrame with a ``DatetimeIndex`` and columns
        ``open``, ``high``, ``low``, ``close``, ``volume``.
        Returns ``None`` when the symbol mapping fails or on any error.
    """
    stooq_sym = _yf_to_stooq(symbol)
    if stooq_sym is None:
        logger.debug("No Stooq mapping for '%s'; caller should use yfinance.", symbol)
        return None

    # Stooq date format: YYYYMMDD
    d1 = start.replace("-", "")
    d2 = end.replace("-", "")

    url = f"{_STOOQ_BASE}?s={stooq_sym}&d1={d1}&d2={d2}&i=d"

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Stooq request failed for '%s': %s", symbol, exc)
        return None

    content = resp.text.strip()

    # Stooq returns a single "No data" line when the symbol is unknown.
    if not content or "no data" in content.lower() or len(content.splitlines()) < 2:
        logger.debug("Stooq returned no data for '%s'.", symbol)
        return None

    try:
        df = pd.read_csv(
            io.StringIO(content),
            parse_dates=["Date"],
            index_col="Date",
        )
    except Exception as exc:
        logger.warning("Stooq CSV parse error for '%s': %s", symbol, exc)
        return None

    if df.empty:
        return None

    # Normalise column names to lowercase.
    df.columns = [c.lower() for c in df.columns]

    # Ensure the expected columns are present.
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        logger.warning(
            "Stooq response for '%s' missing columns: %s",
            symbol,
            required - set(df.columns),
        )
        return None

    df = df[list(required)].sort_index()

    # Cast to numeric, coerce errors to NaN.
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["close"], inplace=True)

    return df if not df.empty else None
