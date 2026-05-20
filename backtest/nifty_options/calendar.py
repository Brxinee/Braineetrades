"""NSE trading calendar: holidays, weekly expiry dates, lot sizes."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

_THURSDAY = 3
_TUESDAY = 1

# NSE moved NIFTY weekly expiry from Thursday → Tuesday from Jan 2025
_EXPIRY_CHANGE = date(2025, 1, 1)

# Lot-size change dates (effective from expiry of that month)
_LOT_75_FROM = date(2024, 11, 28)
_LOT_50_FROM = date(2021, 4, 30)

# ── NSE trading holidays 2020-2026 ───────────────────────────────────────────
# Source: NSE official exchange circulars / market closure notices.
# 2026 dates are approximate; update when NSE publishes the annual calendar.
_NSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # ── 2020 ──
        date(2020, 2, 21),  # Mahashivratri
        date(2020, 3, 10),  # Holi
        date(2020, 4, 2),   # Ram Navami
        date(2020, 4, 10),  # Good Friday
        date(2020, 4, 14),  # Dr. Ambedkar Jayanti
        date(2020, 5, 1),   # Maharashtra Day
        date(2020, 10, 2),  # Gandhi Jayanti
        date(2020, 11, 16), # Diwali Balipratipada
        date(2020, 11, 30), # Guru Nanak Jayanti
        date(2020, 12, 25), # Christmas
        # ── 2021 ──
        date(2021, 1, 26),  # Republic Day
        date(2021, 3, 11),  # Maha Shivratri
        date(2021, 3, 29),  # Holi
        date(2021, 4, 2),   # Good Friday
        date(2021, 4, 14),  # Dr. Ambedkar Jayanti
        date(2021, 5, 13),  # Eid ul-Fitr
        date(2021, 7, 21),  # Bakri Id
        date(2021, 8, 19),  # Muharram
        date(2021, 10, 15), # Dussehra
        date(2021, 11, 4),  # Diwali Laxmi Puja
        date(2021, 11, 5),  # Diwali Balipratipada
        date(2021, 11, 19), # Guru Nanak Jayanti
        date(2021, 12, 24), # Christmas Eve (observed)
        # ── 2022 ──
        date(2022, 1, 26),  # Republic Day
        date(2022, 3, 1),   # Maha Shivratri
        date(2022, 3, 18),  # Holi
        date(2022, 4, 14),  # Dr. Ambedkar Jayanti
        date(2022, 4, 15),  # Good Friday
        date(2022, 5, 3),   # Eid ul-Fitr
        date(2022, 8, 9),   # Muharram
        date(2022, 8, 15),  # Independence Day
        date(2022, 8, 31),  # Ganesh Chaturthi
        date(2022, 10, 5),  # Dussehra
        date(2022, 10, 24), # Diwali Laxmi Puja
        date(2022, 10, 26), # Diwali Balipratipada
        date(2022, 11, 8),  # Guru Nanak Jayanti
        date(2022, 12, 26), # Christmas (observed, Dec 25 = Sunday)
        # ── 2023 ──
        date(2023, 1, 26),  # Republic Day
        date(2023, 3, 8),   # Holi
        date(2023, 3, 30),  # Ram Navami
        date(2023, 4, 4),   # Mahavir Jayanti
        date(2023, 4, 7),   # Good Friday
        date(2023, 4, 14),  # Dr. Ambedkar Jayanti
        date(2023, 4, 22),  # Eid ul-Fitr
        date(2023, 5, 1),   # Maharashtra Day
        date(2023, 6, 28),  # Bakri Eid
        date(2023, 8, 15),  # Independence Day
        date(2023, 9, 19),  # Ganesh Chaturthi
        date(2023, 10, 2),  # Gandhi Jayanti
        date(2023, 11, 13), # Diwali Laxmi Puja
        date(2023, 11, 14), # Diwali Balipratipada
        date(2023, 11, 27), # Guru Nanak Jayanti
        date(2023, 12, 25), # Christmas
        # ── 2024 ──
        date(2024, 1, 22),  # Ram Mandir Pran Pratishtha
        date(2024, 1, 26),  # Republic Day
        date(2024, 3, 8),   # Maha Shivratri
        date(2024, 3, 25),  # Holi
        date(2024, 3, 29),  # Good Friday
        date(2024, 4, 11),  # Eid ul-Fitr
        date(2024, 4, 14),  # Dr. Ambedkar Jayanti
        date(2024, 4, 17),  # Ram Navami
        date(2024, 5, 1),   # Maharashtra Day
        date(2024, 5, 20),  # Special closure (Lok Sabha elections)
        date(2024, 6, 17),  # Bakri Eid
        date(2024, 7, 17),  # Muharram
        date(2024, 8, 15),  # Independence Day
        date(2024, 10, 2),  # Gandhi Jayanti
        date(2024, 10, 12), # Dussehra
        date(2024, 11, 1),  # Diwali Laxmi Puja
        date(2024, 11, 15), # Guru Nanak Jayanti
        date(2024, 12, 25), # Christmas
        # ── 2025 ──
        date(2025, 2, 26),  # Maha Shivratri
        date(2025, 3, 14),  # Holi
        date(2025, 3, 31),  # Eid ul-Fitr
        date(2025, 4, 10),  # Ram Navami
        date(2025, 4, 14),  # Dr. Ambedkar Jayanti
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 1),   # Maharashtra Day
        date(2025, 8, 15),  # Independence Day
        date(2025, 8, 27),  # Ganesh Chaturthi
        date(2025, 10, 2),  # Gandhi Jayanti
        date(2025, 10, 20), # Diwali Laxmi Puja (approximate)
        date(2025, 10, 21), # Diwali Balipratipada (approximate)
        date(2025, 11, 5),  # Guru Nanak Jayanti (approximate)
        date(2025, 12, 25), # Christmas
        # ── 2026 (approximate — update when NSE publishes) ──
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 20),  # Holi (approximate)
        date(2026, 4, 3),   # Good Friday (approximate)
        date(2026, 4, 14),  # Dr. Ambedkar Jayanti
        date(2026, 5, 1),   # Maharashtra Day
        date(2026, 8, 15),  # Independence Day (approximate weekday)
        date(2026, 10, 2),  # Gandhi Jayanti
        date(2026, 12, 25), # Christmas
    }
)


def is_trading_day(d: date) -> bool:
    """True if `d` is a weekday that is not an NSE holiday."""
    return d.weekday() < 5 and d not in _NSE_HOLIDAYS


def lot_size(d: date) -> int:
    """NIFTY 50 index options lot size effective on `d`."""
    if d >= _LOT_75_FROM:
        return 75
    if d >= _LOT_50_FROM:
        return 50
    return 25


def next_expiry(d: date) -> date:
    """Return the soonest NIFTY weekly expiry on or after `d`.

    Schedule:
        pre-2025  → Thursday
        2025+     → Tuesday

    If the computed expiry is a holiday the date rolls backward to the
    previous trading day (standard NSE convention).
    """
    target_wd = _THURSDAY if d < _EXPIRY_CHANGE else _TUESDAY
    days_ahead = (target_wd - d.weekday()) % 7
    expiry = d + timedelta(days=days_ahead)
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


def dte(d: date) -> int:
    """Calendar days to the next weekly expiry from `d` (0 = expiry day)."""
    return (next_expiry(d) - d).days


def trading_days(start: date, end: date) -> Iterator[date]:
    """Yield every NSE trading day in [start, end] inclusive."""
    current = start
    while current <= end:
        if is_trading_day(current):
            yield current
        current += timedelta(days=1)
