# 환경 세팅 (setup)

이 저장소를 **처음부터 굴러가는 상태까지** 만드는 절차의 **정본**이다.
루트 [`README.md`](../README.md)에는 빠른 시작만 두고, 단계별 상세·전제·함정은 여기 있다.

> **이 문서가 담지 않는 것**
> - **현행 진행 상태·미해결·실측 수치** — 저장소 밖 `$OBSIDIAN_VAULT/status/`에 있다.
>   여기 적힌 절차가 *지금 어디까지 돌아가는지*는 이 문서의 관심사가 아니다.
> - **규칙의 근거** — 왜 이렇게 정했는지는 [`conventions/`](conventions/README.md) 각 문서에 있다.
> - **설정값의 정본** — 값은 항상 설정 파일이 사실이다.
>   [`scripts/k8s-env.sh`](../scripts/k8s-env.sh) · [`.env.example`](../.env.example) ·
>   [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) · [`compose.yml`](../compose.yml).
>   이 문서는 **순서와 전제**만 설명하고 값을 중복 정의하지 않는다.

## 전체 그림

이 환경은 **"compose up 하나"가 아니다.** 네 덩어리를 순서대로 올린다.

```
0~1. 로컬 도구 + 파이썬 환경          (호스트)
2.   .env                              (호스트)
3.   로컬 Kubernetes — 컴퓨트·스토리지  (kind on Podman)
4.   러너 이미지                        (로컬 레지스트리)
5.   Dagster                           (호스트 · 메타 DB만 compose)
```

Dagster는 **클러스터 밖 호스트**에서 돌며 K8s를 원격 컴퓨트로 트리거한다.
스토리지(SeaweedFS)·카탈로그(Postgres)·컴퓨트(Spark·Flink)만 클러스터에 있다.

---

## 0. 사전 요구 도구

```shell
brew install podman kind kubectl helm
brew install hadolint                      # pre-commit의 hadolint 훅이 로컬 바이너리를 쓴다
uv tool install pre-commit
```

