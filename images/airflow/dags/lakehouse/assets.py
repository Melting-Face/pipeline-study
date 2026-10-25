"""DAG 간에 공유하는 Asset URI — 단일 출처.

🔴 DAG 모듈을 다른 DAG에서 import하면 그 DAG가 두 번 등록된다
(`AirflowDagDuplicatedIdException`, 실측).
그래서 URI는 여기 두고 각 DAG가 `Asset(<URI>)`를 만든다. URI가 같으면 같은 Asset이다.
"""

FX_RATES_URI = "iceberg://frankfurter_fx/fx_rates_daily"
EQUITY_OHLCV_URI = "iceberg://polygon_market/equity_ohlcv_daily"
US_EQUITY_KRW_URI = "iceberg://us_equity_krw/us_equity_krw__close_daily"
