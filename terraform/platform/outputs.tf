# 🔴 초기 관리자 비밀번호 "값"은 output 으로 내보내지 않는다 — Terraform state 는 평문
# 파일이라, output 에 담으면 state 에 비밀이 영구히 남는다(.gitignore 대상이라도 디스크에는
# 남는다). 대신 "조회 명령 문자열"만 내보낸다 — 비밀번호는 ArgoCD 서버가 최초 기동 시
# 런타임에 argocd-initial-admin-secret 으로 직접 만든다(Helm 템플릿에는 이 Secret 리소스가
# 없다 — 차트 렌더 결과에 해당 리소스가 없다). 즉 apply 직후 파드가 아직 Ready 되기 전이면
# 이 명령도 잠시 "not found" 를 낼 수 있다 — 설정이 틀린 게 아니라 타이밍 문제다.
output "argocd_initial_admin_password_command" {
  description = "ArgoCD 초기 admin 비밀번호를 조회하는 kubectl 명령. 파드가 Ready 된 뒤 실행한다."
  value       = "kubectl --kubeconfig ${var.kubeconfig_path} --context ${var.kube_context} -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
}

output "argocd_url" {
  description = "port-forward 없이 접근하는 ArgoCD UI 주소."
  value       = "http://argocd.localtest.me:${var.http_host_port}"
}
