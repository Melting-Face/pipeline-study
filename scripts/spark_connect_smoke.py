#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""dbt-spark ↔ Spark Connect 어댑터 스모크 (실인프라 수동 관문).

**왜 필요한가.** dbt-spark는 Spark Connect를 **공식 지원하지 않는다** —
`dbt/adapters/spark/connections.py`의 `SparkConnectionMethod`는 thrift/http/odbc/session
4개뿐이고 connect가 없다. 그런데도 동작하는 이유는 `session.py`가
`builder.config(k, v)` → `getOrCreate()`를 타서, pyspark classic 빌더가 `spark.remote`를
보고 RemoteSparkSession으로 위임하는 **내부 동작**에 얹히기 때문이다(2026-08-19 실측).
즉 이 경로는 **계약이 아니라 구현에 의존**한다 → `dbt-spark`·`pyspark` 업그레이드가
에러 없이 조용히 깨뜨릴 수 있다. 이 스크립트가 그 회귀를 잡는 유일한 관측 경로다.

**왜 CI 상시 게이트가 아닌가.** 실인프라(Spark Connect·Iceberg 카탈로그·SeaweedFS)에
실제로 붙어야만 의미가 있다 — 접속·DDL·merge·메타데이터 조회가 검증 대상이다.
`docs/test.md` §6 분석 재현성과 같은 **의도된 격리 예외**이며, 상시 CI가 아니라
**의존성 상한을 올리기 직전의 수동 관문**으로 쓴다.

사용:
    kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널
    uv run scripts/spark_connect_smoke.py

종료 코드: 0 = 전 항목 통과 / 1 = 회귀 발견 / 2 = 사전 조건 미충족(판정 불가).
🔴 2와 1을 구분하는 이유: 포트가 닫혀 못 붙은 것을 "실패"로 읽으면 회귀가 아닌데
회귀로 오진하고, "통과"로 읽으면 관측 경로가 죽은 채 통과가 된다(철학 원칙 7).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

# 이 스크립트는 repo 루트의 `scripts/`에 있다.
REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / "dagster" / "dockerfile.d" / "src" / ".venv"
DBT_BIN = VENV_DIR / "bin" / "dbt"
VENV_PYTHON = VENV_DIR / "bin" / "python"

DEFAULT_REMOTE = "sc://localhost:15002"
# 실데이터 네임스페이스(mimiciv·eicu·poc)와 절대 겹치지 않는 전용 네임스페이스.
SMOKE_SCHEMA = "smoke"
SMOKE_TABLES = ("smoke_seed", "smoke_incremental")
CONNECT_TIMEOUT_SEC = 5

FIXTURE_PROJECT = """\
name: "spark_connect_smoke"
version: "1.0.0"
profile: "smoke"
model-paths: ["models"]
"""

FIXTURE_PROFILE = """\
smoke:
  target: connect
  outputs:
    connect:
      type: spark
      method: session
      host: localhost
      schema: {schema}
      threads: 4
      server_side_parameters:
        spark.remote: "{remote}"
"""

FIXTURE_SEED = """\
{{ config(materialized='table', file_format='iceberg') }}

select 1 as id, 'a' as label, cast('2026-01-01' as date) as event_date
union all
select 2 as id, 'b' as label, cast('2026-01-02' as date) as event_date
"""

FIXTURE_INCREMENTAL = """\
{{
    config(
        materialized='incremental',
        file_format='iceberg',
        incremental_strategy='merge',
        unique_key='id',
    )
}}

select * from {{ ref('smoke_seed') }}

{% if is_incremental() %}
    where id > (select coalesce(max(id), 0) from {{ this }})
{% endif %}
"""

FIXTURE_SCHEMA = """\
version: 2

models:
  - name: smoke_seed
    columns:
      - name: id
        data_tests: [not_null, unique]
  - name: smoke_incremental
    columns:
      - name: id
        data_tests: [not_null]
"""

