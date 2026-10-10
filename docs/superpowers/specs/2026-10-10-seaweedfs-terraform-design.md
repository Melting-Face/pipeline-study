# SeaweedFS를 Terraform 스택 A로 옮긴다 — 설계

> 구현 계획은 이 설계를 입력으로 별도 작성한다. 선행 설계:
> [외부 스토리지 설계](2026-10-10-external-storage-design.md)(이 문서가 그 E1을 번복한다).
> 선행 PR: #181(`docs/setup/local-k8s.md` 분리) — 머지 뒤 그 위에 쌓는다.

## Context

- **현재**: S3(SeaweedFS)는 compose 서비스 `seaweedfs`(profile `storage`)이고 `scripts/storage-up.sh`가
  `s3.json` 생성 → `compose up` → healthy 대기 → 버킷 3개 → kind 네트워크 연결을 한다. Terraform 스택 A는
  `terraform_data.seaweedfs_network`로 **연결만** 하고, 컨테이너가 없으면 경고 후 건너뛴다.
- **목적(사용자 결정)**: 테스트 환경이므로 **로컬 인프라 층을 Terraform으로 일원화**한다 — 컨테이너 설정이
  `plan`에 드러나고, 「S3 먼저 → 클러스터」 같은 순서 규칙을 사람 대신 의존 그래프가 지킨다.
- **유지하는 것**: S3는 여전히 **클러스터 밖 컨테이너**다(실무 모사 — 파드는 엔드포인트+자격증명으로만 닿는다).
  데이터는 **destroy 뒤에도 남는다**(바인드 마운트).
- **번복**: 선행 설계 E1 「SeaweedFS 정본 = compose, 수명주기 독립」. 수명 독립의 실익이던 *데이터 보존*은
  바인드 마운트로 유지되고, 잃는 것은 *컨테이너*가 클러스터와 함께 재생성되는 점뿐이다(테스트 환경에서 수용).

## 결정 요약

| # | 결정 | 근거 |
|---|---|---|
| T1 | 컨테이너는 **스택 A**(`terraform/cluster/kind`)가 소유 | 순서를 의존 그래프로, 스택 수 유지(2개) |
| T2 | 데이터 = 호스트 바인드 마운트 `<repo>/seaweedfs/data` — Terraform은 디렉터리를 만들지도 지우지도 않는다 | destroy 뒤 보존, 기존 데이터 무이관 |
| T3 | 프로바이더 = `kreuzwerker/docker`, `host` = podman 소켓(변수) | 선언형 — 드리프트가 `plan`에 보인다 |
| T4 | 키 = `sensitive` 변수 2개(`TF_VAR_`), `s3.json`은 `upload` 블록으로 주입(호스트 파일 없음) | 키 변경 = 컨테이너 교체로 `plan`에 노출 |
| T5 | 버킷 = `terraform_data` + `local-exec weed shell`, **생성만**(destroy 동작 없음) | 데이터 보존(T2)과 정합. S3 프로바이더는 destroy가 버킷 삭제를 시도한다 |
| T6 | compose `seaweedfs` **삭제**, `trino`·`prometheus`는 `host.containers.internal` 경유 | 정본 하나 — 이중 존재 금지(CLAUDE.md §관측) |
| T7 | `scripts/storage-up.sh` **삭제** | 역할 전부가 T1~T5로 이동 |
| T8 | tfstate의 평문 키는 **수용** | 아래 §보안 |

## 리소스 구성 (스택 A)

```text
provider docker (host = var.container_host)
kind_cluster.this ──► docker_container.seaweedfs ──► terraform_data.seaweedfs_buckets
                        image   docker_image.seaweedfs (chrislusf/seaweedfs:4.36)
                        ports   127.0.0.1:9333 · 8333 · 8888 · 9324(메트릭, 신규 게시)
                        mount   var.seaweedfs_data_dir → /data (bind)
                        upload  /etc/seaweedfs/s3.json ← var.s3_access_key · var.s3_secret_key
                        network kind, aliases = ["seaweedfs-ext"]
                        command compose.yml 의 mini … 그대로(-admin.ui=false -webdav=false 유지)
                        healthcheck 동일, wait = true
registry_certs · registry_network — 변경 없음
terraform_data.seaweedfs_network — 제거(networks_advanced 가 대체)
```

