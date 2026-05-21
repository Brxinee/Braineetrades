"""
nse_api.py — NSE India public API wrapper (no auth required).

Endpoints used:
  GET https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050
  GET https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20BANK
  GET https://www.nseindia.com/api/marketStatus
  GET https://www.nseindia.com/api/quote-equity?symbol=RELIANCE (no .NS suffix)
"""

import urllib.parse
import logging

import requests

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """
    Returns a requests.Session pre-loaded with NSE cookies.

    The first call hits the NSE home page to obtain the session cookie
    that all API endpoints require.  The session is cached at module level
    so subsequent calls reuse the same connection pool and cookies.
    """
    global _session

    if _session is not None:
        return _session

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
    )

    # Seed the cookie jar by loading the NSE home page.
    try:
        resp = session.get(_NSE_BASE, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("NSE home-page seed request failed: %s", exc)
        # Carry on — some cookies might still have been set.

    _session = session
    return _session


def _refresh_session() -> requests.Session:
    """Force a fresh session (used after 401/403 responses)."""
    global _session
    _session = None
    return _get_session()


def _api_get(path: str, **params) -> dict | list:
    """
    Perform a GET against the NSE API, returning parsed JSON.
    Refreshes the session and retries once on 401/403.
    """
    session = _get_session()
    url = f"{_NSE_BASE}{path}"
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

    try:
        resp = session.get(url, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"NSE request failed: {exc}") from exc

    if resp.status_code in (401, 403):
        logger.info("NSE returned %s — refreshing session", resp.status_code)
        session = _refresh_session()
        try:
            resp = session.get(url, timeout=10)
        except Exception as exc:
            raise RuntimeError(f"NSE request failed after session refresh: {exc}") from exc

    if not resp.ok:
        raise RuntimeError(
            f"NSE API error: {resp.status_code} {resp.reason} for {url}"
        )

    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────


def get_index_constituents(index_name: str) -> list[dict]:
    """
    Fetch live quotes for every constituent of *index_name*.

    Parameters
    ----------
    index_name : str
        Human-readable NSE index name, e.g. ``"NIFTY 50"`` or ``"NIFTY BANK"``.

    Returns
    -------
    list[dict]
        Each dict contains:
        ``symbol``, ``name``, ``sector``, ``ltp``, ``open``, ``high``,
        ``low``, ``prev_close``, ``change``, ``change_pct``, ``volume``.

    Raises
    ------
    RuntimeError
        If the NSE API request fails.
    """
    encoded = urllib.parse.quote(index_name)
    data = _api_get(f"/api/equity-stockIndices?index={encoded}")

    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(
            f"Unexpected NSE response shape for index '{index_name}': {str(data)[:200]}"
        )

    result: list[dict] = []
    for item in data["data"]:
        symbol = item.get("symbol", "")
        # Skip the index row itself (it appears as the first entry)
        if not symbol or symbol in ("NIFTY 50", "NIFTY BANK", index_name):
            continue

        try:
            ltp        = float(item.get("lastPrice", 0) or 0)
            prev_close = float(item.get("previousClose", 0) or 0)
            change     = float(item.get("change", 0) or 0)
            change_pct = float(item.get("pChange", 0) or 0)
        except (TypeError, ValueError):
            ltp = prev_close = change = change_pct = 0.0

        result.append(
            {
                "symbol":     symbol,
                "name":       item.get("companyName", symbol),
                "sector":     item.get("industry", ""),
                "ltp":        round(ltp, 2),
                "open":       round(float(item.get("open", 0) or 0), 2),
                "high":       round(float(item.get("dayHigh", 0) or 0), 2),
                "low":        round(float(item.get("dayLow", 0) or 0), 2),
                "prev_close": round(prev_close, 2),
                "change":     round(change, 2),
                "change_pct": round(change_pct, 2),
                "volume":     int(item.get("totalTradedVolume", 0) or 0),
            }
        )

    return result


def get_nifty50_quotes() -> list[dict]:
    """
    Convenience wrapper: returns live quotes for all Nifty 50 constituents.

    Returns
    -------
    list[dict]
        See :func:`get_index_constituents` for field definitions.
    """
    return get_index_constituents("NIFTY 50")


def get_market_status() -> dict:
    """
    Returns the current NSE market status.

    Returns
    -------
    dict
        ``{"market_open": bool, "status": str}``
    """
    try:
        data = _api_get("/api/marketStatus")
    except RuntimeError as exc:
        logger.warning("Could not fetch market status: %s", exc)
        return {"market_open": False, "status": "unknown"}

    # NSE response: {"marketState": [{"market": "Capital Market", "marketStatus": "Open", ...}]}
    status_str = "unknown"
    if isinstance(data, dict):
        states = data.get("marketState", [])
        for state in states:
            mkt = state.get("market", "")
            if "capital" in mkt.lower() or "equity" in mkt.lower():
                status_str = state.get("marketStatus", "unknown")
                break
        if status_str == "unknown" and states:
            status_str = states[0].get("marketStatus", "unknown")

    return {
        "market_open": status_str.lower() in ("open",),
        "status": status_str,
    }
