# Docker · Compose · Dockerfile 규칙

> **목적**: 컨테이너 빌드·구성 규칙(재현성·자원 관리).
> **언제 읽나**: `compose.yml`·`Dockerfile` 수정, 이미지 버전·healthcheck·리소스 한도 작업 시.
> **연관**: 자원 **수치** 산정은 [resource-sizing.md](../resource-sizing.md), 환경변수 주입은 [../operations.md](../operations.md).

`data-pipeline` 레포에서 이식. 이 레포 `compose.yml`은 대부분 이미 준수하며, 아래는 그 규칙을 명문화한 것이다.

> ⚠️ **용어 주의**: 여기서 말하는 "리소스 제한"은 **컨테이너 자원**(`deploy.resources`)이다.
> Dagster **Resource**(코드: `S3Resource`·IO 매니저 등)와 혼동하지 않는다 —
> 후자는 [conventions/dagster.md](dagster.md).

## 1. Compose

### 1-1. 로깅은 json-file 드라이버 + YAML 앵커로 통일

```yaml
x-docker-logging: &docker-logging
  logging:
    driver: "json-file"
    options:
      max-size: "10m"    # 파일당 10MB
      max-file: "20"     # 최대 20개 → 컨테이너당 최대 200MB
```

모든 서비스에 `<<: *docker-logging`으로 적용한다. (보존 정책은 [../operations.md](../operations.md) §2)

> ⚠️ **podman의 json-file 드라이버는 `max-file`을 적용하지 않는다** — 선언은 Docker와의 호환을 위해 남긴다.
> compose 밖 컨테이너(스택 A `docker_container.seaweedfs`)도 같은 값을 `log_opts`로 선언하지만, podman이
> 그 값을 되읽어 주지 않아 매 `plan`이 교체로 나오므로 `ignore_changes`로 둔다.

### 1-2. 환경변수·공통부는 YAML 앵커로 중복 제거 (DRY)

동일 이미지·설정을 공유하는 서비스는 앵커로 공통부를 모은다.

```yaml
# webserver·daemon 공통부(빌드·env·볼륨·depends_on)를 한곳에
x-dagster-common: &dagster-common
  build: { context: dagster/dockerfile.d/ }
  environment:
    - POSTGRES_USER=${POSTGRES_USER}
    # ...
  depends_on:
    postgres: { condition: service_healthy }

services:
  dagster-webserver:
    <<: [*docker-logging, *dagster-common]   # 앵커 병합
```

- 새 환경변수를 이 두 서비스에 넣을 땐 **앵커에 한 번만** 추가한다([../operations.md](../operations.md) §1-1).

### 1-3. 이미지 버전은 반드시 명시 (`latest` 금지)

`latest`는 빌드 시점에 따라 이미지가 달라져 **재현 불가능한 환경**을 만든다.
공식 이미지는 구체 버전 태그를 쓰고, 커스텀 빌드는 `ARG`로 버전을 분리한다.

```yaml
# Bad
image: trinodb/trino:latest

# Good — 현재 레포
image: postgres:15
image: prom/prometheus:v2.21.0
```

```dockerfile
# 커스텀 빌드 — ARG로 분리하면 빌드 시 오버라이드 가능
ARG TRINO_VERSION=477
FROM trinodb/trino:${TRINO_VERSION}
```

- Trino처럼 주 단위 릴리즈를 하는 이미지는 **LTS 버전**을 우선한다(비-LTS는 다음 릴리즈 후 패치 중단).
  compose Trino는 철거됐고, k8s Trino 태그는 `gitops/apps/trino/values.yaml`이 고정한다.
- **SeaweedFS(`chrislusf/seaweedfs:4.36`)는 낮추지 않는다.** `./seaweedfs/data`는 **4.x가 쓴 온디스크
  상태**이고(`.mini_kek_passphrase`·`.mini_sse_kek`·`mini.options`·`admin/` = 4.x 산물) 이것이 오브젝트
  스토리지 **정본**이라, 더 낮은 버전이 인플레이스로 여는 것이 위험하다. 종전에는 K8s 사본(`3.80`)과의
  버전 분기를 예외로 설명했으나 K8s 사본이 철거돼 분기 자체가 없다.
