# 스택 A (substrate) — kind 클러스터.
# required_version·프로바이더 버전은 전부 정확히 고정한다 (latest·범위 금지, Global Constraints).
#
# tehcyx/kind 0.11.0 은 sigs.k8s.io/kind v0.31.0 을 벤더링한다(GitHub 릴리스 기준).
# 이 스택의 node_image 기본값(variables.tf)은 그 kind 버전의 기본 이미지와 맞춘다.
#
# kreuzwerker/docker 3.9.0 은 SeaweedFS 컨테이너용이다 — podman 소켓으로 붙는 것을 spike 에서 실측한 버전.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "0.11.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "2.4.2"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = "3.9.0"
    }
  }
}
