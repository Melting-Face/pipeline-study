# 로컬 Kubernetes — 손으로 올리기

[`../setup.md`](../setup.md) §3의 상세다. 로컬 클러스터를 **podman·terraform·kubectl 명령을 직접 치는 순서**로
올리고 내린다. 스크립트를 열어 보지 않아도 각 층이 무엇으로 만들어지고 어떻게 확인하는지 알 수 있게 하는 것이
목적이다. 같은 일을 하는 스크립트는 단계마다 「단축」 한 줄로만 적는다 — 두 경로의 결과는 같다.

검증 환경(호스트·VM 크기·버전)은 [`../setup.md`](../setup.md) §검증 환경, 설계는
[`../argocd-gitops.md`](../argocd-gitops.md).

## 층 지도

아래 층부터 쌓인다. 각 층은 바로 아래 층이 있어야 만들어진다.

| 층 | 만드는 주체 | 소유하는 것 | 확인 |
| --- | --- | --- | --- |
| ① VM | `podman machine` | podman machine(rootful) | `podman machine list` |
| ② 레지스트리 | `podman run` | 컨테이너 `kind-registry` · 볼륨 `kind-registry-data` | `podman ps -a --filter name=kind-registry` |
| ②' 외부 S3 | `podman compose` | 컨테이너 `seaweedfs` · `./seaweedfs/data` · 버킷 3개 | `podman inspect -f '{{.State.Health.Status}}' seaweedfs` |
| ③ 클러스터 | 스택 A `terraform/cluster/kind` | kind 노드 · 포트 매핑 · kubeconfig · 노드의 레지스트리 설정 | `kubectl get nodes` |
| Secret | `kubectl create secret` | `lakehouse-creds` · `catalog-pg-app` | `kubectl get secret` |
| ④ 플랫폼 | 스택 B `terraform/platform` | `helm_release` 3개(ingress-nginx · argo-cd · appset) | `helm list -A` |
| ⑤ 앱 | ArgoCD | `gitops/charts/<app>` | `kubectl get applications -n argocd` |

- **Terraform이 관리하는 것은 ③과 ④**다. ①②②'·Secret은 Terraform state에 없다.
- ②'(오브젝트 스토리지)는 **클러스터와 수명이 독립**이다 — ③을 지우고 다시 만들어도 데이터가 남는다.
- Secret은 비밀값이라 Git(ArgoCD)에 두지 않는다. 그래서 유일하게 손(또는 스크립트)으로 만드는 클러스터 객체다.
- ⑤는 `terraform apply`로 바뀌지 않는다. `gitops/charts/`에 커밋하고 push하면 ArgoCD가 반영한다.

## 올리기

지금 클러스터는 **`lakehouse-next`(8082/8445)** 다. 아래 이름·포트·`-var`는 매번 그대로 쓴다.
PR2 뒤에는 `-var` 없이 기본값(`lakehouse`, 8080/8443)으로 올린다.
명령은 모두 저장소 **루트**에서 실행한다(Terraform state 위치 때문이다 — 아래 「내리기」).

각 단계는 **명령 → 무엇이 생기나 → 확인 → 단축** 순서다.

### 1단계 — ① VM · ② 레지스트리 (podman)

```shell
# ① VM — 실행 중인 podman machine이 없을 때만 만든다. kind의 podman provider는 rootful이 필요하다
podman machine init dagster-k8s --rootful --cpus 8 --memory 26702 --disk-size 93
podman machine start dagster-k8s

# ② 레지스트리 — 푸시한 이미지는 명명 볼륨에 남아 컨테이너를 지워도 보존된다
podman run -d --restart=always \
  -p 127.0.0.1:5001:5000 \
  -v kind-registry-data:/var/lib/registry \
  --name kind-registry docker.io/library/registry:2.8.3
```

- **생기는 것**: Linux VM 하나(컨테이너가 실제로 도는 곳)와 호스트 `127.0.0.1:5001`의 이미지 레지스트리.
  숫자는 [`scripts/k8s-env.sh`](../../scripts/k8s-env.sh)의 기본값이다.
- **확인**: `podman machine list`에서 `Currently running` · `podman ps --filter name=kind-registry`에서 `Up`.
- **단축**: `scripts/k8s-up.sh`. 스크립트는 분기 두 개를 더한다.
  - VM: 이미 실행 중인 머신이 있으면 새로 만들지 않고 그 머신을 쓴다. 그 머신이 rootless면 멈춘다.
    전용 머신 `dagster-k8s`를 강제하려면 `MANAGE_MACHINE=true`.
  - 레지스트리: 상태가 셋이다 — 실행 중이면 그대로, 중지면 `podman start kind-registry`, 없을 때만 `podman run`.

