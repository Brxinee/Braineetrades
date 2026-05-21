"""
risk.py — Position sizing, risk calculation, and daily loss guard for
          the Brainee Trades backend (F&O focused).

Cost model (Zerodha-style, NSE F&O):
  Brokerage          : ₹20 flat per order (buy + sell = ₹40 round-trip)
  STT                : 0.05% on sell-side premium (options only)
  Exchange charge    : 0.053% of turnover (premium × contracts × lot_size)
  GST                : 18% on (brokerage + exchange charge)
  SEBI charges       : ₹10 per crore of turnover
  Stamp duty         : 0.003% on buy-side premium
  Slippage           : 0.5% of premium per leg

For equity futures/cash the cost model is omitted (out of scope here).
All monetary values in Indian Rupees (₹).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Cost constants
# ---------------------------------------------------------------------------

_BROKERAGE_PER_ORDER: float = 20.0          # ₹20 flat per order
_STT_RATE: float = 0.0005                   # 0.05% on sell-side premium
_EXCHANGE_CHARGE_RATE: float = 0.00053      # 0.053% of turnover
_GST_RATE: float = 0.18                     # 18% on brokerage + exchange
_SEBI_RATE: float = 10.0 / 1e7             # ₹10 per crore → per ₹1
_STAMP_DUTY_RATE: float = 0.000030          # 0.003% on buy-side
_SLIPPAGE_RATE: float = 0.005               # 0.5% of premium per leg


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RiskParams:
    """Input parameters for a single trade risk calculation."""
    capital: float        # total account capital (₹)
    risk_pct: float       # risk as percent of capital (e.g. 2.0 for 2%)
    entry: float          # option premium entry price (or equity price)
    sl: float             # stop-loss price (option premium level or spot level)
    target: float         # target price (option premium level or spot level)
    lot_size: int         # NSE lot size (e.g. 75 for NIFTY)
    premium: float        # option premium per unit (0.0 if equity)
    instrument: str       # "CE" | "PE" | "EQUITY"


@dataclass
class RiskResult:
    """Output of risk calculation for one trade."""
    contracts: int          # number of lots
    max_loss_rs: float      # maximum loss in ₹ (at stop-loss)
    max_loss_pct: float     # max loss as % of capital
    target_gain_rs: float   # target gain in ₹ (at target price)
    rr: float               # reward-to-risk ratio
    breakeven: float        # breakeven option premium price
    one_r: float            # 1R target (= entry + 1× risk)
    two_r: float            # 2R target (= entry + 2× risk)
    three_r: float          # 3R target (= entry + 3× risk)
    margin_required: float  # approximate SPAN + exposure margin (₹)
    cost_rs: float          # total transaction cost for round-trip (₹)
    costs_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "contracts": self.contracts,
            "max_loss_rs": round(self.max_loss_rs, 2),
            "max_loss_pct": round(self.max_loss_pct, 4),
            "target_gain_rs": round(self.target_gain_rs, 2),
            "rr": round(self.rr, 2),
            "breakeven": round(self.breakeven, 2),
            "one_r": round(self.one_r, 2),
            "two_r": round(self.two_r, 2),
            "three_r": round(self.three_r, 2),
            "margin_required": round(self.margin_required, 2),
            "cost_rs": round(self.cost_rs, 2),
            "costs_breakdown": {k: round(v, 2) for k, v in self.costs_breakdown.items()},
        }


# ---------------------------------------------------------------------------
# RiskEngine
# ---------------------------------------------------------------------------

class RiskEngine:
    """
    Risk and position sizing calculator for NSE F&O options trades.

    Usage
    -----
    engine = RiskEngine()

    params = RiskParams(
        capital=500_000,
        risk_pct=2.0,
        entry=150.0,
        sl=100.0,
        target=250.0,
        lot_size=75,
        premium=150.0,
        instrument="CE",
    )
    result = engine.calculate(params)
    print(result.to_dict())
    """

    # ------------------------------------------------------------------
    # Transaction costs
    # ------------------------------------------------------------------

    def _transaction_costs(
        self,
        premium: float,
        contracts: int,
        lot_size: int,
        instrument: str,
    ) -> dict[str, float]:
        """
        Calculate all-in transaction costs for a round-trip options trade.

        Assumptions:
          - Options: cost is on premium (not notional lot value).
          - "Buy" leg: brokerage + stamp duty + exchange + SEBI + GST.
          - "Sell" leg: brokerage + STT + exchange + SEBI + GST.
          - Slippage applied on both legs.

        Parameters
        ----------
        premium     : option premium per unit (₹)
        contracts   : number of lots
        lot_size    : units per lot
        instrument  : "CE" | "PE" | "EQUITY"

        Returns
        -------
        dict with individual cost components + 'total'.
        """
        units = contracts * lot_size
        turnover_buy = premium * units
        turnover_sell = premium * units  # simplified: same premium for costs basis

        # Brokerage (flat ₹20 per order, two orders = buy + sell)
        brokerage_buy = _BROKERAGE_PER_ORDER
        brokerage_sell = _BROKERAGE_PER_ORDER

        # STT: only on sell side for options (0.05% of sell premium)
        stt = turnover_sell * _STT_RATE if instrument.upper() in ("CE", "PE") else 0.0

        # Exchange transaction charges: both legs
        exchange_buy = turnover_buy * _EXCHANGE_CHARGE_RATE
        exchange_sell = turnover_sell * _EXCHANGE_CHARGE_RATE

        # GST: 18% on (brokerage + exchange charges) for each leg
        gst_buy = (brokerage_buy + exchange_buy) * _GST_RATE
        gst_sell = (brokerage_sell + exchange_sell) * _GST_RATE

        # SEBI charges: ₹10 per crore on both sides
        sebi = (turnover_buy + turnover_sell) * _SEBI_RATE

        # Stamp duty: only on buy side
        stamp = turnover_buy * _STAMP_DUTY_RATE

        # Slippage: 0.5% of premium per leg
        slippage_buy = turnover_buy * _SLIPPAGE_RATE
        slippage_sell = turnover_sell * _SLIPPAGE_RATE

        total = (
            brokerage_buy + brokerage_sell
            + stt
            + exchange_buy + exchange_sell
            + gst_buy + gst_sell
            + sebi
            + stamp
            + slippage_buy + slippage_sell
        )

        return {
            "brokerage": brokerage_buy + brokerage_sell,
            "stt": stt,
            "exchange": exchange_buy + exchange_sell,
            "gst": gst_buy + gst_sell,
            "sebi": sebi,
            "stamp_duty": stamp,
            "slippage": slippage_buy + slippage_sell,
            "total": total,
        }

    # ------------------------------------------------------------------
    # Public: calculate
    # ------------------------------------------------------------------

    def calculate(self, params: RiskParams) -> RiskResult:
        """
        Compute position size and risk metrics for one trade.

        Position sizing logic:
          max_risk_rs = capital × risk_pct / 100
          For options: risk per lot = (entry_premium - sl_premium) × lot_size
          contracts = floor(max_risk_rs / risk_per_lot), minimum 1.

        If sl >= entry (invalid), falls back to 1% price stop.

        Parameters
        ----------
        params : RiskParams

        Returns
        -------
        RiskResult
        """
        capital = params.capital
        risk_pct = params.risk_pct
        entry = params.entry
        sl = params.sl
        target = params.target
        lot_size = params.lot_size
        premium = params.premium if params.premium > 0 else entry
        instrument = params.instrument

        max_risk_rs = capital * (risk_pct / 100.0)

        # Risk per unit
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0.0:
            # Fallback: assume 1% of entry price as risk
            risk_per_unit = max(entry * 0.01, 0.01)

        # Risk per lot
        risk_per_lot = risk_per_unit * lot_size
        if risk_per_lot <= 0.0:
            risk_per_lot = max_risk_rs  # worst case: 1 lot

        # Number of contracts
        contracts = max(1, int(max_risk_rs / risk_per_lot))

        # Total costs
        costs = self._transaction_costs(premium, contracts, lot_size, instrument)
        cost_total = costs["total"]

        # P&L calculations
        units = contracts * lot_size
        max_loss_rs = risk_per_lot * contracts + cost_total
        reward_per_unit = abs(target - entry)
        reward_per_lot = reward_per_unit * lot_size
        target_gain_rs = reward_per_lot * contracts - cost_total

        max_loss_pct = (max_loss_rs / capital) * 100.0 if capital > 0 else 0.0
        rr = reward_per_unit / risk_per_unit if risk_per_unit > 0 else 0.0

        # R-multiples (from entry in the direction of trade)
        direction_mult = 1.0 if target > entry else -1.0
        one_r = entry + direction_mult * risk_per_unit
        two_r = entry + direction_mult * 2.0 * risk_per_unit
        three_r = entry + direction_mult * 3.0 * risk_per_unit

        # Breakeven: entry + costs spread per unit
        cost_per_unit = cost_total / units if units > 0 else 0.0
        breakeven = entry + direction_mult * cost_per_unit

        # Margin approximation for options: full premium debit (buyer pays upfront)
        # No SPAN margin required for option buyers.
        margin_required = premium * units

        return RiskResult(
            contracts=contracts,
            max_loss_rs=round(max_loss_rs, 2),
            max_loss_pct=round(max_loss_pct, 4),
            target_gain_rs=round(target_gain_rs, 2),
            rr=round(rr, 2),
            breakeven=round(breakeven, 2),
            one_r=round(one_r, 2),
            two_r=round(two_r, 2),
            three_r=round(three_r, 2),
            margin_required=round(margin_required, 2),
            cost_rs=round(cost_total, 2),
            costs_breakdown=costs,
        )

    # ------------------------------------------------------------------
    # Public: size_from_premium
    # ------------------------------------------------------------------

    def size_from_premium(
        self,
        capital: float,
        risk_pct: float,
        premium: float,
        lot_size: int,
    ) -> int:
        """
        Maximum contracts where total premium cost ≤ capital × risk_pct/100.

        This is the correct sizing method for option buyers: the full premium
        paid IS the maximum loss.

        Parameters
        ----------
        capital   : account capital (₹)
        risk_pct  : percentage of capital willing to risk (e.g. 2.0)
        premium   : option premium per unit (₹)
        lot_size  : lot size (e.g. 75)

        Returns
        -------
        contracts : int (minimum 1)
        """
        if premium <= 0 or lot_size <= 0:
            logger.warning("size_from_premium: invalid premium or lot_size")
            return 1

        max_outlay = capital * (risk_pct / 100.0)
        cost_per_lot = premium * lot_size

        contracts = max(1, int(max_outlay / cost_per_lot))
        logger.debug(
            "size_from_premium: capital=%.0f risk_pct=%.1f premium=%.2f "
            "lot_size=%d → %d contracts (outlay ₹%.0f)",
            capital, risk_pct, premium, lot_size,
            contracts, cost_per_lot * contracts,
        )
        return contracts

    # ------------------------------------------------------------------
    # Public: daily_loss_check
    # ------------------------------------------------------------------

    def daily_loss_check(
        self,
        journal_trades: list[dict],
        capital: float,
        limit_pct: float,
    ) -> dict:
        """
        Check if today's realised losses exceed the daily loss limit.

        Intended for use as a circuit-breaker: if today_pnl < -limit_rs,
        the system should pause trading for the rest of the session.

        Parameters
        ----------
        journal_trades : list of trade dicts, each with at minimum:
            - "exit_time" : ISO-8601 string or date-parseable timestamp
            - "net_pnl"   : realised net P&L for that trade (negative = loss)
          (Other keys are ignored.)
        capital        : starting capital for the day / total account (₹)
        limit_pct      : daily loss limit as percent of capital (e.g. 2.0 for 2%)

        Returns
        -------
        dict:
            limit_breached : bool
            today_pnl      : float — net P&L for today (negative = loss)
            limit_rs       : float — loss limit in ₹ (negative number)
            trades_today   : int   — number of trades counted today
            headroom_rs    : float — remaining room before limit (0 if breached)
        """
        today = datetime.now(IST).date()
        today_pnl = 0.0
        trades_today = 0

        for trade in journal_trades:
            # Parse exit_time
            try:
                raw_time = trade.get("exit_time") or trade.get("date")
                if raw_time is None:
                    continue

                if isinstance(raw_time, (date, datetime)):
                    trade_date = raw_time if isinstance(raw_time, date) else raw_time.date()
                else:
                    # String — try ISO-8601 parse
                    import datetime as _dt
                    parsed = _dt.datetime.fromisoformat(str(raw_time))
                    trade_date = parsed.date()

                if trade_date != today:
                    continue

            except Exception as exc:
                logger.debug("daily_loss_check: could not parse exit_time '%s' — %s", raw_time, exc)
                continue

            pnl_val = trade.get("net_pnl") or trade.get("pnl") or 0.0
            try:
                today_pnl += float(pnl_val)
                trades_today += 1
            except (TypeError, ValueError):
                pass

        limit_rs = -(capital * limit_pct / 100.0)  # negative number
        limit_breached = today_pnl < limit_rs
        headroom_rs = max(0.0, today_pnl - limit_rs) if not limit_breached else 0.0

        logger.info(
            "daily_loss_check: today_pnl=₹%.2f limit=₹%.2f breached=%s trades=%d",
            today_pnl, limit_rs, limit_breached, trades_today,
        )

        return {
            "limit_breached": limit_breached,
            "today_pnl": round(today_pnl, 2),
            "limit_rs": round(limit_rs, 2),
            "trades_today": trades_today,
            "headroom_rs": round(headroom_rs, 2),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

risk_engine = RiskEngine()
