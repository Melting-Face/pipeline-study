"""Flink 배치 잡 자산 — Dagster가 세션 클러스터를 띄우고, SQL을 돌리고, 내린다.

`k8s/flink/iceberg-batch-job.yaml`의 SQL(`iceberg.poc.sample` 읽기 →
`iceberg.poc.sample_flink` 쓰기)을 Dagster 자산으로 끌어온다. 종전에는
`kubectl exec … sql-client.sh`를 사람이 직접 돌려야 했다.

주의: 이 자산은 실인프라(kind 클러스터)에 접속하므로 단위 테스트 대상이 아니다
(`docs/test.md` 격리 원칙 — `poc_spark_ingest`와 같은 취급). context class identity
검사 때문에 자산 모듈에서는 `from __future__ import annotations`를 쓰지 않는다.
"""

import re

import dagster as dg
from dagster_project.defs.flink.resources import FlinkSessionResource

GROUP_NAME = "flink"

# tableau 출력에서 `spark_rows` 값을 뽑는다.
#   +------------+
#   | spark_rows |
#   +------------+
#   |          3 |
# 헤더를 찾은 뒤 이어지는 첫 숫자 행을 읽는다.
_SPARK_ROWS_HEADER = "spark_rows"
_TABLEAU_INT_ROW = re.compile(r"^\|\s*(\d+)\s*\|$")


def _parse_source_rows(logs: str) -> int | None:
    """`SELECT count(*) AS spark_rows` 결과를 뽑는다(못 찾으면 None).

    🔴 **이 값은 원천(`poc.sample`) 행 수이지 Flink가 쓴 행 수가 아니다.**
    배치 SQL에는 INSERT 뒤 검증 SELECT가 없어 `poc.sample_flink`의 행 수는
    이 자산의 출력으로 확인할 수 없다. 라벨을 `source_rows`로 두는 이유다 —
    값은 맞고 라벨이 틀린 수치가 가장 위험하다(philosophy.md §계측 단위).
    쓰기 검증은 `data-verifier`가 카탈로그를 직접 조회해 판정한다.
    """
    lines = logs.splitlines()
    for index, line in enumerate(lines):
        if _SPARK_ROWS_HEADER not in line:
            continue
        for candidate in lines[index + 1 : index + 6]:
            match = _TABLEAU_INT_ROW.match(candidate.strip())
            if match:
                return int(match.group(1))
    return None


@dg.asset(group_name=GROUP_NAME, kinds={"flink", "iceberg", "bronze"})
def flink_iceberg_batch(
    context: dg.AssetExecutionContext,
    flink_session: FlinkSessionResource,
) -> dg.MaterializeResult:
    """Flink 세션 클러스터에서 Iceberg 배치 잡을 돌린다(기동 → 실행 → 회수)."""
    auth_path = flink_session.load_kube_auth()
    context.log.info("Flink 세션 준비 (auth=%s)", auth_path)

    # 마운트 대상 ConfigMap이 없으면 JM은 CreateContainerConfigError로 뜨지 않는다.
    # 그 증상은 "기동 타임아웃"으로 보이므로 먼저 걸러 원인을 명확히 만든다.
    flink_session.ensure_configmaps()

    jm_logs = ""
    try:
        jm_pod = flink_session.ensure_session(context)
        result = flink_session.run_sql(jm_pod)
        # 🔴 **teardown 전에** 진단 근거를 회수한다 — 순서가 규칙이다.
        #   회수가 JM을 지우면 로그도 함께 사라진다.
        jm_logs = flink_session.jm_logs(jm_pod)

        # 마스킹 가드가 막은 경우는 잡 실패와 원인이 다르다 — 문구를 갈라 오진을 막는다.
        if not result.redaction_ok:
            raise dg.Failure(
                description=(
                    f"크리덴셜 마스킹 가드가 실행을 막았다(exit={result.exit_code}). "
                    "잡 실패가 아니라 **출력 보호 경로의 문제**다. "
                    "JM 파드의 PG_PASSWORD 주입과 `grep -F` 가용성을 확인한다."
                ),
                metadata={
                    "guard_output": dg.MetadataValue.md(f"```\n{result.logs}\n```")
                },
            )

        context.log.info(result.logs)

        if result.exit_code != 0:
            raise dg.Failure(
                description=f"Flink SQL 배치 잡 실패: exit={result.exit_code}",
                metadata={
                    "jm_logs": dg.MetadataValue.md(f"```\n{jm_logs[-4000:]}\n```")
                },
            )

        source_rows = _parse_source_rows(result.logs)
        return dg.MaterializeResult(
            metadata={
                "auth": auth_path,
                "jm_pod": jm_pod,
                "exit_code": result.exit_code,
                # 라벨 주의: 원천 읽기 행 수다.
                # 쓰기 결과가 아니다 — _parse_source_rows 참고.
                "source_rows": source_rows if source_rows is not None else "unparsed",
                "write_verified": False,
                "sql_output": dg.MetadataValue.md(f"```\n{result.logs[-4000:]}\n```"),
            }
        )
    finally:
        # 🔴 회수 규율을 코드가 진다 — 실패해도 내린다.
        #   문서 규약만으로는 13시간 샜다(docs/architectures/flink.md §회수 규율).
        flink_session.teardown(context)
