#!/usr/bin/env bash
# terraform/platform/charts/appset/tests/render.test.sh
#
# appset chart 의 렌더 결과를 검증한다. 클러스터를 건드리지 않는다(helm template 은
# 로컬 렌더링만 한다). 의존 도구: helm, uv(PyYAML 은 uv run --with 로 일회성 주입).
#
# 검사 항목:
#   (a) ApplicationSet 1개(이름 apps), generator 가 git files(gitops/apps/*/config.yaml)이고 repoURL·revision 이
#       values 에서 온다. ApplicationSet 컨트롤러가 채울 리터럴(.name·.tier·.namespace·.source.*)이 렌더 결과에
#       그대로 남아 있다(Helm 이 먼저 먹어버리면 빈 문자열이 되어 앱이 전부 같은 이름이 된다)
#   (b) strategy — RollingSync, steps 의 tier 값이 "0"→"1"→"2"(scripts/gitops-apps-check.sh (d)와 같은 집합),
#       applicationsSync == create-update, template 에 source 가 없다(templatePatch 만이 source 를 넣는다)
#   (c) repoUrl 을 비우면 helm template 이 실패한다(helm lint 는 이를 못 잡는다)
#   (d) syncPolicy(docs/argocd-gitops.md §5) — 앱 삭제 보호(preserveResourcesOnDeletion), syncOptions 3종,
#       재시도(retry) 한도·백오프
#   (e) targetRevision 이 values 에서 오고 --set 으로 바뀐다 — generator revision 과 templatePatch 양쪽
#   (f) templatePatch 근사 평가 — 렌더된 patch 문자열을 Helm 엔진으로 픽스처에 대해 평가한다.
#       umbrella 는 source.path 만, upstream 은 sources 2개(상류 차트 + $values ref), 알 수 없는 type 은 실패.
#       ⚠️ 근사다: Helm(sprig)과 ArgoCD goTemplate 은 같은 text/template+sprig 계열이지만 같은 엔진이 아니다
#       (ArgoCD 는 일부 sprig 함수를 뺀다). 정본 판정은 클러스터 대상 `argocd appset generate`(docs/argocd-gitops.md §7).
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

# 렌더 결과(stdin)에서 ApplicationSet(첫 번째)을 골라 파이썬 식 $1 을 평가해 출력한다.
# 식 안에서 ApplicationSet 은 s, 개수는 n, 첫 문서는 d 다. ApplicationSet 이 없으면 s 는 None.
query() {
    uv run --quiet --with pyyaml python -c '
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if d]
sets = [d for d in docs if d.get("kind") == "ApplicationSet"]
n = len(sets)
s = sets[0] if sets else None
d = docs[0] if docs else None
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

# (f) 용 — 렌더된 templatePatch 를 임시 차트의 템플릿으로 옮긴다. patch 는 최상위(.source 등)를 참조하므로
# with .Values 로 감싸 픽스처 values 를 최상위로 보이게 한다.
build_patch_chart() {
    local rendered="$1" chart="${WORKDIR}/patch-chart"
    mkdir -p "${chart}/templates"
    printf 'apiVersion: v2\nname: patch\nversion: 0.0.0\n' > "${chart}/Chart.yaml"
    uv run --quiet --with pyyaml python -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if d]
s = [d for d in docs if d.get("kind") == "ApplicationSet"][0]
open(sys.argv[1], "w").write("{{- with .Values }}\n" + s["spec"]["templatePatch"] + "{{- end }}\n")
' "${chart}/templates/patch.yaml" <<< "${rendered}"
}

# (a)(b)(d) 기본 렌더
if out_a="$(helm template appset "${CHART_DIR}" --set "repoUrl=${REPO_URL}" 2>&1)"; then
    expect_eq "(a) ApplicationSet 1개" "${out_a}" 'n' '1'
    expect_eq "(a) 이름 apps" "${out_a}" 's["metadata"]["name"]' '"apps"'
    expect_eq "(a) generator 는 git files 하나" "${out_a}" \
        '[list(g) for g in s["spec"]["generators"]]' '[["git"]]'
    expect_eq "(a) files glob == gitops/apps/*/config.yaml" "${out_a}" \
        's["spec"]["generators"][0]["git"]["files"]' '[{"path": "gitops/apps/*/config.yaml"}]'
    expect_eq "(a) generator repoURL 이 values 에서 온다" "${out_a}" \
        's["spec"]["generators"][0]["git"]["repoURL"]' "\"${REPO_URL}\""
    # 조각 전체로 찾는다 — ".name" 만 찾으면 ".namespace" 에 걸려 이스케이프가 깨져도 통과한다.
    for key in '{{ .name }}' '{{ .tier }}' '{{ .namespace }}' 'eq .source.type "upstream"' '{{ .source.path | quote }}' '.path.path | quote }}'; do
        if grep -qF "${key}" <<< "${out_a}"; then
            pass "(a) 리터럴 ${key} 보존"
        else
            fail "(a) 리터럴 ${key} 가 렌더 결과에 없다 - Helm 이스케이프 누락"
        fi
    done

    expect_eq "(b) strategy RollingSync" "${out_a}" 's["spec"]["strategy"]["type"]' '"RollingSync"'
    expect_eq "(b) steps tier 값 == 0→1→2" "${out_a}" \
        '[[(e["key"], e["operator"], e["values"]) for e in st["matchExpressions"]] for st in s["spec"]["strategy"]["rollingSync"]["steps"]]' \
        '[[["tier", "In", ["0"]]], [["tier", "In", ["1"]]], [["tier", "In", ["2"]]]]'
    expect_eq "(b) applicationsSync == create-update" "${out_a}" \
        's["spec"]["syncPolicy"]["applicationsSync"]' '"create-update"'
    expect_eq "(b) template 에 source·sources 없음" "${out_a}" \
        'sorted({"source", "sources"} & set(s["spec"]["template"]["spec"]))' '[]'

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

    # (e) 기본 targetRevision — generator 와 patch 의 두 source 자리(umbrella 1 + upstream values ref 1)
    expect_eq "(e) 기본 generator revision == main" "${out_a}" \
        's["spec"]["generators"][0]["git"]["revision"]' '"main"'
    expect_eq "(e) patch 의 targetRevision \"main\" 2곳" "${out_a}" \
        's["spec"]["templatePatch"].count("targetRevision: \"main\"")' '2'

    # (f) templatePatch 근사 평가
    build_patch_chart "${out_a}"
    patch_chart="${WORKDIR}/patch-chart"

    cat > "${WORKDIR}/umbrella.yaml" <<'VALUES'
