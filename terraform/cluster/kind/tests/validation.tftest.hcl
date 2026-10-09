# 변수 검증 단위 테스트 — 전부 plan 모드라 실제 클러스터를 만들지 않는다.
# 기존 lakehouse 와 병행하는 argocd-study 가 쓰는 포트(8081/8444)는 이 스택에서 금지한다.

# 호스트 런타임에 의존하지 않도록 탐지 결과를 고정한다(CI 러너는 docker 가 PATH 에 있다).
override_data {
  target = data.external.runtime
  values = {
    result = {
      detected = "podman"
    }
  }
}

run "reject_forbidden_http_port" {
  command = plan

  variables {
    http_host_port = 8081
  }

  expect_failures = [var.http_host_port]
}

run "reject_forbidden_https_port" {
  command = plan

  variables {
    https_host_port = 8444
  }

  expect_failures = [var.https_host_port]
}

run "reject_unknown_runtime" {
  command = plan

  variables {
    expected_runtime = "containerd"
  }

  expect_failures = [var.expected_runtime]
}

run "defaults_are_the_spec_values" {
  command = plan

  assert {
    condition     = var.cluster_name == "lakehouse" && var.http_host_port == 8080 && var.https_host_port == 8443
    error_message = "기본값이 docs/argocd-gitops.md 와 다르다"
  }
}

run "kubeconfig_default" {
  command = plan

  variables {
    cluster_name = "lakehouse-next"
  }

  assert {
    condition     = output.kubeconfig_path == pathexpand("~/.kube/lakehouse-next.config")
    error_message = "kubeconfig 기본 경로가 ~/.kube/<cluster_name>.config 가 아니다"
  }

  assert {
    condition     = output.kube_context == "kind-lakehouse-next"
    error_message = "kube_context 가 kind-<cluster_name> 이 아니다"
  }
}

# hosts.toml 은 extra_mounts 가 아니라 terraform_data.registry_certs 로 노드에 써 넣는다
# (콜론 경로·체크아웃 경로 결합 회피 — main.tf 주석 참고). 마운트가 되살아나면 실패한다.
run "no_extra_mounts" {
  command = plan

  assert {
    condition     = length(kind_cluster.this.kind_config[0].node[0].extra_mounts) == 0
    error_message = "extra_mounts 가 다시 생겼다 — hosts.toml 은 registry_certs(local-exec)로 쓴다"
  }
}
