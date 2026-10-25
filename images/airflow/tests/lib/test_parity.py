"""Dagster ↔ Airflow 스키마 패리티(설계 F3 — 두 벌 공존의 갈라짐 방지).

🔴 스키마가 한쪽만 바뀌면 같은 Iceberg 테이블에 두 오케스트레이터가
다른 모양으로 쓴다.
Dagster 정의를 지우는 컷오버(계획 Task 8)에서 이 파일을 스키마 리터럴 비교로 바꾼다.
"""

from lakehouse import fx, polygon


def test_fx_schema_matches_dagster():
    from dagster_project.defs.frankfurter_fx.assets import FX_SCHEMA as DAGSTER

    assert fx.FX_SCHEMA.equals(DAGSTER, check_metadata=True)


def test_ohlcv_schema_matches_dagster():
    from dagster_project.defs.polygon_market.assets import OHLCV_SCHEMA as DAGSTER

    assert polygon.OHLCV_SCHEMA.equals(DAGSTER, check_metadata=True)
