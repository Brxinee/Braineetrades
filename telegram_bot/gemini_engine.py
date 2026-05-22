"""
gemini_engine.py — Gemini AI commentary for NIFTY trade signals.
"""
from __future__ import annotations
import os
import logging

log = logging.getLogger(__name__)

_client = None
_model  = "gemini-2.0-flash"


def _init():
    global _client
    if _client:
        return _client
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        from google import genai
        _client = genai.Client(api_key=key)
        log.info("Gemini AI ready")
    except Exception as e:
        log.warning("Gemini init failed: %s", e)
    return _client


def ai_signal_commentary(strategy: str, direction: str, spot: float,
                          regime: str, vix: float, ema9: float = 0,
                          ema20: float = 0, vwap: float = 0, atr: float = 0) -> str:
    c = _init()
    if not c:
        return ""
    prompt = (
        f"You are a senior NIFTY 50 options proprietary trader.\n"
        f"Write exactly 2 sentences of dry, institutional desk commentary on this signal.\n\n"
        f"Signal: {strategy} — {direction}\n"
        f"NIFTY Spot: ₹{spot:,.0f} | VIX: {vix} | Regime: {regime}\n"
        f"EMA 9/20: {ema9}/{ema20} | VWAP: {vwap} | ATR: {atr}\n\n"
        f"Rules: Use only the data above. No fabricated levels. No intro phrases. 2 sentences only."
    )
    try:
        r = c.models.generate_content(model=_model, contents=prompt)
        text = r.text.strip() if r.text else ""
        return f"\n\n🎙 _{text}_" if text else ""
    except Exception as e:
        log.warning("Gemini commentary failed: %s", e)
        return ""


def ai_morning_commentary(regime: str, spot: float, vix: float,
                           ema9: float, ema20: float, vwap: float,
                           pcr: float, ce_wall: int, pe_wall: int) -> str:
    c = _init()
    if not c:
        return ""
    prompt = (
        f"You are a senior NIFTY 50 options desk analyst.\n"
        f"Write a 3-sentence morning market outlook based strictly on these parameters.\n\n"
        f"Regime: {regime} | Spot: ₹{spot:,.0f} | VIX: {vix}\n"
        f"EMA 9/20: {ema9}/{ema20} | VWAP: {vwap}\n"
        f"PCR: {pcr} | CE Wall (resistance): {ce_wall} | PE Wall (support): {pe_wall}\n\n"
        f"Rules: Cite specific levels. No fabrication. No intro phrases. 3 sentences only."
    )
    try:
        r = c.models.generate_content(model=_model, contents=prompt)
        text = r.text.strip() if r.text else ""
        return f"\n\n🤖 *AI Desk View:*\n_{text}_" if text else ""
    except Exception as e:
        log.warning("Gemini morning commentary failed: %s", e)
        return ""


def ai_news_synthesis(headlines: list[str]) -> str:
    c = _init()
    if not c or not headlines:
        return ""
    bullet_list = "\n".join(f"• {h}" for h in headlines)
    prompt = (
        f"You are a financial news editor.\n"
        f"In 3 bullet points, synthesize the key market takeaways from these NIFTY-relevant headlines.\n"
        f"Be concise and institutional. No fluff.\n\n{bullet_list}"
    )
    try:
        r = c.models.generate_content(model=_model, contents=prompt)
        text = r.text.strip() if r.text else ""
        return f"\n\n🤖 *AI News Summary:*\n{text}" if text else ""
    except Exception as e:
        log.warning("Gemini news synthesis failed: %s", e)
        return ""
