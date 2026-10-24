"""미국 주식 원화 종가 서브프로젝트 전용 상수.

🔴 **이 서브프로젝트에는 `@asset`이 없다** — `stock_forecast`와 같은 구조다.
원화 종가가 두 원천(`polygon_market` 시세·`frankfurter_fx` 환율)을 가로질러 조인하므로
어느 원천 디렉터리에도 둘 수 없고, 여기는 dbt 모델 소유권만 갖는다.
"""

# dbt 셀렉터. 🔴 `fqn:`을 쓴다 — `path:`는 cwd 기준 글롭이라 조용히 0개를 수집한다.
DBT_SELECT = "fqn:us_equity_krw"

# dbt 모델이 적재될 Iceberg 네임스페이스. `dbt_project.yml`의 `+schema`와 같아야 한다
# (문서·조회용 — 실제 값은 dbt가 정한다).
SCHEMA = "us_equity_krw"

# Dagster 그룹. 🔴 `dbt_project.yml`에서 전역 `dbt_ingest`를 재정의한 값과 같아야 한다.
GROUP_NAME = "us_equity_krw"

# 일 1회 빌드 시각(KST). 🔴 **두 원천 스케줄보다 뒤**여야 같은 날의 시세·환율이 다 있다.
#   - 환율 `frankfurter_fx`  : 01:00 UTC = 10:00 KST
#   - 시세 `polygon_market`  : 09:00 UTC = 18:00 KST
# 시세 적재가 끝날 여유를 두고 19:00 KST로 둔다. 원천 시각을 옮기면 이 값도 다시 본다.
SCHEDULE_CRON_KST = "0 19 * * *"
