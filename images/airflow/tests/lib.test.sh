#!/usr/bin/env bash
# images/airflow/tests/lib.test.sh
#
# Airflow `lakehouse` 패키지 단위 테스트(`tests/lib/`)를 호스트에서 돌린다 — 이미지 빌드도,
# 클러스터·S3·Trino 접속도 없다(카탈로그는 SqlCatalog=sqlite, HTTP·S3는 monkeypatch/스텁).
# CI(`ci.yml` lint 잡, 필수 체크)와 로컬이 **이 한 파일**을 부른다 — 명령을 두 곳에 적지 않는다.
#
# 🔴 의존성 버전의 정본은 `images/airflow/requirements.txt`다(이미지와 같은 pyiceberg·holidays·defusedxml).
#   단 DAG 전용인 `astronomer-cosmos`·`trino`는 뺀다 — cosmos가 `apache-airflow`를 범위로 끌어와
#   이미지 고정판(3.2.2)이 아닌 최신판으로 해석되고(실측 3.4.0, 32개 → 160개 패키지), `lakehouse`는
#   **모듈 로드 시점에** 둘 다 import하지 않는다(`lakehouse/trino.py`는 함수 안에서 지연 import하고,
#   테스트는 가짜 `trino` 모듈을 끼운다). 뺀 줄이 없으면 실패한다(이름이 바뀌어 필터가 조용히 무력해지지 않게).
# ⚠️ `boto3`·`requests`·`pyyaml`·`pytest`는 이미지에서 Airflow가 동반 설치하는 것이라 requirements에
#   없다 — 여기서는 **고정하지 않는다**. 이 테스트는 그 라이브러리들의 장기 안정 API만 쓰고,
#   판을 맞추려면 Airflow constraints 파일을 들여와야 해 비용이 크다. 상류 릴리스가 PR과 무관한
#   빨강을 낼 수 있음을 수용한다.
# 파이썬은 베이스 이미지(`apache/airflow:3.2.2`)의 판과 맞춘다(실측 3.13).
#
# 수집 0건은 pytest가 rc 5로 끝내 그대로 실패다(「돌았다」를 「검사했다」로 읽지 않는다).
#
# 의존 도구: uv
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQUIREMENTS="${CONTEXT_DIR}/requirements.txt"
PYTHON_VERSION="3.13"
DAG_ONLY_PATTERN='^(astronomer-cosmos|trino)=='

command -v uv >/dev/null || { echo "uv 없음 — https://docs.astral.sh/uv/ 로 설치한다" >&2; exit 1; }

excluded="$(grep -cE "${DAG_ONLY_PATTERN}" "${REQUIREMENTS}" || true)"
if [ "${excluded}" -ne 2 ]; then
    echo "requirements.txt에서 DAG 전용 의존(cosmos·trino)을 2줄 찾아야 하는데 ${excluded}줄이다 — 필터를 고친다" >&2
    exit 1
fi

lib_requirements="$(mktemp)"
trap 'rm -f "${lib_requirements}"' EXIT
grep -vE "${DAG_ONLY_PATTERN}" "${REQUIREMENTS}" > "${lib_requirements}"

PYTHONPATH="${CONTEXT_DIR}/dags" uv run --no-project --python "${PYTHON_VERSION}" \
    --with-requirements "${lib_requirements}" \
    --with pytest --with boto3 --with requests --with pyyaml \
    pytest "${SCRIPT_DIR}/lib" -q -p no:cacheprovider "$@"
