# Airflow on ArgoCD(PR3-B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공식 Airflow chart를 ArgoCD 앱으로 배포해 ArgoCD UI와 `airflow.localtest.me` Airflow UI를 함께 띄우고,
`hello` DAG의 Celery·K8s 두 경로 태스크를 성공시킨다.

**Architecture:** `gitops/charts/airflow/` umbrella chart(공식 `airflow` 1.22.0)를 ApplicationSet `var.apps`에
등록한다. 메타 DB는 CNPG `catalog-postgres`에 별도 롤·DB를 선언하고, 차트가 랜덤 생성하는 Secret은 전부
`scripts/k8s-secrets.sh`가 미리 만든다. 정적 검증은 `scripts/gitops-charts-check.sh`의 `expect.yaml` 단언이 맡는다.

**Tech Stack:** Helm(공식 apache-airflow chart 1.22.0, Airflow 3.2.2), Argo CD v3.5.3 ApplicationSet,
CloudNativePG(`Database` CR), Terraform(platform 스택), bash, podman.

**Spec:** 같은 브랜치 `docs/superpowers/specs/`의 `*-airflow-argocd-design.md` — 실행자는 계획과 spec을 함께 읽는다.

## Global Constraints

- 앱 이름 `airflow`, 네임스페이스 `airflow`, 릴리스 이름 = 앱 이름(그래서 리소스 접두어는 `airflow-`).
- `executor: "CeleryExecutor,KubernetesExecutor"` — 순서가 의미다(첫 자리 = 기본).
- Celery worker replicas `1`. triggerer·statsd·flower·pgbouncer·postgresql(내장)·webserver는 `enabled: false`.
- 이미지 `ghcr.io/melting-face/pipeline-study-airflow`, 태그 줄 하나에만 `# bump-image-tag` 마커.
- Ingress: `ingress.apiServer` host `airflow.localtest.me`, `ingressClassName: nginx`, TLS 없음.
- 메타 DB 접속 `catalog-postgres-rw.default.svc:5432/airflow`, 롤 `airflow`.
- Secret 값은 Git에 두지 않는다. 비밀번호 쌍은 **두 벌을 한 번에 만들고 반쪽이면 실패**.
- 모든 컨테이너에 `resources.requests`·`limits`를 둔다 — 검사기 (a) 단언.
- 문서(`docs/`)에는 일자·실측 수치를 쓰지 않는다. 실측은 볼트.
- 커밋은 **사용자 요청 시에만**(각 Task의 Commit 단계는 요청을 받은 뒤 수행). 메시지는 Conventional Commits·한국어.
- 비가역 단계(Task 7)는 `reviewer` 보안 체크리스트 1회 + 사용자 승인 후에만.

## 실측 사실 (계획 수립 시 확인 — 서브에이전트는 이 대화를 모른다)

- 차트 원본: `../argocd-study/gitops/charts/airflow/charts/airflow-1.22.0.tgz`(풀어서 읽는다),
  이식 출발점 values: `../argocd-study/gitops/charts/airflow/values.yaml`, 훅 교착 기록:
  `../argocd-study/wiki/airflow-on-argocd.md`. 자매 저장소는 LocalExecutor·내장 PG라 그 부분은 이식하지 않는다.
- `values.schema.json`의 `executor` 패턴은 쉼표 구분 다중 executor를 허용한다.
- `templates/secrets/redis-secrets.yaml`은 `randAlphaNum` + `helm.sh/hook: pre-install`로 Redis 비밀번호·broker URL
  Secret을 만든다 → `redis.passwordSecretName`과 `data.brokerUrlSecretName`을 **함께** 줘야 렌더되지 않는다.
- `data.metadataSecretName`의 Secret은 `connection` 키(SQLAlchemy URL)를 가져야 한다.
  result backend는 이 값으로 폴백한다.
- `createUserJob.defaultUser.password` 기본값은 `admin`이다. `createUserJob.args`는 템플릿 문자열 목록이라
  `-p` 다음 원소를 `$(AIRFLOW_ADMIN_PASSWORD)`로 바꾸고 `createUserJob.env`에 `secretKeyRef`를 주면 K8s가 치환한다.
- 차트 기본 `auth_manager`는 `FabAuthManager`(values 4241행 부근)라 `airflow users create`가 유효하다.
- `scripts/gitops-charts-check.sh`는 `expect.yaml`의 **모르는 최상위 키를 조용히 무시**한다(Task 1이 고친다).
- `scripts/k8s-secrets.sh`의 `ensure_secret`은 네임스페이스가 `default`로 고정이다(Task 3이 고친다).
- ApplicationSet은 `targetRevision: main`을 추적한다 → `catalog-postgres` 변경은 **머지 즉시** sync된다.
  airflow 앱은 `var.apps` apply 전까지 생기지 않는다.
