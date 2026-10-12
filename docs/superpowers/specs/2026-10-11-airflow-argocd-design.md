# Airflow on ArgoCD(PR3-B) — 설계

> 구현 계획은 이 설계를 입력으로 별도 작성한다. 관련 설계: [`argocd-gitops.md`](../../argocd-gitops.md)
> (D2 ③단계·D7·§8 PR3-B), [외부 스토리지 설계](2026-10-10-external-storage-design.md)("Airflow 메타 DB는 PR3 소관").

## Context

- **현재**: `argocd-gitops.md` D2의 ①오퍼레이터·②데이터 저장소는 수렴했고, ③Airflow의 PR3-B는
  「별도 설계」로 비어 있다. PR3-A(이미지 `images/airflow/` + GHCR 발행)는 끝났다. 클러스터 안에서 도는
  오케스트레이터는 없다(D6 — Dagster는 배포하지 않는다).
- **목적(사용자 결정)**: Airflow를 클러스터에 띄우고 **ArgoCD UI(앱 트리·Sync 상태)** 와 **Airflow UI**를
  함께 브라우저로 본다. 범위는 **UI 기동까지** — 데이터셋 DAG 이관·Cosmos·레이크하우스 접속·원격 로깅은
  Dagster→Airflow 이관 계획의 이후 단계 몫이다.
- **기존 결정과의 관계**:
  - IaC — 이관 계획의 「Terraform `helm_release`」는 GitOps 설계(D2)로 **ArgoCD 차트로 대체**됐다.
  - Executor — 이관 계획의 「KubernetesExecutor 단독」을 **`CeleryExecutor,KubernetesExecutor`로
    갱신**한다(사용자 결정).

## 결정 요약

| # | 결정 | 근거 |
|---|---|---|
| A1 | 공식 chart를 감싼 **umbrella chart** `gitops/charts/airflow/`. 자매 저장소 `argocd-study`의 같은 차트를 이식한다 | 모든 앱이 `path: gitops/charts/<앱>` 한 모양(ApplicationSet List generator). 같은 chart·ArgoCD 버전으로 기동이 확인됐다 |
| A2 | `executor: "CeleryExecutor,KubernetesExecutor"` — **Celery가 기본**(첫 자리), 태스크 단위 `executor="KubernetesExecutor"`로 라우팅 | 사용자 결정. chart `values.schema.json`의 executor 패턴이 쉼표 구분 다중 executor를 허용한다 |
| A3 | Celery worker **replicas 1**, KEDA·HPA 없음 | CPU가 급소. 동시성은 `worker_concurrency`로 조절 |
| A4 | 메타 DB는 **기존 CNPG `catalog-postgres`에 별도 롤·DB** — 차트 내장 PG는 끈다 | `cluster.yaml`의 지시("오케스트레이터 메타 DB는 `iceberg` 롤을 재사용하지 말고 롤·Secret을 따로"). PG 이중화를 피한다 |
| A5 | 랜덤 Secret 6종을 **수동 Secret으로 고정**, Git엔 이름만 | 차트는 이름을 안 주면 렌더마다 `randAlphaNum`으로 만든다 → 영구 OutOfSync·복호화 실패. D7 |
| A6 | **훅 교착 해법 이식** — Job 2종 `useHelmHooks: false`, migrate Job은 ArgoCD `Sync` 훅, createUser Job은 `PostSync` | ArgoCD가 Helm `post-install` 훅을 PostSync로 옮겨, 마이그레이션을 기다리는 파드와 교착한다(`argocd-study/wiki/airflow-on-argocd.md`) |
| A7 | **triggerer는 끈다** | hello DAG에 deferrable 태스크가 없다(YAGNI). 재활성 조건: deferrable operator를 쓰는 첫 DAG |
| A8 | UI는 `airflow.localtest.me` — 기존 ingress-nginx, `ingressClassName: nginx`, HTTP | ArgoCD UI와 같은 진입점. kind 공개 포트를 새로 열지 않는다 |

## 설계

### 1. 구성 요소 (namespace `airflow`)

| 컴포넌트 | 수명 | 비고 |
|---|---|---|
| apiServer | 상주 | Airflow 3 UI·REST. Ingress는 `ingress.apiServer`(`ingress.web`은 Airflow 2 전용 게이트) |
| scheduler | 상주 | 두 executor를 함께 구동. K8s task pod 생성 권한은 차트 RBAC(`allowPodLaunching`) |
| dagProcessor | 상주 | Airflow 3에서 필수(standalone) |
| Celery worker | 상주 1 | StatefulSet+PVC(`standard`) — 태스크 로그를 들고 있어 UI가 서빙한다 |
| redis | 상주 | Celery 브로커 |
| K8s task pod | 온디맨드 | 태스크 종료 시 회수 |
| triggerer·statsd·flower·pgbouncer·webserver·postgresql | 끈다 | A7 / 수집기 없음 / 불필요 / 레거시 / A4 |

