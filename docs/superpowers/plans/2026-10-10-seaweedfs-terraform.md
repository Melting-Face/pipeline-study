# SeaweedFS를 Terraform 스택 A로 옮기기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** compose `seaweedfs`·`scripts/storage-up.sh`를 Terraform 스택 A의 `docker_container`로 대체하고,
데이터는 바인드 마운트로 destroy 뒤에도 보존한다.

**Architecture:** 스택 A(`terraform/cluster/kind`)에 `kreuzwerker/docker` 프로바이더(podman 소켓)를 더해
`kind_cluster.this → docker_container.seaweedfs → terraform_data.seaweedfs_buckets` 순으로 만든다. 컨테이너는
kind 네트워크에 별칭 `seaweedfs-ext`로 직접 붙어 `terraform_data.seaweedfs_network`를 대체한다. compose의
`trino`·`prometheus`는 `host.containers.internal`로 닿는다.

**Tech Stack:** Terraform 1.15(`terraform test` + `mock_provider`) · `kreuzwerker/docker` · podman · podman compose ·
bash

**Spec:** 같은 브랜치의
[`docs/superpowers/specs/*-seaweedfs-terraform-design.md`](../specs/2026-10-10-seaweedfs-terraform-design.md)
— 결정 번호(T1~T8·S1~S6)는 spec을 따른다.

## 실행 방식 (사용자 결정)

**혼합형 Subagent-driven**, 이 계획을 읽는 **새 세션**에서 시작한다.

