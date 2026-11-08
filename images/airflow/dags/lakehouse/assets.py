"""DAG 간에 공유하는 Asset URI — 단일 출처.

🔴 DAG 모듈을 다른 DAG에서 import하면 그 DAG가 두 번 등록된다
(`AirflowDagDuplicatedIdException`, 실측).
그래서 URI는 여기 두고 각 DAG가 `Asset(<URI>)`를 만든다. URI가 같으면 같은 Asset이다.
"""

FX_RATES_URI = "iceberg://frankfurter_fx/fx_rates_daily"
EQUITY_OHLCV_URI = "iceberg://polygon_market/equity_ohlcv_daily"
US_EQUITY_KRW_URI = "iceberg://us_equity_krw/us_equity_krw__close_daily"
# NVD bronze 셋은 `nvd_cves`·`nvd_cve_history`·`nvd_kev` DAG가
# bronze 적재 태스크의 outlet으로 낸다.
# nvd_dbt의 Asset 스케줄이 앞의 둘을 소비한다. KEV는 alerts_dbt가 소비한다.
NVD_CVES_URI = "iceberg://nvd/nvd_cves"
NVD_CVE_HISTORY_URI = "iceberg://nvd/nvd_cve_history"
NVD_KEV_URI = "iceberg://nvd/nvd_kev"
# 생산자 nvd_dbt, 소비자 alerts_dbt.
NVD_SILVER_URI = "iceberg://nvd/nvd_cve_latest"
GEEKNEWS_ITEMS_URI = "iceberg://geeknews/geeknews_items"
# 생산자 geeknews_dbt, 소비자 alerts_dbt.
GEEKNEWS_SILVER_URI = "iceberg://geeknews/geeknews_items_latest"
# 생산자 alerts_dbt(gold 두 모델 빌드 뒤 1회). 소비자는 Discord 발신 DAG
# (미션 ③ 다음 단계 — 아직 없다). 대표 URI는 발신이 직접 읽는
# geeknews__cve_links다(digest는 그 상류).
ALERTS_GOLD_URI = "iceberg://geeknews/geeknews__cve_links"
