"""일자 파티션 키 유도 — 데이터 구간 시작의 UTC 날짜.

🔴 logical date 없이 수동 트리거하면 Airflow 3.2는 `data_interval_start`를 주지 않는다.
그때 원인 모를 AttributeError로 죽지 않고 원인을 말하며 멈춘다(최종 리뷰 I1).
"""

from datetime import datetime, timezone


def partition_key(data_interval_start: datetime | None) -> str:
    """`data_interval_start`의 UTC 날짜("YYYY-MM-DD"). None이면 ValueError."""
    if data_interval_start is None:
        message = (
            "data_interval_start가 없다 — "
            "logical date를 지정해 트리거하거나 backfill로 돌린다"
        )
        raise ValueError(message)
    return data_interval_start.astimezone(timezone.utc).date().isoformat()
