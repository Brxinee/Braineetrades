"""
bot.py — Brainee Trades | NIFTY Options Intraday Bot

Scheduled alerts (weekdays, IST):
  09:10 — Morning brief: regime, spot, ATM strike, CPR, OI analysis
  09:30 — ORB setup check
  09:45, 10:00 … 15:00 (every 15 min) — Signal scan
  15:00 — Start exiting reminder
  15:15 — HARD EXIT alert
  15:35 — EOD summary
  Every 30 min 09:00–15:30 — Important market news

Commands:
  /entry   — best CE/PE right now with T1/T2/SL
  /regime  — current market regime
  /oi      — option chain: PCR, CE wall, PE wall, max pain
  /levels  — CPR + key support/resistance levels
  /news    — latest market news
  /expiry  — this week's expiry date
  /status  — bot health check
  /test    — fire a test alert now (anytime)

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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
    format_entry_suggestion,
    format_exit_reminder,
    format_eod_summary,
    format_news,
    format_oi_analysis,
    format_levels,
    fetch_news,
    get_regime,
    get_nifty_spot,
    get_nifty_signals,
    _next_thursday,
    REGIME_EMOJI,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("braineebot")

IST     = pytz.timezone("Asia/Kolkata")
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

_last_signals: set[str] = set()
_last_news:    set[str] = set()
_app: Application | None = None


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


async def _send(text: str) -> None:
    if not _app or not text:
        return
    try:
        await _app.bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ── scheduled jobs ────────────────────────────────────────────────────────────

async def job_morning_brief() -> None:
    log.info("Morning brief")
    await _send(await format_morning_brief())


async def job_scan() -> None:
    if not _is_market_hours():
        return
    log.info("Signal scan")
    try:
        spot    = await get_nifty_spot()
        regime  = await get_regime()
        signals = await get_nifty_signals()

        if not spot or not signals:
            return

        r_name    = regime.get("regime", "SIDEWAYS") if regime else "SIDEWAYS"
        direction = (
            "BULLISH" if r_name in ("BULL_TREND", "RECOVERING") else
            "BEARISH" if r_name in ("BEAR_TREND", "WEAKENING") else
            None
        )

        for sig in signals[:2]:
            key = f"{sig.get('_strategy')}:{sig.get('bar_time', sig.get('timestamp', ''))}"
            if key in _last_signals:
                continue
            _last_signals.add(key)
            if len(_last_signals) > 200:
                _last_signals.clear()

            conf = sig.get("confidence", sig.get("score", 0))
            if conf < 55:
                continue

            d   = direction or ("BULLISH" if sig.get("side", "").upper() == "LONG" else "BEARISH")
            msg = await format_signal_alert(sig, d, spot)
            await _send(msg)
            await asyncio.sleep(1)

    except Exception as e:
        log.warning("Scan error: %s", e)


async def job_exit_warning() -> None:
    await _send(format_exit_reminder(hard=False))


async def job_hard_exit() -> None:
    await _send(format_exit_reminder(hard=True))


async def job_eod() -> None:
    await _send(await format_eod_summary())


async def job_news() -> None:
    articles = fetch_news()
    new = [a for a in articles if a["title"] not in _last_news]
    if not new:
        return
    for a in new:
        _last_news.add(a["title"])
    if len(_last_news) > 300:
        _last_news.clear()
    await _send(format_news(new))


# ── command handlers ──────────────────────────────────────────────────────────

async def cmd_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔍 Analysing current conditions…")
    msg = await format_entry_suggestion()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_regime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await get_regime()
    if not data:
        await update.message.reply_text("❌ Could not fetch regime.")
        return
    r  = data.get("regime", "UNKNOWN")
    c  = data.get("confidence", 0)
    g  = data.get("trade_gate", "?")
    em = REGIME_EMOJI.get(r, "❓")
    ge = {"TRADE": "✅", "CAUTION": "⚠️", "AVOID": "🚫"}.get(g, "❓")
    await update.message.reply_text(
        f"{em} *Regime: {r}*  ({c:.0f}%)\n{ge} Trade gate: *{g}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_oi(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📊 Fetching option chain…")
    msg = await format_oi_analysis()
    await update.message.reply_text(
        msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )


async def cmd_levels(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📐 Calculating levels…")
    msg = await format_levels()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    articles = fetch_news()
    await update.message.reply_text(
        format_news(articles), parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def cmd_expiry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    exp     = _next_thursday()
    exp_fmt = datetime.strptime(exp, "%Y-%m-%d").strftime("%A, %d %b %Y")
    await update.message.reply_text(
        f"📅 *Next NIFTY Weekly Expiry*\n{exp_fmt}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    now    = datetime.now(IST).strftime("%d %b, %I:%M %p IST")
    market = "🟢 OPEN" if _is_market_hours() else "🔴 CLOSED"
    spot   = await get_nifty_spot()
    spot_s = f"₹{spot:,.2f}" if spot else "N/A"
    await update.message.reply_text(
        f"✅ *Bot running*\n\n"
        f"🕐 {now}\n"
        f"📈 Market: {market}\n"
        f"📍 NIFTY: {spot_s}\n"
        f"📅 Expiry: {_next_thursday()}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🧪 Running test — morning brief + entry + levels + OI…")
    await update.message.reply_text(
        await format_morning_brief(), parse_mode=ParseMode.MARKDOWN,
    )
    await asyncio.sleep(1)
    await update.message.reply_text(
        await format_entry_suggestion(), parse_mode=ParseMode.MARKDOWN,
    )
    await asyncio.sleep(1)
    await update.message.reply_text(
        await format_levels(), parse_mode=ParseMode.MARKDOWN,
    )
    await asyncio.sleep(1)
    await update.message.reply_text(
        await format_oi_analysis(), parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _app

    _app = Application.builder().token(TOKEN).build()

    _app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(
        "🤖 *Brainee Trades — NIFTY Options Bot*\n\n"
        "Commands:\n"
        "  /entry   — best CE/PE right now (T1, T2, SL, R:R)\n"
        "  /oi      — option chain: PCR, CE wall, PE wall, max pain\n"
        "  /levels  — CPR pivot levels + key S/R\n"
        "  /regime  — current market regime\n"
        "  /expiry  — this week's expiry\n"
        "  /news    — market news\n"
        "  /status  — bot health\n"
        "  /test    — fire a test alert now\n\n"
        "Auto-alerts every 15 min during market hours.",
        parse_mode=ParseMode.MARKDOWN,
    )))
    _app.add_handler(CommandHandler("entry",  cmd_entry))
    _app.add_handler(CommandHandler("regime", cmd_regime))
    _app.add_handler(CommandHandler("oi",     cmd_oi))
    _app.add_handler(CommandHandler("levels", cmd_levels))
    _app.add_handler(CommandHandler("news",   cmd_news))
    _app.add_handler(CommandHandler("expiry", cmd_expiry))
    _app.add_handler(CommandHandler("status", cmd_status))
    _app.add_handler(CommandHandler("test",   cmd_test))

    s  = AsyncIOScheduler(timezone=IST)
    wd = "mon-fri"

    s.add_job(job_morning_brief, CronTrigger(day_of_week=wd, hour=9, minute=10, timezone=IST))

    for h in range(9, 16):
        for m in [0, 15, 30, 45]:
            if h == 9 and m < 30:
                continue
            if h == 15 and m > 0:
                continue
            s.add_job(job_scan, CronTrigger(day_of_week=wd, hour=h, minute=m, timezone=IST))

    s.add_job(job_exit_warning, CronTrigger(day_of_week=wd, hour=15, minute=0,  timezone=IST))
    s.add_job(job_hard_exit,    CronTrigger(day_of_week=wd, hour=15, minute=15, timezone=IST))
    s.add_job(job_eod,          CronTrigger(day_of_week=wd, hour=15, minute=35, timezone=IST))

    for h in range(9, 16):
        for m in [0, 30]:
            s.add_job(job_news, CronTrigger(day_of_week=wd, hour=h, minute=m, timezone=IST))

    s.start()
    log.info("Brainee Trades NIFTY Options Bot started")
    _app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
