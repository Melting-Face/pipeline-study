# ingress-nginx — 클러스터 진입 설비. ArgoCD 가 아니라 이 스택(Terraform)이 설치한다
# (docs/argocd-gitops.md §3 — ArgoCD UI 의 노출 경로라 ArgoCD 가 자기 진입로를 소유하면 닭-달걀이 된다).
#
# 차트 버전 4.15.1 은 ingress-nginx 공식 헬름 레포지토리(https://kubernetes.github.io/ingress-nginx)에서
# 확인한 안정판이다(docs/argocd-gitops.md D10 - argocd-study 와 같은 짝으로 고정). 차트의
# kubeVersion 제약(">=1.21.0-0")은 terraform/cluster/kind 의 node_image 로 충족된다.
resource "helm_release" "ingress_nginx" {
  name             = "ingress-nginx"
  repository       = "https://kubernetes.github.io/ingress-nginx"
  chart            = "ingress-nginx"
  version          = "4.15.1"
  namespace        = "ingress-nginx"
  create_namespace = true

  # 🔴 wait = true — ArgoCD 의 admission webhook 경합 대비. ingress-nginx
  # 가 ValidatingWebhookConfiguration 을 설치하는데, 이 릴리스가 "준비됨"을 보고하기 전에
  # ArgoCD 쪽 Ingress 가 먼저 만들어지면
  # `failed calling webhook "validate.nginx.ingress.kubernetes.io"` 로 실패한다. wait = true 는
  # 컨트롤러 파드가 Ready 가 될 때까지 이 리소스의 apply 를 막아, argocd.tf 가 depends_on 으로
  # 이 리소스를 참조하면 그 경합이 구조적으로 사라지게 한다.
  wait = true

  values = [file("${path.module}/values/ingress-nginx.${var.ingress_profile}.yaml")]
}
