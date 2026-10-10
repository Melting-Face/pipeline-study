# 스택 A — kind 클러스터(substrate). 클러스터 "안"의 어떤 것도 소유하지 않는다
# (소유권 경계는 docs/argocd-gitops.md §3 표 참고 — 노드·레이블·포트매핑·kubeconfig 까지만 이 스택 소관).

# kind 가 실제로 어떤 런타임을 선택할지 "탐지"한다. var.expected_runtime 과 대조하기 위한
# 입력일 뿐, 이 데이터 소스 자체가 런타임을 고르지는 않는다(그건 kind 라이브러리 내부 로직이다).
data "external" "runtime" {
  program = [abspath("${path.module}/scripts/detect-runtime.sh")]
}

locals {
  # 전용 kubeconfig 한 경로 — kind_cluster 와 output 이 이 local 하나를 함께 쓴다.
  kubeconfig_path = abspath(pathexpand(coalesce(var.kubeconfig_path, "~/.kube/${var.cluster_name}.config")))
}

resource "kind_cluster" "this" {
  name            = var.cluster_name
  node_image      = var.node_image
  wait_for_ready  = true
  kubeconfig_path = local.kubeconfig_path

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    # 로컬 레지스트리(localhost:5001)용 certs.d 방식 — 철거된 k8s/kind-cluster.yaml 의 설정을 이 스택이 이어받았다.
    containerd_config_patches = [
      <<-EOT
        [plugins."io.containerd.grpc.v1.cri".registry]
            config_path = "/etc/containerd/certs.d"
      EOT
    ]

    node {
      role = "control-plane"

      # ingress-nginx(kind provider 매니페스트)가 이 레이블로 자신을 어느 노드에
      # 스케줄할지 정한다(nodeSelector) — platform 스택이 참조하므로 여기서 박아둔다.
      labels = {
        "ingress-ready" = "true"
      }

      # hosts.toml 은 extra_mounts 로 마운트하지 않는다 — container_path 의 콜론(localhost:5001)이
      # podman `--volume host:ctr[:opts]` 구분자와 겹치고, host_path 가 이 체크아웃 경로에 묶여
      # 체크아웃이 지워지면 노드 설정이 사라진다. 대신 아래 terraform_data.registry_certs 가
      # 노드 안에 직접 써 넣는다(철거된 k8s-up.sh 의 방식).

      # 🔴 extra_port_mappings 는 클러스터 "생성 시점"에만 지정할 수 있다 — kind 노드가
      #   컨테이너라 사후에 공개 포트를 추가할 방법이 없다. 포트를 바꾸려면
      #   클러스터를 재생성해야 한다.
      extra_port_mappings {
        container_port = 80
        host_port      = var.http_host_port
        listen_address = "127.0.0.1"
        protocol       = "TCP"
      }

      extra_port_mappings {
        container_port = 443
        host_port      = var.https_host_port
        listen_address = "127.0.0.1"
        protocol       = "TCP"
      }
    }
  }

  lifecycle {
    precondition {
      # 🔴 이것은 "고정"이 아니라 "조용한 변경 차단"이다. tehcyx/kind 는 런타임 옵션을
      # kind 라이브러리에 넘기지 않으므로(kind/resource_cluster.go), 실제 선택은
      # DetectNodeProvider()의 docker -> nerdctl -> podman 자동탐지가 결정한다.
      # KIND_EXPERIMENTAL_PROVIDER 환경변수는 kind **CLI** 만 보고 이 라이브러리 경로는
      # 보지 않는다 — 즉 이 변수로 "원하는 런타임을 강제"할 수단이 없다.
      # 이 precondition 은 "지금 PATH 에서 탐지되는 런타임이 우리가 알던 값과 같은가"만
      # plan 단계에서 확인해, Docker Desktop 설치 등으로 자동탐지 결과가 조용히 바뀌는
      # 것을 막는다. 바이너리 존재만 보므로 "런타임이 실제로 동작하는가"는 검증하지
      # 못한다(podman machine 이 중지돼 있어도 이 precondition 은 통과한다).
      condition     = data.external.runtime.result.detected == var.expected_runtime
      error_message = "탐지된 런타임(${data.external.runtime.result.detected})이 expected_runtime(${var.expected_runtime})과 다르다. kind 라이브러리의 자동탐지 순서(docker > nerdctl > podman)가 조용히 바뀌었을 수 있다 — KIND_EXPERIMENTAL_PROVIDER 로는 되돌릴 수 없다. var.expected_runtime 을 의도적으로 바꾸는 것이 아니라면 PATH 에서 어떤 런타임이 새로 잡혔는지 확인한다."
    }
  }
}

