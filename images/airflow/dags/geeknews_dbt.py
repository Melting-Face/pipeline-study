"""GeekNews silver dbt 빌드 — bronze가 갱신되면 topic_id당 최신 1건 silver를 빌드.

`nvd_dbt.py`(Cosmos)와 같은 모양이다(docs/conventions/airflow.md).
- 상류 bronze는 Airflow DAG `geeknews_items`가 적재한다(Asset 트리거).
- 결과는 행 수·최신 수집 시각만 남긴다(원천 값 — 제목·링크 — 은 남기지 않는다).
"""

import os
from datetime import timedelta
from typing import Any

import pendulum
from airflow.sdk import Asset, dag, task
from cosmos import (
    DbtTaskGroup,
    ExecutionConfig,
    ProfileConfig,
    ProjectConfig,
    RenderConfig,
)
from cosmos.constants import LoadMode, TestIndirectSelection
from lakehouse.assets import GEEKNEWS_ITEMS_URI, GEEKNEWS_SILVER_URI

ITEMS_ASSET = Asset(GEEKNEWS_ITEMS_URI)
SILVER_ASSET = Asset(GEEKNEWS_SILVER_URI)
DBT_DIR = "/opt/airflow/dbt/dbt_pipelines"
DBT_EXECUTABLE = "/opt/dbt-venv/bin/dbt"
# 🔴 Cosmos `fqn:`은 완전 일치만 해 디렉터리를 못 고른다 — `path:`를 쓴다
#    (us_equity_krw_dbt.py).
DBT_SELECT = ["path:models/geeknews"]
PROFILE = ProfileConfig(
    profile_name="dbt_pipelines",
    target_name="dev",
    profiles_yml_filepath=f"{DBT_DIR}/profiles.yml",
)


@dag(
    schedule=[ITEMS_ASSET],
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    # 🔴 태스크 동시 1 — 1Gi Celery 워커 OOM(us_equity_krw_dbt.py 주석·#284)
    max_active_tasks=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["silver", "geeknews", "dbt"],
)
def geeknews_dbt() -> None:
    """GeekNews silver dbt DAG."""
    models = DbtTaskGroup(
        group_id="dbt",
        # install_dbt_deps=False — 이미지에 구운 dbt_packages를 쓴다
        # (us_equity_krw_dbt.py)
        project_config=ProjectConfig(
            DBT_DIR,
            manifest_path=f"{DBT_DIR}/target/manifest.json",
            install_dbt_deps=False,
        ),
        profile_config=PROFILE,
        # cautious — 부모가 모두 선택된 테스트만 돌린다(us_equity_krw_dbt.py)
        execution_config=ExecutionConfig(
            dbt_executable_path=DBT_EXECUTABLE,
            test_indirect_selection=TestIndirectSelection.CAUTIOUS,
        ),
        render_config=RenderConfig(
            load_method=LoadMode.DBT_MANIFEST, select=DBT_SELECT
        ),
    )

    @task(outlets=[SILVER_ASSET])
    def record_geeknews_silver(outlet_events: Any = None) -> dict:
        """Silver 행 수·최신 수집 시각을 Asset event에 남긴다(값은 남기지 않는다)."""
        import trino

        conn = trino.dbapi.connect(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ.get("TRINO_PORT", "8080")),
            user="airflow",
            catalog="iceberg",
        )
        cur = conn.cursor()
        cur.execute("select count(*) from geeknews.geeknews_items_latest")
        rows = cur.fetchone()[0]
        cur.execute("select max(fetched_at) from geeknews.geeknews_items_latest")
        latest = cur.fetchone()[0]
        conn.close()
        extra: dict[str, Any] = {
            "geeknews_items_latest_rows": rows,
            "latest_fetched_at": latest.isoformat() if latest else None,
        }
        outlet_events[SILVER_ASSET].extra = extra
        return extra

    models >> record_geeknews_silver()


geeknews_dbt()
