"""Black-Scholes option pricing for NIFTY 50 weekly options.

Synthesises CE/PE prices from:
  - spot price  (NIFTY 50 index level)
  - India VIX   (annualised implied volatility, quoted in percent)
  - days to expiry
  - risk-free rate (default: India repo rate ~6.5%)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

_DEFAULT_R = 0.065          # India repo rate (decimal)
_STRIKE_GRANULARITY = 50    # NIFTY option strikes at 50-point intervals


# ── core Black-Scholes ────────────────────────────────────────────────────────

def _d_terms(
    S: float, K: float, T: float, r: float, sigma: float
) -> tuple[float, float]:
    sqrt_T = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "CE",
) -> float:
    """European option price via Black-Scholes.

    Parameters
    ----------
    S : spot price
    K : strike price
    T : time to expiry in years (e.g. 7/365 for 7 calendar days)
    r : risk-free rate (decimal)
    sigma : annualised implied volatility (decimal, e.g. 0.20)
    option_type : "CE" (call) or "PE" (put)

    Returns
    -------
    Option premium in index points (same units as S and K).
    """
    is_call = option_type.upper() in ("CE", "CALL", "C")

    if T <= 0:
        return float(max(S - K, 0.0) if is_call else max(K - S, 0.0))
    if sigma <= 0 or S <= 0:
        return 0.0

    d1, d2 = _d_terms(S, K, T, r, sigma)
    if is_call:
        return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))
    return float(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


# ── greeks ────────────────────────────────────────────────────────────────────

def bs_delta(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "CE"
) -> float:
    """Option delta (∂price/∂S).  Call ∈ (0,1), Put ∈ (-1,0)."""
    is_call = option_type.upper() in ("CE", "CALL", "C")
    if T <= 0 or sigma <= 0:
        return float((1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0))
    d1, _ = _d_terms(S, K, T, r, sigma)
    return float(norm.cdf(d1) if is_call else norm.cdf(d1) - 1.0)


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Gamma (∂²price/∂S²).  Identical for calls and puts."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = _d_terms(S, K, T, r, sigma)
    return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))


def bs_theta(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "CE"
) -> float:
    """Theta per calendar day (negative = time decay)."""
    is_call = option_type.upper() in ("CE", "CALL", "C")
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, d2 = _d_terms(S, K, T, r, sigma)
    common = -(S * norm.pdf(d1) * sigma) / (2.0 * np.sqrt(T))
    if is_call:
        annual = common - r * K * np.exp(-r * T) * norm.cdf(d2)
    else:
        annual = common + r * K * np.exp(-r * T) * norm.cdf(-d2)
    return float(annual / 365.0)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega: price change per 1 percentage-point move in IV."""
    if T <= 0:
        return 0.0
    d1, _ = _d_terms(S, K, T, r, sigma)
    return float(S * norm.pdf(d1) * np.sqrt(T) / 100.0)


# ── helpers ───────────────────────────────────────────────────────────────────

def atm_strike(spot: float, granularity: int = _STRIKE_GRANULARITY) -> int:
    """Round spot to the nearest strike multiple.

    atm_strike(21543) → 21550
    atm_strike(21524) → 21500
    """
    return int(round(spot / granularity) * granularity)


def vix_to_iv(vix: float) -> float:
    """India VIX (percentage points) → annualised IV decimal.

    vix_to_iv(18.0) → 0.18
    """
    return vix / 100.0


# ── option chain ──────────────────────────────────────────────────────────────

def option_chain_snapshot(
    spot: float,
    vix: float,
    dte_days: int,
    num_strikes: int = 5,
    r: float = _DEFAULT_R,
) -> pd.DataFrame:
    """Generate an ATM-centred option chain using Black-Scholes.

    Parameters
    ----------
    spot       : current NIFTY 50 level
    vix        : India VIX (percent, e.g. 18.0)
    dte_days   : calendar days to expiry
    num_strikes: number of strikes above and below ATM (total = 2*n + 1)
    r          : risk-free rate (decimal)

    Returns
    -------
    DataFrame with columns:
        strike, ce_price, pe_price, ce_delta, pe_delta, iv, dte_days
    """
    # Clamp T to a minimum of half a day to avoid d1/d2 blow-up at expiry
    T = max(dte_days, 0.5) / 365.0
    sigma = vix_to_iv(vix)
    atm = atm_strike(spot)
    strikes = [atm + _STRIKE_GRANULARITY * i for i in range(-num_strikes, num_strikes + 1)]

    rows = [
        {
            "strike": K,
            "ce_price": bs_price(spot, K, T, r, sigma, "CE"),
            "pe_price": bs_price(spot, K, T, r, sigma, "PE"),
            "ce_delta": bs_delta(spot, K, T, r, sigma, "CE"),
            "pe_delta": bs_delta(spot, K, T, r, sigma, "PE"),
            "iv": sigma,
            "dte_days": dte_days,
        }
        for K in strikes
    ]
    return pd.DataFrame(rows)
