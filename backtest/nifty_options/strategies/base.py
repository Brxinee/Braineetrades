"""Abstract Strategy base class and Signal dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class Signal:
    """A trade signal for one 5-min bar close.

    The simulator acts on signals in chronological order, entering a position
    only when no trade is currently open.
    """

    time: pd.Timestamp    # bar-close timestamp at which the signal fires
    direction: str        # "CE" (bullish — buy call) or "PE" (bearish — buy put)
    spot_sl: float        # NIFTY spot level that triggers stop-loss if breached
    spot_target: float    # NIFTY spot level that triggers profit-take if reached
    tag: str = field(default="")   # optional debug label


class Strategy(ABC):
    """Abstract base for all NIFTY weekly-options intraday strategies.

    Subclasses must set a unique `name` string and implement
    `generate_signals()`.  The simulator calls this once per trading day
    with the full day's 5-min OHLCV before any bar is 'played back'.
    """

    name: str = "unnamed"

    @abstractmethod
    def generate_signals(
        self,
        df_5m: pd.DataFrame,
        trade_date: date,
        vix: float,
    ) -> list[Signal]:
        """Return trade signals for `trade_date`.

        Parameters
        ----------
        df_5m      : 5-min OHLCV for the session, IST-aware DatetimeIndex,
                     columns [open, high, low, close, volume]
        trade_date : calendar date being simulated
        vix        : India VIX daily close (percent, e.g. 18.0)

        Returns
        -------
        List of Signal objects in chronological order.  May be empty (no trade
        opportunity today).  The simulator enforces the 9:20–14:30 entry
        window and the one-position-at-a-time rule independently.
        """
        ...
