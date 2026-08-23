#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Iceberg changelog 판독 관문 + 스트림 소스 적격성 진단 (실인프라 수동 관문).

**무엇을 하는가.** 두 가지를 한 번에 한다.

1. **관문(gate)** — 전용 네임스페이스에 프로브 테이블을 만들어 Spark Iceberg 프로시저
   `create_changelog_view`가 **기대한 행 수·변경 종류**를 내는지 확인한다.
2. **진단(report)** — 카탈로그의 **기존 테이블**을 훑어 각각이 changelog·스트리밍
   소스로 쓸 수 있는지 판정한다. 읽기 전용이며 종료 코드를 바꾸지 않는다.

**왜 진단이 붙어 있는가.** 소스 적격성은 "테이블이 있다"로 결정되지 않는다. 축이 둘이다.

- **스냅샷 연산 종류** — Iceberg **Flink 스트리밍 읽기는 `IncrementalAppendScan`
  기반이라 append 스냅샷만** 본다. `UPDATE`/`MERGE`/`INSERT OVERWRITE`는 `overwrite`
  스냅샷을 남기므로 그런 테이블은 스트림 소스로 쓸 수 없다. 반면 **Spark
  `create_changelog_view`는 overwrite도 읽는다** — 같은 "변경분"이라는 말 아래
  두 엔진의 허용 범위가 다르다.
- **스냅샷 계보(lineage)** — 🔴 `.snapshots`를 `committed_at`으로 정렬한 **인접 두
  행이 부모-자식이라는 보장이 없다.** `writeTo(...).createOrReplace()`로 쓴 테이블은
  매 실행이 `parent_id = NULL`인 **새 루트**를 만들어 이전 스냅샷이 전부
  `is_current_ancestor = false`가 된다. 그런 테이블에 시간순 인접 스냅샷으로 창을
  잡으면 `Starting snapshot (exclusive) ... is not a parent ancestor of end
  snapshot`으로 죽는다.
  📌 **"멱등"은 데이터 축이다.** `createOrReplace`는 결과 데이터를 멱등하게 만들지만
  **계보를 매번 끊어** 증분·changelog 읽기를 구조적으로 불가능하게 한다.
  두 축을 같은 말로 덮지 않는다.

그래서 이 스크립트는 창을 **`.history`의 `parent_id`로 직접 걸어** 잡는다
(`committed_at` 정렬에 기대지 않는다).

**왜 CI 상시 게이트가 아닌가.** 실인프라(Spark Connect·Iceberg 카탈로그·SeaweedFS)에
실제로 붙어야 의미가 있다. `scripts/spark_connect_smoke.py`와 같은 **의도된 격리
예외**이며, changelog 경로를 건드리기 직전의 수동 관문으로 쓴다.

사용:
    kubectl scale deploy/spark-connect --replicas=1
    kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널
    uv run scripts/iceberg_changelog_probe.py

종료 코드: 0 = 관문 통과 / 1 = 회귀 발견 / 2 = 사전 조건 미충족(판정 불가).
🔴 2와 1을 구분하는 이유는 `spark_connect_smoke.py`와 같다 — 포트가 닫혀 못 붙은 것을
"실패"로 읽으면 회귀가 아닌데 회귀로 오진하고, "통과"로 읽으면 관측 경로가 죽은 채
통과가 된다.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# 이 스크립트는 repo 루트의 `scripts/`에 있다.
REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = (
    REPO_ROOT / "dagster" / "dockerfile.d" / "src" / ".venv" / "bin" / "python"
)

DEFAULT_REMOTE = "sc://localhost:15002"
CONNECT_TIMEOUT_SEC = 5
CATALOG = "iceberg"

# 실데이터 네임스페이스(mimiciv·eicu·poc)와 절대 겹치지 않는 전용 네임스페이스.
# 관문이 끝나면 통째로 지운다.
PROBE_SCHEMA = "chglog"
PROBE_TABLE = "probe"

# 프로브가 만드는 변경 시나리오 — 기대값의 **근거**를 값 옆에 둔다(계측 단위).
#   s1: append 3행 → 최초 적재
#   s2: append 2행 → 증분 적재  ⇒ s1→s2 창의 기대 변경분은 2건, 전부 INSERT
#   s3: update 1행 → 값 정정    ⇒ s2→s3 창은 identifier_columns 유무로 표현이 갈린다
APPEND_2 = 2
UPDATED_ROWS = 1
EXPECTED_SNAPSHOTS = 3


