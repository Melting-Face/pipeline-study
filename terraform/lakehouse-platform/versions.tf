# Terraform·프로바이더 버전 고정 (재현성 — latest 금지, conventions/terraform.md §2)
#
# 스택 범위는 **C(platform)** 뿐이다 — 오퍼레이터·CA·RBAC·Dagster.
# A(cluster: kind·레지스트리·ingress-nginx)와 B(data: SeaweedFS·CNPG Cluster·Secret)는
# 셸에 남는다. 분할 근거는 폭발반경이다(docs/architectures/terraform.md).
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    # 매니페스트 적용용. YAML 을 정본으로 두고 yamldecode 로 읽는다.
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38"
    }
    # 오퍼레이터 3종(Spark·Flink·CNPG). cert-manager 와 Barman 플러그인은
    # 원격 멀티도큐먼트 매니페스트라 이 스택이 다루지 않는다 — 셸에 남는다.
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
  }
}
