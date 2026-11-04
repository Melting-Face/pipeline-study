"""GeekNews bronze 수집 — Atom 피드를 매시 받아 raw(S3)와 Iceberg에 남긴다.

로직은 `lakehouse/geeknews.py`(Airflow 비의존)에 있고 이 파일은 배선만 한다.
- 스케줄은 매시 17분(KST) — 정각 몰림을 피한다.
- 직전 bronze 적재분과 피드 해시가 같으면 `AirflowSkipException`으로 끝낸다.
  성공으로 끝내면 Asset 이벤트가 매시 나가 하류 silver가 매시 재빌드된다.
  스킵 태스크가 Asset 이벤트를 내는지는 미확인 (manual check M4).
- 원천 값은 로그·Asset event에 남기지 않는다 — 행 수·키·해시 접두만.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pendulum
from airflow.sdk import Asset, Connection, dag, task
from airflow.sdk.exceptions import AirflowSkipException
from lakehouse.assets import GEEKNEWS_ITEMS_URI

GEEKNEWS_ASSET = Asset(GEEKNEWS_ITEMS_URI)


@dag(
    schedule="17 * * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["bronze", "geeknews"],
)
def geeknews_items() -> None:
    """GeekNews bronze DAG."""

    @task(outlets=[GEEKNEWS_ASSET])
    def load_geeknews_items(outlet_events: Any = None) -> dict:
        """피드를 받아 직전과 다르면 raw → bronze append 한다."""
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook
        from lakehouse.geeknews import (
            FEED_URL,
            GEEKNEWS_SCHEMA,
            IDENTIFIER,
            USER_AGENT,
            ingest_feed,
        )
        from lakehouse.http import request_bytes
        from lakehouse.iceberg import (
            S3Conf,
            catalog_uri,
            ensure_table,
            make_catalog,
        )

        fetched_at = datetime.now(tz=timezone.utc)
        body = request_bytes(
            FEED_URL,
            params={},
            headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
        )

        cat = Connection.get("iceberg_catalog")
        s3c = Connection.get("seaweedfs_s3")
        s3conf = S3Conf(
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
        table = ensure_table(make_catalog(uri, s3conf), IDENTIFIER, GEEKNEWS_SCHEMA)
        s3 = S3Hook(aws_conn_id="seaweedfs_s3").get_conn()

        result = ingest_feed(s3, table, body, fetched_at)
        if result is None:
            msg = "피드 변경 없음"
            raise AirflowSkipException(msg)
        outlet_events[GEEKNEWS_ASSET].extra = result
        return result

    load_geeknews_items()


geeknews_items()
