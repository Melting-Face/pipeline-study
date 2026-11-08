"""일자 파티션 키 유도 — 데이터 구간 시작의 UTC 날짜.

🔴 logical date 없이 수동 트리거하면 Airflow 3.2는 `data_interval_start`를 주지 않는다.
그때 원인 모를 AttributeError로 죽지 않고 원인을 말하며 멈춘다(최종 리뷰 I1).

🔴 logical date를 지정해 수동 트리거해도 interval은 그 날이 아니라
**실행 시각 기준 최근 구간**이다(#315). 그대로 쓰면 다른 날짜의 파티션을
에러 없이 교체하므로 두 값이 다르면 멈춘다. 스케줄·backfill run은 둘이 같은
순간이라 통과한다. 과거 구간은 run이 없는 날짜에 `airflow backfill create`로
채운다 — 같은 날짜에 잘못 만든 run이 남아 있으면 backfill은 그 run(과 그
interval)을 재사용한다.
"""

from datetime import datetime, timezone


def partition_key(
    data_interval_start: datetime | None, logical_date: datetime | None
) -> str:
    """`data_interval_start`의 UTC 날짜("YYYY-MM-DD").

    interval이 None이거나 logical_date와 다른 순간이면 ValueError.
    """
    if data_interval_start is None:
        message = (
            "data_interval_start가 없다 — "
            "run이 없는 날짜를 airflow backfill create로 돌린다"
        )
        raise ValueError(message)
    _ = logical_date  # [프로브] 가드 삭제 — ruff ARG001 회피
    return data_interval_start.astimezone(timezone.utc).date().isoformat()
