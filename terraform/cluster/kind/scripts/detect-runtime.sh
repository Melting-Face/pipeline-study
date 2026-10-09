#!/usr/bin/env bash
# 컨테이너 런타임 자동탐지 — kind 라이브러리(DetectNodeProvider)와 같은 순서를 재현한다.
#
# 왜 이 순서인가 (kind 라이브러리 소스 기준):
#   tehcyx/terraform-provider-kind 의 kind/resource_cluster.go 는 런타임 옵션 없이
#   cluster.NewProvider(cluster.ProviderWithLogger(...))를 호출한다. 옵션이 없으면
#   kubernetes-sigs/kind 의 pkg/cluster/provider.go 가 DetectNodeProvider()로 떨어지고,
#   그 함수는 docker -> nerdctl -> podman 순서로 IsAvailable()을 본다.
#   이 스크립트는 그 탐지 결과를 Terraform 의 `data "external"`로 가져와
#   `lifecycle.precondition`이 "우리가 기대한 런타임과 실제 탐지 결과가 같은가"를
#   plan 단계에서 검증할 수 있게 한다.
#
# 🔴 한계: 이 스크립트도, precondition 도 "바이너리가 PATH 에 있는가"만 본다.
#   런타임이 실제로 동작하는가(예: podman machine 이 떠 있는가)는 검증하지 못한다 —
#   podman machine 이 중지돼 있어도 탐지는 통과한다.
set -euo pipefail

detected="none"

if command -v docker >/dev/null 2>&1; then
    detected="docker"
elif command -v nerdctl >/dev/null 2>&1; then
    detected="nerdctl"
elif command -v podman >/dev/null 2>&1; then
    detected="podman"
fi

printf '{"detected":"%s"}\n' "$detected"
