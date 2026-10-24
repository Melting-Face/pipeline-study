"""원화 종가 dbt 빌드 — 환율·시세 bronze가 모두 갱신되면 모델 3개를 빌드한다.

Dagster `defs/us_equity_krw/dbt_assets.py`(`@dbt_assets`) 이관(설계 F4·F5·F8·F10).
- 🔴 스케줄은 두 Asset의 AND다. 환율은 주말에도 갱신되므로 OR이면
  낡은 시세 위에서 빌드한다.
- `equity_prices_daily`(silver)는 이 DAG가 소유한다(F10).
  Dagster `stock_forecast`는 컷오버에서 exclude한다.
- 최신 거래일은 날짜·행 수만 남긴다(원천 값 금지 — Polygon 재배포 금지).
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
from cosmos.constants import LoadMode
from lakehouse.assets import EQUITY_OHLCV_URI, FX_RATES_URI, US_EQUITY_KRW_URI

FX_ASSET = Asset(FX_RATES_URI)
OHLCV_ASSET = Asset(EQUITY_OHLCV_URI)
KRW_ASSET = Asset(US_EQUITY_KRW_URI)
DBT_DIR = "/opt/airflow/dbt/dbt_pipelines"
DBT_EXECUTABLE = "/opt/dbt-venv/bin/dbt"
# 이 DAG가 소유하는 모델 — Dagster `@dbt_assets(select=...)`와 같은 역할.
# 🔴 Cosmos의 `fqn:`은 **완전 일치**만 한다(dbt는 접두 일치).
#    `fqn:us_equity_krw`는 0개를 고른다(실측). Cosmos `path:`는 프로젝트 기준이라
#    Dagster의 cwd 글롭 문제가 없다.
DBT_SELECT = ["path:models/us_equity_krw", "equity_prices_daily"]


@dag(
    schedule=FX_ASSET & OHLCV_ASSET,
    start_date=pendulum.datetime(2024, 10, 9, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["gold", "us_equity_krw", "dbt"],
)
def us_equity_krw_dbt() -> None:
    """원화 종가 dbt DAG."""
    models = DbtTaskGroup(
        group_id="dbt",
        # 🔴 install_dbt_deps=False — Cosmos 기본값은 태스크마다 dbt deps로
        #    hub.getdbt.com에 나간다. 끄면 이미지에 구운 dbt_packages를
        #    심볼릭 링크로 쓴다(cosmos create_symlinks, 최종 리뷰 I2).
        project_config=ProjectConfig(
            DBT_DIR,
            manifest_path=f"{DBT_DIR}/target/manifest.json",
            install_dbt_deps=False,
        ),
        profile_config=ProfileConfig(
            profile_name="dbt_pipelines",
            target_name="dev",
            profiles_yml_filepath=f"{DBT_DIR}/profiles.yml",
        ),
        execution_config=ExecutionConfig(dbt_executable_path=DBT_EXECUTABLE),
        render_config=RenderConfig(
            load_method=LoadMode.DBT_MANIFEST, select=DBT_SELECT
        ),
    )

    @task(outlets=[KRW_ASSET])
    def record_latest_trade_date(outlet_events: Any = None) -> dict:
        """원화 종가의 최신 거래일·행 수를 Asset event에 남긴다(성공 기준 ②)."""
        import trino

        conn = trino.dbapi.connect(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ.get("TRINO_PORT", "8080")),
            user="airflow",
            catalog="iceberg",
        )
        cur = conn.cursor()
        cur.execute(
            "select max(trade_date), count(*)"
            " from us_equity_krw.us_equity_krw__close_daily"
        )
        latest, rows = cur.fetchone()
        conn.close()
        extra = {"latest_trade_date": latest, "rows": rows}
        outlet_events[KRW_ASSET].extra = extra
        return extra

    models >> record_latest_trade_date()


us_equity_krw_dbt()
