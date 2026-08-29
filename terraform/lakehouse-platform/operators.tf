# 오퍼레이터 3종 — Spark · Flink · CloudNativePG.
#
# 🔴 **여기 적힌 값은 추측이 아니라 `helm get values <release> -n <ns>` 실측이다**(2026-08-28).
#    기존 릴리스를 import 로 인수하는 것이 목적이므로, 판정 기준은 "에러 없음"이 아니라
#    **`terraform plan` 이 `0 to change`** 다. diff 가 뜨면 HCL 이 실제 설치값과 어긋난 것이고,
#    모르고 apply 하면 **오퍼레이터가 재설치된다.**
#
# ⚠️ cert-manager 와 Barman Cloud 플러그인은 여기 없다 — 원격 멀티도큐먼트 매니페스트를
#    `kubectl apply` 로 설치하므로 `helm_release` 로 받을 수 없다. 셸에 남는다
#    (docs/architectures/terraform.md).

locals {
  # Flink 차트 저장소 URL 에는 **버전이 박힌다**. downloads.apache.org 는 현행 릴리스만
  # 보관하므로 구버전은 404 가 되어 설치가 깨진다(2026-08-18 실측: 1.10.0 → 404).
  flink_repository = "https://downloads.apache.org/flink/flink-kubernetes-operator-${var.flink_operator.chart_version}/"
}

# --- Spark Kubernetes Operator ---
# 값 경로 주의: 자원은 `operatorDeployment.operatorPod.operatorContainer.resources` 아래다
# (`operatorPod.resources` 가 아니다 — Flink 와 경로가 다르다).
# helm 은 **모르는 키를 조용히 무시**하므로 경로를 추측하면 값이 안 먹은 채 통과한다.
resource "helm_release" "spark_operator" {
  name       = var.spark_operator.release
  repository = var.spark_operator.repository
  chart      = var.spark_operator.chart
  version    = var.spark_operator.chart_version
  namespace  = var.spark_operator.namespace
  # 🔴 import 는 `create_namespace` 를 state 에 기록하지 않는다(프로바이더 측 플래그).
  #    `true` 로 두면 인수 직후 plan 이 항상 update-in-place 를 계획하고, 그 여파로
  #    metadata 전체가 known after apply 로 표시돼 **진짜 diff 가 묻힌다**(2026-08-28 실측).
  #    ⇒ 네임스페이스는 **`scripts/k8s-operators.sh` 가 만든다(계약)**. 그 스크립트에서 빼면
  #    빈 클러스터에서 이 릴리스가 `namespaces "..." not found` 로 죽는다(2026-08-29 실측).
  create_namespace = false

  set = [
    {
      name  = "operatorDeployment.operatorPod.operatorContainer.resources.requests.cpu"
      value = var.operator_resources["spark"].requests.cpu
    },
    {
      name  = "operatorDeployment.operatorPod.operatorContainer.resources.requests.memory"
      value = var.operator_resources["spark"].requests.memory
    },
    {
      name  = "operatorDeployment.operatorPod.operatorContainer.resources.limits.cpu"
      value = var.operator_resources["spark"].limits.cpu
    },
    {
      name  = "operatorDeployment.operatorPod.operatorContainer.resources.limits.memory"
      value = var.operator_resources["spark"].limits.memory
    },
    # 잡 네임스페이스·SA 는 오퍼레이터가 만들지 않는다(`default` 를 그대로 쓴다).
    # 🔴 **`namespaces.data` 를 비워 두면 안 된다** — 차트 기본값이 비어 있고
    #    `overrideWatchedNamespaces=true` 라, 비우면 **감시 네임스페이스가 없고
    #    workload SA·rolebinding 도 안 생긴다**(잡이 조용히 안 뜬다).
    #    아래 `namespaces.data[0]` 과 `serviceAccount.name` 은 그래서 필수 값이다.
    {
      name  = "workloadResources.namespaces.create"
      value = "false"
    },
    {
      name  = "workloadResources.namespaces.data[0]"
      value = "default"
    },
    {
      name  = "workloadResources.serviceAccount.name"
      value = "spark"
    },
  ]
}

