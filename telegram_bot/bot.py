"""
bot.py — Brainee Trades Telegram Alert Bot

Schedules:
  09:10 IST (weekdays) — Morning brief
  09:30 IST (weekdays) — ORB signal check
  Every 15 min 09:15–15:15 (weekdays) — Signal scan (all strategies)
  Every 30 min 09:00–16:00 (weekdays) — News filter
  15:35 IST (weekdays) — EOD summary

Commands:
  /start   — subscribe to alerts
  /stop    — unsubscribe
  /regime  — current market regime
  /signals — run signal scan now
  /news    — latest market news
  /status  — bot status

Deploy: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time as dtime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from alerts import (
    format_morning_brief,
    format_signal_alert,
    format_eod_summary,
    fetch_news_sync,
    format_news,
    get_regime,
    get_signals,
    STRATEGY_EMOJI,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("braineebot")

IST = pytz.timezone("Asia/Kolkata")
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STRATEGIES = ["orb15", "vwap_reversal", "supertrend_ema", "gap_fade", "rsi_divergence"]

_last_signals: set[str] = set()   # deduplicate signal alerts
_last_news:    set[str] = set()   # deduplicate news alerts
_app: Application | None = None


# ─── helpers ──────────────────────────────────────────────────────────────────

def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


async def _send(text: str, chat_id: str = CHAT_ID) -> None:
    if _app is None:
        return
    try:
        await _app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ─── scheduled jobs ───────────────────────────────────────────────────────────

async def job_morning_brief() -> None:
    log.info("Running morning brief")
    msg = await format_morning_brief()
    await _send(msg)


async def job_scan_signals() -> None:
    if not _is_market_hours():
        return
    log.info("Running signal scan")
    for strategy in STRATEGIES:
        try:
            signals = await get_signals(strategy)
            for sig in signals:
                key = f"{strategy}:{sig.get('symbol')}:{sig.get('bar_time', sig.get('timestamp', ''))}"
                if key in _last_signals:
                    continue
                _last_signals.add(key)
                # Keep cache bounded
                if len(_last_signals) > 500:
                    _last_signals.clear()
                conf = sig.get("confidence", sig.get("score", 0))
                if conf < 60:
                    continue
                msg = await format_signal_alert(sig, strategy)
                await _send(msg)
                await asyncio.sleep(1)
        except Exception as e:
            log.warning("Signal scan error (%s): %s", strategy, e)


async def job_news() -> None:
    log.info("Running news check")
    articles = fetch_news_sync()
    new_articles = [a for a in articles if a["title"] not in _last_news]
    if not new_articles:
        return
    for a in new_articles:
        _last_news.add(a["title"])
    if len(_last_news) > 200:
        _last_news.clear()
    msg = format_news(new_articles)
    await _send(msg)


async def job_eod_summary() -> None:
    log.info("Running EOD summary")
    msg = await format_eod_summary()
    await _send(msg)


# ─── command handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Brainee Trades Bot active!*\n\n"
        "You'll receive:\n"
        "  🌅 Morning brief at 9:10 AM\n"
        "  🔔 Entry signals every 15 min\n"
        "  📰 Important news every 30 min\n"
        "  🌆 EOD summary at 3:35 PM\n\n"
        "Commands: /regime /signals /news /status",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_regime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await get_regime()
    if not data:
        await update.message.reply_text("❌ Could not fetch regime right now.")
        return
    regime = data.get("regime", "UNKNOWN")
    conf   = data.get("confidence", 0)
    gate   = data.get("trade_gate", "?")
    gate_emoji = {"TRADE": "✅", "CAUTION": "⚠️", "AVOID": "🚫"}.get(gate, "❓")
    await update.message.reply_text(
        f"📊 *Current Market Regime*\n\n"
        f"*{regime}*  ({conf:.0f}% confidence)\n"
        f"{gate_emoji} Trade gate: *{gate}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Scanning signals across all strategies…")
    found = False
    for strategy in STRATEGIES:
        signals = await get_signals(strategy)
        for sig in signals[:2]:
            msg = await format_signal_alert(sig, strategy)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            found = True
            await asyncio.sleep(0.5)
    if not found:
        await update.message.reply_text("😴 No active signals right now. Check back during market hours.")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    articles = fetch_news_sync()
    msg = format_news(articles)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    market = "🟢 OPEN" if _is_market_hours() else "🔴 CLOSED"
    await update.message.reply_text(
        f"✅ *Bot is running*\n\n"
        f"🕐 Time: {now}\n"
        f"📈 Market: {market}\n"
        f"🔔 Signals tracked: {len(_last_signals)}\n"
        f"📰 News seen: {len(_last_news)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global _app

    _app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    _app.add_handler(CommandHandler("start",   cmd_start))
    _app.add_handler(CommandHandler("regime",  cmd_regime))
    _app.add_handler(CommandHandler("signals", cmd_signals))
    _app.add_handler(CommandHandler("news",    cmd_news))
    _app.add_handler(CommandHandler("status",  cmd_status))

    scheduler = AsyncIOScheduler(timezone=IST)

    # Morning brief — 9:10 AM IST, Mon–Fri
    scheduler.add_job(job_morning_brief, CronTrigger(day_of_week="mon-fri", hour=9,  minute=10, timezone=IST))
    # ORB signal check — 9:30 AM IST, Mon–Fri
    scheduler.add_job(job_scan_signals,  CronTrigger(day_of_week="mon-fri", hour=9,  minute=30, timezone=IST))
    # Signal scan every 15 min during market hours — Mon–Fri
    scheduler.add_job(job_scan_signals,  CronTrigger(day_of_week="mon-fri", hour="9-15", minute="0,15,30,45", timezone=IST))
    # News every 30 min — Mon–Fri
    scheduler.add_job(job_news,          CronTrigger(day_of_week="mon-fri", hour="9-16", minute="0,30", timezone=IST))
    # EOD summary — 3:35 PM IST, Mon–Fri
    scheduler.add_job(job_eod_summary,   CronTrigger(day_of_week="mon-fri", hour=15, minute=35, timezone=IST))

    scheduler.start()
    log.info("Brainee Trades Bot starting (polling mode)")
    _app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
