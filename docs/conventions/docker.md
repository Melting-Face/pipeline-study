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
image: trinodb/trino:468
```

```dockerfile
# 커스텀 빌드 — ARG로 분리하면 빌드 시 오버라이드 가능
ARG TRINO_VERSION=477
FROM trinodb/trino:${TRINO_VERSION}
```

- Trino처럼 주 단위 릴리즈를 하는 이미지는 **LTS 버전**을 우선한다(비-LTS는 다음 릴리즈 후 패치 중단).
  현재 Trino LTS는 `477` 계열. 이 레포는 `trino:468`을 쓰므로, 업그레이드 시 LTS로 올리는 것을 권장한다.
- **예외**: compose의 `chrislusf/seaweedfs`(`compose.yml:166`)는 **고정하지 않은 채 둔다.**
  🔴 다만 근거는 *"태그 정책이 없어 고정 불가"* 가 **아니다**(교정됨 — 이전 문구는 사실이
  아니었다). 업스트림은 버전을 발급하며(로컬 이미지의 `org.opencontainers.image.version` 라벨),
  이 저장소도 **K8s 쪽은 이미 고정**했다(`k8s/seaweedfs.yaml:21` = `chrislusf/seaweedfs:3.80`).
  고정하지 않는 실제 이유는 셋이다.
  - 이 서비스는 `legacy-storage` 계열이라 **기본 `up`에 없다**(§1-6). 스토리지 정본은 K8s로 넘어갔다.
  - `./seaweedfs/data`는 **이관 전 원본 백업**이다(§1-6의 `seaweedfs` 문단). 지금 `latest`로 뜬
    버전이 이미 쓴 온디스크 상태를, **더 낮은 버전이 인플레이스로 여는 것**이 위험하다.
  - 🔴 그래서 **K8s 값(`3.80`)으로 표기를 통일하면 다운그레이드가 된다.**
    **버전 표기의 통일은 원본 백업의 무결성보다 우선하지 않는다.**
  - ⇒ K8s 정본(고정)과 compose(미고정)의 **버전 분기는 의도된 것**이다. 고정한다면 값은
    **로컬 이미지가 실제로 보고하는 버전**이어야 하며, 그 태그가 업스트림 레지스트리에
    실재하는지 먼저 확인한다. 어느 경우에도 **낮추는 방향으로는 맞추지 않는다.**

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
  trino:
    depends_on:
      postgres: { condition: service_healthy }   # DB가 준비된 뒤 기동
      seaweedfs: { condition: service_started }
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
- 원칙 요약: `limits.memory` 합 ≤ 호스트 RAM − OS 여유(1~2g). Trino(JVM MPP)가 메모리 최다 소비.
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
- **옵션**: `prometheus`(`monitoring`) · `trino`(`legacy-sql`) ·
  `seaweedfs`(`legacy-storage`+`legacy-sql`+`monitoring`).

```bash
docker compose up -d                        # 뼈대만
docker compose --profile monitoring up -d   # 뼈대 + 모니터링
COMPOSE_PROFILES=monitoring docker compose up -d   # 프로필 고정
```

> **`profiles`는 "제거 예정"의 중간 단계로도 쓴다.** `trino`는 재설계에서 제거 대상이지만
> 22모델 방언 교정이 끝날 때까지 **값 대조의 정본**이라 정의는 남긴다
> ([../architectures/trino.md](../architectures/trino.md)). 상시 기동만 끊어 자원(3 CPU / 6G)을
> 회수하고 대조할 때만 올린다 — **"중단"과 "삭제"를 분리**하면 자원은 즉시 회수되면서
> 롤백 비용이 0으로 유지된다.
> **`seaweedfs`도 같은 처리를 했다** — 오브젝트 스토리지 정본이 K8s로 이전됐고
> ([../redesign.md](../redesign.md) Phase 1) 원천 csv.gz까지 전량 이관·CRC 검증을 마쳐 상시 기동
> 이유가 사라졌다. 로컬 데이터 `./seaweedfs/data`(309MB)는 바인드 마운트라 남아 사실상 원본 백업이다.

> `profiles`를 붙인 서비스를 **의존**(`depends_on`)하는 뼈대 서비스가 없어야 한다(있으면 기본 기동이 깨진다).
> 옵션↔옵션 의존은 같은 프로필을 공유하거나 함께 활성화한다. 대안인 다중 파일 `-f` override는
> YAML 앵커가 파일 스코프라 기능 파일에서 공용 앵커를 못 써 이 레포에선 profiles를 택했다.
>
> 🔴 **의존받는 서비스는 의존하는 쪽의 profile을 전부 물려받아야 한다.** `seaweedfs`에
> `legacy-storage` 하나만 붙이면 `--profile legacy-sql`(trino)·`--profile monitoring`(prometheus,
> `seaweedfs:9324` 스크랩)이 **의존 비활성으로 깨진다**. 그래서 profile이 3개다.
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

## 참고

- Docker Compose — `deploy.resources`: https://docs.docker.com/reference/compose-file/deploy/#resources
- Docker Compose — extension fields(YAML 앵커): https://docs.docker.com/reference/compose-file/fragments/
- Docker — logging drivers(json-file): https://docs.docker.com/config/containers/logging/json-file/
- Trino — release types(LTS): https://trino.io/docs/current/release.html
- hadolint: https://github.com/hadolint/hadolint
