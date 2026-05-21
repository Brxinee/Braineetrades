"""
backtest.py — Realistic 5-year NIFTY options backtest engine.

Design goals:
  - 1-year run in < 30 seconds (vectorised where possible; no per-bar Python loops).
  - Realistic F&O cost model (same as risk.py).
  - Black-Scholes option pricing (same math as options.py — no scipy).
  - Market regime classification (same as scanners.py).
  - Max 1 position at a time; 2% capital at risk per trade.
  - Entry window: 09:20–14:30 IST; forced exit at 15:15 IST.
  - NSE holidays excluded (frozenset of known dates 2020–2026).

Output metrics: total_trades, win_rate, avg_win, avg_loss, expectancy,
  profit_factor, sharpe, calmar, max_dd, cagr, total_return_pct,
  final_capital, monthly_returns, equity_curve, trades, regime_breakdown,
  exit_breakdown.
"""

from __future__ import annotations

import logging
import math
import sys
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.data_loader import loader
from backend.strategies import STRATEGY_REGISTRY as REGISTRY

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# NSE Holidays 2020–2026
# ---------------------------------------------------------------------------

NSE_HOLIDAYS: frozenset[date] = frozenset([
    # 2020
    date(2020, 2, 21),  # Mahashivratri
    date(2020, 3, 10),  # Holi
    date(2020, 4, 2),   # Ram Navami
    date(2020, 4, 6),   # Mahavir Jayanti
    date(2020, 4, 10),  # Good Friday
    date(2020, 4, 14),  # Dr Ambedkar Jayanti
    date(2020, 5, 25),  # Id-Ul-Fitr (Ramadan Eid)
    date(2020, 10, 2),  # Gandhi Jayanti / Mahatma Gandhi
    date(2020, 11, 16), # Gurunanak Jayanti
    date(2020, 11, 30), # Gurunanaka Jayanti (extra)
    # 2021
    date(2021, 1, 26),  # Republic Day
    date(2021, 3, 11),  # Mahashivratri
    date(2021, 3, 29),  # Holi
    date(2021, 4, 2),   # Good Friday
    date(2021, 4, 14),  # Dr Ambedkar Jayanti
    date(2021, 4, 21),  # Ram Navami
    date(2021, 5, 13),  # Id-Ul-Fitr
    date(2021, 7, 21),  # Bakri Id
    date(2021, 8, 19),  # Muharram
    date(2021, 10, 15), # Dussehra
    date(2021, 11, 4),  # Diwali Laxmi Puja
    date(2021, 11, 5),  # Diwali-Balipratipada
    date(2021, 11, 19), # Gurunanak Jayanti
    # 2022
    date(2022, 1, 26),  # Republic Day
    date(2022, 3, 1),   # Mahashivratri
    date(2022, 3, 18),  # Holi
    date(2022, 4, 14),  # Dr Ambedkar Jayanti / Good Friday
    date(2022, 4, 15),  # Good Friday
    date(2022, 5, 3),   # Id-Ul-Fitr
    date(2022, 8, 9),   # Muharram
    date(2022, 8, 15),  # Independence Day
    date(2022, 10, 2),  # Gandhi Jayanti / Dussehra
    date(2022, 10, 5),  # Dussehra
    date(2022, 10, 24), # Diwali Laxmi Puja
    date(2022, 10, 26), # Diwali-Balipratipada
    date(2022, 11, 8),  # Gurunanak Jayanti
    # 2023
    date(2023, 1, 26),  # Republic Day
    date(2023, 3, 7),   # Holi
    date(2023, 3, 30),  # Ram Navami
    date(2023, 4, 4),   # Mahavir Jayanti
    date(2023, 4, 7),   # Good Friday
    date(2023, 4, 14),  # Dr Ambedkar Jayanti
    date(2023, 5, 1),   # Maharashtra Day
    date(2023, 6, 29),  # Bakri Id
    date(2023, 8, 15),  # Independence Day
    date(2023, 9, 19),  # Ganesh Chaturthi
    date(2023, 10, 2),  # Gandhi Jayanti / Dussehra
    date(2023, 10, 24), # Dussehra
    date(2023, 11, 13), # Diwali Laxmi Puja
    date(2023, 11, 14), # Diwali-Balipratipada
    date(2023, 11, 27), # Gurunanak Jayanti
    date(2023, 12, 25), # Christmas
    # 2024
    date(2024, 1, 22),  # Ram Mandir Consecration (special holiday)
    date(2024, 1, 26),  # Republic Day
    date(2024, 3, 25),  # Holi
    date(2024, 3, 29),  # Good Friday
    date(2024, 4, 11),  # Id-Ul-Fitr
    date(2024, 4, 14),  # Dr Ambedkar Jayanti
    date(2024, 4, 17),  # Ram Navami
    date(2024, 4, 21),  # Mahavir Jayanti
    date(2024, 5, 23),  # Buddha Pournima
    date(2024, 6, 17),  # Bakri Id
    date(2024, 7, 17),  # Muharram
    date(2024, 8, 15),  # Independence Day
    date(2024, 10, 2),  # Gandhi Jayanti
    date(2024, 11, 1),  # Diwali Laxmi Puja
    date(2024, 11, 15), # Gurunanak Jayanti
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 26),  # Republic Day
    date(2025, 2, 26),  # Mahashivratri
    date(2025, 3, 14),  # Holi
    date(2025, 3, 31),  # Id-Ul-Fitr
    date(2025, 4, 10),  # Ram Navami (approximate)
    date(2025, 4, 14),  # Dr Ambedkar Jayanti
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 1),   # Maharashtra Day
    date(2025, 8, 15),  # Independence Day
    date(2025, 10, 2),  # Gandhi Jayanti
    date(2025, 10, 21), # Dussehra (approximate)
    date(2025, 10, 20), # Diwali Laxmi Puja (approximate)
    date(2025, 11, 5),  # Gurunanak Jayanti (approximate)
    date(2025, 12, 25), # Christmas
    # 2026
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 20),  # Holi (approximate)
    date(2026, 4, 3),   # Good Friday (approximate)
    date(2026, 4, 14),  # Dr Ambedkar Jayanti
    date(2026, 8, 15),  # Independence Day
    date(2026, 10, 2),  # Gandhi Jayanti
    date(2026, 12, 25), # Christmas
])


