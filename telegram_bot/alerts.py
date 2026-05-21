"""
alerts.py — NIFTY 50 Options Intraday Alert Engine.

All signals are for NIFTY weekly options, intraday only.
Calls braineetrades.vercel.app API for regime + signals + option chain.
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, date, timedelta

import httpx
import feedparser
import pytz

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
API = "https://braineetrades.vercel.app"

# ── helpers ───────────────────────────────────────────────────────────────────

REGIME_EMOJI = {
    "BULL_TREND": "🟢", "RECOVERING": "🟡",
    "BEAR_TREND": "🔴", "WEAKENING":  "🟠",
    "SIDEWAYS":   "⬛", "HIGH_VOL":   "⚡",
}

NEWS_KEYWORDS = [
    "nifty", "banknifty", "rbi", "sebi", "circuit", "f&o", "expiry",
    "ipo", "results", "earnings", "fii", "inflation", "rate", "gdp",
    "budget", "policy", "crude", "vix", "options",
]
NEWS_RSS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/business.xml",
]


def _next_thursday() -> str:
    """Return the nearest upcoming Thursday (weekly NIFTY expiry) as YYYY-MM-DD."""
    today = date.today()
    days_ahead = (3 - today.weekday()) % 7  # 3 = Thursday
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _atm_strike(spot: float, step: int = 50) -> int:
    return round(round(spot / step) * step)


async def _get(path: str, **params) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{API}{path}", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("GET %s failed: %s", path, e)
        return None


async def _post(path: str, body: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}{path}", json=body)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("POST %s failed: %s", path, e)
        return None


async def get_nifty_spot() -> float | None:
    data = await _get("/api/quotes", symbols="^NSEI")
    if not data:
        return None
    quotes = data.get("quotes", data) if isinstance(data, dict) else data
    if isinstance(quotes, list) and quotes:
        return quotes[0].get("ltp")
    return None


async def get_vix() -> float | None:
    data = await _get("/api/quotes", symbols="^VIX")
    if not data:
        return None
    quotes = data.get("quotes", data) if isinstance(data, dict) else data
    if isinstance(quotes, list) and quotes:
        return quotes[0].get("ltp")
    return None


async def get_regime() -> dict | None:
    return await _get("/api/regime")


async def get_option_chain(expiry: str) -> dict | None:
    return await _get("/api/options", symbol="NIFTY", expiry=expiry)


async def get_nifty_signals() -> list[dict]:
    """Run ORB + VWAP strategies on NIFTY index (^NSEI)."""
    results = []
    for strategy in ["orb15", "vwap_reversal", "supertrend_ema"]:
        data = await _post("/api/scan", {
            "strategy": strategy,
            "symbols": ["^NSEI"],
            "lookback_days": 1,
        })
        if data and data.get("signals"):
            for sig in data["signals"]:
                sig["_strategy"] = strategy
            results.extend(data["signals"])
    return results


# ── option strike suggestion ──────────────────────────────────────────────────

async def suggest_option(direction: str, spot: float, expiry: str) -> dict:
    """
    Given bullish/bearish direction and NIFTY spot, suggest the best CE or PE.
    Returns dict with strike, type (CE/PE), premium estimate, SL, target.
    """
    opt_type = "CE" if direction.upper() in ("BULLISH", "LONG", "BUY") else "PE"
    atm = _atm_strike(spot)

    # For CE: buy ATM or 1-strike OTM (cheaper premium, higher leverage)
    # For PE: buy ATM or 1-strike OTM
    strike = atm if opt_type == "CE" else atm
    otm_strike = (atm + 50) if opt_type == "CE" else (atm - 50)

    # Try to get live premium from option chain
    premium_atm = None
    premium_otm = None
    chain = await get_option_chain(expiry)
    if chain and "chain" in chain:
        for row in chain["chain"]:
            if row.get("strike") == strike:
                premium_atm = row.get(f"{opt_type.lower()}_ltp") or row.get("ltp")
            if row.get("strike") == otm_strike:
                premium_otm = row.get(f"{opt_type.lower()}_ltp") or row.get("ltp")

    # Fallback: rough BS estimate using VIX
    vix = await get_vix() or 15.0
    if not premium_atm:
        T = 5 / 365  # ~1 week to expiry
        sigma = vix / 100
        d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        ncdf = lambda x: math.erfc(-x / math.sqrt(2)) / 2
        if opt_type == "CE":
            premium_atm = round(spot * ncdf(d1) - strike * math.exp(-0.065 * T) * ncdf(d2), 0)
        else:
            premium_atm = round(strike * math.exp(-0.065 * T) * ncdf(-d2) - spot * ncdf(-d1), 0)
        premium_atm = max(premium_atm, 10)

    sl_pct     = 0.40  # SL at 40% of premium (tight intraday)
    target_pct = 1.80  # Target 80% profit (realistic for intraday)

    entry  = premium_atm
    sl     = round(entry * sl_pct)
    target = round(entry * target_pct)
    lot_sl = round((entry - sl) * 50)  # NIFTY lot = 50 units

    return {
        "opt_type":   opt_type,
        "strike":     strike,
        "otm_strike": otm_strike,
        "expiry":     expiry,
        "premium":    entry,
        "sl":         sl,
        "target":     target,
        "lot_sl":     lot_sl,
        "vix":        round(vix, 1),
    }


# ── message formatters ────────────────────────────────────────────────────────

async def format_morning_brief() -> str:
    now       = datetime.now(IST).strftime("%d %b %Y")
    regime    = await get_regime()
    spot      = await get_nifty_spot()
    vix       = await get_vix()
    expiry    = _next_thursday()

    r_name  = regime.get("regime",     "UNKNOWN") if regime else "UNKNOWN"
    conf    = regime.get("confidence",  0)          if regime else 0
    gate    = regime.get("trade_gate", "?")         if regime else "?"
    emoji   = REGIME_EMOJI.get(r_name, "❓")

    gate_text = {
        "TRADE":   "✅ Good day to trade options",
        "CAUTION": "⚠️ Buy cheap OTM — small lots only",
        "AVOID":   "🚫 High risk — sit out today",
    }.get(gate, "")

    bias = "📈 Lean BULLISH → watch for CE entries" if r_name in ("BULL_TREND", "RECOVERING") \
      else "📉 Lean BEARISH → watch for PE entries" if r_name in ("BEAR_TREND", "WEAKENING") \
      else "↔️ No clear bias — wait for ORB breakout"

    atm = _atm_strike(spot) if spot else "?"
    exp_short = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d %b")

    lines = [
        f"🌅 *NIFTY Options Brief — {now}*",
        "",
        f"{emoji} *Regime: {r_name}*  ({conf:.0f}% conf)",
        gate_text,
        "",
        f"📍 *NIFTY Spot:* ₹{spot:,.2f}" if spot else "📍 NIFTY: N/A",
        f"⚡ *VIX:* {vix:.1f}" if vix else "",
        f"🎯 *ATM Strike:* {atm}  |  Expiry: {exp_short}",
        "",
        bias,
        "",
        "🕘 *Today's plan:*",
        "  • 9:15–9:30: Wait — let market settle",
        "  • 9:30: ORB setup — enter on breakout",
        "  • 10:00+: VWAP reversal / trend entries",
        "  • 15:00: Start trailing / booking profits",
        "  • 15:15: 🚨 EXIT ALL — no overnight options",
        "",
        "Type /entry for option suggestion now",
    ]
    return "\n".join(l for l in lines if l is not None)


async def format_signal_alert(sig: dict, direction: str, spot: float) -> str:
    strategy = sig.get("_strategy", "signal").upper().replace("_", " ")
    ts       = sig.get("bar_time", sig.get("timestamp", ""))
    conf     = sig.get("confidence", sig.get("score", 75))
    expiry   = _next_thursday()

    opt      = await suggest_option(direction, spot, expiry)
    opt_type = opt["opt_type"]
    strike   = opt["strike"]
    premium  = opt["premium"]
    sl       = opt["sl"]
    target   = opt["target"]
    lot_sl   = opt["lot_sl"]
    exp_short = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d %b")

    dir_emoji = "📈" if direction.upper() in ("BULLISH", "LONG") else "📉"

    lines = [
        f"🔔 *NIFTY OPTIONS SIGNAL*",
        f"",
        f"📐 Strategy: *{strategy}*",
        f"{dir_emoji} Direction: *{direction.upper()}*",
        f"",
        f"🎯 *Trade Setup*",
        f"  Buy: *NIFTY {strike} {opt_type}* ({exp_short})",
        f"  Entry premium: *₹{premium}–{premium+10}*",
        f"  🛑 SL: ₹{sl}  (exit if premium drops here)",
        f"  💰 Target: ₹{target}  (~{round((target/premium-1)*100)}% profit)",
        f"  📦 1 lot ({50} qty) risk: ~₹{lot_sl}",
        f"",
        f"📍 NIFTY Spot: ₹{spot:,.2f}",
        f"⚡ VIX: {opt['vix']}",
        f"💡 Confidence: {conf:.0f}%",
    ]
    if ts:
        lines.append(f"⏰ Signal at: {ts}")
    lines += [
        f"",
        f"🚨 *INTRADAY — Exit by 3:15 PM*",
    ]
    return "\n".join(lines)


async def format_entry_suggestion() -> str:
    """On-demand: give current best option trade."""
    spot    = await get_nifty_spot()
    regime  = await get_regime()
    expiry  = _next_thursday()

    if not spot:
        return "❌ Could not fetch NIFTY spot right now."

    r_name = regime.get("regime", "SIDEWAYS") if regime else "SIDEWAYS"
    gate   = regime.get("trade_gate", "CAUTION") if regime else "CAUTION"

    if gate == "AVOID":
        return f"🚫 *Avoid trading today*\nRegime: {r_name} — conditions too risky for options."

    direction = "BULLISH" if r_name in ("BULL_TREND", "RECOVERING") \
           else "BEARISH" if r_name in ("BEAR_TREND", "WEAKENING") \
           else None

    if not direction:
        return (
            f"↔️ *No clear directional bias*\n"
            f"Regime: {r_name}\n\n"
            f"Wait for ORB breakout (9:30 AM) before entering.\n"
            f"Enter CE if NIFTY breaks above ORB high, PE if below ORB low."
        )

    opt       = await suggest_option(direction, spot, expiry)
    opt_type  = opt["opt_type"]
    strike    = opt["strike"]
    premium   = opt["premium"]
    sl        = opt["sl"]
    target    = opt["target"]
    lot_sl    = opt["lot_sl"]
    exp_short = datetime.strptime(expiry, "%Y-%m-%d").strftime("%d %b")
    dir_emoji = "📈" if direction == "BULLISH" else "📉"

    return "\n".join([
        f"🎯 *Current Option Suggestion*",
        f"",
        f"{dir_emoji} *{direction}* | Regime: {r_name}",
        f"",
        f"  Buy: *NIFTY {strike} {opt_type}* ({exp_short})",
        f"  Entry: *₹{premium}–{premium+10}*",
        f"  SL: ₹{sl}",
        f"  Target: ₹{target}",
        f"  1 lot risk: ~₹{lot_sl}",
        f"",
        f"📍 Spot: ₹{spot:,.2f}  |  ⚡ VIX: {opt['vix']}",
        f"🚨 *Intraday — Exit by 3:15 PM*",
    ])


def format_exit_reminder(hard: bool = False) -> str:
    if hard:
        return (
            "🚨🚨 *3:15 PM — EXIT NOW* 🚨🚨\n\n"
            "Close ALL open NIFTY option positions.\n"
            "Do NOT hold options overnight.\n"
            "Theta decay accelerates — exit at market if needed."
        )
    return (
        "⏰ *3:00 PM — Start Exiting*\n\n"
        "15 minutes to close.\n"
        "If in profit → book now or trail SL tight.\n"
        "If at SL → exit immediately, don't hope.\n\n"
        "Hard exit reminder at 3:15 PM."
    )


async def format_eod_summary() -> str:
    spot   = await get_nifty_spot()
    regime = await get_regime()
    vix    = await get_vix()
    r_name = regime.get("regime", "UNKNOWN") if regime else "UNKNOWN"
    emoji  = REGIME_EMOJI.get(r_name, "❓")
    exp_short = datetime.strptime(_next_thursday(), "%Y-%m-%d").strftime("%d %b")

    return "\n".join([
        "🌆 *NIFTY Options — Day Close*",
        "",
        f"{emoji} Closing regime: *{r_name}*",
        f"📍 NIFTY: ₹{spot:,.2f}" if spot else "",
        f"⚡ VIX: {vix:.1f}" if vix else "",
        "",
        f"📅 Next expiry: {exp_short}",
        "",
        "✅ All positions should be closed.",
        "📓 Log your trades in the journal.",
        "🌙 Good night — brief at 9:10 AM tomorrow.",
    ])


def fetch_news() -> list[dict]:
    articles, seen = [], set()
    for url in NEWS_RSS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:15]:
                title = e.get("title", "")
                if title in seen:
                    continue
                if any(kw in title.lower() for kw in NEWS_KEYWORDS):
                    seen.add(title)
                    articles.append({
                        "title":  title,
                        "link":   e.get("link", ""),
                        "source": feed.feed.get("title", "News"),
                    })
        except Exception as ex:
            log.warning("RSS %s failed: %s", url, ex)
    return articles[:5]


def format_news(articles: list[dict]) -> str:
    if not articles:
        return "📰 No important market news right now."
    lines = ["📰 *NIFTY Market News*", ""]
    for a in articles:
        lines += [f"• [{a['title']}]({a['link']})", f"  _— {a['source']}_", ""]
    return "\n".join(lines)
