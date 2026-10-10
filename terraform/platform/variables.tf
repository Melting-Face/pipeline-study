# 입력 변수 - 되돌리기 어렵거나 환경과 충돌하는 값은 validation 블록으로 plan 단계에서 막는다
# (docs/conventions/terraform.md §5).
#
# kubeconfig_path·kube_context·ingress_profile·storage_class·http_host_port 5개는
# terraform/cluster/kind 의 output·변수와 "이름·값"이 같다 - substrate 계약이다.
# terraform_remote_state 를 쓰지 않으므로 양쪽에 중복 선언된다. 한쪽을 고치고 다른 쪽을
# 안 고치면 조용히 어긋난다 - 기계가 안 잡아주는 수동 책임이다.

variable "kubeconfig_path" {
  description = <<-EOT
    kind 가 쓴 kubeconfig 파일의 경로. terraform/cluster/kind 의 output "kubeconfig_path"
    와 같은 값이어야 한다(기본 ~/.kube/<cluster_name>.config). pathexpand()로 ~ 를 홈
    디렉터리로 확장한다(provider.tf). 기본 ~/.kube/config 가 아니라 전용 파일을 쓰므로
    다른 클러스터를 조용히 가리키지 않는다.
  EOT
  type        = string
  default     = "~/.kube/lakehouse.config"
}

variable "kube_context" {
  description = <<-EOT
    kubeconfig 안에서 쓸 context 이름. terraform/cluster/kind 의 output "kube_context" 와
    같은 값이어야 한다. 🔴 비우면 helm 프로바이더가 kubeconfig 의 현재 컨텍스트를 따라가
    같은 머신의 다른 kind 클러스터에 조용히 apply 할 수 있다. kind 가 만드는 context 는 항상 "kind-" 로 시작하므로 그 접두를 강제한다.
  EOT
  type        = string
  default     = "kind-lakehouse"

  validation {
    condition     = startswith(var.kube_context, "kind-")
    error_message = "kube_context 는 \"kind-\" 로 시작해야 한다 - 비우거나 다른 컨텍스트를 가리키면 의도하지 않은 클러스터에 apply 된다."
  }
}

variable "ingress_profile" {
  description = <<-EOT
    ingress-nginx values 파일을 고르는 키. terraform/cluster/kind 의 output
    "ingress_profile" 과 같은 값이어야 한다. 이 스택에는 values/ingress-nginx.kind.yaml
    만 있으므로 "kind" 외의 값은 존재하지 않는 파일을 참조해 apply 가 실패한다 - 오선택을
    plan 단계에서 막는다.
  EOT
  type        = string
  default     = "kind"

  validation {
    condition     = var.ingress_profile == "kind"
    error_message = "ingress_profile 은 \"kind\" 여야 한다 - values/ingress-nginx.<profile>.yaml 은 kind 하나뿐이라 다른 값은 파일이 없어 apply 가 실패한다."
  }
}

# substrate 계약의 일부라 "계약을 온전히 받는다"는 것 자체가 이 스택의 역할이므로, 아직
# 참조하지 않아도 선언해 둔다. PVC 를 쓰는 리소스가 이 스택에 생기면 참조를 추가하고 이
# ignore 주석은 지운다.
# tflint-ignore: terraform_unused_declarations
variable "storage_class" {
  description = <<-EOT
    이 substrate 가 기본 제공하는 StorageClass 이름. terraform/cluster/kind 의 output
    "storage_class" 와 같은 값이어야 한다. 이 스택의 리소스는 PVC 를 쓰지 않아 아직 쓰지 않는다.
  EOT
  type        = string
  default     = "standard"
}

variable "repo_url" {
  description = <<-EOT
    ApplicationSet 이 추적할 Git 저장소 URL. HTTPS 여야 한다 - 저장소가 public 이라
    ArgoCD 는 자격증명 없이 fetch 하므로 SSH(git@...) 형태는 쓸 수 없다.
  EOT
  type        = string
  default     = "https://github.com/Melting-Face/pipeline-study.git"

  validation {
    condition     = startswith(var.repo_url, "https://")
    error_message = "repo_url 은 https:// 로 시작해야 한다 - ArgoCD 가 무인증 public fetch 를 하므로 git@ 형태의 SSH URL 은 쓸 수 없다."
  }
}