# ---------------------------------------------------------------------------
# F&O cost constants (same as risk.py)
# ---------------------------------------------------------------------------

_BROKERAGE = 20.0           # ₹ per order flat
_STT_RATE = 0.0005          # sell-side only
_EXCHANGE_RATE = 0.00053    # both sides
_GST_RATE = 0.18
_SEBI_RATE = 10.0 / 1e7
_STAMP_RATE = 0.00003       # buy side only
_SLIP_RATE = 0.005          # per leg
_DEFAULT_R = 0.065          # risk-free rate
_LOT_SIZE_NIFTY = 75
_RISK_FRACTION = 0.02       # 2% capital per trade

# Session boundaries (IST)
_ENTRY_OPEN_H, _ENTRY_OPEN_M = 9, 20
_ENTRY_CLOSE_H, _ENTRY_CLOSE_M = 14, 30
_FORCE_EXIT_H, _FORCE_EXIT_M = 15, 15


# ---------------------------------------------------------------------------
# Black-Scholes (no scipy — math.erfc approximation)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return math.erfc(-x / math.sqrt(2.0)) / 2.0


def _bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> float:
    """European option price via Black-Scholes."""
    is_call = option_type.upper() == "CE"
    if T <= 0.0:
        intrinsic = (S - K) if is_call else (K - S)
        return float(max(intrinsic, 0.0))
    if sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc = math.exp(-r * T)
    if is_call:
        return float(S * _norm_cdf(d1) - K * disc * _norm_cdf(d2))
    return float(K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1))


def _atm_strike(spot: float, step: int = 50) -> int:
    return int(round(spot / step) * step)