- 🔴 **다만 「낮추지 않는다」를 「고정하지 않는다」로 지키지 않는다** — 무태그는 재현성만이 아니라
  **공급망 축**이다. 같은 이름이 다음 `pull`에서 **다른 바이너리**를 가리키고 그 변경이 **조용하며**,
  디스크 포맷을 또 올릴 수 있다(예외가 막으려던 바로 그 위험이 통제 없이 들어온다).
  실제로 업스트림 `:latest`는 **이미 `:4.36`과 다른 이미지 인덱스**다(실측). ⇒ 규칙은
  **「현재 버전 이상으로 상향 고정」** 이다. 값은 **로컬 이미지가 실제로 보고하는 버전**이어야 하고,
  **그 태그가 업스트림에 실재하는지 먼저 확인**한다(실재를 확인하지 않은 태그를 박으면 남의
  환경에서 `pull`이 깨진다). 어느 경우에도 **낮추는 방향으로는 맞추지 않는다.**
- 🔴 **digest 핀으로 대체하지 않는다** — 로컬에서 뜨는 digest는 보통 **단일 아키텍처 매니페스트**라
  (이 이미지의 경우 arm64) 그대로 박으면 amd64 호스트에서 깨진다. 멀티아키를 유지하려면
  **태그 또는 이미지 인덱스 digest**를 쓴다.

### 1-4. Healthcheck + `depends_on` 조건 명시

기동 순서를 헬스체크로 강제해 초기화 경쟁(race)을 막는다.

```yaml
services:
  postgres:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 8s
      retries: 5
  dagster-daemon:
    depends_on:
      postgres: { condition: service_healthy }   # DB가 준비된 뒤 기동
```

### 1-5. 모든 서비스에 `deploy.resources` 명시

호스트 리소스 초과로 인한 **OOM·CPU 경합**을 방지하기 위해 모든 서비스에 CPU·메모리 한도를 명시한다.
Compose v2는 Swarm 없이 단독 실행에서도 `deploy.resources`를 적용한다.

```yaml
services:
  my-service:
    deploy:
      resources:
        limits:        # 상한(하드)
          cpus: "1.0"
          memory: 1g
        reservations:  # 예약(소프트, 보장 최소)
          cpus: "0.5"
          memory: 512m
```

- **배분 원칙·서비스별 수치·daemon 메모리 계산식**은 [resource-sizing.md](../resource-sizing.md)에서 단일 관리한다.
- 원칙 요약: `limits.memory` 합 ≤ 호스트 RAM − OS 여유(1~2g).
  메모리 최다 소비는 `dagster-daemon`(run 서브프로세스 fork)이다.
- `max_concurrent_runs`(`dagster.yaml`)와 daemon `memory`는 **강하게 결합** — 한쪽만 바꾸면
  OOM 또는 낭비. 반드시 함께 조정한다([resource-sizing.md](../resource-sizing.md)).

### 1-6. 옵션 기능은 `profiles`로 분리 (뼈대 = 항상, 부가기능 = opt-in)

핵심 서비스(뼈대)와 부가기능(모니터링 등)을 **한 파일**에서 관리하되, 부가기능은
`profiles`로 태그해 기본 `up`에서 제외한다(적은 파일로 파악 — 파일 분할 없이 토글).

```yaml
services:
  postgres: {}                   # 뼈대: profile 없음 → 항상 실행
  prometheus:
    profiles: ["monitoring"]     # 옵션: --profile monitoring 일 때만 기동
```

- **뼈대(core)**: `dagster-webserver`·`dagster-daemon`·`postgres` — profile 없음.
- **옵션**: `prometheus`(`monitoring`).
- **compose 밖**: 오브젝트 스토리지 정본 `seaweedfs`는 Terraform 스택 A(`docker_container.seaweedfs`)가 소유한다.
  compose 서비스는 `host.containers.internal:<게시 포트>`로 닿고(podman 전제 — Docker Desktop은
  `host.docker.internal`), compose `depends_on`으로 표현할 수 없어 **스택 A를 먼저 apply하는 것은 운영자 몫**이다.

```bash
docker compose up -d                        # 뼈대만
docker compose --profile monitoring up -d   # 뼈대 + 모니터링
COMPOSE_PROFILES=monitoring docker compose up -d   # 프로필 고정
```

