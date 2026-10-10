# SeaweedFS 컨테이너 모양 단위 테스트 — 전부 plan 모드라 컨테이너를 만들지 않는다.
# 머리(override·mock·variables)는 validation.tftest.hcl 과 같다 — 파일마다 독립이라 반복한다.

# 호스트 런타임에 의존하지 않도록 탐지 결과를 고정한다(CI 러너는 docker 가 PATH 에 있다).
override_data {
  target = data.external.runtime
  values = {
    result = {
      detected = "podman"
    }
  }
}

# docker 프로바이더는 podman 소켓에 붙으므로 테스트에서는 접속하지 않도록 목으로 바꾼다.
mock_provider "docker" {}

# worktree 판정도 호스트 체크아웃에 의존하지 않게 "루트 체크아웃"으로 고정한다.
override_data {
  target = data.external.checkout
  values = {
    result = {
      linked = "false"
    }
  }
}

# 필수 변수(기본값 없음)의 테스트용 값 — 실제 키가 아니다.
variables {
  container_host = "unix:///tmp/podman.sock"
  s3_access_key  = "test-access"
  s3_secret_key  = "test-secret"
}

run "seaweedfs_container_shape" {
  command = plan

  assert {
    condition     = docker_container.seaweedfs.name == "seaweedfs"
    error_message = "컨테이너 이름이 seaweedfs 가 아니다"
  }

  # 포트는 전부 127.0.0.1 게시 — 0.0.0.0 으로 새면 S3·UI 가 LAN 에 노출된다.
  assert {
    condition = (
      length(docker_container.seaweedfs.ports) == 4 &&
      length(setsubtract(
        ["127.0.0.1:9333", "127.0.0.1:8333", "127.0.0.1:8888", "127.0.0.1:9324"],
        [for p in docker_container.seaweedfs.ports : "${p.ip}:${p.external}"]
      )) == 0
    )
    error_message = "게시 포트가 127.0.0.1 의 9333·8333·8888·9324 네 개가 아니다"
  }

  # 파드의 Service seaweedfs(ExternalName)가 이 별칭을 가리킨다 — 클러스터 재생성 뒤에도 같아야 한다.
  assert {
    condition = contains(
      one([for n in docker_container.seaweedfs.networks_advanced : n.aliases if n.name == "kind"]),
      "seaweedfs-ext"
    )
    error_message = "kind 네트워크 별칭 seaweedfs-ext 가 없다"
  }

  # 데이터는 바인드 마운트 — destroy 뒤에도 남는 근거다.
  assert {
    condition = endswith(
      one([for v in docker_container.seaweedfs.volumes : v.host_path if v.container_path == "/data"]),
      "seaweedfs/data"
    )
    error_message = "/data 가 호스트 seaweedfs/data 바인드가 아니다"
  }

  # 키 회전이 컨테이너에 반영되려면 s3.json 내용이 변수에서 와야 한다(Review Focus 4).
  assert {
    condition = strcontains(
      nonsensitive(one([for u in docker_container.seaweedfs.upload : u.content if u.file == "/etc/seaweedfs/s3.json"])),
      "test-access"
    )
    error_message = "s3.json 내용이 s3_access_key 변수에서 오지 않는다"
  }

  assert {
    condition = docker_container.seaweedfs.command == split(" ",
      "mini -ip.bind=0.0.0.0 -dir=/data -metricsPort=9324 -s3.config=/etc/seaweedfs/s3.json -admin.ui=false -webdav=false"
    )
    error_message = "command 가 compose 의 seaweedfs command 와 다르다"
  }
}

# linked worktree 에서 apply 하면 빈 seaweedfs/data 로 새 레이크가 조용히 뜬다 — precondition 에서 멈춰야 한다(Review Focus 2).
run "reject_linked_worktree" {
  command = plan

  override_data {
    target = data.external.checkout
    values = {
      result = {
        linked = "true"
      }
    }
  }

  expect_failures = [docker_container.seaweedfs]
}
