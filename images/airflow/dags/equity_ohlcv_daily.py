"""Polygon 시세 bronze 수집 — 전 미국 티커 하루치 OHLCV를 파티션 교체로 적재한다.

Dagster `defs/polygon_market/assets.py`의 `equity_ohlcv_daily` 이관(설계 F1·F2).
- 파티션 키 = 전일 UTC 하루(`data_interval_start`). 시세는 하루 1요청이다.
- 🔴 무료 한도(분당 5회): run 1개·태스크 동시 1개 + 호출 뒤 대기로 순간 요청을
  1로 묶는다. Polygon을 쓰는 두 번째 DAG(뉴스 이관)가 생기면 Pool로 바꾼다.
- 🔴 거래일인데 0행이면 **적재 전에** 실패한다 — 빈 파티션으로 덮어쓰지 않는다.
- 무료 플랜의 과거 경계 밖 날짜는 403이 정상이다(백필 시).
- 원천 값은 로그·Asset event에 남기지 않는다.
"""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pendulum
from airflow.sdk import Asset, Connection, CronDataIntervalTimetable, dag, task
from lakehouse.assets import EQUITY_OHLCV_URI

OHLCV_ASSET = Asset(EQUITY_OHLCV_URI)
IDENTIFIER = "polygon_market.equity_ohlcv_daily"
# 분당 5회 상한 — 호출 하나가 끝난 뒤 이만큼 쉰다(재시도가 붙어도 간격을 지킨다)
MIN_CALL_INTERVAL_S = 12.5


@dag(
    schedule=CronDataIntervalTimetable("0 9 * * *", timezone="UTC"),
    start_date=pendulum.datetime(2024, 10, 9, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "polygon_market"],
)
def equity_ohlcv_daily() -> None:
    """Polygon 시세 bronze DAG."""

    @task(outlets=[OHLCV_ASSET], max_active_tis_per_dag=1)
    def load_equity_ohlcv(
        data_interval_start: pendulum.DateTime | None = None,
        outlet_events: Any = None,
    ) -> dict:
        """전일 grouped daily를 받아 그 파티션만 교체한다."""
        from lakehouse.calendar import is_us_trading_day, require_rows_on_trading_day
        from lakehouse.iceberg import (
            S3Conf,
            catalog_uri,
            ensure_table,
            make_catalog,
            replace_partition,
        )
        from lakehouse.partition import partition_key
        from lakehouse.polygon import (
            OHLCV_SCHEMA,
            fetch_grouped_daily,
            grouped_to_arrow,
        )

        trade_date = partition_key(data_interval_start)
        try:
            payload = fetch_grouped_daily(
                Connection.get("polygon_api").password or "", trade_date
            )
        finally:
            time.sleep(MIN_CALL_INTERVAL_S)
        arrow = grouped_to_arrow(payload, trade_date, datetime.now(tz=timezone.utc))
        require_rows_on_trading_day(date.fromisoformat(trade_date), arrow.num_rows)

        cat = Connection.get("iceberg_catalog")
        s3c = Connection.get("seaweedfs_s3")
        s3 = S3Conf(
            s3c.extra_dejson.get("endpoint_url", ""),
            s3c.login or "",
            s3c.password or "",
        )
        uri = catalog_uri(
            cat.login or "",
            cat.password or "",
            cat.host or "",
            cat.port or 5432,
            cat.schema or "",
        )
        table = ensure_table(make_catalog(uri, s3), IDENTIFIER, OHLCV_SCHEMA)
        loaded = replace_partition(table, arrow, "trade_date", trade_date)

        source_dates = sorted(set(arrow.column("source_date").to_pylist()))
        extra = {
            "partition": trade_date,
            "rows": loaded["rows"],
            # 🔴 응답이 신고한 수와 담은 행 수를 나란히 둔다 — 조용한 잘림은 둘이 갈릴
            # 때만 드러난다
            "results_count": payload.get("resultsCount"),
            "source_dates": ", ".join(source_dates) or "(없음)",
            "date_matches_request": source_dates in ([trade_date], []),
            "is_trading_day": is_us_trading_day(date.fromisoformat(trade_date)),
        }
        outlet_events[OHLCV_ASSET].extra = extra
        return extra

    load_equity_ohlcv()


equity_ohlcv_daily()