- 계획 수립 시점에 kind 클러스터는 꺼져 있었다(Task 7 Step 0이 기동한다).

## 실행 규칙 (사용자 합의)

- Task 1→6 **순차**. 워커: Task 1·2·3·4·6 `devops-engineer`, Task 5 `data-engineer`, 리뷰 `reviewer`.
  Task 7은 메인 세션.
- 워커는 커밋하지 않는다 — 리뷰 통과 후 메인 세션이 Task별로 커밋한다(이 브랜치 한정 일괄 허용). 푸시·PR은 별도 요청.
- 리뷰 결함은 같은 워커에게 수정 왕복 최대 2회, 넘으면 사용자에게 올린다. 범위 밖 결함은 Issue 한 줄.
- PR 직전 `reviewer` 전체 브랜치 리뷰 1회, Task 7 직전 보안 체크리스트 1회.

## Review Focus

1. **`expect.yaml`의 모르는 키** — 검사기가 무시해 오타·미구현 키가 초록으로 남는다 → Task 1이 미지 키를 실패로 만든다.
2. **Secret 반쪽 상태**(쌍 중 한쪽만 존재 — 수동 삭제·부분 실패 후 재실행) → 스크립트가 만들지 않고 실패해야 한다(Task 3
   프로브).
3. **훅 회귀**(values 정리 중 `useHelmHooks: false` 누락) → ArgoCD 교착. Task 4의 위반 프로브가 단언 실패를 확인.
4. **재sync 시 OutOfSync 복귀**(차트가 무언가를 랜덤 렌더) → Task 4의 이중 렌더 diff, Task 7의 재sync 관측.
5. **공유 PG 연결 고갈**(Airflow 풀 × 프로세스 수) → Task 7의 `pg_stat_activity` 실측.

---

### Task 1: 차트 검사기 확장 — `absent`·`no_hook_kinds`·미지 키 거부·airflow 네임스페이스

**Files:**
- Modify: `scripts/gitops-charts-check.sh` — 헤더 주석의 (c) 목록, `case` 매핑, 파이썬 `(c)` 블록

**Interfaces:**
- Produces(`tests/expect.yaml` 키):
  - `absent: ["Kind/name", ...]` — 그 리소스가 렌더 결과에 **없다**.
  - `no_hook_kinds: [Kind, ...]` — 그 kind의 어떤 문서에도 `helm.sh/hook` 어노테이션이 없다(대상 0개면 실패).
  - 허용 키 집합 밖의 최상위 키 → `(c) 알 수 없는 키 <k>` 실패.
  - `case`에 `airflow) ns="airflow" ;;`.

- [ ] **Step 1: 실패하는 프로브(키가 무시되는지)를 만든다**
  `gitops/charts/storage-external/tests/expect.yaml`에 임시로 `absent: ["Service/seaweedfs"]`(렌더에 **있는** 리소스)를
  추가한다.

- [ ] **Step 2: 현재는 통과해 버림을 확인(= 키가 읽히지 않는다)**
  Run: `scripts/gitops-charts-check.sh gitops/charts/storage-external`
  Expected: `검사한 차트: 1개`, exit 0 — 이것이 결함의 증거다.

- [ ] **Step 3: 구현** — 파이썬 블록 `(c)`에 `absent`·`no_hook_kinds` 처리와 허용 키 집합
  `{crd_keep_exempt, min_crds, service_accounts, fields, kinds_with_wave, absent, no_hook_kinds}` 검사를 넣고,
  헤더 주석 (c)에 두 키를 한 줄씩 적는다. `case`에 airflow 매핑을 추가한다.

- [ ] **Step 4: 프로브가 이제 실패함을 확인**
  Run: 같은 명령. Expected: `FAIL [storage-external] (c) Service/seaweedfs: 렌더 결과에 있다(absent)`, exit 1.
  추가 프로브: 임시 키 `absnet: []` → `(c) 알 수 없는 키 absnet`.

- [ ] **Step 5: 프로브를 지우고 전체 통과 확인**
  Run: `scripts/gitops-charts-check.sh` → Expected: 기존 6개 차트 모두 통과, `검사한 차트: 6개`.

