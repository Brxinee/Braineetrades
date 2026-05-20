"""First Hour Breakout: 60-minute opening range (first 12 × 5-min bars)."""

from .orb15 import ORB15Strategy


class FirstHourBOStrategy(ORB15Strategy):
    name = "FH-BO"
    _bars = 12
