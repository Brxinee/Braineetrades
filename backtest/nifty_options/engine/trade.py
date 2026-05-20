"""Trade dataclass and cost constants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

BROKERAGE_RT: float = 40.0   # ₹ per round trip (all-in, platform + STT)
SLIP_PER_LEG: float = 1.0    # ₹ per lot per leg (bid-ask half-spread)


@dataclass
class Trade:
    """One completed intraday options trade."""

    date: date
    strategy: str
    direction: str            # "CE" or "PE"
    strike: int
    expiry: date
    entry_time: pd.Timestamp
    entry_px: float           # option premium per unit at entry (mid-price)
    exit_time: pd.Timestamp
    exit_px: float            # option premium per unit at exit (mid-price)
    exit_reason: str          # "target" | "sl" | "forced"
    lots: int
    lot_size: int
    pnl: float                # net P&L after brokerage + slippage
    running_capital: float    # portfolio value after this trade settles

    # ── derived ───────────────────────────────────────────────────────────────

    @property
    def units(self) -> int:
        return self.lots * self.lot_size

    @property
    def gross_pnl(self) -> float:
        """P&L before costs."""
        return (self.exit_px - self.entry_px) * self.units

    @property
    def total_costs(self) -> float:
        return self.gross_pnl - self.pnl

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "strategy": self.strategy,
            "direction": self.direction,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "entry_time": self.entry_time.isoformat(),
            "entry_px": round(self.entry_px, 2),
            "exit_time": self.exit_time.isoformat(),
            "exit_px": round(self.exit_px, 2),
            "exit_reason": self.exit_reason,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "units": self.units,
            "gross_pnl": round(self.gross_pnl, 2),
            "pnl": round(self.pnl, 2),
            "running_capital": round(self.running_capital, 2),
        }
