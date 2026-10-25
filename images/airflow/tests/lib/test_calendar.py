"""미국 거래일 판정(`lakehouse.calendar`) — 거래일 0행 차단의 기준(계획 Review Focus
2)."""

from datetime import date

import pytest
from lakehouse.calendar import is_us_trading_day, require_rows_on_trading_day


def test_weekend_is_not_trading_day():
    assert not is_us_trading_day(date(2025, 4, 19))  # 토요일


def test_nyse_holidays_are_not_trading_days():
    assert not is_us_trading_day(
        date(2025, 4, 18)
    )  # 성금요일(NYSE 휴장, 연방 공휴일 아님)
    assert not is_us_trading_day(date(2025, 11, 27))  # 추수감사절


def test_easter_monday_is_trading_day():
    # ECB는 휴장, 미국은 개장 — 환율 as-of가 4일 지연되는 바로 그날
    assert is_us_trading_day(date(2025, 4, 21))


def test_ordinary_tuesday_is_trading_day():
    assert is_us_trading_day(date(2025, 4, 22))


def test_zero_rows_on_trading_day_fails():
    with pytest.raises(ValueError, match="거래일"):
        require_rows_on_trading_day(date(2025, 4, 22), 0)


def test_zero_rows_on_holiday_passes():
    require_rows_on_trading_day(date(2025, 4, 18), 0)  # 성금요일 0행은 정상


def test_rows_on_trading_day_passes():
    require_rows_on_trading_day(date(2025, 4, 22), 12000)
