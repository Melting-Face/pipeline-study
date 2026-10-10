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

이 환경은 **dev(개발·검증) 전용**이다. 운영(prod) 환경이 아니며 운영 배포 대상은 아직 없다
([OCI k3s](architectures/oci.md)는 보류). 단일 노드·로컬 VM이라 가용성·외부 노출을 운영 기준으로 갖추지 않는다.
카탈로그 백업이 없는 것은 dev 등급의 결정이 아니라 원인 규명 중인 미배선이다
([`cluster.yaml`](../gitops/charts/catalog-postgres/templates/cluster.yaml) 주석).

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

### 층 지도

아래 층부터 쌓인다. 각 층은 바로 아래 층이 있어야 만들어진다.

| 층 | 만드는 주체 | 소유하는 것 | 확인 |
| --- | --- | --- | --- |
| ① VM | `scripts/k8s-up.sh` | podman machine(rootful) | `podman machine list` |
| ② 레지스트리 | `scripts/k8s-up.sh` | 컨테이너 `kind-registry` · 볼륨 `kind-registry-data` | `podman ps -a --filter name=kind-registry` |
| ②' 외부 S3 | `scripts/storage-up.sh` | compose `seaweedfs` · `./seaweedfs/data` · 버킷 3개 | `podman inspect -f '{{.State.Health.Status}}' seaweedfs` |
| ③ 클러스터 | 스택 A `terraform/cluster/kind` | kind 노드 · 포트 매핑 · kubeconfig · 노드의 레지스트리 설정 | `kind get clusters` |
| ④ 플랫폼 | 스택 B `terraform/platform` | `helm_release` 3개(ingress-nginx · argo-cd · appset) | `helm list -A` |
| ⑤ 오퍼레이터 | ArgoCD | `gitops/charts/<app>` | `kubectl get applications -n argocd` |

- **Terraform이 관리하는 것은 ③부터**다. ①②②'는 Terraform state에 없다.
- ②'(오브젝트 스토리지)는 **클러스터와 수명이 독립**이다 — ③을 지우고 다시 만들어도 데이터가 남는다.
  스택 A가 클러스터를 만들 때 이 컨테이너를 kind 네트워크에 별칭 `seaweedfs-ext`로 붙인다(없으면 경고만).
- 그래도 ③은 ②에 묶여 있다. 스택 A가 `local-exec`로 호스트의 podman을 직접 부르기 때문이다(아래 「스택 A가 하는 일」).
- ⑤는 `terraform apply`로 바뀌지 않는다. `gitops/charts/`에 커밋하고 push하면 ArgoCD가 반영한다.
- ④·⑤의 확인 명령은 `KUBECONFIG`를 export한 뒤에 쓴다(아래 「올리기」 마지막 줄).

### 올리기

지금 클러스터는 **`lakehouse-next`(8082/8445)** 다. 아래 `-var`는 매번 그대로 넘긴다.
Terraform 명령은 저장소 **루트**에서 실행한다(아래 §내리기 참고).

```shell
scripts/k8s-up.sh                         # ① VM · ② 레지스트리 — 손으로 하는 방법은 아래
scripts/storage-up.sh                     # ②' 외부 S3 — 클러스터보다 먼저(스택 A가 네트워크에 붙인다)

terraform -chdir=terraform/cluster/kind init
terraform -chdir=terraform/cluster/kind apply \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445

./scripts/k8s-secrets.sh                  # Secret(lakehouse-creds·catalog-pg-app) — ArgoCD 밖

terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform apply \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main

export KUBECONFIG=~/.kube/lakehouse-next.config   # 스택 A가 쓴 클러스터 전용 kubeconfig
```

- `scripts/k8s-env.sh`를 `source`해도 같은 `KUBECONFIG`가 잡힌다. 다만 그 전에 `CLUSTER_NAME=lakehouse-next`를
  export해야 한다. 빼면 없는 파일(`~/.kube/lakehouse.config`)을 가리킨다.
- `kubectl`에 옵션을 붙이는 대신 `KUBECONFIG`를 export한다(zsh는 `$K` 형태의 명령 변수를 쪼개지 않는다).
- 기능 브랜치를 클러스터에서 검증할 때만 `target_revision=<브랜치>`로 바꾸고, 그 전에 브랜치를 push한다.
- PR2 뒤에는 `-var` 없이 기본값(`lakehouse`, 8080/8443)으로 올린다.
- `scripts/k8s-secrets.sh`는 스택 A 다음에 돌린다(Secret — S3 키는 `.env`의 `ICEBERG_S3_*`). 카탈로그 Cluster는
  ArgoCD 앱 `catalog-postgres`가 만들고 Secret이 늦으면 기다린다.

#### `k8s-up.sh`가 하는 일 — 손으로 하기

스크립트 대신 아래 명령을 직접 쳐도 ①②가 같은 상태가 된다. 숫자는 `scripts/k8s-env.sh`의 기본값이다.

```shell
# ① VM — 실행 중인 podman machine이 없을 때만 만든다
podman machine init dagster-k8s --rootful --cpus 8 --memory 26702 --disk-size 93
podman machine start dagster-k8s

# ② 레지스트리 — 푸시한 이미지는 명명 볼륨에 남아 컨테이너를 지워도 보존된다
podman run -d --restart=always \
  -p 127.0.0.1:5001:5000 \
  -v kind-registry-data:/var/lib/registry \
  --name kind-registry docker.io/library/registry:2.8.3
```

