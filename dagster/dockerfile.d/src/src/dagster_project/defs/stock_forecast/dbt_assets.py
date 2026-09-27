"""주식예측 silver dbt 모델 자산.

단일 dbt 프로젝트(dbt_pipelines)에서 `models/stock_forecast`의 모델만 select로
소유한다. 공유 DbtProject·리소스는 common.dbt에서 참조한다.

🔴 **이 모듈이 이 서브프로젝트의 전부다** — `@asset`이 없다. silver가 두 원천을
가로질러 조인하므로 소유자를 원천 디렉터리가 아닌 별도 서브프로젝트에 둔 결과다.
`@dg.definitions`도 없으므로 "자산 모듈에 definitions를 두면 모듈 스코프 자산이
조용히 누락된다"는 규약과 충돌하지 않는다.

주의: Dagster context 클래스 identity 검사 때문에 자산 모듈에서는
`from __future__ import annotations`를 사용하지 않는다.
"""

from collections.abc import Iterator
from typing import Any

from dagster_dbt import DbtCliResource, dbt_assets

import dagster as dg
from dagster_project.common.dbt import dbt_project
from dagster_project.defs.stock_forecast.constants import DBT_SELECT


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select=DBT_SELECT,
)
def stock_forecast_dbt_models(
    context: dg.AssetExecutionContext, dbt: DbtCliResource
) -> Iterator[Any]:
    """models/stock_forecast의 dbt 모델을 빌드해 Iceberg에 적재한다."""
    yield from dbt.cli(["build"], context=context).stream()
