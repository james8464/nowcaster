from datetime import date

from src.intraday.us_regular_sessions import is_full_us_cash_session


def test_nyse_holidays_early_closes_and_special_closure_excluded():
    assert is_full_us_cash_session(date(2023, 7, 3)) is False
    assert is_full_us_cash_session(date(2024, 3, 29)) is False
    assert is_full_us_cash_session(date(2025, 1, 9)) is False
    assert is_full_us_cash_session(date(2025, 12, 24)) is False
    assert is_full_us_cash_session(date(2025, 3, 10)) is True
