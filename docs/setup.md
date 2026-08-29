# 환경 세팅 (setup)

이 저장소를 **처음부터 굴러가는 상태까지** 만드는 절차의 **정본**이다.
루트 [`README.md`](../README.md)에는 빠른 시작만 두고, 단계별 상세·전제·함정은 여기 있다.

## 전체 그림

이 환경은 **"compose up 하나"가 아니다.** 네 덩어리를 순서대로 올린다.

```
0~1. 로컬 도구 + 파이썬 환경          (호스트)
2.   .env                              (호스트)
3.   로컬 Kubernetes — 컴퓨트·스토리지  (kind on Podman)
4.   러너 이미지                        (로컬 레지스트리)
5.   Dagster                           (in-cluster)
```

**Dagster도 클러스터 안에서 돈다**(구 규약은 호스트 실행이었다 — 폐기).
스토리지(SeaweedFS)·카탈로그(Postgres)·컴퓨트(Spark·Flink)와 같은 클러스터에 있다.

**선언의 소유자가 둘로 갈린다.** 오퍼레이터 3종·로컬 CA·워크로드 RBAC·Dagster 매니페스트는
**Terraform**(`terraform/lakehouse-platform/` = 스택 C)이 소유하고, 클러스터·레지스트리·스토리지·
카탈로그 DB는 **셸 스크립트**가 소유한다. 그래서 §3의 순서에 `terraform apply`가 끼어든다.
가르는 기준은 **destroy 가 무엇을 파괴하는가**다([`architectures/terraform.md`](architectures/terraform.md)).

---

## 0. 사전 요구 도구

```shell
brew install podman kind kubectl helm
brew install terraform                     # §3의 플랫폼 스택 + pre-commit의 terraform_fmt 훅
brew install hadolint                      # pre-commit의 hadolint 훅이 로컬 바이너리를 쓴다
uv tool install pre-commit
```

> ⚠️ **`terraform`은 선택이 아니다.** §3에서 플랫폼 스택을 올리는 데 쓰이고, 그와 별개로
> `terraform_fmt` 훅이 **로컬 바이너리를 호출**하므로 없으면 **커밋이 막힌다**
> (`.pre-commit-config.yaml`). 문서만 고치는 작업에서도 걸린다.

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

### 빈 클러스터에서 처음 올릴 때 — **6단계**

```shell
./scripts/k8s-up.sh          # podman machine + kind 클러스터 + 레지스트리 + ingress-nginx
./scripts/k8s-operators.sh   # 네임스페이스 3종 + cert-manager + Barman Cloud 플러그인

# ⚠️ 최초 1회만 — 오퍼레이터를 먼저 만든다(아래 "왜 두 번인가")
terraform -chdir=terraform/lakehouse-platform apply \
    -target=helm_release.spark_operator \
    -target=helm_release.flink_operator \
    -target=helm_release.cnpg

./scripts/k8s-poc-storage.sh # Secret 3종 + SeaweedFS + 버킷 3개 + CNPG Cluster(카탈로그 DB)
terraform -chdir=terraform/lakehouse-platform apply   # 매니페스트 18종(로컬 CA·RBAC·Dagster)
./scripts/k8s-dagster.sh     # Dagster 이미지 빌드·push + ConfigMap + 수렴 대기
```

### 이미 있는 클러스터를 다시 올릴 때

CRD가 남아 있으므로 `terraform apply`가 **한 번으로 합쳐진다.**

```shell
./scripts/k8s-up.sh && ./scripts/k8s-operators.sh
terraform -chdir=terraform/lakehouse-platform apply
./scripts/k8s-poc-storage.sh && ./scripts/k8s-dagster.sh
```

### 왜 `terraform apply`가 두 번인가

빈 클러스터에서 **한 번으로는 안 된다.** 스택 C가 적용하는 매니페스트 18개 중
`Database/default/dagster` 하나만 `postgresql.cnpg.io/v1`인데, 그 CRD를 **같은 apply의
`helm_release.cnpg`가 만든다.** `kubernetes_manifest`는 plan 시점에 `/apis`로 GVK를 해석하므로
`depends_on`(apply 순서만 보장)으로는 못 미루고, plan이 이렇게 죽는다.

