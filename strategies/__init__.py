from .base import Strategy, SignalResult
from .vwap_reversal import VWAPReversal
from .opening_range_breakout import OpeningRangeBreakout
from .supertrend_ema import SupertrendEMA
from .rsi_divergence import RSIDivergence
from .gap_fade import GapFade

REGISTRY: dict[str, type[Strategy]] = {
    "vwap_reversal": VWAPReversal,
    "opening_range_breakout": OpeningRangeBreakout,
    "supertrend_ema": SupertrendEMA,
    "rsi_divergence": RSIDivergence,
    "gap_fade": GapFade,
}

__all__ = [
    "Strategy",
    "SignalResult",
    "VWAPReversal",
    "OpeningRangeBreakout",
    "SupertrendEMA",
    "RSIDivergence",
    "GapFade",
    "REGISTRY",
]