### 2단계 — ②' 외부 S3 (podman compose)

클러스터보다 **먼저** 띄운다 — 3단계의 스택 A가 클러스터를 만들 때 이 컨테이너를 kind 네트워크에 붙인다.
**메인 체크아웃에서만** 실행한다(데이터 경로 `./seaweedfs/`가 체크아웃마다 따로 생긴다).

```shell
# (a) 인증 설정 — 키는 .env 의 ICEBERG_S3_ACCESS_KEY / ICEBERG_S3_SECRET_KEY 와 같은 값
mkdir -p seaweedfs
(umask 077 && cat > seaweedfs/s3.json) <<'EOF'
{"identities":[{"name":"lakehouse","credentials":[{"accessKey":"<ACCESS_KEY>","secretKey":"<SECRET_KEY>"}],
 "actions":["Admin","Read","Write","List","Tagging"]}]}
EOF

# (b) 기동 — compose.yml 의 seaweedfs 서비스(profile storage)
podman compose --profile storage up -d seaweedfs
podman inspect -f '{{.State.Health.Status}}' seaweedfs      # healthy 가 될 때까지 다시 친다

# (c) 버킷 3개 — weed shell 은 실패해도 0으로 끝날 수 있어 목록으로 확인한다
for b in warehouse pg-backup dagster-logs; do
  podman exec seaweedfs sh -c "echo 's3.bucket.create -name $b' | weed shell -master localhost:9333 -filer localhost:8888"
done
podman exec seaweedfs sh -c "echo 's3.bucket.list' | weed shell -master localhost:9333 -filer localhost:8888"
```

- **생기는 것**: S3 API `http://localhost:8333`(루프백만), 데이터 디렉터리 `./seaweedfs/data`, 버킷
  `warehouse`·`pg-backup`·`dagster-logs`.
- **확인**: 위 `s3.bucket.list` 출력에 버킷 3개가 있다.
- **단축**: `scripts/storage-up.sh` — **이 단계는 단축을 권장한다.** `.env`에서 키를 읽어 `s3.json`을 만들고
  (값을 화면·히스토리에 남기지 않는다), `AWS_*`와 `ICEBERG_S3_*`가 다르면 멈추고, 키가 바뀌었으면 restart한다.
  kind 네트워크가 이미 있으면 (d)까지 한다.
- (d) **kind 네트워크 연결**은 3단계의 스택 A가 한다. 클러스터가 있는 상태에서 `seaweedfs` 컨테이너를
  **재생성**했다면(설정 변경 후 `up` 등) 연결이 풀리므로 손으로 다시 붙인다(`restart`는 연결을 유지한다).

  ```shell
  podman network connect --alias seaweedfs-ext kind seaweedfs
  ```

### 3단계 — ③ 클러스터 (Terraform 스택 A)

```shell
terraform -chdir=terraform/cluster/kind init
terraform -chdir=terraform/cluster/kind plan \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445
terraform -chdir=terraform/cluster/kind apply \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445

export KUBECONFIG=~/.kube/lakehouse-next.config   # 스택 A가 쓴 클러스터 전용 kubeconfig — 이후 kubectl 전부가 쓴다
```

- **`plan`에서 읽을 것** — 새로 만드는 리소스는 4개다([`main.tf`](../../terraform/cluster/kind/main.tf)).

  | 리소스 | 하는 일 |
  | --- | --- |
  | `kind_cluster.this` | 노드 컨테이너 `lakehouse-next-control-plane` · 호스트 포트 매핑(80→8082, 443→8445) · kubeconfig 파일 |
  | `terraform_data.registry_certs` | 노드 containerd가 `localhost:5001`을 `kind-registry:5000`으로 보내게 한다 |
  | `terraform_data.registry_network` | 레지스트리 컨테이너를 kind 네트워크에 붙인다 |
  | `terraform_data.seaweedfs_network` | `seaweedfs`를 kind 네트워크에 별칭 `seaweedfs-ext`로 붙인다(없으면 경고만) |

  `data.external.runtime`은 만들지 않고 읽기만 한다 — PATH에서 잡히는 런타임이 `podman`인지 보는 precondition이다.
- **확인**:

  ```shell
  terraform -chdir=terraform/cluster/kind output   # kubeconfig_path · kube_context(kind-lakehouse-next)
  kubectl get nodes                                # lakehouse-next-control-plane  Ready
  kubectl get pods -A                              # kube-system · local-path-storage 파드
  ```

