"""
alerts.py — Fetches data from braineetrades.vercel.app and formats Telegram messages.
"""
from __future__ import annotations
import httpx
import feedparser
import logging
from datetime import datetime
import pytz

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
API = "https://braineetrades.vercel.app"

IMPORTANT_KEYWORDS = [
    "nifty", "sensex", "banknifty", "rbi", "sebi", "circuit", "halt",
    "ipo", "results", "earnings", "fii", "dii", "crude", "inflation",
    "rate", "gdp", "budget", "policy", "ban", "f&o", "expiry",
]

NEWS_RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/business.xml",
]

STRATEGY_EMOJI = {
    "orb15":                 "📐",
    "opening_range_breakout":"📐",
    "vwap_reversal":         "🔄",
    "supertrend_ema":        "📈",
    "gap_fade":              "🕳️",
    "rsi_divergence":        "📊",
}

REGIME_EMOJI = {
    "BULL_TREND":   "🟢",
    "BEAR_TREND":   "🔴",
    "SIDEWAYS":     "⬛",
    "HIGH_VOL":     "⚡",
    "RECOVERING":   "🟡",
    "WEAKENING":    "🟠",
}


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


async def get_regime() -> dict | None:
    return await _get("/api/regime")


async def get_quotes() -> list[dict]:
    data = await _get("/api/quotes", symbols="^NSEI,^NSEBANK,^VIX")
    if not data:
        return []
    return data.get("quotes", data) if isinstance(data, dict) else data


async def get_signals(strategy: str = "orb15") -> list[dict]:
    data = await _post("/api/scan", {"strategy": strategy, "symbols": [], "lookback_days": 2})
    if not data:
        return []
    return data.get("signals", [])


async def format_morning_brief() -> str:
    now = datetime.now(IST).strftime("%d %b %Y")
    regime_data = await get_regime()
    quotes = await get_quotes()

    regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
    conf   = regime_data.get("confidence", 0)   if regime_data else 0
    gate   = regime_data.get("trade_gate", "?") if regime_data else "?"
    emoji  = REGIME_EMOJI.get(regime, "❓")

    q_map = {q["symbol"]: q for q in quotes}
    nifty = q_map.get("^NSEI", {})
    bank  = q_map.get("^NSEBANK", {})
    vix   = q_map.get("^VIX", {})

    def fmt_q(q: dict, label: str) -> str:
        ltp = q.get("ltp", 0)
        chg = q.get("change_pct", 0)
        sign = "▲" if chg >= 0 else "▼"
        col  = "+" if chg >= 0 else ""
        return f"  {label}: ₹{ltp:,.2f}  {sign} {col}{chg:.2f}%"

    gate_msg = {
        "TRADE":   "✅ Market looks tradeable today",
        "CAUTION": "⚠️ Trade small — choppy conditions",
        "AVOID":   "🚫 High risk — consider sitting out",
    }.get(gate, "")

    lines = [
        f"🌅 *Good Morning — {now}*",
        "",
        f"{emoji} *Market Regime: {regime}*  ({conf:.0f}% confidence)",
        gate_msg,
        "",
        "📊 *Key Levels*",
    ]
    if nifty:  lines.append(fmt_q(nifty, "NIFTY 50"))
    if bank:   lines.append(fmt_q(bank,  "BANKNIFTY"))
    if vix:    lines.append(fmt_q(vix,   "VIX     "))

    lines += [
        "",
        "📋 *Strategies active today:*",
        "  • ORB-15 breakout watch from 9:30",
        "  • VWAP Reversal from 10:00",
        "  • Gap Fade if gap > 0.5%",
        "",
        "Type /signals for live entry alerts",
    ]
    return "\n".join(lines)


async def format_signal_alert(sig: dict, strategy_key: str) -> str:
    sym   = sig.get("symbol", "?")
    entry = sig.get("entry", sig.get("close", 0))
    sl    = sig.get("stop_loss", sig.get("sl", 0))
    tgt   = sig.get("target", 0)
    conf  = sig.get("confidence", sig.get("score", 0))
    side  = sig.get("side", "LONG").upper()
    ts    = sig.get("timestamp", sig.get("bar_time", ""))

    rr = round((tgt - entry) / (entry - sl), 1) if sl and tgt and (entry - sl) != 0 else 0
    emoji = STRATEGY_EMOJI.get(strategy_key, "🔔")
    side_emoji = "📈" if side == "LONG" else "📉"

    lines = [
        f"{emoji} *SIGNAL — {strategy_key.upper().replace('_',' ')}*",
        f"{side_emoji} *{sym}  |  {side}*",
        "",
        f"  🎯 Entry:  ₹{entry:,.2f}",
        f"  🛑 SL:     ₹{sl:,.2f}",
        f"  💰 Target: ₹{tgt:,.2f}",
        f"  📐 R:R     1 : {rr}",
        f"  💡 Confidence: {conf:.0f}%",
    ]
    if ts:
        lines.append(f"  ⏰ Signal bar: {ts}")
    return "\n".join(lines)


async def format_eod_summary() -> str:
    regime_data = await get_regime()
    quotes = await get_quotes()

    regime = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
    emoji  = REGIME_EMOJI.get(regime, "❓")

    q_map = {q["symbol"]: q for q in quotes}
    nifty = q_map.get("^NSEI", {})
    bank  = q_map.get("^NSEBANK", {})

    def fmt_eod(q: dict, label: str) -> str:
        chg = q.get("change_pct", 0)
        ltp = q.get("ltp", 0)
        sign = "▲" if chg >= 0 else "▼"
        return f"  {label}: ₹{ltp:,.2f}  {sign} {abs(chg):.2f}%"

    lines = [
        "🌆 *Market Close Summary*",
        "",
        f"{emoji} *Closing regime: {regime}*",
        "",
        "📊 *Day's close*",
    ]
    if nifty: lines.append(fmt_eod(nifty, "NIFTY 50 "))
    if bank:  lines.append(fmt_eod(bank,  "BANKNIFTY"))
    lines += [
        "",
        "📋 Review your trades in the journal",
        "🌙 Good night — see you at 9:10 AM",
    ]
    return "\n".join(lines)


def fetch_news_sync() -> list[dict]:
    """Fetch and filter important market news from RSS feeds."""
    articles = []
    for url in NEWS_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:10]:
                title = e.get("title", "")
                if any(kw in title.lower() for kw in IMPORTANT_KEYWORDS):
                    articles.append({
                        "title": title,
                        "link":  e.get("link", ""),
                        "source": feed.feed.get("title", "News"),
                    })
        except Exception as ex:
            log.warning("RSS %s failed: %s", url, ex)
    seen, unique = set(), []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
    return unique[:5]


def format_news(articles: list[dict]) -> str:
    if not articles:
        return "📰 No important market news right now."
    lines = ["📰 *Market News Alerts*", ""]
    for a in articles:
        lines.append(f"• [{a['title']}]({a['link']})")
        lines.append(f"  _— {a['source']}_")
        lines.append("")
    return "\n".join(lines)
