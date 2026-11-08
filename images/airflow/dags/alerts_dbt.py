"""알림 gold dbt 빌드 — 상류 셋 중 하나가 갱신되면 `tag:alerts`를 빌드.

상류는 NVD silver·GeekNews silver·KEV bronze다.
`geeknews_dbt.py`(Cosmos)와 같은 모양이다(docs/conventions/airflow.md).
- 🔴 빌드 소유는 이 DAG 하나다. `tag:alerts` 모델(silver `nvd_kev_entries`, gold
  `nvd__cve_digest`·`geeknews__cve_links`)은 `nvd_dbt`·`geeknews_dbt`가
  RenderConfig `exclude`로 뺀다 — 같은 모델을 두 DAG가 빌드하면 replace가
  서로를 덮고 run이 겹친다.
- 스케줄은 세 Asset의 **OR**다. 상류마다 갱신 주기가 달라
  (KEV 매시간·NVD·GeekNews 일별) AND로 묶으면 가장 느린 상류에 맞춰 늦게 돈다.
  셋 중 어느 것이 바뀌어도 gold는 다시 맞다.
  ⚠️ 상류 silver(`nvd_cve_latest` 등)는 다른 DAG가 빌드한다 —
  이 DAG는 그것을 외부 상류로 읽는다.
- 결과는 두 gold의 행 수만 남긴다(원천 값 — 제목·링크·CVE ID — 은 남기지 않는다).
- 관심목록(watchlist)은 dbt에 없다 — 발신층 Python이 건다
  (gold는 비공개 입력 없이 재현된다).
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
from lakehouse.assets import (
    ALERTS_GOLD_URI,
    GEEKNEWS_SILVER_URI,
    NVD_KEV_URI,
    NVD_SILVER_URI,
)

GOLD_ASSET = Asset(ALERTS_GOLD_URI)
DBT_DIR = "/opt/airflow/dbt/dbt_pipelines"
DBT_EXECUTABLE = "/opt/dbt-venv/bin/dbt"
# 데이터셋 디렉터리를 가로지르는 선택이라 path:가 아니라 tag:다.
# Cosmos 1.15.1 manifest 로드도 노드의 tags로 `tag:`를 거른다
# (cosmos/dbt/selector.py `_is_tags_subset`).
DBT_SELECT = ["tag:alerts"]
PROFILE = ProfileConfig(
    profile_name="dbt_pipelines",
    target_name="dev",
    profiles_yml_filepath=f"{DBT_DIR}/profiles.yml",
)
# 행 수를 남길 gold 모델(스키마.모델)
GOLD_MODELS = (
    "nvd.nvd__cve_digest",
    "geeknews.geeknews__cve_links",
)


@dag(
    schedule=(Asset(NVD_SILVER_URI) | Asset(GEEKNEWS_SILVER_URI) | Asset(NVD_KEV_URI)),
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    # 🔴 태스크 동시 1 — 1Gi Celery 워커 OOM(us_equity_krw_dbt.py 주석·#284)
    max_active_tasks=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["gold", "alerts", "dbt"],
)
def alerts_dbt() -> None:
    """알림 gold dbt DAG."""
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

    @task(outlets=[GOLD_ASSET])
    def record_alerts_gold(outlet_events: Any = None) -> dict:
        """Gold 행 수만 Asset event에 남긴다(값은 남기지 않는다)."""
        import trino

        conn = trino.dbapi.connect(
            host=os.environ["TRINO_HOST"],
            port=int(os.environ.get("TRINO_PORT", "8080")),
            user="airflow",
            catalog="iceberg",
        )
        cur = conn.cursor()
        extra: dict[str, Any] = {}
        for model in GOLD_MODELS:
            cur.execute(f"select count(*) from {model}")  # noqa: S608 — 상수 목록
            extra[f"{model.split('.')[1]}_rows"] = cur.fetchone()[0]
        conn.close()
        outlet_events[GOLD_ASSET].extra = extra
        return extra

    models >> record_alerts_gold()


alerts_dbt()