# 정리는 스모크가 만든 것만 지운다(전용 네임스페이스라 실데이터와 겹치지 않는다).
#
# 🔴 **`PURGE`가 없으면 카탈로그만 지워지고 S3 데이터 파일은 남는다.**
#    2026-08-23 실측 — `PURGE` 없이 반복 실행한 결과 `s3://warehouse/smoke/`에
#    parquet·metadata **85개(275.9 KiB)** 가 누적돼 있었다. 카탈로그에는 `smoke`가
#    없으므로 `SHOW TABLES`로 확인하면 **"정리됐다"가 참으로 보인다** —
#    참인 범위가 **카탈로그 축뿐**인데 전 범위로 읽히는 형태다.
#    이 스크립트는 의존성 상한을 올릴 때마다 도는 관문이라 **돌 때마다 누적**된다.
#    (같은 결함을 `scripts/iceberg_changelog_probe.py`에서 먼저 발견해 고쳤고,
#     거기서 `PURGE` 추가 → 재실행 시 같은 prefix가 0개로 떨어지는 것을 확인했다.)
CLEANUP_SNIPPET = """
from pyspark.sql import SparkSession

session = SparkSession.builder.remote({remote!r}).getOrCreate()
for table in {tables!r}:
    session.sql(f"DROP TABLE IF EXISTS iceberg.{schema}.{{table}} PURGE")
session.sql("DROP NAMESPACE IF EXISTS iceberg.{schema}")
print("cleanup-ok")
"""


