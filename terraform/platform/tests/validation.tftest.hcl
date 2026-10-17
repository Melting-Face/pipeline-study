# 변수 검증 단위 테스트 - 전부 plan 모드라 실제 헬름 릴리스를 만들지 않는다.
# 호스트 독립: 이 파일은 ~/.kube/lakehouse.config 나 실제 클러스터 없이도 통과해야 한다
# (CI 러너에는 둘 다 없다). helm 프로바이더는 plan 에서 API 서버에 붙지 않는다.
# 되돌리기 어려운 입력(kube_context 오선택·repo_url 프로토콜)은 validation 블록으로
# plan 단계에서 막는다. 앱 목록 검사는 Terraform 밖(scripts/gitops-apps-check.sh)이다 - 목록이 git 에 있다.

run "defaults_are_the_spec_values" {
  command = plan

  assert {
    condition     = var.kube_context == "kind-lakehouse" && var.ingress_profile == "kind" && var.http_host_port == 8080 && var.target_revision == "main"
    error_message = "기본값이 docs/argocd-gitops.md 와 다르다"
  }

  assert {
    condition     = endswith(var.kubeconfig_path, "/.kube/lakehouse.config")
    error_message = "kubeconfig_path 기본값이 ~/.kube/<cluster_name>.config 규칙과 다르다"
  }
}

run "outputs_follow_variables" {
  command = plan

  variables {
    http_host_port = 9090
    kube_context   = "kind-other"
  }

  assert {
    condition     = output.argocd_url == "http://argocd.localtest.me:9090"
    error_message = "argocd_url 이 http_host_port 를 따르지 않는다"
  }

  assert {
    condition     = strcontains(output.argocd_initial_admin_password_command, "--context kind-other")
    error_message = "비밀번호 조회 명령이 kube_context 를 따르지 않는다"
  }
}

run "reject_unknown_ingress_profile" {
  command = plan

  variables {
    ingress_profile = "traefik"
  }

  expect_failures = [var.ingress_profile]
}

# 🔴 kube_context 가 비면 현재 컨텍스트를 따라가 기존 lakehouse 에 조용히 apply 될 수 있다.
run "reject_empty_kube_context" {
  command = plan

  variables {
    kube_context = ""
  }

  expect_failures = [var.kube_context]
}

run "reject_non_kind_kube_context" {
  command = plan

  variables {
    kube_context = "docker-desktop"
  }

  expect_failures = [var.kube_context]
}

run "reject_empty_target_revision" {
  command = plan

  variables {
    target_revision = ""
  }

  expect_failures = [var.target_revision]
}

run "repo_url_must_be_https_git" {
  command = plan

  variables {
    repo_url = "git@github.com:Melting-Face/pipeline-study.git"
  }

  expect_failures = [var.repo_url]
}

# 스택 A(terraform/cluster/kind)와 같은 금지 포트 — 두 스택의 http_host_port 는 값이 일치해야 하므로
# 한쪽에서만 막으면 다른 쪽이 조용히 argocd-study 의 포트를 가리킨다.
run "reject_forbidden_http_port" {
  command = plan

  variables {
    http_host_port = 8081
  }

  expect_failures = [var.http_host_port]
}

# argocd values 에 CRD printer-column priority 무시 설정이 렌더에 실리는지 본다.
# 빠지면 Spark·Flink Application 이 영구 OutOfSync 로 돌아온다(docs/argocd-gitops.md §5).
run "argocd_values_ignore_crd_priority_drift" {
  command = plan

  assert {
    condition = (
      yamldecode(templatefile("${path.module}/values/argocd.yaml.tftpl", {})).configs.cm["resource.customizations.ignoreDifferences.apiextensions.k8s.io_CustomResourceDefinition"] != null
      && strcontains(yamldecode(templatefile("${path.module}/values/argocd.yaml.tftpl", {})).configs.cm["resource.customizations.ignoreDifferences.apiextensions.k8s.io_CustomResourceDefinition"], "additionalPrinterColumns[]?.priority")
    )
    error_message = "argocd values 에 CRD priority ignoreDifferences 가 없다"
  }
}

# apps AppSet 의 strategy(RollingSync)는 이 컨트롤러 파라미터가 없으면 에러 없이 무시된다 - 순서 제어가
# 조용히 사라지므로 렌더에 실리는지 본다. ConfigMap 값이라 bool 이 아니라 문자열 "true" 여야 한다.
run "argocd_values_enable_progressive_syncs" {
  command = plan

  assert {
    condition     = yamldecode(templatefile("${path.module}/values/argocd.yaml.tftpl", {})).configs.params["applicationsetcontroller.enable.progressive.syncs"] == "true"
    error_message = "argocd values 에 applicationsetcontroller.enable.progressive.syncs: \"true\" 가 없다"
  }
}
