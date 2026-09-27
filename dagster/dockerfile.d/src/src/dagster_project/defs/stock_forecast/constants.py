"""주식예측 silver 서브프로젝트 전용 상수.

🔴 **이 서브프로젝트에는 `@asset`이 없다.** 다른 `defs/<dataset>/`은 bronze 적재
자산을 갖지만 여기는 **dbt 모델의 소유권만** 갖는다 — silver가 두 원천
(`polygon_market`·`fred_calendar`)을 가로질러 조인하므로 어느 한 원천
디렉터리에 둘 수 없기 때문이다. 그래서 Iceberg 테이블 리소스도, 네임스페이스
상수도 여기서는 필요 없다(dbt가 `+schema`로 정한다).
"""

# dbt 셀렉터. 🔴 `fqn:`을 쓴다 — `path:`는 정의 빌드 시 cwd 기준 파일시스템
# 글롭이라 "does not match any enabled nodes"로 **조용히 0개를 수집**한다.
DBT_SELECT = "fqn:stock_forecast"

# dbt 모델이 적재될 Iceberg 네임스페이스. `dbt_project.yml`의 `+schema`와 같아야
# 하며, 이 상수는 문서·조회용이다(dbt가 실제 값을 정한다).
SCHEMA = "stock_forecast"

# Dagster 그룹. 🔴 전역 기본값 `dbt_ingest`를 재정의한 값과 같아야 한다 —
# 어긋나면 `dbt_all_job`이 매시각 이 모델들을 끌고 간다.
GROUP_NAME = "stock_forecast_silver"
