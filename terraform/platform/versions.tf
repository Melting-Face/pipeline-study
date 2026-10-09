# 스택 B (platform) - ingress-nginx · argo-cd · appset.
# required_version·프로바이더 버전은 전부 정확히 고정한다(latest·범위 금지).
#
# hashicorp/helm 3.0.2: argo-cd 차트 10.9.6 / ArgoCD v3.5.3 은 docs/argocd-gitops.md D10 에서
# argocd-study 와 같은 짝으로 고정한 버전이고, 그 짝은 helm provider 3.0 라인에서 검증됐다
# (근거는 argocd-study 저장소에 있다). 3.0.x 중 그 패치로 고정한다. kubernetes provider 는
# 쓰지 않는다 - kubernetes_manifest 대신 helm_release 두 개로 CR 을 만든다(argocd.tf).
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.0.2"
    }
  }
}
