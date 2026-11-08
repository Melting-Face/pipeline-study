"""파티션 키 유도(`lakehouse.partition`) — 최종 리뷰 I1 · #315.

logical date 없이 수동 트리거하면 `data_interval_start`가 None이다(Airflow 3.2).
원인 모를 AttributeError 대신 원인을 말하는 ValueError로 멈춰야 한다.

logical date를 지정해 수동 트리거하면 interval은 그 날이 아니라 실행 시각 기준
최근 구간으로 잡힌다(#315). 그 run이 다른 날짜의
파티션을 조용히 교체하지 않도록 두 값이 다르면 멈춰야 한다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from lakehouse.partition import partition_key

INTERVAL_START = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def test_utc_date_of_interval_start():
    assert partition_key(INTERVAL_START, INTERVAL_START) == "2026-10-23"


def test_non_utc_input_is_converted_to_utc_date():
    kst = timezone(timedelta(hours=9))
    # KST 10-24 09:00 = UTC 10-24 00:00 → 10-24
    # KST 10-24 08:00 = UTC 10-23 23:00 → 10-23
    at_0900 = datetime(2026, 10, 24, 9, 0, tzinfo=kst)
    at_0800 = datetime(2026, 10, 24, 8, 0, tzinfo=kst)
    assert partition_key(at_0900, at_0900) == "2026-10-24"
    assert partition_key(at_0800, at_0800) == "2026-10-23"


def test_same_instant_in_other_tz_is_not_a_mismatch():
    # 스케줄·backfill run은 같은 순간이다 — 표기 tz가 달라도 막지 않는다
    kst = timezone(timedelta(hours=9))
    same_in_kst = INTERVAL_START.astimezone(kst)
    assert partition_key(INTERVAL_START, same_in_kst) == "2026-10-23"


def test_missing_interval_fails_with_cause():
    with pytest.raises(ValueError, match="backfill"):
        partition_key(None, None)


def test_logical_date_mismatch_fails_with_cause():
    # #315 재현: logical 11-02로 트리거했는데 interval은 11-04
    logical = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
    interval = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="logical_date") as excinfo:
        partition_key(interval, logical)
    # 두 날짜를 함께 말해야 어느 파티션이 덮일 뻔했는지 보인다
    assert "2026-11-02" in str(excinfo.value)
    assert "2026-11-04" in str(excinfo.value)