- 새 변수: `container_host`(podman 소켓 URI — 머신마다 다르다, 기본값 없음), `s3_access_key`·`s3_secret_key`
  (`sensitive = true`, 빈 값 금지 validation), `seaweedfs_data_dir`(기본값 = 이 체크아웃의 `seaweedfs/data`).
- **precondition**: linked worktree에서 apply하면 막는다 — 데이터 경로가 체크아웃마다 갈려 빈 레이크가 뜬다
  (`storage-up.sh`의 같은 가드를 이전). 판정 입력은 `git rev-parse --git-dir` ≠ `--git-common-dir`.
- 컨테이너 이름 `seaweedfs`·별칭 `seaweedfs-ext`·포트를 그대로 둬 **소비자 무변경**:
  `k8s/**`의 `http://seaweedfs:8333`(ExternalName 경유), Dagster·노트북의 `localhost:8333`.

## 기존 자산 이행

| 대상 | 처리 |
| --- | --- |
| `compose.yml` `seaweedfs` | 삭제. `trino`·`prometheus`의 `depends_on: seaweedfs` 제거 |
| `trino/etc/catalog/iceberg.properties` | `s3.endpoint=http://host.containers.internal:8333` |
| `prometheus/prometheus.yml` | 타깃 `host.containers.internal:9324` |
| `scripts/storage-up.sh` | 삭제. `AWS_*` ≠ `ICEBERG_S3_*` 가드는 Terraform이 `.env`를 못 읽어 이전 불가 → 문서 체크리스트 1줄 |
| 주석·안내의 `storage-up.sh` 언급 | 「스택 A `docker_container.seaweedfs`」로 바꾼다(동작 무변경) — 대상은 아래 목록 |
| 문서 | 「compose 정본 → 스택 A 정본」으로 갱신 — 대상은 아래 목록 |
| 범위 밖(Issue 1줄) | `kind-registry` 컨테이너의 Terraform 이관 |

- **주석·안내 대상**: `.env.example`, `common/constants.py`, `scripts/k8s-secrets.sh`,
  `gitops/charts/storage-external/values.yaml`·`tests/expect.yaml`, `k8s/catalog-pg-backup.yaml`,
  `scripts/storage_conformance_probe.py`, 노트북 `00-lakehouse-connect.ipynb`.
- **문서 대상**: `CLAUDE.md`·`AGENTS.md` 요약, `setup.md`, `setup/local-k8s.md`(2단계를 3단계에 합침),
  `architectures/storage.md`·`overview.md`, `conventions/docker.md`·`k8s.md`, `operations.md`, `security.md`,
  `redesign.md`.
- 착수 시 `grep -rlI storage-up`을 다시 돌려 목록과 대조한다(이 목록은 설계 시점 기준).

### 전환 절차 (데이터가 있는 현재 상태 → 새 구조)

1. `podman compose --profile storage down` — 컨테이너만 내린다. `./seaweedfs/data`는 남는다.
2. 스택 A `plan` — 기대: **추가 3**(`docker_image`·`docker_container`·`terraform_data.seaweedfs_buckets`),
   **제거 1**(`seaweedfs_network`, destroy 동작 없는 리소스라 무해). 클러스터는 무변동.
3. `apply` → 기존 객체가 그대로 보이는지 확인(데이터 무이관).

## 보안

- **tfstate 평문 키(T8)**: `sensitive`는 `plan`/출력만 가리고 state에는 평문이다(`upload` 내용 포함).
  - **수용 근거**: 로컬 테스트 환경 · state는 로컬 백엔드이고 `**/*.tfstate*`가 gitignore · 키는 로컬 SeaweedFS 전용.
  - **재검토 조건**: state를 원격 백엔드로 옮길 때 · 같은 키를 로컬 밖에서 쓰게 될 때 · state를 공유하게 될 때.
  - `docs/security.md`에는 정책(수용과 조건)만, 실태는 볼트 `security/posture.md`에 한 벌로 적는다.
