#!/usr/bin/env bash
# terraform/platform/charts/appset/tests/render.test.sh
#
# appset chart 의 렌더 결과를 검증한다. 클러스터를 건드리지 않는다(helm template 은
# 로컬 렌더링만 한다). 의존 도구: helm, uv(PyYAML 은 uv run --with 로 일회성 주입).
#
# 검사 항목:
#   (a) 기본 앱 4개 렌더 — ApplicationSet 1개(이름 apps), elements 4개, 그리고 ApplicationSet
#       컨트롤러가 채울 리터럴 {{ .name }}·{{ .path }}·{{ .namespace }} 가 렌더 결과에
#       그대로 남아 있다(Helm 이 먼저 먹어버리면 빈 문자열이 되어 앱이 전부 같은 이름이 된다)
#   (b) apps=[] 도 렌더되고 elements: [] 로 유효한 YAML 이다
#   (c) repoUrl 을 비우면 helm template 이 실패한다(helm lint 는 이를 못 잡는다)
#   (d) syncPolicy(docs/argocd-gitops.md §5) — 앱 삭제 보호(preserveResourcesOnDeletion), syncOptions 3종,
#       재시도(retry) 한도·백오프
#   (e) targetRevision 이 values 에서 오고 --set 으로 바뀐다
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_URL="https://github.com/Melting-Face/pipeline-study.git"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

FAILED=0
fail() {
    echo "FAIL: $*" >&2
    FAILED=1
}
pass() {
    echo "PASS: $*"
}

cat > "${WORKDIR}/four-apps.yaml" <<'VALUES'
apps:
  - name: cert-manager
    path: gitops/charts/cert-manager
    namespace: cert-manager
  - name: cnpg-operator
    path: gitops/charts/cnpg-operator
    namespace: cnpg-system
  - name: spark-operator
    path: gitops/charts/spark-operator
    namespace: spark-operator
  - name: flink-operator
    path: gitops/charts/flink-operator
    namespace: flink-operator
VALUES

# 렌더 결과(stdin)에서 ApplicationSet(첫 번째)을 골라 파이썬 식 $1 을 평가해 출력한다.
# 식 안에서 ApplicationSet 은 s, 개수는 n 이다. ApplicationSet 이 없으면 s 는 None.
query() {
    uv run --quiet --with pyyaml python -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if d]
sets = [d for d in docs if d.get("kind") == "ApplicationSet"]
n = len(sets)
s = sets[0] if sets else None
print(json.dumps(eval(sys.argv[1]), sort_keys=True))
' "$1"
}

# 단언 하나: expect_eq <설명> <렌더 결과> <파이썬 식> <기대 JSON>
expect_eq() {
    local label="$1" rendered="$2" expr="$3" want="$4" got
    if ! got="$(query "${expr}" <<< "${rendered}" 2>&1)"; then
        fail "${label} - 식 평가 실패: ${got}"
    elif [[ "${got}" == "${want}" ]]; then
        pass "${label}"
    else
        fail "${label} - 기대 ${want}, 실제 ${got}"
    fi
}

# (a) 앱 4개
if out_a="$(helm template appset "${CHART_DIR}" --set "repoUrl=${REPO_URL}" -f "${WORKDIR}/four-apps.yaml" 2>&1)"; then
    expect_eq "(a) ApplicationSet 1개" "${out_a}" 'n' '1'
    expect_eq "(a) 이름 apps" "${out_a}" 's["metadata"]["name"]' '"apps"'
    expect_eq "(a) elements 4개" "${out_a}" 'len(s["spec"]["generators"][0]["list"]["elements"])' '4'
    for key in name path namespace; do
        if grep -qF "{{ .${key} }}" <<< "${out_a}"; then
            pass "(a) 리터럴 {{ .${key} }} 보존"
        else
            fail "(a) 리터럴 {{ .${key} }} 가 렌더 결과에 없다 - Helm 이스케이프 누락"
        fi
    done

    # (d) syncPolicy — docs/argocd-gitops.md §5 · §4 겹 1
    expect_eq "(d) spec.syncPolicy.preserveResourcesOnDeletion == true" "${out_a}" \
        's["spec"]["syncPolicy"]["preserveResourcesOnDeletion"]' 'true'
    expect_eq "(d) template syncOptions 가 필수 3종을 포함" "${out_a}" \
        'sorted({"CreateNamespace=true","ServerSideApply=true","SkipDryRunOnMissingResource=true"} - set(s["spec"]["template"]["spec"]["syncPolicy"]["syncOptions"]))' '[]'
    expect_eq "(d) automated selfHeal·prune" "${out_a}" \
        's["spec"]["template"]["spec"]["syncPolicy"]["automated"]' '{"prune": true, "selfHeal": true}'
    expect_eq "(d) retry.limit == 10" "${out_a}" \
        's["spec"]["template"]["spec"]["syncPolicy"]["retry"]["limit"]' '10'
    expect_eq "(d) retry.backoff == {10s, 2, 3m}" "${out_a}" \
        's["spec"]["template"]["spec"]["syncPolicy"]["retry"]["backoff"]' '{"duration": "10s", "factor": 2, "maxDuration": "3m"}'

    # (e) 기본 targetRevision
    expect_eq "(e) 기본 targetRevision == main" "${out_a}" \
        's["spec"]["template"]["spec"]["source"]["targetRevision"]' '"main"'
else
    fail "(a) helm template 실패: ${out_a}"
fi

# (b) apps=[]
if out_b="$(helm template appset "${CHART_DIR}" --set "repoUrl=${REPO_URL}" --set-json 'apps=[]' 2>&1)"; then
    expect_eq "(b) apps=[] 렌더, elements 0개로 유효한 YAML" "${out_b}" \
        's["spec"]["generators"][0]["list"]["elements"]' '[]'
else
    fail "(b) helm template 실패: ${out_b}"
fi

# (c) repoUrl 비움
if helm template appset "${CHART_DIR}" --set "repoUrl=" > /dev/null 2>&1; then
    fail "(c) repoUrl 이 비었는데 helm template 이 성공했다"
else
    pass "(c) repoUrl 비움 - helm template 실패"
fi

# (e) targetRevision 덮어쓰기
if out_e="$(helm template appset "${CHART_DIR}" --set "repoUrl=${REPO_URL}" --set targetRevision=feat/x 2>&1)"; then
    expect_eq "(e) --set targetRevision=feat/x 가 렌더에 반영" "${out_e}" \
        's["spec"]["template"]["spec"]["source"]["targetRevision"]' '"feat/x"'
else
    fail "(e) helm template 실패: ${out_e}"
fi

exit "${FAILED}"