모든 상주 컴포넌트(log groomer 사이드카 포함)에 `resources`를 values로 명시한다 — 차트 기본값에 맡기지
않는다(Flink Operator 함정의 재발 방지).

### 2. GitOps 배선

- `gitops/charts/airflow/`: `Chart.yaml`(의존성 `airflow` `1.22.0` @ `https://airflow.apache.org`),
  `Chart.lock` 커밋, `charts/*.tgz`는 gitignore, `values.yaml`은 공식 키를 최상위 `airflow:` 아래에 둔다.
- 이미지: `ghcr.io/melting-face/pipeline-study-airflow:<tag>` — 태그 줄 하나에 `# bump-image-tag` 마커.
- 등록: `terraform/platform/variables.tf`의 `var.apps`에
  `{ name = "airflow", path = "gitops/charts/airflow", namespace = "airflow" }`를 추가하고 `terraform apply`.
  syncPolicy는 ApplicationSet 공통값(selfHeal·prune·ServerSideApply·retry)을 그대로 받는다.
- `tests/expect.yaml` 단언은 `scripts/gitops-charts-check.sh`의 검사 (c)가 읽는다.
  아래 셋은 **이 설계의 핵심 가정을 직접 겨눈다**:
  1. `helm.sh/hook` 어노테이션이 붙은 Job이 0개(A6)
  2. migrate Job에 `argocd.argoproj.io/hook: Sync`(A6)
  3. 차트가 렌더하는 Secret이 0개(A5 — fernet·jwt·api·redis·broker·metadata)

  기존 검사기가 표현하지 못하는 단언이 있으면 검사기 확장은 구현 계획에서 정한다.

### 3. 메타 DB (A4)

- `gitops/charts/catalog-postgres/templates/cluster.yaml`의 `managed.roles`에 `airflow` 추가 —
  웹훅 기본값(`connectionLimit: -1`, `inherit: true`)을 명시하고 `passwordSecret: airflow-pg-app`.
- 같은 차트에 CNPG `Database` CR `airflow`(owner `airflow`) 추가, `Prune=false,Delete=false`.
- `catalog-postgres/tests/expect.yaml`: `roles[1] = null` → `roles[1].name = airflow` + `roles[2] = null`.
- 접속 대상 `catalog-postgres-rw.default.svc:5432/airflow` — 🔴 `-rw` 접미사 없는 서비스명은 없다.
- ⚠️ `max_connections`를 카탈로그와 공유한다. 프로세스마다 SQLAlchemy 풀을 작게 묶는다
  (`AIRFLOW__DATABASE__SQL_ALCHEMY_POOL_SIZE`·`MAX_OVERFLOW` — 값은 구현 계획에서 정하고 검증에서 실측).

### 4. Secret (A5·D7)

`scripts/k8s-secrets.sh`에 `ensure_secret`(get-or-create) 항목을 더한다. `airflow` 네임스페이스도
이 스크립트가 먼저 만든다 — Secret이 Application보다 먼저 있어야 파드가 `CreateContainerConfigError`로
멈추지 않는다.

| Secret | ns | 키 | 참조하는 values |
|---|---|---|---|
| `airflow-pg-app` | default | `username`·`password`(basic-auth) | CNPG `passwordSecret` |
| `airflow-metadata` | airflow | `connection` = `postgresql+psycopg2://airflow:<pw>@catalog-postgres-rw.default.svc:5432/airflow` | `data.metadataSecretName`(result backend도 폴백) |
| `airflow-fernet-key` | airflow | `fernet-key` | `fernetKeySecretName` |
| `airflow-jwt-secret` | airflow | `jwt-secret` | `jwtSecretName` |
| `airflow-api-secret` | airflow | `api-secret-key` | `apiSecretKeySecretName` |
| `airflow-redis-password` | airflow | `password` | `redis.passwordSecretName` |
| `airflow-broker-url` | airflow | `connection` = `redis://:<pw>@airflow-redis:6379/0` | `data.brokerUrlSecretName` |
| `airflow-admin` | airflow | `password`(사용자명은 `admin`) | `createUserJob`의 `args`·`env`(`$(AIRFLOW_ADMIN_PASSWORD)`) |

Secret은 모두 **8종**(ns `airflow` 7 + `default`의 `airflow-pg-app` 1)이다.

🔴 **비밀번호 하나가 Secret 두 벌에 산다** — PG(`airflow-pg-app` ↔ `airflow-metadata`),
Redis(`airflow-redis-password` ↔ `airflow-broker-url`). 스크립트는 한 값으로 **두 벌을 한 번에** 만들고,
한쪽만 있으면 만들지 않고 실패한다(get-or-create가 반쪽을 새 값으로 채우면 쌍이 어긋난다).
회전 규칙은 `docs/conventions/k8s/cnpg.md`의 회전 절에 쌍으로 적는다.

