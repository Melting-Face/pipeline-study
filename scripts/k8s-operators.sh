#!/usr/bin/env bash
# **Terraform 스택 C 의 선행 조건** — 네임스페이스 3종 + cert-manager + Barman Cloud 플러그인.
# 사용: ./scripts/k8s-operators.sh
#
# 🔴 **이 스크립트는 더 이상 오퍼레이터를 설치하지 않는다**(2026-08-28 이관).
#    Spark Operator · Flink Operator · CloudNativePG 와 로컬 CA · 워크로드 RBAC 은
#    **`terraform/lakehouse-platform/`** 이 소유한다. 여기 남은 것은 **TF 가 받을 수 없는 것**들이다
#    — 원격 멀티도큐먼트 매니페스트 둘과, 그 둘·helm 이 **요구만 하고 아무도 안 만드는**
#    네임스페이스 3종(docs/architectures/terraform.md).
#
# ⚠️ **기동 순서가 바뀌었다.** 빈 클러스터 최초 구축은 **6단계**다.
#      k8s-up.sh → **이 스크립트**
#      → terraform apply -target=helm_release.{spark_operator,flink_operator,cnpg}  ← 최초 1회만
#      → k8s-poc-storage.sh → terraform apply(전체) → k8s-dagster.sh(이미지 빌드·push)
#    `k8s-poc-storage.sh` 는 CNPG CRD 를 요구하는데 그 CRD 는 이제 Terraform 이 만든다.
#    평시 재기동에서는 두 apply 가 `terraform apply` 하나로 합쳐진다.
#    상세·근거는 docs/setup.md §3.
#
# 🔴 **TF 가 소유한 리소스를 `kubectl apply` 로 다시 넣지 마라.** 서버사이드 apply 의
#    필드 소유권이 `kubectl` 로 넘어가 Terraform 이 drift 를 보고도 못 덮는다
#    (2026-08-28 실측: `conflict with "kubectl-patch"`). 고쳐야 하면 `k8s/**` 의 YAML 을
#    고치고 `terraform apply` 를 돌린다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

require_cli kubectl

kubectl config use-context "kind-${CLUSTER_NAME}"

# 1) 네임스페이스 3종 — **아무도 안 만들기 때문에 여기서 만든다**(2026-08-29 실측).
#    🔴 이건 편의가 아니라 **계약**이다. 빼면 두 곳이 죽는다:
#      ① 바로 아래 Barman 플러그인 매니페스트 — `namespace: cnpg-system` 을 **참조만 하고
#         `kind: Namespace` 는 0건**이다(v0.14.0 문서 17개 전수 확인). 빼면 ns 스코프 9건이
#         `namespaces "cnpg-system" not found` 로 죽는데, **클러스터 스코프(CRD·ClusterRole)는
#         먼저 생성돼 부분 성공으로 보인다** — 오진하기 쉬운 상태다.
#      ② `terraform/lakehouse-platform/operators.tf` 의 `helm_release` 3종 — 셋 다
#         `create_namespace = false` 다(import 가 그 플래그를 state 에 기록하지 않아 매 plan 이
#         update 로 뜨고 진짜 diff 가 묻히기 때문. PR #9 실측).
#    구 스크립트의 `--create-namespace` 가 이관 커밋에서 사라지면서 생긴 구멍이라,
#    라이브 클러스터가 도는 것은 **과거 helm 이 만들어 둔 ns 덕**이지 현행 코드 덕이 아니었다.
log "네임스페이스 준비: cnpg-system · spark-operator · flink-operator"
for ns in cnpg-system spark-operator flink-operator; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
done

# 2) cert-manager — Flink Operator 웹훅과 CNPG Barman 플러그인이 **공용**으로 요구한다.
#    원격 매니페스트 한 장이 수십 개 오브젝트를 담아 `kubernetes_manifest` 하나로 못 받는다.
#    버전이 URL 에 박혀 있어 drift 위험이 낮은 것도 셸에 남긴 이유다.
ensure_cert_manager

# 3) Barman Cloud 플러그인 — 백업·PITR. 백업 대상은 클러스터 내부 SeaweedFS(S3)라 외부 비용 0.
#    🔴 선택이 아니다 — Cluster CR이 `isWALArchiver: true`로 이 플러그인을 참조하므로
#    없으면 WAL 아카이빙이 실패한다(k8s-env.sh 주석 참고).
log "Barman Cloud 플러그인 설치 (${CNPG_BARMAN_PLUGIN_VERSION}) — CNPG 와 같은 ns(${CNPG_NS})"
kubectl apply -f \
    "https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/${CNPG_BARMAN_PLUGIN_VERSION}/manifest.yaml"
kubectl rollout status deployment -n "${CNPG_NS}" barman-cloud --timeout=180s

log "완료. 다음:"
log "  [빈 클러스터 최초 1회] 오퍼레이터만 먼저 만든다 — CRD 가 있어야 나머지가 plan 된다"
log "    terraform -chdir=${REPO_ROOT}/terraform/lakehouse-platform apply \\"
log "      -target=helm_release.spark_operator -target=helm_release.flink_operator \\"
log "      -target=helm_release.cnpg"
log "    → ./scripts/k8s-poc-storage.sh → terraform apply(전체) → ./scripts/k8s-dagster.sh"
log "  [평시 재기동] terraform -chdir=${REPO_ROOT}/terraform/lakehouse-platform apply"
