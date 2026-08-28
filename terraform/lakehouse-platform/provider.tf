# kubeconfig 기반 접속. 자격증명은 파일 참조이며 이 저장소에 값이 들어오지 않는다.
#
# 🔴 `config_context`를 반드시 고정한다. 비우면 kubeconfig의 **current-context**를 따라가는데,
#    그 값은 다른 세션·다른 도구가 언제든 바꾼다. 이 스택이 의도치 않은 클러스터에
#    apply 하는 것을 막는 유일한 선언적 수단이다.
provider "kubernetes" {
  config_path    = var.kube_config_path
  config_context = var.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = var.kube_config_path
    config_context = var.kube_context
  }
}