- [ ] **Step 6: Commit** — `test(gitops): 차트 검사기에 부재·훅 단언과 미지 키 거부를 더한다`

### Task 2: CNPG에 `airflow` 롤·DB 선언

**Files:**
- Modify: `gitops/charts/catalog-postgres/templates/cluster.yaml`(`managed.roles`, 하단 「Dagster 메타 롤은 두지
  않는다」 주석)
- Create: `gitops/charts/catalog-postgres/templates/database-airflow.yaml`
- Modify: `gitops/charts/catalog-postgres/tests/expect.yaml`

**Interfaces:**
- Produces: 롤 `airflow`(passwordSecret `airflow-pg-app`, ns `default`), `Database/airflow`(name `airflow`, owner
  `airflow`, cluster `catalog-postgres`).

- [ ] **Step 1: 단언을 먼저 쓴다** — `expect.yaml`의 `roles, 1 = null`을 아래로 교체:
  ```yaml
  - {ref: Cluster/catalog-postgres, path: [spec, managed, roles, 1, name], value: airflow}
  - {ref: Cluster/catalog-postgres, path: [spec, managed, roles, 1, passwordSecret, name], value: airflow-pg-app}
  - {ref: Cluster/catalog-postgres, path: [spec, managed, roles, 1, connectionLimit], value: -1}
  - {ref: Cluster/catalog-postgres, path: [spec, managed, roles, 1, inherit], value: true}
  - {ref: Cluster/catalog-postgres, path: [spec, managed, roles, 2], value: null}
  - {ref: Database/airflow, path: [spec, owner], value: airflow}
  - {ref: Database/airflow, path: [spec, cluster, name], value: catalog-postgres}
  - {ref: Database/airflow, path: [metadata, annotations, argocd.argoproj.io/sync-options], value: "Prune=false,Delete=false"}
  ```
  상단 주석 「index 1 = 부재」도 갱신.

- [ ] **Step 2: 실패 확인** — `scripts/gitops-charts-check.sh gitops/charts/catalog-postgres` →
      `roles.1.name: 기대 'airflow', 실제 None`·`Database/airflow: 렌더 결과에 없다`.

- [ ] **Step 3: 구현** — 롤 항목은 `iceberg` 항목과 같은 모양(`ensure: present`, `login: true`, comment 「Airflow 메타
      DB 소유자」).
  `Database` CR은 `postgresql.cnpg.io/v1`, ns `default`, `spec.ensure: present`. 「Dagster 메타 롤」 주석을
  「오케스트레이터(Airflow) 메타 롤은 `iceberg`와 분리한다」로 고친다.

- [ ] **Step 4: 통과 확인** — 같은 명령 → exit 0.

- [ ] **Step 5: Commit** — `feat(catalog-postgres): Airflow 메타 DB용 롤과 Database를 선언한다`

### Task 3: `k8s-secrets.sh` — 네임스페이스 인자와 Airflow Secret 8종

**Files:**
- Modify: `scripts/k8s-secrets.sh`
- Modify: `docs/conventions/k8s/cnpg.md`(회전 절에 「쌍 Secret」 항목)

**Interfaces:**
- Consumes: Task 2의 롤 이름 `airflow`·Secret 이름 `airflow-pg-app`.
- Produces: `ensure_secret <ns> <name> <kubectl create secret generic args...>`(첫 인자가 네임스페이스로 바뀐다 — 기존
  호출 3곳 `default` 추가),
  `ensure_secret_pair <ns1> <name1> <ns2> <name2>` 판정 함수(둘 다 있음 → 0 / 둘 다 없음 → 1 / 반쪽 → 메시지 후 exit 1).

| Secret | ns | 키 | 값 |
|---|---|---|---|
| `airflow-pg-app` | default | `username`·`password`(basic-auth) | `airflow` / 랜덤 PG_PW |
| `airflow-metadata` | airflow | `connection` | `postgresql+psycopg2://airflow:<PG_PW>@catalog-postgres-rw.default.svc:5432/airflow` |
| `airflow-redis-password` | airflow | `password` | 랜덤 REDIS_PW |
| `airflow-broker-url` | airflow | `connection` | `redis://:<REDIS_PW>@airflow-redis:6379/0` |
| `airflow-fernet-key` | airflow | `fernet-key` | Fernet 키(urlsafe base64 32바이트) |
| `airflow-jwt-secret` | airflow | `jwt-secret` | 랜덤 |
| `airflow-api-secret` | airflow | `api-secret-key` | 랜덤 |
| `airflow-admin` | airflow | `password` | 랜덤(UI admin 로그인용) |