```
Error: API did not recognize GroupVersionKind from manifest (CRD may not be installed)
  no matches for kind "Database" in group "postgresql.cnpg.io"
```

⚠️ 나머지 20개는 정상 계획되어 **`Plan: 20 to add`를 띄운 채 실패**한다 — 부분 성공처럼 보이니
"거의 됐다"로 읽지 않는다. CRD가 생긴 뒤로는 단일 apply로 돈다.

### 각 단계가 무엇을 만드는가

**`k8s-up.sh`** — 클러스터 바닥. podman machine(rootful) → 레지스트리 컨테이너(이미지는
**명명 볼륨**에 남아 `k8s-down.sh`로 사라지지 않는다) → kind 클러스터 → 각 노드에 `certs.d`
주입 → 레지스트리를 kind 네트워크에 연결 → ingress-nginx.

**`k8s-operators.sh`** — **Terraform의 선행 조건**만 만든다. 오퍼레이터는 여기서 설치하지 않는다.
네임스페이스 `cnpg-system`·`spark-operator`·`flink-operator` → cert-manager → Barman Cloud 플러그인.

> ⚠️ **네임스페이스 3종은 아무도 안 만들기 때문에 여기서 만든다.** Barman 플러그인의 원격
> 매니페스트는 `cnpg-system`을 **참조만 하고 `kind: Namespace`를 담지 않으며**, helm 릴리스 3종은
> `create_namespace = false`다. 이 단계를 빼면 Barman `kubectl apply`가
> `namespaces "cnpg-system" not found`로 죽는데, **클러스터 스코프(CRD·ClusterRole)는 먼저
> 생성되어 부분 성공처럼 보인다.**

**`terraform apply`** — 오퍼레이터 3종(Spark·Flink·CNPG) + 로컬 CA 발급 체인 + 워크로드 RBAC +
Dagster(ConfigMap·SA·Role·Deployment 2·Service·Ingress·`Database` CR).

**`k8s-poc-storage.sh`** — 데이터가 앉을 자리. Secret `lakehouse-creds`·`catalog-pg-app`·
`dagster-meta-pg-app` → SeaweedFS StatefulSet → 버킷 `warehouse`·`pg-backup`·`dagster-logs` →
백업 구성(ObjectStore·ScheduledBackup) → `Cluster/catalog-postgres`.

**`k8s-dagster.sh`** — 이미지 빌드·push + `spark-app-manifests` ConfigMap + rollout 대기.
매니페스트는 적용하지 않는다(Terraform 소유).

> ✅ **`terraform apply`가 `k8s-dagster.sh`보다 먼저라 Dagster 파드는 이미지 없이 먼저 생긴다** —
> `Init:ErrImagePull`을 한 번 거치는 것이 정상이다. 이미지를 push하면 **kubelet이 스스로 재시도해
> 같은 파드가 그대로 올라오므로** `rollout restart`는 필요 없다.

주요 다이얼 — 전부 환경변수로 덮는다.

- `INSTALL_INGRESS=false` — ingress-nginx 제외
- `MACHINE_CPUS` · `MACHINE_MEMORY_MIB` · `MACHINE_DISK_GIB` — VM 자원.
  예산 근거는 [`resource-sizing.md`](resource-sizing.md)
- 오퍼레이터 쪽 값(ns·차트 좌표·버전·자원)은 셸이 아니라
  [`terraform/lakehouse-platform/variables.tf`](../terraform/lakehouse-platform/variables.tf)가 정본이다

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
| **Dagster UI** | http://dagster.localtest.me:8080 | **상시**(오케스트레이터는 회수 대상이 아니다) |
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

# Dagster 이미지는 scripts/k8s-dagster.sh 가 빌드·push한다(§5). 수동으로 하려면:
#   podman build -t localhost:5001/dagster:0.1.0 dagster/dockerfile.d
#   podman push --tls-verify=false localhost:5001/dagster:0.1.0

