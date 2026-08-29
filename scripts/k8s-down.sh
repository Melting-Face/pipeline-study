#!/usr/bin/env bash
# 로컬 K8s 정리: kind 클러스터 + 레지스트리 삭제. podman machine은 기본 보존.
# 사용: ./scripts/k8s-down.sh
#       STOP_MACHINE=true   ./scripts/k8s-down.sh   # 머신 중지(자원 회수, 데이터 보존)
#       REMOVE_MACHINE=true ./scripts/k8s-down.sh   # 머신 삭제(데이터 소멸)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

STOP_MACHINE="${STOP_MACHINE:-false}"
REMOVE_MACHINE="${REMOVE_MACHINE:-false}"

require_cli kind podman

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
    log "kind 클러스터 삭제: ${CLUSTER_NAME}"
    kind delete cluster --name "${CLUSTER_NAME}"
fi

if podman inspect "${REGISTRY_NAME}" >/dev/null 2>&1; then
    # 컨테이너만 지운다 — **이미지는 명명 볼륨 `${REGISTRY_NAME}-data` 에 남는다**
    # (k8s-up.sh 가 그렇게 만든다). 다음 `k8s-up.sh` 가 같은 볼륨을 그대로 이어받으므로
    # 푸시해 둔 이미지를 다시 빌드할 필요가 없다.
    log "레지스트리 컨테이너 삭제: ${REGISTRY_NAME} (이미지 볼륨은 보존)"
    podman rm -f "${REGISTRY_NAME}"
fi

if [ "${REMOVE_MACHINE}" = "true" ]; then
    log "podman machine 삭제: ${MACHINE_NAME} (데이터 소멸)"
    # 머신을 지우면 볼륨도 함께 사라지므로 여기서는 레지스트리 볼륨도 정리한다
    # (머신 보존 경로에서 지우면 "이미지가 왜 없지"가 되므로 이 분기에만 둔다).
    podman volume rm -f "${REGISTRY_NAME}-data" 2>/dev/null || true
    podman machine rm -f "${MACHINE_NAME}"
elif [ "${STOP_MACHINE}" = "true" ]; then
    log "podman machine 중지: ${MACHINE_NAME}"
    podman machine stop "${MACHINE_NAME}"
else
    log "podman machine 보존: ${MACHINE_NAME} (중지=STOP_MACHINE=true / 삭제=REMOVE_MACHINE=true)"
fi
