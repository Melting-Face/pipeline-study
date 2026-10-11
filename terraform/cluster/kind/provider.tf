# docker 프로바이더 — SeaweedFS 컨테이너를 podman 소켓으로 관리한다.
# 소켓 경로는 머신마다 달라 변수로만 받는다(variables.tf 의 container_host).
provider "docker" {
  host = var.container_host
}