| Task | 담당 |
| --- | --- |
| 0 spike | 메인 세션(`terraform apply` 필요 — `devops-engineer`는 apply 금지) |
| 1·2·3·4 | `devops-engineer` 구현 → `reviewer` 검토, Task마다 |
| 5 | `devops-engineer` 또는 메인(#181 머지 후 rebase 선행) |
| 6·7 | 메인 + `reviewer` 보안 체크리스트 + 사용자 승인(비가역) |

spike 결과가 Task 1·2의 값(프로바이더 버전·CPU 속성·실패 시 대안)을 바꾸면 메인이 이 문서를 먼저 고친 뒤 배정한다.

## Global Constraints

- 이미지 `chrislusf/seaweedfs:4.36` 고정, 컨테이너 이름 `seaweedfs`, kind 네트워크 `kind`·별칭 `seaweedfs-ext`.
- 포트는 전부 `127.0.0.1` 게시: `9333`(master UI)·`8333`(S3)·`8888`(filer UI)·`9324`(메트릭, 신규).
- command(compose와 동일):
  `mini -ip.bind=0.0.0.0 -dir=/data -metricsPort=9324 -s3.config=/etc/seaweedfs/s3.json -admin.ui=false -webdav=false`.
- healthcheck: `curl -s -o /dev/null http://127.0.0.1:8333/` · interval `10s` · timeout `5s` · retries `6` ·
  start_period `20s`.
- 로그: `json-file`, `max-size=10m`, `max-file=20`(compose `x-docker-logging`과 동일). 메모리 상한 1G.
- `s3.json` 형식(storage-up.sh와 동일): identity `lakehouse`, actions `["Admin","Read","Write","List","Tagging"]`.
- 버킷 3개: `warehouse`·`pg-backup`·`dagster-logs` — 생성만, destroy 동작 없음.
- 프로바이더·required_version은 **정확히 고정**(versions.tf 머리 주석 규칙). docker 프로바이더 버전은 Task 0에서
  init한 값을 그대로 고정한다.
- Terraform은 저장소 **루트 체크아웃**에서만 apply한다. 포매터 `terraform fmt`(2-space).
- 비가역(`apply`·`destroy`·compose `down`)은 실행 전 `reviewer` 보안 체크리스트 + 사용자 승인.
- 커밋은 Conventional Commits·한국어, 사용자 요청 시에만.

## Review Focus

1. **빈 키로 apply** → 컨테이너가 인증 없이(또는 빈 키로) 뜨면 안 된다. 변수 validation에서 멈춰야 한다 — Task 1 테스트.
2. **linked worktree에서 apply** → 빈 `seaweedfs/data`로 새 레이크가 조용히 뜨면 안 된다. precondition에서 멈춘다 — Task
1 테스트.
3. **클러스터만 재생성** → 컨테이너는 교체되어도 별칭·마운트 경로가 같아 데이터가 그대로 보여야 한다 — Task 2 plan 단언
+ Task 7 실측.
4. **키 회전** → 새 키가 컨테이너에 반영돼야 한다(`upload` 내용이 변수에서 온다) — Task 2 단언, 반영은 컨테이너 교체로.
5. **podman machine이 꺼진 채 apply** → 아무것도 만들기 전에 프로바이더 접속 오류로 멈춰야 한다 — Task 6 Step 1 관찰.

---

### Task 0: spike — 프로바이더 가정 판정 (버리는 코드)

**Files:** scratchpad의 임시 루트 `spike/main.tf`만. 저장소에 남기지 않는다.

**Interfaces:**
- Produces: S1~S6 판정표 + 고정할 `kreuzwerker/docker` 버전 + `cpus` 류 속성 지원 여부(S7). Task 1·2는 이 결과를 전제로
  한다.

- [ ] **Step 1: VM 기동 확인** — `podman machine list`가 running, 소켓 경로는
  `podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}'`.
- [ ] **Step 2: 임시 루트 작성** — `docker_image`(keep_locally=true) + `docker_container` 이름 `seaweedfs-spike`,
  포트 `127.0.0.1:18333→8333`, `upload`로 테스트 키 `s3.json`, `healthcheck` + `wait = true`, `networks_advanced`
  (`kind` 네트워크가 없으면 `podman network create spike-net`으로 대체, 별칭 `spike-ext`), 데이터는 scratchpad 디렉터리
  바인드.
- [ ] **Step 3: S1·S3** — `terraform apply` 성공, 종료 직후
  `podman inspect -f '{{.State.Health.Status}}' seaweedfs-spike` = `healthy`.
- [ ] **Step 4: S2(음성·양성 대조)** — `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18333/` = `403`,
  그리고 `uv run --with boto3` 로 테스트 키 서명 `create_bucket`+`head_bucket` 성공. 둘 다여야 통과.
- [ ] **Step 5: S4** — `podman inspect -f '{{json .NetworkSettings.Networks}}' seaweedfs-spike`에 별칭 존재.
- [ ] **Step 6: S5** — 아래가 응답코드 `403`을 낸다(연결 실패 `000`이면 불통).

  ```shell
  podman run --rm --entrypoint curl chrislusf/seaweedfs:4.36 \
    -s -o /dev/null -w '%{http_code}' http://host.containers.internal:18333/
  ```
- [ ] **Step 7: S6** — 재 `apply` → `No changes.`. **S7**: `terraform providers schema -json`에서 `docker_container`의
  CPU 상한 속성 이름 확인.
- [ ] **Step 8: 회수** — `terraform destroy`, 테스트 네트워크·scratchpad 데이터 삭제. 판정표를 PR #183 코멘트 초안으로
  남긴다(발행은 사용자).
- [ ] **게이트**: S1 실패 → 중단하고 사용자에게 접근 B 전환을 묻는다. S2~S6 실패 → spec의 「실패 시」 열로 Task 2를 고친
  뒤 진행.

### Task 1: 프로바이더·변수·worktree 가드 (스택 A)

**Files:**
- Modify: `terraform/cluster/kind/versions.tf` — `docker = { source = "kreuzwerker/docker", version = "<Task 0 값>" }`
- Modify: `terraform/cluster/kind/variables.tf` — 변수 4개
- Create: `terraform/cluster/kind/provider.tf` — `provider "docker" { host = var.container_host }`
- Create: `terraform/cluster/kind/scripts/detect-checkout.sh` — `{"linked":"true"|"false"}` 출력
- Modify: `terraform/cluster/kind/main.tf` — `data "external" "checkout"`
- Modify: `terraform/cluster/kind/tests/validation.tftest.hcl`
- Modify: `terraform/cluster/kind/.terraform.lock.hcl`(init 결과, 커밋 대상)

**Interfaces:**
- Produces: `var.container_host` (string, 기본값 없음, `unix://`로 시작 validation),
  `var.s3_access_key`·`var.s3_secret_key` (string, `sensitive = true`, 빈 값 금지 validation),
  `var.seaweedfs_data_dir` (string, 기본 `null` →
  `local.seaweedfs_data_dir = coalesce(var…, abspath("${path.module}/../../../seaweedfs/data"))`),
  `data.external.checkout.result.linked` ("true"/"false"; `git rev-parse --git-dir` ≠ `--git-common-dir`이면 "true").

- [ ] **Step 1: 실패하는 테스트** — `validation.tftest.hcl`에 파일 수준 `mock_provider "docker" {}`,
  `override_data { target = data.external.checkout, values = { result = { linked = "false" } } }`, 기본 `variables`
  (`container_host = "unix:///tmp/podman.sock"`, 키 `"test-access"`/`"test-secret"`) 추가 후 run 3개:
  - `reject_empty_access_key`: `s3_access_key = ""` → `expect_failures = [var.s3_access_key]`
  - `reject_non_unix_host`: `container_host = "tcp://x"` → `expect_failures = [var.container_host]`
  - (worktree precondition 테스트는 리소스가 생기는 Task 2에 둔다 — 없는 리소스를 참조하면 파일 전체가 로드되지 않는다)
- [ ] **Step 2: 실패 확인** —
  `terraform -chdir=terraform/cluster/kind init -backend=false && terraform -chdir=terraform/cluster/kind test`
  → 변수 미정의로 FAIL.
- [ ] **Step 3: 구현** — 위 Interfaces대로. `detect-checkout.sh`는 `detect-runtime.sh`와 같은 모양(`set -euo pipefail`,
  JSON 한 줄).
- [ ] **Step 4: 통과 확인** — 같은 명령, 기존 run + 신규 2개 PASS.
  `terraform fmt -check -recursive terraform/cluster/kind` 무출력.
- [ ] **Step 5: Commit** — `feat(terraform): 스택 A에 docker 프로바이더와 S3 키 변수를 더한다`

### Task 2: SeaweedFS 컨테이너·버킷 리소스

**Files:**
- Modify: `terraform/cluster/kind/main.tf` —
  `docker_image.seaweedfs`·`docker_container.seaweedfs`·`terraform_data.seaweedfs_buckets` 추가,
  `terraform_data.seaweedfs_network` 삭제
- Modify: `terraform/cluster/kind/outputs.tf` — `output "s3_endpoint"` = `"http://localhost:8333"`
- Test: `terraform/cluster/kind/tests/seaweedfs.tftest.hcl`(신규, 같은 mock·override 머리)

**Interfaces:**
- Consumes: Task 1의 변수·`local.seaweedfs_data_dir`·`data.external.checkout`.
- Produces: `docker_container.seaweedfs`(name `seaweedfs`),
  `terraform_data.seaweedfs_buckets`(`triggers_replace = docker_container.seaweedfs.id`).

- [ ] **Step 1: 실패하는 테스트** — `run "seaweedfs_container_shape"`(command = plan) 단언:
  - `docker_container.seaweedfs.name == "seaweedfs"`
  - 포트 집합: `[for p in docker_container.seaweedfs.ports : "${p.ip}:${p.external}"]`에
    `127.0.0.1:9333`·`8333`·`8888`·`9324` 모두 포함, 길이 4
  - `one([for n in docker_container.seaweedfs.networks_advanced : n.aliases if n.name == "kind"])`에 `"seaweedfs-ext"`
    포함
  - `docker_container.seaweedfs.volumes`의 `container_path == "/data"` 항목 `host_path`가 `endswith("seaweedfs/data")`
  - `upload`의 `file == "/etc/seaweedfs/s3.json"` 항목 `content`에 `"test-access"` 포함 (Review Focus 4)
  - `docker_container.seaweedfs.command`가 Global Constraints의 command를 공백 분리한 리스트와 같다
  - 별도 `run "reject_linked_worktree"`: run 안 `override_data` linked = `"true"` →
    `expect_failures = [docker_container.seaweedfs]` (Review Focus 2)
- [ ] **Step 2: 실패 확인** — `terraform … test` → 리소스 없음으로 FAIL.
- [ ] **Step 3: 구현** — `docker_container`: `image = docker_image.seaweedfs.image_id`,
  `depends_on = [kind_cluster.this]`,
  `wait = true`·`wait_timeout = 120`, healthcheck·로그·메모리 1024는 Global Constraints 값, CPU 상한은 Task 0 S7 속성,
  `lifecycle.precondition { condition = data.external.checkout.result.linked == "false" }`(오류 메시지에 「메인
  체크아웃에서 apply」).
  `upload.content`는 `jsonencode`로 `s3.json` 구조를 만든다. 버킷은 `local-exec`(`/bin/bash -ec`)로 버킷마다
  최대 12회 `s3.bucket.create` → `s3.bucket.list`에 이름이 나올 때까지 5초 간격 재시도(storage-up.sh 3단계와 같은 판정),
  끝내 없으면 `exit 1`. destroy 프로비저너는 두지 않는다. `docker_image`는 `keep_locally = true`.
- [ ] **Step 4: 통과 확인** — 전체 `terraform test` PASS(`reject_linked_worktree` 포함), `terraform validate`,
  `fmt -check`.
- [ ] **Step 5: Commit** — `feat(terraform)!: SeaweedFS 컨테이너를 스택 A가 소유한다`

### Task 3: compose·소비자 이행, `storage-up.sh` 삭제

**Files:**
- Modify: `compose.yml` — `seaweedfs` 서비스 블록 삭제, `trino`·`prometheus`의 `depends_on`에서 `seaweedfs` 삭제
- Modify: `trino/etc/catalog/iceberg.properties:14` — `s3.endpoint=http://host.containers.internal:8333`
- Modify: `prometheus/prometheus.yml:11` — `"host.containers.internal:9324"`
- Delete: `scripts/storage-up.sh`

- [ ] **Step 1: 실패 확인(현재 상태 기준선)** — `grep -c "seaweedfs:" compose.yml` > 0 을 기록.
- [ ] **Step 2: 수정** — 위 Files대로. `trino`의 `depends_on`이 `postgres`만 남는지 확인.
- [ ] **Step 3: 검증** — 각각 종료코드 0이고 출력에 `seaweedfs`가 없다:
  `podman compose --profile legacy-sql config --services`, `podman compose --profile monitoring config --services`,
  `podman compose --profile host-dagster config --services`. `podman compose --profile storage config --services`는 빈
  출력.
- [ ] **Step 4: Commit** — `refactor(compose)!: seaweedfs 서비스와 storage-up.sh를 제거한다`

### Task 4: 코드·설정 주석과 안내 갱신

**Files:** spec §기존 자산 이행의 「주석·안내 대상」 목록(`.env.example`, `common/constants.py`,
`scripts/k8s-secrets.sh`,
`gitops/charts/storage-external/values.yaml`·`tests/expect.yaml`, `k8s/catalog-pg-backup.yaml`,
`scripts/storage_conformance_probe.py`, `notebooks/00-lakehouse-connect.ipynb`).

- [ ] **Step 1: 대상 재측정** —
  `grep -rlI storage-up . --exclude-dir=.git --exclude-dir=.terraform --exclude-dir=superpowers`
  를 spec 목록과 대조(다르면 이 Task 목록을 갱신).
- [ ] **Step 2: 수정** — 「`storage-up.sh`」 → 「스택 A `docker_container.seaweedfs`(`terraform apply`)」. 동작 코드는
  바꾸지 않는다.
  `expect.yaml`은 주석만인지 확인하고 아니면 `scripts/gitops-charts-check.sh`로 검증.
- [ ] **Step 3: 검증** — 위 grep이 문서(Task 5 대상) 외 0건. `scripts/gitops-charts-check.sh` 통과,
  `uv run --with ruff ruff check` 변경 파일 통과,
  노트북은 `nbstripout` 훅 통과.
- [ ] **Step 4: Commit** — `chore: storage-up.sh 언급을 스택 A로 바꾼다`

### Task 5: 문서 갱신

**Precondition:** PR #181 머지 → `git fetch` 후 이 브랜치를 `origin/main` 기준 rebase(`docs/setup/local-k8s.md`가 그
PR에서 생긴다).

