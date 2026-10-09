#!/usr/bin/env bash
# 로컬 K8s 부트스트랩 공용 설정·헬퍼 (k8s-up/down/poc-storage.sh가 source)
# 값은 이 파일 한 곳에서 관리(단일 출처). 자원 수치 근거: docs/resource-sizing.md
# 모든 값은 환경변수로 override 가능(예: MACHINE_MEMORY_MIB=24576 ./scripts/k8s-up.sh)

# --- 클러스터 / podman machine ---
CLUSTER_NAME="${CLUSTER_NAME:-lakehouse}"
MACHINE_NAME="${MACHINE_NAME:-dagster-k8s}"
# 🔴 **단위 축 주의 — 아래 값은 VM 총량이지 노드 Allocatable이 아니다.**
#    실측(2026-08-27 10:08 KST, `podman machine inspect podman-machine-default`):
#    CPUs=8 / Memory=26702 MiB / DiskSize=93 GiB.
#    같은 시점 노드 Allocatable은 **26054Mi(raw 26679964Ki, 내림)로 약 648MiB 적다**(VM 커널·kubelet 예약분).
#    예산을 짤 때 두 축을 섞으면 안 된다 — 계획서 초안이 22528(VM 축 추정치)을 쓰다 360MiB 부족을 냈다.
# 🔴 이 값은 **k8s-up.sh가 머신을 만들 때만** 쓰인다. MANAGE_MACHINE=false가 실행 중 머신을
#    재사용하므로, 머신이 이미 있으면 여기를 고쳐도 현재 머신에는 반영되지 않는다.
#    목적은 **머신 없는 상태에서 처음 돌리는 사람이 같은 크기의 머신을 받게 하는 것**이다.
#    ⚠️ 이것을 "podman이 사후 변경을 못 한다"로 읽지 마라 — **두 축은 다르다.**
#    `podman machine set --cpus/--memory/--disk-size`는 **중지 상태에서 변경된다**(2026-08-27 반증:
#    22888 → 26702 MiB로 바뀐 뒤에도 kind 클러스터 `lakehouse`와 PVC 2종이 그대로 살아 있었다).
#    바꾼 뒤에는 **여기와 docs/resource-sizing.md §(A)를 함께 갱신**한다 — 안 그러면 선언이 죽은 값이 된다.
# ⚠️ MACHINE_NAME(dagster-k8s)은 **MANAGE_MACHINE=true 경로 전용**이다. 현재 실체는 기존
#    `podman-machine-default`를 재사용 중이며 `dagster-k8s` 머신은 존재하지 않는다(k8s-up.sh:19-28).
MACHINE_CPUS="${MACHINE_CPUS:-8}"
MACHINE_MEMORY_MIB="${MACHINE_MEMORY_MIB:-26702}"    # =26.08 GiB(28.0 GB 십진), VM 총량
MACHINE_DISK_GIB="${MACHINE_DISK_GIB:-93}"           # =99.9 GB 십진

# --- 로컬 레지스트리 (호스트·클러스터 공통 이름 localhost:5001) ---
REGISTRY_NAME="${REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5001}"
REGISTRY_IMAGE="${REGISTRY_IMAGE:-docker.io/library/registry:2.8.3}"

# --- 클러스터·플랫폼 설정의 정본은 이 파일이 아니다 ---
# 클러스터(kind 노드·certs.d·레지스트리 네트워크 연결)는 `terraform/cluster/kind`가,
# 오퍼레이터·ingress·cert-manager는 ArgoCD(`gitops/`)가 소유한다.
# 이 스크립트 묶음이 하는 일은 podman 머신과 레지스트리 컨테이너를 준비하는 것까지다.

# --- kubeconfig: 기본 ~/.kube/config 대신 클러스터 전용 파일을 쓴다 ---
# 경로는 `terraform/cluster/kind`의 kubeconfig_path 기본값(~/.kube/<cluster_name>.config)과 같아야 한다.
export KUBECONFIG="${KUBECONFIG_PATH:-$HOME/.kube/${CLUSTER_NAME}.config}"

# kind Podman provider(experimental) — rootful 머신 필요
export KIND_EXPERIMENTAL_PROVIDER=podman

# --- 헬퍼 ---
log() {
    printf '\033[1;34m[k8s]\033[0m %s\n' "$*"
}

# 현재 컨텍스트가 kind-${CLUSTER_NAME}이 아니면 이유를 출력하고 1을 반환한다.
# source 시점에는 호출하지 않는다 — k8s-up.sh는 클러스터가 생기기 전에 돌기 때문이다.
# 클러스터에 kubectl/helm을 쓰는 스크립트가 그 앞에서 명시적으로 호출한다.
require_cluster_context() {
    local expected="kind-${CLUSTER_NAME}" current
    current="$(kubectl config current-context 2>/dev/null || true)"
    if [ "${current}" != "${expected}" ]; then
        printf '컨텍스트 불일치: 현재 "%s", 기대 "%s" (KUBECONFIG=%s)\n' \
            "${current:-없음}" "${expected}" "${KUBECONFIG}" >&2
        return 1
    fi
}

# 필수 CLI 존재 확인, 없으면 종료
require_cli() {
    local missing=0 cli
    for cli in "$@"; do
        if ! command -v "${cli}" >/dev/null 2>&1; then
            printf '필수 CLI 없음: %s\n' "${cli}" >&2
            missing=1
        fi
    done
    if [ "${missing}" -ne 0 ]; then
        printf '설치 후 재시도: brew install podman kind kubectl helm\n' >&2
        exit 1
    fi
}
