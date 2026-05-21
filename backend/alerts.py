"""
alerts.py — Real-time alert management with WebSocket broadcast support.

AlertManager handles:
  - Maintaining a list of active WebSocket connections.
  - Broadcasting Alert objects to all connected clients as JSON.
  - Rule-based alert generation for: strategy signals, regime changes,
    volatility spikes, daily loss limits, and event warnings.
  - Keeping a ring-buffer of the last 100 fired alerts for late-joining clients.

Dependencies: fastapi (WebSocket), standard library only.

Usage (FastAPI router):
    from alerts import alert_manager

    @router.websocket("/ws/alerts")
    async def ws_alerts(websocket: WebSocket):
        await alert_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()   # keep alive
        except WebSocketDisconnect:
            await alert_manager.disconnect(websocket)
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytz
from fastapi import WebSocket
from fastapi.websockets import WebSocketState

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Maximum number of alerts held in the history ring-buffer
_HISTORY_MAXLEN = 100

# VIX change thresholds for volatility-spike alerts
_VIX_SPIKE_PCT = 8.0          # relative % jump triggers WARNING
_VIX_SPIKE_CRITICAL_PCT = 15.0  # relative % jump triggers CRITICAL

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """
    Immutable alert record.

    id        : UUID string — unique per alert instance.
    type      : One of the well-known alert type constants (see AlertType).
    title     : Short human-readable title (fits in a toast notification).
    message   : Longer descriptive body for the alert panel.
    symbol    : Instrument ticker this alert relates to ("NIFTY" for index-wide).
    severity  : "INFO" | "WARNING" | "CRITICAL".
    timestamp : ISO-8601 string in IST.
    data      : Arbitrary extra payload for the frontend (e.g. signal details).
    """

    id: str
    type: str
    title: str
    message: str
    symbol: str
    severity: str
    timestamp: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return asdict(self)


class AlertType:
    """Well-known alert type strings — use as constants throughout the codebase."""

    SIGNAL = "SIGNAL"
    REGIME_CHANGE = "REGIME_CHANGE"
    VWAP_REVERSAL = "VWAP_REVERSAL"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    EVENT_WARNING = "EVENT_WARNING"


class AlertSeverity:
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# AlertRule — pluggable rule descriptor (for future extensibility)
# ---------------------------------------------------------------------------


@dataclass
class AlertRule:
    """
    Descriptor for a registered alert rule.

    rule_id  : Unique identifier for the rule.
    enabled  : Set to False to suppress the rule without deleting it.
    meta     : Arbitrary metadata for the rule (thresholds, cooldown, etc.).
    """

    rule_id: str
    enabled: bool = True
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------


class AlertManager:
    """
    Central alert hub with WebSocket fan-out.

    Thread-safety note: this class is designed for use within a single
    asyncio event loop (FastAPI / uvicorn).  The WebSocket operations are
    async; do not call them from synchronous code without an event loop.
    """

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._history: deque[Alert] = deque(maxlen=_HISTORY_MAXLEN)
        self._rules: list[AlertRule] = self._default_rules()
        logger.info("AlertManager initialised")

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection and replay recent history.

        The client immediately receives up to 10 recent alerts so the UI
        can render a populated alert panel without waiting for the next
        broadcast cycle.
        """
        await websocket.accept()
        self._connections.append(websocket)
        logger.info(
            "AlertManager: client connected (%d total)", len(self._connections)
        )

        # Replay the last min(10, history) alerts to the newly connected client
        recent = list(self._history)[-10:]
        for alert in recent:
            try:
                await websocket.send_json(alert.to_dict())
            except Exception as exc:
                logger.debug("AlertManager: failed to replay alert to new client: %s", exc)
                break

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the connection pool."""
        try:
            self._connections.remove(websocket)
        except ValueError:
            pass
        logger.info(
            "AlertManager: client disconnected (%d remaining)", len(self._connections)
        )

    async def broadcast(self, alert: Alert) -> None:
        """
        Append ``alert`` to history and send it to all connected clients.

        Stale / closed connections are silently removed from the pool.
        """
        self._history.append(alert)

        stale: list[WebSocket] = []
        payload = alert.to_dict()

        for ws in list(self._connections):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(payload)
                else:
                    stale.append(ws)
            except Exception as exc:
                logger.debug("AlertManager: send failed for client, marking stale: %s", exc)
                stale.append(ws)

        for ws in stale:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass

        if stale:
            logger.debug(
                "AlertManager: removed %d stale connections (%d remaining)",
                len(stale),
                len(self._connections),
            )

        logger.info(
            "Alert broadcast: [%s] %s — %s",
            alert.severity,
            alert.type,
            alert.title,
        )

    # ------------------------------------------------------------------
    # Rule-based alert generators
    # ------------------------------------------------------------------

    def check_signal_alert(self, signals: list[Any]) -> list[Alert]:
        """
        Convert a list of strategy signal objects into Alert records.

        Each signal is expected to be a dict (or object with a ``__dict__``)
        containing at minimum:
            {
                "symbol": str,
                "direction": str,   # "LONG" | "SHORT"
                "strategy": str,
                "entry": float,
                "sl": float,
                "target": float | None,
                "confidence": float | None,   # 0–10
            }

        Returns list of SIGNAL alerts — caller is responsible for broadcasting.
        """
        if not self._rule_enabled("signal_alert"):
            return []

        alerts: list[Alert] = []
        for sig in signals:
            if isinstance(sig, dict):
                d = sig
            elif hasattr(sig, "__dict__"):
                d = sig.__dict__
            else:
                logger.debug("check_signal_alert: unrecognised signal type %s", type(sig))
                continue

            symbol = str(d.get("symbol", "NIFTY"))
            direction = str(d.get("direction", "LONG"))
            strategy = str(d.get("strategy", "Unknown"))
            entry = float(d.get("entry") or 0.0)
            sl = float(d.get("sl") or 0.0)
            target = d.get("target")
            confidence = d.get("confidence")

            target_str = f"₹{target:.2f}" if target else "trailing"
            conf_str = f" (confidence {confidence:.1f}/10)" if confidence else ""

            title = f"{direction} signal — {symbol} via {strategy}"
            message = (
                f"{strategy} generated a {direction} signal on {symbol}. "
                f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | Target: {target_str}{conf_str}."
            )

            severity = AlertSeverity.INFO
            if confidence and float(confidence) >= 7.5:
                severity = AlertSeverity.WARNING  # high-conviction upgrade

            alerts.append(
                _make_alert(
                    alert_type=AlertType.SIGNAL,
                    title=title,
                    message=message,
                    symbol=symbol,
                    severity=severity,
                    data={
                        "direction": direction,
                        "strategy": strategy,
                        "entry": entry,
                        "sl": sl,
                        "target": target,
                        "confidence": confidence,
                    },
                )
            )

        return alerts

    def check_regime_change(
        self, old_regime: str, new_regime: str
    ) -> Alert | None:
        """
        Fire an alert when the market regime transitions.

        Parameters
        ----------
        old_regime : str   Previous regime string (e.g. "TRENDING_BULL").
        new_regime : str   Incoming regime string.

        Returns None if regimes are the same or the rule is disabled.
        """
        if not self._rule_enabled("regime_change"):
            return None
        if old_regime == new_regime:
            return None

        # Determine severity: AVOID regimes are CRITICAL
        avoid_regimes = {"EVENT_CHAOS", "NO_TRADE"}
        caution_regimes = {"VOLATILE", "LOW_VOL_CHOP"}

        if new_regime in avoid_regimes:
            severity = AlertSeverity.CRITICAL
        elif new_regime in caution_regimes:
            severity = AlertSeverity.WARNING
        else:
            severity = AlertSeverity.INFO

        title = f"Regime changed: {old_regime} → {new_regime}"
        message = (
            f"Market regime has shifted from {old_regime} to {new_regime}. "
            f"{'⚠ Review open positions and halt new entries.' if severity == AlertSeverity.CRITICAL else 'Adjust strategy selection accordingly.'}"
        )

        return _make_alert(
            alert_type=AlertType.REGIME_CHANGE,
            title=title,
            message=message,
            symbol="NIFTY",
            severity=severity,
            data={"old_regime": old_regime, "new_regime": new_regime},
        )

    def check_volatility_spike(
        self, vix: float, prev_vix: float
    ) -> Alert | None:
        """
        Fire an alert when India VIX spikes significantly.

        Parameters
        ----------
        vix      : float   Current VIX reading.
        prev_vix : float   Previous VIX reading (typically from 1 bar ago).

        Returns None if the change is within normal range or the rule is
        disabled.
        """
        if not self._rule_enabled("volatility_spike"):
            return None
        if prev_vix <= 0:
            return None

        change_pct = (vix - prev_vix) / prev_vix * 100.0

        if abs(change_pct) < _VIX_SPIKE_PCT:
            return None

        direction = "spiked" if change_pct > 0 else "dropped"
        if abs(change_pct) >= _VIX_SPIKE_CRITICAL_PCT:
            severity = AlertSeverity.CRITICAL
        else:
            severity = AlertSeverity.WARNING

        title = f"VIX {direction} {abs(change_pct):.1f}% → {vix:.1f}"
        message = (
            f"India VIX has {direction} from {prev_vix:.1f} to {vix:.1f} "
            f"({change_pct:+.1f}%). "
            f"{'Elevated risk — consider reducing position sizes.' if change_pct > 0 else 'Volatility contracting — normal risk levels may apply.'}"
        )

        return _make_alert(
            alert_type=AlertType.VOLATILITY_SPIKE,
            title=title,
            message=message,
            symbol="VIX",
            severity=severity,
            data={
                "vix": vix,
                "prev_vix": prev_vix,
                "change_pct": round(change_pct, 2),
            },
        )

    def check_daily_loss(
        self,
        today_pnl: float,
        capital: float,
        limit_pct: float,
    ) -> Alert | None:
        """
        Fire a DAILY_LOSS_LIMIT alert when PnL has breached the configured
        drawdown limit for the session.

        Parameters
        ----------
        today_pnl  : float   Net realised PnL for today (negative = loss).
        capital    : float   Account capital used for percentage calculation.
        limit_pct  : float   Daily loss limit as a percentage (e.g. 2.0 → 2%).

        Returns None if the limit has not been breached or the rule is
        disabled.
        """
        if not self._rule_enabled("daily_loss"):
            return None
        if capital <= 0 or today_pnl >= 0:
            return None

        loss_pct = abs(today_pnl) / capital * 100.0

        if loss_pct < limit_pct:
            return None

        # Graded severity: critical at 1.5× the limit
        if loss_pct >= limit_pct * 1.5:
            severity = AlertSeverity.CRITICAL
        else:
            severity = AlertSeverity.WARNING

        title = f"Daily loss limit breached — ₹{abs(today_pnl):,.0f} ({loss_pct:.1f}%)"
        message = (
            f"Today's session loss of ₹{abs(today_pnl):,.0f} has exceeded the "
            f"configured {limit_pct:.1f}% daily limit "
            f"(actual: {loss_pct:.2f}%). Stop trading for the session."
        )

        return _make_alert(
            alert_type=AlertType.DAILY_LOSS_LIMIT,
            title=title,
            message=message,
            symbol="ACCOUNT",
            severity=severity,
            data={
                "today_pnl": today_pnl,
                "capital": capital,
                "limit_pct": limit_pct,
                "actual_loss_pct": round(loss_pct, 4),
            },
        )

    def check_vwap_reversal(
        self,
        symbol: str,
        ltp: float,
        vwap: float,
        prev_ltp: float,
    ) -> Alert | None:
        """
        Detect a VWAP crossover (price crossing VWAP) and raise an alert.

        A crossover is detected when ltp and prev_ltp are on opposite sides
        of vwap.  Requires both prices to be valid positives.

        Parameters
        ----------
        symbol   : str    Ticker symbol.
        ltp      : float  Current last-traded price.
        vwap     : float  Current VWAP value.
        prev_ltp : float  Last-bar's LTP.

        Returns None if no crossover or rule disabled.
        """
        if not self._rule_enabled("vwap_reversal"):
            return None
        if ltp <= 0 or vwap <= 0 or prev_ltp <= 0:
            return None

        was_above = prev_ltp > vwap
        is_above = ltp > vwap

        if was_above == is_above:
            return None  # no crossover

        direction = "above" if is_above else "below"
        cross_type = "reclaim" if is_above else "breakdown"

        title = f"VWAP {cross_type} — {symbol}"
        message = (
            f"{symbol} has crossed {direction} VWAP. "
            f"LTP: ₹{ltp:.2f} | VWAP: ₹{vwap:.2f}. "
            f"{'Potential long setup.' if is_above else 'Potential short / exit signal.'}"
        )

        return _make_alert(
            alert_type=AlertType.VWAP_REVERSAL,
            title=title,
            message=message,
            symbol=symbol,
            severity=AlertSeverity.INFO,
            data={
                "ltp": ltp,
                "vwap": vwap,
                "prev_ltp": prev_ltp,
                "cross_type": cross_type,
            },
        )

    def fire_event_warning(
        self,
        event_name: str,
        detail: str,
        symbol: str = "NIFTY",
        severity: str = AlertSeverity.WARNING,
        extra_data: dict | None = None,
    ) -> Alert:
        """
        Manually fire an EVENT_WARNING alert (e.g. RBI announcement, expiry).

        Returns the created Alert for the caller to broadcast.
        """
        return _make_alert(
            alert_type=AlertType.EVENT_WARNING,
            title=f"Event: {event_name}",
            message=detail,
            symbol=symbol,
            severity=severity,
            data={"event_name": event_name, **(extra_data or {})},
        )

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> list[Alert]:
        """
        Return the most recent alerts (up to ``limit``) in chronological order.

        Parameters
        ----------
        limit : int   Maximum number of alerts to return (default 50).

        Returns
        -------
        list[Alert]
        """
        limit = max(1, min(limit, _HISTORY_MAXLEN))
        history = list(self._history)
        return history[-limit:]

    def get_history_dicts(self, limit: int = 50) -> list[dict]:
        """Convenience wrapper — returns history as JSON-serialisable dicts."""
        return [a.to_dict() for a in self.get_history(limit)]

    def clear_history(self) -> None:
        """Flush all stored alert history."""
        self._history.clear()
        logger.info("AlertManager: history cleared")

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def add_rule(self, rule: AlertRule) -> None:
        """Register a new alert rule."""
        self._rules.append(rule)

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule by its ID. Returns True if found."""
        for rule in self._rules:
            if rule.rule_id == rule_id:
                rule.enabled = False
                return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """Re-enable a previously disabled rule. Returns True if found."""
        for rule in self._rules:
            if rule.rule_id == rule_id:
                rule.enabled = True
                return True
        return False

    def list_rules(self) -> list[dict]:
        """Return all registered rules as dicts."""
        return [{"rule_id": r.rule_id, "enabled": r.enabled, "meta": r.meta} for r in self._rules]

    # ------------------------------------------------------------------
    # Connection info
    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        """Number of active WebSocket connections."""
        return len(self._connections)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rule_enabled(self, rule_id: str) -> bool:
        """Return True if the named rule exists and is enabled."""
        for rule in self._rules:
            if rule.rule_id == rule_id:
                return rule.enabled
        # Default: allow unknown rules (they may not have been registered yet)
        return True

    @staticmethod
    def _default_rules() -> list[AlertRule]:
        """Initialise the built-in rule set."""
        return [
            AlertRule(rule_id="signal_alert", enabled=True),
            AlertRule(rule_id="regime_change", enabled=True),
            AlertRule(rule_id="volatility_spike", enabled=True),
            AlertRule(rule_id="daily_loss", enabled=True),
            AlertRule(rule_id="vwap_reversal", enabled=True),
            AlertRule(rule_id="event_warning", enabled=True),
        ]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _now_ist() -> str:
    """Return current timestamp as ISO-8601 in IST."""
    return datetime.now(IST).isoformat()


def _make_alert(
    alert_type: str,
    title: str,
    message: str,
    symbol: str,
    severity: str,
    data: dict | None = None,
) -> Alert:
    """
    Construct a new Alert with a fresh UUID and current IST timestamp.
    """
    return Alert(
        id=str(uuid.uuid4()),
        type=alert_type,
        title=title,
        message=message,
        symbol=symbol,
        severity=severity,
        timestamp=_now_ist(),
        data=data or {},
    )


# ---------------------------------------------------------------------------
# Module-level singleton — import this in FastAPI routers
# ---------------------------------------------------------------------------

alert_manager = AlertManager()
