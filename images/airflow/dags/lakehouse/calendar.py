"""미국 거래일 판정 — 시세 적재의 「거래일 0행 차단」 기준.

NYSE 휴장일(`holidays.financial_holidays("NYSE")`) + 주말. 조기 폐장일은 거래일이다.
🔴 연방 공휴일 달력이 아니다 — 성금요일은 연방 공휴일이 아니지만 NYSE는 닫는다.
"""

from datetime import date
from functools import cache

import holidays


@cache
def _nyse(year: int) -> holidays.HolidayBase:
    return holidays.financial_holidays("NYSE", years=year)


def is_us_trading_day(day: date) -> bool:
    """NYSE가 여는 날인가."""
    return day.weekday() < 5 and day not in _nyse(day.year)


def require_rows_on_trading_day(day: date, rows: int) -> None:
    """거래일인데 0행이면 실패시킨다.

    「0행 적재 + 성공」을 막는다(설계 반목표). 휴장일 0행은 정상이다.
    """
    if rows == 0 and is_us_trading_day(day):
        message = (
            f"{day.isoformat()}은 미국 거래일인데 시세가 0행이다 — 원천 응답을 확인하라"
        )
        raise ValueError(message)