def main() -> int:
    """관문(프로브)과 진단(기존 테이블)을 차례로 수행하고 종료 코드를 돌려준다."""
    # --- 0. 사전 조건 — "못 붙었다"와 "회귀다"를 구분한다 ---
    if not VENV_PYTHON.exists():
        print(f"✗ 사전 조건 미충족 — venv 없음: {VENV_PYTHON}", file=sys.stderr)
        print("  `cd dagster/dockerfile.d/src && uv sync` 후 재실행", file=sys.stderr)
        return 2

    remote = os.environ.get("SPARK_REMOTE", DEFAULT_REMOTE)
    parsed = urlparse(remote)
    host, port = parsed.hostname or "localhost", parsed.port or 15002
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC):
            pass
    except OSError as exc:
        print(f"✗ 사전 조건 미충족 — {host}:{port} 도달 불가 ({exc})", file=sys.stderr)
        print(
            "  kubectl scale deploy/spark-connect --replicas=1\n"
            "  kubectl port-forward svc/spark-connect 15002:15002",
            file=sys.stderr,
        )
        return 2
    print(f"✓ 사전 조건 — venv·{host}:{port} 도달 확인 (remote={remote})")

    # pyspark 핀(`pyspark[connect]>=3.5.9,<3.6`)의 단일 출처는 프로젝트 pyproject.toml
    # 이다. 여기서 PEP 723으로 다시 선언하면 핀이 두 곳이 되므로,
    # `spark_connect_smoke.py`와 같은 취지로 **프로젝트 venv 파이썬에 위임**한다 —
    # 다만 별도 워커 파일을 두지 않고 자기 자신을 그 인터프리터로 다시 실행해
    # "실행 순서 = 읽는 순서"를 유지한다.
    if importlib.util.find_spec("pyspark") is None:
        print(f"→ pyspark 부재, 프로젝트 venv로 재실행: {VENV_PYTHON}")
        return subprocess.run(  # noqa: S603
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            check=False,
        ).returncode

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.remote(remote).getOrCreate()
    catalog = spark.conf.get("spark.sql.defaultCatalog", "<unset>")
    print(f"✓ 접속 — Spark {spark.version} / defaultCatalog={catalog}")

    failures: list[str] = []
    qualified = f"{CATALOG}.{PROBE_SCHEMA}.{PROBE_TABLE}"
    short = f"{PROBE_SCHEMA}.{PROBE_TABLE}"

    # --- 1. 프로브 테이블 — 알려진 변경 3건을 만든다 ---
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.{PROBE_SCHEMA}")
    spark.sql(f"DROP TABLE IF EXISTS {qualified}")
    spark.sql(f"CREATE TABLE {qualified} (id INT, name STRING) USING iceberg")
    spark.sql(f"INSERT INTO {qualified} VALUES (1,'a'),(2,'b'),(3,'c')")  # noqa: S608
    spark.sql(f"INSERT INTO {qualified} VALUES (4,'d'),(5,'e')")  # noqa: S608
    spark.sql(f"UPDATE {qualified} SET name='B' WHERE id=2")  # noqa: S608

    # 🔴 창은 `.history`의 parent_id로 계보를 따라 잡는다
    #    (committed_at 정렬에 기대지 않는다 — 모듈 docstring 참고).
    q_hist = (
        f"SELECT snapshot_id, is_current_ancestor FROM {qualified}.history "  # noqa: S608
        f"ORDER BY made_current_at"
    )
    rows_hist = spark.sql(q_hist).collect()
    chain = [r.snapshot_id for r in rows_hist if r.is_current_ancestor]
    q_ops = f"SELECT snapshot_id, operation FROM {qualified}.snapshots"  # noqa: S608
    live = set(chain)
    ops = [r.operation for r in spark.sql(q_ops).collect() if r.snapshot_id in live]
    print(f"✓ 프로브 — 계보 {len(chain)}개 스냅샷, ops={ops}")
    if len(chain) != EXPECTED_SNAPSHOTS:
        failures.append(f"프로브 계보가 {EXPECTED_SNAPSHOTS}개가 아님({len(chain)}개)")
        chain += [None] * (EXPECTED_SNAPSHOTS - len(chain))

    s1, s2, s3 = chain[0], chain[1], chain[2]

    # --- 2~4. 판정 3건 — 창과 옵션에 따라 변경 종류가 어떻게 표현되는가 ---
    #   A: append 창은 append 건수와 같아야 한다
    #   B: identifier_columns가 없으면 갱신이 DELETE+INSERT로 풀린다
    #   C: identifier_columns를 주면 UPDATE_BEFORE/AFTER로 접힌다
    cases = (
        ("A) append 창 s1→s2   ", s1, s2, "", {"INSERT": APPEND_2}),
        ("B) 갱신 창(식별자 없음)", s2, s3, "", {"DELETE": 1, "INSERT": 1}),
        (
            "C) 갱신 창(식별자 id) ",
            s2,
            s3,
            "identifier_columns => array('id'), ",
            {"UPDATE_BEFORE": UPDATED_ROWS, "UPDATE_AFTER": UPDATED_ROWS},
        ),
    )
    for idx, (label, start, end, extra, want) in enumerate(cases):
        view = f"cl_{idx}"
        spark.sql(
            f"CALL {CATALOG}.system.create_changelog_view("
            f"table => '{short}', "
            f"options => map('start-snapshot-id','{start}',"
            f"'end-snapshot-id','{end}'), "
            f"{extra}"
            f"changelog_view => '{view}')"
        )
        q_cnt = f"SELECT _change_type t, count(*) c FROM {view} GROUP BY 1"  # noqa: S608
        got = {r.t: r.c for r in spark.sql(q_cnt).collect()}
        print(f"  {label}: {got}  (기대 {want})")
        if got != want:
            failures.append(f"{label.strip()}: got={got} want={want}")

    # --- 5. 진단 — 카탈로그의 기존 테이블이 어떤 소스로 쓸 수 있는가 ---
    # 🔴 이 표는 관문이 아니다(종료 코드를 바꾸지 않는다).
    #    소스를 **고르기 위한** 관측이다.
    print("\n소스 적격성 진단 (읽기 전용)")
    head = f"  {'테이블':26s} {'행수':>7} {'계보':>10} {'ops':18s}"
    print(f"{head} spark_cl  flink_stream")
    q_ns = f"SHOW NAMESPACES IN {CATALOG}"
    namespaces = [r[0] for r in spark.sql(q_ns).collect() if r[0] != PROBE_SCHEMA]
    for ns in namespaces:
        for row in spark.sql(f"SHOW TABLES IN {CATALOG}.{ns}").collect():
            tbl = f"{CATALOG}.{ns}.{row.tableName}"
            q_rows = f"SELECT count(*) c FROM {tbl}"  # noqa: S608
            n_rows = spark.sql(q_rows).collect()[0].c
            q_h = f"SELECT snapshot_id, is_current_ancestor FROM {tbl}.history"  # noqa: S608
            hist = spark.sql(q_h).collect()
            alive = {r.snapshot_id for r in hist if r.is_current_ancestor}
            orphan = len(hist) - len(alive)
            q_o = f"SELECT snapshot_id, operation FROM {tbl}.snapshots"  # noqa: S608
            rows_o = spark.sql(q_o).collect()
            tops = sorted({r.operation for r in rows_o if r.snapshot_id in alive})
            # Spark changelog는 overwrite도 읽는다 → 계보에 스냅샷이 있으면 가능.
            spark_cl = "가능" if alive else "불가"
            # Flink 스트리밍 읽기는 append 스냅샷만 본다.
            flink_cl = "가능" if alive and tops == ["append"] else "불가"
            lineage = f"{len(alive)}" + (f"(고아{orphan})" if orphan else "")
            name = f"{ns}.{row.tableName}"
            print(
                f"  {name:26s} {n_rows:>7} {lineage:>10} "
                f"{','.join(tops):18s} {spark_cl:9s} {flink_cl}"
            )

    # --- 6. 정리 — 프로브가 만든 것만 되돌린다 ---
    # 🔴 **`PURGE`가 없으면 카탈로그만 지워지고 S3 데이터 파일은 남는다.**
    #    2026-08-23 실측 — `PURGE` 없이 돌린 뒤 `s3://warehouse/chglog/`에
    #    parquet·metadata **64개(212.7 KiB)** 가 잔류했다.
    #    "정리했다"가 **카탈로그 축에서만** 참이 되고 스토리지에는 고아가 쌓인다
    #    (반복 실행할수록 누적). 두 축을 같은 말로 덮지 않는다(계측 단위).
    #    붙인 뒤 재실행하니 같은 prefix가 **0개**로 떨어졌다(변인 하나로 확인).
    #    📌 같은 결함이 `scripts/spark_connect_smoke.py`에도 있다
    #       (`s3://warehouse/smoke/` 잔류 — 이 스크립트 범위 밖이라 손대지 않는다).
    try:
        spark.sql(f"DROP TABLE IF EXISTS {qualified} PURGE")
        spark.sql(f"DROP NAMESPACE IF EXISTS {CATALOG}.{PROBE_SCHEMA}")
        print(f"\n✓ 정리 — {CATALOG}.{PROBE_SCHEMA} 삭제(PURGE — 데이터 파일 포함)")
    except Exception as exc:
        # 정리 실패는 회귀가 아니다 — 판정을 뒤집지 않고 사람에게만 알린다.
        print(f"\n⚠ 정리 실패 — 수동 삭제 필요: {exc}", file=sys.stderr)

    # --- 7. 판정 ---
    if failures:
        print("\n✗ 회귀 — changelog가 기대대로 동작하지 않는다:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        print(
            "  갈리는 지점: ① 프로시저 인자 계약 변경 ② 계보 판정 변경\n"
            "  ③ Iceberg 런타임 jar 버전 불일치.",
            file=sys.stderr,
        )
        return 1

    print("\n✓ 관문 통과 — Spark Iceberg changelog가 기대한 변경분을 낸다.")
    print(
        "🔴 이 통과가 보증하지 않는 것: **Flink 스트리밍 읽기는 별개 축이다.**\n"
        "   Spark changelog는 overwrite 스냅샷도 읽지만 Flink는 append만 본다 —\n"
        "   위 진단표 `flink_stream` 열이 그 축이고, 실제 검증은 Flink 잡으로만 닫힌다."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