- `kubectl`에 옵션을 붙이는 대신 `KUBECONFIG`를 export한다(zsh는 `$K` 형태의 명령 변수를 쪼개지 않는다).
- 포트 매핑은 **생성 시점에만** 정해진다. 포트를 바꾸려면 destroy → 새 `-var`로 apply.

#### 스택 A가 `kind_cluster` 말고 하는 일 — 손으로 하기

위 `terraform_data` 3개는 `local-exec`로 호스트의 podman을 부른다. 그래서 ③은 ①에 묶여 있다.
손으로 하면 다음과 같다.

```shell
# registry_certs
podman exec lakehouse-next-control-plane mkdir -p /etc/containerd/certs.d/localhost:5001
podman exec -i lakehouse-next-control-plane \
  sh -c "cat > '/etc/containerd/certs.d/localhost:5001/hosts.toml'" \
  < terraform/cluster/kind/registry/hosts.toml

# registry_network — 이미 연결돼 있으면 스택 A는 건너뛴다
podman network connect kind kind-registry

# seaweedfs_network — 컨테이너가 없거나 이미 연결돼 있으면 스택 A는 건너뛴다
podman network connect --alias seaweedfs-ext kind seaweedfs
```

- 이 셋은 **클러스터가 새로 만들어질 때만** 다시 돈다(`registry_certs`는 `hosts.toml`이 바뀔 때도).
  노드 안의 설정이 지워져도 Terraform state는 그 사실을 모르므로 `plan`에 차이가 나오지 않는다.
- precondition은 PATH의 런타임 이름만 본다. VM이 멈춰 있어도 통과하므로 `podman machine list`로 따로 확인한다.

### 4단계 — Secret (kubectl)

카탈로그 Postgres(5단계 뒤 ArgoCD가 만든다)와 Spark·Flink 파드가 읽는 비밀값이다.
ArgoCD보다 먼저 두는 편이 깔끔하지만 늦어도 된다 — CNPG가 Secret이 생길 때까지 기다린다.

```shell
kubectl -n default create secret generic lakehouse-creds \
  --from-literal=s3-access-key='<ICEBERG_S3_ACCESS_KEY>' \
  --from-literal=s3-secret-key='<ICEBERG_S3_SECRET_KEY>'

kubectl -n default create secret generic catalog-pg-app --type=kubernetes.io/basic-auth \
  --from-literal=username=iceberg \
  --from-literal=password='<카탈로그 비밀번호>'
```

- `lakehouse-creds`의 값은 2단계 `s3.json`과 **같은 키**다.
  다르면 파드는 뜨는데 `load_table`이 `ACCESS_DENIED`로 죽는다.
- `catalog-pg-app`의 `username`은 CNPG Cluster CR의 `owner`
  ([`catalog-postgres/templates/cluster.yaml`](../../gitops/charts/catalog-postgres/templates/cluster.yaml))와 같아야 한다.
- 이미 있으면 `create`는 실패한다. 값을 바꾸려면 지우고 다시 만들되, 회전은 Secret·DB 롤·`.env`·워크로드를
  한 벌로 한다([`conventions/k8s/cnpg.md`](../conventions/k8s/cnpg.md)).
- **확인**: `kubectl get secret -n default`에 두 이름이 있다.
- **단축**: `CLUSTER_NAME=lakehouse-next ./scripts/k8s-secrets.sh` — **이 단계도 단축을 권장한다**(값이 셸 히스토리에
  남지 않는다). 스크립트는 `.env`에서 S3 키를 읽고, 위 `owner` 일치를 먼저 검사하고, **없을 때만** 만든다(get‖create).
  `PHYSIONET_*`가 있으면 `physionet-creds`도 만든다.
  `CLUSTER_NAME`을 빼면 스크립트가 `KUBECONFIG`를 `~/.kube/lakehouse.config`로 **덮어써**
  ([`k8s-env.sh`](../../scripts/k8s-env.sh)) 컨텍스트 검사에서 멈춘다.

### 5단계 — ④ 플랫폼 (Terraform 스택 B)

```shell
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main
terraform -chdir=terraform/platform apply \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main
```

- **`plan`에서 읽을 것** — `helm_release` 3개가 이 순서로 생긴다(각각 `wait = true`라 Ready까지 기다린다).

  | 리소스 | 네임스페이스 | 하는 일 |
  | --- | --- | --- |
  | `helm_release.ingress_nginx` | `ingress-nginx` | 호스트 8082/8445 → 클러스터 Ingress 입구 |
  | `helm_release.argo_cd` | `argocd` | ArgoCD 본체 |
  | `helm_release.appset` | `argocd` | ApplicationSet `apps` — `var.apps`의 원소마다 Application 하나를 만든다 |