랜덤은 `openssl rand -hex 24`, Fernet은 `openssl rand -base64 32 | tr '+/' '-_'`. 값은 출력하지 않는다.
`airflow` 네임스페이스는 `kubectl create namespace airflow --dry-run=client -o yaml | kubectl apply -f -`로 먼저 만든다.
헤더 주석의 「같은 비밀번호를 두 시크릿에 중복 보관하지 않는다」에 예외를 적는다 — CNPG `passwordSecret`은
Cluster 네임스페이스에, 차트 `connection`은 앱 네임스페이스에 있어야 해 **교차 참조가 불가능**하므로 쌍으로 두고 반쪽을
거부한다.

- [ ] **Step 1: 반쪽 거부 프로브를 스텁으로 먼저 만든다**(클러스터 없음) — scratchpad에 가짜 `kubectl`
  (`get secret <name>`이 목록 파일에 있으면 exit 0, 없으면 exit 1, `create`는 목록에 추가)을 두고 PATH 앞에 넣는다.
  `require_cluster_context`는 스텁의 `config current-context`가 `kind-lakehouse`를 내게 해 통과시킨다.
  세 시나리오의 기대: 둘 다 있음 → 생성 0 / 둘 다 없음 → 둘 다 생성 / `airflow-redis-password`만 있음 →
  `반쪽 Secret 쌍: airflow-redis-password 있음 / airflow-broker-url 없음` 후 exit 1. 구현 전에는 반쪽 시나리오가 exit 0
  (= 결함)임을 확인한다. 실클러스터 확인은 Task 7 Step 1.

- [ ] **Step 2: 구현** — `ensure_secret`에 ns 인자, `ensure_secret_pair`, 8종 생성 블록(「3) Airflow」 절).
- [ ] **Step 3: 정적 검사** — `shellcheck scripts/k8s-secrets.sh` 0건, `bash -n` 통과.
- [ ] **Step 4: 프로브 실행** — Step 1의 스텁으로 셋 판정(둘 다 있음 → 생성 안 함 / 둘 다 없음 → 둘 다 생성 / 반쪽 →
      exit 1) 확인.
- [ ] **Step 5: `cnpg.md` 회전 절에 쌍 두 개(PG·Redis)와 「한 벌 회전」 규칙 추가.**
- [ ] **Step 6: Commit** — `feat(scripts): Airflow Secret을 쌍 단위로 만들고 반쪽을 거부한다`

### Task 4: Airflow umbrella chart

**Files:**
- Create: `gitops/charts/airflow/Chart.yaml`, `gitops/charts/airflow/values.yaml`,
  `gitops/charts/airflow/tests/expect.yaml`
- Create(생성물 커밋): `gitops/charts/airflow/Chart.lock`(`helm dependency build`로 생성, `charts/`는 이미 gitignore)

**Interfaces:**
- Consumes: Task 1의 `absent`·`no_hook_kinds`, Task 3의 Secret 이름·키.
- Produces: 렌더 리소스 `StatefulSet/airflow-worker`, `Job/airflow-run-airflow-migrations`, `Job/airflow-create-user`,
  `Ingress/airflow-api-server`(정확한 이름은 Step 2 렌더에서 확정해 expect에 쓴다).

values 결정(키는 모두 `airflow:` 아래, 출처 `argocd-study/gitops/charts/airflow/values.yaml`에서 이식·수정):
- `images.airflow.repository`/`tag: "v0.2.0"  # bump-image-tag`
- `executor`, `fernetKeySecretName: airflow-fernet-key`, `jwtSecretName: airflow-jwt-secret`,
  `apiSecretKeySecretName: airflow-api-secret`
- `data.metadataSecretName: airflow-metadata`, `data.brokerUrlSecretName: airflow-broker-url`,
  `redis.passwordSecretName: airflow-redis-password`
- `postgresql.enabled: false`, `redis.enabled: true`(+resources), `triggerer.enabled: false`,
  `statsd`·`flower`·`pgbouncer`·`webserver.enabled: false`
- `workers.celery.replicas: 1`(키가 deprecated 경고를 내면 차트가 권하는 키로), worker·log groomer resources,
  `persistence.storageClassName: standard`
- `migrateDatabaseJob`/`createUserJob`: `useHelmHooks: false`, `applyCustomEnv: false`, `jobAnnotations` Sync / PostSync
- `createUserJob.env`: `AIRFLOW_ADMIN_PASSWORD` ← `secretKeyRef airflow-admin/password`, `args`의 `-p` 값을
  `$(AIRFLOW_ADMIN_PASSWORD)`로 교체(나머지 args는 차트 기본 유지)
