#!/usr/bin/env bash
# 로컬 K8s(kind on Podman) 클러스터 + 로컬 레지스트리 기동 (재설계 PoC Phase 0)
# 사용: ./scripts/k8s-up.sh
# 자원 override 예: MACHINE_MEMORY_MIB=24576 ./scripts/k8s-up.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

REGISTRY_DIR="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"

require_cli podman kind kubectl

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

# 3) kind 클러스터 생성
# 🔴 여기도 상태가 **셋**이다(§2 레지스트리와 같은 축) — 없음 / 있고 도는 중 / **있는데 중지**.
#    `kind get clusters`는 노드 컨테이너가 멈춰 있어도 이름을 그대로 보여준다. 그래서
#    "존재하면 통과"로 두면 다음 단계의 `podman exec`가 죽는다(2026-08-27 실측: 노드가
#    exit 137로 남아 있었다 — VM 재부팅·OOM이면 흔한 경로다).
if ! kind get clusters | grep -qx "${CLUSTER_NAME}"; then
    log "kind 클러스터 생성: ${CLUSTER_NAME} (provider=podman)"
    kind create cluster --name "${CLUSTER_NAME}" --config "${REPO_ROOT}/k8s/kind-cluster.yaml"
else
    log "kind 클러스터 존재: ${CLUSTER_NAME} — 중지된 노드가 있으면 기동한다"
    for node in $(kind get nodes --name "${CLUSTER_NAME}"); do
        if [ "$(podman inspect -f '{{.State.Running}}' "${node}" 2>/dev/null || echo absent)" != "true" ]; then
            log "노드 재시작: ${node}"
            podman start "${node}"
        fi
    done
    # 노드가 방금 떴다면 API 서버가 아직 안 받는다 — 응답할 때까지 기다린다.
    for _ in $(seq 1 60); do
        kubectl --context "kind-${CLUSTER_NAME}" get --raw='/readyz' >/dev/null 2>&1 && break
        sleep 5
    done
fi

# 3-1) 🔴 컨텍스트를 고정한다 — 아래 단계들(ConfigMap·ingress-nginx)은 `--context`를 주지 않아
#      **current-context 로 나간다**. 신규 생성 경로는 kind 가 컨텍스트를 자동 전환해 우연히
#      맞지만, **위의 "중지된 클러스터 재기동" 분기에는 전환이 없다** — kubeconfig 에 다른 kind
#      클러스터가 함께 있으면 엉뚱한 클러스터에 깔린다. 나머지 k8s-*.sh 셋은 이미 이 줄이 있다.
kubectl config use-context "kind-${CLUSTER_NAME}"

# 4) 각 노드에 레지스트리 hosts.toml 주입 (localhost:5001 → kind-registry:5000)
log "노드 registry certs.d 설정: ${REGISTRY_DIR}"
for node in $(kind get nodes --name "${CLUSTER_NAME}"); do
    podman exec "${node}" mkdir -p "${REGISTRY_DIR}"
    podman exec -i "${node}" sh -c "cat > '${REGISTRY_DIR}/hosts.toml'" <<EOF
[host."http://${REGISTRY_NAME}:5000"]
EOF
done

# 5) 레지스트리를 kind 네트워크에 연결 (노드가 kind-registry 이름 해석)
if [ "$(podman inspect -f '{{json .NetworkSettings.Networks.kind}}' "${REGISTRY_NAME}" 2>/dev/null || echo null)" = "null" ]; then
    log "레지스트리를 kind 네트워크에 연결"
    podman network connect kind "${REGISTRY_NAME}"
fi

# 6) local-registry-hosting ConfigMap (도구 호환 표준)
log "local-registry-hosting ConfigMap 적용"
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
    name: local-registry-hosting
    namespace: kube-public
data:
    localRegistryHosting.v1: |
        host: "localhost:${REGISTRY_PORT}"
        help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
EOF

# 7) ingress-nginx (선택) — UI 고정 URL 노출. 노드의 hostPort 80/443을 쓰므로
#    kind-cluster.yaml의 extraPortMappings가 있어야 호스트까지 닿는다.
if [ "${INSTALL_INGRESS}" = "true" ]; then
    log "ingress-nginx 설치 (${INGRESS_NGINX_VERSION}, kind provider)"
    kubectl apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-${INGRESS_NGINX_VERSION}/deploy/static/provider/kind/deploy.yaml"
    log "ingress-nginx 준비 대기"
    # `wait --for=condition=ready pod`는 파드가 아직 생성 전이면 "no matching resources found"로
    # 즉시 실패한다(2026-08-19 실측). Deployment의 rollout을 기다리면 생성 전 상태도 포함된다.
    kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s
else
    log "ingress-nginx 건너뜀 (INSTALL_INGRESS=true 로 활성화)"
fi

log "완료. 다음: ./scripts/k8s-operators.sh (네임스페이스 3종 + cert-manager + Barman)"
[ "${INSTALL_INGRESS}" = "true" ] && log "Ingress 진입점: http://<host>.localtest.me:${INGRESS_HTTP_PORT}"
log "확인: kubectl cluster-info --context kind-${CLUSTER_NAME}"
