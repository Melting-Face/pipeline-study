"""NVD silver dbt 빌드 — CVE·History bronze가 모두 갱신되면 silver 5종을 빌드.

`us_equity_krw_dbt.py`(Cosmos)와 같은 모양이다(docs/conventions/airflow.md).
- 🔴 상류 bronze는 Airflow DAG `nvd_cves`·`nvd_cve_history`가 적재하고 각 bronze
  태스크의 outlet(`lakehouse/assets.py`의 URI)이 이 스케줄을 깨운다. 두 Asset이
  **모두** 갱신돼야 돈다(AND). cron으로 맞추지 않는 이유: 상류 완료 시각을 모른 채
  시각만 맞추면 빈 데이터 위에서 「돌았다」만 남긴다.
- 모델 간 테스트가 없어(relationships 미사용) 전용 테스트 태스크를 두지 않는다.
- 결과는 행 수·최신 수정 시각만 남긴다(원천 값은 남기지 않는다).
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
from lakehouse.assets import NVD_CVE_HISTORY_URI, NVD_CVES_URI, NVD_SILVER_URI

CVES_ASSET = Asset(NVD_CVES_URI)
HISTORY_ASSET = Asset(NVD_CVE_HISTORY_URI)
SILVER_ASSET = Asset(NVD_SILVER_URI)
DBT_DIR = "/opt/airflow/dbt/dbt_pipelines"
DBT_EXECUTABLE = "/opt/dbt-venv/bin/dbt"
# 🔴 Cosmos `fqn:`은 완전 일치만 해 디렉터리를 못 고른다 — `path:`를 쓴다
#    (us_equity_krw_dbt.py).
DBT_SELECT = ["path:models/nvd"]
# 🔴 `tag:alerts` 모델(nvd_kev_entries·nvd__cve_digest)은 alerts_dbt가 소유한다 —
#    여기서 빼야 같은 모델을 두 DAG가 빌드하지 않는다(alerts_dbt.py 독스트링).
DBT_EXCLUDE = ["tag:alerts"]
PROFILE = ProfileConfig(
    profile_name="dbt_pipelines",
    target_name="dev",
    profiles_yml_filepath=f"{DBT_DIR}/profiles.yml",
)
# 행 수를 남길 silver 모델(스키마 nvd)
SILVER_MODELS = (
    "nvd_cve_latest",
    "nvd_cve_metrics",
    "nvd_cve_cwe",
    "nvd_cve_cpe",
    "nvd_cve_changes",
)


@dag(
    schedule=CVES_ASSET & HISTORY_ASSET,
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    # 🔴 태스크 동시 1 — 1Gi Celery 워커 OOM(us_equity_krw_dbt.py 주석·#284)
    max_active_tasks=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["silver", "nvd", "dbt"],
)
def nvd_dbt() -> None:
    """NVD silver dbt DAG."""
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
            load_method=LoadMode.DBT_MANIFEST,
            select=DBT_SELECT,
            exclude=DBT_EXCLUDE,
        ),
    )

    @task(outlets=[SILVER_ASSET])
    def record_nvd_silver(outlet_events: Any = None) -> dict:
        """Silver 행 수·최신 수정 시각을 Asset event에 남긴다(미션 ③ 상류 신호)."""
        import trino

        conn = trino.dbapi.connect(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ.get("TRINO_PORT", "8080")),
            user="airflow",
            catalog="iceberg",
        )
        cur = conn.cursor()
        extra: dict[str, Any] = {}
        for model in SILVER_MODELS:
            cur.execute(f"select count(*) from nvd.{model}")  # noqa: S608 — 상수 목록
            extra[f"{model}_rows"] = cur.fetchone()[0]
        cur.execute("select max(last_modified) from nvd.nvd_cve_latest")
        latest = cur.fetchone()[0]
        conn.close()
        extra["latest_last_modified"] = latest.isoformat() if latest else None
        outlet_events[SILVER_ASSET].extra = extra
        return extra

    models >> record_nvd_silver()


nvd_dbt()
