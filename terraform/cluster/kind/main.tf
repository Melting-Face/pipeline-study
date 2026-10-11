# 스택 A — kind 클러스터(substrate). 클러스터 "안"의 어떤 것도 소유하지 않는다
# (소유권 경계는 docs/argocd-gitops.md §3 표 참고 — 노드·레이블·포트매핑·kubeconfig 까지만 이 스택 소관).

# kind 가 실제로 어떤 런타임을 선택할지 "탐지"한다. var.expected_runtime 과 대조하기 위한
# 입력일 뿐, 이 데이터 소스 자체가 런타임을 고르지는 않는다(그건 kind 라이브러리 내부 로직이다).
data "external" "runtime" {
  program = [abspath("${path.module}/scripts/detect-runtime.sh")]
}

# 이 체크아웃이 linked worktree 인지 판정한다. seaweedfs 데이터 기본 경로가 체크아웃에 묶이므로
# worktree 에서 apply 하면 빈 seaweedfs/data 로 새 레이크가 조용히 뜬다 — 그 차단(precondition)의 입력이다.
data "external" "checkout" {
  program = [abspath("${path.module}/scripts/detect-checkout.sh")]
}

locals {
  # 전용 kubeconfig 한 경로 — kind_cluster 와 output 이 이 local 하나를 함께 쓴다.
  kubeconfig_path = abspath(pathexpand(coalesce(var.kubeconfig_path, "~/.kube/${var.cluster_name}.config")))

  # SeaweedFS 데이터 경로 — 기본은 저장소 루트의 seaweedfs/data(compose 바인드 마운트와 같은 자리).
  seaweedfs_data_dir = coalesce(var.seaweedfs_data_dir, abspath("${path.module}/../../../seaweedfs/data"))
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

# SeaweedFS(S3) — 클러스터 밖 컨테이너지만 수명은 이 스택이 소유한다(종전 compose + storage-up.sh 를 대체).
# 데이터는 바인드 마운트(local.seaweedfs_data_dir)라 컨테이너가 교체·destroy 돼도 호스트에 남는다.
# keep_locally — destroy 가 이미지까지 지우면 재 apply 가 매번 다시 받는다(compose 시절과 같은 캐시 유지).
# 🔴 4.36 은 상향 고정이다 — seaweedfs/data 는 4.x 가 쓴 온디스크 상태(mini.options 등)라 낮은 버전으로
#   인플레이스로 여는 것이 위험하다. 근거 docs/conventions/docker.md §1-3.
resource "docker_image" "seaweedfs" {
  name         = "chrislusf/seaweedfs:4.36"
  keep_locally = true
}

resource "docker_container" "seaweedfs" {
  # 클러스터가 먼저 있어야 kind 네트워크에 붙을 수 있다.
  depends_on = [kind_cluster.this]

  name  = "seaweedfs"
  image = docker_image.seaweedfs.image_id

  # compose 와 같은 command. -s3.config 로 인증을 켠다(무인증 결정 번복 — docs/security.md §4-3).
  # -ip.bind=0.0.0.0 은 서비스별로 못 나눠(mini 에 per-service bind 없음) kind 네트워크에서는 S3 외 포트도 닿는다.
  # 그래서 인증 배선 없는 운영 평면 둘(Admin UI 23646 · WebDAV 7333)은 프로세스 자체를 끈다. 운영은 weed shell.
  command = split(" ", "mini -ip.bind=0.0.0.0 -dir=/data -metricsPort=9324 -s3.config=/etc/seaweedfs/s3.json -admin.ui=false -webdav=false")

  # 전부 루프백 게시 — LAN 도달을 막는다(docs/security.md 최소 노출). 9324(메트릭)는 신규 게시.
  ports {
    ip       = "127.0.0.1"
    internal = 9333
    external = 9333
  }
  ports {
    ip       = "127.0.0.1"
    internal = 8333
    external = 8333
  }
  ports {
    ip       = "127.0.0.1"
    internal = 8888
    external = 8888
  }
  ports {
    ip       = "127.0.0.1"
    internal = 9324
    external = 9324
  }

  # 볼륨 슬롯: mini 는 -volume.max 가 없고 여유 디스크로 자동 설정된다(`weed mini -h`). PutObject 가
  # 「No writable volumes」로 실패하면 체크섬이 아니라 디스크 여유부터 본다(docs/conventions/k8s/checksum.md).
  volumes {
    host_path      = local.seaweedfs_data_dir
    container_path = "/data"
    # spike S6: 명시하지 않으면 podman 이 되돌려주는 false 와 null 이 달라 매 apply 마다 교체된다.
    read_only = false
  }

  # s3.json 은 파일이 아니라 변수에서 만든다 — 키를 회전하면 내용이 바뀌어 컨테이너가 교체되며 반영된다.
  upload {
    file = "/etc/seaweedfs/s3.json"
    content = jsonencode({
      identities = [{
        name        = "lakehouse"
        credentials = [{ accessKey = var.s3_access_key, secretKey = var.s3_secret_key }]
        actions     = ["Admin", "Read", "Write", "List", "Tagging"]
      }]
    })
  }

  # 인증이 켜져 있어 무서명 요청은 403 — 응답이 오면 살아 있다(curl 은 --fail 없이 HTTP 오류에 0).
  healthcheck {
    test         = ["CMD-SHELL", "curl -s -o /dev/null http://127.0.0.1:8333/"]
    interval     = "10s"
    timeout      = "5s"
    retries      = 6
    start_period = "20s"
  }

  # healthy 가 될 때까지 apply 가 기다린다(spike S3) — 뒤의 버킷 생성이 기동 경합을 덜 탄다.
  wait         = true
  wait_timeout = 120

  # 파드의 Service seaweedfs(ExternalName)가 이 별칭을 가리킨다 — 종전 terraform_data.seaweedfs_network 대체.
  networks_advanced {
    name    = "kind"
    aliases = ["seaweedfs-ext"]
  }

  log_driver = "json-file"
  log_opts = {
    max-size = "10m"
    max-file = "20"
  }

  memory = 1024
  # spike S6: podman 기본값이 2 × memory 라 명시하지 않으면 드리프트로 교체된다(종전 compose 와 같은 실효값).
  memory_swap = 2048
  # spike S7: cpus 는 NanoCpus 로 반영되고 드리프트가 없다(compose limits.cpus 1.0 과 동일).
  cpus = "1.0"

  lifecycle {
    # spike S6 — 셋 다 podman 이 되읽는 값이 선언과 달라 매 apply 마다 교체가 났다.
    # log_opts: podman 이 LogConfig.Config 를 null 로 돌려준다(max-file 은 podman json-file 에서 원래 미적용).
    # pid_mode·ulimit: podman 기본값(private·RLIMIT_NOFILE/NPROC)을 되읽는다.
    ignore_changes = [log_opts, pid_mode, ulimit]

    # 클러스터를 재생성하면 kind 네트워크가 다시 만들어져 별칭(seaweedfs-ext) 연결이 풀린다.
    # depends_on 은 순서일 뿐 교체 트리거가 아니라, 종전 seaweedfs_network 의 triggers_replace 를 여기로 옮긴다.
    # 데이터는 바인드라 교체돼도 그대로다.
    replace_triggered_by = [kind_cluster.this]

    precondition {
      # 기본 데이터 경로가 체크아웃에 묶여 있어 linked worktree 에서 apply 하면 빈 seaweedfs/data 로
      # 새 레이크가 조용히 뜬다. Terraform 은 저장소 루트 체크아웃에서만 apply 한다.
      condition     = data.external.checkout.result.linked == "false"
      error_message = "linked worktree 에서 apply 하고 있다 — seaweedfs 데이터 기본 경로가 이 체크아웃의 빈 seaweedfs/data 를 가리킨다. 저장소 메인(루트) 체크아웃에서 apply 한다."
    }
  }
}

# 버킷 3개를 만든다 — 생성만 하고 destroy 동작은 두지 않는다(데이터는 바인드 디렉터리에 남는다).
# 컨테이너가 교체되면(id 변경) 다시 돈다. s3.bucket.create 는 이미 있거나 filer 가 덜 떴을 때 실패할 수 있어
# 종료코드가 아니라 s3.bucket.list 에 이름이 있는지로 판정한다(종전 storage-up.sh 와 같은 판정 축).
resource "terraform_data" "seaweedfs_buckets" {
  triggers_replace = docker_container.seaweedfs.id

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-ec"]
    environment = {
      RUNTIME   = data.external.runtime.result.detected
      CONTAINER = docker_container.seaweedfs.name
      BUCKETS   = "warehouse pg-backup dagster-logs"
    }
    # 🔴 판정은 정확 일치다 — storage-up.sh 의 grep -w 는 pg-backup-old 에도 걸린다(#178).
    # ⚠️ 가정(미확인): s3.bucket.list 는 버킷마다 한 줄, 첫 토큰이 이름(<이름>\tsize:… 형태)이다.
    #   형식이 다르면 토큰이 안 맞아 12회 뒤 exit 1 — 성공으로 새지 않고 실패 쪽으로 깨진다.
    #   파이프 입력이라 줄 앞에 weed shell 프롬프트(`> `)가 붙을 수 있어 그것만 벗긴다
    #   (종전 grep -w 는 접두가 있어도 통과했으므로 그 동작을 잃지 않는다).
    command = <<-EOT
      weed_shell() {
        "$RUNTIME" exec "$CONTAINER" sh -c "echo '$1' | weed shell -master localhost:9333 -filer localhost:8888" 2>&1
      }
      for bucket in $BUCKETS; do
        ready=""
        for _ in $(seq 1 12); do
          weed_shell "s3.bucket.create -name $bucket" >/dev/null || true
          if weed_shell "s3.bucket.list" | sed -E 's/^(> *)+//' | awk '{print $1}' | grep -qxF -- "$bucket"; then
            ready="yes"
            break
          fi
          sleep 5
        done
        if [ -z "$ready" ]; then
          echo "버킷 $bucket 이 s3.bucket.list 에 안 나온다(60초) — filer 기동·목록 형식을 확인한다" >&2
          exit 1
        fi
        echo "버킷 준비: $bucket"
      done
    EOT
  }
}
