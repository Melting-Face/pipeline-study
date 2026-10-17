#!/usr/bin/env bash
# argocd CLI 를 core 모드(--core, API 서버·로그인 없이 Kubernetes 직접)로 이 클러스터에 붙여 실행한다.
#
# 사용법: scripts/argocd-core.sh <argocd 하위 명령...>
#   예: scripts/argocd-core.sh app list
#       scripts/argocd-core.sh app diff spark-operator          # 종료코드 0=차이 없음, 1=차이 있음
#       scripts/argocd-core.sh app sync spark-operator --dry-run
#       scripts/argocd-core.sh app sync spark-operator           # 손 sync(RollingSync 아래 drift 복구)
#       scripts/argocd-core.sh appset generate <렌더한 ApplicationSet.yaml>
#
# 왜 래퍼인가: core 모드는 ArgoCD 네임스페이스를 kubeconfig 컨텍스트의 기본 네임스페이스로 읽는다.
# 전용 kubeconfig(~/.kube/<cluster_name>.config)의 컨텍스트는 default 를 가리켜, 그대로 부르면
# `configmap "argocd-cm" not found` 로 죽는다. 그 파일은 kind(terraform/cluster/kind)가 쓰므로 고치지 않고,
# **임시 사본에만** namespace=argocd 를 지정한 뒤 끝나면 지운다(사본에 클라이언트 키가 있어 600·trap 삭제).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${root}/scripts/k8s-env.sh"
require_cluster_context

context="kind-${CLUSTER_NAME}"
tmp_kc="$(mktemp)"
trap 'rm -f "${tmp_kc}"' EXIT
install -m 600 "${KUBECONFIG}" "${tmp_kc}"
KUBECONFIG="${tmp_kc}" kubectl config set-context "${context}" --namespace=argocd > /dev/null

KUBECONFIG="${tmp_kc}" argocd "$@" --core --kube-context "${context}"
