"""ORB-30: 30-minute Opening Range Breakout (first 6 × 5-min bars)."""

from .orb15 import ORB15Strategy


class ORB30Strategy(ORB15Strategy):
    name = "ORB-30"
    _bars = 6
