#!/usr/bin/env bash
# scripts/airflow-image-version.sh 검증 — values 파일의 `# bump-image-tag` 줄에서 발행 버전을 뽑는다.
# 그 버전이 GHCR 발행의 단일 출처라(.github/workflows/airflow-image.yml), 마커가 0개·2개·형식 위반일 때
# 조용히 무언가를 내놓지 않고 실패하는지가 핵심이다.
# 사용: bash scripts/tests/airflow-image-version.sh   (repo 루트 어디서든)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SUT="${ROOT}/scripts/airflow-image-version.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

pass=0
fail=0
# check <이름> <기대 종료코드> <기대 stdout> <values 파일>
check() {
    local name="$1" want_rc="$2" want_out="$3" file="$4" out rc
    set +e
    out="$(bash "${SUT}" "${file}" 2>/dev/null)"
    rc=$?
    set -e
    if [ "${rc}" = "${want_rc}" ] && [ "${out}" = "${want_out}" ]; then
        echo "PASS [${name}]"
        pass=$((pass + 1))
    else
        echo "FAIL [${name}] 기대 rc=${want_rc} out='${want_out}', 실제 rc=${rc} out='${out}'"
        fail=$((fail + 1))
    fi
}

printf '    images:\n        airflow:\n            tag: "v1.2.3"  # bump-image-tag\n' > "${TMP}/ok.yaml"
check "정상" 0 "v1.2.3" "${TMP}/ok.yaml"

# 실제 차트 values — 대상 파일이 마커 규약을 지키는지도 함께 본다(형식만, 값은 고정하지 않는다)
set +e
real="$(bash "${SUT}" "${ROOT}/gitops/charts/airflow/values.yaml" 2>/dev/null)"
real_rc=$?
set -e
if [ "${real_rc}" = 0 ] && [[ "${real}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "PASS [실제 values] ${real}"
    pass=$((pass + 1))
else
    echo "FAIL [실제 values] rc=${real_rc} out='${real}'"
    fail=$((fail + 1))
fi

printf '            tag: "v1.2.3"\n' > "${TMP}/none.yaml"
check "마커 0개" 3 "" "${TMP}/none.yaml"

printf '            tag: "v1.2.3"  # bump-image-tag\n            tag: "v1.2.4"  # bump-image-tag\n' > "${TMP}/two.yaml"
check "마커 2개" 3 "" "${TMP}/two.yaml"

printf '            tag: "v1.2"  # bump-image-tag\n' > "${TMP}/bad.yaml"
check "형식 위반(v1.2)" 3 "" "${TMP}/bad.yaml"

printf '            tag: "latest"  # bump-image-tag\n' > "${TMP}/latest.yaml"
check "형식 위반(latest)" 3 "" "${TMP}/latest.yaml"

check "파일 없음" 1 "" "${TMP}/missing.yaml"

echo "PASS ${pass} / FAIL ${fail}"
# 🔴 0건 통과를 성공으로 읽지 않는다 — 케이스가 하나도 안 돌았다면 배선이 죽은 것이다
[ "${fail}" -eq 0 ] && [ "${pass}" -ge 7 ]