**Files:** spec 「문서 대상」 목록 + `docs/security.md`(정책) + 볼트 `security/posture.md`(실태, 저장소 밖).

- [ ] **Step 1: 수정 포인트**
  - `docs/setup/local-k8s.md`: 2단계(외부 S3)를 3단계(스택 A)에 합친다 — `plan` 표에
    `docker_image`·`docker_container`·`seaweedfs_buckets`,
    `TF_VAR_s3_access_key`·`TF_VAR_s3_secret_key`·`TF_VAR_container_host` 준비 명령(값은 `.env`에서, 화면에 찍지 않는
    형태),
    체크리스트 1줄 「`.env`의 `AWS_*`와 `ICEBERG_S3_*`는 같은 값」, 내리기에 「destroy해도 `seaweedfs/data`는 남는다」.
  - `docs/setup.md` §3 층 지도·단축 블록, `CLAUDE.md`·`AGENTS.md`의 「SeaweedFS는 클러스터 밖 compose
    정본(`storage-up.sh`)」 문장.
  - `architectures/storage.md`: 결정 E1 번복 기록(이유: 테스트 환경 IaC 일원화, 데이터는 바인드로 보존).
  - `security.md`: T8 수용 근거·재검토 조건(spec §보안 문장 그대로). 볼트 `posture.md`에 같은 내용 한 벌.
  - `conventions/docker.md`(profile `storage` 제거·`seaweedfs` 의존 profile 3개 규칙의 사례 갱신), `conventions/k8s.md`,
    `operations.md`, `overview.md`, `redesign.md`.