스크립트는 여기에 분기 두 개를 더한다.

- **VM**: 이미 실행 중인 머신이 있으면 새로 만들지 않고 그 머신을 쓴다. 그 머신이 rootless면 멈춘다
  (kind의 podman provider는 rootful이 필요하다). 전용 머신 `dagster-k8s`를 강제하려면 `MANAGE_MACHINE=true`.
- **레지스트리**: 상태가 셋이다 — 실행 중이면 그대로, 중지면 `podman start kind-registry`, 없을 때만 `podman run`.
- `k8s-env.sh`는 `KIND_EXPERIMENTAL_PROVIDER=podman`도 export한다. 이 변수는 `kind` **CLI**만 읽는다.
  Terraform 프로바이더는 이 변수를 보지 않고 PATH에서 런타임을 자동으로 고른다(docker → nerdctl → podman).

#### 스택 A가 `kind_cluster` 말고 하는 일

[`terraform/cluster/kind/main.tf`](../terraform/cluster/kind/main.tf)의 `terraform_data` 2개가
`local-exec`로 podman을 부른다. 손으로 하면 다음과 같다.

```shell
# registry_certs — 노드 containerd가 localhost:5001을 kind-registry:5000으로 보내게 한다
podman exec lakehouse-next-control-plane mkdir -p /etc/containerd/certs.d/localhost:5001
podman exec -i lakehouse-next-control-plane \
  sh -c "cat > '/etc/containerd/certs.d/localhost:5001/hosts.toml'" \
  < terraform/cluster/kind/registry/hosts.toml

# registry_network — 레지스트리를 kind 네트워크에 붙여 노드가 kind-registry 이름을 해석한다
podman network connect kind kind-registry       # 이미 연결돼 있으면 스택 A는 건너뛴다
```

- 이 둘은 **클러스터가 새로 만들어질 때만** 다시 돈다. 노드 안의 설정이 지워져도 Terraform state는 그 사실을
  모르므로 `plan`에 차이가 나오지 않는다.
- `plan` 단계의 precondition(`scripts/detect-runtime.sh`)은 PATH에 잡히는 런타임이 `podman`인지만 본다.
  VM이 멈춰 있어도 통과하므로, VM이 떠 있는지는 `podman machine list`로 따로 확인한다.

### 수렴 확인

```shell
kubectl get applications -n argocd
terraform -chdir=terraform/platform output -raw argocd_url
terraform -chdir=terraform/platform output -raw argocd_initial_admin_password_command
```

- **통과 기준**: Application 수 = [`terraform/platform/variables.tf`](../terraform/platform/variables.tf)의
  `apps` 원소 수, 전부 `Synced` · `Healthy`. apply 직후엔 몇 분 걸린다.
- `Healthy`인데 `OutOfSync`가 남으면 `argocd app diff --core <앱>`으로 필드를 확인한다
  ([`argocd-gitops.md`](argocd-gitops.md) §5).
- 오퍼레이터 변경은 `terraform apply`가 아니라 `gitops/charts/` 커밋 → push로 반영된다.

### 내리기

```shell
terraform -chdir=terraform/platform destroy \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main
terraform -chdir=terraform/cluster/kind destroy \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445

scripts/k8s-down.sh                        # 레지스트리 컨테이너 삭제(이미지 볼륨은 보존)
STOP_MACHINE=true   scripts/k8s-down.sh    # + VM 중지
REMOVE_MACHINE=true scripts/k8s-down.sh    # + VM 삭제(데이터 소멸)
```

- 클러스터는 **`destroy`로 먼저** 내린다.
  `k8s-down.sh`도 같은 이름의 kind 클러스터를 지우지만 Terraform state가 남는다.
- tfstate는 `apply`를 실행한 디렉터리의 `terraform/*/terraform.tfstate`에 생긴다.
  그래서 Terraform은 **저장소 루트에서만** 실행한다([`conventions/terraform.md`](conventions/terraform.md) §4).
- worktree에서 apply했다면 그 worktree를 지우기 전에 state를 루트로 옮기고,
  **apply 때와 같은 `-var`로** `plan`해 `No changes.`를 확인한다. `-var`를 빼면 기본값과 비교돼 교체가 나온다.

#### `k8s-down.sh`가 하는 일 — 손으로 하기

```shell
kind delete cluster --name lakehouse-next     # 남아 있을 때만. destroy를 먼저 했다면 이미 없다
podman rm -f kind-registry                    # 컨테이너만 지운다. 볼륨 kind-registry-data는 남는다

podman machine stop <머신>                    # STOP_MACHINE=true 에 해당
podman volume rm -f kind-registry-data        # REMOVE_MACHINE=true 에 해당 — 푸시한 이미지가 사라진다
podman machine rm -f <머신>                   #   〃 VM 안의 데이터가 모두 사라진다
```

- `<머신>`은 **지금 실행 중인 머신**이다(`podman machine list`). ①에서 기존 머신을 재사용했다면 `dagster-k8s`가 아니다.
  스크립트도 같은 기준으로 대상을 고르고(`MANAGE_MACHINE=true`면 `MACHINE_NAME`),
  실행 중인 대상이 없으면 **아무것도 지우지 않고** 실패한다.

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
