# NIFTY 50 Weekly Options Intraday Backtest

5-year backtest (2020–2026) of 10 intraday strategies on NIFTY 50 weekly options.

## Module layout

```
backtest/nifty_options/
├── calendar.py          # expiry calendar + NSE holidays
├── data_loader.py       # 5-min OHLCV + India VIX fetcher
├── option_pricer.py     # Black-Scholes CE/PE pricing
├── indicators.py        # VWAP, EMA, Supertrend, ATR, ORB
├── engine/
│   ├── simulator.py     # event-driven intraday simulator
│   └── trade.py         # Trade dataclass
├── strategies/
│   ├── base.py          # abstract Strategy base class
│   ├── orb15.py         # ORB-15
│   ├── orb30.py         # ORB-30
│   ├── vwap_reclaim.py  # VWAP Reclaim
│   ├── vwap_rejection.py# VWAP Rejection
│   ├── gap_fade.py      # Gap Fade
│   ├── gap_go.py        # Gap & Go
│   ├── supertrend_ema.py# Supertrend + EMA
│   ├── first_hour_bo.py # First Hour Breakout
│   ├── vix_regime_orb.py# VIX Regime ORB
│   └── inside_bar_bo.py # Inside Bar Breakout
├── ranking.py           # composite score + top5_config.json writer
├── runner.py            # CLI entry point
├── tests/
│   ├── test_calendar.py
│   └── test_pricer.py
├── output/              # gitignored — generated JSON/CSV reports
├── requirements.txt
└── .gitignore
```

## Key parameters

| Parameter | Value |
|---|---|
| Backtest period | 2020-01-01 → 2025-12-31 |
| Intraday entry window | 09:20 – 14:30 IST |
| Forced exit | 15:20 IST |
| Weekly expiry | Thursday (pre-2025), Tuesday (2025+) |
| Lot sizes | 25 (pre Apr-2021), 50 (Apr-2021 – Nov-2024), 75 (Nov-2024+) |
| Capital | ₹1,00,000 |
| Risk per trade | 2% of capital |
| Brokerage (RT) | ₹40 |
| Slippage | ₹1 per leg |
| Option pricing | Black-Scholes (spot + India VIX as ATM IV) |

## Data sources

1. Upstox Historical API (primary)
2. jugaad-data (fallback)
3. yfinance / `^NSEI` (final fallback)

## Ranking formula

`score = 0.30×Sharpe + 0.20×Calmar + 0.15×profit_factor + 0.10×win_rate − 0.15×max_dd + 0.10×expectancy`

Top-5 strategies exported to `public/data/top5_config.json` for consumption by the live site.

## Usage

```bash
pip install -r backtest/nifty_options/requirements.txt
python -m backtest.nifty_options.runner --start 2020-01-01 --end 2025-12-31
```
