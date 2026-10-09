# ArgoCD - argo-cd 차트 10.9.6(ArgoCD v3.5.3)을 argocd 네임스페이스에 설치한다(docs/argocd-gitops.md §3 -
# 스택 B 가 소유하는 것은 "argocd 네임스페이스 · ArgoCD 자체 · 앱 등록 진입점 1개"뿐이고,
# 실제 앱은 ArgoCD 가 소유한다). 진입점 릴리스는 이 릴리스와 분리한다 - Application CRD 가
# 이 릴리스에 들어 있어 같은 helm install 로 CR 을 함께 만들 수 없기 때문이다
# (values/argocd.yaml.tftpl 의 주석 참고).
#
# 🔴 kubernetes_manifest 를 쓰지 않는다. plan 시점에 API 서버에 붙어 리소스 스키마를
# 조회하는데, Application CRD 는 helm_release 가 설치하므로 최초 plan 에서는 CRD 가 아직 없어
# plan 자체가 죽는다. plan 은 apply 이전이라 depends_on 으로도 풀리지 않는다.
# helm_release 는 plan 시점에 API 서버 스키마를 조회하지 않는다(values 는 불투명한 문자열).
resource "helm_release" "argo_cd" {
  name             = "argo-cd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "10.9.6" # ArgoCD v3.5.3 - 버전 근거는 docs/argocd-gitops.md D10
  namespace        = "argocd"
  create_namespace = true

  # 🔴 ingress-nginx admission webhook 경합 대비. ingress-nginx 가 설치하는
  # ValidatingWebhookConfiguration 이 준비되기 전에 ArgoCD 의 Ingress 가 먼저 만들어지면
  # `failed calling webhook "validate.nginx.ingress.kubernetes.io"` 로 apply 가 실패한다.
  # depends_on 으로 순서를, wait = true(ingress.tf)로 "준비됨"을 둘 다 강제한다.
  depends_on = [helm_release.ingress_nginx]

  # 컨트롤러 파드 등이 Ready 가 될 때까지 apply 를 막는다. 뒤에 붙는 별도 릴리스(appset)가
  # 이 대기에 기대 Application CRD 존재를 전제한다.
  wait = true

  values = [templatefile("${path.module}/values/argocd.yaml.tftpl", {})]
}

# ApplicationSet 전용 두 번째 릴리스 - charts/appset/ (로컬 chart, 이 저장소 소유).
#
# 🔴 왜 별도 릴리스인가: argo-cd 차트는 ApplicationSet CRD 를 평범한 템플릿으로 담고 있어,
# 같은 helm install 안에서 CRD 와 그 CR 을 함께 만들면 Helm 이 매니페스트를 빌드하는 단계에서
# `no matches for kind ... ensure CRDs are installed first` 로 막힌다.
# ⇒ depends_on + wait = true 로 "argo_cd 릴리스가 완전히 끝난 뒤"에만 적용한다.
# kubernetes_manifest 는 여전히 쓰지 않는다(plan 시점 CRD 스키마 조회 문제).
#
# 값은 repoUrl·targetRevision·apps 뿐이다 - 이미지 태그는 절대 여기 넣지 않는다.
# targetRevision 은 PR 검증 중 -var target_revision=<기능 브랜치> 로만 바꾼다(tfvars 에 쓰지
# 않으므로 되돌리기를 잊어도 다음 apply 가 기본값 main 으로 돌아간다).
resource "helm_release" "appset" {
  name       = "appset"
  chart      = "${path.module}/charts/appset"
  namespace  = "argocd"
  depends_on = [helm_release.argo_cd]
  wait       = true

  values = [yamlencode({
    repoUrl        = var.repo_url
    targetRevision = var.target_revision
    apps           = var.apps
  })]
}