def main() -> int:
    """스모크 전 항목을 위→아래 순서로 실행한다."""
    # --- 0. 사전 조건 (판정 불가와 실패를 여기서 갈라낸다) ---
    if not DBT_BIN.exists():
        print(f"[사전조건] dbt 실행 파일이 없다: {DBT_BIN}", file=sys.stderr)
        print("          cd dagster/dockerfile.d/src && uv sync", file=sys.stderr)
        return 2

    remote = os.environ.get("SPARK_REMOTE") or DEFAULT_REMOTE
    parsed = urlparse(remote)
    host, port = parsed.hostname or "localhost", parsed.port or 15002
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC):
            pass
    except OSError as exc:
        print(f"[사전조건] Connect 도달 실패 ({remote}): {exc}", file=sys.stderr)
        print(
            "          kubectl port-forward svc/spark-connect 15002:15002",
            file=sys.stderr,
        )
        return 2
    print(f"✓ 사전조건 — Spark Connect 도달 가능 ({remote})")

    # --- 1. 픽스처 dbt 프로젝트 생성 (실제 프로젝트를 건드리지 않는다) ---
    # 실 프로젝트 모델을 쓰면 매니페스트 전체 스키마를 훑느라 원천 데이터에 묶인다.
    # 스모크가 묻는 건 **어댑터 경로**이지 모델 내용이 아니라 최소 픽스처로 격리한다.
    workdir = Path(tempfile.mkdtemp(prefix="spark-connect-smoke-"))
    project = workdir / "project"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "dbt_project.yml").write_text(FIXTURE_PROJECT, encoding="utf-8")
    (project / "profiles.yml").write_text(
        FIXTURE_PROFILE.format(schema=SMOKE_SCHEMA, remote=remote), encoding="utf-8"
    )
    (models / "smoke_seed.sql").write_text(FIXTURE_SEED, encoding="utf-8")
    (models / "smoke_incremental.sql").write_text(FIXTURE_INCREMENTAL, encoding="utf-8")
    (models / "schema.yml").write_text(FIXTURE_SCHEMA, encoding="utf-8")
    print(f"✓ 픽스처 생성 — {project}")

    failures: list[str] = []
    # 🔴 `--profiles-dir`·`--project-dir`는 dbt 1.10에서 **서브커맨드 플래그**다.
    #    전역 자리에 두면 `No such option: --profiles-dir`로 죽는다(2026-08-19 실측).
    #    반대로 `--debug`는 전역이라 서브커맨드 앞에 와야 한다.
    dbt_bin = [str(DBT_BIN), "--no-use-colors"]
    dbt_dirs = ["--profiles-dir", str(project), "--project-dir", str(project)]

    # --- 2. 1회차 build — 접속·create table·테스트 경로 ---
    started = time.monotonic()
    first = subprocess.run(  # noqa: S603
        [*dbt_bin, "build", *dbt_dirs], capture_output=True, text=True, check=False
    )
    if first.returncode != 0:
        failures.append("1회차 `dbt build` 실패 (접속 또는 create table 경로)")
        print(first.stdout[-4000:], file=sys.stderr)
    else:
        took = time.monotonic() - started
        print(f"✓ 1회차 build — create table + 스키마 테스트 통과 ({took:.1f}s)")

    # --- 3. 2회차 build — incremental merge 경로 ---
    # 🔴 "성공했다"가 아니라 **merge가 실제로 발행됐는지**를 본다. incremental이 조용히
    #    full-refresh로 떨어져도 종료 코드는 0이라, 코드만 보면 통과로 읽힌다(원칙 7).
    second = subprocess.run(  # noqa: S603
        [*dbt_bin, "--debug", "build", *dbt_dirs],
        capture_output=True,
        text=True,
        check=False,
    )
    if second.returncode != 0:
        failures.append("2회차 `dbt build` 실패 (incremental 경로)")
        print(second.stdout[-4000:], file=sys.stderr)
    elif "merge into" not in second.stdout.lower():
        failures.append("2회차 build가 `merge into` 미발행 (전략이 조용히 바뀜)")
    else:
        print("✓ 2회차 build — `merge into` 발행 확인")

    # --- 4. 카탈로그 메타데이터 경로 (Dagster 자산 메타데이터가 여기 얹힌다) ---
    docs = subprocess.run(  # noqa: S603
        [*dbt_bin, "docs", "generate", *dbt_dirs],
        capture_output=True,
        text=True,
        check=False,
    )
    if docs.returncode != 0:
        failures.append("`dbt docs generate` 실패 (describe/show 메타데이터 경로)")
        print(docs.stdout[-4000:], file=sys.stderr)
    else:
        print("✓ docs generate — 카탈로그 메타데이터 조회 통과")

    # --- 5. 정리 — 스모크가 만든 테이블·네임스페이스만 되돌린다 ---
    cleanup = subprocess.run(  # noqa: S603
        [
            str(VENV_PYTHON),
            "-c",
            CLEANUP_SNIPPET.format(
                remote=remote, tables=list(SMOKE_TABLES), schema=SMOKE_SCHEMA
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if "cleanup-ok" in cleanup.stdout:
        print(f"✓ 정리 — iceberg.{SMOKE_SCHEMA} 테이블·네임스페이스 삭제")
    else:
        # 정리 실패는 회귀가 아니다 — 판정을 뒤집지 않고 사람에게만 알린다.
        print(
            f"⚠ 정리 실패 — iceberg.{SMOKE_SCHEMA} 수동 삭제 필요\n"
            f"{cleanup.stderr[-1000:]}",
            file=sys.stderr,
        )
    shutil.rmtree(workdir, ignore_errors=True)

    # --- 6. 판정 ---
    if failures:
        print("\n✗ 회귀 발견 — Spark Connect 경로가 깨졌다:", file=sys.stderr)
        for item in failures:
            print(f"  · {item}", file=sys.stderr)
        print(
            "\n대피로: `k8s/spark/spark-thrift-server.yaml`(공식 method: thrift).\n"
            "  쓰려면 `dbt-spark[PyHive]` 설치가 먼저다 — pyhive·thrift·\n"
            "  thrift_sasl 미설치 시 connections.py가 ImportError를 삼켜\n"
            "  접속 시점에 NoneType으로 죽는다.",
            file=sys.stderr,
        )
        return 1

    print("\n✓ 전 항목 통과 — Connect 경로 정상. 의존성 상한을 올려도 된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
