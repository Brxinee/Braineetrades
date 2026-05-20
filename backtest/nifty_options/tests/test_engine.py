"""End-to-end tests for engine/simulator.py and strategies/orb15.py.

All tests use fully synthetic 5-min OHLCV — no network calls.

Test date: 2024-01-08 (Monday)
  next_expiry → 2024-01-11 (Thursday), DTE = 3
  lot_size    → 50 (Apr-2021 to Nov-2024 period)

Synthetic ORB setup:
  Bar 1  09:15  close=21500  (ORB bar)
  Bar 2  09:20  close=21530  H=21550  (ORB bar, ORB high = 21550)
  Bar 3  09:25  close=21480  L=21450  (ORB bar, ORB low  = 21450)
  ─── range complete: high=21550, low=21450, width=100 ───
  Bar 4  09:30  close=21560  → CE signal (close > 21550)
                               spot_sl=21450, spot_target=21660 (21560+100)
  Bar 5  09:35  close=21670  H=21700  → target hit  (H > 21660)
  Bar 6  09:40  ...
"""

from datetime import date

import pandas as pd
import pytest
import pytz

from backtest.nifty_options.calendar import next_expiry, lot_size
from backtest.nifty_options.engine.simulator import simulate_day, _option_px
from backtest.nifty_options.engine.trade import BROKERAGE_RT, SLIP_PER_LEG
from backtest.nifty_options.strategies.base import Signal
from backtest.nifty_options.strategies.orb15 import ORB15Strategy

IST = pytz.timezone("Asia/Kolkata")
TRADE_DATE = date(2024, 1, 8)
VIX = 15.0
CAPITAL = 100_000.0


# ── synthetic data helpers ────────────────────────────────────────────────────

def _ts(hour: int, minute: int) -> pd.Timestamp:
    return pd.Timestamp(TRADE_DATE, tz=IST) + pd.Timedelta(hours=hour, minutes=minute)


def _bar(hour: int, minute: int, o, h, lo, c, vol=200_000) -> dict:
    return {"open": o, "high": h, "low": lo, "close": c, "volume": vol,
            "_ts": _ts(hour, minute)}


def _make_df(bars: list[dict]) -> pd.DataFrame:
    rows = [{k: v for k, v in b.items() if k != "_ts"} for b in bars]
    idx = pd.DatetimeIndex([b["_ts"] for b in bars], name="datetime")
    return pd.DataFrame(rows, index=idx)


def _base_day_bars(override: dict[int, dict] | None = None) -> pd.DataFrame:
    """75-bar flat day at 21500; individual bars overridden by minute offset."""
    bars = []
    base = 21500.0
    for i in range(75):
        h, m = divmod(9 * 60 + 15 + 5 * i, 60)
        b = {"open": base, "high": base + 10, "low": base - 10,
             "close": base, "volume": 200_000, "_ts": _ts(h, m)}
        if override and i in override:
            b.update(override[i])
        bars.append(b)
    return _make_df(bars)


# ── ORB setup bars ────────────────────────────────────────────────────────────

def _orb_setup_bars(extra: list[dict]) -> pd.DataFrame:
    """3 ORB bars (range 21450–21550, width 100) + caller-supplied extra bars."""
    base_bars = [
        _bar(9, 15, 21490, 21520, 21480, 21500),   # bar 0
        _bar(9, 20, 21500, 21550, 21490, 21530),   # bar 1  ← ORB high=21550
        _bar(9, 25, 21530, 21540, 21450, 21480),   # bar 2  ← ORB low=21450
    ]
    return _make_df(base_bars + extra)


# ─────────────────────────────────────────────────────────────────────────────
# calendar sanity (used by engine)
# ─────────────────────────────────────────────────────────────────────────────

def test_trade_date_fixtures():
    assert next_expiry(TRADE_DATE) == date(2024, 1, 11)
    assert lot_size(TRADE_DATE) == 50


# ─────────────────────────────────────────────────────────────────────────────
# ORB-15 signal generation
# ─────────────────────────────────────────────────────────────────────────────