> **`profiles`는 "제거 예정"의 중간 단계로도 쓴다** — **"중단"과 "삭제"를 분리**하면 자원은 즉시
> 회수되면서 롤백 비용이 0으로 유지된다. compose `trino`(`legacy-sql`)가 그 사례였고 철거됐다
> ([../architectures/trino.md](../architectures/trino.md)).
> `seaweedfs`는 이 처리에서 **되돌아왔다가 compose를 떠났다** — 정본을 K8s로 옮겨 중단했었고, 클러스터 밖
> 외부 스토리지로 compose에서 다시 정본이 된 뒤, 지금은 Terraform 스택 A가 컨테이너를 소유한다
> ([../architectures/storage.md](../architectures/storage.md)). 그래서 profile `storage`는 없다.

> `profiles`를 붙인 서비스를 **의존**(`depends_on`)하는 뼈대 서비스가 없어야 한다(있으면 기본 기동이 깨진다).
> 옵션↔옵션 의존은 같은 프로필을 공유하거나 함께 활성화한다. 대안인 다중 파일 `-f` override는
> YAML 앵커가 파일 스코프라 기능 파일에서 공용 앵커를 못 써 이 레포에선 profiles를 택했다.
>
> 🔴 **의존받는 서비스는 의존하는 쪽의 profile을 전부 물려받아야 한다.** 빠뜨리면 의존하는 쪽의
> profile만 켰을 때 **의존 비활성으로 깨진다**. 현행 사례는 `postgres`다 — `dagster-*`(`host-dagster`)가
> 의존하므로 자기 profile `legacy-meta`에 그것을 더해 profile이 2개다(철거된 `trino`의 `legacy-sql`까지 3개였다).
> (옛 compose `seaweedfs`도 `trino`·`prometheus`의 의존으로 3개였으나 스택 A로 옮겨 사례에서 빠졌다.)
> 바꾼 뒤에는 **profile별로 `docker compose --profile <p> config --services`를 돌려 확인**한다
> — 이 검증은 기동 없이 수초면 끝난다.

## 2. Dockerfile

- 컨테이너는 **비루트 사용자**로 실행한다. `USER 1000` 전환 **전에** 소유권을 넘긴다:
  `chown -R 1000:1000 $DAGSTER_HOME`.
- pip 설치는 `--no-cache-dir --compile --prefer-binary`로 이미지 크기·빌드 시간을 줄인다.
- `hadolint`(`.hadolint.yaml`)로 Dockerfile을 린트한다([general.md](general.md)).

- **extras와 dependency-group을 섞지 않는다.** `-e ".[dev]"`는 `[project.optional-dependencies]`에
  `dev`가 있을 때만 유효하다. PEP 735 `[dependency-groups]`의 그룹은 extras가 아니라서 pip이
  **에러 없이 `WARNING: … does not provide the extra 'dev'`만 찍고 통과**한다 — 설치된 줄 알지만
  아무것도 안 깔린 상태가 된다(빌드 로그에서 실재 확인). 런타임 이미지에는 애초에
  dev 도구를 넣지 않으므로 **`-e .`이 정답**이다.

```dockerfile
RUN pip install --no-cache-dir --compile --prefer-binary -e . \
    && chown -R 1000:1000 /opt/dagster/dagster_home

USER 1000
```

## 3. 이미지 발행 (GHCR)

Airflow 커스텀 이미지(`images/airflow/` — 공식 base + DAG)는 **CI가 GHCR에 발행**한다.
Spark·Flink 러너 이미지는 이 경로가 아니라 로컬 레지스트리 `localhost:5001`에 사람이 push한다([k8s.md](k8s.md)).

- **버전의 단일 출처는 `gitops/charts/airflow/values.yaml`의 `# bump-image-tag` 한 줄**이다.
  ArgoCD는 그 태그를 당겨 가고 `.github/workflows/airflow-image.yml`은 같은 태그를 발행한다.
  추출은 `scripts/airflow-image-version.sh`(마커 0·2개·형식 위반은 실패).
  이미지 이름은 `ghcr.io/melting-face/pipeline-study-airflow:vX.Y.Z`다.