- 노출 면 불변: 포트는 루프백만, Admin UI·WebDAV는 계속 끈다. 메트릭 9324가 루프백에 새로 게시된다.
- `apply`·`destroy`·compose `down`은 **비가역 게이트**(reviewer 보안 체크리스트 + 사용자 승인) 대상이다.

## 검증

### 0. spike (버리는 코드 — scratchpad의 임시 Terraform 루트, 저장소에 남기지 않는다)

| # | 판정 | 통과 조건 | 실패 시 |
| --- | --- | --- | --- |
| S1 | 프로바이더 ↔ podman 소켓 | `docker_image` pull + `docker_container` 생성 | 접근 B(`local-exec podman run`)로 하향 |
| S2 | `upload`된 `s3.json` 적용 | 무서명 요청 403 **그리고** 서명 `head_bucket` 성공(음성·양성 대조 함께) | 호스트 파일 마운트(`local_sensitive_file`) |
| S3 | `wait`가 healthy 대기 | apply 종료 직후 `healthy` | 버킷 단계에 대기 루프 |
| S4 | 네트워크 별칭 | `podman inspect` Aliases에 `seaweedfs-ext` | `terraform_data` 연결 유지 |
| S5 | compose 컨테이너 → `host.containers.internal:<루프백 게시 포트>` | 임시 컨테이너에서 응답 | Terraform이 compose external 네트워크에도 연결 |
| S6 | 재 `apply` | `No changes.` | 해당 속성 `ignore_changes` |

spike는 다른 이름·포트(`seaweedfs-spike`)로 띄우고 끝나면 destroy한다 — 정본 컨테이너·데이터를 건드리지 않는다.

### 1. 정적

- `terraform validate`, `terraform fmt -check`
- `terraform test` — 빈 키 validation · worktree precondition을 **일부러 위반시켜** 막히는지
- `podman compose --profile legacy-sql config --services`, `--profile monitoring` 동일 — `seaweedfs` 없이 해석되는지
- pre-commit(문서 링크·가독성 포함)

### 2. 실기동

- 전환 절차 2의 `plan` 수치가 기대(추가 3 · 제거 1 · 변경 0)와 일치
- `apply` 뒤: 기존 객체 존재(`head_bucket` + 객체 목록), `scripts/storage_conformance_probe.py`,
  파드에서 `seaweedfs:8333` 도달, `trino`·`prometheus` 기동 시 S3·메트릭 도달
- 재 `apply` → `No changes.`

### 3. destroy 데이터 보존 (반드시 실측)

- destroy 전 `./seaweedfs/data`의 파일 수 + 정렬된 해시 목록을 박제 → 스택 A destroy → 같은 값인지 →
  다시 apply → 같은 객체가 보이는지. 이 설계의 핵심 약속(T2)이라 생략하지 않는다.

## 리스크

| 축 | 내용 | 대응 |
| --- | --- | --- |
| 정확도 | 프로바이더 동작 가정 3개(`upload`·`wait`·별칭)와 S5 경로가 미확인 | spike S1~S6 실호출 판정 |
| 위험 | `seaweedfs_data_dir`이 틀리면 빈 디렉터리로 새 레이크가 뜬다(에러 없음) | worktree precondition + apply 뒤 객체 목록 확인 |
| 위험 | 클러스터만 재생성해도 S3 컨테이너가 교체된다 | 데이터는 바인드라 무손실 — 문서에 명시 |
| 보안 | tfstate 평문 키 | §보안 수용 + 재검토 조건 |
| 비용 | 실기동 검증에 VM 기동 필요 | 검증 직후 회수 |
| 효율 | 스크립트 1개 감소, 순서 규칙이 그래프로 | — |