variable "target_revision" {
  description = <<-EOT
    ApplicationSet 이 추적할 Git 리비전. 기본 main. PR 검증 중에는 tfvars 가 아니라
    -var target_revision=<기능 브랜치> 로만 넘긴다 - tfvars 에 쓰지 않으므로 머지 뒤
    되돌리기를 잊어도 다음 apply 가 기본값으로 돌아간다. 빈 문자열이면 ArgoCD 가 HEAD 를
    따라가 의도와 다른 리비전을 추적하므로 막는다.
  EOT
  type        = string
  default     = "main"

  validation {
    condition     = length(trimspace(var.target_revision)) > 0
    error_message = "target_revision 은 비울 수 없다 - 빈 값이면 ApplicationSet 이 기본 브랜치(HEAD)를 조용히 추적한다."
  }
}

variable "http_host_port" {
  description = <<-EOT
    호스트에서 ingress-nginx 로 들어가는 HTTP 포트. terraform/cluster/kind 의 같은 이름
    변수(extra_port_mappings 의 실제 호스트 포트)와 값이 일치해야 한다. 이 스택은 그
    포트로 트래픽을 보내지 않고 outputs.tf 의 argocd_url 을 조립하는 데만 쓴다.

    🔴 어긋나면 argocd_url 이 조용히 틀린 포트를 가리킨다. kind 의 extra_port_mappings 는
    클러스터 생성 시점에만 정할 수 있어, cluster 쪽 값을 바로잡으려면 이 변수만 고치는
    게 아니라 클러스터 재생성이 필요하다.
    8081/8444 는 병행 검증 클러스터(argocd-study)가 점유하므로 스택 A 와 같이 금지한다.
  EOT
  type        = number
  default     = 8080

  validation {
    condition     = !contains([8081, 8444], var.http_host_port)
    error_message = "8081/8444 는 병행 검증 클러스터(argocd-study)가 점유한 호스트 포트다 — 다른 포트를 쓴다."
  }
}

# helm_release.appset(argocd.tf)이 이 변수를 ApplicationSet List generator 의 elements 로 넘긴다.
variable "apps" {
  description = <<-EOT
    ApplicationSet 이 Application 으로 펼칠 앱 목록. 원소는 name·path·namespace 셋뿐이며
    이미지 태그는 넣지 않는다. 검증: name 은 중복 불가이자 DNS-1123 label(63자 이하),
    path 는 gitops/charts/ 로 시작해야 한다. 데이터 층 앱(PR2)은 겹 1 의 List 원소 제거
    동작을 검증 관문에서 판정하기 전까지 목록에서 빼지 않는다(docs/argocd-gitops.md §4).
  EOT
  type = list(object({
    name      = string
    path      = string
    namespace = string
  }))
  default = [
    { name = "cert-manager", path = "gitops/charts/cert-manager", namespace = "cert-manager" },
    { name = "cnpg-operator", path = "gitops/charts/cnpg-operator", namespace = "cnpg-system" },
    { name = "spark-operator", path = "gitops/charts/spark-operator", namespace = "spark-operator" },
    { name = "flink-operator", path = "gitops/charts/flink-operator", namespace = "flink-operator" },
    # 클러스터 밖 SeaweedFS(compose)를 가리키는 ExternalName — 데이터 층이라 Prune=false,Delete=false.
    { name = "storage-external", path = "gitops/charts/storage-external", namespace = "default" },
  ]

  validation {
    condition     = length(distinct([for a in var.apps : a.name])) == length(var.apps)
    error_message = "apps 의 name 은 중복될 수 없다 - name 이 Application 이름이라 중복되면 하나가 다른 하나를 덮어쓴다."
  }

  validation {
    condition     = alltrue([for a in var.apps : startswith(a.path, "gitops/charts/")])
    error_message = "apps 의 path 는 gitops/charts/ 로 시작해야 한다 - plain manifest 등 다른 소스 타입은 이 ApplicationSet 의 범위 밖이다."
  }

  validation {
    condition = alltrue([
      for a in var.apps : can(regex("^[a-z0-9]$|^[a-z0-9][-a-z0-9]*[a-z0-9]$", a.name)) && length(a.name) <= 63
    ])
    error_message = "apps 의 name 은 DNS-1123 label(소문자·숫자·하이픈, 양 끝은 영숫자, 63자 이하)이어야 한다 - Application 이름과 리소스 이름이 된다."
  }
}
