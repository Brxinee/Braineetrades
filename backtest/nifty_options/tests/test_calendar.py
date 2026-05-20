"""Unit tests for calendar.py."""

from datetime import date

import pytest

from backtest.nifty_options.calendar import (
    dte,
    is_trading_day,
    lot_size,
    next_expiry,
    trading_days,
)


# ── is_trading_day ────────────────────────────────────────────────────────────

def test_weekend_not_trading():
    assert not is_trading_day(date(2024, 3, 9))   # Saturday
    assert not is_trading_day(date(2024, 3, 10))  # Sunday


def test_holiday_not_trading():
    assert not is_trading_day(date(2024, 3, 25))  # Holi 2024
    assert not is_trading_day(date(2024, 8, 15))  # Independence Day 2024


def test_normal_weekday_is_trading():
    assert is_trading_day(date(2024, 3, 26))  # Tuesday after Holi
    assert is_trading_day(date(2024, 1, 2))   # Normal Tuesday


# ── next_expiry ───────────────────────────────────────────────────────────────

def test_expiry_pre_2025_is_thursday():
    # Monday 2024-01-08 → expiry should be Thursday 2024-01-11
    expiry = next_expiry(date(2024, 1, 8))
    assert expiry == date(2024, 1, 11)
    assert expiry.weekday() == 3  # Thursday


def test_expiry_on_thursday_itself():
    assert next_expiry(date(2024, 1, 11)) == date(2024, 1, 11)


def test_expiry_friday_rolls_to_next_thursday():
    # Friday 2024-01-12 → next Thursday 2024-01-18
    assert next_expiry(date(2024, 1, 12)) == date(2024, 1, 18)


def test_expiry_2025_plus_is_tuesday():
    # Monday 2025-01-06 → expiry should be Tuesday 2025-01-07
    expiry = next_expiry(date(2025, 1, 6))
    assert expiry == date(2025, 1, 7)
    assert expiry.weekday() == 1  # Tuesday


def test_expiry_rolls_back_on_holiday():
    # 2021-11-04 was Diwali (holiday); expiry should roll to Wednesday 2021-11-03
    expiry = next_expiry(date(2021, 11, 1))  # Monday before Diwali Thursday
    assert is_trading_day(expiry)
    assert expiry <= date(2021, 11, 4)


# ── lot_size ──────────────────────────────────────────────────────────────────

def test_lot_size_pre_2021():
    assert lot_size(date(2020, 6, 1)) == 25
    assert lot_size(date(2021, 4, 1)) == 25


def test_lot_size_mid_period():
    assert lot_size(date(2021, 5, 1)) == 50
    assert lot_size(date(2024, 1, 1)) == 50


def test_lot_size_post_nov_2024():
    assert lot_size(date(2024, 11, 28)) == 75
    assert lot_size(date(2025, 6, 1)) == 75


# ── trading_days ──────────────────────────────────────────────────────────────

def test_trading_days_count_week():
    # Week 2024-01-08 to 2024-01-12 (Mon–Fri, no holidays) = 5 days
    days = list(trading_days(date(2024, 1, 8), date(2024, 1, 12)))
    assert len(days) == 5


def test_trading_days_skips_holiday():
    # Holi 2024 (Mon 25-Mar) skipped
    days = list(trading_days(date(2024, 3, 25), date(2024, 3, 28)))
    assert date(2024, 3, 25) not in days
    assert date(2024, 3, 26) in days


# ── dte ───────────────────────────────────────────────────────────────────────

def test_dte_on_expiry_day():
    expiry = next_expiry(date(2024, 1, 11))
    assert dte(expiry) == 0


def test_dte_monday_before_thursday():
    # Monday → Thursday = 3 calendar days
    assert dte(date(2024, 1, 8)) == 3