def _option_px_intraday(
    spot: float,
    strike: int,
    opt_type: str,
    ts: pd.Timestamp,
    expiry: date,
    vix: float,
    r: float = _DEFAULT_R,
) -> float:
    """
    BS option price with continuous intraday time decay.

    Linearly interpolates the fraction of the trading day that has elapsed
    so theta accumulates continuously rather than only rolling over at midnight.
    """
    ts_ist = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize(IST)
    trade_date = ts_ist.date()
    cal_dte = (expiry - trade_date).days

    sess_start = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(hours=9, minutes=15)
    sess_end = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(hours=15, minutes=30)
    sess_secs = (sess_end - sess_start).total_seconds()
    elapsed = float(
        min(max((ts_ist - sess_start).total_seconds(), 0.0), sess_secs)
    )
    day_frac = elapsed / sess_secs

    T = max((cal_dte - day_frac) / 365.0, 0.25 / 365.0)
    sigma = max(vix / 100.0, 0.05)
    return _bs_price(spot, strike, T, r, sigma, opt_type)


# ---------------------------------------------------------------------------
# Next NIFTY weekly expiry (Thursday)
# ---------------------------------------------------------------------------

def _next_expiry(trade_date: date) -> date:
    """Return the next Thursday (NIFTY weekly expiry) on or after trade_date."""
    d = trade_date
    # Thursday = weekday 3
    days_ahead = (3 - d.weekday()) % 7
    candidate = d + timedelta(days=days_ahead)
    # If candidate is a holiday, roll forward to next Thursday
    while candidate in NSE_HOLIDAYS:
        candidate += timedelta(days=7)
    return candidate


# ---------------------------------------------------------------------------
# Trading day iterator
# ---------------------------------------------------------------------------

def _trading_days(start: date, end: date) -> list[date]:
    """Return weekdays (Mon–Fri) in [start, end] excluding NSE holidays."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in NSE_HOLIDAYS:
            days.append(current)
        current += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Transaction cost calculator
# ---------------------------------------------------------------------------

def _calc_costs(
    entry_px: float,
    exit_px: float,
    lots: int,
    lot_size: int = _LOT_SIZE_NIFTY,
) -> float:
    """
    Full NSE F&O transaction cost for a round-trip options trade.

    Parameters
    ----------
    entry_px : option premium at entry
    exit_px  : option premium at exit
    lots     : number of lots
    lot_size : units per lot

    Returns
    -------
    Total cost in ₹ (always positive).
    """
    units = lots * lot_size
    buy_turnover = entry_px * units
    sell_turnover = exit_px * units

    brokerage = _BROKERAGE * 2  # buy + sell

    stt = sell_turnover * _STT_RATE  # sell-side only for options

    exchange = (buy_turnover + sell_turnover) * _EXCHANGE_RATE

    gst = (brokerage + exchange) * _GST_RATE

    sebi = (buy_turnover + sell_turnover) * _SEBI_RATE

    stamp = buy_turnover * _STAMP_RATE

    slippage = (buy_turnover + sell_turnover) * _SLIP_RATE

    return brokerage + stt + exchange + gst + sebi + stamp + slippage


# ---------------------------------------------------------------------------
# Regime classification (inline — no import from scanners to avoid circularity)
# ---------------------------------------------------------------------------

def _classify_regime(df_5m: pd.DataFrame, vix: float) -> str:
    """Classify day's regime; returns one of five REGIME_* strings."""
    if df_5m is None or len(df_5m) < 10:
        return "SIDEWAYS_LOW_VIX"

    close = df_5m["close"]
    n = len(close)

    if vix >= 25:
        return "VOLATILE"

    trend = "FLAT"
    if n >= 20:
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if n >= 50 else ema20
        lc = float(close.iloc[-1])
        if lc > ema20 and ema20 >= ema50:
            trend = "UP"
        elif lc < ema20 and ema20 <= ema50:
            trend = "DOWN"

    if trend == "UP":
        return "TRENDING_BULL"
    if trend == "DOWN":
        return "TRENDING_BEAR"
    return "SIDEWAYS_LOW_VIX" if vix < 18 else "SIDEWAYS_HIGH_VIX"


