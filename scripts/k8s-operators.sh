#!/usr/bin/env bash
# cert-manager + Barman Cloud 플러그인 설치.
# 사용: ./scripts/k8s-operators.sh
#
# 🔴 **이 스크립트는 더 이상 오퍼레이터를 설치하지 않는다**(2026-08-28 이관).
#    Spark Operator · Flink Operator · CloudNativePG 와 로컬 CA · 워크로드 RBAC 은
#    **`terraform/lakehouse-platform/`** 이 소유한다. 여기 남은 둘은 원격 멀티도큐먼트
#    매니페스트라 `helm_release` 로 받을 수 없어 셸에 남았다
#    (docs/architectures/terraform.md).
#
# ⚠️ **기동 순서가 바뀌었다.**
#      k8s-up.sh → **이 스크립트** → `terraform -chdir=terraform/lakehouse-platform apply`
#      → k8s-poc-storage.sh → k8s-dagster.sh(이미지 빌드·push)
#    `k8s-poc-storage.sh` 는 CNPG CRD 를 요구하는데 그 CRD 는 이제 Terraform 이 만든다.
#    ⚠️ 이 순서는 **처음부터 재구축해 검증한 적이 없다**(클러스터를 지워야 한다).
#    docs/setup.md 절차 문서 갱신도 아직이다 — 의도적 보류, 재구축 검증과 한 벌로 한다.
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

# 1) cert-manager — Flink Operator 웹훅과 CNPG Barman 플러그인이 **공용**으로 요구한다.
#    원격 매니페스트 한 장이 수십 개 오브젝트를 담아 `kubernetes_manifest` 하나로 못 받는다.
#    버전이 URL 에 박혀 있어 drift 위험이 낮은 것도 셸에 남긴 이유다.
ensure_cert_manager

# 2) Barman Cloud 플러그인 — 백업·PITR. 백업 대상은 클러스터 내부 SeaweedFS(S3)라 외부 비용 0.
#    🔴 선택이 아니다 — Cluster CR이 `isWALArchiver: true`로 이 플러그인을 참조하므로
#    없으면 WAL 아카이빙이 실패한다(k8s-env.sh 주석 참고).
log "Barman Cloud 플러그인 설치 (${CNPG_BARMAN_PLUGIN_VERSION}) — CNPG 와 같은 ns(${CNPG_NS})"
kubectl apply -f \
    "https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/${CNPG_BARMAN_PLUGIN_VERSION}/manifest.yaml"
kubectl rollout status deployment -n "${CNPG_NS}" barman-cloud --timeout=180s

log "완료. 다음: terraform -chdir=${REPO_ROOT}/terraform/lakehouse-platform apply"
log "  (오퍼레이터 3종·로컬 CA·워크로드 RBAC·Dagster 를 Terraform 이 만든다)"
