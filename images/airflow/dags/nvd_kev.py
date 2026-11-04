"""CISA KEV bronze 수집 — 매시간 카탈로그를 받아 바뀐 때만 전체 교체 적재한다.

Dagster `defs/nvd/`의 `nvd_kev` raw·bronze 자산 이관(미션 ②).
- 두 단계: `land_raw_nvd_kev`(CISA → S3 raw) >>
  `load_nvd_kev`(raw → Iceberg 전체 overwrite).
- 🔴 NVD 키를 쓰지 않는다 — 다른 호스트(cisa.gov)·CC0라 키가 없고,
  NVD 키 헤더를 남의 호스트로 보내지 않는다. 그래서 Pool `nvd_api`에도 넣지 않는다.
- 항상 GET하고 gzip sha256이 직전 커밋 회차와 같으면 쓰지 않는다(`skipped_unchanged`).
  적재 쪽은 테이블의 `fetched_at`이 최신 회차와 같으면 건너뛴다(스킵의 정본은 이쪽).
- 🔴 스킵은 `AirflowSkipException`이다 — 적재 태스크가 스킵되면
  outlet을 내지 않으려는 의도다. 스킵 상태에서 Asset event가 실제로 나가지 않는지는
  **미확인**이다(이 파일은 단정하지 않는다). 현재 KEV Asset의 소비자는 없다.
- 스케줄은 `catchup=False`·기본 타임존(KST)이다 — API에 날짜로 나가는 값이 없다.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pendulum
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.sdk import Asset, Connection, CronTriggerTimetable, Param, dag, task
from airflow.sdk.exceptions import AirflowSkipException
from lakehouse.assets import NVD_KEV_URI

KEV_ASSET = Asset(NVD_KEV_URI)
IDENTIFIER = "nvd.nvd_kev"
S3_CONN_ID = "seaweedfs_s3"


@dag(
    schedule=CronTriggerTimetable("0 * * * *", timezone="Asia/Seoul"),
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={"force": Param(False, type="boolean")},
    tags=["bronze", "nvd"],
)
def nvd_kev() -> None:
    """CISA KEV bronze DAG."""

    @task
    def land_raw_nvd_kev(params: Any = None) -> dict:
        """카탈로그를 받아 내용이 바뀌었으면 새 회차로 S3 raw에 놓는다."""
        from lakehouse.nvd import land_kev

        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        return land_kev(
            s3,
            fetched_at=datetime.now(tz=timezone.utc),
            force=bool(params["force"]),
        )

    @task(outlets=[KEV_ASSET])
    def load_nvd_kev(outlet_events: Any = None) -> dict:
        """최신 커밋 회차가 테이블에 없으면 전체 교체하고, 같으면 스킵한다."""
        from lakehouse.iceberg import (
            catalog_from_conns,
            ensure_table,
            overwrite_table,
        )
        from lakehouse.nvd import read_latest_kev
        from lakehouse.nvd_parse import (
            NVD_KEV_SCHEMA,
            kev_needs_load,
            kev_to_arrow,
            table_fetched_at,
        )

        s3 = S3Hook(aws_conn_id=S3_CONN_ID).get_conn()
        payload, fetched_at, source_object = read_latest_kev(s3)

        catalog = catalog_from_conns(
            Connection.get("iceberg_catalog"), Connection.get(S3_CONN_ID)
        )
        table = ensure_table(catalog, IDENTIFIER, NVD_KEV_SCHEMA)
        # 🔴 스킵 판정의 정본은 land의 `skipped_unchanged`가 아니라
        #    **테이블의 fetched_at**이다 — 직전 시각에 raw만 쓰고 적재가 죽었다면
        #    land는 「변경 없음」을 내도 테이블은 낡았다.
        #    내용이 같아 raw를 새로 쓰지 않은 시각에는 이 판정이 곧 스킵이 된다
        if not kev_needs_load(table_fetched_at(table), fetched_at):
            message = "테이블이 이미 최신 KEV 회차다 — 적재하지 않는다"
            raise AirflowSkipException(message)

        arrow = kev_to_arrow(payload, fetched_at)
        loaded = overwrite_table(table, arrow)

        extra = {
            "rows": loaded["rows"],
            # KEV는 CC0라 카탈로그 버전을 남겨도 된다(docs/security/datasets.md)
            "catalog_version": str(payload["catalogVersion"]),
            "total_results": int(payload["count"]),
            "with_ransomware_field": sum(
                1
                for value in arrow.column("known_ransomware_campaign_use").to_pylist()
                if value is not None
            ),
            # 🔴 단위: 회차 안의 **객체**(kev.json.gz)다
            #    — CVE·History의 `source_run`(prefix)과 다르다
            "source_object": source_object,
        }
        outlet_events[KEV_ASSET].extra = extra
        return extra

    land_raw_nvd_kev() >> load_nvd_kev()


nvd_kev()
