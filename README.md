# Brainee Trades

Nifty 50 intraday trading research — strategy backtests, live signals, and a trade journal. Single-file frontend, Python serverless API, deployed on Vercel.

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | Single-file vanilla HTML/CSS/JS (`index.html`) — no build step |
| Charts | [uPlot](https://github.com/leeoniya/uPlot) (CDN, ~40 KB) |
| API | Python [FastAPI](https://fastapi.tiangolo.com/) serverless functions (`api/`) |
| Data | [yfinance](https://github.com/ranaroussi/yfinance) for 5-minute NSE OHLCV |
| Deploy | [Vercel](https://vercel.com) — static frontend + `@vercel/python` functions |
| CI | GitHub Actions — strategy unit tests on Python 3.11 |

---

## Project Structure

```
Braineetrades/
├── index.html                    # Single-file SPA (4 tabs)
├── vercel.json                   # Routes + CORS headers
├── requirements.txt              # Python deps for Vercel
│
├── api/
│   ├── live_quotes.py            # GET /api/live-quotes
│   ├── scan.py                   # POST /api/scan
│   └── backtest.py               # POST /api/backtest
│
├── strategies/
│   ├── base.py                   # Strategy ABC + SignalResult + shared indicators
│   ├── vwap_reversal.py
│   ├── opening_range_breakout.py
│   ├── supertrend_ema.py
│   ├── rsi_divergence.py
│   ├── gap_fade.py
│   ├── __init__.py               # REGISTRY dict
│   └── tests/
│       └── test_strategies.py    # 48 pytest tests
│
├── backtest/
│   ├── engine.py                 # Pure-pandas event-driven simulator
│   ├── metrics.py                # Sharpe, drawdown, win rate, etc.
│   └── runner.py                 # CLI: python -m backtest.runner
│
└── public/data/
    ├── nifty50.json              # 50 symbols with name, sector, lot_size
    ├── leaderboard.json          # Latest strategy rankings
    └── results/                  # Per-strategy backtest JSON
        ├── vwap_reversal.json
        ├── opening_range_breakout.json
        ├── supertrend_ema.json
        ├── rsi_divergence.json
        └── gap_fade.json
```

---

## Local Development

### Prerequisites

```bash
python 3.11+
pip install pandas numpy pytz yfinance fastapi uvicorn pydantic scipy
```

### Run the API locally

```bash
uvicorn api.live_quotes:app --reload --port 8001
uvicorn api.scan:app       --reload --port 8002
uvicorn api.backtest:app   --reload --port 8003
```

Then open `index.html` directly in a browser. Update the fetch URLs in `index.html` to point to `localhost` ports if needed, or use Vercel CLI (`vercel dev`) for a unified local server.

### Run the backtest CLI

```bash
# Full run — all 5 strategies, last 60 days, all Nifty 50 symbols
python -m backtest.runner --strategy all

# Single strategy, custom period
python -m backtest.runner --strategy vwap_reversal --days 30

# Quick mode — 10 liquid symbols, 14 days (fast, for testing)
python -m backtest.runner --strategy all --quick

# Custom symbols and capital
python -m backtest.runner --strategy supertrend_ema --symbols RELIANCE.NS TCS.NS --capital 500000
```

Results are written to `public/data/results/<strategy_key>.json` and `public/data/leaderboard.json`.

### Run the tests

```bash
pytest strategies/tests/ -v
```

---

## Deploy to Vercel

1. Fork / clone this repo.
2. Import it into [vercel.com](https://vercel.com) — it auto-detects `vercel.json`.
3. No environment variables required — yfinance pulls public NSE data.
4. The `@vercel/python` builder bundles each `api/*.py` as a separate serverless function.

> **Note:** The `/api/backtest` endpoint can take 30–90 s for a full 50-symbol run.  
> On Vercel Pro you can set `maxDuration` to 300 in the project settings.

---

## Strategies

All strategies inherit from `Strategy` (ABC in `strategies/base.py`) and implement one method:

```python
def generate_signals(self, df: pd.DataFrame) -> SignalResult:
    ...
```

`df` is a 5-minute IST-indexed DataFrame with columns `open high low close volume`.  
`SignalResult` carries four `pd.Series` (same index as `df`): `entries`, `exits`, `sl`, `target`, and a `signal_meta` dict.

### Adding a new strategy

1. Create `strategies/my_strategy.py` — subclass `Strategy`, implement `generate_signals`.
2. Register it in `strategies/__init__.py`:
   ```python
   from strategies.my_strategy import MyStrategy
   REGISTRY["my_strategy"] = MyStrategy
   ```
3. Add contract tests in `strategies/tests/test_strategies.py`.
4. Run the CLI to generate results and update the leaderboard.

### Built-in strategies

| Key | Name | Logic |
|---|---|---|
| `vwap_reversal` | VWAP Reversal | Entry on >1% pullback below VWAP recovering to mean with volume confirmation |
| `opening_range_breakout` | Opening Range Breakout | First 15-min range; entry on first close above range high with 1.5× avg volume |
| `supertrend_ema` | Supertrend + EMA | Supertrend flips bullish and close > EMA(20) |
| `rsi_divergence` | RSI Divergence | Bullish divergence: price makes lower low, RSI makes higher low |
| `gap_fade` | Gap Fade | 0.8–2% gap-down opens; fade back to previous close |

---

## API Reference

### `GET /api/live-quotes`

Returns current OHLCV + change for all Nifty 50 symbols. 30-second in-memory cache.

```json
{
  "market_open": true,
  "timestamp": "2025-05-20T14:30:00+05:30",
  "cached": false,
  "quotes": [
    { "symbol": "RELIANCE.NS", "ltp": 1234.55, "change": 9.55, "change_pct": 0.78, ... }
  ]
}
```

### `POST /api/scan`

Scans for active signals across Nifty 50 (or a custom symbol list).

```json
// Request
{ "strategy": "vwap_reversal", "symbols": ["RELIANCE.NS"] }

// Response
{
  "strategy": "VWAP Reversal",
  "market_open": true,
  "signals": [
    { "symbol": "HCLTECH.NS", "ltp": 1612.4, "entry": 1608.0, "sl": 1595.0, "target": 1620.0, "rr": 1.14, ... }
  ]
}
```

A signal is "active" if an entry fired within the last 6 bars (~30 min) with no subsequent exit.

### `POST /api/backtest`

Runs a full backtest over a date range (≤ 60 days, yfinance 5m limit).

```json
// Request
{ "strategy": "supertrend_ema", "start": "2025-03-01", "end": "2025-05-01", "capital": 100000 }

// Response — full trade log + metrics + equity curve
{
  "strategy": "Supertrend + EMA",
  "summary": { "sharpe": 0.31, "win_rate": 0.52, "max_drawdown": -0.04, ... },
  "trades": [ ... ],
  "equity_curve": [ ... ],
  "per_symbol": { "RELIANCE.NS": { ... }, ... }
}
```

---

## Backtest Engine

- **Position sizing**: `qty = floor(capital × 1% / (entry − SL))` per slot
- **Concurrency**: max 3 open positions; `capital_per_slot = capital / 3`
- **Exit priority**: SL hit (low ≤ sl) → target hit (high ≥ target) → strategy exit signal → 3:15 PM IST hard square-off
- **Costs**: 0.03% fee + 0.01% slippage per leg; 0.01% STT on the sell leg
- **Data**: yfinance 5-minute bars, IST-localised, zero-volume bars removed

---

## Daily Backtest Refresh (GitHub Action)

`.github/workflows/daily_backtest.yml` runs at **16:00 IST Mon–Fri** (10:30 UTC):

1. Installs Python dependencies
2. Runs `python -m backtest.runner --strategy all --days 60`
3. Commits updated `public/data/results/*.json` + `leaderboard.json` with `[skip ci]`
4. Pushes to `main` — Vercel redeploys automatically

You can also trigger it manually from the **Actions** tab.

---

## Design Notes

- **No fake data** — every widget shows "Data unavailable" on API errors; no hardcoded prices
- **All times in IST** — `Asia/Kolkata` throughout (API, signals, journal)
- **Mobile-first** — responsive grid breakpoints at 640 px
- **Aesthetic** — matte black `#080808` + acid green `#B8FF57`, Barlow Condensed headings, DM Sans body
