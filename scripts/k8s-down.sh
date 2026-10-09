#!/usr/bin/env bash
# 로컬 K8s 정리: kind 클러스터 + 레지스트리 삭제. podman machine은 기본 보존.
# 사용: ./scripts/k8s-down.sh
#       STOP_MACHINE=true   ./scripts/k8s-down.sh   # 머신 중지(자원 회수, 데이터 보존)
#       REMOVE_MACHINE=true ./scripts/k8s-down.sh   # 머신 삭제(데이터 소멸)
# 대상 머신은 실행 중인 머신이다(MANAGE_MACHINE=true면 MACHINE_NAME). 없으면 아무것도 지우지 않고 실패한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

STOP_MACHINE="${STOP_MACHINE:-false}"
REMOVE_MACHINE="${REMOVE_MACHINE:-false}"

require_cli kind podman

# 0) 대상 머신 판정 — **무엇이든 지우기 전에** 한다.
#    k8s-up.sh와 같은 기준: 기본 경로는 실행 중인 머신을 재사용하므로 대상도 그 머신이고,
#    MACHINE_NAME(dagster-k8s)은 MANAGE_MACHINE=true 경로 전용이다.
#    🔴 판정을 뒤에 두면 REMOVE가 레지스트리 볼륨을 먼저 지운 뒤 머신 삭제에서 실패해
#    머신은 남고 이미지만 사라진다(비가역 부분 실행, #163).
TARGET_MACHINE=""
if [ "${MANAGE_MACHINE:-false}" = "true" ]; then
    TARGET_MACHINE="${MACHINE_NAME}"
else
    for m in $(podman machine list -q 2>/dev/null); do
        if [ "$(podman machine inspect "${m}" --format '{{.State}}' 2>/dev/null)" = "running" ]; then
            TARGET_MACHINE="${m}"
            break
        fi
    done
fi

if [ "${REMOVE_MACHINE}" = "true" ] || [ "${STOP_MACHINE}" = "true" ]; then
    # 중지·삭제 모두 실행 중인 대상이 있어야 진행한다 — 볼륨 삭제도 VM이 떠 있어야 된다.
    TARGET_STATE="$(podman machine inspect "${TARGET_MACHINE:-<없음>}" --format '{{.State}}' 2>/dev/null || echo absent)"
    if [ -z "${TARGET_MACHINE}" ] || [ "${TARGET_STATE}" != "running" ]; then
        printf '실행 중인 대상 머신이 없다(대상=%s, 상태=%s). 아무것도 지우지 않고 종료한다.\n' \
            "${TARGET_MACHINE:-없음}" "${TARGET_STATE}" >&2
        printf '머신 확인: podman machine list / 전용 머신 지정: MANAGE_MACHINE=true MACHINE_NAME=<이름>\n' >&2
        exit 1
    fi
fi

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
    log "podman machine 삭제: ${TARGET_MACHINE} (데이터 소멸)"
    # 머신을 지우면 볼륨도 함께 사라지므로 여기서는 레지스트리 볼륨도 정리한다
    # (머신 보존 경로에서 지우면 "이미지가 왜 없지"가 되므로 이 분기에만 둔다).
    podman volume rm -f "${REGISTRY_NAME}-data" 2>/dev/null || true
    podman machine rm -f "${TARGET_MACHINE}"
elif [ "${STOP_MACHINE}" = "true" ]; then
    log "podman machine 중지: ${TARGET_MACHINE}"
    podman machine stop "${TARGET_MACHINE}"
else
    log "podman machine 보존: ${TARGET_MACHINE:-없음} (중지=STOP_MACHINE=true / 삭제=REMOVE_MACHINE=true)"
fi
