"""NVD CVE bronze 수집 — UTC 하루 구간을 raw로 착지시킨 뒤 그 파티션만 교체 적재한다.

옛 Dagster `nvd_cves` raw·bronze 자산 이관(미션 ②, Dagster 정의는 삭제됨). 로직은
`lakehouse.nvd`·`lakehouse.nvd_parse`(Airflow 비의존)에 있고 이 파일은 배선만 한다.
- 두 단계: `land_raw_nvd_cves`(API → S3 raw 새 회차) >> `load_nvd_cves`(raw → Iceberg).
  파서를 고친 뒤 bronze만 다시 돌릴 수 있게(API 미호출) 나눴다.
- 창 = `data_interval_start`의 UTC 날짜 하루(`lastModStartDate`~`lastModEndDate`).
  스케줄은 `catchup=False`다 — 과거 구간은 `airflow backfill create`로 수동 채운다.
- 🔴 API 호출 태스크만 Pool `nvd_api`(slots=1)에 넣는다 —
  키 하나의 한도를 DAG 간에 공유한다.
- 🔴 NVD 키는 `apiKey` 헤더로만 간다. 빈 키(공개 한도)도 허용한다.
  키는 로그·XCom·반환값·Asset event에 싣지 않는다 — 날짜·건수·경로만 남긴다.
- raw 단계는 Asset으로 내지 않는다(소비자 없음).
  bronze 태스크만 outlet을 내어 `nvd_dbt`를 깨운다.
- `force`: 이미 커밋된 회차가 있어도 새 회차를 옆에 추가한다(옛 회차는 지우지 않는다).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pendulum
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.sdk import (
    Asset,
    Connection,
    CronDataIntervalTimetable,
    Param,
    dag,
    task,
)
from lakehouse.assets import NVD_CVES_URI

CVES_ASSET = Asset(NVD_CVES_URI)
IDENTIFIER = "nvd.nvd_cves"
S3_CONN_ID = "seaweedfs_s3"


@dag(
    schedule=CronDataIntervalTimetable("0 19 * * *", timezone="UTC"),
    # Dagster 파티션 시작 리터럴 그대로(NVD CVE 2.0 API 공개 시점 이후).
    # 🔴 History(1999-01-01)와 26년 다른 것이 정상이다 — CVE의 lastModified는
    #    재수정마다 앞으로 끌려와 과거가 비지만 History는 변경 시각을 보존한다.
    #    두 시작일을 "맞추면" History 앞 24년이 에러 없이 범위 밖이 된다.
    start_date=pendulum.datetime(2023, 8, 23, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={"force": Param(False, type="boolean")},
    tags=["bronze", "nvd"],
)
def nvd_cves() -> None:
    """NVD CVE bronze DAG."""

    @task(pool="nvd_api", max_active_tis_per_dag=1)
    def land_raw_nvd_cves(
        data_interval_start: pendulum.DateTime | None = None,
        logical_date: pendulum.DateTime | None = None,
        params: Any = None,
    ) -> dict:
        """UTC 하루 구간을 끝까지 받아 새 회차로 S3 raw에 놓는다(API 호출 단계)."""
        from lakehouse.nvd import (
            CVES_END_PARAM,
            CVES_FEED,
            CVES_PAGE_SIZE,
            CVES_PATH,
            CVES_START_PARAM,
            land_window_pages,
        )
        from lakehouse.nvd_parse import CVES_ITEM_KEY, CVES_ITEMS_KEY
        from lakehouse.partition import partition_key

        window_date = partition_key(data_interval_start, logical_date)
        # 🔴 키는 이 지역 변수에서 fetch_page의 헤더로만 간다 — 반환값에 싣지 않는다
        api_key = Connection.get("nvd_api").password or ""
        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        result = land_window_pages(
            s3,
            api_key=api_key,
            feed=CVES_FEED,
            path=CVES_PATH,
            start_param=CVES_START_PARAM,
            end_param=CVES_END_PARAM,
            items_key=CVES_ITEMS_KEY,
            item_key=CVES_ITEM_KEY,
            id_field="id",
            page_size=CVES_PAGE_SIZE,
            window_date=window_date,
            fetched_at=datetime.now(tz=timezone.utc),
            force=bool(params["force"]),
        )
        return {"partition": window_date, **result}

    @task(outlets=[CVES_ASSET])
    def load_nvd_cves(
        data_interval_start: pendulum.DateTime | None = None,
        logical_date: pendulum.DateTime | None = None,
        outlet_events: Any = None,
    ) -> dict:
        """그 날의 최신 커밋 회차를 읽어 `window_date` 파티션만 교체한다."""
        from lakehouse.iceberg import (
            catalog_from_conns,
            ensure_table,
            replace_partition,
        )
        from lakehouse.nvd import CVES_FEED, read_window_pages
        from lakehouse.nvd_parse import (
            CVES_ITEMS_KEY,
            NVD_CVES_SCHEMA,
            count_outside_window,
            cves_to_arrow,
        )
        from lakehouse.partition import partition_key

        window_date = partition_key(data_interval_start, logical_date)
        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        pages, fetched_at, source_run = read_window_pages(
            s3, feed=CVES_FEED, window_date=window_date, items_key=CVES_ITEMS_KEY
        )
        arrow = cves_to_arrow(pages, window_date, fetched_at)

        catalog = catalog_from_conns(
            Connection.get("iceberg_catalog"), Connection.get(S3_CONN_ID)
        )
        table = ensure_table(catalog, IDENTIFIER, NVD_CVES_SCHEMA)
        loaded = replace_partition(table, arrow, "window_date", window_date)

        extra = {
            "partition": window_date,
            "rows": loaded["rows"],
            # 🔴 단위: 회차 **경로**(prefix)다 — KEV의 `source_object`(객체)와 다르다
            "source_run": source_run,
            "pages": len(pages),
            "total_results": pages[0]["totalResults"],
            # 이 적재는 API를 부르지 않는다(raw 단계의 몫)
            "api_requests": 0,
            # 관측 셀: 구간 밖 lastModified가 0이 아니면 UTC 가정·원천 해석을 의심한다
            "last_modified_outside_window": count_outside_window(
                arrow.column("last_modified").to_pylist(), window_date
            ),
        }
        outlet_events[CVES_ASSET].extra = extra
        return extra

    land_raw_nvd_cves() >> load_nvd_cves()


nvd_cves()