- [ ] **Step 2: 검증** — `grep -rn "storage-up" docs CLAUDE.md AGENTS.md README.md` 가 `superpowers/` 밖 0건(대조군:
  같은 명령으로 `docker_container.seaweedfs` ≥ 1건).
  스테이징 후 `pre-commit run --files <변경 문서>`에서 문서 링크·가독성 훅 `Passed`.
- [ ] **Step 3: Commit** — `docs: SeaweedFS 정본을 스택 A로 갱신한다`

### Task 6: 전환 실기동 (🔴 비가역 게이트)

**실행 위치:** 루트 체크아웃(`/Users/jin/dagster-study`) — tfstate가 거기 있고, worktree apply는 Task 2의
precondition이 막는다. 따라서 Task 0~5가 CI 녹색이 된 뒤 PR을 머지하고, 루트에서 **Step 2(compose down)를
`git pull` 전에** 한다 — pull 뒤의 compose.yml엔 `seaweedfs`가 없어 down이 대상을 못 찾는다.
Task 6·7이 실패하면 머지 커밋을 revert하는 PR로 되돌린다(데이터는 바인드라 손실 없음 — Step 1 기준선으로 판정).

- [ ] **Step 0: 게이트** — `reviewer`에 보안 체크리스트(키 주입 경로·tfstate 평문·포트 노출·compose down 대상) 1회 →
  사용자 승인.
