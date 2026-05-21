"""
market_internals.py — Nifty-50 market breadth and internal-strength analysis.

Computes advance/decline statistics, VWAP-relative breadth, sector breakdowns,
top/bottom movers, volume leaders, and a composite breadth score from the live
quote snapshots that the frontend passes in (or that the data_loader fetches).

All public functions are pure/stateless — instantiate MarketInternals once and
call its methods, or use the module-level convenience functions directly.

Quote dict schema (each element of nifty50_quotes):
    {
        "symbol":     str,          # e.g. "RELIANCE.NS"
        "name":       str,
        "sector":     str,
        "ltp":        float,        # last traded price
        "open":       float,
        "prev_close": float,
        "vwap_est":   float | None, # caller may supply; falls back to (O+H+L+C)/4
        "change_pct": float,        # (ltp - prev_close) / prev_close * 100
        "volume":     int | float,
        "avg_volume": int | float,  # rolling 20-day average volume
        # Optional extras used for VWAP fallback:
        "high":  float | None,
        "low":   float | None,
    }
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector strength labels
# ---------------------------------------------------------------------------

_STRENGTH_THRESHOLDS: list[tuple[float, str]] = [
    (1.5, "STRONG_UP"),
    (0.5, "UP"),
    (-0.5, "FLAT"),
    (-1.5, "DOWN"),
    (-float("inf"), "STRONG_DOWN"),
]

# Minimum stocks in a sector required to compute a meaningful sector figure
_MIN_SECTOR_STOCKS = 2

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce value to float; return default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _vwap_estimate(quote: dict) -> float:
    """
    Return the best VWAP estimate for a quote.

    Priority:
      1. Caller-supplied ``vwap_est`` (already computed server-side).
      2. (open + high + low + ltp) / 4 — OHLC/4 proxy.
      3. ltp alone as last resort.
    """
    vwap_est = _safe_float(quote.get("vwap_est"), -1.0)
    if vwap_est > 0:
        return vwap_est

    o = _safe_float(quote.get("open"))
    h = _safe_float(quote.get("high"))
    l = _safe_float(quote.get("low"))
    c = _safe_float(quote.get("ltp"))

    # If high/low are missing, use open and ltp as H/L proxies
    if h == 0.0:
        h = max(o, c)
    if l == 0.0:
        l = min(o, c) if o > 0 else c

    if o > 0 and h > 0 and l > 0 and c > 0:
        return (o + h + l + c) / 4.0

    return c  # last resort


def _strength_label(change_pct: float) -> str:
    """Map a sector change_pct to a human-readable strength label."""
    for threshold, label in _STRENGTH_THRESHOLDS:
        if change_pct >= threshold:
            return label
    return "STRONG_DOWN"


def _validate_quotes(quotes: list[dict]) -> list[dict]:
    """
    Filter out any quotes that are missing essential numeric fields.
    Logs a warning for each rejected quote.
    """
    valid: list[dict] = []
    for q in quotes:
        symbol = q.get("symbol", "<unknown>")
        ltp = _safe_float(q.get("ltp"))
        prev_close = _safe_float(q.get("prev_close"))
        if ltp <= 0 or prev_close <= 0:
            logger.debug(
                "market_internals: skipping %s — ltp=%.2f prev_close=%.2f",
                symbol,
                ltp,
                prev_close,
            )
            continue
        valid.append(q)
    return valid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class MarketInternals:
    """
    Stateless market-breadth calculator for the Nifty-50 universe.

    Instantiate once at application startup and call instance methods as
    the frontend polls for fresh data.
    """

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def get_internals(self, nifty50_quotes: list[dict]) -> dict:
        """
        Compute full market-internals snapshot from a list of live quote dicts.

        Parameters
        ----------
        nifty50_quotes:
            List of quote dicts (see module docstring for schema).

        Returns
        -------
        dict
            {
                "advance": int,
                "decline": int,
                "neutral": int,
                "ad_ratio": float,
                "pct_above_vwap": float,
                "pct_above_prev_close": float,
                "pct_positive": float,
                "breadth_score": float,      # 0–10
                "sectors": [...],
                "top_movers": [...],         # top-5 gainers
                "weak_movers": [...],        # bottom-5 losers
                "volume_leaders": [...],     # top-5 by volume/avg_volume ratio
                "breadth_trend": str,        # "EXPANDING" / "CONTRACTING" / "NEUTRAL"
            }
        """
        if not nifty50_quotes:
            logger.warning("get_internals: received empty quotes list")
            return self._empty_internals()

        quotes = _validate_quotes(nifty50_quotes)
        if not quotes:
            logger.warning("get_internals: no valid quotes after validation")
            return self._empty_internals()

        n = len(quotes)

        # ── Advance / Decline / Neutral ──────────────────────────────────
        advance = 0
        decline = 0
        neutral = 0
        above_vwap = 0
        above_prev_close = 0

        for q in quotes:
            chg = _safe_float(q.get("change_pct"))
            if chg > 0.05:
                advance += 1
            elif chg < -0.05:
                decline += 1
            else:
                neutral += 1

            ltp = _safe_float(q.get("ltp"))
            if ltp > _vwap_estimate(q):
                above_vwap += 1
            if ltp > _safe_float(q.get("prev_close")):
                above_prev_close += 1

        ad_ratio = round(advance / decline, 4) if decline > 0 else float(advance)
        pct_above_vwap = round(above_vwap / n * 100, 2)
        pct_above_prev_close = round(above_prev_close / n * 100, 2)
        pct_positive = round(advance / n * 100, 2)

        breadth_score = self.compute_breadth_score(advance, decline, pct_above_vwap)

        # ── Sectors ──────────────────────────────────────────────────────
        sectors = self.get_sector_breakdown(nifty50_quotes, nifty50_quotes)

        # ── Movers ───────────────────────────────────────────────────────
        sorted_by_change = sorted(
            quotes,
            key=lambda q: _safe_float(q.get("change_pct")),
            reverse=True,
        )
        top_movers = [
            {
                "symbol": q["symbol"],
                "name": q.get("name", q["symbol"]),
                "change_pct": round(_safe_float(q.get("change_pct")), 4),
                "sector": q.get("sector", "Unknown"),
            }
            for q in sorted_by_change[:5]
        ]
        weak_movers = [
            {
                "symbol": q["symbol"],
                "name": q.get("name", q["symbol"]),
                "change_pct": round(_safe_float(q.get("change_pct")), 4),
                "sector": q.get("sector", "Unknown"),
            }
            for q in sorted_by_change[-5:]
        ]

        # ── Volume leaders ───────────────────────────────────────────────
        volume_leaders = self._volume_leaders(quotes)

        # ── Breadth trend ────────────────────────────────────────────────
        breadth_trend = self._breadth_trend(advance, decline, pct_above_vwap)

        return {
            "advance": advance,
            "decline": decline,
            "neutral": neutral,
            "ad_ratio": ad_ratio,
            "pct_above_vwap": pct_above_vwap,
            "pct_above_prev_close": pct_above_prev_close,
            "pct_positive": pct_positive,
            "breadth_score": breadth_score,
            "sectors": sectors,
            "top_movers": top_movers,
            "weak_movers": weak_movers,
            "volume_leaders": volume_leaders,
            "breadth_trend": breadth_trend,
        }

    # ------------------------------------------------------------------
    # Sector breakdown
    # ------------------------------------------------------------------

    def get_sector_breakdown(
        self,
        nifty50_universe: list[dict],
        quotes: list[dict],
    ) -> list[dict]:
        """
        Group quotes by sector and compute aggregate statistics per sector.

        Parameters
        ----------
        nifty50_universe:
            Universe list (may include static metadata such as sector).
            Falls back to the ``sector`` field in each quote if universe is
            the same as quotes.
        quotes:
            Live quote dicts with ``change_pct`` populated.

        Returns
        -------
        list[dict]
            Sorted by ``change_pct`` descending (best sector first):
            [{"name": str, "change_pct": float, "strength": str,
              "stocks": int, "advance": int, "decline": int}, ...]
        """
        # Build a symbol→sector map from the universe (or quotes themselves)
        sym_to_sector: dict[str, str] = {}
        for item in nifty50_universe:
            sym = item.get("symbol", "")
            sector = item.get("sector", "Unknown")
            if sym:
                sym_to_sector[sym] = sector

        # Aggregate per sector
        sector_data: dict[str, dict] = {}
        for q in quotes:
            symbol = q.get("symbol", "")
            sector = sym_to_sector.get(symbol) or q.get("sector", "Unknown")
            chg = _safe_float(q.get("change_pct"))
            ltp = _safe_float(q.get("ltp"))
            prev_close = _safe_float(q.get("prev_close"))

            if sector not in sector_data:
                sector_data[sector] = {
                    "name": sector,
                    "changes": [],
                    "advance": 0,
                    "decline": 0,
                    "neutral": 0,
                }

            sector_data[sector]["changes"].append(chg)
            if chg > 0.05:
                sector_data[sector]["advance"] += 1
            elif chg < -0.05:
                sector_data[sector]["decline"] += 1
            else:
                sector_data[sector]["neutral"] += 1

        # Build result list
        result: list[dict] = []
        for sector, data in sector_data.items():
            changes = data["changes"]
            if not changes:
                continue
            avg_change = sum(changes) / len(changes)
            result.append(
                {
                    "name": sector,
                    "change_pct": round(avg_change, 4),
                    "strength": _strength_label(avg_change),
                    "stocks": len(changes),
                    "advance": data["advance"],
                    "decline": data["decline"],
                    "neutral": data["neutral"],
                }
            )

        return sorted(result, key=lambda s: s["change_pct"], reverse=True)

    # ------------------------------------------------------------------
    # Breadth score
    # ------------------------------------------------------------------

    @staticmethod
    def compute_breadth_score(
        advance: int,
        decline: int,
        pct_above_vwap: float,
    ) -> float:
        """
        Compute a composite breadth score in [0, 10].

        Formula:
          ad_component   = (advance - decline) / (advance + decline) * 5
                           → ranges from −5 to +5; shifted to 0–5 range.
          vwap_component = pct_above_vwap / 100 * 5
                           → 0–5 based on % of stocks above VWAP.

        Combined:
          raw_score = ((advance - decline) / total) * 5 + (pct_above_vwap / 100) * 5
          score     = clamp(raw_score + 5, 0, 10)   # shift −5..+5 → 0..10
        """
        total = advance + decline
        if total == 0:
            ad_component = 0.0
        else:
            ad_component = ((advance - decline) / total) * 5.0

        vwap_component = (pct_above_vwap / 100.0) * 5.0

        raw = ad_component + vwap_component
        # ad_component is in [-5, +5], vwap_component in [0, 5]
        # To normalise to [0, 10]: add 5 (to shift ad fully positive), then
        # clamp. vwap_component already in [0,5].
        score = ad_component + 5.0 + vwap_component  # maps to [0, 15] theoretically
        # Re-scale so that 0 advance = score around 2.5 and full advance = 10
        # Use the simpler two-component average shifted to 0–10:
        score = ((ad_component + 5.0) / 10.0) * 5.0 + vwap_component
        score = max(0.0, min(10.0, round(score, 2)))
        return score

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _volume_leaders(quotes: list[dict], top_n: int = 5) -> list[dict]:
        """
        Return top-N quotes ranked by volume-to-average-volume ratio.
        Quotes without a valid avg_volume are excluded.
        """
        leaders: list[dict] = []
        for q in quotes:
            vol = _safe_float(q.get("volume"))
            avg_vol = _safe_float(q.get("avg_volume"))
            if avg_vol <= 0 or vol <= 0:
                continue
            ratio = vol / avg_vol
            leaders.append(
                {
                    "symbol": q.get("symbol", ""),
                    "volume_ratio": round(ratio, 3),
                }
            )
        leaders.sort(key=lambda x: x["volume_ratio"], reverse=True)
        return leaders[:top_n]

    @staticmethod
    def _breadth_trend(
        advance: int,
        decline: int,
        pct_above_vwap: float,
    ) -> str:
        """
        Classify breadth as EXPANDING, CONTRACTING, or NEUTRAL.

        EXPANDING  : advance > decline AND pct_above_vwap >= 60
        CONTRACTING: decline > advance AND pct_above_vwap <= 40
        NEUTRAL    : otherwise
        """
        if advance > decline and pct_above_vwap >= 60.0:
            return "EXPANDING"
        if decline > advance and pct_above_vwap <= 40.0:
            return "CONTRACTING"
        return "NEUTRAL"

    @staticmethod
    def _empty_internals() -> dict:
        return {
            "advance": 0,
            "decline": 0,
            "neutral": 0,
            "ad_ratio": 0.0,
            "pct_above_vwap": 0.0,
            "pct_above_prev_close": 0.0,
            "pct_positive": 0.0,
            "breadth_score": 0.0,
            "sectors": [],
            "top_movers": [],
            "weak_movers": [],
            "volume_leaders": [],
            "breadth_trend": "NEUTRAL",
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

_internals = MarketInternals()


def get_internals(nifty50_quotes: list[dict]) -> dict:
    """Module-level wrapper — see MarketInternals.get_internals."""
    return _internals.get_internals(nifty50_quotes)


def get_sector_breakdown(
    nifty50_universe: list[dict],
    quotes: list[dict],
) -> list[dict]:
    """Module-level wrapper — see MarketInternals.get_sector_breakdown."""
    return _internals.get_sector_breakdown(nifty50_universe, quotes)


def compute_breadth_score(
    advance: int,
    decline: int,
    pct_above_vwap: float,
) -> float:
    """Module-level wrapper — see MarketInternals.compute_breadth_score."""
    return MarketInternals.compute_breadth_score(advance, decline, pct_above_vwap)
