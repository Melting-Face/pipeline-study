# 컨테이너 PID 상한 — kind on podman

> [k8s.md](../k8s.md) §2에서 분리(상위 문서가 doc_lint 500줄 상한에 닿았다).
> 값의 단일 출처는 `scripts/k8s-env.sh`의 `NODE_PIDS_LIMIT`, 쓰기는 `scripts/k8s-up.sh` 1-1단계다.

## 1. 증상

- JVM처럼 스레드가 많은 워크로드가 `OutOfMemoryError: unable to create native thread`로 죽는다.
  **메모리 상한과 무관하다** — 메모리는 여유가 있는데 스레드(=태스크)를 더 못 만든다.
- Spark Connect driver에서는 `dag-scheduler-event-loop` 스레드가 이 에러로 죽고,
  이후 잡이 에러 없이 **무한 대기**한다. 클라이언트(dbt)는 끝나지 않는다.
- 판별은 컨테이너 안에서 `cat /sys/fs/cgroup/pids.max /sys/fs/cgroup/pids.peak`다.
  `peak == max`면 이 문서의 갈래다.

## 2. 원인 — 기본값 셋이 곱해진다

| 층 | 결정 주체 | 값(조치 전) |
| --- | --- | --- |
| kind 노드 컨테이너 `PidsLimit` | podman 기본값(kind는 `--pids-limit`을 넘기지 않는다) | 2048 |
| 노드 안 systemd `DefaultTasksMax` | `kernel.pid_max`·`threads-max`·root cgroup `pids.max` 중 최솟값의 15% | 307 |
| 컨테이너 scope `TasksMax` | 위 기본값을 컨테이너마다 적용(cgroup 드라이버 systemd) | 307 |

- `DefaultTasksMax`의 정의는 `systemd-system.conf(5)`가 1차 출처다. 노드 안에서는 root cgroup `pids.max`가
  노드 컨테이너의 `PidsLimit`이므로 그 값이 곧 분모가 된다.
- 2048은 컨테이너별 상한일 뿐 아니라 **노드 전체(모든 파드 합계)의 상한**이기도 하다.
- kubelet의 `podPidsLimit`은 설정돼 있지 않다(파드 slice는 `max`). 갈래를 kubelet으로 잡지 않는다.

## 3. 처방

- VM의 `/etc/containers/containers.conf.d/50-kind-pids-limit.conf`에 `[containers] pids_limit`을 둔다.
  kind 공식 문서(rootless 절)가 같은 키와 클러스터 재생성을 처방한다.
- 🔴 **macOS의 `podman`은 원격 클라이언트다.** 컨테이너 기본값은 VM의 **API 서비스**가 채우고, 서비스는
  기동 시점의 설정을 들고 있다. 드롭인만 쓰면 VM 안에서 직접 만든 컨테이너에만 반영되고
  **원격으로 만든 컨테이너(kind 노드 포함)는 옛 값**을 받는다. 그래서 `k8s-up.sh`는 드롭인을 바꿀 때
  `podman.service`를 재시작한다(실행 중 컨테이너는 conmon 관리라 그대로다).
- **노드 컨테이너 생성 시점에만 반영된다.** 값을 바꾼 뒤에는 클러스터를 재생성해야 하며,
  그 전까지 `k8s-up.sh`가 노드의 `PidsLimit` 불일치를 경고한다.

## 4. 검증 — 세 층을 각각 잰다

```shell
podman inspect -f '{{.HostConfig.PidsLimit}}' lakehouse-control-plane          # 노드
podman exec lakehouse-control-plane systemctl show -p DefaultTasksMax           # 노드 systemd
kubectl exec <pod> -- cat /sys/fs/cgroup/pids.max                               # 컨테이너
```

- 원격 기본값 확인은 일회용 컨테이너로 한다: 기본 실행과 `--pids-limit 2048` 명시 실행을 **함께** 돌린다.
  명시 실행이 2048을 내야 읽기 경로가 살아 있다는 증거다(둘 다 같은 값이면 판정 불가).
- 한 층만 보고 「풀렸다」고 쓰지 않는다 — 드롭인은 맞는데 서비스가 옛 값을 들고 있던 사례가 그것이다.

## 5. 미확인

- podman 문서들이 기본값을 1024·2048·4096으로 서로 다르게 적는다. 이 환경의 실측은 2048이다.
- `--pids-limit`의 현행 문서(-1 의미)는 열람하지 못했다. 드롭인은 양수 상한을 쓰므로 의존하지 않는다.
- kind v1alpha4·Terraform provider 스키마에 노드 PID 상한을 선언하는 필드는 문서상 없다(소스 미확인).
