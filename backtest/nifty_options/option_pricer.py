# CHECKPOINT 3 — Black-Scholes option pricing
# Synthesises CE/PE prices from spot price + India VIX as ATM IV proxy
# Functions:
#   bs_price(S, K, T, r, sigma, option_type) -> float
#   atm_strike(spot, lot_granularity=50) -> int
#   option_chain_snapshot(spot, vix, dte, strikes) -> DataFrame