- `config.database.sql_alchemy_pool_size: 2`, `sql_alchemy_max_overflow: 3`
- `ingress.apiServer`: enabled, host, `ingressClassName: nginx`
- resources: apiServer·scheduler·dagProcessor `requests 250m/512Mi limits 500m/1Gi`, worker 동일, redis
  `100m/128Mi · 250m/256Mi`, sidecar·Job은 argocd-study 값

- [ ] **Step 1: 단언을 먼저 쓴다**(`tests/expect.yaml`):
  ```yaml
  no_hook_kinds: [Job]   # Secret은 렌더 0개가 기대값이라 absent가 맡는다
  absent:
    - Secret/airflow-fernet-key
    - Secret/airflow-jwt-secret
    - Secret/airflow-api-secret
    - Secret/airflow-metadata
    - Secret/airflow-broker-url
    - Secret/airflow-redis-password
    - Deployment/airflow-triggerer
    - StatefulSet/airflow-triggerer
    - StatefulSet/airflow-postgresql
  fields:
    - {ref: Job/airflow-run-airflow-migrations, path: [metadata, annotations, argocd.argoproj.io/hook], value: Sync}
    - {ref: StatefulSet/airflow-worker, path: [spec, replicas], value: 1}
  ```
  executor 단언(Step 3에서 추가): ConfigMap의 `airflow.cfg` 문자열 포함 검사는 `fields`로 표현 못 하므로, 차트가
  executor를 넣는
  `env`(스케줄러 컨테이너 `AIRFLOW__CORE__EXECUTOR`) 경로가 있으면 그 `fields`로, 없으면 Step 3에서 렌더를 보고
  `fields`로 잡을 수 있는 경로를 고른다(없으면 이 단언은 선언된 공백으로 expect 주석에 남긴다).

- [ ] **Step 2: 실패 확인** — `scripts/gitops-charts-check.sh gitops/charts/airflow` → `Chart.yaml 이 없다`.
- [ ] **Step 3: `Chart.yaml`·`values.yaml` 작성 → `scripts/helm-dep-build.sh gitops/charts/airflow` → 렌더로 리소스 이름
      확정 후 expect 보정.**
- [ ] **Step 4: 통과 확인** — 같은 명령 exit 0. (a)가 컨테이너 resources 누락을 내면 해당 컴포넌트 values에 resources를
      채운다(검사 완화 금지).
- [ ] **Step 5: 위반 프로브 2종** — ⓐ `migrateDatabaseJob.useHelmHooks: false` 삭제 → `no_hook_kinds` 실패
  ⓑ `fernetKeySecretName` 삭제 → `absent Secret/airflow-fernet-key` 실패. 각각 확인 후 원복.
- [ ] **Step 6: 렌더 결정성** — `helm template airflow gitops/charts/airflow -n airflow`를 두 번 떠 `diff` → 출력 0줄.
- [ ] **Step 7: Commit** — `feat(gitops): Airflow umbrella chart를 추가한다`

### Task 5: hello DAG에 K8s 경로 태스크

**Files:**
- Modify: `images/airflow/dags/hello.py`
- Modify: `images/airflow/tests/dag-import.test.sh`

**Interfaces:**
- Produces: DAG `hello`의 태스크 `say_hello`(executor 미지정 → 기본 Celery),
  `say_hello_k8s`(`executor="KubernetesExecutor"`), 의존 `say_hello >> say_hello_k8s`.

- [ ] **Step 1: 테스트 먼저** — `CHECK_PY`가 `{"tasks": {task_id: executor}}`도 출력하게 하고 단언 추가:
  `jq -e '.tasks == {"say_hello": null, "say_hello_k8s": "KubernetesExecutor"}'` → `PASS [executors]`.
- [ ] **Step 2: 실패 확인** — `images/airflow/tests/dag-import.test.sh` → `FAIL [executors]`.
- [ ] **Step 3: 구현** — 태스크 추가(로그 메시지에 경로 이름 포함, docstring 「단일 태스크」 문구 갱신).
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS 3개, exit 0.
- [ ] **Step 5: Commit** — `feat(airflow): hello DAG에 KubernetesExecutor 경로 태스크를 더한다`

### Task 6: ApplicationSet 등록과 문서

