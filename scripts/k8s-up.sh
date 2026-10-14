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
#    기본 경로는 기존 머신을 재사용한다(비파괴적). 전용 머신을 강제하려면 MANAGE_MACHINE=true.
#    🔴 상태는 **셋**이다 — 실행중 / 중지 / 부재. 실행중만 재사용 대상으로 보면
#    `STOP_MACHINE=true k8s-down.sh`가 남긴 중지 머신을 「없음」으로 읽고 새 머신을 만든다(#182).
RUNNING_MACHINE=""
STOPPED_MACHINES=()
for m in $(podman machine list -q 2>/dev/null); do
    if [ "$(podman machine inspect "${m}" --format '{{.State}}' 2>/dev/null)" = "running" ]; then
        RUNNING_MACHINE="${m}"
        break
    fi
    STOPPED_MACHINES+=("${m}")
done

REUSE_MACHINE=""
if [ "${MANAGE_MACHINE:-false}" != "true" ]; then
    if [ -n "${RUNNING_MACHINE}" ]; then
        REUSE_MACHINE="${RUNNING_MACHINE}"
    elif [ "${#STOPPED_MACHINES[@]}" -gt 1 ]; then
        # 어느 것을 켤지 스크립트가 고르지 않는다 — 잘못 고르면 다른 VM의 데이터로 클러스터가 선다.
        printf '실행 중 머신이 없고 중지된 머신이 여럿이다: %s\n' "${STOPPED_MACHINES[*]}" >&2
        printf '쓸 머신을 먼저 시작하세요: podman machine start <이름>\n' >&2
        exit 1
    elif [ "${#STOPPED_MACHINES[@]}" -eq 1 ]; then
        REUSE_MACHINE="${STOPPED_MACHINES[0]}"
    fi
fi

if [ -n "${REUSE_MACHINE}" ]; then
    # rootful 판정은 시작 **전에** 한다 — rootless를 켠 뒤 실패하면 쓰지도 않을 VM만 떠 있다.
    ROOTFUL="$(podman machine inspect "${REUSE_MACHINE}" --format '{{.Rootful}}' 2>/dev/null)"
    log "기존 podman machine 재사용: ${REUSE_MACHINE} (rootful=${ROOTFUL})"
    if [ "${ROOTFUL}" != "true" ]; then
        printf 'kind(Podman provider)는 rootful 머신이 필요하나 재사용 머신이 rootless입니다.\n' >&2
        printf 'podman machine set --rootful 후 재시작하거나, MANAGE_MACHINE=true로 전용 머신(%s)을 쓰세요.\n' "${MACHINE_NAME}" >&2
        exit 1
    fi
    if [ "${REUSE_MACHINE}" != "${RUNNING_MACHINE}" ]; then
        log "podman machine 시작: ${REUSE_MACHINE}"
        podman machine start "${REUSE_MACHINE}"
    fi
else
    # 전용 머신 경로 (머신이 하나도 없거나 MANAGE_MACHINE=true) — Apple Silicon은 생성 시 자원 확정
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

# 1-1) 컨테이너 PID 상한 드롭인 — kind 노드가 받을 `pids_limit`(값·근거는 k8s-env.sh NODE_PIDS_LIMIT).
#      VM의 /etc는 재부팅에도 남는다. 내용이 같으면 쓰지 않는다(멱등).
ACTIVE_MACHINE="${REUSE_MACHINE:-${MACHINE_NAME}}"
PIDS_DROPIN="/etc/containers/containers.conf.d/50-kind-pids-limit.conf"
PIDS_CONTENT="$(printf '# k8s-up.sh가 관리한다(scripts/k8s-env.sh NODE_PIDS_LIMIT)\n[containers]\npids_limit = %s\n' "${NODE_PIDS_LIMIT}")"
if [ "$(podman machine ssh "${ACTIVE_MACHINE}" "cat ${PIDS_DROPIN} 2>/dev/null" || true)" = "${PIDS_CONTENT}" ]; then
    log "PID 상한 드롭인 유지: pids_limit=${NODE_PIDS_LIMIT}"
else
    log "PID 상한 드롭인 쓰기: ${PIDS_DROPIN} (pids_limit=${NODE_PIDS_LIMIT})"
    printf '%s\n' "${PIDS_CONTENT}" | podman machine ssh "${ACTIVE_MACHINE}" \
        "sudo mkdir -p /etc/containers/containers.conf.d && sudo tee ${PIDS_DROPIN} >/dev/null"
    # 🔴 macOS의 podman은 원격 클라이언트라 컨테이너 기본값을 **VM의 API 서비스**가 채운다. 서비스는 기동 시점의
    #    설정을 들고 있어 드롭인만 쓰면 반영되지 않는다(실측: VM 안 직접 실행은 65536, 원격은 2048).
    #    재시작해도 실행 중 컨테이너(conmon 관리)는 그대로다.
    log "podman API 서비스 재시작(드롭인 반영)"
    podman machine ssh "${ACTIVE_MACHINE}" "sudo systemctl restart podman.service"
fi
# 드롭인은 **새로 만드는 컨테이너에만** 적용된다. 이미 있는 kind 노드가 다른 값이면 재생성 전까지 옛 상한이다.
NODE_CONTAINER="${CLUSTER_NAME}-control-plane"
if podman container exists "${NODE_CONTAINER}"; then
    NODE_PIDS="$(podman inspect -f '{{.HostConfig.PidsLimit}}' "${NODE_CONTAINER}")"
    if [ "${NODE_PIDS}" != "${NODE_PIDS_LIMIT}" ]; then
        printf '⚠️  kind 노드 %s의 PidsLimit=%s (기대 %s) — 클러스터를 재생성해야 반영된다.\n' \
            "${NODE_CONTAINER}" "${NODE_PIDS}" "${NODE_PIDS_LIMIT}" >&2
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
