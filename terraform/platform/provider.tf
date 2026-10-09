# 프로바이더 설정. config_context 를 반드시 고정한다.
#
# 🔴 config_context 를 비우면 kubeconfig 의 "현재 컨텍스트"를 따라간다. 같은 머신에 기존 kind
# 클러스터 lakehouse 가 공존하므로, current-context 가 바뀌어 있으면 이 스택이 조용히 엉뚱한
# 클러스터에 apply 할 수 있다. 그래서 kube_context 는 비우지 않고(variables.tf 의 validation)
# 전용 kubeconfig 파일(~/.kube/<cluster_name>.config)과 함께 쓴다.
provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}
