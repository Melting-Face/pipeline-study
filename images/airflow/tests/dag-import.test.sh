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
# 의존 도구: podman (arm64 호스트, docker는 쓰지 않는다)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE="pipeline-study-airflow:test"

echo "== 빌드: ${IMAGE} (linux/arm64) =="
if ! podman build --platform linux/arm64 -t "${IMAGE}" "${CONTEXT_DIR}"; then
    echo "FAIL [build] 이미지 빌드 실패 — Dockerfile 확인"
    exit 1
fi

# 컨테이너 안에서 실행할 검증 코드: 오류 수와 DAG id 목록을 JSON으로 출력한다.
CHECK_PY='
import json
from airflow.models.dagbag import DagBag

bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
print(json.dumps({"errors": bag.import_errors, "dag_ids": sorted(bag.dag_ids)}))
'

if ! result="$(podman run --rm "${IMAGE}" python -c "${CHECK_PY}" | tail -n 1)"; then
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

exit "${rc}"