UI 계정은 `createUserJob`의 admin을 쓰되 비밀번호를 values에 두지 않는다. 차트에 Secret 참조 키가 없어
`airflow-admin` Secret을 만들고 `createUserJob`의 `args`·`env`로 주입한다(확정 — 기본값 대신 Secret).

### 5. DAG — 두 경로 검증

- `images/airflow/dags/hello.py`에 `@task(executor="KubernetesExecutor")` 태스크 `say_hello_k8s` 추가.
  기존 `say_hello`는 기본(Celery) 경로로 둔다.
- `images/airflow/tests/dag-import.test.sh`가 태스크 2개와 각 executor 속성을 단언한다.
- 새 이미지는 PR3-A 경로(`airflow-vX.Y.Z` 태그 push → GHCR 발행) 후 values 태그를 bump한다.

### 6. 예산

- 상주 요청 = apiServer + scheduler + dagProcessor + worker + redis + 사이드카 3. 값은 values의 `requests`가 정본이고
  `docs/resource-sizing.md` §B에 Airflow 행으로 옮긴다.
- `docs/conventions/k8s.md` §9-3 경계 ④(오케스트레이터 상주)의 서술을 Dagster에서 Airflow 컴포넌트로 고쳐 쓴다.
  동시 기동 최대 시나리오(Connect+executor, Flink JM, K8s task pod 1)가 Allocatable 안인지는 **실측으로** 판정한다.

### 7. 관측 경로 선언

| 경로 | 결정 |
|---|---|
| probe | 둔다 — 차트 기본 liveness·readiness |
| 로그 | Celery 태스크는 worker PVC → UI. **K8s 태스크 로그는 파드 회수와 함께 사라진다 — 선언된 공백**. 후속 Issue(SeaweedFS 원격 로깅), 재검토 조건: K8s 경로를 실데이터 DAG가 처음 쓸 때 |
| 메트릭 | 안 둔다 — statsd off, 수집기 없음 |

### 8. 문서 갱신

- `docs/argocd-gitops.md` §3 트리·§8 PR3-B 행, `docs/setup.md`(UI URL·Secret 선행 절차)
- `docs/conventions/k8s/cnpg.md`(Airflow 롤·회전 쌍), `docs/resource-sizing.md`, `docs/conventions/k8s.md` §9-3
- `CLAUDE.md` — 「메타 Postgres는 두지 않는다」를 「Airflow 메타는 CNPG의 별도 롤·DB」로 **같은 줄에서** 바꾼다
- `docs/architectures/airflow.md` 신설은 이관 계획의 마지막 단계 몫이라 이번에 하지 않는다

## 비가역 단계 (게이트 2단 — `reviewer` 보안 체크리스트 + 사용자 승인)

1. `airflow-vX.Y.Z` 태그 push(외부 발행)
2. `scripts/k8s-secrets.sh` 실행(Secret 생성)
3. CNPG 롤·DB 생성(catalog-postgres 차트 머지 후 ArgoCD sync)
4. `terraform apply`(platform `var.apps`)

순서 근거: 이미지와 Secret이 앞서야 3·4의 sync가 첫 시도에 수렴한다.

## 검증

1. **정적** — `gitops-charts-check.sh` 통과, 그리고 **위반 프로브**: values에서 `useHelmHooks: false`를 지우거나
   Secret 이름 하나를 비우면 단언이 실패하는지 본다(통과만으로는 검사가 돌았다는 증거가 아니다).
2. **DAG** — `dag-import.test.sh`가 태스크 2개·executor 속성을 단언.
3. **ArgoCD** — `airflow` Application Synced/Healthy, UI 트리에 리소스 표시, **수동 재sync 후에도 OutOfSync로
   돌아가지 않음**(렌더 결정성).
4. **Airflow** — `http://airflow.localtest.me:8080` 로그인 → `hello` 트리거 → `say_hello`(Celery worker 로그에
   KST 시각)·`say_hello_k8s`(`airflow` ns에 task pod 생성→회수 관측) 둘 다 success.
5. **DB** — `pg_stat_activity`에서 DB별 연결 수가 `max_connections` 대비 여유인지.
6. **예산** — 상주 requests 합계와 `kubectl top`을 실측해 §6과 대조. 실측값은 볼트에 둔다.

## 리스크

| 리스크 | 등급 | 대응 |
|---|---|---|
| 훅 교착 재발 | 중 | A6 + expect 단언 + 위반 프로브 |
| 렌더 비결정 → 영구 OutOfSync | 중 | A5 + 재sync 관측 |
| 공유 PG 연결 고갈 | 중·미확인 | 풀 축소 + 실측 |
| 비밀번호 쌍 드리프트 | 중·비가역 | 쌍 동시 생성·반쪽이면 실패 + 회전 규칙 |
| K8s 태스크 로그 미보존 | 낮음·수용 | §7 선언 + 후속 Issue |