# ---------------------------------------------------------------------------
# BacktestTrade dataclass
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    """One completed simulated trade."""
    date: date
    symbol: str
    strategy: str
    direction: str          # "CE" or "PE"
    strike: int
    expiry: date
    entry_time: str         # ISO-8601
    entry_px: float         # option premium at entry
    exit_time: str          # ISO-8601
    exit_px: float          # option premium at exit
    exit_reason: str        # "sl" | "target" | "forced"
    lots: int
    lot_size: int
    gross_pnl: float
    net_pnl: float
    costs: float
    running_capital: float
    regime: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "symbol": self.symbol,
            "strategy": self.strategy,
            "direction": self.direction,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "entry_time": self.entry_time,
            "entry_px": round(self.entry_px, 2),
            "exit_time": self.exit_time,
            "exit_px": round(self.exit_px, 2),
            "exit_reason": self.exit_reason,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "gross_pnl": round(self.gross_pnl, 2),
            "net_pnl": round(self.net_pnl, 2),
            "costs": round(self.costs, 2),
            "running_capital": round(self.running_capital, 2),
            "regime": self.regime,
        }


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Realistic intraday NIFTY options backtest.

    Usage
    -----
    engine = BacktestEngine()
    result = engine.run(
        strategy_key="opening_range_breakout",
        start=date(2023, 1, 1),
        end=date(2023, 12, 31),
        initial_capital=100_000,
    )
    """

    NIFTY_SYMBOL = "^NSEI"
    VIX_SYMBOL = "^INDIAVIX"

    def __init__(self) -> None:
        logger.info("BacktestEngine initialised")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        strategy_key: str,
        start: date,
        end: date,
        initial_capital: float = 100_000.0,
    ) -> dict:
        """
        Run a full strategy backtest over [start, end].

        Parameters
        ----------
        strategy_key    : key into strategies.REGISTRY (e.g. "opening_range_breakout")
        start           : backtest start date (inclusive)
        end             : backtest end date (inclusive)
        initial_capital : starting capital in ₹ (default 100,000)

        Returns
        -------
        Comprehensive result dict (see module docstring for schema).
        """
        t0 = time.perf_counter()
        logger.info(
            "BacktestEngine.run: strategy=%s period=%s→%s capital=₹%.0f",
            strategy_key, start, end, initial_capital,
        )

        if strategy_key not in REGISTRY:
            raise ValueError(
                f"Unknown strategy_key '{strategy_key}'. "
                f"Available: {list(REGISTRY.keys())}"
            )

        StrategyClass = REGISTRY[strategy_key]
        strategy = StrategyClass()

        # ── 1. Load data ────────────────────────────────────────────────
        logger.info("Loading NIFTY 5m data …")
        try:
            df_5m_all = loader.get_5m_ohlcv(self.NIFTY_SYMBOL, start, end)
        except Exception as exc:
            raise RuntimeError(f"Failed to load 5m data: {exc}") from exc

        logger.info("Loading VIX series …")
        try:
            vix_series = loader.get_vix_series(start, end)
        except Exception as exc:
            logger.warning("VIX load failed (%s); using default 15.0", exc)
            # Synthesise flat VIX series
            idx = pd.date_range(start, end, freq="B", tz=IST)
            vix_series = pd.Series(15.0, index=idx, name="vix")

        # ── 2. Enumerate trading days ───────────────────────────────────
        days = _trading_days(start, end)
        logger.info("Simulating %d trading days …", len(days))

        all_trades: list[BacktestTrade] = []
        capital = initial_capital
        daily_capitals: list[tuple[date, float]] = [(start - timedelta(days=1), initial_capital)]

        for trade_date in days:
            # Slice day's 5m data
            day_start = pd.Timestamp(trade_date, tz=IST)
            day_end = day_start + pd.Timedelta(hours=23, minutes=59)
            try:
                df_day = df_5m_all.loc[day_start:day_end]
            except Exception:
                df_day = pd.DataFrame()

            if df_day.empty:
                logger.debug("%s: no 5m data — skip", trade_date)
                daily_capitals.append((trade_date, capital))
                continue

            # VIX for the day
            vix = self._get_vix_for_day(vix_series, trade_date)

            # Regime
            regime = _classify_regime(df_day, vix)

            # Generate strategy signals for the day
            try:
                sig_result = strategy.generate_signals(df_day)
            except Exception as exc:
                logger.debug("%s: signal generation failed — %s", trade_date, exc)
                daily_capitals.append((trade_date, capital))
                continue

            # Simulate this day
            day_trades = self._simulate_day(
                df_day=df_day,
                sig_result=sig_result,
                trade_date=trade_date,
                vix=vix,
                regime=regime,
                capital=capital,
                strategy_key=strategy_key,
            )

            for t in day_trades:
                capital = t.running_capital
                all_trades.append(t)

            daily_capitals.append((trade_date, capital))

        elapsed = time.perf_counter() - t0
        logger.info(
            "BacktestEngine done: %d trades in %.2fs (capital ₹%.0f → ₹%.0f)",
            len(all_trades), elapsed, initial_capital, capital,
        )

        return self._aggregate(
            trades=all_trades,
            daily_capitals=daily_capitals,
            initial_capital=initial_capital,
            final_capital=capital,
            start=start,
            end=end,
            strategy_key=strategy_key,
            elapsed_secs=elapsed,
        )

    # ------------------------------------------------------------------
    # Per-day simulation
    # ------------------------------------------------------------------

    def _simulate_day(
        self,
        df_day: pd.DataFrame,
        sig_result,
        trade_date: date,
        vix: float,
        regime: str,
        capital: float,
        strategy_key: str,
    ) -> list[BacktestTrade]:
        """
        Replay one trading day bar-by-bar, entering on signals and
        exiting on SL / target / forced-close rules.

        Max 1 position at a time (simplicity constraint per spec).
        """
        trades: list[BacktestTrade] = []

        entries_arr = sig_result.entries.values.astype(bool)
        sl_arr = sig_result.sl.fillna(np.nan).values.astype(float)
        tgt_arr = sig_result.target.fillna(np.nan).values.astype(float)
        close_arr = df_day["close"].values.astype(float)
        high_arr = df_day["high"].values.astype(float)
        low_arr = df_day["low"].values.astype(float)
        timestamps = df_day.index

        expiry = _next_expiry(trade_date)
        lot_size = _LOT_SIZE_NIFTY

        # Entry / exit timestamps (IST)
        entry_open_ts = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(
            hours=_ENTRY_OPEN_H, minutes=_ENTRY_OPEN_M
        )
        entry_close_ts = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(
            hours=_ENTRY_CLOSE_H, minutes=_ENTRY_CLOSE_M
        )
        force_exit_ts = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(
            hours=_FORCE_EXIT_H, minutes=_FORCE_EXIT_M
        )

        # Open position state
        in_trade = False
        opt_type: str = "CE"
        strike: int = 0
        entry_ts: pd.Timestamp = entry_open_ts
        entry_px: float = 0.0
        lots: int = 1
        spot_sl: float = 0.0
        spot_tgt: float = 0.0
        entry_bar: int = -1

        for i, ts in enumerate(timestamps):
            spot = float(close_arr[i])

            # ── 1. Exit check ────────────────────────────────────────────
            if in_trade and i > entry_bar:
                exit_px: Optional[float] = None
                exit_reason = ""

                h, lo = float(high_arr[i]), float(low_arr[i])

                if opt_type == "CE":
                    if lo <= spot_sl:
                        exit_spot = float(spot_sl)
                        exit_px = _option_px_intraday(exit_spot, strike, "CE", ts, expiry, vix)
                        exit_reason = "sl"
                    elif h >= spot_tgt:
                        exit_spot = float(spot_tgt)
                        exit_px = _option_px_intraday(exit_spot, strike, "CE", ts, expiry, vix)
                        exit_reason = "target"
                else:  # PE
                    if h >= spot_sl:
                        exit_spot = float(spot_sl)
                        exit_px = _option_px_intraday(exit_spot, strike, "PE", ts, expiry, vix)
                        exit_reason = "sl"
                    elif lo <= spot_tgt:
                        exit_spot = float(spot_tgt)
                        exit_px = _option_px_intraday(exit_spot, strike, "PE", ts, expiry, vix)
                        exit_reason = "target"

                # Forced exit at or after 15:15 IST
                if exit_px is None and ts >= force_exit_ts:
                    exit_px = _option_px_intraday(spot, strike, opt_type, ts, expiry, vix)
                    exit_reason = "forced"

                if exit_px is not None and exit_reason:
                    exit_px = max(exit_px, 0.05)  # floor at 0.05 to avoid negative
                    costs = _calc_costs(entry_px, exit_px, lots, lot_size)
                    units = lots * lot_size
                    gross_pnl = (exit_px - entry_px) * units
                    net_pnl = gross_pnl - costs
                    capital += net_pnl

                    trades.append(BacktestTrade(
                        date=trade_date,
                        symbol=self.NIFTY_SYMBOL,
                        strategy=strategy_key,
                        direction=opt_type,
                        strike=strike,
                        expiry=expiry,
                        entry_time=entry_ts.isoformat(),
                        entry_px=round(entry_px, 2),
                        exit_time=ts.isoformat(),
                        exit_px=round(exit_px, 2),
                        exit_reason=exit_reason,
                        lots=lots,
                        lot_size=lot_size,
                        gross_pnl=round(gross_pnl, 2),
                        net_pnl=round(net_pnl, 2),
                        costs=round(costs, 2),
                        running_capital=round(capital, 2),
                        regime=regime,
                    ))
                    in_trade = False

            # ── 2. Entry check ───────────────────────────────────────────
            if (
                not in_trade
                and entries_arr[i]
                and entry_open_ts <= ts <= entry_close_ts
            ):
                entry_bar = i
                entry_ts = ts
                sl_price = float(sl_arr[i])
                tgt_price = float(tgt_arr[i])

                # Validate sl and target
                if np.isnan(sl_price) or sl_price <= 0:
                    sl_price = spot * 0.99
                if np.isnan(tgt_price) or tgt_price <= 0:
                    tgt_price = spot * 1.01

                # Infer direction
                if tgt_price > spot:
                    opt_type = "CE"
                    spot_sl = sl_price
                    spot_tgt = tgt_price
                else:
                    opt_type = "PE"
                    spot_sl = sl_price
                    spot_tgt = tgt_price

                strike = _atm_strike(spot)
                entry_px = _option_px_intraday(spot, strike, opt_type, ts, expiry, vix)
                entry_px = max(entry_px, 0.05)

                # Option risk = full premium (buyer's max loss)
                max_risk_rs = capital * _RISK_FRACTION
                cost_per_lot = entry_px * lot_size
                if cost_per_lot > 0:
                    lots = max(1, int(max_risk_rs / cost_per_lot))
                else:
                    lots = 1

                in_trade = True

        # End of day: force-close any open position at last bar
        if in_trade and len(timestamps) > 0:
            i = len(timestamps) - 1
            ts = timestamps[i]
            spot = float(close_arr[i])
            exit_px = _option_px_intraday(spot, strike, opt_type, ts, expiry, vix)
            exit_px = max(exit_px, 0.05)
            costs = _calc_costs(entry_px, exit_px, lots, lot_size)
            units = lots * lot_size
            gross_pnl = (exit_px - entry_px) * units
            net_pnl = gross_pnl - costs
            capital += net_pnl

            trades.append(BacktestTrade(
                date=trade_date,
                symbol=self.NIFTY_SYMBOL,
                strategy=strategy_key,
                direction=opt_type,
                strike=strike,
                expiry=expiry,
                entry_time=entry_ts.isoformat(),
                entry_px=round(entry_px, 2),
                exit_time=ts.isoformat(),
                exit_px=round(exit_px, 2),
                exit_reason="forced",
                lots=lots,
                lot_size=lot_size,
                gross_pnl=round(gross_pnl, 2),
                net_pnl=round(net_pnl, 2),
                costs=round(costs, 2),
                running_capital=round(capital, 2),
                regime=regime,
            ))

        return trades

    # ------------------------------------------------------------------
    # Metrics aggregation
    # ------------------------------------------------------------------

    def _aggregate(
        self,
        trades: list[BacktestTrade],
        daily_capitals: list[tuple[date, float]],
        initial_capital: float,
        final_capital: float,
        start: date,
        end: date,
        strategy_key: str,
        elapsed_secs: float,
    ) -> dict:
        """Compute all output metrics from the trade list."""

        n_trades = len(trades)

        if n_trades == 0:
            return self._empty_result(
                initial_capital, final_capital, start, end,
                strategy_key, elapsed_secs, daily_capitals,
            )

        net_pnls = np.array([t.net_pnl for t in trades], dtype=float)
        wins = net_pnls > 0
        losses = net_pnls <= 0

        win_trades = int(wins.sum())
        win_rate = float(win_trades) / n_trades
        avg_win = float(net_pnls[wins].mean()) if wins.any() else 0.0
        avg_loss = float(net_pnls[losses].mean()) if losses.any() else 0.0

        gross_profit = float(net_pnls[wins].sum()) if wins.any() else 0.0
        gross_loss = float(abs(net_pnls[losses].sum())) if losses.any() else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

        # Equity curve from daily capitals
        equity_by_date: dict[date, float] = {}
        for d, cap in daily_capitals:
            equity_by_date[d] = cap

        # Fill forward from start to end
        all_days = _trading_days(start, end)
        prev_cap = initial_capital
        equity_dates: list[date] = []
        equity_vals: list[float] = []
        for d in all_days:
            cap = equity_by_date.get(d, prev_cap)
            equity_dates.append(d)
            equity_vals.append(cap)
            prev_cap = cap

        eq_arr = np.array(equity_vals, dtype=float)
        peak = np.maximum.accumulate(eq_arr)
        dd = (eq_arr - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(dd.min()) if len(dd) > 0 else 0.0

        # Sharpe (annualised, from daily returns)
        sharpe = self._compute_sharpe(equity_dates, equity_vals)

        # CAGR
        n_years = (end - start).days / 365.25
        if n_years > 0 and initial_capital > 0 and final_capital > 0:
            cagr = (final_capital / initial_capital) ** (1.0 / n_years) - 1.0
        else:
            cagr = 0.0

        total_return_pct = (
            (final_capital - initial_capital) / initial_capital * 100.0
            if initial_capital > 0 else 0.0
        )

        # Calmar
        calmar = cagr / abs(max_dd) if max_dd != 0 else float("inf")

        # Monthly returns
        monthly_returns = self._monthly_returns(trades, initial_capital)

        # Equity curve as unix_ms timestamps
        equity_curve: list[list] = []
        for d, v in zip(equity_dates, equity_vals):
            ts_ms = int(
                datetime(d.year, d.month, d.day, 15, 30, 0, tzinfo=IST).timestamp() * 1000
            )
            equity_curve.append([ts_ms, round(v, 2)])

        # Regime breakdown
        regime_breakdown = self._regime_breakdown(trades)

        # Exit breakdown
        exit_breakdown: dict[str, int] = {}
        for t in trades:
            exit_breakdown[t.exit_reason] = exit_breakdown.get(t.exit_reason, 0) + 1

        return {
            "strategy": strategy_key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "total_trades": n_trades,
            "win_trades": win_trades,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(min(profit_factor, 9999.0), 4),
            "sharpe": round(sharpe, 4),
            "calmar": round(min(calmar, 9999.0), 4),
            "max_dd": round(max_dd, 4),
            "cagr": round(cagr, 4),
            "total_return_pct": round(total_return_pct, 2),
            "monthly_returns": monthly_returns,
            "equity_curve": equity_curve,
            "trades": [t.to_dict() for t in trades],
            "regime_breakdown": regime_breakdown,
            "exit_breakdown": exit_breakdown,
            "elapsed_secs": round(elapsed_secs, 2),
            "generated_at": datetime.now(IST).isoformat(),
        }

    # ------------------------------------------------------------------
    # Helper: daily Sharpe
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sharpe(
        equity_dates: list[date],
        equity_vals: list[float],
    ) -> float:
        if len(equity_vals) < 2:
            return 0.0
        eq = pd.Series(equity_vals, index=equity_dates, dtype=float)
        daily_ret = eq.pct_change().dropna()
        if len(daily_ret) < 2 or daily_ret.std() == 0:
            return 0.0
        return float(daily_ret.mean() / daily_ret.std() * math.sqrt(252))

    # ------------------------------------------------------------------
    # Helper: monthly returns
    # ------------------------------------------------------------------

    @staticmethod
    def _monthly_returns(
        trades: list[BacktestTrade],
        initial_capital: float,
    ) -> list[dict]:
        if not trades:
            return []

        monthly: dict[str, float] = {}
        for t in trades:
            key = f"{t.date.year}-{t.date.month:02d}"
            monthly[key] = monthly.get(key, 0.0) + t.net_pnl

        # Sort chronologically
        sorted_months = sorted(monthly.keys())
        result = []
        cumulative = initial_capital
        for m in sorted_months:
            pnl = monthly[m]
            ret_pct = (pnl / cumulative) * 100.0 if cumulative > 0 else 0.0
            result.append({
                "month": m,
                "pnl": round(pnl, 2),
                "return_pct": round(ret_pct, 4),
            })
            cumulative += pnl

        return result

    # ------------------------------------------------------------------
    # Helper: regime breakdown
    # ------------------------------------------------------------------

    @staticmethod
    def _regime_breakdown(trades: list[BacktestTrade]) -> dict:
        breakdown: dict[str, dict] = {}
        for t in trades:
            r = t.regime or "UNKNOWN"
            if r not in breakdown:
                breakdown[r] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            breakdown[r]["trades"] += 1
            if t.net_pnl > 0:
                breakdown[r]["wins"] += 1
            breakdown[r]["total_pnl"] += t.net_pnl

        result = {}
        for regime, stats in breakdown.items():
            n = stats["trades"]
            result[regime] = {
                "trades": n,
                "win_rate": round(stats["wins"] / n, 4) if n > 0 else 0.0,
                "total_pnl": round(stats["total_pnl"], 2),
                "avg_pnl": round(stats["total_pnl"] / n, 2) if n > 0 else 0.0,
            }
        return result

    # ------------------------------------------------------------------
    # Helper: VIX for day
    # ------------------------------------------------------------------

    @staticmethod
    def _get_vix_for_day(vix_series: pd.Series, trade_date: date) -> float:
        """Look up VIX for trade_date from the daily series. Fall back to 15.0."""
        if vix_series is None or vix_series.empty:
            return 15.0
        try:
            # Try exact IST-localised Timestamp match
            key = pd.Timestamp(trade_date, tz=IST)
            matches = vix_series.loc[
                vix_series.index.normalize() == key.normalize()
            ]
            if not matches.empty:
                val = float(matches.iloc[0])
                return val if not math.isnan(val) and val > 0 else 15.0
        except Exception:
            pass
        return 15.0

    # ------------------------------------------------------------------
    # Helper: empty result
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(
        initial_capital: float,
        final_capital: float,
        start: date,
        end: date,
        strategy_key: str,
        elapsed_secs: float,
        daily_capitals: list[tuple[date, float]],
    ) -> dict:
        # Build minimal equity curve
        equity_curve = []
        for d, cap in daily_capitals:
            ts_ms = int(
                datetime(d.year, d.month, d.day, 15, 30, 0, tzinfo=IST).timestamp() * 1000
            )
            equity_curve.append([ts_ms, round(cap, 2)])

        return {
            "strategy": strategy_key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "total_trades": 0,
            "win_trades": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "calmar": 0.0,
            "max_dd": 0.0,
            "cagr": 0.0,
            "total_return_pct": 0.0,
            "monthly_returns": [],
            "equity_curve": equity_curve,
            "trades": [],
            "regime_breakdown": {},
            "exit_breakdown": {},
            "elapsed_secs": round(elapsed_secs, 2),
            "generated_at": datetime.now(IST).isoformat(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

backtest_engine = BacktestEngine()