`uv` 자체는 [Astral 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른다.

> 🔴 **이 환경에는 `docker` 바이너리가 없다.** 컨테이너 런타임은 **podman**이고 compose는
> `podman compose`(외부 provider `docker-compose` 경유)로 돈다.
> 다른 문서에 나오는 `docker compose ...`는 **전부 `podman compose ...`로 읽는다.**

`k8s-*.sh`는 시작할 때 `require_cli`로 위 CLI를 확인하고, 없으면 같은 `brew install` 문구를 띄우고 멈춘다.

## 1. 저장소 준비

```shell
git clone <repo> && cd dagster-study

# 파이썬 환경 — 실제 프로젝트는 dagster/dockerfile.d/src 에 있다
cd dagster/dockerfile.d/src
uv sync --group dev
cd -

# 커밋 게이트
pre-commit install --install-hooks         # pre-commit + commit-msg 훅
pre-commit run --all-files                 # repo 루트에서 — 아래 §8 CWD 함정
```

dbt 패키지는 미커밋이라 한 번 받아야 한다.

```shell
cd dagster/dockerfile.d/src
uv run dbt deps  --project-dir dbt_pipelines
uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines
```

`dbt parse`가 만드는 `target/manifest.json`은 **`@dbt_assets` 로드에 필요**하다 — 빠뜨리면
정의 로드 단계에서 dbt 자산이 수집되지 않는다.

> **lock 파일은 커밋되지 않는다** — `uv sync`는 매번 의존성을 재해석한다.
> 루트 `uv.lock`은 실질 락이 아닌 스텁이니 설치 근거로 읽지 마라. 실제 락은
> `dagster/dockerfile.d/src/uv.lock`이고, 파이썬 요구는 그쪽 `pyproject.toml`의 `requires-python`이 정본이다.

## 2. 환경변수 (`.env`)

```shell
cp .env.example .env
```

[`.env.example`](../.env.example)이 키·형식의 정본이고 각 키의 의도가 주석으로 붙어 있다.
그룹은 여섯이다.

| 그룹 | 무엇을 가리키나 |
| --- | --- |
| `POSTGRES_*` | Dagster **메타 스토리지**(compose `postgres`) |
| `DAGSTER_PORT` | webserver UI 포트 |
| `AWS_*` · `ENDPOINT_URL` | S3(SeaweedFS) 공용 자격증명·엔드포인트 |
| `ICEBERG_CATALOG_*` | Iceberg 카탈로그 — **pyiceberg(Dagster) 경로** |
| `ICEBERG_JDBC_*` · `ICEBERG_PG_*` · `ICEBERG_S3_*` · `ICEBERG_WAREHOUSE` | 같은 카탈로그의 **JDBC(dbt-spark) 경로** |
| `SPARK_REMOTE` · `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` | dbt-spark ↔ Spark Connect 접속 |
| `AWS_*_CHECKSUM_*` | SeaweedFS 호환(§8 참조) |

비밀값은 **§3을 돌린 뒤** 클러스터 Secret에서 꺼내 채운다(그래서 `.env` 완성은 §3 이후다).

```shell
kubectl get secret catalog-pg-app  -o jsonpath='{.data.password}'     | base64 -d   # ICEBERG_*_PASSWORD
kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-access-key}' | base64 -d   # ICEBERG_S3_ACCESS_KEY
kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-secret-key}' | base64 -d   # ICEBERG_S3_SECRET_KEY
kubectl get secret spark-grpc-tls  -o jsonpath='{.data.ca\.crt}'      | base64 -d > ~/.lakehouse-ca.crt
```

환경변수를 **새로 추가**할 때의 전파 체인(`.env.example` → `.env` → `compose.yml` → `EnvVar`)은
[`operations.md`](operations.md) §1이 정본이다.

## 3. 로컬 Kubernetes

**kind on Podman**(rootful 머신 필수) + 로컬 레지스트리 `localhost:5001`.
설정 단일 출처는 [`scripts/k8s-env.sh`](../scripts/k8s-env.sh)이고, 모든 값이 `${VAR:-기본값}`이라
환경변수로 덮을 수 있다.

```shell
./scripts/k8s-up.sh          # podman machine + kind 클러스터 + 레지스트리 + ingress-nginx
./scripts/k8s-operators.sh   # cert-manager + Spark/Flink Operator + CloudNativePG + 로컬 CA
./scripts/k8s-poc-storage.sh # Secret 2종 + SeaweedFS + 버킷 + CNPG Cluster(카탈로그 DB)
```

각 스크립트는 끝에 **다음 단계를 출력**한다. 단계별로 무엇이 생기는지:

**`k8s-up.sh`** — 클러스터 바닥을 깐다.
전용 podman machine(rootful) → 레지스트리 컨테이너 → kind 클러스터 `lakehouse` →
각 노드에 `certs.d` 주입 → 레지스트리를 kind 네트워크에 연결 → ingress-nginx.

**`k8s-operators.sh`** — 컨트롤 플레인을 얹는다.
Spark Operator와 cleanup RBAC → cert-manager와 Flink Operator → CloudNativePG와
Barman Cloud 플러그인 → `k8s/local-ca.yaml`의 gRPC TLS 발급 체인.

**`k8s-poc-storage.sh`** — 데이터가 앉을 자리를 만든다.
Secret `lakehouse-creds`·`catalog-pg-app` → SeaweedFS StatefulSet →
버킷 `warehouse`·`pg-backup` → 백업 구성 → `Cluster/catalog-postgres`.

주요 다이얼 — 전부 환경변수로 덮는다.

- `INSTALL_FLINK=false` — Flink Operator 제외
- `INSTALL_INGRESS=false` — ingress-nginx 제외
- `MACHINE_CPUS` · `MACHINE_MEMORY_MIB` · `MACHINE_DISK_GIB` — VM 자원.
  예산 근거는 [`resource-sizing.md`](resource-sizing.md)

정리는 이렇다. **podman machine은 기본 보존**된다.

```shell
./scripts/k8s-down.sh                      # kind 클러스터 + 레지스트리 삭제
STOP_MACHINE=true  ./scripts/k8s-down.sh   # VM 중지(데이터 보존)
REMOVE_MACHINE=true ./scripts/k8s-down.sh  # VM 삭제(데이터 소멸)
```

### 3-1. 접근 경로

HTTP UI와 gRPC는 **Ingress**로 나가고(`*.localtest.me:8080` — `localtest.me`는 공개 DNS가 127.0.0.1로
응답하므로 `/etc/hosts` 수정이 필요 없다), JDBC·S3만 `port-forward`를 쓴다.

| 경로 | 주소 | 조건 |
| --- | --- | --- |
| Flink Web UI | http://flink.localtest.me:8080 | 세션 클러스터가 떠 있을 때 |
| Spark Web UI | http://spark.localtest.me:8080 | Spark Connect가 `--replicas=1`일 때 |
| Spark Connect (gRPC) | `sc://spark-grpc.localtest.me:8443/;use_ssl=true` | `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` 필요 |

```shell
kubectl port-forward svc/catalog-postgres-rw 15432:5432   # Iceberg JDBC 카탈로그(CNPG 쓰기 서비스)
kubectl port-forward svc/seaweedfs           18333:8333   # S3 API
kubectl port-forward svc/spark-connect       15002:15002  # Spark Connect 폴백 경로
```

> 🔴 **Flink는 REST와 UI가 같은 포트다** — UI를 열면 **잡 제출 API도 함께 나간다**.
> "UI만 열었다"로 읽지 않는다.

### 3-2. 컴퓨트 기동·회수

상주 컴퓨트는 **켜둔 채 잊으면 예산을 계속 갉아먹는다.** 쓰기 직전에 올리고 **끝난 자리에서 내린다.**
Spark Connect의 `scale` 명령은 §4에서 러너 이미지를 push하고 매니페스트를 최초 적용한 뒤부터 쓸 수 있다.

```shell
# Spark Connect (dbt·노트북용) — §4의 최초 배포 완료 후, 평시 0에서 쓸 때만 1
kubectl scale deploy/spark-connect --replicas=1
kubectl scale deploy/spark-connect --replicas=0

# Flink 세션 클러스터 — 잡이 없어도 JobManager가 상주 점유한다
kubectl apply  -f k8s/flink/flinkdeployment-session.yaml
kubectl delete -f k8s/flink/flinkdeployment-session.yaml
```

Spark·Flink **동시 기동은 허용**되며, 지켜야 할 경계는 `spark.executor.instances` ≤ 1이다.
근거·실측은 [`conventions/k8s.md`](conventions/k8s.md) §9-3.

## 4. 러너 이미지와 Spark Connect 리소스 (최초 1회 / 이미지·매니페스트 변경 시)

Spark·Flink 워크로드는 Iceberg·S3A 의존을 구운 **전용 이미지**로 돈다.
로컬 레지스트리에 직접 push하면 클러스터가 같은 이름으로 받는다(`kind load` 불필요).

```shell
podman build -f k8s/spark/Dockerfile.spark-runner -t localhost:5001/spark-runner:0.5.0 k8s/spark
podman push --tls-verify=false localhost:5001/spark-runner:0.5.0

podman build -f k8s/flink/Dockerfile.flink-runner -t localhost:5001/flink-runner:0.3.0 k8s/flink
podman push --tls-verify=false localhost:5001/flink-runner:0.3.0

# 최초 1회: Spark Connect Deployment·Service·Ingress·Certificate 생성
kubectl apply -f k8s/spark/spark-connect-server.yaml
kubectl scale deploy/spark-connect --replicas=0  # 평시 자원 회수
```

`spark-connect-server.yaml`을 바꾼 뒤에도 같은 `kubectl apply`로 선언을 갱신한다. 최초 적용 직후
`Deployment`가 존재해야 이후 §3-2와 §7의 `kubectl scale` 명령이 동작한다.

> 🔴 **태그와 매니페스트는 한 벌로 올린다.** 태그만 올리고 `k8s/spark/*.yaml`·`k8s/flink/*.yaml`을
> 그대로 두면 구 이미지가 계속 돈다. **현재 태그의 사실은 매니페스트의 `image:` 값**이다 —
> 위 명령의 태그가 의심스러우면 문서가 아니라 매니페스트를 본다.
>
> ```shell
> grep -rn "image:" k8s/spark/*.yaml k8s/flink/*.yaml
> ```

## 5. Dagster (호스트)

```shell
podman compose up -d postgres              # 메타 스토리지만 기동 (127.0.0.1 바인딩)

cd dagster/dockerfile.d/src
export DAGSTER_HOME="$PWD"                 # dagster.yaml이 있는 디렉터리
uv run dg dev                              # http://localhost:3000
```

`compose.yml`의 뼈대는 `dagster-webserver`·`dagster-daemon`·`postgres` 셋이고, 나머지는 `profiles`로
opt-in한다(`legacy-sql`=trino · `legacy-storage`=seaweedfs · `monitoring`=prometheus).
호스트 실행 경로에서는 **`postgres`만** 필요하다.

```shell
podman compose --profile legacy-sql up -d trino    # Trino 값 대조가 필요할 때만
```

## 6. 검증

기동 자체가 검증은 아니다. 아래는 **접속 없이 도는 것**과 **실인프라를 타는 것**으로 갈린다.

```shell
# 인프라 미접속 — 언제든 돌린다
pre-commit run --all-files                                                    # repo 루트
uv run --project dagster/dockerfile.d/src --with mypy mypy dagster/dockerfile.d/src/src   # repo 루트
cd dagster/dockerfile.d/src && uv run dg check defs                           # 정의 로드 + 자산 수집

# 실인프라 접속 — 수동 관문
uv run scripts/spark_connect_smoke.py       # dbt-spark ↔ Spark Connect 어댑터
uv run scripts/iceberg_changelog_probe.py   # Iceberg changelog 판독
```

> 🔴 **`spark_connect_smoke.py`의 종료코드는 셋이다** — `0`=통과 / `1`=회귀 /
> **`2`=사전조건 미충족(판정 불가)**. `2`를 통과로 읽지 않는다. 이 관문은
> `dbt-spark`·`pyspark` 상한을 올리기 **직전에** 통과시킨다.

테스트 계층·우선순위와 각 관문이 무엇을 보증하지 *않는지*는 [`test.md`](test.md)가 정본이다.

## 7. 노트북 (옵션)

Dagster와 **같은 venv**를 쓰므로 커널 하나로 Spark Connect·pyiceberg에 붙고
`dagster_project.common.*`를 그대로 import할 수 있다.

```shell
kubectl scale deploy/spark-connect --replicas=1      # 평시 0이라 먼저 올린다 (§3-2)
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널

cd dagster/dockerfile.d/src
uv run --group notebook jupyter lab --port 8889 --notebook-dir ../../../notebooks
```

**8889를 쓰는 이유**: 8888은 compose SeaweedFS filer UI가 점유한다.
작성 규칙은 [`notebooks/README.md`](../notebooks/README.md)와 [`conventions/analysis.md`](conventions/analysis.md).

---

## 8. 함정 (전부 실측으로 확인된 것)

### CWD가 결정적인 명령

도구마다 설정 탐색 방식이 달라 **어느 디렉터리에서 실행하느냐가 결과를 바꾼다.**

| 명령 | 실행 위치 | 이유 |
| --- | --- | --- |
| `mypy` | **repo 루트** | mypy는 상위 디렉터리를 탐색하지 않는다 |
| `sqlfluff` · `pre-commit` | **repo 루트** | `library_path`가 **CWD 기준 상대경로**다 |
| `dg` · `dbt` · `pytest` | `dagster/dockerfile.d/src` | 프로젝트 루트 |

### 정상인데 고장처럼 보이는 것

- **15002 포트포워드 실패** — Spark Connect는 평시 `--replicas=0`이다. 고장이 아니라 **회수된 상태**다.
  §3-2로 먼저 1로 올린다.
- **15432 포트포워드가 접속 종료마다 죽는다** — Postgres 경로가 FIN이 아닌 **RST**로 끊고 kubectl이
  이를 터널 전체의 치명 오류로 취급한다. 같은 조건에서 15002·18333은 생존한다.
  🔴 **이것을 "호스트 메모리 압박"의 근거로 삼지 마라** — 그 지표는 무효다. 자동 재기동하되 시각을 남긴다.

  ```shell
  until kubectl port-forward svc/catalog-postgres-rw 15432:5432; do
      echo "$(date '+%F %T') 15432 재기동" >> /tmp/pf-15432.log
      sleep 1
  done
  ```

### 조용히 틀리는 것

- **`DAGSTER_HOME` 미지정** → 임시 sqlite 인스턴스가 쓰여 **런이 UI에 남지 않는다**. 실패하지 않고 조용하다.
- **`AWS_REQUEST_CHECKSUM_CALCULATION=when_required` 누락** → 최신 AWS SDK가 본문을 aws-chunked로 감싸는데
  SeaweedFS가 못 푼다. 업로드는 성공한 것처럼 보이고 **객체가 손상된다.**
- **`ICEBERG_S3_*` 엔드포인트와 키가 어긋남** → 카탈로그 **나열은 되고** `load_table`에서 `ACCESS_DENIED`.
  부분 성공이라 원인을 오해하기 쉽다. 엔드포인트와 자격증명은 **한 쌍으로 바꾼다.**
- **dbt 타깃에 `trino`라는 이름은 없다** — Trino는 `dev`/`prod`이고, 기본값은 `spark_connect`다
  (`target: "{{ env_var('DBT_TARGET', 'spark_connect') }}"`).

### podman machine

- **rootful이 아니면 `k8s-up.sh`가 멈춘다** — `podman machine set --rootful`로 바꾼다.
- **머신이 이미 있으면 `MACHINE_CPUS`·`MACHINE_MEMORY_MIB`가 반영되지 않는다**(재사용 경로).
  자원을 바꾸려면 머신을 다시 만들어야 한다.
- **kind 노드의 공개 포트는 클러스터 생성 시점에만 정할 수 있다** — `k8s/kind-cluster.yaml`의
  `extraPortMappings`를 빠뜨리면 **재생성이 유일한 해법**이다.

### dbt 타깃별 전제

**`spark_connect`** *(기본)* — Spark Connect `--replicas=1`에 더해 TLS Ingress나 15002 포트포워드가 필요하다.
**`spark.remote` 외의 conf를 넣지 마라** — 리모트 세션에서 static conf가 조용히 무시된다.

**`spark_session`** — 호스트 로컬 SparkSession으로 돈다. 15432·18333 포트포워드가 필요하고,
다음 다섯이 **기본값 없이** 있어야 한다.
`ICEBERG_JDBC_URI` · `ICEBERG_PG_USER` · `ICEBERG_PG_PASSWORD` · `ICEBERG_WAREHOUSE` · `ICEBERG_S3_ENDPOINT`

**`dev` · `prod`** — Trino. `podman compose --profile legacy-sql up -d trino`가 선행이다.

**`spark_thrift`** — 선언만 있고 배포되지 않는다. 쓰려면 `dbt-spark[PyHive]` 선설치가 필요하다.

---

## 참고

- 절차의 전제가 되는 **아키텍처**: [`architectures/overview.md`](architectures/overview.md)
- **환경변수 전파 체인·운영 정책**: [`operations.md`](operations.md)
- **K8s 규약**(워크로드·probe·Ingress·러너 이미지): [`conventions/k8s.md`](conventions/k8s.md)
- **Docker/Compose 규약**(앵커·profiles·healthcheck): [`conventions/docker.md`](conventions/docker.md)
- **커밋 게이트·pre-commit**: [`conventions/general.md`](conventions/general.md)
- **테스트 계층·관문**: [`test.md`](test.md)
