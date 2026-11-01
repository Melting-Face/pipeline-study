"""DAG 간에 공유하는 Asset URI — 단일 출처.

🔴 DAG 모듈을 다른 DAG에서 import하면 그 DAG가 두 번 등록된다
(`AirflowDagDuplicatedIdException`, 실측).
그래서 URI는 여기 두고 각 DAG가 `Asset(<URI>)`를 만든다. URI가 같으면 같은 Asset이다.
"""

FX_RATES_URI = "iceberg://frankfurter_fx/fx_rates_daily"
EQUITY_OHLCV_URI = "iceberg://polygon_market/equity_ohlcv_daily"
US_EQUITY_KRW_URI = "iceberg://us_equity_krw/us_equity_krw__close_daily"
# 🔴 NVD bronze 둘은 **Airflow가 아니라 Dagster `defs/nvd/`가 적재한다** — Airflow 안에
#    이 Asset의 생산자가 없어 nvd_dbt의 Asset 스케줄은 저절로 돌지 않는다(수동 트리거).
#    bronze를 Airflow로 이관하면 그 DAG가 같은 URI를 outlet으로 내어 그대로 이어진다.
NVD_CVES_URI = "iceberg://nvd/nvd_cves"
NVD_CVE_HISTORY_URI = "iceberg://nvd/nvd_cve_history"
NVD_SILVER_URI = "iceberg://nvd/nvd_cve_latest"