- **확인**:

  ```shell
  helm list -A                                  # ingress-nginx · argo-cd · appset
  kubectl get pods -n ingress-nginx             # 전부 Running
  kubectl get pods -n argocd                    # 전부 Running
  kubectl get applicationset -n argocd          # apps
  ```

- 스택 B가 어느 클러스터에 붙을지는 위 두 `-var`(kubeconfig·context)가 정한다 — 스택 A의 `output`과 같은 값이다.
- 기능 브랜치를 클러스터에서 검증할 때만 `target_revision=<브랜치>`로 바꾸고, 그 전에 브랜치를 push한다.

### 6단계 — ⑤ 앱 수렴 (ArgoCD)

여기서부터는 명령을 치지 않는다. ApplicationSet이 만든 Application이 `gitops/charts/<app>`을 Git에서 읽어
클러스터에 적용한다.

```shell
kubectl get applications -n argocd                # 전부 Synced · Healthy
kubectl get pods -n cert-manager                  # 앱마다 자기 네임스페이스에 파드가 뜬다
kubectl get pods -n cnpg-system
kubectl get clusters.postgresql.cnpg.io -n default  # catalog-postgres  (Secret 4단계가 있어야 진행)
kubectl get svc seaweedfs -n default              # ExternalName → seaweedfs-ext (2단계의 별칭)

terraform -chdir=terraform/platform output -raw argocd_url
terraform -chdir=terraform/platform output -raw argocd_initial_admin_password_command
```

- **통과 기준**: Application 수 = [`terraform/platform/variables.tf`](../../terraform/platform/variables.tf)의
  `apps` 원소 수, 전부 `Synced` · `Healthy`. apply 직후엔 몇 분 걸린다.
- `Healthy`인데 `OutOfSync`가 남으면 `argocd app diff --core <앱>`으로 필드를 확인한다
  ([`argocd-gitops.md`](../argocd-gitops.md) §5).
- 오퍼레이터 변경은 `terraform apply`가 아니라 `gitops/charts/` 커밋 → push로 반영된다.

## 내리기

위에서부터 거꾸로 내린다. Terraform이 만든 것은 **`destroy`로 먼저** 내린다 — `kind delete cluster`로 지우면
Terraform state가 남아 다음 `apply`가 꼬인다.

```shell
# ④ → ③ (Secret은 클러스터와 함께 사라진다)
terraform -chdir=terraform/platform destroy \
  -var kubeconfig_path=~/.kube/lakehouse-next.config -var kube_context=kind-lakehouse-next \
  -var http_host_port=8082 -var target_revision=main
terraform -chdir=terraform/cluster/kind destroy \
  -var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445

# ② 레지스트리 — 컨테이너만 지운다. 볼륨 kind-registry-data(푸시한 이미지)는 남는다
podman rm -f kind-registry

# ① VM — 필요할 때만. <머신>은 지금 실행 중인 머신(podman machine list)
podman machine stop <머신>
podman volume rm -f kind-registry-data        # 삭제까지 할 때만 — 푸시한 이미지가 사라진다
podman machine rm -f <머신>                   #   〃 VM 안의 데이터가 모두 사라진다
```

- ②'(SeaweedFS)는 클러스터와 수명이 독립이라 여기서 내리지 않는다.
- `<머신>`은 ①에서 기존 머신을 재사용했다면 `dagster-k8s`가 아니다.
- **단축**: Terraform destroy 뒤 `CLUSTER_NAME=lakehouse-next scripts/k8s-down.sh`.

  | 명령 | 하는 일 |
  | --- | --- |
  | `scripts/k8s-down.sh` | kind 클러스터가 남아 있으면 삭제 + 레지스트리 컨테이너 삭제 |
  | `STOP_MACHINE=true scripts/k8s-down.sh` | + VM 중지 |
  | `REMOVE_MACHINE=true scripts/k8s-down.sh` | + 레지스트리 볼륨·VM 삭제(데이터 소멸) |

  대상 머신은 실행 중인 머신이고(`MANAGE_MACHINE=true`면 `MACHINE_NAME`), 실행 중인 대상이 없으면
  **아무것도 지우지 않고** 실패한다.
- tfstate는 `terraform/*/terraform.tfstate`에 생긴다. 그래서 Terraform은 **저장소 루트에서만** 실행한다
  ([`conventions/terraform.md`](../conventions/terraform.md) §4).
- worktree에서 apply했다면 그 worktree를 지우기 전에 state를 루트로 옮기고,
  **apply 때와 같은 `-var`로** `plan`해 `No changes.`를 확인한다. `-var`를 빼면 기본값과 비교돼 교체가 나온다.
