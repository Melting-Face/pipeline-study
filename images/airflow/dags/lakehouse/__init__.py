"""레이크하우스 수집·적재 공용 패키지(Airflow DAG용).

🔴 **Airflow를 import하지 않는다.** Connection 조회는 DAG 파일이 하고
이 패키지는 값만 받는다. 그래야 호스트 venv에서 인프라 없이 단위 테스트가
돈다(`images/airflow/tests/lib/`).

Dagster `dagster_project/common/`·`defs/*/assets.py`에서 이식했다(설계 F3).
뉴스·FRED가 Dagster에 남아 같은 로직을 쓰므로 **두 벌이 당분간 공존**하고,
스키마 갈라짐은 `test_parity.py`가 막는다.
"""
