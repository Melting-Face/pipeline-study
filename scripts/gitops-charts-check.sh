#!/usr/bin/env bash
# gitops 차트를 렌더해 정적 단언을 검사한다 — 클러스터에는 붙지 않는다.
#
# 사용법: scripts/gitops-charts-check.sh [chart-dir...]   (인자 없으면 gitops/charts/* 전부)
#
# 차트마다 helm-dep-build.sh -> helm template <앱명> <dir> -n <ns> 후 아래를 단언한다.
#   (a) 모든 Pod 템플릿 컨테이너(initContainers 포함)에 resources.requests·limits (docs/conventions/k8s.md)
#       단 `helm.sh/hook: test` Pod 는 제외하고 `(a) hook 제외 N개` 로 센다(업스트림 test hook 은 자원을 선언하지 않고
#       끌 values 키도 없다. 설치되지 않는 일회성 Pod 이며 다른 hook 은 제외하지 않는다)
#   (b) 모든 CRD에 helm.sh/resource-policy: keep 또는 sync-options Delete=false (docs/argocd-gitops.md §4 겹 3)
#       렌더는 --include-crds — crds/ 디렉터리 CRD 도 보려는 것이다(없으면 CRD 0개로 거짓 통과한다)
#       expect.yaml 의 crd_keep_exempt: [CRD 이름...] 에 적힌 것만 면제하고 `(b) 면제 N개` 로 따로 센다(면제 ≠ 통과)
#   (c) <chart>/tests/expect.yaml 이 있으면 kinds_with_wave: {Kind: "wave"} — 해당 kind 전부가 그 sync-wave 를 가짐,
#       min_crds: N — 렌더된 CRD 가 N개 이상(crds.enabled 회귀로 (b)의 대상이 사라지는 것을 잡는다)
#       service_accounts: [name...] — 그 이름의 ServiceAccount 가 렌더에 있다(릴리스 이름 변경으로
#       SA 이름이 바뀌어 RBAC 가 조용히 빗나가는 것을 잡는다)
#   빈 렌더(문서 0개)는 실패다.
# 앱명·네임스페이스는 terraform/platform 의 var.apps 와 같은 값을 쓴다(아래 case).
# 마지막 줄은 `검사한 차트: N개` 이며, 0개면 실패한다.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${root}"

if [ "$#" -gt 0 ]; then
    charts=("$@")
