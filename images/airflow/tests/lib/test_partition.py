"""파티션 키 유도(`lakehouse.partition`) — 최종 리뷰 I1.

logical date 없이 수동 트리거하면 `data_interval_start`가 None이다(Airflow 3.2).
원인 모를 AttributeError 대신 원인을 말하는 ValueError로 멈춰야 한다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from lakehouse.partition import partition_key


def test_utc_date_of_interval_start():
    assert (
        partition_key(datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)) == "2026-10-23"
    )


def test_non_utc_input_is_converted_to_utc_date():
    kst = timezone(timedelta(hours=9))
    # KST 10-24 09:00 = UTC 10-24 00:00 → 10-24
    # KST 10-24 08:00 = UTC 10-23 23:00 → 10-23
    assert partition_key(datetime(2026, 10, 24, 9, 0, tzinfo=kst)) == "2026-10-24"
    assert partition_key(datetime(2026, 10, 24, 8, 0, tzinfo=kst)) == "2026-10-23"


def test_missing_interval_fails_with_cause():
    with pytest.raises(ValueError, match="logical date"):
        partition_key(None)
