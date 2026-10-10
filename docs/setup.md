# 환경 세팅 (setup)

클론한 뒤 **위에서 아래로 실행**하면 로컬 환경이 뜨는 절차서다. 명령은 모두 **repo 루트**에서 시작한다.
왜 이렇게 구성했는지는 다루지 않는다 — 배경은 각 절의 링크와 [`architectures/overview.md`](architectures/overview.md)를 본다.

## 지금 되는 범위

GitOps 전환의 데이터 층(SeaweedFS·카탈로그 Postgres·Secret)은 PR2에서 온다.
**지금은 오퍼레이터까지 뜨고, 적재·dbt는 돌지 않는다.** ⏸ 절은 PR2 뒤에 쓰는 절차다.

| 절 | 상태 |
| --- | --- |
| §0 도구 · §1 저장소 · §2 `.env` 복사 | ✅ |
| §3 클러스터 → ArgoCD → 오퍼레이터 수렴 | ✅ |
| §6 인프라 미접속 검증(`pre-commit`·`mypy`) | ✅ |
| §6 `dg check defs` | ⚠️ 의존성 결함으로 실패 |
| §2 비밀값 · §3-1 port-forward · §3-2 컴퓨트 · §4 Spark Connect · §5 Dagster · §6-1 원천 · §7 노트북 | ⏸ PR2 |

## 검증 환경

이 절차는 아래 환경에서만 검증했다. 다른 OS(Linux·Windows)와 Intel Mac은 **미검증**이다.
값을 바꿀 때는 이 표가 아니라 **출처 열의 파일**을 고친다.

| 항목 | 값 | 출처 |
| --- | --- | --- |
| 호스트 | macOS · Apple Silicon(arm64) | — |
| 컨테이너 런타임 | podman 5.x · `podman machine`(applehv VM) · **rootful** | [`k8s-up.sh`](../scripts/k8s-up.sh) |
| VM 크기 | 8 CPU · 26702 MiB · 93 GiB — VM 총량이며 노드 Allocatable은 이보다 작다 | [`k8s-env.sh`](../scripts/k8s-env.sh) |
| 로컬 레지스트리 | `docker.io/library/registry:2.8.3` → `127.0.0.1:5001` | [`k8s-env.sh`](../scripts/k8s-env.sh) |
| kind 노드 | `kindest/node:v1.35.0`(digest 고정) | [`cluster/kind/variables.tf`](../terraform/cluster/kind/variables.tf) |
| Terraform | `>= 1.5.0` | 두 스택의 `versions.tf` |
| 스택 A 프로바이더 | `tehcyx/kind 0.11.0` · `hashicorp/external 2.4.2` | [`cluster/kind/versions.tf`](../terraform/cluster/kind/versions.tf) |
| 스택 B 프로바이더 | `hashicorp/helm 3.0.2` | [`platform/versions.tf`](../terraform/platform/versions.tf) |

- 호스트 CLI(`podman`·`kind`·`kubectl`·`helm`·`terraform`)는 버전을 고정하지 않는다(`brew` 최신).
  클러스터 버전을 정하는 것은 `kind` CLI가 아니라 위의 노드 이미지와 프로바이더다.

## 0. 사전 요구 도구

```shell
brew install podman kind kubectl helm terraform hadolint
uv tool install pre-commit
```

