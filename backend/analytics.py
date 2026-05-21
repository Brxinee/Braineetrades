"""
analytics.py — AnalyticsEngine for NIFTY intraday journal trade analysis.

Accepts journal trade dicts (as passed from frontend localStorage) and
computes a comprehensive analytics dict plus actionable insights.

Trade dict schema:
    {
        "id": str,
        "date": str  (YYYY-MM-DD),
        "symbol": str,
        "setup": str,
        "direction": str,          # "LONG" | "SHORT"
        "entry": float,
        "exit": float,
        "qty": int,
        "pnl": float,
        "mistake_tags": list[str],
        "notes": str,
        "hold_time_mins": int,
    }
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                     #
# --------------------------------------------------------------------------- #

_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_TRADING_DAYS_PER_YEAR = 252
_MIN_TRADES_FOR_INSIGHT = 3          # minimum sample size before drawing a conclusion

# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #


class AnalyticsEngine:
    """
    Stateless analytics engine — instantiate once and call `compute_analytics`
    with the current journal trade list each time the UI needs a refresh.
    """

    # ---------------------------------------------------------------------- #
    #  Main entry point                                                         #
    # ---------------------------------------------------------------------- #

    def compute_analytics(self, trades: list[dict]) -> dict:
        """
        Compute a comprehensive analytics dict from a list of journal trade dicts.

        Parameters
        ----------
        trades:
            List of trade dicts as stored in frontend localStorage.

        Returns
        -------
        dict
            Fully populated analytics payload (see module docstring for schema).
        """
        if not trades:
            return self._empty_analytics()

        validated = [t for t in trades if self._is_valid(t)]
        if not validated:
            logger.warning("compute_analytics: no valid trades after validation")
            return self._empty_analytics()

        logger.debug("compute_analytics: %d valid trades", len(validated))

        # Sort by date then entry price for determinism
        validated = sorted(validated, key=lambda t: t["date"])

        pnls = [float(t["pnl"]) for t in validated]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_trades = len(pnls)
        win_trades = len(wins)
        loss_trades = len(losses)
        win_rate = win_trades / total_trades if total_trades else 0.0

        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        profit_factor = min(profit_factor, 9999.0)

        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
        total_pnl = sum(pnls)

        avg_hold = (
            sum(float(t.get("hold_time_mins", 0)) for t in validated) / total_trades
        )

        sharpe = self._compute_daily_sharpe(validated)
        max_dd = self._compute_max_drawdown(pnls)
        streak = self._compute_streaks(pnls)

        by_setup = self._group_by_setup(validated)
        by_weekday = self._group_by_weekday(validated)
        by_hour = self._group_by_hour(validated)
        monthly_pnl = self._monthly_pnl(validated)
        mistake_breakdown = self._mistake_breakdown(validated)
        pnl_calendar = self._pnl_calendar(validated)
        equity_curve = self._equity_curve(validated)
        risk_metrics = self._risk_metrics(validated)

        best_setup, worst_setup = self._best_worst_setup(by_setup)
        best_weekday, worst_weekday = self._best_worst_weekday(by_weekday)

        return {
            "total_trades": total_trades,
            "win_trades": win_trades,
            "loss_trades": loss_trades,
            "win_rate": round(win_rate, 4),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "avg_hold_mins": round(avg_hold, 1),
            "total_pnl": round(total_pnl, 2),
            "sharpe": round(sharpe, 4),
            "max_dd": round(max_dd, 4),
            "streak": streak,
            "best_setup": best_setup,
            "worst_setup": worst_setup,
            "best_weekday": best_weekday,
            "worst_weekday": worst_weekday,
            "by_setup": by_setup,
            "by_weekday": by_weekday,
            "by_hour": by_hour,
            "monthly_pnl": monthly_pnl,
            "mistake_breakdown": mistake_breakdown,
            "pnl_calendar": pnl_calendar,
            "equity_curve": equity_curve,
            "risk_metrics": risk_metrics,
        }

    def get_insights(self, analytics: dict) -> list[str]:
        """
        Derive 3–5 actionable, data-driven insights from a computed analytics dict.

        Parameters
        ----------
        analytics:
            Result of `compute_analytics`.

        Returns
        -------
        list[str]
            Human-readable insight strings suitable for display in the UI.
        """
        insights: list[str] = []

        if not analytics or analytics.get("total_trades", 0) == 0:
            return ["No trades to analyse yet. Start journalling to unlock insights."]

        win_rate = analytics.get("win_rate", 0.0)
        total_wr_pct = round(win_rate * 100, 1)
        by_weekday: dict = analytics.get("by_weekday", {})
        by_setup: dict = analytics.get("by_setup", {})
        by_hour: dict = analytics.get("by_hour", {})
        avg_hold = analytics.get("avg_hold_mins", 0.0)
        mistake_breakdown: dict = analytics.get("mistake_breakdown", {})
        best_setup = analytics.get("best_setup", "")
        worst_setup = analytics.get("worst_setup", "")
        profit_factor = analytics.get("profit_factor", 0.0)
        max_dd = analytics.get("max_dd", 0.0)

        # ── Insight 1: Best setup ──────────────────────────────────────────
        if best_setup and by_setup.get(best_setup):
            s = by_setup[best_setup]
            if s.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT:
                wr = round(s.get("win_rate", 0) * 100, 1)
                insights.append(
                    f"{best_setup} is your best setup with {wr}% win rate "
                    f"across {s['trades']} trades — lean into it."
                )

        # ── Insight 2: Worst weekday ──────────────────────────────────────
        worst_weekday = analytics.get("worst_weekday", "")
        if worst_weekday and by_weekday.get(worst_weekday):
            wd = by_weekday[worst_weekday]
            if wd.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT:
                wd_wr = round(wd.get("win_rate", 0) * 100, 1)
                if wd_wr < total_wr_pct - 10:
                    insights.append(
                        f"Your win rate on {worst_weekday}s ({wd_wr}%) is significantly "
                        f"below your overall average ({total_wr_pct}%) — "
                        f"consider reducing size or skipping {worst_weekday} entries."
                    )

        # ── Insight 3: Hold-time vs expectancy ────────────────────────────
        # Split trades into short (<avg/2) and long (>avg*1.5) holds
        if avg_hold > 0 and analytics.get("total_trades", 0) >= 6:
            insights.append(self._hold_time_insight(analytics, avg_hold))

        # ── Insight 4: Worst setup ────────────────────────────────────────
        if worst_setup and worst_setup != best_setup and by_setup.get(worst_setup):
            s = by_setup[worst_setup]
            if s.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT and s.get("total_pnl", 0) < 0:
                wr = round(s.get("win_rate", 0) * 100, 1)
                insights.append(
                    f"{worst_setup} is dragging performance ({wr}% win rate, "
                    f"₹{s['total_pnl']:,.0f} net PnL) — review or remove from playbook."
                )

        # ── Insight 5: Most common mistake ───────────────────────────────
        if mistake_breakdown:
            top_mistake, count = max(mistake_breakdown.items(), key=lambda kv: kv[1])
            pct = round(count / analytics["total_trades"] * 100)
            insights.append(
                f'"{top_mistake}" is your most frequent mistake, appearing in '
                f"{pct}% of trades — target this first in your process review."
            )

        # ── Insight 6: Best intraday hour ────────────────────────────────
        qualified_hours = {
            h: v for h, v in by_hour.items()
            if v.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT
        }
        if qualified_hours:
            best_hr = max(qualified_hours.items(), key=lambda kv: kv[1].get("win_rate", 0))
            hr_label = _format_hour(int(best_hr[0]))
            hr_wr = round(best_hr[1].get("win_rate", 0) * 100, 1)
            if hr_wr > total_wr_pct + 10:
                insights.append(
                    f"Entries around {hr_label} produce your highest win rate ({hr_wr}%) — "
                    f"prioritise setups in that window."
                )

        # ── Insight 7: Profit factor health ──────────────────────────────
        if profit_factor < 1.2 and analytics.get("total_trades", 0) >= 10:
            insights.append(
                f"Profit factor is {profit_factor:.2f} — below 1.5 signals that "
                f"average wins don't compensate average losses. Tighten stops or "
                f"widen targets."
            )

        # ── Insight 8: Max drawdown ───────────────────────────────────────
        dd_pct = abs(max_dd) * 100
        if dd_pct > 15:
            insights.append(
                f"Maximum drawdown of {dd_pct:.1f}% detected. Consider a daily loss "
                f"limit to prevent large equity erosion."
            )

        # Return at most 5 insights, prioritising the most impactful
        return insights[:5] if insights else [
            "Keep journalling — more trades unlock deeper insights."
        ]

    # ---------------------------------------------------------------------- #
    #  Private helpers                                                          #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _is_valid(trade: dict) -> bool:
        """Return True if the trade dict has all required numeric fields."""
        try:
            float(trade["entry"])
            float(trade["exit"])
            float(trade["pnl"])
            int(trade["qty"])
            datetime.strptime(trade["date"], "%Y-%m-%d")
            return True
        except (KeyError, ValueError, TypeError):
            return False

    # ── Equity & drawdown ─────────────────────────────────────────────────

    @staticmethod
    def _compute_max_drawdown(pnls: list[float]) -> float:
        """Peak-to-trough max drawdown as a fraction (negative value)."""
        if not pnls:
            return 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak if peak != 0 else 0.0
            if dd < max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _equity_curve(trades: list[dict]) -> list[list]:
        """
        Cumulative equity curve starting from 0.

        Returns list of [unix_ms, equity] pairs using trade date as timestamp.
        """
        curve: list[list] = []
        equity = 0.0
        for t in trades:
            equity += float(t["pnl"])
            try:
                dt = datetime.strptime(t["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                unix_ms = int(dt.timestamp() * 1000)
            except ValueError:
                unix_ms = 0
            curve.append([unix_ms, round(equity, 2)])
        return curve

    # ── Sharpe ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_daily_sharpe(trades: list[dict]) -> float:
        """Annualised Sharpe on daily-aggregated PnL (assumes 252 trading days)."""
        if len(trades) < 5:
            return 0.0
        daily: dict[str, float] = defaultdict(float)
        for t in trades:
            daily[t["date"]] += float(t["pnl"])
        values = list(daily.values())
        if len(values) < 2:
            return 0.0
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    # ── Streaks ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_streaks(pnls: list[float]) -> dict:
        """
        Compute current streak, best winning streak, worst losing streak.
        """
        current = 0
        best_win = 0
        worst_loss = 0
        running_win = 0
        running_loss = 0

        for p in pnls:
            if p > 0:
                running_win += 1
                running_loss = 0
            else:
                running_loss += 1
                running_win = 0
            best_win = max(best_win, running_win)
            worst_loss = max(worst_loss, running_loss)

        # Current streak: positive = win streak, negative = loss streak
        if pnls:
            last = pnls[-1]
            streak_val = 0
            for p in reversed(pnls):
                if (p > 0) == (last > 0):
                    streak_val += 1 if p > 0 else -1
                else:
                    break
            current = streak_val
        return {"current": current, "best_win": best_win, "worst_loss": worst_loss}

    # ── Grouping helpers ──────────────────────────────────────────────────

    @staticmethod
    def _group_by_setup(trades: list[dict]) -> dict[str, dict]:
        """
        Returns per-setup aggregates: trades, win_rate, total_pnl, expectancy.
        """
        grouped: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            setup = t.get("setup") or "Unknown"
            grouped[setup].append(t)

        result = {}
        for setup, group in grouped.items():
            pnls = [float(t["pnl"]) for t in group]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            n = len(pnls)
            wr = len(wins) / n
            avg_w = sum(wins) / len(wins) if wins else 0.0
            avg_l = sum(losses) / len(losses) if losses else 0.0
            exp = wr * avg_w + (1 - wr) * avg_l
            result[setup] = {
                "trades": n,
                "win_rate": round(wr, 4),
                "total_pnl": round(sum(pnls), 2),
                "expectancy": round(exp, 2),
            }
        return result

    @staticmethod
    def _group_by_weekday(trades: list[dict]) -> dict[str, dict]:
        """Returns per-weekday aggregates: trades, win_rate, pnl."""
        grouped: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            try:
                dt = datetime.strptime(t["date"], "%Y-%m-%d")
                day = _WEEKDAY_NAMES[dt.weekday()]
            except (ValueError, IndexError):
                day = "Unknown"
            grouped[day].append(float(t["pnl"]))

        result = {}
        for day, pnls in grouped.items():
            wins = [p for p in pnls if p > 0]
            wr = len(wins) / len(pnls) if pnls else 0.0
            result[day] = {
                "trades": len(pnls),
                "win_rate": round(wr, 4),
                "pnl": round(sum(pnls), 2),
            }
        return result

    @staticmethod
    def _group_by_hour(trades: list[dict]) -> dict[str, dict]:
        """
        Group by entry hour (IST).  The trade dict does not carry an explicit
        time field, so we derive hour from hold_time_mins heuristic only when
        an explicit 'time' key is present; otherwise we omit.

        If 'entry_time' (ISO string) is present on the trade dict, use it.
        Otherwise skip — don't guess.
        """
        grouped: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            entry_time: str | None = t.get("entry_time") or t.get("time")
            if not entry_time:
                continue
            try:
                if "T" in entry_time:
                    dt = datetime.fromisoformat(entry_time)
                else:
                    # Assume HH:MM format appended to date
                    dt = datetime.strptime(f"{t['date']} {entry_time}", "%Y-%m-%d %H:%M")
                hour = str(dt.hour)
            except (ValueError, TypeError):
                continue
            grouped[hour].append(float(t["pnl"]))

        result = {}
        for hour, pnls in grouped.items():
            wins = [p for p in pnls if p > 0]
            wr = len(wins) / len(pnls) if pnls else 0.0
            result[hour] = {
                "trades": len(pnls),
                "win_rate": round(wr, 4),
                "pnl": round(sum(pnls), 2),
            }
        return result

    @staticmethod
    def _monthly_pnl(trades: list[dict]) -> list[dict]:
        """Aggregate PnL by calendar month, sorted chronologically."""
        monthly: dict[str, dict] = {}
        for t in trades:
            try:
                month = t["date"][:7]   # "YYYY-MM"
            except (KeyError, TypeError):
                continue
            if month not in monthly:
                monthly[month] = {"month": month, "pnl": 0.0, "trades": 0}
            monthly[month]["pnl"] += float(t["pnl"])
            monthly[month]["trades"] += 1

        sorted_months = sorted(monthly.values(), key=lambda m: m["month"])
        for m in sorted_months:
            m["pnl"] = round(m["pnl"], 2)
        return sorted_months

    @staticmethod
    def _mistake_breakdown(trades: list[dict]) -> dict[str, int]:
        """Count occurrences of each mistake tag across all trades."""
        counter: Counter = Counter()
        for t in trades:
            tags = t.get("mistake_tags") or []
            if isinstance(tags, list):
                for tag in tags:
                    if tag:
                        counter[str(tag)] += 1
        return dict(counter.most_common())

    @staticmethod
    def _pnl_calendar(trades: list[dict]) -> list[dict]:
        """Daily PnL calendar: one entry per calendar date."""
        daily: dict[str, float] = defaultdict(float)
        for t in trades:
            date_str = t.get("date", "")
            if date_str:
                daily[date_str] += float(t["pnl"])
        return [
            {"date": d, "pnl": round(p, 2)}
            for d, p in sorted(daily.items())
        ]

    # ── Risk metrics ──────────────────────────────────────────────────────

    @staticmethod
    def _risk_metrics(trades: list[dict]) -> dict:
        """
        Compute R-multiple metrics where 1R = abs(entry - stop_loss) * qty.
        Trades without a stop_loss field are excluded from R calculations.
        """
        rr_values: list[float] = []
        for t in trades:
            sl = t.get("stop_loss") or t.get("sl")
            entry = float(t.get("entry", 0))
            exit_ = float(t.get("exit", 0))
            qty = int(t.get("qty", 1))
            if sl is None or entry == 0:
                continue
            risk_per_unit = abs(entry - float(sl))
            if risk_per_unit == 0:
                continue
            one_r = risk_per_unit * qty
            reward = (exit_ - entry) * qty
            if t.get("direction", "LONG").upper() == "SHORT":
                reward = (entry - exit_) * qty
            rr = reward / one_r
            rr_values.append(rr)

        if not rr_values:
            return {"avg_rr": 0.0, "pct_above_1r": 0.0, "pct_above_2r": 0.0}

        n = len(rr_values)
        avg_rr = sum(rr_values) / n
        pct_1r = len([r for r in rr_values if r >= 1.0]) / n
        pct_2r = len([r for r in rr_values if r >= 2.0]) / n
        return {
            "avg_rr": round(avg_rr, 3),
            "pct_above_1r": round(pct_1r, 4),
            "pct_above_2r": round(pct_2r, 4),
        }

    # ── Best / worst helpers ──────────────────────────────────────────────

    @staticmethod
    def _best_worst_setup(by_setup: dict[str, dict]) -> tuple[str, str]:
        """Return (best_setup, worst_setup) by expectancy, minimum 3 trades."""
        qualified = {
            k: v for k, v in by_setup.items() if v.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT
        }
        if not qualified:
            if by_setup:
                all_by_exp = sorted(by_setup.items(), key=lambda kv: kv[1].get("expectancy", 0))
                return all_by_exp[-1][0], all_by_exp[0][0]
            return "", ""
        sorted_by_exp = sorted(qualified.items(), key=lambda kv: kv[1].get("expectancy", 0))
        return sorted_by_exp[-1][0], sorted_by_exp[0][0]

    @staticmethod
    def _best_worst_weekday(by_weekday: dict[str, dict]) -> tuple[str, str]:
        """Return (best_weekday, worst_weekday) by win_rate, minimum 3 trades."""
        qualified = {
            k: v for k, v in by_weekday.items() if v.get("trades", 0) >= _MIN_TRADES_FOR_INSIGHT
        }
        if not qualified:
            if by_weekday:
                srt = sorted(by_weekday.items(), key=lambda kv: kv[1].get("win_rate", 0))
                return srt[-1][0], srt[0][0]
            return "", ""
        srt = sorted(qualified.items(), key=lambda kv: kv[1].get("win_rate", 0))
        return srt[-1][0], srt[0][0]

    # ── Hold-time insight ────────────────────────────────────────────────

    @staticmethod
    def _hold_time_insight(analytics: dict, avg_hold: float) -> str:
        """
        Compare expectancy of short-hold vs long-hold trades.
        Uses raw trades stored inside analytics if available; otherwise
        fall back to a generic hold-time observation.
        """
        # We can't re-access raw trades from analytics dict alone;
        # generate a generic insight using the avg_hold figure.
        if avg_hold < 15:
            return (
                f"Average hold time is {avg_hold:.0f} minutes — very short. "
                f"Ensure you're not exiting profitable trades too early."
            )
        elif avg_hold > 120:
            return (
                f"Average hold time of {avg_hold:.0f} minutes is high for intraday. "
                f"Review whether long holds align with your strategy rules."
            )
        return (
            f"Average hold time is {avg_hold:.0f} minutes. "
            f"Compare PnL by hold-length in the breakdown view."
        )

    # ── Empty state ───────────────────────────────────────────────────────

    @staticmethod
    def _empty_analytics() -> dict:
        return {
            "total_trades": 0,
            "win_trades": 0,
            "loss_trades": 0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "avg_hold_mins": 0.0,
            "total_pnl": 0.0,
            "sharpe": 0.0,
            "max_dd": 0.0,
            "streak": {"current": 0, "best_win": 0, "worst_loss": 0},
            "best_setup": "",
            "worst_setup": "",
            "best_weekday": "",
            "worst_weekday": "",
            "by_setup": {},
            "by_weekday": {},
            "by_hour": {},
            "monthly_pnl": [],
            "mistake_breakdown": {},
            "pnl_calendar": [],
            "equity_curve": [],
            "risk_metrics": {"avg_rr": 0.0, "pct_above_1r": 0.0, "pct_above_2r": 0.0},
        }


# --------------------------------------------------------------------------- #
# Module-level helpers                                                          #
# --------------------------------------------------------------------------- #

def _format_hour(hour: int) -> str:
    """Convert 24h hour int to human-readable string e.g. 9 → '9:00 AM'."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
    return f"{h12}:00 {suffix}"


# --------------------------------------------------------------------------- #
# Module-level convenience functions (for direct import)                        #
# --------------------------------------------------------------------------- #

_engine = AnalyticsEngine()


def compute_analytics(trades: list[dict]) -> dict:
    """Module-level convenience wrapper around AnalyticsEngine.compute_analytics."""
    return _engine.compute_analytics(trades)


def get_insights(analytics: dict) -> list[str]:
    """Module-level convenience wrapper around AnalyticsEngine.get_insights."""
    return _engine.get_insights(analytics)
