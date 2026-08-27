"""PoC 자산: 호스트 Dagster가 SparkApplication을 트리거해 Iceberg에 적재.

Spark Operator CRD를 제출·폴링하고, driver 로그에서 결과를 회수해
materialization 메타데이터로 남긴다. 재설계의 "호스트 오케스트레이터 →
원격 컴퓨트(Spark on K8s)" 패턴의 최소 실증이다.

주의: 이 자산은 실인프라(kind 클러스터)에 접속하므로 단위 테스트 대상이 아니다
(격리 원칙 — 검증은 라이브 클러스터에서 수행). context class identity 검사 때문에
자산 모듈에서는 `from __future__ import annotations`를 쓰지 않는다.
"""

import re
from pathlib import Path

import yaml

import dagster as dg
from dagster_project.defs.poc.constants import SPARKAPP_MANIFEST
from dagster_project.defs.poc.resources import SparkOperatorResource

GROUP_NAME = "poc"
# 러너 결과 줄에서 뽑는다: `[poc] wrote table=<catalog>.<ns>.sample rows=<n>`
# 테이블명 하드코딩은 러너 카탈로그 설정과 갈린다(실제로 `jdbccat`으로 남아 있었다).
_ROW_RE = re.compile(r"rows=(\d+)")
_TABLE_RE = re.compile(r"table=(\S+)")


@dg.asset(group_name=GROUP_NAME, kinds={"spark", "iceberg", "bronze"})
def poc_spark_ingest(
    context: dg.AssetExecutionContext,
    spark_operator: SparkOperatorResource,
) -> dg.MaterializeResult:
    """SparkApplication을 제출해 Iceberg 샘플 테이블을 적재한다(호스트→kind)."""
    manifest = yaml.safe_load(Path(SPARKAPP_MANIFEST).read_text())
    name = manifest["metadata"]["name"]
    # 🔴 `kube_context`를 찍지 않는다 — in-cluster에서는 그 값이 쓰이지 않아 로그가
    # 거짓을 말하게 된다. 실제로 로드된 인증 경로를 받아 찍는다.
    auth_path = spark_operator.load_kube_auth()
    context.log.info(
        "SparkApplication 제출: %s (auth=%s, manifest=%s)",
        name,
        auth_path,
        SPARKAPP_MANIFEST,
    )

    run = spark_operator.submit_and_wait(manifest)
    context.log.info(run.logs)

    # 성공·실패 모두 같은 최종 상태로 수렴한다 → 상태 대신 성공 플래그로 판정.
    if not run.succeeded:
        raise dg.Failure(description=f"SparkApplication 실패: {name} state={run.state}")

    row_match = _ROW_RE.search(run.logs)
    table_match = _TABLE_RE.search(run.logs)
    return dg.MaterializeResult(
        metadata={
            "state": run.state,
            "rows": int(row_match.group(1)) if row_match else "unknown",
            "table": table_match.group(1) if table_match else "unknown",
            "driver_pod": run.driver_pod,
        }
    )
