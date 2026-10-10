# 외부 스토리지(SeaweedFS on Docker) + CNPG 카탈로그 차트화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** S3 정본을 kind 밖 compose SeaweedFS로 옮기고, 카탈로그 Postgres CR을 ArgoCD 차트로 넘긴다.

**Architecture:** compose `seaweedfs`가 독립 기동하고, kind 네트워크에 별칭 `seaweedfs-ext`로 후결합된다.
클러스터 안에서는 `Service seaweedfs`(`ExternalName` → `seaweedfs-ext`)가 기존 엔드포인트 `http://seaweedfs:8333`을
그대로 받는다. CNPG `catalog-postgres` Cluster CR은 `gitops/charts/catalog-postgres`로 옮겨 ApplicationSet이 관리한다.

**Tech Stack:** podman compose · kind · Terraform(`terraform_data`) · Helm 차트 + ArgoCD ApplicationSet · CNPG · bash ·
Python(PEP 723, boto3)

**Spec:** 같은 브랜치 `docs/superpowers/specs/`의 `*-external-storage-design.md` — 계획은 spec을 근거로 하므로 함께
읽는다.

## Global Constraints

- 컨테이너 런타임은 podman — 명령은 `podman compose ...`.
- compose `seaweedfs` 이미지 `chrislusf/seaweedfs:4.36` 유지, 포트는 `127.0.0.1` 바인딩 유지.
- profile: `seaweedfs`는 `["storage", "legacy-sql", "monitoring"]`(의존자 profile 상속).
- kind 네트워크 별칭 `seaweedfs-ext`, 클러스터 Service 이름 `seaweedfs`(ns `default`), 포트 `8333`.
- 버킷 3개: `warehouse`·`pg-backup`·`dagster-logs`.
- S3 키 단일 출처: `.env`의 `ICEBERG_S3_ACCESS_KEY`/`ICEBERG_S3_SECRET_KEY` = K8s Secret `lakehouse-creds`의
  `s3-access-key`/`s3-secret-key` = `seaweedfs/s3.json`.
