# CHECKPOINT 2 — expiry calendar + NSE holiday schedule
# Implements:
#   weekly_expiry(date) -> nearest weekly expiry date
#   is_trading_day(date) -> bool
#   trading_days(start, end) -> list[date]
#
# Expiry schedule:
#   pre-2025  : Thursday
#   2025+     : Tuesday