# 노드 containerd 에 certs.d/localhost:5001/hosts.toml 을 써 넣는다 — 이후 localhost:5001 → kind-registry:5000.
# 내용의 단일 출처는 registry/hosts.toml 이다(여기서는 그 파일을 노드로 흘려보내기만 한다).
# 노드 이름은 kind 규칙상 <cluster_name>-control-plane(단일 노드 구성).
resource "terraform_data" "registry_certs" {
  depends_on = [kind_cluster.this]

  # 클러스터 재생성(id 변경) 또는 hosts.toml 내용 변경 시 다시 쓴다.
  triggers_replace = [kind_cluster.this.id, filesha256("${path.module}/registry/hosts.toml")]

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-ec"]
    environment = {
      NODE         = "${var.cluster_name}-control-plane"
      REGISTRY_DIR = "/etc/containerd/certs.d/localhost:5001"
      HOSTS_TOML   = abspath("${path.module}/registry/hosts.toml")
    }
    command = <<-EOT
      podman exec "$NODE" mkdir -p "$REGISTRY_DIR"
      podman exec -i "$NODE" sh -c "cat > '$REGISTRY_DIR/hosts.toml'" < "$HOSTS_TOML"
    EOT
  }
}

# 레지스트리 컨테이너를 kind 네트워크에 연결 — 노드가 kind-registry 이름을 해석한다.
# 이미 연결됐으면 건너뛴다(k8s-up.sh 의 NetworkSettings.Networks.kind 판정과 동일, 멱등).
# 레지스트리 컨테이너 자체는 k8s-up.sh 몫이다(이 스택은 연결만 한다).
resource "terraform_data" "registry_network" {
  depends_on = [kind_cluster.this]

  # 클러스터가 재생성되면(id 변경) 이 단계도 다시 돈다 — depends_on 만으로는 재실행되지 않는다.
  triggers_replace = kind_cluster.this.id

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-ec"]
    command     = <<-EOT
      if [ "$(podman inspect -f '{{json .NetworkSettings.Networks.kind}}' kind-registry 2>/dev/null || echo null)" = "null" ]; then
        podman network connect kind kind-registry
      fi
    EOT
  }
}

# 외부 오브젝트 스토리지(compose seaweedfs)를 kind 네트워크에 별칭 seaweedfs-ext 로 연결한다 — 파드의
# Service seaweedfs(ExternalName, gitops/charts/storage-external)가 이 별칭을 가리킨다.
# 컨테이너 수명은 compose 몫이라 없으면 경고만 하고 넘어간다(스토리지는 클러스터와 독립).
# 컨테이너 재생성으로 풀린 연결·별칭 누락은 scripts/storage-up.sh 가 고친다(이 리소스는 클러스터 생성 시점만).
resource "terraform_data" "seaweedfs_network" {
  depends_on = [kind_cluster.this]

  triggers_replace = kind_cluster.this.id

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-ec"]
    command     = <<-EOT
      if ! podman container exists seaweedfs; then
        echo "경고: seaweedfs 컨테이너가 없다 - ./scripts/storage-up.sh 가 기동과 연결을 함께 한다" >&2
        exit 0
      fi
      if [ "$(podman inspect -f '{{json .NetworkSettings.Networks.kind}}' seaweedfs 2>/dev/null || echo null)" = "null" ]; then
        podman network connect --alias seaweedfs-ext kind seaweedfs
      fi
    EOT
  }
}
