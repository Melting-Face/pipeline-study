#!/usr/bin/env bash
# 로컬 K8s 선행 조건 기동: podman machine + 로컬 레지스트리 컨테이너
# kind 클러스터·certs.d·레지스트리 네트워크 연결은 terraform/cluster/kind가 소유한다.
# 사용: ./scripts/k8s-up.sh
# 자원 override 예: MACHINE_MEMORY_MIB=24576 ./scripts/k8s-up.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

require_cli podman

# 1) podman machine — macOS podman은 동시 1개 VM만 활성.
#    이미 실행 중인 머신이 있으면 재사용(비파괴적). 전용 머신을 강제하려면 MANAGE_MACHINE=true.
RUNNING_MACHINE=""
for m in $(podman machine list -q 2>/dev/null); do
    if [ "$(podman machine inspect "${m}" --format '{{.State}}' 2>/dev/null)" = "running" ]; then
        RUNNING_MACHINE="${m}"
        break
    fi
done

if [ -n "${RUNNING_MACHINE}" ] && [ "${MANAGE_MACHINE:-false}" != "true" ]; then
    ROOTFUL="$(podman machine inspect "${RUNNING_MACHINE}" --format '{{.Rootful}}' 2>/dev/null)"
    log "실행 중 podman machine 재사용: ${RUNNING_MACHINE} (rootful=${ROOTFUL})"
    if [ "${ROOTFUL}" != "true" ]; then
        printf 'kind(Podman provider)는 rootful 머신이 필요하나 재사용 머신이 rootless입니다.\n' >&2
        printf 'podman machine set --rootful 후 재시작하거나, MANAGE_MACHINE=true로 전용 머신(%s)을 쓰세요.\n' "${MACHINE_NAME}" >&2
        exit 1
    fi
else
    # 전용 머신 경로 (실행 중 머신이 없거나 MANAGE_MACHINE=true) — Apple Silicon은 생성 시 자원 확정
    if ! podman machine inspect "${MACHINE_NAME}" >/dev/null 2>&1; then
        log "podman machine 생성: ${MACHINE_NAME} (cpus=${MACHINE_CPUS}, mem=${MACHINE_MEMORY_MIB}MiB, disk=${MACHINE_DISK_GIB}GiB, rootful)"
        podman machine init "${MACHINE_NAME}" \
            --rootful \
            --cpus "${MACHINE_CPUS}" \
            --memory "${MACHINE_MEMORY_MIB}" \
            --disk-size "${MACHINE_DISK_GIB}"
    fi
    if [ -n "${RUNNING_MACHINE}" ] && [ "${RUNNING_MACHINE}" != "${MACHINE_NAME}" ]; then
        log "다른 머신 중지: ${RUNNING_MACHINE} (동시 1개 제약, MANAGE_MACHINE=true)"
        podman machine stop "${RUNNING_MACHINE}"
    fi
    if [ "$(podman machine inspect "${MACHINE_NAME}" --format '{{.State}}' 2>/dev/null)" != "running" ]; then
        log "podman machine 시작: ${MACHINE_NAME}"
        podman machine start "${MACHINE_NAME}"
    fi
fi

# 2) 로컬 레지스트리 컨테이너 (127.0.0.1:5001)
# 🔴 상태는 **셋**이다 — 실행중 / 중지(컨테이너는 존재) / 부재.
#    이분법(`Running != true` → `podman run`)으로 보면 중지 상태에서
#    `the container name "kind-registry" is already in use`로 죽는다(2026-08-27 실측).
#    머신을 재부팅하면 `--restart=always`가 있어도 중지 상태로 남을 수 있어 흔한 경로다.
if [ "$(podman inspect -f '{{.State.Running}}' "${REGISTRY_NAME}" 2>/dev/null || echo absent)" = "true" ]; then
    log "로컬 레지스트리 실행중: ${REGISTRY_NAME}"
elif podman container exists "${REGISTRY_NAME}"; then
    log "로컬 레지스트리 재시작: ${REGISTRY_NAME} → 127.0.0.1:${REGISTRY_PORT}"
    podman start "${REGISTRY_NAME}"
else
    log "로컬 레지스트리 기동: ${REGISTRY_NAME} → 127.0.0.1:${REGISTRY_PORT}"
    # 🔴 **명명 볼륨**이다. 익명 볼륨으로 두면 `k8s-down.sh`의 `podman rm -f` 한 번에
    #    푸시해 둔 이미지가 전부 사라지고, 그걸 되돌리는 유일한 길이 전량 재빌드다.
    #    이름을 붙이면 컨테이너를 지워도 볼륨이 남아 다음 `podman run`이 그대로 이어받는다
    #    (정말 지우려면 `podman volume rm ${REGISTRY_NAME}-data`).
    podman run -d --restart=always \
        -p "127.0.0.1:${REGISTRY_PORT}:5000" \
        -v "${REGISTRY_NAME}-data:/var/lib/registry" \
        --name "${REGISTRY_NAME}" "${REGISTRY_IMAGE}"
fi

log "완료: podman machine + 레지스트리(127.0.0.1:${REGISTRY_PORT}) 준비됨"
log "다음: terraform -chdir=terraform/cluster/kind apply (클러스터), 이후 platform 스택"