- boto3 클라이언트는 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`·`AWS_RESPONSE_CHECKSUM_VALIDATION=when_required`.
- `scripts/*.py`는 PEP 723 + 절차형 단일 `main()`. 셸은 `set -euo pipefail` + shellcheck 통과.
- 주석·문서·커밋 설명은 한국어, Conventional Commits, 제목 72자 이내.
- `docs/` 본문에 일자·실측 수치를 두지 않는다. doc-lint: 줄 120열(한글 2열), 🔴 문서당 5개 이하.
- Terraform은 `terraform fmt`(2-space). 비가역 집행(🔴)은 reviewer 체크리스트 + 사용자 승인 후에만.

## Review Focus

1. **compose 재기동으로 kind 네트워크 연결 해제** — 파드에서 `seaweedfs` 해석 실패. 기대: `storage-up.sh` 재실행이
   재연결(Task 1 Step 4).
2. **키 불일치** — 나열은 되는데 `load_table`이 `ACCESS_DENIED`. 기대: 실증이 `load_table`까지 간다(Task 6 Step 5).
3. **레거시 Service가 정본 대신 답함** — 기대: 클러스터 생성 후
   `kubectl get endpointslices -l kubernetes.io/service-name=seaweedfs` 0건(Task 6 Step 3).
4. **원천 손상(백업에서 승격된 데이터)** — 기대: `raw/` 사이드카 대조가 불일치 시 실패하고, 음성 대조로
   그 검사가 실제로 잡는지 본다(Task 1 Step 6).
5. **kind 없이 compose만 기동** — 기대: `storage-up.sh`가 경고 후 성공(독립성), TF는 컨테이너 부재 시 경고·skip(Task 1
   Step 4, Task 2 Step 3).

---

## PR-A — 외부 스토리지 (`feat(storage)`)

### Task 0: Spike — 파드에서 kind 네트워크 별칭 해석 (게이트, 코드 비보존)

> **개정:** 클러스터가 없어 코드(Task 1~3·5·7·8)를 먼저 하고, 이 spike는 Task 6 클러스터 생성 직후
> Service 적용 **전에** 실행한다. 실패하면 Task 2·3을 B안으로 고친다(대가: 그 두 Task의 재작업).

**Files:** 없음(임시 리소스는 같은 Task에서 회수).

- [ ] **Step 1:** 현행 compose `seaweedfs`를 띄우고(`./scripts/storage-up.sh` — 별칭 연결 포함)
  `podman network connect --alias seaweedfs-ext kind seaweedfs`.
- [ ] **Step 2:** 임시 Service `spike-s3`(`type: ExternalName`, `externalName: seaweedfs-ext`)를 `default`에 적용하고
  `kubectl run spike --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- curl -s -o /dev/null -w '%{http_code}\n'
  http://spike-s3:8333/`.
  Expected: HTTP 코드 출력(무인증이라 `200`/`403` 어느 쪽이든 **응답이 온 것**이 통과).
- [ ] **Step 3 (음성 대조):** 같은 명령을 `externalName: seaweedfs-nope`로 바꿔 실행. Expected: `000`(해석 실패).
  Step 2와 Step 3이 갈리지 않으면 판정 무효.
- [ ] **Step 4:** 회수 — `kubectl delete svc spike-s3`, `podman network disconnect kind seaweedfs`, compose `seaweedfs`
      정지.
- [ ] **Step 5:** 결과를 사용자에게 보고. 실패 시 **여기서 정지**하고 spec E2를 B안(호스트 게이트웨이)으로 재설계한다.

### Task 1: compose `seaweedfs` 정본 승격 + `scripts/storage-up.sh`

**Files:**
- Modify: `compose.yml` (`seaweedfs` 블록과 위 주석)
- Create: `scripts/storage-up.sh`
- Modify: `.gitignore`는 이미 `seaweedfs/`를 무시 — `seaweedfs/s3.json`도 포함됨을 `git check-ignore`로 확인만.

**Interfaces:**
- Produces: `./scripts/storage-up.sh` — 인자 없음. `.env`의 `ICEBERG_S3_*`를 읽어 `seaweedfs/s3.json`(0600) 생성 →
  `podman compose --profile storage up -d seaweedfs` → healthy 대기 → 버킷 3개 멱등 생성 → kind 네트워크가 있으면
  별칭 `seaweedfs-ext`로 멱등 연결(없으면 경고 후 계속). 종료코드 0 = 위 전부 성공.

- [ ] **Step 1:** `compose.yml` `seaweedfs` 수정
  - `profiles: ["storage", "legacy-sql", "monitoring"]`
  - `command: "mini -ip.bind=0.0.0.0 -dir=/data -metricsPort=9324 -s3.config=/etc/seaweedfs/s3.json"`
  - 볼륨 추가 `./seaweedfs/s3.json:/etc/seaweedfs/s3.json:ro`
  - healthcheck: S3 포트 TCP 응답(이미지 내 `wget -q -O /dev/null http://127.0.0.1:8333/` 의 비정상 종료 허용 여부를
    먼저 컨테이너에서 확인하고, 인증 403도 "살아 있음"으로 치는 명령을 고른다).
  - 블록 위 주석: "레거시·이관 전 백업" 서술을 "정본(외부 스토리지)"으로 교체. 무인증 결정 주석은
    **재검토 트리거 ①(다시 서비스로 쓸 때)이 발동해 인증을 켰다**로 갱신(`docs/security.md` §4-3과 한 벌 — Task 5).
- [ ] **Step 2:** `podman compose --profile storage config --services`, `--profile legacy-sql`, `--profile monitoring`
      각각 실행.
  Expected: 셋 모두 `seaweedfs` 포함, `legacy-sql`엔 `trino`·`postgres`도 포함(기존과 동일).
- [ ] **Step 3:** `scripts/storage-up.sh` 작성. 버킷 생성은 `k8s-poc-storage.sh:204-240`의 `weed shell` 루프를
  `podman exec seaweedfs`로 옮기되, REPL 종료코드를 믿지 않고 `s3.bucket.list` 출력에 버킷명이 있는지로 판정.
  네트워크 연결 판정은 `terraform/cluster/kind/main.tf`의 `registry_network`와 같은 `NetworkSettings.Networks.kind`
  검사.
- [ ] **Step 4 (Review Focus 1·5):** 실행 시나리오 셋.
  - kind 없음(또는 `podman network exists kind` 거짓): exit 0 + 경고 1줄.
  - 두 번 연속 실행: 두 번째도 exit 0, 버킷·연결 중복 오류 없음.
  - `podman compose --profile storage restart seaweedfs` 후 재실행: `podman inspect -f '{{json
    .NetworkSettings.Networks.kind.Aliases}}' seaweedfs`에 `seaweedfs-ext` 포함.
- [ ] **Step 5 (인증 음성 대조):** `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8333/warehouse` → `403`,
  같은 요청을 `.env` 키로 서명(`aws s3 ls s3://warehouse --endpoint-url http://127.0.0.1:8333` 또는 boto3 한 줄) → 성공.
- [ ] **Step 6 (Review Focus 4):** 원천 무결성 — `raw/` 아래 `<key>.sha256` 사이드카를 가진 객체 전부를 스트리밍
  해시해 사이드카 첫 토큰과 대조(일회성 boto3 스니펫, 커밋 안 함). 라벨: "대조한 객체 수 / 불일치 수".
  음성 대조: 사이드카 하나를 임시 키로 복사해 내용을 바꾼 뒤 같은 검사를 돌려 불일치 1을 확인하고 임시 키 삭제.
- [ ] **Step 7:** `shellcheck scripts/storage-up.sh` 통과 후 커밋 `feat(storage): compose SeaweedFS를 인증 켠 정본으로
      승격한다`.

### Task 2: Terraform `seaweedfs_network`

**Files:** Modify `terraform/cluster/kind/main.tf` (`registry_network` 아래)

**Interfaces:** Produces `terraform_data.seaweedfs_network` — `triggers_replace = kind_cluster.this.id`, `depends_on =
[kind_cluster.this]`.

- [ ] **Step 1:** 리소스 추가. 셸 본문: `podman container exists seaweedfs`가 거짓이면 경고 출력 후 `exit 0`;
  연결 안 돼 있으면 `podman network connect --alias seaweedfs-ext kind seaweedfs`.
  주석: 컨테이너 수명은 compose 몫이고 이 스택은 연결만 한다(registry와 같은 분업), compose 재기동 시 재연결은
  `storage-up.sh`.
- [ ] **Step 2:** `terraform -chdir=terraform/cluster/kind fmt -check && terraform -chdir=terraform/cluster/kind
      validate`.
- [ ] **Step 3 (Review Focus 5):** `terraform -chdir=terraform/cluster/kind plan` Expected: `1 to add, 0 to change, 0
      to destroy`
  (seaweedfs_network만). 셸 본문을 컨테이너 부재 상태에서 직접 실행 → exit 0 + 경고.
- [ ] **Step 4:** 커밋 `feat(kind): SeaweedFS 컨테이너를 kind 네트워크에 후결합한다`.

### Task 3: `gitops/charts/storage-external`

**Files:**
- Create: `gitops/charts/storage-external/Chart.yaml`, `values.yaml`, `templates/service.yaml`, `tests/expect.yaml`
- Modify: `terraform/platform/variables.tf` (`var.apps` default), `scripts/gitops-charts-check.sh`(앱명→ns case)
- Test: `terraform/platform/tests/validation.tftest.hcl`(앱 수 단언이 있으면 갱신)

**Interfaces:** Produces Service `default/seaweedfs` — `type: ExternalName`,
`externalName: {{ .Values.externalName }}`(기본 `seaweedfs-ext`), `ports: [{name: s3, port: 8333}]`.
var.apps 원소 `{ name = "storage-external", path = "gitops/charts/storage-external", namespace = "default" }`.

- [ ] **Step 1:** `tests/expect.yaml`에 기대를 먼저 쓴다(렌더 결과 Service 1개). `scripts/gitops-charts-check.sh
      gitops/charts/storage-external` → FAIL(차트 없음).
- [ ] **Step 2:** 차트 작성(의존성 없음, `version: 0.1.0`). Service에 `argocd.argoproj.io/sync-options:
      Prune=false,Delete=false`
  (spec의 데이터 층 앱 규약, `docs/argocd-gitops.md` §4와 동일 형태).
- [ ] **Step 3:** `scripts/gitops-charts-check.sh gitops/charts/storage-external` → 마지막 줄 `검사한 차트: 1개`, FAIL
      0.
- [ ] **Step 4:** `var.apps` 원소 추가 → `terraform -chdir=terraform/platform test` 통과.
- [ ] **Step 5:** 커밋 `feat(gitops): 외부 SeaweedFS용 ExternalName 차트를 추가한다`.

### Task 4: (개정으로 삭제)

K8s 원천이 이미 소멸해 sync 대상이 없다(spec 개정 E4). 번호는 ledger 연속성을 위해 남긴다.

### Task 5: 구 경로 철거 + 문서 한 벌

**Files:**
- Delete: `k8s/seaweedfs.yaml`
- Modify: `scripts/k8s-poc-storage.sh`(§2 SeaweedFS apply·§3 버킷 생성 제거, `lakehouse-creds` 생성은 유지)
- Modify: `.env.example`(`ICEBERG_S3_ENDPOINT=http://localhost:8333`, port-forward 18333 안내 제거, 미사용
  `ENDPOINT_URL` 제거)
- Modify(문서): `docs/redesign.md`, `docs/architectures/storage.md`, `docs/architectures/overview.md`,
  `docs/conventions/k8s.md`(§1·§10·§11), `docs/conventions/docker.md`(profile `storage`),
  `docs/operations.md`(엔드포인트 표),
  `docs/security.md` §4-3, `docs/setup.md`(기동 절차에 `storage-up.sh`), `docs/argocd-gitops.md`(PR2 범위),
  `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1:** `git grep -n "k8s/seaweedfs.yaml\|18333\|legacy-storage"` 로 참조 모집단을 먼저 뽑아 목록화(이 수가
      대조군).
- [ ] **Step 2:** 코드·스크립트·`.env.example` 수정 후 같은 grep 재실행. Expected: 남은 히트는 "번복 이력" 서술뿐이고
      각각 의도된 것.
- [ ] **Step 3:** 문서 갱신. `CLAUDE.md`는 "SeaweedFS는 오퍼레이터 미채택…" 문장과 "`seaweedfs`도 스토리지 정본이 K8s로
      이전돼…" 문장을
  "SeaweedFS는 클러스터 밖 compose 정본, 파드는 `ExternalName`(kind 네트워크 별칭)으로 닿는다"로 교체 — **바이트
  순감**을 `wc -c` 전후로 확인.
- [ ] **Step 4:** `shellcheck scripts/k8s-poc-storage.sh`, `pre-commit run --files <변경 파일>` 통과(doc-lint·링크 검사
      포함).
- [ ] **Step 5:** 커밋 `refactor(k8s)!: K8s SeaweedFS를 철거하고 문서를 외부 스토리지로 갱신한다`(본문 `BREAKING
      CHANGE:` 엔드포인트·기동 절차 변경).
- [ ] **Step 6:** PR-A 생성(push는 사용자 요청 시). PR 본문에 Task 0 결과와 Task 6이 머지 **후** 집행임을 적는다.

### Task 6: (PR-B 뒤 최종 집행으로 이동)

## PR-B — 카탈로그 차트화 (`feat(k8s)`)

### Task 7: `gitops/charts/catalog-postgres`

**Files:**
- Create: `gitops/charts/catalog-postgres/Chart.yaml`, `values.yaml`, `templates/cluster.yaml`, `tests/expect.yaml`
- Delete: `k8s/catalog-postgres.yaml`
- Modify: `terraform/platform/variables.tf`(원소 `{ name = "catalog-postgres", path = "gitops/charts/catalog-postgres",
  namespace = "default" }`),
  `scripts/gitops-charts-check.sh`(case)

**Interfaces:** Produces Cluster `default/catalog-postgres` — 스펙은 `k8s/catalog-postgres.yaml`과 동일하되
`managed.roles`에서
`dagster` 원소를 지운다(클러스터를 새로 만들므로 롤이 생긴 적이 없다 — `ensure: absent` 불필요). 어노테이션
`argocd.argoproj.io/sync-options: Prune=false,Delete=false`.

- [ ] **Step 1:** `tests/expect.yaml` 먼저 → check FAIL.
- [ ] **Step 2:** CR을 템플릿으로 이관(Barman `plugins` 주석 블록 포함 그대로). `helm template`이 Helm 문법(`{{`)과
      충돌하는 줄이 없는지 확인.
- [ ] **Step 3:** `scripts/gitops-charts-check.sh gitops/charts/catalog-postgres` 통과, `terraform
      -chdir=terraform/platform test` 통과.
- [ ] **Step 4:** 동등성: `helm template catalog-postgres gitops/charts/catalog-postgres -n default` 결과와 삭제 전
  `k8s/catalog-postgres.yaml`을 `yq -P 'sort_keys(..)'`로 정규화해 diff. Expected: `dagster` 롤 원소 삭제와 어노테이션만
  차이.
- [ ] **Step 5:** 커밋 `feat(gitops): catalog-postgres CNPG 클러스터를 차트로 옮긴다`.

### Task 8: `k8s-poc-storage.sh` → `k8s-secrets.sh` + 문서

**Files:**
- Rename/Modify: `scripts/k8s-poc-storage.sh` → `scripts/k8s-secrets.sh`(Secret `lakehouse-creds`·`catalog-pg-app`만,
  `dagster-meta-pg-app`·`DAGSTER_PG_*`·CR apply·Barman 가드 제거; owner 가드는 경로를
  `gitops/charts/catalog-postgres/templates/cluster.yaml`로)
- Modify: `docs/conventions/k8s/cnpg.md`, `docs/argocd-gitops.md`(PR2 범위·k8s-secrets), `docs/setup.md`,
  `CLAUDE.md`(메타 Postgres 문장 정리), `AGENTS.md`

- [ ] **Step 1:** `git grep -n "k8s-poc-storage\|dagster-meta-pg-app\|DAGSTER_PG_"` 모집단 기록.
- [ ] **Step 2:** 스크립트 축소·이름 변경, 참조 갱신 후 grep 재실행 → 남은 히트 0(이력 서술 제외).
- [ ] **Step 3 (가드 위반 확인):** owner 가드가 살아 있는지 `PG_USER=wrong ./scripts/k8s-secrets.sh` → 가드 메시지로
      exit 1(클러스터 접속 전 단계).
- [ ] **Step 4:** `shellcheck`, `pre-commit run --files ...` 통과. 커밋 `refactor(scripts)!: 스토리지 스크립트를 Secret
      전용 k8s-secrets.sh로 줄인다`.
- [ ] **Step 5:** Barman 백업 재활성화 Issue 1줄 등록 제안(대상이 compose SeaweedFS로 바뀜) — 등록은 사용자 승인 후.
      PR-B 생성.

### Task 9: (Task 6에 흡수)

---

## 집행

### Task 6: 🔴 클러스터 생성·실증 (PR-A·PR-B 머지 후, reviewer + 사용자 승인)

ApplicationSet은 `main`을 추적하므로 두 차트는 머지 후에만 생긴다. 이 Task는 커밋을 만들지 않는다.

- [ ] **Step 1:** `./scripts/storage-up.sh` → healthy·버킷 3개(클러스터 없으면 경고만).
- [ ] **Step 2:** `scripts/k8s-up.sh` → `terraform -chdir=terraform/cluster/kind apply` → `./scripts/k8s-secrets.sh`
  → `terraform -chdir=terraform/platform apply`. 키 동일성: `.env` 키와 `lakehouse-creds`의 **sha256만** 비교(값
  비노출).
- [ ] **Step 3 (Review Focus 3):** `kubectl get svc seaweedfs -o jsonpath='{.spec.type}'` = `ExternalName`,
  endpointslice 0건, `kubectl get cluster catalog-postgres` Ready,
  `psql -Atc "select count(*) from pg_roles where rolname='dagster'"` = `0`.
- [ ] **Step 4 (Task 0 재확인):** 실제 Service로 `curl http://seaweedfs:8333/` 응답(인증 403 = 도달) — spike의 양성
  경로가
  실 구성에서도 성립하는지.
- [ ] **Step 5 (Review Focus 2):** 원천 → warehouse 재적재, Spark 새 테이블 쓰기→읽기, 호스트 pyiceberg `load_table`
  (`localhost:8333`). `uv run scripts/storage_conformance_probe.py`, `uv run scripts/spark_connect_smoke.py` 통과.
- [ ] **Step 6:** 검증용 상주 컴퓨트(Spark Connect 등)는 그 자리에서 내린다. compose 고아 `warehouse/` 정리 Issue 1줄
  제안.
