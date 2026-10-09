#!/usr/bin/env bash
# k8s-env.sh 컨텍스트 가드 검증 — 음성 대조(가드가 막는다)와 양성 대조(맞으면 통과한다)를 함께 본다.
# 실제 ~/.kube는 읽지 않는다: HOME을 임시 디렉터리로 바꾸고 KUBECONFIG를 비운 채 source한다.
# 사용: bash scripts/tests/k8s-env-guard.sh   (repo 루트 어디서든)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../k8s-env.sh"
TMP_HOME="$(mktemp -d)"
trap 'rm -rf "${TMP_HOME}"' EXIT
mkdir -p "${TMP_HOME}/.kube"

run_guard() {
    # shellcheck disable=SC2016  # $1은 내부 bash가 펼쳐야 한다
    env -u KUBECONFIG HOME="${TMP_HOME}" \
        bash -c 'source "$1" && require_cluster_context' _ "${ENV_FILE}"
}


# 가드 고유 문구 — 기대값 문자열(kind-lakehouse)만으로는 가드가 아닌 다른 출력도 통과하므로 이 문구를 단언한다.
GUARD_PHRASE='컨텍스트 불일치'

write_config() {
    # $1 = current-context 이름. 해당 이름의 컨텍스트 하나만 가진 최소 kubeconfig를 둔다.
    cat > "${TMP_HOME}/.kube/lakehouse.config" <<YAML
apiVersion: v1
kind: Config
current-context: $1
clusters:
  - name: $1
    cluster: {server: "https://127.0.0.1:1"}
contexts:
  - name: $1
    context: {cluster: $1, user: u}
users:
  - name: u
    user: {}
YAML
}

expect_refused() {
    # $1 = 케이스 라벨. 비0 종료 + stderr에 가드 고유 문구와 기대 컨텍스트가 모두 있어야 한다.
    local status=0 err
    err="$(run_guard 2>&1 >/dev/null)" || status=$?
    if [ "${status}" -eq 0 ]; then
        printf 'FAIL(%s): 가드가 통과했다\n' "$1" >&2
        exit 1
    fi
    if ! printf '%s' "${err}" | grep -q "${GUARD_PHRASE}" ||
        ! printf '%s' "${err}" | grep -q 'kind-lakehouse'; then
        printf 'FAIL(%s): stderr에 가드 문구/기대 컨텍스트가 없다: %s\n' "$1" "${err}" >&2
        exit 1
    fi
}

# 1) 음성 (i) — kubeconfig가 없으면(현재 컨텍스트 비어 있음) 막혀야 한다.
expect_refused '음성-kubeconfig 없음'

# 2) 음성 (iii) — 다른 클러스터가 current-context이면 막혀야 한다. 가드의 본 경로다.
write_config kind-argocd-study
expect_refused '음성-다른 컨텍스트'

# 3) 양성 대조 — current-context가 kind-lakehouse이면 0으로 끝나야 한다.
#    (음성만 보면 kubectl 부재 등 다른 이유의 실패도 통과로 읽힌다)
write_config kind-lakehouse
if ! run_guard >/dev/null 2>&1; then
    printf 'FAIL(양성): 올바른 컨텍스트인데 가드가 막았다\n' >&2
    exit 1
fi

printf 'PASS: 음성 2종(없음·다른 컨텍스트)과 양성 대조 모두 기대대로\n'
