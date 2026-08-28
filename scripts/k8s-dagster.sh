#!/usr/bin/env bash
# Dagster in-cluster 배포: 이미지 빌드·push → 메타 DB → RBAC → Deployment/Service/Ingress
# 사용: ./scripts/k8s-dagster.sh [--skip-build]
# 전제: ./scripts/k8s-up.sh → k8s-operators.sh → k8s-poc-storage.sh 가 먼저 끝나 있어야 한다
#       (Secret `dagster-meta-pg-app`·버킷 `dagster-logs`·CNPG Cluster가 그 단계에서 생긴다).
#
# 이 스크립트는 절차형이다 — 선언은 위, 실행은 아래, 보조 함수로 쪼개지 않는다
# (CLAUDE.md §scripts 규칙: 실행 순서 = 읽는 순서).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

SKIP_BUILD=false
if [ "${1:-}" = "--skip-build" ]; then
    SKIP_BUILD=true
fi

IMAGE="localhost:${REGISTRY_PORT}/${DAGSTER_IMAGE_NAME}:${DAGSTER_IMAGE_TAG}"
BUILD_CONTEXT="${REPO_ROOT}/dagster/dockerfile.d"
SPARKAPP_SRC="${REPO_ROOT}/k8s/spark/sparkapplication-poc.yaml"

require_cli kubectl
kubectl config use-context "kind-${CLUSTER_NAME}"

# 0) 매니페스트가 참조하는 태그와 이 스크립트가 만드는 태그가 같은지 대조.
#    어긋나면 "빌드는 성공했는데 파드는 옛 이미지"가 되어 조용히 통과한다.
MANIFEST_TAGS="$(grep -oE 'image: localhost:[0-9]+/[a-z-]+:[0-9.]+' \
    "${REPO_ROOT}/k8s/dagster/dagster-deploy.yaml" | sed 's/^image: //' | sort -u)"
if [ "$(printf '%s\n' "${MANIFEST_TAGS}" | wc -l | tr -d ' ')" != "1" ] \
    || [ "${MANIFEST_TAGS}" != "${IMAGE}" ]; then
    printf '매니페스트의 이미지 참조가 %s 와 다르거나 여러 개다:\n%s\n' \
        "${IMAGE}" "${MANIFEST_TAGS}" >&2
    printf 'k8s/dagster/dagster-deploy.yaml 과 DAGSTER_IMAGE_TAG 를 맞춘 뒤 다시 실행하라.\n' >&2
    exit 1
fi

# 1) 이미지 빌드·push. `kind load`는 불필요하다 — k8s-up.sh가 certs.d를 주입해
#    `localhost:5001`이 호스트·클러스터 공통 이름이 돼 있다.
if [ "${SKIP_BUILD}" = "false" ]; then
    require_cli podman
    log "이미지 빌드: ${IMAGE}"
    podman build -t "${IMAGE}" "${BUILD_CONTEXT}"

    log "이미지 push: ${IMAGE}"
    podman push --tls-verify=false "${IMAGE}"

    # 🔴 "에러가 안 났다"로 닫지 않는다 — 레지스트리에서 태그를 되읽어 양성 증거를 만든다.
    log "레지스트리 태그 확인"
    curl -sf "http://localhost:${REGISTRY_PORT}/v2/${DAGSTER_IMAGE_NAME}/tags/list" \
        | grep -q "\"${DAGSTER_IMAGE_TAG}\"" \
        || { printf 'push 후에도 레지스트리에 태그가 없다: %s\n' "${IMAGE}" >&2; exit 1; }
fi

# 2) 🔴 **매니페스트는 여기서 적용하지 않는다**(2026-08-28 이관).
#    `dagster-meta-db.yaml`·`dagster-rbac.yaml`·`dagster-deploy.yaml` 은
#    `terraform/lakehouse-platform/` 이 소유한다. `kubectl apply` 로 다시 넣으면
#    서버사이드 apply 의 **필드 소유권이 kubectl 로 넘어가** Terraform 이 drift 를
#    보고도 못 덮는다(실측: `conflict with "kubectl-patch"`).
#    ⇒ 매니페스트를 고쳤으면 `terraform -chdir=terraform/lakehouse-platform apply`.
#
#    남은 것은 **Terraform 이 다루지 않는 둘**이다 — 이미지(위)와 아래 ConfigMap.

# 3) SparkApplication 매니페스트를 ConfigMap으로 주입.
#    🔴 매번 **레포 파일에서 다시 만든다** — ConfigMap은 사본이라 그대로 두면 레포와 갈리고,
#    그러면 "클러스터가 정본 대신 답하는" 이중 존재가 하나 더 생긴다.
log "ConfigMap spark-app-manifests 갱신 (정본 = 레포 파일)"
kubectl create configmap spark-app-manifests -n default \
    --from-file="$(basename "${SPARKAPP_SRC}")=${SPARKAPP_SRC}" \
    --dry-run=client -o yaml | kubectl apply -f -

# 4) rollout 대기 — 배포 자체는 Terraform 이 했다. 여기서는 **수렴을 기다리기만** 한다.
#    이미지 태그를 올린 뒤 이 스크립트를 돌리는 흐름이라, 새 이미지로 파드가 뜨는 것을
#    확인할 자리가 필요하다.
# 코드 로케이션 로드(dbt manifest + pyspark import)가 느려 넉넉히 준다.
# `wait --for=condition=ready pod`가 아니라 rollout status를 쓴다 — 파드 생성 전이면
# 전자는 즉시 실패한다(conventions/k8s.md §10).
log "rollout 대기"
kubectl -n default rollout status deploy/dagster-webserver --timeout=600s
kubectl -n default rollout status deploy/dagster-daemon --timeout=600s

# 6) 대상 정합 확인 — 200이 아니라 **버전 JSON**이 나오는지 본다.
#    ingress-nginx의 default backend도 200을 줄 수 있어 상태코드로는 판별이 안 된다.
#
# 🔴 재시도가 필요하다. `rollout status`가 끝나도 nginx가 Ingress를 반영하기까지 몇 초가 더 걸려,
#    한 번만 찔러보면 **정상 배포를 실패로 판정**한다(2026-08-27 실측 — 직후엔 실패하고
#    수십 초 뒤 같은 curl이 버전 JSON을 돌려줬다). 게이트가 틀린 게 아니라 시점이 틀렸다.
log "Ingress 대상 정합 확인 (최대 60초)"
for attempt in $(seq 1 12); do
    if curl -sf -m 5 "http://${DAGSTER_INGRESS_HOST}:${INGRESS_HTTP_PORT}/server_info" \
        | grep -q dagster_webserver_version; then
        log "확인됨(시도 ${attempt}): http://${DAGSTER_INGRESS_HOST}:${INGRESS_HTTP_PORT} 가 Dagster를 향한다"
        break
    fi
    if [ "${attempt}" = "12" ]; then
        printf '/server_info 에서 버전 JSON을 못 받았다. 다음을 확인하라 —\n' >&2
        printf '  kubectl -n default get endpoints dagster-webserver\n' >&2
        printf '  kubectl -n default describe ingress dagster-webserver\n' >&2
        exit 1
    fi
    sleep 5
done

log "완료. UI: http://${DAGSTER_INGRESS_HOST}:${INGRESS_HTTP_PORT}"
