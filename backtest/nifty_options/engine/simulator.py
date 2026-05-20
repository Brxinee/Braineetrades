"""Event-driven intraday options simulator.

One pass per trading day:
  1. Strategy generates signals from the full day's 5-min OHLCV.
  2. Simulator replays bars sequentially, entering when a signal fires
     (within the 9:20–14:30 entry window) and exiting on SL / target /
     15:15 forced close — whichever comes first.

Option prices are synthesised via Black-Scholes (spot + India VIX as IV).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd
import pytz

from ..calendar import lot_size, next_expiry, trading_days
from ..data_loader import load_5m_ohlcv, load_daily_vix
from ..option_pricer import atm_strike, bs_price, vix_to_iv
from ..strategies.base import Signal, Strategy
from .trade import BROKERAGE_RT, SLIP_PER_LEG, Trade

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_R = 0.065              # India risk-free rate (decimal)
RISK_FRACTION = 0.02    # 2 % capital at risk per trade

# Entry window (IST)
_ENTRY_OPEN = (9, 20)
_ENTRY_CLOSE = (14, 30)
_FORCE_EXIT = (15, 15)  # first bar at / after 15:15 IST → forced close


# ── helpers ───────────────────────────────────────────────────────────────────

def _ist_ts(day: date, hour: int, minute: int) -> pd.Timestamp:
    return pd.Timestamp(day, tz=IST) + pd.Timedelta(hours=hour, minutes=minute)


def _option_px(
    spot: float,
    strike: int,
    direction: str,
    ts: pd.Timestamp,
    expiry: date,
    vix: float,
) -> float:
    """Black-Scholes price with intraday time-of-day adjustment.

    Linearly interpolates DTE across the 9:15–15:30 trading session so
    theta accumulates continuously rather than jumping only on day roll.
    """
    ts_ist = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize(IST)
    trade_date = ts_ist.date()
    cal_dte = (expiry - trade_date).days

    sess_start = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(hours=9, minutes=15)
    sess_end = pd.Timestamp(trade_date, tz=IST) + pd.Timedelta(hours=15, minutes=30)
    sess_secs = (sess_end - sess_start).total_seconds()
    elapsed = min(max((ts_ist - sess_start).total_seconds(), 0.0), sess_secs)
    day_frac = elapsed / sess_secs   # 0 = open, 1 = close

    T = max((cal_dte - day_frac) / 365.0, 0.25 / 365.0)
    sigma = vix_to_iv(vix)
    return bs_price(spot, strike, T, _R, sigma, direction)


# ── internal open-trade state ─────────────────────────────────────────────────

@dataclass
class _OpenTrade:
    entry_time: pd.Timestamp
    entry_px: float       # mid-price at entry (before slippage)
    strike: int
    direction: str
    spot_sl: float
    spot_target: float
    lots: int
    # Exit check is suppressed on the same bar we entered
    entry_bar_ts: pd.Timestamp


def _check_exit(
    row: pd.Series,
    ts: pd.Timestamp,
    trade: _OpenTrade,
    expiry: date,
    vix: float,
    force_exit_ts: pd.Timestamp,
) -> tuple[Optional[float], Optional[str]]:
    """Return (exit_price, reason) or (None, None) to stay in trade."""
    if ts == trade.entry_bar_ts:
        return None, None   # never exit on the entry bar

    h, lo = row["high"], row["low"]

    if trade.direction == "CE":
        if lo <= trade.spot_sl:   # SL checked first (conservative)
            return _option_px(trade.spot_sl, trade.strike, "CE", ts, expiry, vix), "sl"
        if h >= trade.spot_target:
            return _option_px(trade.spot_target, trade.strike, "CE", ts, expiry, vix), "target"
    else:  # PE
        if h >= trade.spot_sl:
            return _option_px(trade.spot_sl, trade.strike, "PE", ts, expiry, vix), "sl"
        if lo <= trade.spot_target:
            return _option_px(trade.spot_target, trade.strike, "PE", ts, expiry, vix), "target"

    if ts >= force_exit_ts:
        return _option_px(row["close"], trade.strike, trade.direction, ts, expiry, vix), "forced"

    return None, None


# ── public simulation API ─────────────────────────────────────────────────────

def simulate_day(
    df_5m: pd.DataFrame,
    signals: list[Signal],
    trade_date: date,
    vix: float,
    capital: float,
    strategy_name: str,
) -> tuple[list[Trade], float]:
    """Simulate one session.  Returns (trades_executed, updated_capital).

    Parameters
    ----------
    df_5m         : 5-min OHLCV for *trade_date* only, IST DatetimeIndex
    signals       : signals from strategy.generate_signals()
    trade_date    : date being simulated
    vix           : India VIX (percent, e.g. 18.0)
    capital       : portfolio value before this session
    strategy_name : label written into each Trade record
    """
    trades: list[Trade] = []
    if df_5m.empty or not signals:
        return trades, capital

    expiry = next_expiry(trade_date)
    ls = lot_size(trade_date)

    entry_open_ts = _ist_ts(trade_date, *_ENTRY_OPEN)
    entry_close_ts = _ist_ts(trade_date, *_ENTRY_CLOSE)
    force_exit_ts = _ist_ts(trade_date, *_FORCE_EXIT)

    # Map signal.time → Signal for O(1) bar lookup
    sig_map: dict[pd.Timestamp, Signal] = {s.time: s for s in signals}

    open_trade: Optional[_OpenTrade] = None

    for ts, row in df_5m.iterrows():

        # ── 1. Check exit ────────────────────────────────────────────────────
        if open_trade is not None:
            exit_px, reason = _check_exit(row, ts, open_trade, expiry, vix, force_exit_ts)
            if exit_px is not None:
                units = open_trade.lots * ls
                # Effective prices after slippage (entry paid 1pt more, exit 1pt less)
                eff_entry = open_trade.entry_px + SLIP_PER_LEG
                eff_exit = exit_px - SLIP_PER_LEG
                gross = (eff_exit - eff_entry) * units
                pnl = gross - BROKERAGE_RT
                capital += pnl

                trades.append(
                    Trade(
                        date=trade_date,
                        strategy=strategy_name,
                        direction=open_trade.direction,
                        strike=open_trade.strike,
                        expiry=expiry,
                        entry_time=open_trade.entry_time,
                        entry_px=open_trade.entry_px,
                        exit_time=ts,
                        exit_px=exit_px,
                        exit_reason=reason,
                        lots=open_trade.lots,
                        lot_size=ls,
                        pnl=pnl,
                        running_capital=capital,
                    )
                )
                log.debug(
                    "%s: EXIT %s K=%d @%.2f reason=%s pnl=%.0f",
                    trade_date, open_trade.direction, open_trade.strike,
                    exit_px, reason, pnl,
                )
                open_trade = None

        # ── 2. Check entry ───────────────────────────────────────────────────
        if open_trade is None and entry_open_ts <= ts <= entry_close_ts:
            sig = sig_map.get(ts)
            if sig is not None:
                spot = float(row["close"])
                strike = atm_strike(spot)
                entry_px = _option_px(spot, strike, sig.direction, ts, expiry, vix)
                sl_px = _option_px(sig.spot_sl, strike, sig.direction, ts, expiry, vix)
                # Option SL width in premium points (floor at 1 to avoid division by 0)
                sl_pts = max(entry_px - sl_px, 1.0)
                lots = max(1, int(capital * RISK_FRACTION / (sl_pts * ls)))

                open_trade = _OpenTrade(
                    entry_time=ts,
                    entry_px=entry_px,
                    strike=strike,
                    direction=sig.direction,
                    spot_sl=sig.spot_sl,
                    spot_target=sig.spot_target,
                    lots=lots,
                    entry_bar_ts=ts,
                )
                log.debug(
                    "%s: ENTER %s K=%d @%.2f sl_spot=%.0f tgt_spot=%.0f lots=%d",
                    trade_date, sig.direction, strike, entry_px,
                    sig.spot_sl, sig.spot_target, lots,
                )

    return trades, capital


def run_strategy(
    strategy: Strategy,
    start: date,
    end: date,
    initial_capital: float = 100_000.0,
) -> tuple[list[Trade], float]:
    """Run `strategy` over every trading day in [start, end].

    Fetches and caches 5-min OHLCV + daily VIX automatically.

    Returns
    -------
    (all_trades, final_capital)
    """
    log.info("run_strategy: %s  %s → %s", strategy.name, start, end)

    df_5m = load_5m_ohlcv(start, end)
    vix_series = load_daily_vix(start, end)

    all_trades: list[Trade] = []
    capital = initial_capital

    for day in trading_days(start, end):
        # Slice the day's 5-min bars
        day_start = pd.Timestamp(day, tz=IST)
        day_end = day_start + pd.Timedelta(hours=23, minutes=59)
        df_day = df_5m.loc[day_start:day_end]
        if df_day.empty:
            log.debug("%s: no data — skip", day)
            continue

        # VIX for this day (fall back to 15.0 if missing)
        vix_key = pd.Timestamp(day)
        vix = float(vix_series.get(vix_key, pd.Series([15.0])).iloc[0] if isinstance(vix_series.get(vix_key), pd.Series) else vix_series.get(vix_key, 15.0))
        if pd.isna(vix) or vix <= 0:
            vix = 15.0

        signals = strategy.generate_signals(df_day, day, vix)
        day_trades, capital = simulate_day(
            df_day, signals, day, vix, capital, strategy.name
        )
        all_trades.extend(day_trades)

        # Give gap-aware strategies access to today's closing price
        if not df_day.empty:
            strategy.prev_close = float(df_day["close"].iloc[-1])

    log.info("Done: %d trades, capital=%.0f", len(all_trades), capital)
    return all_trades, capital
