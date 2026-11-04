"""NVD CVE Change History bronze 수집 — UTC 하루를 raw로 착지시켜 파티션 교체 적재.

`nvd_cves.py`와 같은 모양이다(옛 Dagster `nvd_cve_history` 이관, 미션 ②, 삭제됨).
- 두 단계: `land_raw_nvd_cve_history`(API → S3 raw 새 회차) >>
  `load_nvd_cve_history`(raw → Iceberg).
- 창 = `data_interval_start`의 UTC 날짜 하루(`changeStartDate`~`changeEndDate`).
  `catchup=False` — 과거 구간은 `airflow backfill create`로 수동 채운다.
- 🔴 API 호출 태스크만 Pool `nvd_api`(slots=1) — CVE DAG와 같은 키 한도를 공유한다.
- 🔴 NVD 키는 `apiKey` 헤더로만. 로그·XCom·반환값·Asset event에 싣지 않는다.
- 페이지 상한이 CVE(2000)와 달리 5000이다(`lakehouse.nvd.HISTORY_PAGE_SIZE`).
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
from lakehouse.assets import NVD_CVE_HISTORY_URI

HISTORY_ASSET = Asset(NVD_CVE_HISTORY_URI)
IDENTIFIER = "nvd.nvd_cve_history"
S3_CONN_ID = "seaweedfs_s3"


@dag(
    schedule=CronDataIntervalTimetable("0 19 * * *", timezone="UTC"),
    # Dagster 파티션 시작 리터럴 그대로(가장 이른 변경 이벤트는 1999-01 ~ 05 창 안).
    # 🔴 CVE(2023-08-23)와 26년 다른 것이 정상이다 — CVE의 lastModified는
    #    재수정마다 앞으로 끌려와 과거가 비지만 History는 변경 시각을 보존한다.
    #    두 시작일을 "맞추면" History 앞 24년이 에러 없이 범위 밖이 된다.
    start_date=pendulum.datetime(1999, 1, 1, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={"force": Param(False, type="boolean")},
    tags=["bronze", "nvd"],
)
def nvd_cve_history() -> None:
    """NVD CVE Change History bronze DAG."""

    @task(pool="nvd_api", max_active_tis_per_dag=1)
    def land_raw_nvd_cve_history(
        data_interval_start: pendulum.DateTime | None = None,
        params: Any = None,
    ) -> dict:
        """UTC 하루 구간을 끝까지 받아 새 회차로 S3 raw에 놓는다(API 호출 단계)."""
        from lakehouse.nvd import (
            HISTORY_END_PARAM,
            HISTORY_FEED,
            HISTORY_PAGE_SIZE,
            HISTORY_PATH,
            HISTORY_START_PARAM,
            land_window_pages,
        )
        from lakehouse.nvd_parse import HISTORY_ITEM_KEY, HISTORY_ITEMS_KEY
        from lakehouse.partition import partition_key

        window_date = partition_key(data_interval_start)
        # 🔴 키는 이 지역 변수에서 fetch_page의 헤더로만 간다 — 반환값에 싣지 않는다
        api_key = Connection.get("nvd_api").password or ""
        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        result = land_window_pages(
            s3,
            api_key=api_key,
            feed=HISTORY_FEED,
            path=HISTORY_PATH,
            start_param=HISTORY_START_PARAM,
            end_param=HISTORY_END_PARAM,
            items_key=HISTORY_ITEMS_KEY,
            item_key=HISTORY_ITEM_KEY,
            id_field="cveChangeId",
            page_size=HISTORY_PAGE_SIZE,
            window_date=window_date,
            fetched_at=datetime.now(tz=timezone.utc),
            force=bool(params["force"]),
        )
        return {"partition": window_date, **result}

    @task(outlets=[HISTORY_ASSET])
    def load_nvd_cve_history(
        data_interval_start: pendulum.DateTime | None = None,
        outlet_events: Any = None,
    ) -> dict:
        """그 날의 최신 커밋 회차를 읽어 `window_date` 파티션만 교체한다."""
        from lakehouse.iceberg import (
            catalog_from_conns,
            ensure_table,
            replace_partition,
        )
        from lakehouse.nvd import HISTORY_FEED, read_window_pages
        from lakehouse.nvd_parse import (
            HISTORY_ITEMS_KEY,
            NVD_CVE_HISTORY_SCHEMA,
            changes_to_arrow,
            count_outside_window,
        )
        from lakehouse.partition import partition_key

        window_date = partition_key(data_interval_start)
        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        pages, fetched_at, source_run = read_window_pages(
            s3,
            feed=HISTORY_FEED,
            window_date=window_date,
            items_key=HISTORY_ITEMS_KEY,
        )
        arrow = changes_to_arrow(pages, window_date, fetched_at)

        catalog = catalog_from_conns(
            Connection.get("iceberg_catalog"), Connection.get(S3_CONN_ID)
        )
        table = ensure_table(catalog, IDENTIFIER, NVD_CVE_HISTORY_SCHEMA)
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
            # 관측 셀: 구간 밖 created가 0이 아니면 UTC 가정이나 원천 해석을 의심한다
            "created_outside_window": count_outside_window(
                arrow.column("created").to_pylist(), window_date
            ),
            # 변경 이벤트 수와 영향받은 CVE 수는 다른 단위다
            "distinct_cve_ids": len(set(arrow.column("cve_id").to_pylist())),
        }
        outlet_events[HISTORY_ASSET].extra = extra
        return extra

    land_raw_nvd_cve_history() >> load_nvd_cve_history()


nvd_cve_history()
