# substrate 계약 — 바닥이 플랫폼에 보장하는 값 4개(docs/argocd-gitops.md §3).
#
# 이 네 값이 계약이다. 다른 substrate 구현(k3d, 원격 k3s, docker 위의 kind 등)을
# 형제 디렉터리(예: terraform/cluster/k3d/)로 추가하더라도, 같은 이름·같은 타입으로
# 이 네 값을 내보내기만 하면 terraform/platform 은 한 줄도 바뀌지 않는다(substrate 중립화).
# platform 스택은 이 값들을 terraform_remote_state 가 아니라 variable 기본값으로 "다시 선언"해
# 받는다 — 결합은 kubeconfig 경로 문자열 하나뿐이고, 대가는 값 중복이다.

output "kubeconfig_path" {
  description = "kind 가 쓴 kubeconfig 파일의 절대 경로. platform 스택의 provider 설정이 이 값을 받는다."
  value       = local.kubeconfig_path
}

output "kube_context" {
  description = "kubeconfig 안의 context 이름. kind 가 \"kind-<cluster_name>\" 형태로 고정 생성한다."
  value       = "kind-${var.cluster_name}"
}

output "ingress_profile" {
  description = "platform 스택이 ingress-nginx values 파일을 고르는 키. 이 substrate 구현은 항상 \"kind\"."
  value       = "kind"
}

output "storage_class" {
  description = "이 substrate 가 기본 제공하는 StorageClass 이름. kind 는 local-path-provisioner 로 \"standard\" 를 기본 제공한다."
  value       = "standard"
}