# 최초 1회: Spark Connect Deployment·Service·Ingress·Certificate 생성
# (이 파일은 온디맨드 컴퓨트라 Terraform 스택 밖이다 — 그래서 kubectl apply 가 맞다)
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

## 5. Dagster (in-cluster — 정본)

```shell
./scripts/k8s-dagster.sh                   # 이미지 빌드·push → ConfigMap → rollout·Ingress 대조
# UI: http://dagster.localtest.me:8080
```

**메타 DB·RBAC·Deployment·Service·Ingress는 이 스크립트가 만들지 않는다** — 전부
`terraform/lakehouse-platform/`이 소유하며 §3의 `terraform apply`에서 이미 생겼다. 여기 남은 것은
Terraform이 다루지 않는 둘, **이미지**와 `spark-app-manifests` **ConfigMap**이다. 매니페스트를
고쳤다면 `kubectl apply`가 아니라 `terraform apply`를 돌린다(서버사이드 apply의 필드 소유권이
`kubectl`로 넘어가면 Terraform이 drift를 보고도 못 덮는다).

스크립트는 **빌드 전에** 매니페스트의 `image:` 태그와 `DAGSTER_IMAGE_TAG`를 대조하고,
마지막에 `/server_info`가 **버전 JSON을 돌려주는지**까지 본다(상태코드 200으로 닫지 않는다).
이미 빌드된 이미지를 재사용하려면 `--skip-build`.

### 호스트 `dg dev` (개발 루프 대안)

```shell
kubectl port-forward svc/catalog-postgres-rw 15432:5432   # 메타 DB(dagster) + 카탈로그(iceberg)
kubectl port-forward svc/seaweedfs           18333:8333   # S3 (compute log·Iceberg)

cd dagster/dockerfile.d/src
export DAGSTER_HOME="$PWD"                 # dagster.yaml이 있는 디렉터리
uv run dg dev                              # http://localhost:3000
```

⚠️ **in-cluster와 동시에 띄우지 않는다.** 같은 서비스가 두 곳에 살아 있으면 관측 확인을 전부
통과하면서 레거시가 정본 대신 답한다([conventions/monitoring.md](conventions/monitoring.md) §3-④).
또 `.env`의 `POSTGRES_PORT`가 **15432**(port-forward)여야 한다 — 5432면 compose DB를 보게 돼
**run 이력이 두 벌로 갈린다.**

`compose.yml`은 이제 **기본 `up`으로 아무것도 띄우지 않는다**(전부 `profiles` opt-in):
`host-dagster`=webserver·daemon·postgres · `legacy-meta`=postgres만 ·
`legacy-sql`=trino · `legacy-storage`=seaweedfs · `monitoring`=prometheus.

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
- **`ICEBERG_CATALOG_*`를 비워두면 `POSTGRES_*`로 폴백한다** — in-cluster에서 앞은 메타 DB(`dagster`),
  뒤는 카탈로그(`iceberg`)로 **처음으로 갈린다**. 빠뜨리면 `dagster` 계정으로 `iceberg` DB에 붙어
  **접속은 성공하고 테이블 접근에서 거부**된다. 위 `ICEBERG_S3_*` 함정과 같은 형태의 부분 성공이다.
- **`.dockerignore` 패턴은 빌드 컨텍스트 루트 기준이다** — 코드는 `src/` 아래에 있으므로
  접두어 없는 패턴은 **아무것도 매칭하지 않는다**. 에러가 없어 조용하다.
  2026-08-27까지 `src/.venv`(1.2GB)가 이미지에 들어가 있었다. 고친 뒤에는 **이미지 크기로 확인**한다.
- **SeaweedFS는 버킷(collection)마다 볼륨 슬롯을 쓴다** — `-volume.max`가 차면 **새 버킷에만**
  `PutObject`가 `InternalError`로 실패한다. 기존 버킷은 멀쩡해서 자격증명·체크섬 문제로 오진하기 쉽다.
  판정은 `weed shell`의 `volume.list` 첫 줄(`free:0`)과 서버 로그의
  `No writable volumes and no free volumes left`다. **디스크 여유와는 다른 축**이다.

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