- `uv`는 [Astral 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따른다.
- 컨테이너 런타임은 podman이다. 다른 문서의 `docker compose ...`는 `podman compose ...`로 실행한다.

## 1. 저장소 준비

```shell
git clone https://github.com/Melting-Face/pipeline-study.git && cd pipeline-study

(cd dagster/dockerfile.d/src && uv sync --group dev)

(cd dagster/dockerfile.d/src \
  && uv run dbt deps  --project-dir dbt_pipelines \
  && uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines)

pre-commit install --install-hooks
pre-commit run --all-files
```

## 2. 환경변수 (`.env`)

```shell
cp .env.example .env
```

키와 형식은 [`.env.example`](../.env.example)의 주석을 따른다.

| 그룹 | 무엇을 가리키나 |
| --- | --- |
| `POSTGRES_*` | Dagster 메타 스토리지 |
| `DAGSTER_PORT` | webserver UI 포트 |
| `AWS_*` · `ENDPOINT_URL` | S3(SeaweedFS) 자격증명·엔드포인트 |
| `ICEBERG_CATALOG_*` | Iceberg 카탈로그 — pyiceberg(Dagster) 경로 |
| `ICEBERG_JDBC_*` · `ICEBERG_PG_*` · `ICEBERG_S3_*` · `ICEBERG_WAREHOUSE` | 같은 카탈로그 — JDBC(dbt-spark) 경로 |
| `SPARK_REMOTE` · `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` | dbt-spark ↔ Spark Connect |
| `AWS_*_CHECKSUM_*` | SeaweedFS 호환 — `when_required` 유지 |
| `PHYSIONET_*` | MIMIC-IV·eICU 다운로드 계정(§6-1) |
| `POLYGON_API_KEY` · `FRED_API_KEY` | 시장 데이터 API 키(§6-2) |

⏸ **PR2 뒤** — §3 이후 클러스터 Secret에서 비밀값을 꺼내 채운다.

```shell
kubectl get secret catalog-pg-app  -o jsonpath='{.data.password}'     | base64 -d   # ICEBERG_*_PASSWORD
kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-access-key}' | base64 -d   # ICEBERG_S3_ACCESS_KEY
kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-secret-key}' | base64 -d   # ICEBERG_S3_SECRET_KEY
kubectl get secret spark-grpc-tls  -o jsonpath='{.data.ca\.crt}'      | base64 -d > ~/.lakehouse-ca.crt
```

환경변수를 새로 추가하는 절차는 [`operations.md`](operations.md) §1.

## 3. 로컬 Kubernetes

설계는 [`argocd-gitops.md`](argocd-gitops.md).
**처음이라면 [`setup/local-k8s.md`](setup/local-k8s.md)를 따른다** — 층마다 podman·terraform·kubectl 명령을
직접 치고, 무엇이 생기는지와 kubectl로 확인하는 법을 함께 적었다. 이 절은 그 요약과 단축 경로다.

### 층 지도

| 층 | 만드는 주체 | 단축 |
| --- | --- | --- |
| ① VM · ② 레지스트리 | `podman machine` · `podman run` | `scripts/k8s-up.sh` |
| ②' 외부 S3(SeaweedFS) | `podman compose --profile storage` | `scripts/storage-up.sh` |
| ③ 클러스터 | Terraform 스택 A `terraform/cluster/kind` | — |
| Secret | `kubectl create secret` | `scripts/k8s-secrets.sh` |
| ④ 플랫폼 | Terraform 스택 B `terraform/platform` | — |
| ⑤ 앱 | ArgoCD(`gitops/charts/`) | — |

- Terraform이 관리하는 것은 ③과 ④뿐이다. ②'는 클러스터와 수명이 독립이라 ③을 다시 만들어도 데이터가 남는다.

### 올리기 (단축 경로)

지금 클러스터는 **`lakehouse-next`(8082/8445)** 다. 명령은 저장소 **루트**에서 실행한다.

```shell
scripts/k8s-up.sh
scripts/storage-up.sh                     # 클러스터보다 먼저(스택 A가 kind 네트워크에 붙인다)

terraform -chdir=terraform/cluster/kind init
terraform -chdir=terraform/cluster/kind apply \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445
export KUBECONFIG=~/.kube/lakehouse-next.config

CLUSTER_NAME=lakehouse-next ./scripts/k8s-secrets.sh   # CLUSTER_NAME을 빼면 다른 kubeconfig를 본다

terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform apply \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main

kubectl get applications -n argocd        # 전부 Synced · Healthy 면 수렴
```

내리기·통과 기준·각 단계가 하는 일은 [`setup/local-k8s.md`](setup/local-k8s.md).

### 다이얼

- VM 자원: `MACHINE_CPUS` · `MACHINE_MEMORY_MIB` · `MACHINE_DISK_GIB` 환경변수
  (`k8s-up.sh`가 머신을 **새로 만들 때만** 반영). 근거는 [`resource-sizing.md`](resource-sizing.md).
- 오퍼레이터 버전·값: `gitops/charts/<app>/Chart.yaml`·`values.yaml`.
- 앱 목록·추적 리비전: `terraform/platform/variables.tf`의 `apps`·`target_revision`.

### 3-1. 접근 경로

| 경로 | 주소(기본 포트 — 지금은 8082/8445) | 상태 |
| --- | --- | --- |
| ArgoCD UI | http://argocd.localtest.me:8080 | ✅ 상시 |
| Flink Web UI | http://flink.localtest.me:8080 | ⏸ 세션 클러스터가 떠 있을 때 |
| Spark Web UI | http://spark.localtest.me:8080 | ⏸ Spark Connect `--replicas=1`일 때 |
| Spark Connect (gRPC) | `sc://spark-grpc.localtest.me:8443/;use_ssl=true` | ⏸ `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` 필요 |

⏸ port-forward 경로:

```shell
kubectl port-forward svc/catalog-postgres-rw 15432:5432   # Iceberg JDBC 카탈로그
kubectl port-forward svc/spark-connect       15002:15002  # Spark Connect 폴백
```

- Flink UI를 열면 잡 제출 REST API도 같은 포트로 열린다.
- S3는 클러스터 밖(compose)이라 port-forward 없이 `http://localhost:8333`으로 직결한다.

### 3-2. 컴퓨트 기동·회수 ⏸

쓰기 직전에 올리고 끝나면 바로 내린다.

```shell
kubectl scale deploy/spark-connect --replicas=1          # Spark Connect(§4 최초 적용 뒤)
kubectl scale deploy/spark-connect --replicas=0
kubectl get pods -l spark-role=executor                  # 내린 뒤 0개여야 한다

kubectl apply  -f k8s/flink/flinkdeployment-session.yaml # Flink 세션 클러스터
kubectl delete -f k8s/flink/flinkdeployment-session.yaml
```

- Spark·Flink 동시 기동은 허용, `spark.executor.instances` ≤ 1을 지킨다([`conventions/k8s.md`](conventions/k8s.md) §9-3).

## 4. 러너 이미지와 Spark Connect

이미지 build·push는 지금도 된다(최초 1회 / 이미지 변경 시).

```shell
podman build -f k8s/spark/Dockerfile.spark-runner -t localhost:5001/spark-runner:0.5.0 k8s/spark
podman push --tls-verify=false localhost:5001/spark-runner:0.5.0

podman build -f k8s/flink/Dockerfile.flink-runner -t localhost:5001/flink-runner:0.3.0 k8s/flink
podman push --tls-verify=false localhost:5001/flink-runner:0.3.0
```

⏸ Spark Connect 리소스 생성(최초 1회 / 매니페스트 변경 시):

```shell
kubectl apply -f k8s/spark/spark-connect-server.yaml
kubectl scale deploy/spark-connect --replicas=0
```

- 태그를 올릴 때는 `k8s/spark/*.yaml`·`k8s/flink/*.yaml`의 `image:`도 함께 바꾼다.
  현재 태그 확인: `grep -rn "image:" k8s/spark/*.yaml k8s/flink/*.yaml`.

## 5. Dagster (호스트 실행) ⏸

Dagster는 클러스터에 배포하지 않고 호스트에서 돌린다. 메타 DB 위치는 PR2가 정한다
([operations.md](operations.md) §1-2).

```shell
kubectl port-forward svc/catalog-postgres-rw 15432:5432   # 별도 터미널 (S3는 storage-up.sh로 직결)

(cd dagster/dockerfile.d/src && DAGSTER_HOME="$PWD" uv run dg dev)   # http://localhost:3000
```

- `.env`의 `POSTGRES_PORT`로 메타 DB를 하나만 고른다 — 15432=port-forward, 5432=compose `postgres`.
  compose `--profile host-dagster`와 호스트 `dg dev`를 섞지 않는다.
- compose는 기본 `up`으로 아무것도 띄우지 않는다. 필요한 profile만 켠다:
  `host-dagster`(webserver·daemon·postgres) · `legacy-meta`(postgres) · `legacy-sql`(trino) ·
  `storage`(seaweedfs — `scripts/storage-up.sh`로 띄운다) · `monitoring`(prometheus).

```shell
podman compose --profile legacy-sql up -d trino    # Trino 값 대조가 필요할 때만
```

## 6. 검증

```shell
# 인프라 미접속 ✅
pre-commit run --all-files
uv run --project dagster/dockerfile.d/src --with types-PyYAML --with types-requests \
  --with mypy mypy dagster/dockerfile.d/src/src

# 정의 로드 — ⚠️ 지금은 의존성 해석 결함(pyarrow ↔ numpy<2)으로 import 단계에서 실패한다
(cd dagster/dockerfile.d/src && DAGSTER_HOME="$(mktemp -d)" uv run dg check defs)

# 실인프라 ⏸
uv run scripts/spark_connect_smoke.py       # 종료코드 0=통과 / 1=회귀 / 2=판정 불가(통과 아님)
uv run scripts/iceberg_changelog_probe.py

# 외부 접속 — 해당 원천을 처음 켜기 직전
uv run scripts/physionet_access_probe.py
uv run scripts/stock_source_access_probe.py --source all
```

관문별 실행 규약은 [`test/manual-gates.md`](test/manual-gates.md), 테스트 계층은 [`test.md`](test.md).

## 6-1. 원천 데이터 가져오기 ⏸

전제: `s3://warehouse` 버킷이 있다(스토리지 프로비저닝이 만든다). 존재는 `list_buckets`가 아니라
`head_bucket`으로 확인한다.

1. `.env`에 `PHYSIONET_USERNAME`/`PHYSIONET_PASSWORD`를 채운다(PhysioNet credentialed 계정 필요).
2. in-cluster로 쓰려면 Secret `physionet-creds`를 만든다(PR2 `k8s-secrets.sh`).
3. `uv run scripts/physionet_access_probe.py` — 종료코드 `0`이어야 진행한다.
4. Dagster UI에서 `raw_*` 자산을 머티리얼라이즈한다.
   `raw_mimiciv_chartevents`·`raw_mimiciv_labevents`는 동시에 돌리지 않는다.
5. 이어서 적재 자산(`chartevents` 등)을 돌린다.

- 다시 받아야 하면 자산 config `force: true`([`operations.md`](operations.md) §1-1-2).
- 이미 받아둔 파일이 있으면 `./data/raw/mimiciv/icu/...` 구조로 두고
  `uv run scripts/upload_raw_to_seaweedfs.py -n ./data/raw`로 확인한 뒤 `-n`을 빼고 올린다.

## 6-2. 시장 데이터 원천 (API 키 발급)

| 사이트 | `.env` 키 |
| --- | --- |
| Polygon.io — Dashboard → API Keys | `POLYGON_API_KEY` |
| FRED — `fredaccount.stlouisfed.org/apikeys` | `FRED_API_KEY` |

키를 채운 뒤 `uv run scripts/stock_source_access_probe.py --source all` — 종료코드 `0`이어야 자산을 켠다.
발급 절차는 [`setup/market-data-keys.md`](setup/market-data-keys.md).

## 7. 노트북 (옵션) ⏸

```shell
kubectl scale deploy/spark-connect --replicas=1
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널

(cd dagster/dockerfile.d/src \
  && uv run --group notebook jupyter lab --port 8889 --notebook-dir ../../../notebooks)
```

작성 규칙은 [`notebooks/README.md`](../notebooks/README.md).

---

## 8. 문제 해결

### 실행 위치

| 명령 | 실행 위치 |
| --- | --- |
| `mypy` · `sqlfluff` · `pre-commit` | repo 루트 |
| `dg` · `dbt` · `pytest` | `dagster/dockerfile.d/src` |

### 증상별 조치

| 증상 | 조치 | 상세 |
| --- | --- | --- |
| `k8s-up.sh`가 rootful 오류로 멈춤 | `podman machine set --rootful` 후 재시작 | [`architectures/k8s.md`](architectures/k8s.md) |
| `MACHINE_*`를 바꿔도 VM 자원이 그대로 | 머신을 중지하고 `podman machine set --cpus/--memory` | [`resource-sizing.md`](resource-sizing.md) |
| 호스트 포트를 바꿔야 함 | 스택 A destroy → 새 `-var`로 apply(생성 시점에만 정해진다) | [`argocd-gitops.md`](argocd-gitops.md) §6 |
| 15002 port-forward 실패 | Spark Connect를 `--replicas=1`로 올린다(평시 0) | §3-2 |
| 15432 port-forward가 접속마다 끊김 | 재기동 루프로 감싼다 | [`resource-sizing.md`](resource-sizing.md) §호스트 압박 판정 지표 |
| `dg dev` 런이 UI에 안 남음 | `DAGSTER_HOME`을 `dagster/dockerfile.d/src`로 지정 | [`operations.md`](operations.md) |
| S3 업로드는 성공, 객체 손상 | `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` | [`conventions/k8s/checksum.md`](conventions/k8s/checksum.md) |
| 카탈로그 나열은 되고 `load_table`이 `ACCESS_DENIED` | `ICEBERG_S3_*` 엔드포인트와 키를 한 쌍으로 맞춘다 | [`operations.md`](operations.md) |
| `iceberg` DB 테이블 접근 거부 | `ICEBERG_CATALOG_*`를 채운다(비우면 `POSTGRES_*`로 폴백) | [`operations.md`](operations.md) |
| 새 버킷에만 `PutObject` `InternalError` | SeaweedFS 볼륨 슬롯 부족 — `-volume.max` 상향 | [`resource-sizing.md`](resource-sizing.md) |
| 이미지에 `.venv`가 들어감 | `.dockerignore` 패턴에 `src/` 접두어 | [`conventions/dagster.md`](conventions/dagster.md) |

### dbt 타깃별 전제

| 타깃 | 전제 |
| --- | --- |
| `spark_connect`(기본) | Spark Connect `--replicas=1` + TLS Ingress 또는 15002 port-forward. `spark.remote` 외 conf를 넣지 않는다 |
| `spark_session` | 15432 port-forward + `storage-up.sh` + `ICEBERG_JDBC_URI`·`ICEBERG_PG_USER`·`ICEBERG_PG_PASSWORD`·`ICEBERG_WAREHOUSE`·`ICEBERG_S3_ENDPOINT` |
| `dev` · `prod` | Trino — `podman compose --profile legacy-sql up -d trino` |
| `spark_thrift` | 배포 안 됨. `dbt-spark[PyHive]` 선설치 필요 |

---

## 참고

- 아키텍처: [`architectures/overview.md`](architectures/overview.md)
- 환경변수 전파·운영 정책: [`operations.md`](operations.md)
- K8s 규약: [`conventions/k8s.md`](conventions/k8s.md) / Docker·Compose 규약: [`conventions/docker.md`](conventions/docker.md)
- 커밋 게이트: [`conventions/general.md`](conventions/general.md)
- 테스트: [`test.md`](test.md) / 수동 관문: [`test/manual-gates.md`](test/manual-gates.md)