name: cert-manager
namespace: cert-manager
tier: "0"
source:
  type: umbrella
  path: gitops/charts/cert-manager
path:
  path: gitops/apps/cert-manager
VALUES
    if out_u="$(helm template p "${patch_chart}" -f "${WORKDIR}/umbrella.yaml" 2>&1)"; then
        expect_eq "(f) umbrella → source(path·repoURL·main)" "${out_u}" \
            'd["spec"]["source"]' "{\"path\": \"gitops/charts/cert-manager\", \"repoURL\": \"${REPO_URL}\", \"targetRevision\": \"main\"}"
        expect_eq "(f) umbrella → sources 없음" "${out_u}" '"sources" in d["spec"]' 'false'
    else
        fail "(f) umbrella 픽스처 평가 실패: ${out_u}"
    fi

    cat > "${WORKDIR}/upstream.yaml" <<'VALUES'
name: trino
namespace: trino
tier: "2"
source:
  type: upstream
  repoURL: https://trinodb.github.io/charts
  chart: trino
  targetRevision: 1.2.3
path:
  path: gitops/apps/trino
VALUES
    if out_up="$(helm template p "${patch_chart}" -f "${WORKDIR}/upstream.yaml" 2>&1)"; then
        expect_eq "(f) upstream → source 없음" "${out_up}" '"source" in d["spec"]' 'false'
        # $values 는 ArgoCD multi-source ref 리터럴이다 — 셸이 펼치면 안 된다.
        # shellcheck disable=SC2016
        expect_eq "(f) upstream → sources[0] 상류 차트 + \$values 파일" "${out_up}" \
            'd["spec"]["sources"][0]' '{"chart": "trino", "helm": {"valueFiles": ["$values/gitops/apps/trino/values.yaml"]}, "repoURL": "https://trinodb.github.io/charts", "targetRevision": "1.2.3"}'
        expect_eq "(f) upstream → sources[1] values ref" "${out_up}" \
            'd["spec"]["sources"][1]' "{\"ref\": \"values\", \"repoURL\": \"${REPO_URL}\", \"targetRevision\": \"main\"}"
    else
        fail "(f) upstream 픽스처 평가 실패: ${out_up}"
    fi

    # 음성: type 오타 — 어느 분기에도 안 걸리면 source 없는 Application 이 아니라 실패여야 한다
    sed 's/type: umbrella/type: umbrela/' "${WORKDIR}/umbrella.yaml" > "${WORKDIR}/bad-type.yaml"
    if out_bad="$(helm template p "${patch_chart}" -f "${WORKDIR}/bad-type.yaml" 2>&1)"; then
        fail "(f) 알 수 없는 source.type 인데 평가가 성공했다: ${out_bad}"
    elif grep -qF "umbrella·upstream 중 하나여야 한다" <<< "${out_bad}"; then
        pass "(f) 알 수 없는 source.type - fail 분기로 실패"
    else
        fail "(f) 알 수 없는 source.type 이 다른 이유로 실패: ${out_bad}"
    fi
else
    fail "(a) helm template 실패: ${out_a}"
fi

# (c) repoUrl 비움
if helm template appset "${CHART_DIR}" --set "repoUrl=" > /dev/null 2>&1; then
    fail "(c) repoUrl 이 비었는데 helm template 이 성공했다"
else
    pass "(c) repoUrl 비움 - helm template 실패"
fi

# (e) targetRevision 덮어쓰기
if out_e="$(helm template appset "${CHART_DIR}" --set "repoUrl=${REPO_URL}" --set targetRevision=feat/x 2>&1)"; then
    expect_eq "(e) --set targetRevision=feat/x 가 generator revision 에 반영" "${out_e}" \
        's["spec"]["generators"][0]["git"]["revision"]' '"feat/x"'
    expect_eq "(e) --set targetRevision=feat/x 가 patch 2곳에 반영" "${out_e}" \
        's["spec"]["templatePatch"].count("targetRevision: \"feat/x\"")' '2'
else
    fail "(e) helm template 실패: ${out_e}"
fi

exit "${FAILED}"
