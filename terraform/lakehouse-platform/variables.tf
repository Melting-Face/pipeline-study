# 입력 변수 — 모두 description·type 명시(conventions/terraform.md §5).
#
# 🔴 **값의 출처는 `scripts/k8s-env.sh`다.** 이행이 끝날 때까지 두 곳에 같은 값이 산다 —
#    셸이 아직 cert-manager·Barman·클러스터 계층을 세우기 때문이다. 어느 한쪽만 바꾸면
#    "설치는 됐는데 Terraform 이 재설치를 계획하는" 상태가 된다.
#    ⇒ 차트 버전을 올릴 때는 **두 파일을 같은 커밋에서** 바꾼다.
#    기본값은 2026-08-28 기준 `k8s-env.sh` 실값과 일치시켰다.

variable "kube_config_path" {
  description = "kubeconfig 파일 경로"
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "kubeconfig 컨텍스트. kind 클러스터명 앞에 kind- 접두어가 붙는다"
  type        = string
  default     = "kind-lakehouse"

  validation {
    # 이 스택은 로컬 kind 전용이다. 원격 클러스터를 겨냥하면 폭발반경이 설계 전제를 벗어난다.
    condition     = startswith(var.kube_context, "kind-")
    error_message = "이 스택은 로컬 kind 클러스터 전용이다. 컨텍스트는 kind- 로 시작해야 한다."
  }
}

# --- Spark Operator (Apache 공식) ---
# 🔴 차트 버전 ≠ appVersion. GA appVersion 1.0.0 = chart 1.8.0 (conventions/k8s.md §9).
variable "spark_operator" {
  description = "Spark Kubernetes Operator 의 helm 좌표와 네임스페이스"
  type = object({
    namespace     = string
    release       = string
    chart         = string
    repository    = string
    chart_version = string
  })
  default = {
    namespace     = "spark-operator"
    release       = "spark-kubernetes-operator"
    chart         = "spark-kubernetes-operator"
    repository    = "https://apache.github.io/spark-kubernetes-operator"
    chart_version = "1.8.0"
  }
}

# --- Flink Operator (Apache 공식) ---
# ⚠️ 저장소 URL 에 차트 버전이 박힌다(downloads.apache.org 는 현행 릴리스만 보관한다).
#    구버전은 404 가 되어 설치가 깨지므로 버전을 올릴 때 URL 도 함께 움직인다.
variable "flink_operator" {
  description = "Flink Kubernetes Operator 의 helm 좌표. 차트 버전 = appVersion 이다"
  type = object({
    namespace        = string
    release          = string
    chart            = string
    chart_version    = string
    watch_namespaces = list(string)
  })
  default = {
    namespace        = "flink-operator"
    release          = "flink-kubernetes-operator"
    chart            = "flink-kubernetes-operator"
    chart_version    = "1.15.0"
    watch_namespaces = ["default"]
  }
}

# --- CloudNativePG ---
# 🔴 차트 0.29.0 = CNPG 1.30.0 (여기도 차트 버전 ≠ appVersion).
variable "cnpg" {
  description = "CloudNativePG 오퍼레이터의 helm 좌표"
  type = object({
    namespace     = string
    release       = string
    chart         = string
    repository    = string
    chart_version = string
  })
  default = {
    namespace     = "cnpg-system"
    release       = "cloudnative-pg"
    chart         = "cloudnative-pg"
    repository    = "https://cloudnative-pg.github.io/charts"
    chart_version = "0.29.0"
  }
}

# --- 오퍼레이터 자원 한도 ---
# 🔴 수치의 단일 출처는 docs/resource-sizing.md §(B) 배분표다.
#    Flink Operator 차트는 자원을 **선언하지 않는다**(`resources: {}`) — 값을 주지 않으면
#    BestEffort 로 떠서 `describe node` 합계에 0으로 잡히고 배분표가 처음부터 거짓이 된다.
#    Spark Operator 는 반대로 차트 기본값이 커서 낮춰 잡는다.
variable "operator_resources" {
  description = "오퍼레이터 파드 자원. 근거는 docs/resource-sizing.md §(B)"
  type = map(object({
    requests = object({ cpu = string, memory = string })
    limits   = object({ cpu = string, memory = string })
  }))
  default = {
    spark = {
      requests = { cpu = "250m", memory = "512Mi" }
      limits   = { cpu = "500m", memory = "1Gi" }
    }
    flink = {
      requests = { cpu = "200m", memory = "512Mi" }
      limits   = { cpu = "500m", memory = "1Gi" }
    }
    flink_webhook = {
      requests = { cpu = "100m", memory = "256Mi" }
      limits   = { cpu = "200m", memory = "512Mi" }
    }
    cnpg = {
      requests = { cpu = "100m", memory = "200Mi" }
      limits   = { cpu = "250m", memory = "384Mi" }
    }
  }
}
