"""환율 bronze 수집 — Frankfurter(ECB) 하루치를 Iceberg 파티션 교체로 적재한다.

Dagster `defs/frankfurter_fx/assets.py`의 `fx_rates_daily` 이관(설계 F1·F2).
- 파티션 키 = 전일 UTC 하루(`data_interval_start`). 🔴 KST가 아닌 이유는
  파티션 키가 그대로 API 날짜 파라미터로 나가기 때문이다(타임존 정책의 의도된 예외).
- 응답이 수십 행이라 태스크 하나에서 fetch → parse → 적재를 끝낸다(설계 F11).
- 원천 값은 로그·Asset event에 남기지 않는다 — 날짜·행 수·불리언만.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pendulum
from airflow.sdk import Asset, Connection, CronDataIntervalTimetable, dag, task
from lakehouse.assets import FX_RATES_URI

FX_ASSET = Asset(FX_RATES_URI)
IDENTIFIER = "frankfurter_fx.fx_rates_daily"


@dag(
    schedule=CronDataIntervalTimetable("0 1 * * *", timezone="UTC"),
    start_date=pendulum.datetime(2024, 10, 9, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "frankfurter_fx"],
)
def fx_rates_daily() -> None:
    """환율 bronze DAG."""

    @task(outlets=[FX_ASSET])
    def load_fx_rates(
        data_interval_start: pendulum.DateTime | None = None,
        outlet_events: Any = None,
    ) -> dict:
        """전일 환율을 받아 그 파티션만 교체한다."""
        from lakehouse.fx import FX_SCHEMA, RATES_BASE, rates_json_to_arrow
        from lakehouse.http import request_json
        from lakehouse.iceberg import (
            S3Conf,
            catalog_uri,
            ensure_table,
            make_catalog,
            replace_partition,
        )

        rate_date = data_interval_start.in_timezone("UTC").date().isoformat()
        payload = request_json(f"{RATES_BASE}/{rate_date}", params={})
        arrow = rates_json_to_arrow(payload, rate_date, datetime.now(tz=timezone.utc))

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
        table = ensure_table(make_catalog(uri, s3), IDENTIFIER, FX_SCHEMA)
        loaded = replace_partition(table, arrow, "rate_date", rate_date)

        source_date = payload["date"]
        extra = {
            "partition": rate_date,
            "rows": loaded["rows"],
            "source_date": source_date,
            # 🔴 주말·휴일 대체 응답을 드러내는 유일한 관측점 — 판정하지 않고 값만
            # 남긴다
            "date_matches_request": source_date == rate_date,
        }
        outlet_events[FX_ASSET].extra = extra
        return extra

    load_fx_rates()


fx_rates_daily()