**Files:**
- Modify: `terraform/platform/variables.tf`(`var.apps` default 끝에 airflow 원소 + 한 줄 주석)
- Modify: `docs/argocd-gitops.md`(§3 트리 `airflow/` 줄, §8 PR3-B 행 「별도 설계」→ 스펙 링크)
- Modify: `docs/setup.md`(UI URL 표에 `airflow.localtest.me`, Secret 선행 실행 순서)
- Modify: `docs/resource-sizing.md`(§B Airflow 행 — 값은 values의 requests)
- Modify: `docs/conventions/k8s.md`(§9-3 경계 ④를 Airflow 컴포넌트로)
- Modify: `CLAUDE.md`(「메타 Postgres는 두지 않는다」 문장을 같은 줄에서 「Airflow 메타는 CNPG의 별도 롤·DB」로 — 줄 수
  불변)
- Modify: 스펙 §4 — UI 계정을 `airflow-admin` Secret + args 주입으로 확정(Secret 8종)

- [ ] **Step 1:** `terraform -chdir=terraform/platform fmt -check` 및 `terraform -chdir=terraform/platform validate` →
      통과.
- [ ] **Step 2:** `terraform -chdir=terraform/platform plan` → 변경이 **`helm_release.appset` in-place 1건**뿐인지
      확인(다른 리소스 변경이 보이면 멈춘다). plan은 읽기만 한다.
- [ ] **Step 3:** 문서 수정 후 `pre-commit run --files <수정 문서들>` → doc-lint·링크 검사 통과.
- [ ] **Step 4: Commit** — `feat(platform): ApplicationSet에 airflow 앱을 등록한다` /
      `docs: Airflow 배포 절차·예산을 반영한다`

### Task 7: 배포·E2E 검증 (🔴 비가역 — 게이트 2단)

**선행:** Task 1~6 PR 리뷰 완료.

- [ ] **Step 0: 클러스터 기동** — `docs/setup.md` 절차 그대로(`scripts/k8s-up.sh` → 스택 A `terraform apply` →
  platform `terraform apply`, 기존 머신 재사용) → `kubectl -n argocd get applications` 기존 6개 Synced/Healthy.
 `reviewer`에게 보안 체크리스트(Secret 생성·외부 발행·apply) 1회 → 사용자 승인.

- [ ] **Step 1: Secret** — 먼저 실클러스터 반쪽 프로브(`airflow` ns에 `airflow-redis-password`만 수동 생성 →
  스크립트 exit 1 확인 → 그 Secret 삭제).
  그다음 `scripts/k8s-secrets.sh`(PR 브랜치에서) → `kubectl -n airflow get secret` 7개,
      `-n default get secret airflow-pg-app` 1개.
- [ ] **Step 2: PR 머지** → `catalog-postgres` 앱 자동 sync →
      `kubectl -n default get database airflow -o jsonpath='{.status.applied}'` = `true`.
- [ ] **Step 3: 이미지 발행** — 머지 커밋에 `airflow-v0.2.0` 태그 push → `airflow-image` 워크플로 성공 →
      `podman pull ghcr.io/melting-face/pipeline-study-airflow:v0.2.0` 인증 없이 성공.
      ⚠️ **대체됨**: 태그 push 트리거는 걷어냈다 — 발행은 `main` 머지가 부르고 버전은 values 태그다
      ([`docs/conventions/docker.md`](../../conventions/docker.md) §3). v0.2.0은 그 전환 PR의 머지가 발행한다.
- [ ] **Step 4: 등록** — `terraform -chdir=terraform/platform apply`(main에서) →
      `kubectl -n argocd get application airflow` Synced/Healthy.
- [ ] **Step 5: 결정성** — ArgoCD에서 수동 Sync 1회 후 5분 뒤에도 Synced(자동 재sync 루프 없음).
- [ ] **Step 6: UI** — `http://argocd.localtest.me:8080`에서 airflow 앱 트리 확인, `http://airflow.localtest.me:8080`에
      admin/`airflow-admin` 값으로 로그인.
- [ ] **Step 7: DAG** — `hello` 트리거 → `say_hello`(worker 로그에 KST 시각), `say_hello_k8s`(`kubectl -n airflow get
      pods -w`에서 task pod 생성→Completed→삭제) 둘 다 success.
- [ ] **Step 8: 실측(볼트에 기록)** — `pg_stat_activity` DB별 연결 수, `kubectl top pods -A`·requests 합계를 스펙 §6과
      대조.
- [ ] **Step 9: 후속 Issue** — 「K8s 태스크 로그 원격 저장(SeaweedFS)」 한 줄(재검토 조건 포함).