- **트리거는 `main` 머지**다 — 이미지·values·워크플로·발행 스크립트(`scripts/airflow-image-version.sh`·
  `scripts/ghcr-tag-exists.sh`)가 바뀐 커밋이 `main`에 들어오면 DAG import 테스트 후 그 태그가 GHCR에
  **없을 때만** 빌드·push한다. 있으면 건너뛰되, 그 push가 이미지 내용을 바꿨으면 **실패**한다(조용한 미발행 방지).
  태그 존재를 판정하지 못하면(레지스트리 오류)도 실패한다. git 태그 push 단계는 없다.
- **PR 검사**: 이미지 내용(`images/airflow`에서 `tests/` 제외)이 바뀐 PR은 values 태그를 올려야 하고,
  그 태그는 아직 발행 전이어야 한다. 필수 체크가 아니라 권고 수준이고, 머지 뒤 발행 단계가 같은 조건을 다시 본다.
- **태그는 불변이다** — 덮어쓰지 않는다. 롤백은 **values 태그만** 이전 값으로 되돌리는 PR이다
  (이미지 내용 변경을 섞으면 PR 검사에 걸린다). 잘못 발행한 태그는 다음 패치 버전으로 전진한다.
- **발행 전 검증**: `bash images/airflow/tests/dag-import.test.sh`(arm64 빌드 → DagBag 파싱 —
  import 오류 0건·DAG별 태스크·executor·의존, `github_trending`은 cron·해석 tz·Asset outlet까지 —
  executor 목록은 values에서 읽는다). **DAG를 추가하면 판정 셀도 함께 추가**한다(오류 0건만으로는 모양을 못 본다).
  로컬은 podman, CI는 `CONTAINER_CLI=docker`로 같은 스크립트를 돈다. DB 없이 파싱만 하므로 클러스터가 필요 없다.
- **DAG의 외부 연결은 이미지가 아니라 차트 env로 주입한다** — `values.yaml`의 `secret:`(→ `AIRFLOW_CONN_<ID>`)·`env:`가
  모든 Airflow 컨테이너와 K8s 태스크 파드 템플릿에 들어간다. Secret 값은 `scripts/k8s-secrets.sh`가 만든다.
  🔴 `secretKeyRef`는 필수 참조라 **Secret이 sync보다 먼저** 있어야 한다(없으면 파드가 `CreateContainerConfigError`).
  S3 연결 `seaweedfs_s3`(Secret `airflow-conn-seaweedfs`)의 키는 `default/lakehouse-creds`와 **한 벌**이다 —
  Secret은 네임스페이스를 넘어 참조할 수 없어 사본이 생기므로 **회전 시 둘을 함께** 바꾼다.
  공식 base 이미지에 amazon provider·`requests`가 이미 있어 `Dockerfile`에 pip 설치를 두지 않는다.
- **발행은 곧 공개다** — 이 패키지는 public 저장소에 연결돼 **처음 생길 때부터 public**이었다(실측).
  「private으로 올리고 확인한 뒤 공개」하는 단계가 없으므로, 공개 전 게이트는 **발행 전**에 있다:
  위 로컬 검증과 `images/airflow/` 변경의 PR 리뷰. 빌드 컨텍스트에 비밀·데이터가 들어갈 경로를 만들지 않는다
  (`.dockerignore`, `COPY`는 `dags/`만).
- **발행 후 확인**: 인증 없이 pull되는지 본다 — `podman logout ghcr.io` 후
  `podman pull --platform linux/arm64 ghcr.io/melting-face/pipeline-study-airflow:<태그>`.
- 이미지는 `linux/arm64` 단일 아키텍처다(현행 검증 환경이 arm64 호스트 위 kind).

## 참고

- Docker Compose — `deploy.resources`: https://docs.docker.com/reference/compose-file/deploy/#resources
- Docker Compose — extension fields(YAML 앵커): https://docs.docker.com/reference/compose-file/fragments/
- Docker — logging drivers(json-file): https://docs.docker.com/config/containers/logging/json-file/
- Trino — release types(LTS): https://trino.io/docs/current/release.html
- hadolint: https://github.com/hadolint/hadolint
- GitHub — Working with the Container registry: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