else
    charts=(gitops/charts/*)
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

checked=0
failed=0
for dir in "${charts[@]}"; do
    dir="${dir%/}"
    app="$(basename "${dir}")"
    if [ ! -f "${dir}/Chart.yaml" ]; then
        echo "FAIL [${app}] ${dir}/Chart.yaml 이 없다"
        failed=1
        continue
    fi
    # terraform/platform/variables.tf 의 var.apps 와 같은 값 — 앱이 늘면 여기에도 추가(미등록은 실패)
    case "${app}" in
        cert-manager) ns="cert-manager" ;;
        cnpg-operator) ns="cnpg-system" ;;
        spark-operator) ns="spark-operator" ;;
        flink-operator) ns="flink-operator" ;;
        *)
            echo "FAIL [${app}] 네임스페이스 매핑이 없다 — 이 스크립트의 case 와 var.apps 에 등록"
            failed=1
            continue
            ;;
    esac

    "${root}/scripts/helm-dep-build.sh" "${dir}" >"${tmp}/dep.log" 2>&1 || {
        cat "${tmp}/dep.log"
        echo "FAIL [${app}] helm dependency build 실패"
        failed=1
        continue
    }
    helm template "${app}" "${dir}" -n "${ns}" --include-crds >"${tmp}/${app}.yaml" || {
        echo "FAIL [${app}] helm template 실패"
        failed=1
        continue
    }

    expect="${dir}/tests/expect.yaml"
    [ -f "${expect}" ] || expect=""
    uv run --quiet --with pyyaml python - "${app}" "${tmp}/${app}.yaml" "${expect}" <<'PY' || failed=1
import sys
import yaml

app, rendered, expect_path = sys.argv[1], sys.argv[2], sys.argv[3]
docs = [d for d in yaml.safe_load_all(open(rendered)) if d]
errors = []
if not docs:
    errors.append("렌더 결과가 비어 있다(문서 0개)")


def pod_spec(doc):
    kind = doc["kind"]
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return spec["jobTemplate"]["spec"]["template"]["spec"]
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
        return spec["template"]["spec"]
    return None


def name_of(doc):
    return f'{doc["kind"]}/{(doc.get("metadata") or {}).get("name")}'


# (a) 모든 컨테이너에 requests/limits
containers = 0
hook_skipped = 0
for doc in docs:
    spec = pod_spec(doc)
    if spec is None:
        continue
    hook = ((doc.get("metadata") or {}).get("annotations") or {}).get("helm.sh/hook", "")
    if doc["kind"] == "Pod" and "test" in [h.strip() for h in str(hook).split(",")]:
        hook_skipped += 1
        continue
    for field in ("initContainers", "containers"):
        for c in spec.get(field) or []:
            containers += 1
            res = c.get("resources") or {}
            for key in ("requests", "limits"):
                if not res.get(key):
                    errors.append(f"(a) {name_of(doc)} {field}/{c['name']}: resources.{key} 없음")

# (b) 모든 CRD 에 삭제 방지
crds = [d for d in docs if d["kind"] == "CustomResourceDefinition"]
expect = yaml.safe_load(open(expect_path)) or {} if expect_path else {}
exempt = set(expect.get("crd_keep_exempt") or [])
exempted = []
for doc in crds:
    if doc["metadata"]["name"] in exempt:
        exempted.append(doc["metadata"]["name"])
        continue
    ann = (doc.get("metadata") or {}).get("annotations") or {}
    keep = ann.get("helm.sh/resource-policy") == "keep"
    nodelete = "Delete=false" in str(ann.get("argocd.argoproj.io/sync-options", ""))
    if not (keep or nodelete):
        errors.append(f"(b) {name_of(doc)}: resource-policy keep / Delete=false 없음")

# (c) 차트별 기대
waves = 0
if expect_path:
    min_crds = expect.get("min_crds")
    if min_crds is not None and len(crds) < min_crds:
        errors.append(f"(c) CRD {len(crds)}개 < min_crds {min_crds}")
    sa_names = {(d.get("metadata") or {}).get("name") for d in docs if d["kind"] == "ServiceAccount"}
    for sa in expect.get("service_accounts") or []:
        if sa not in sa_names:
            errors.append(f"(c) ServiceAccount {sa!r}: 렌더 결과에 없다(있는 것: {sorted(sa_names)})")
    for kind, wave in (expect.get("kinds_with_wave") or {}).items():
        targets = [d for d in docs if d["kind"] == kind]
        if not targets:
            errors.append(f"(c) kind {kind}: 렌더 결과에 없다(대상 0개)")
        for doc in targets:
            waves += 1
            ann = (doc.get("metadata") or {}).get("annotations") or {}
            got = ann.get("argocd.argoproj.io/sync-wave")
            if got != wave:
                errors.append(f"(c) {name_of(doc)}: sync-wave 기대 {wave!r}, 실제 {got!r}")

crd_note = "CRD 0개" if not crds else f"CRD {len(crds)}개"
print(f"[{app}] 컨테이너 {containers}개, {crd_note}, wave 검사 {waves}개")
if hook_skipped:
    print(f"[{app}] (a) hook 제외 {hook_skipped}개")
if exempted:
    print(f"[{app}] (b) 면제 {len(exempted)}개: {', '.join(sorted(exempted))}")
for name in sorted(exempt - set(exempted)):
    errors.append(f"(b) crd_keep_exempt 의 {name}: 렌더 결과에 없다(낡은 면제)")
for e in errors:
    print(f"FAIL [{app}] {e}")
sys.exit(1 if errors else 0)
PY
    checked=$((checked + 1))
done

echo "검사한 차트: ${checked}개"
if [ "${failed}" -ne 0 ] || [ "${checked}" -eq 0 ]; then
    exit 1
fi