- [ ] **Step 1: 기준선 박제** — `find seaweedfs/data -type f | wc -l` 과 정렬 해시
  목록(`find … -type f -exec shasum -a 256 {} + | sort`)을
  scratchpad에 저장(이 값은 *파일 수*를 센다). VM을 일부러 멈춘 상태의 `plan`이 프로바이더 접속 오류로 끝나는지 1회
  관찰(Review Focus 5).
- [ ] **Step 2: compose 내리기** — 루트 체크아웃에서 **pull 전** `podman compose --profile storage down` →
  `podman ps -a --filter name=seaweedfs` 0건 → 그다음 루트 `git pull --ff-only`.
- [ ] **Step 3: plan** — 루트에서 브랜치 내용으로 `terraform -chdir=terraform/cluster/kind plan` + 기존 `-var` +
  `TF_VAR_*` →
  기대 **3 to add · 0 to change · 1 to destroy**(`seaweedfs_network`). 다르면 멈추고 보고.
- [ ] **Step 4: apply 후 확인** — `healthy`, `scripts/storage_conformance_probe.py` 통과, 기존 객체 목록이 Step 1 이전과
  같은 버킷에 보임,
  파드에서 `seaweedfs:8333` 응답(`kubectl run --rm` 1회용 curl), `podman compose --profile monitoring up -d prometheus`
  후 타깃 `up`.
- [ ] **Step 5: 재 apply** — `No changes.`

### Task 7: destroy 데이터 보존 실측 (🔴 비가역 게이트)

- [ ] **Step 0: 게이트** — Task 6과 같은 게이트(대상: 스택 B → 스택 A destroy).
- [ ] **Step 1: destroy** — 스택 B → 스택 A 순. `podman ps -a --filter name=seaweedfs` 0건.
- [ ] **Step 2: 보존 판정** — Task 6 Step 1과 같은 명령의 파일 수·해시 목록이 **일치**(불일치면 즉시 중단·보고, 재생성
  금지).
- [ ] **Step 3: 재기동** — 스택 A·B apply → 같은 객체가 보이고 Application 전부 `Synced`·`Healthy`.
- [ ] **Step 4: 회수** — 검증용 상주 컴퓨트를 쓰지 않았는지 확인, 필요 시 VM 정지(사용자 결정).
- [ ] **Step 5: PR** — draft 해제는 사용자 요청 시. 범위 밖 Issue 1줄: `kind-registry` Terraform 이관.
