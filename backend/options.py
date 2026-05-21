"""
options.py — Black-Scholes options engine for NIFTY intraday trading platform.

Implements:
  - Pure-Python Black-Scholes pricing (no scipy; uses math.erfc approximation)
  - Synthetic option chain generation (ATM ± 15 strikes at 50-pt increments)
  - Max Pain calculation
  - Put-Call Ratio (PCR)
  - IV Percentile
  - Option suggestion based on confidence + VIX regime
  - CE/PE OI wall detection

NSE Lot Sizes (hardcoded per SEBI circular):
  NIFTY       = 75
  BANKNIFTY   = 35
  FINNIFTY    = 65
  MIDCPNIFTY  = 75

Risk-free rate: India repo rate 6.5% (decimal 0.065).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_R = 0.065          # India risk-free rate
_STRIKE_STEP = 50           # NIFTY option strike granularity
_N_STRIKES = 15             # strikes above and below ATM (total = 2*15+1 = 31)

LOT_SIZES: dict[str, int] = {
    "NIFTY": 75,
    "^NSEI": 75,
    "BANKNIFTY": 35,
    "^NSEBANK": 35,
    "FINNIFTY": 65,
    "MIDCPNIFTY": 75,
}
_DEFAULT_LOT_SIZE = 75

# OI smile parameters (mimics typical NSE OI distribution)
_OI_BASE = 1_000_000        # baseline OI units
_OI_ROUND_BOOST = 1.5       # round number strikes (multiples of 500) get extra OI


# ---------------------------------------------------------------------------
# Pure-Python Black-Scholes (no scipy)
# ---------------------------------------------------------------------------

class OptionsEngine:
    """
    Options pricing and analytics engine.

    All Black-Scholes computations are self-contained using the math module.
    No scipy dependency required.
    """

    # ------------------------------------------------------------------
    # Core math
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """
        Standard normal CDF using math.erfc approximation.

        N(x) = erfc(-x / sqrt(2)) / 2

        erfc is the complementary error function available in Python's
        math module.  Accuracy matches scipy.stats.norm.cdf to ~7 decimal
        places across [-5, 5].
        """
        return math.erfc(-x / math.sqrt(2.0)) / 2.0

    @staticmethod
    def _norm_pdf(x: float) -> float:
        """Standard normal PDF."""
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def _d1_d2(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
    ) -> tuple[float, float]:
        """Compute d1 and d2 for Black-Scholes."""
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        return d1, d2

    def _bs_price(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: str,
    ) -> float:
        """
        European option price via Black-Scholes.

        Parameters
        ----------
        S           : spot price
        K           : strike price
        T           : time to expiry in years (e.g. 7/365 for 7 calendar days)
        r           : risk-free rate (decimal)
        sigma       : annualised implied volatility (decimal, e.g. 0.18)
        option_type : "CE" (call) or "PE" (put)

        Returns
        -------
        Option premium (same price units as S and K).
        """
        is_call = option_type.upper() in ("CE", "CALL", "C")

        if T <= 0.0:
            intrinsic = (S - K) if is_call else (K - S)
            return float(max(intrinsic, 0.0))
        if sigma <= 0.0 or S <= 0.0 or K <= 0.0:
            return 0.0

        d1, d2 = self._d1_d2(S, K, T, r, sigma)
        disc = math.exp(-r * T)

        if is_call:
            return float(S * self._norm_cdf(d1) - K * disc * self._norm_cdf(d2))
        else:
            return float(K * disc * self._norm_cdf(-d2) - S * self._norm_cdf(-d1))

    def _bs_delta(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        option_type: str,
    ) -> float:
        """
        Option delta (dPrice/dS).

        Call delta ∈ (0, 1); Put delta ∈ (-1, 0).
        """
        is_call = option_type.upper() in ("CE", "CALL", "C")

        if T <= 0.0 or sigma <= 0.0:
            if is_call:
                return 1.0 if S > K else 0.0
            else:
                return -1.0 if S < K else 0.0

        d1, _ = self._d1_d2(S, K, T, r, sigma)
        cdf_d1 = self._norm_cdf(d1)
        return float(cdf_d1 if is_call else cdf_d1 - 1.0)

    def _bs_vega(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
    ) -> float:
        """Vega: dPrice/d(sigma).  Same for calls and puts."""
        if T <= 0.0 or sigma <= 0.0:
            return 0.0
        d1, _ = self._d1_d2(S, K, T, r, sigma)
        return float(S * self._norm_pdf(d1) * math.sqrt(T))

    # ------------------------------------------------------------------
    # Strike grid helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _atm_strike(spot: float, step: int = _STRIKE_STEP) -> int:
        """Round spot to the nearest strike multiple."""
        return int(round(spot / step) * step)

    @staticmethod
    def _lot_size(symbol: str) -> int:
        """Return NSE lot size for the given symbol."""
        upper = symbol.upper()
        for key, size in LOT_SIZES.items():
            if key in upper:
                return size
        return _DEFAULT_LOT_SIZE

    # ------------------------------------------------------------------
    # IV smile
    # ------------------------------------------------------------------

    @staticmethod
    def _iv_smile(atm_iv: float, moneyness_steps: int) -> float:
        """
        Apply a simple symmetric vol smile adjustment.

        Strikes farther from ATM carry slightly higher IV (wings).
        moneyness_steps : integer offset from ATM in strike-step units.
        Adjustment: +0.003 * steps^1.5 (roughly matches NSE term structure).
        """
        if moneyness_steps == 0:
            return atm_iv
        adjustment = 0.003 * abs(moneyness_steps) ** 1.5
        return min(atm_iv + adjustment, atm_iv * 1.6)

    # ------------------------------------------------------------------
    # Synthetic OI distribution
    # ------------------------------------------------------------------

    @staticmethod
    def _oi_distribution(
        strike: int,
        atm: int,
        option_type: str,
        step: int = _STRIKE_STEP,
    ) -> int:
        """
        Generate realistic synthetic OI using a bell curve centred near ATM.

        CEs peak at ATM + 1–2 strikes (writers sell slightly OTM calls).
        PEs peak at ATM - 1–2 strikes (writers sell slightly OTM puts).
        Round-number strikes (500-point multiples) get a 1.5× OI boost.
        """
        # Centre the bell curve 1 strike OTM for each side
        if option_type.upper() == "CE":
            centre = atm + step
        else:
            centre = atm - step

        distance_steps = abs(strike - centre) / step
        # Gaussian kernel with sigma = 4 strikes
        oi_raw = _OI_BASE * math.exp(-0.5 * (distance_steps / 4.0) ** 2)

        # Round-number boost
        if strike % 500 == 0:
            oi_raw *= _OI_ROUND_BOOST

        # Add some noise-like deterministic variation
        noise_factor = 1.0 + 0.1 * math.sin(strike / 100.0)
        return int(oi_raw * noise_factor)

    # ------------------------------------------------------------------
    # Public: option chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol: str,
        expiry: date,
        spot: float,
        vix: float,
        r: float = _DEFAULT_R,
        n_strikes: int = _N_STRIKES,
    ) -> dict:
        """
        Generate a synthetic option chain using Black-Scholes.

        Parameters
        ----------
        symbol    : underlying symbol e.g. "NIFTY"
        expiry    : option expiry date
        spot      : current underlying spot price
        vix       : India VIX (percent, e.g. 14.5)
        r         : risk-free rate (decimal)
        n_strikes : number of strikes above and below ATM

        Returns
        -------
        dict with keys:
            symbol, spot, expiry, dte, atm
            strikes : list[dict] — one dict per strike with:
                strike, ce_price, ce_delta, ce_iv, ce_oi,
                pe_price, pe_delta, pe_iv, pe_oi,
                total_oi
            generated_at : str
        """
        today = date.today()
        dte = (expiry - today).days
        T = max(dte, 0.25) / 365.0  # floor at 6 hours to avoid instability
        atm_iv = vix / 100.0        # VIX percent → decimal

        atm = self._atm_strike(spot)
        strikes = [
            atm + _STRIKE_STEP * i
            for i in range(-n_strikes, n_strikes + 1)
        ]

        chain_strikes: list[dict] = []

        for K in strikes:
            steps_from_atm = (K - atm) // _STRIKE_STEP
            ce_iv = self._iv_smile(atm_iv, steps_from_atm)
            pe_iv = self._iv_smile(atm_iv, steps_from_atm)

            ce_price = self._bs_price(spot, K, T, r, ce_iv, "CE")
            pe_price = self._bs_price(spot, K, T, r, pe_iv, "PE")
            ce_delta = self._bs_delta(spot, K, T, r, ce_iv, "CE")
            pe_delta = self._bs_delta(spot, K, T, r, pe_iv, "PE")

            ce_oi = self._oi_distribution(K, atm, "CE")
            pe_oi = self._oi_distribution(K, atm, "PE")

            chain_strikes.append({
                "strike": K,
                "ce_price": round(ce_price, 2),
                "ce_delta": round(ce_delta, 4),
                "ce_iv": round(ce_iv * 100.0, 2),    # back to percent for display
                "ce_oi": ce_oi,
                "pe_price": round(pe_price, 2),
                "pe_delta": round(pe_delta, 4),
                "pe_iv": round(pe_iv * 100.0, 2),
                "pe_oi": pe_oi,
                "total_oi": ce_oi + pe_oi,
            })

        return {
            "symbol": symbol,
            "spot": round(spot, 2),
            "expiry": expiry.isoformat(),
            "dte": dte,
            "atm": atm,
            "strikes": chain_strikes,
            "generated_at": datetime.now(IST).isoformat(),
        }

    # ------------------------------------------------------------------
    # Public: max pain
    # ------------------------------------------------------------------

    def get_max_pain(self, chain: dict) -> int:
        """
        Calculate max pain strike.

        For each potential expiry price (= each strike), sum the total
        intrinsic value loss for CE and PE writers if expiry occurs there.

        Max pain = strike where writers' total pain (= buyers' total intrinsic
        gain) is MINIMISED.

        Parameters
        ----------
        chain : dict from get_option_chain()

        Returns
        -------
        max_pain_strike : int
        """
        strikes_data = chain["strikes"]
        if not strikes_data:
            return chain.get("atm", 0)

        strikes = [row["strike"] for row in strikes_data]
        all_ce_oi = {row["strike"]: row["ce_oi"] for row in strikes_data}
        all_pe_oi = {row["strike"]: row["pe_oi"] for row in strikes_data}

        min_pain = float("inf")
        max_pain_strike = strikes[len(strikes) // 2]

        for expiry_price in strikes:
            total_pain = 0

            # Pain for CE writers: all calls with strike < expiry_price are ITM
            for K, ce_oi in all_ce_oi.items():
                intrinsic = max(expiry_price - K, 0)
                total_pain += intrinsic * ce_oi

            # Pain for PE writers: all puts with strike > expiry_price are ITM
            for K, pe_oi in all_pe_oi.items():
                intrinsic = max(K - expiry_price, 0)
                total_pain += intrinsic * pe_oi

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = expiry_price

        return max_pain_strike

    # ------------------------------------------------------------------
    # Public: PCR
    # ------------------------------------------------------------------

    def get_pcr(self, chain: dict) -> float:
        """
        Put-Call Ratio by open interest.

        PCR = total_PE_OI / total_CE_OI

        Values < 0.7 indicate bullish sentiment; > 1.2 bearish.

        Parameters
        ----------
        chain : dict from get_option_chain()

        Returns
        -------
        pcr : float
        """
        strikes_data = chain["strikes"]
        total_ce_oi = sum(row["ce_oi"] for row in strikes_data)
        total_pe_oi = sum(row["pe_oi"] for row in strikes_data)
        if total_ce_oi == 0:
            return 0.0
        return round(total_pe_oi / total_ce_oi, 4)

    # ------------------------------------------------------------------
    # Public: IV percentile
    # ------------------------------------------------------------------

    def get_iv_percentile(
        self,
        vix: float,
        historical_vix: pd.Series,
    ) -> float:
        """
        Compute the IV percentile rank of the current VIX vs its history.

        IV percentile = fraction of historical observations below current VIX.

        Parameters
        ----------
        vix            : current India VIX value
        historical_vix : pd.Series of daily VIX closes (last 252 trading days
                         recommended)

        Returns
        -------
        percentile : float in [0, 100]
        """
        if historical_vix is None or historical_vix.empty:
            logger.warning("get_iv_percentile: empty historical VIX — returning 50.0")
            return 50.0

        clean = historical_vix.dropna().values
        if len(clean) == 0:
            return 50.0

        below = float((clean < vix).sum())
        percentile = (below / len(clean)) * 100.0
        return round(percentile, 2)

    # ------------------------------------------------------------------
    # Public: option suggestion
    # ------------------------------------------------------------------

    def suggest_option(
        self,
        signal_direction: str,
        spot: float,
        confidence: float,
        vix: float,
        dte: int,
        capital: float,
        lot_size_override: Optional[int] = None,
        r: float = _DEFAULT_R,
    ) -> dict:
        """
        Suggest an option trade structure based on signal parameters.

        Logic
        -----
        - Determine option type: LONG → CE; SHORT → PE.
        - High confidence (≥ 6) + low VIX (≤ 20): buy ATM option (delta ~0.50).
        - Low confidence (< 6) or high VIX (> 20): suggest defined-risk spread.
          Bull Call Spread (CE) or Bear Put Spread (PE).
        - Delta target: 0.45–0.55 for outright; 0.30–0.45 for spread long leg.

        Parameters
        ----------
        signal_direction : "LONG" or "SHORT"
        spot             : underlying spot price
        confidence       : signal confidence score (1–10)
        vix              : current India VIX
        dte              : calendar days to expiry
        capital          : available capital for the trade (₹)
        lot_size_override: if set, use instead of hardcoded lot sizes
        r                : risk-free rate

        Returns
        -------
        dict with keys:
            type, strike, delta, iv, premium_est,
            max_loss, spread_suggestion
        """
        opt_type = "CE" if signal_direction.upper() == "LONG" else "PE"
        atm = self._atm_strike(spot)
        sigma = vix / 100.0
        T = max(dte, 0.25) / 365.0
        ls = lot_size_override or _DEFAULT_LOT_SIZE

        # Decide structure
        use_spread = (confidence < 6.0) or (vix > 20.0)

        if not use_spread:
            # Outright ATM option
            strike = atm
            premium = self._bs_price(spot, strike, T, r, sigma, opt_type)
            delta = self._bs_delta(spot, strike, T, r, sigma, opt_type)
            max_loss_per_lot = premium * ls
            lots = max(1, int(capital * 0.02 / max_loss_per_lot)) if max_loss_per_lot > 0 else 1
            max_loss = max_loss_per_lot * lots

            spread_info: dict = {}

        else:
            # Defined-risk spread
            if opt_type == "CE":
                # Bull Call Spread: buy ATM CE, sell OTM CE (1 strike away)
                long_strike = atm
                short_strike = atm + _STRIKE_STEP
            else:
                # Bear Put Spread: buy ATM PE, sell OTM PE (1 strike below)
                long_strike = atm
                short_strike = atm - _STRIKE_STEP

            strike = long_strike
            long_sigma = self._iv_smile(sigma, 0)
            short_sigma = self._iv_smile(sigma, 1 if opt_type == "CE" else -1)

            long_premium = self._bs_price(spot, long_strike, T, r, long_sigma, opt_type)
            short_premium = self._bs_price(spot, short_strike, T, r, short_sigma, opt_type)

            net_debit = long_premium - short_premium
            premium = net_debit
            delta = self._bs_delta(spot, long_strike, T, r, long_sigma, opt_type)

            max_loss_per_lot = max(net_debit, 0.0) * ls
            lots = max(1, int(capital * 0.02 / max_loss_per_lot)) if max_loss_per_lot > 0 else 1
            max_loss = max_loss_per_lot * lots

            spread_type = "Bull Call Spread" if opt_type == "CE" else "Bear Put Spread"
            spread_info = {
                "type": spread_type,
                "long_strike": long_strike,
                "long_premium": round(long_premium, 2),
                "short_strike": short_strike,
                "short_premium": round(short_premium, 2),
                "net_debit": round(net_debit, 2),
                "max_profit_per_lot": round(
                    (abs(short_strike - long_strike) - net_debit) * ls, 2
                ),
                "breakeven": round(
                    long_strike + net_debit if opt_type == "CE"
                    else long_strike - net_debit,
                    2,
                ),
            }

        return {
            "type": opt_type,
            "strike": strike,
            "delta": round(abs(delta), 4),
            "iv": round(sigma * 100.0, 2),
            "premium_est": round(premium, 2),
            "max_loss": round(max_loss, 2),
            "lots_suggested": lots,
            "use_spread": use_spread,
            "spread_suggestion": spread_info,
        }

    # ------------------------------------------------------------------
    # Public: CE/PE walls (support/resistance from OI)
    # ------------------------------------------------------------------

    def get_ce_pe_walls(self, chain: dict) -> dict:
        """
        Identify the highest OI CE and PE strikes (market walls).

        CE wall = strike with maximum CE OI → resistance level.
        PE wall = strike with maximum PE OI → support level.

        Parameters
        ----------
        chain : dict from get_option_chain()

        Returns
        -------
        dict:
            ce_wall      : int   — resistance strike
            pe_wall      : int   — support strike
            ce_wall_oi   : float — CE OI at resistance
            pe_wall_oi   : float — PE OI at support
        """
        strikes_data = chain.get("strikes", [])
        if not strikes_data:
            atm = chain.get("atm", 0)
            return {
                "ce_wall": atm + _STRIKE_STEP,
                "pe_wall": atm - _STRIKE_STEP,
                "ce_wall_oi": 0.0,
                "pe_wall_oi": 0.0,
            }

        ce_wall_row = max(strikes_data, key=lambda r: r["ce_oi"])
        pe_wall_row = max(strikes_data, key=lambda r: r["pe_oi"])

        return {
            "ce_wall": ce_wall_row["strike"],
            "pe_wall": pe_wall_row["strike"],
            "ce_wall_oi": float(ce_wall_row["ce_oi"]),
            "pe_wall_oi": float(pe_wall_row["pe_oi"]),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

options_engine = OptionsEngine()
