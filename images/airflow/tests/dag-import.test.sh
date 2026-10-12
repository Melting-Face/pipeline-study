#!/usr/bin/env bash
# images/airflow/tests/dag-import.test.sh
#
# 커스텀 Airflow 이미지를 빌드하고, 이미지 안에서 DAG가 "import 오류 0건"이며
# DAG id `hello`가 존재하는지 본다. 클러스터·레지스트리를 건드리지 않는다.
#
# 🔴 왜 `airflow dags list`가 아니라 DagBag 직접 호출인가:
#   Airflow 3의 `dags list`/`list-import-errors`는 메타데이터 DB(초기화 필요)를
#   읽는다. 일회용 컨테이너에서는 DB가 없어 이 경로가 깨지기 쉽다. DagBag은
#   DAG 폴더를 파싱만 하므로 DB 없이 같은 의미("import 오류 0 + hello 존재")를
#   가장 덜 취약하게 검증한다.
#
# 🔴 보지 못하는 것: DagBag 기본 safe_mode는 내용에 "airflow"와 "dag" 문자열이 둘 다 없는
#   파일을 파싱하지 않고 건너뛴다(오류로도 세지 않는다). 음성 대조용으로 깨진 파일을 넣을 때
#   두 문자열을 함께 넣지 않으면 "오류 0건"이 나와 게이트가 죽은 것처럼 보인다.
#
# 의존 도구: 컨테이너 CLI(기본 podman — 로컬 arm64 호스트. CI 러너는 CONTAINER_CLI=docker), jq
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${CONTEXT_DIR}/../.." && pwd)"
VALUES="${REPO_ROOT}/gitops/charts/airflow/values.yaml"
IMAGE="pipeline-study-airflow:test"
CLI="${CONTAINER_CLI:-podman}"

# 배포(차트)와 같은 executor 목록 — 단일 출처는 values.yaml 이다(테스트에 다시 적지 않는다).
# 줄이 정확히 1개가 아니면 판정 불가로 실패한다(엉뚱한 목록으로 통과하지 않게).
executor_lines="$(grep -E '^ +executor: "[^"]+"$' "${VALUES}" || true)"
if [ "$(printf '%s' "${executor_lines}" | grep -c . || true)" -ne 1 ]; then
    echo "FAIL [executor-source] ${VALUES} 에서 executor 줄을 정확히 1개 찾지 못했다"
    exit 1
fi
EXECUTORS="$(printf '%s' "${executor_lines}" | sed -E 's/^ +executor: "([^"]+)"$/\1/')"

echo "== 빌드: ${IMAGE} (linux/arm64, ${CLI}) =="
if ! "${CLI}" build --platform linux/arm64 -t "${IMAGE}" "${CONTEXT_DIR}"; then
    echo "FAIL [build] 이미지 빌드 실패 — Dockerfile 확인"
    exit 1
fi

# 컨테이너 안에서 실행할 검증 코드: 오류 수와 DAG id 목록을 JSON으로 출력한다.
CHECK_PY='
import json
from airflow.models.dagbag import DagBag

bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
hello = bag.dags.get("hello")
tasks = {t.task_id: t.executor for t in hello.tasks} if hello else {}
downstream = {t.task_id: sorted(t.downstream_task_ids) for t in hello.tasks} if hello else {}
print(json.dumps({"errors": bag.import_errors, "dag_ids": sorted(bag.dag_ids), "tasks": tasks,
                  "downstream": downstream}))
'

# values 의 executor 목록을 주입한다. 없으면 태스크의 executor="KubernetesExecutor"가
# UnknownExecutorException으로 import 오류가 된다. 첫 번째가 기본 executor다.
if ! result="$("${CLI}" run --rm -e AIRFLOW__CORE__EXECUTOR="${EXECUTORS}" "${IMAGE}" python -c "${CHECK_PY}" | tail -n 1)"; then
    echo "FAIL [run] 컨테이너에서 DagBag 검사 실행 실패"
    exit 1
fi
echo "검사 결과: ${result}"

rc=0

if [[ "$(echo "${result}" | jq -c '.errors')" == "{}" ]]; then
    echo "PASS [import-errors] DAG import 오류 0건"
else
    echo "FAIL [import-errors] DAG import 오류가 있다"
    rc=1
fi

if echo "${result}" | jq -e '.dag_ids | index("hello")' >/dev/null; then
    echo "PASS [dag-id] DAG id 'hello' 존재"
else
    echo "FAIL [dag-id] DAG id 'hello'가 없다"
    rc=1
fi

# 미지정 태스크는 null(→ 배포 기본 Celery), k8s 태스크만 명시 executor를 가진다.
if echo "${result}" | jq -e '.tasks == {"say_hello": null, "say_hello_k8s": "KubernetesExecutor"}' >/dev/null; then
    echo "PASS [executors] say_hello=기본(Celery), say_hello_k8s=KubernetesExecutor"
else
    echo "FAIL [executors] 태스크별 executor가 기대와 다르다"
    rc=1
fi

# 기본(Celery) 경로가 끝난 뒤 K8s 경로가 돈다 — 의존이 빠지면 두 경로가 병렬로 돌아도 테스트가 통과했었다
if echo "${result}" | jq -e '.downstream == {"say_hello": ["say_hello_k8s"], "say_hello_k8s": []}' >/dev/null; then
    echo "PASS [dependency] say_hello >> say_hello_k8s"
else
    echo "FAIL [dependency] 태스크 의존이 기대와 다르다"
    rc=1
fi

exit "${rc}"
