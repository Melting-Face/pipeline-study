"""Trino 조회 헬퍼 — DAG 태스크가 gold·silver를 읽을 때 쓴다.

- 접속 값은 env `TRINO_HOST`·`TRINO_PORT`(기본 8080)에서 읽는다. 기존 dbt DAG
  4곳(`*_dbt.py`)의 인라인 `trino.dbapi.connect`와 같은 값이다.
  그 4곳은 아직 이 헬퍼로 바꾸지 않았다(별도 리팩터 후보).
- 🔴 `trino` 패키지는 **함수 안에서** import한다. `lakehouse` 단위 테스트 환경
  (`tests/lib.test.sh`)은 DAG 전용 의존인 trino를 설치하지 않으므로, 모듈
  로드 시점에 import하면 이 패키지의 다른 테스트까지 깨진다.
"""

import os
from typing import Any

TRINO_USER = "airflow"
TRINO_CATALOG = "iceberg"
DEFAULT_PORT = "8080"


def query_rows(sql: str) -> list[dict[str, Any]]:
    """SQL 1개를 실행해 행을 `{컬럼: 값}` 목록으로 돌려준다. 연결은 매번 닫는다.

    호출자는 상수 SQL만 넘긴다(값을 끼워 넣지 않는다).
    """
    # 지연 import — 모듈 docstring(테스트 환경에 trino가 없다)
    import trino

    conn = trino.dbapi.connect(
        host=os.environ["TRINO_HOST"],
        port=int(os.environ.get("TRINO_PORT", DEFAULT_PORT)),
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [column[0] for column in cur.description]
    finally:
        conn.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]