class TestORB15Signals:

    def _df_up(self) -> pd.DataFrame:
        """CE scenario: bar 4 closes above ORB high."""
        return _orb_setup_bars([
            _bar(9, 30, 21480, 21570, 21470, 21560),  # close > 21550 → CE
            _bar(9, 35, 21560, 21700, 21550, 21680),
        ])

    def _df_down(self) -> pd.DataFrame:
        """PE scenario: bar 4 closes below ORB low."""
        return _orb_setup_bars([
            _bar(9, 30, 21490, 21500, 21440, 21430),  # close < 21450 → PE
            _bar(9, 35, 21430, 21440, 21350, 21360),
        ])

    def _df_no_break(self) -> pd.DataFrame:
        """No breakout: all bars close inside ORB range."""
        return _orb_setup_bars([
            _bar(9, 30, 21480, 21540, 21460, 21500),  # inside range
            _bar(9, 35, 21500, 21540, 21460, 21510),  # inside range
        ])

    def test_ce_signal_fires(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_up(), TRADE_DATE, VIX)
        assert len(sigs) == 1
        assert sigs[0].direction == "CE"

    def test_ce_signal_time(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_up(), TRADE_DATE, VIX)
        assert sigs[0].time == _ts(9, 30)

    def test_ce_spot_sl_is_orb_low(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_up(), TRADE_DATE, VIX)
        assert sigs[0].spot_sl == pytest.approx(21450.0)

    def test_ce_spot_target_is_entry_plus_width(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_up(), TRADE_DATE, VIX)
        # close=21560, orb_width=100 → target=21660
        assert sigs[0].spot_target == pytest.approx(21660.0)

    def test_pe_signal_fires(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_down(), TRADE_DATE, VIX)
        assert len(sigs) == 1
        assert sigs[0].direction == "PE"

    def test_pe_spot_sl_is_orb_high(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_down(), TRADE_DATE, VIX)
        assert sigs[0].spot_sl == pytest.approx(21550.0)

    def test_pe_spot_target_below_entry(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_down(), TRADE_DATE, VIX)
        assert sigs[0].spot_target < sigs[0].spot_sl

    def test_no_signal_when_no_breakout(self):
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(self._df_no_break(), TRADE_DATE, VIX)
        assert sigs == []

    def test_only_one_signal_per_day(self):
        """Even if two bars break the ORB, only the first fires."""
        df = _orb_setup_bars([
            _bar(9, 30, 21480, 21570, 21470, 21560),  # CE signal
            _bar(9, 35, 21560, 21590, 21440, 21430),  # CE already emitted
        ])
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(df, TRADE_DATE, VIX)
        assert len(sigs) == 1

    def test_not_enough_bars_returns_empty(self):
        df = _orb_setup_bars([])  # only 3 bars, no post-ORB bars
        strategy = ORB15Strategy()
        sigs = strategy.generate_signals(df, TRADE_DATE, VIX)
        assert sigs == []