# --- Flink Kubernetes Operator ---
# 🔴 이 릴리스만 `--set` 과 `--values` 를 **둘 다** 쓴다. 값 파일에는 오퍼레이터
#    기동 설정(`flink-conf.yaml`)이 들어 있고, 그중 `user.artifacts.allowed-schemes: local`
#    은 **공급망 통제**다(https 를 빼서 런타임 외부 jar fetch 경로를 닫았다).
#    파일을 빠뜨리면 그 통제가 조용히 풀린다.
resource "helm_release" "flink_operator" {
  name       = var.flink_operator.release
  repository = local.flink_repository
  chart      = var.flink_operator.chart
  version    = var.flink_operator.chart_version
  namespace  = var.flink_operator.namespace
  # 🔴 import 는 `create_namespace` 를 state 에 기록하지 않는다(프로바이더 측 플래그).
  #    `true` 로 두면 인수 직후 plan 이 항상 update-in-place 를 계획하고, 그 여파로
  #    metadata 전체가 known after apply 로 표시돼 **진짜 diff 가 묻힌다**(2026-08-28 실측).
  #    ⇒ 네임스페이스는 **`scripts/k8s-operators.sh` 가 만든다(계약)**. 그 스크립트에서 빼면
  #    빈 클러스터에서 이 릴리스가 `namespaces "..." not found` 로 죽는다(2026-08-29 실측).
  create_namespace = false

  values = [file("${path.module}/../../k8s/flink/operator-values.yaml")]

  set = concat(
    [
      for idx, ns in var.flink_operator.watch_namespaces : {
        name  = "watchNamespaces[${idx}]"
        value = ns
      }
    ],
    [
      # 🔴 차트가 자원을 **선언하지 않는다**(`operatorPod.resources: {}`).
      #    값을 주지 않으면 BestEffort 로 떠서 `describe node` 합계에 0으로 잡히고
      #    docs/resource-sizing.md 의 배분표 두 행이 처음부터 거짓이 된다.
      {
        name  = "operatorPod.resources.requests.cpu"
        value = var.operator_resources["flink"].requests.cpu
      },
      {
        name  = "operatorPod.resources.requests.memory"
        value = var.operator_resources["flink"].requests.memory
      },
      {
        name  = "operatorPod.resources.limits.cpu"
        value = var.operator_resources["flink"].limits.cpu
      },
      {
        name  = "operatorPod.resources.limits.memory"
        value = var.operator_resources["flink"].limits.memory
      },
      {
        name  = "operatorPod.webhook.resources.requests.cpu"
        value = var.operator_resources["flink_webhook"].requests.cpu
      },
      {
        name  = "operatorPod.webhook.resources.requests.memory"
        value = var.operator_resources["flink_webhook"].requests.memory
      },
      {
        name  = "operatorPod.webhook.resources.limits.cpu"
        value = var.operator_resources["flink_webhook"].limits.cpu
      },
      {
        name  = "operatorPod.webhook.resources.limits.memory"
        value = var.operator_resources["flink_webhook"].limits.memory
      },
    ]
  )
}

# --- CloudNativePG ---
# 자원 경로가 최상위 `resources` 다 — 위 둘과 또 다르다.
resource "helm_release" "cnpg" {
  name       = var.cnpg.release
  repository = var.cnpg.repository
  chart      = var.cnpg.chart
  version    = var.cnpg.chart_version
  namespace  = var.cnpg.namespace
  # 🔴 import 는 `create_namespace` 를 state 에 기록하지 않는다(프로바이더 측 플래그).
  #    `true` 로 두면 인수 직후 plan 이 항상 update-in-place 를 계획하고, 그 여파로
  #    metadata 전체가 known after apply 로 표시돼 **진짜 diff 가 묻힌다**(2026-08-28 실측).
  #    ⇒ 네임스페이스는 **`scripts/k8s-operators.sh` 가 만든다(계약)**. 그 스크립트에서 빼면
  #    빈 클러스터에서 이 릴리스가 `namespaces "..." not found` 로 죽는다(2026-08-29 실측).
  create_namespace = false

  set = [
    {
      name  = "resources.requests.cpu"
      value = var.operator_resources["cnpg"].requests.cpu
    },
    {
      name  = "resources.requests.memory"
      value = var.operator_resources["cnpg"].requests.memory
    },
    {
      name  = "resources.limits.cpu"
      value = var.operator_resources["cnpg"].limits.cpu
    },
    {
      name  = "resources.limits.memory"
      value = var.operator_resources["cnpg"].limits.memory
    },
  ]
}
