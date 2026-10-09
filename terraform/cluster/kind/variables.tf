# 입력 변수 — 되돌리기 어렵거나 환경과 충돌하는 값은 validation 블록으로 plan 단계에서 막는다
# (docs/conventions/terraform.md §5).

variable "cluster_name" {
  description = "kind 클러스터 이름. kube context 는 \"kind-<cluster_name>\" 형태로 파생된다."
  type        = string
  default     = "lakehouse"
}

variable "node_image" {
  description = <<-EOT
    kind 노드(컨트롤플레인) 이미지. tehcyx/kind 0.11.0 이 벤더링하는
    sigs.k8s.io/kind v0.31.0 의 기본 이미지(Kubernetes v1.35.0)로 고정한다.
    다이제스트까지 고정해 재현성을 보장한다(latest 금지).
  EOT
  type        = string
  default     = "kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f"
}

variable "http_host_port" {
  description = <<-EOT
    호스트에서 Ingress 80 포트로 매핑할 포트. 기본 8080.
    8081/8444 는 병행 검증 클러스터(argocd-study)가 점유하므로 이 스택에서는 금지한다.

    이 변수는 extra_port_mappings(main.tf)의 실제 호스트 포트를 정한다 — kind 노드는
    컨테이너라 공개 포트를 클러스터 **생성 시점에만** 정할 수 있으므로, 값을 바로잡으려면
    변수만 고치는 게 아니라 **클러스터 재생성**이 필요하다.
    terraform/platform 스택의 같은 이름 변수와 값이 일치해야 한다(양쪽 중복 선언).
  EOT
  type        = number
  default     = 8080

  validation {
    condition     = !contains([8081, 8444], var.http_host_port)
    error_message = "8081/8444 는 병행 검증 클러스터(argocd-study)가 점유한 호스트 포트다 — 다른 포트를 쓴다."
  }
}

variable "https_host_port" {
  description = <<-EOT
    호스트에서 Ingress 443 포트로 매핑할 포트. 기본 8443.
    8081/8444 는 병행 검증 클러스터(argocd-study)가 점유하므로 이 스택에서는 금지한다.
  EOT
  type        = number
  default     = 8443

  validation {
    condition     = !contains([8081, 8444], var.https_host_port)
    error_message = "8081/8444 는 병행 검증 클러스터(argocd-study)가 점유한 호스트 포트다 — 다른 포트를 쓴다."
  }
}

variable "expected_runtime" {
  description = <<-EOT
    kind 이 선택할 것으로 "기대하는" 컨테이너 런타임. 이 값을 "고정"할 수는 없다 —
    tehcyx/kind 는 런타임 옵션을 넘기지 않으므로 kind 라이브러리의 DetectNodeProvider()가
    docker -> nerdctl -> podman 순으로 자동탐지한다. 이 변수는 그 탐지 결과가
    우리가 알고 있는 값과 같은지 main.tf 의 precondition 에서 대조하는 데만 쓰인다 —
    즉 "조용한 변경 차단"이지 "런타임 고정"이 아니다.
  EOT
  type        = string
  default     = "podman"

  validation {
    condition     = contains(["docker", "nerdctl", "podman"], var.expected_runtime)
    error_message = "expected_runtime 은 kind 가 자동탐지하는 순서(docker, nerdctl, podman) 중 하나여야 한다."
  }
}

variable "kubeconfig_path" {
  description = <<-EOT
    kind 가 쓸 kubeconfig 파일 경로. null 이면 ~/.kube/<cluster_name>.config 로 채운다
    (main.tf 의 local.kubeconfig_path). 기본 kubeconfig(~/.kube/config)를 오염시키지 않기
    위해 전용 경로를 쓴다 — 비우면 기존 lakehouse 컨텍스트와 섞인다.
  EOT
  type        = string
  default     = null
}