# ─────────────────────────────────────────────────────────────────────────────
# Simulator — simulate_day
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulateDay:

    def _target_hit_df(self) -> pd.DataFrame:
        """CE trade, target hit on bar 5 (H=21700 > target=21660)."""
        return _orb_setup_bars([
            _bar(9, 30, 21480, 21570, 21470, 21560),  # CE signal bar
            _bar(9, 35, 21560, 21700, 21555, 21680),  # H > 21660 → target
            _bar(9, 40, 21680, 21690, 21650, 21670),
        ])

    def _sl_hit_df(self) -> pd.DataFrame:
        """CE trade, SL hit on bar 5 (L=21430 < sl=21450)."""
        return _orb_setup_bars([
            _bar(9, 30, 21480, 21570, 21470, 21560),  # CE signal bar
            _bar(9, 35, 21560, 21565, 21430, 21440),  # L < 21450 → SL
            _bar(9, 40, 21440, 21450, 21420, 21430),
        ])

    def _forced_exit_df(self) -> pd.DataFrame:
        """CE trade, neither SL nor target hit; forced exit at 15:15."""
        bars_after_orb: list[dict] = []
        # Bar at 9:30 — CE signal
        bars_after_orb.append(_bar(9, 30, 21480, 21570, 21470, 21560))
        # Bars 9:35 → 15:20: spot stays in range, never hits SL (21450) or target (21660)
        t_min = 9 * 60 + 35
        while t_min <= 15 * 60 + 20:
            h, m = divmod(t_min, 60)
            bars_after_orb.append(_bar(h, m, 21550, 21600, 21500, 21560))
            t_min += 5
        return _orb_setup_bars(bars_after_orb)

    def _signals_ce(self, df: pd.DataFrame) -> list[Signal]:
        return ORB15Strategy().generate_signals(df, TRADE_DATE, VIX)

    # ── target hit ──────────────────────────────────────────────────────────

    def test_target_trade_recorded(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert len(trades) == 1

    def test_target_exit_reason(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].exit_reason == "target"

    def test_target_trade_is_ce(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].direction == "CE"

    def test_target_trade_profitable(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].pnl > 0

    def test_target_strike_is_atm(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        # spot_entry = 21560, ATM = round(21560/50)*50 = 21550
        assert trades[0].strike == 21550

    def test_target_capital_increases(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        _, new_cap = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert new_cap > CAPITAL

    def test_target_entry_time(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].entry_time == _ts(9, 30)

    def test_target_exit_time(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].exit_time == _ts(9, 35)

    # ── SL hit ──────────────────────────────────────────────────────────────

    def test_sl_trade_recorded(self):
        df = self._sl_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert len(trades) == 1

    def test_sl_exit_reason(self):
        df = self._sl_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].exit_reason == "sl"

    def test_sl_trade_is_loss(self):
        df = self._sl_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].pnl < 0

    def test_sl_capital_decreases(self):
        df = self._sl_hit_df()
        sigs = self._signals_ce(df)
        _, new_cap = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert new_cap < CAPITAL

    def test_sl_loss_less_than_full_premium(self):
        """SL exit loss must be < full premium paid (can't lose more than you paid)."""
        df = self._sl_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        t = trades[0]
        # Exit at SL level means option still has value → loss < full entry premium
        max_possible = (t.entry_px + SLIP_PER_LEG) * t.units + BROKERAGE_RT
        assert abs(t.pnl) < max_possible

    # ── forced exit ──────────────────────────────────────────────────────────

    def test_forced_exit_reason(self):
        df = self._forced_exit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert len(trades) == 1
        assert trades[0].exit_reason == "forced"

    def test_forced_exit_time_at_or_after_1515(self):
        df = self._forced_exit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        exit_ts = trades[0].exit_time
        assert exit_ts.hour == 15 and exit_ts.minute >= 15

    # ── no signal / edge cases ───────────────────────────────────────────────

    def test_no_signals_no_trades(self):
        df = _base_day_bars()
        trades, cap = simulate_day(df, [], TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades == []
        assert cap == CAPITAL

    def test_signal_before_entry_window_ignored(self):
        """A signal timestamped before 9:20 must not trigger an entry."""
        sig = Signal(
            time=_ts(9, 15),   # before entry window open (9:20)
            direction="CE",
            spot_sl=21450.0,
            spot_target=21660.0,
        )
        df = _base_day_bars()
        trades, _ = simulate_day(df, [sig], TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades == []

    def test_signal_after_entry_window_ignored(self):
        """A signal timestamped after 14:30 must not trigger an entry."""
        sig = Signal(
            time=_ts(14, 35),
            direction="CE",
            spot_sl=21450.0,
            spot_target=21660.0,
        )
        df = _base_day_bars()
        trades, _ = simulate_day(df, [sig], TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades == []

    # ── trade record completeness ────────────────────────────────────────────

    def test_trade_dict_has_all_keys(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        d = trades[0].to_dict()
        for key in ["date", "strategy", "direction", "strike", "expiry",
                    "entry_time", "entry_px", "exit_time", "exit_px",
                    "exit_reason", "lots", "lot_size", "units", "pnl",
                    "running_capital"]:
            assert key in d, f"missing key: {key}"

    def test_trade_expiry_matches_calendar(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].expiry == next_expiry(TRADE_DATE)

    def test_trade_lot_size_correct(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].lot_size == lot_size(TRADE_DATE)  # 50

    def test_running_capital_equals_capital_plus_pnl(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, new_cap = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        assert trades[0].running_capital == pytest.approx(CAPITAL + trades[0].pnl)
        assert new_cap == pytest.approx(trades[0].running_capital)

    def test_gross_pnl_before_costs(self):
        df = self._target_hit_df()
        sigs = self._signals_ce(df)
        trades, _ = simulate_day(df, sigs, TRADE_DATE, VIX, CAPITAL, "ORB-15")
        t = trades[0]
        # gross = (exit_px - entry_px) * units (mid-to-mid, no slippage)
        assert t.gross_pnl == pytest.approx(
            (t.exit_px - t.entry_px) * t.units, abs=0.01
        )
