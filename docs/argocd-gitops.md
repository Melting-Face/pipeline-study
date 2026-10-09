# ArgoCD GitOps 전환 — 설계

> **상태**: 📝 **설계(spec) — 사용자 리뷰 대기**.
> 구현은 이 문서 승인 → 구현 계획(writing-plans) 승인 뒤 단계별 PR로 한다.
> **동기**: 자매 저장소 `argocd-study`의 GitOps 패턴을 이 저장소의 로컬 K8s 스택에 이식해
> **두 저장소의 운영 방식을 통일**한다(학습·패턴 통일).
> **성공 기준**: **`git push`만으로 클러스터가 수렴한다** — 빈 클러스터는 `terraform apply` 2회 + Secret 생성 뒤
> 사람의 `kubectl apply` 없이 전 Application이 Synced·Healthy.
> **연관**: 현행 스택 분할 [architectures/terraform.md](architectures/terraform.md), 부트스트랩 [setup.md](setup.md) §3,
> K8s 규칙 [conventions/k8s.md](conventions/k8s.md), 자원 [resource-sizing.md](resource-sizing.md),
> 이행 로드맵 [redesign.md](redesign.md).

## 1. 배경 — 무엇을 바꾸나

현행 로컬 클러스터 `lakehouse`는 **세 주체가 번갈아** 세운다([setup.md](setup.md) §3).

| 층 | 현행 주체 | 비고 |
| --- | --- | --- |
| 클러스터·레지스트리·ingress-nginx | `k8s-up.sh` | `kind_cluster` import 불가가 셸 유지 근거였다 |
| cert-manager·Barman 플러그인·네임스페이스 | `k8s-operators.sh` | 원격 매니페스트 `kubectl apply` |
| 오퍼레이터 3종·플랫폼 매니페스트·Dagster | `terraform/lakehouse-platform/` | 빈 클러스터는 CRD 해석 때문에 **2단 apply** |
| SeaweedFS·CNPG Cluster·Secret·버킷 | `k8s-poc-storage.sh` | 데이터 보유 층 |
| Spark Connect·Flink 세션 | 수동 `kubectl` | 온디맨드 |

[architectures/terraform.md](architectures/terraform.md)는 A(cluster)·B(data)를 **셸에 남긴다**고 결정했다 —
`kind_cluster`가 import를 지원하지 않아 Terraform 채택이 곧 클러스터 재생성이고, 그 비용이 데이터 재적재이기 때문이다.
**이 설계는 그 결정을 뒤집는다.** 근거는 사용자 결정 두 가지다: ⓐ 클러스터를 Terraform으로 **재생성**한다
ⓑ 기존 데이터는 **폐기하고 원천에서 재적재**한다. 재생성 비용을 받아들였으므로 import 불가는 더 이상 장애가 아니다.

## 2. 결정 요약

| # | 결정 | 근거 |
| --- | --- | --- |
| D1 | ArgoCD가 **플랫폼(오퍼레이터)과 데이터 저장소, 이후 앱**까지 소유한다 | 「push만으로 수렴」은 옮긴 층에서만 성립한다 |
| D2 | 단계는 **① 오퍼레이터 → ② 데이터 저장소 → ③ Airflow**, 단계 = PR | 되돌리기 쉬운(데이터 없는) 층부터 |
| D3 | 클러스터는 **Terraform 스택 A**로 **재생성**, 기존 데이터 폐기 | §1 |
| D4 | ArgoCD는 **그 클러스터 안**, Terraform 스택 B가 설치 | `argocd-study`와 같은 경계 — "Terraform은 ArgoCD까지" |
| D5 | 순서 보장은 **ApplicationSet(List generator) + 재시도 수렴**(§5) | `argocd-study` 구조 재사용 |
| D6 | **Dagster는 새 클러스터에 배포하지 않는다**, k8s 배포 산출물은 삭제 | Airflow 전환 예정 — 실행 경로 없는 파일을 남기지 않는다 |
| D7 | Secret은 **수동 생성·Git엔 이름만**, 온디맨드 컴퓨트는 **ArgoCD 밖** | 저장소가 public / selfHeal이 수동 기동·회수를 되돌린다 |
| D8 | 검증은 **나란히 띄운 신규 클러스터**에서, 교체는 PR2 뒤 | 데이터 폐기 시점을 검증 뒤로 미룬다(§6) |
| D9 | kubeconfig는 **이 저장소 전용 파일**(`~/.kube/<cluster_name>.config`) | `argocd-study`와 같은 격리 — 다른 클러스터를 조용히 가리키지 않는다 |
| D10 | 버전은 **`argocd-study`와 같게 고정** — argo-cd 차트 10.9.6, ingress-nginx 차트 4.15.1 | 같은 VM에서 실측 검증된 짝. 상향은 두 저장소 공동 작업 |

## 3. 목표 구조 — 한 리소스는 한 주인만

```
terraform/cluster/kind/      스택 A — kind 클러스터
                             (cluster_name·호스트 포트 변수, ingress-ready 라벨,
                              containerd가 localhost:5001 레지스트리를 쓰도록 선언,
                              kubeconfig를 전용 파일로 출력)
terraform/platform/          스택 B — ingress-nginx · argo-cd · appset(두 번째 helm_release)
  charts/appset/             ApplicationSet 하나(List generator) — argocd-study에서 이식
  values/argocd.yaml.tftpl
gitops/charts/<app>/         ArgoCD가 sync하는 대상 — 앱 하나 = umbrella 차트 하나
  cert-manager/              ①  jetstack 차트 dependency + 로컬 CA(local-ca.yaml, sync-wave 1)
  cnpg-operator/             ①  cloudnative-pg 차트
  spark-operator/            ①  spark-kubernetes-operator 차트 + 워크로드 정리 RBAC
  flink-operator/            ①  flink-kubernetes-operator 차트 + 웹훅·워크로드 RBAC + operator-values
  seaweedfs/                 ②  직접 작성(StatefulSet·Service + 버킷 생성 Job)
  catalog-postgres/          ②  CNPG Cluster CR
  airflow/                   ③  argocd-study 차트 재사용(별도 PR)
```

| 주체 | 소유 |
| --- | --- |
| Terraform A | kind 클러스터 · 전용 kubeconfig 파일 |
| Terraform B | ingress-nginx · ArgoCD · ApplicationSet과 그 앱 목록(`var.apps`) |
| ArgoCD | `gitops/charts/` 하위 전부 |
| 사람 | Secret · 로컬 레지스트리 컨테이너 · Spark Connect·Flink 세션 기동/회수 · 커스텀 이미지 빌드·push |

- **업스트림 차트는 umbrella로 감싼다** — `Chart.yaml` dependency + `Chart.lock` 커밋, `charts/*.tgz`는 gitignore.
  repo-server가 dependency를 받는다. 버전은 정확 고정하고 [`scripts/k8s-env.sh`](../scripts/k8s-env.sh)의 현행 짝을 따른다
  (엔진 버전은 최신이 아니라 Iceberg가 지원하는 짝 — [conventions/k8s.md](conventions/k8s.md)).
- **Flink 오퍼레이터 차트 저장소는 처음부터 `archive.apache.org`로 고정한다.** 차트가 버전이 박힌 배포 디렉터리에서만
  제공되는데, Apache 정책상 PMC는 개발이 끝난 버전의 링크를 `downloads.apache.org`에서 **지우게 돼 있고**
  모든 릴리스는 `archive.apache.org`에 자동 보관된다(출처 R5). `downloads` URL은 언젠가 깨진다.
- **ingress-nginx는 Terraform B가 설치한다** — ArgoCD UI의 노출 경로라 ArgoCD가 자기 진입로를 소유하면 닭-달걀이 된다.
- **ArgoCD 구성**: dex(SSO)·notifications는 **끈다**(로컬 단일 사용자, 읽는 사람 없는 알림은 관측이 아니다).
  남는 5개(application-controller·repo-server·server·applicationset-controller·redis)에 requests/limits를 명시한다
  ([conventions/k8s.md](conventions/k8s.md) §2). repo-server probe `timeoutSeconds: 5`는 `argocd-study`의 실측 교정을 이식한다.
- **로컬 레지스트리**(`localhost:5001`)는 spark-runner·flink-runner 이미지 때문에 유지한다. 클러스터 밖 컨테이너라
  `k8s-up.sh`는 **podman 머신·레지스트리 기동만** 남긴다.
- **Barman 백업 플러그인은 옮기지 않는다** — 백업 배선이 꺼져 있다(YAGNI). 켤 때 `catalog-postgres` 차트에 함께 넣는다.

### 걷어내는 것 · 남기는 것

| 대상 | 처리 | 단계 |
| --- | --- | --- |
| `terraform/lakehouse-platform/` | 철거 — 오퍼레이터는 ArgoCD로, Dagster는 미배포 | ① |
| `scripts/k8s-operators.sh` | 철거 — cert-manager·네임스페이스가 차트·`CreateNamespace`로 | ① |
| `k8s/dagster/*` · `scripts/k8s-dagster.sh` | **삭제**(git 이력으로 복원 가능). 이미지·코드(`dagster/dockerfile.d`)는 호스트 Dagster용으로 유지 | ① |
| `scripts/k8s-up.sh` | 축소 — podman 머신·레지스트리만 | ① |
| `scripts/k8s-env.sh` | `KUBECONFIG` export + 컨텍스트 가드 추가(§6) | ① |
| `scripts/k8s-poc-storage.sh` | 철거 — 데이터 층은 차트로, Secret은 `scripts/k8s-secrets.sh`로 | ② |
| `k8s/spark/spark-connect-server.yaml` · `k8s/flink/*` | 그대로(수동 운영) | — |

## 4. 데이터 보호 — 3겹

데이터는 재적재 가능하다고 정했지만 재적재는 공짜가 아니다(원천 대용량 + dbt 재빌드).
평상시 실수로 잃지 않도록 3겹을 둔다.

| 겹 | 막는 사고 | 수단 | 근거 |
| --- | --- | --- | --- |
| 1. 앱 삭제 | Application 삭제 시 하위 리소스 연쇄 삭제 | ApplicationSet `syncPolicy.preserveResourcesOnDeletion: true` | R2 |
| 2. 상태 리소스 | prune·삭제로 CNPG Cluster·SeaweedFS 소실 | 해당 리소스에 `argocd.argoproj.io/sync-options: Prune=false,Delete=false` | R1 |
| 3. CRD | CRD 삭제 → 그 CRD의 CR이 GC로 연쇄 삭제 | `helm.sh/resource-policy: keep` — ArgoCD가 `Delete=false`와 동등하게 지원 | R3·R4·R6 |

- 겹 3의 차트별 상태: **CNPG**는 CRD 템플릿에 keep이 고정 부착돼 있다(R6).
  **cert-manager**는 `crds.enabled`(기본 false)를 **true로 켜야** CRD가 설치되고, `crds.keep`은 기본 true다(R4).
  **Spark·Flink** 오퍼레이터 차트의 CRD keep 여부는 **미확인** —
  구현 시 `helm template`으로 확인하고, 없으면 values·템플릿으로 `Delete=false`를 주입한다.
- ⚠️ keep은 문서상 **`Delete=false`와 동등**이고 `Prune=false`와의 관계는 문서에 없다(R3).
  겹 3은 "삭제 방지"까지만 보증한다.
- ⚠️ **겹 1이 List 원소 제거에도 적용되는지는 문서에 명시가 없다**(R2 — 문서는 Application 삭제만 말한다).
  §7 관문 ③이 이것을 **실측으로 판정**한다. 판정 전까지 `var.apps`에서 **데이터 층 앱을 빼지 않는다**.
- SeaweedFS의 PVC는 StatefulSet `volumeClaimTemplates`가 만들며 기본 보존(Retain)이다 — ArgoCD 추적 대상이 아니다.

## 5. sync 정책 — 앱 사이는 재시도, 앱 안은 wave

ApplicationSet template의 `syncPolicy`를 **모든 앱에 같게** 건다. 앱별로 갈리지 않으므로 List 원소 스키마는
`argocd-study`와 같은 `name·path·namespace` 셋으로 유지된다.

```yaml
syncPolicy:
  automated:
    selfHeal: true
    prune: true
  syncOptions:
    - CreateNamespace=true
    - ServerSideApply=true              # 큰 CRD의 annotation 크기 한도(262144 bytes) 회피 (R1)
    - SkipDryRunOnMissingResource=true  # 아직 없는 리소스 타입은 dry-run 생략 (R1)
  retry:
    limit: 10
    backoff:
      duration: 10s
      factor: 2
      maxDuration: 3m
```

- **앱 사이 — 재시도 수렴**: CR 앱(예: `catalog-postgres`)이 CRD보다 먼저 sync되면 실패하고 재시도한다.
  오퍼레이터 앱이 CRD를 깔면 다음 재시도에서 성공한다. 순서가 **확정적이지 않은 대가**로 검증은
  **최종 상태**(전부 Synced·Healthy)로만 판정하고, 소요 시간은 임계값 없이 실측만 남긴다(§7 관문 ①).
  기준선이 없는 값에 임계를 먼저 박지 않는다.
- **앱 안 — sync-wave**: 같은 앱 안에서 웹훅이 준비돼야 받아들여지는 리소스는 wave로 뒤에 둔다.
  `cert-manager` 앱의 로컬 CA(Issuer·Certificate)는 `argocd.argoproj.io/sync-wave: "1"` —
  wave 0(cert-manager 본체)이 Healthy가 된 뒤 적용된다. 재시도에만 기대는 것보다 확정적이다.
  Flink 웹훅 RBAC 등 같은 형태가 더 있는지는 구현 시 `helm template`으로 판단한다.
- **기각한 대안**: app-of-apps + **앱 간** sync-wave(순서는 확정적이나
  `argocd-study`가 걷어낸 루트 Application 구조로 돌아간다),
  ApplicationSet Progressive Sync(별도 활성화가 필요한 기능).
- syncPolicy는 **블록 스타일**로 쓴다 — flow 스타일은 영구 drift를 낸다(`argocd-study` appset 템플릿 주석).
- `ignoreDifferences`는 **비워 두고 시작**한다. 반복 OutOfSync가 관측되면 그 필드만 추가한다.

### 추적할 리비전

- `var.target_revision`의 **기본값은 `main`**. PR 검증 중에는 `-var target_revision=<기능 브랜치>`로만 넘긴다 —
  `tfvars`에 쓰지 않으므로 머지 뒤 되돌리기를 잊어도 다음 `apply`가 기본값으로 돌아간다.
- 저장소가 public이라 ArgoCD는 **무인증으로 fetch**한다. 레포 자격증명은 두지 않는다.

## 6. 클러스터 · kubeconfig · Secret · 부트스트랩

**클러스터 이름과 포트** — 스택 A 변수 `cluster_name`(기본 `lakehouse`)·호스트 포트(기본 8080/8443).

| 시기 | 클러스터 | 포트 | kubeconfig |
| --- | --- | --- | --- |
| PR1·PR2 검증 | `lakehouse-next`(`-var`) | 8082/8445 | `~/.kube/lakehouse-next.config` |
| 교체 뒤 | `lakehouse`(기본값) | 8080/8443 | `~/.kube/lakehouse.config` |

- 8081/8444는 `argocd-study` 클러스터 몫이다. 검증 중에는 그 클러스터를 **내려** VM 위 동시 클러스터를 최대 2개로 둔다
  (그쪽은 상태 데이터가 없어 Terraform으로 재생성 비용이 낮다). 급소가 CPU 축이다([resource-sizing.md](resource-sizing.md)).
- **교체**: PR2 관문 통과 → 기존 `lakehouse` 삭제(비가역) → `lakehouse-next` 삭제 → 스택 A·B를 기본값으로 `apply`.
  문서·엔드포인트에 박힌 `*.localtest.me:8080`은 그대로 유효하다.

**kubeconfig** — 스택 A가 `~/.kube/<cluster_name>.config`에 쓰고 `kubeconfig_path`·`kube_context`를 출력한다.
스택 B는 같은 값을 **변수 기본값으로 재선언**한다(`argocd-study`의 기반 계약 방식, `terraform_remote_state` 미사용).
provider `config_context`는 고정하고 `kind-` 접두를 검증한다.

- `scripts/k8s-env.sh`가 `KUBECONFIG="${KUBECONFIG_PATH:-$HOME/.kube/${CLUSTER_NAME}.config}"`를 export한다.
  스크립트는 이미 이 파일을 source하므로 따라오고, 사람은 `source scripts/k8s-env.sh` 한 줄로 맞춘다.
- **컨텍스트 가드**: `k8s-env.sh`는 현재 컨텍스트가 `kind-${CLUSTER_NAME}`이 아니면 **멈춘다**.
  옵션 없는 `kubectl`이 `~/.kube/config`의 다른 클러스터를 조용히 가리키는 것을 막는다.

**Secret** — `scripts/k8s-secrets.sh`(②) 하나가 만든다. **`get || create`** 형태 — 이미 있으면 건드리지 않아
회전되지 않는다(`create --dry-run | apply`는 매 실행 값을 덮는다). 대상은 `lakehouse-creds`·`catalog-pg-app`·
`physionet-creds`(선택). Dagster 미배포이므로 `dagster-meta-pg-app`은 만들지 않는다.

**네임스페이스** — 데이터 층은 **`default` 유지**(Spark·Flink 매니페스트와 엔드포인트가 전제),
오퍼레이터는 현행처럼 각자의 ns.

**부트스트랩**

```
scripts/k8s-up.sh                         # podman 머신 · 레지스트리
terraform -chdir=terraform/cluster/kind apply
terraform -chdir=terraform/platform apply
source scripts/k8s-env.sh                 # KUBECONFIG + 컨텍스트 가드
scripts/k8s-secrets.sh                    # ② 이후. 순서 강제 아님 — 늦으면 CNPG가 Degraded였다가 재시도로 수렴
```

## 7. 검증

**정적 게이트(CI, 실인프라 미접속)**

- `gitops/charts/*`: `helm dependency build` → `helm template`. **필수 체크인 lint 잡 안의 스텝**으로 둔다 —
  필수가 아닌 잡의 빨간불은 읽는 사람이 없으면 무시다. helm은 액션이 아니라 러너에 직접 설치하고 SHA256으로 대조한다
  (기존 terraform 설치와 같은 방식). 대가로 lint 잡이 업스트림 차트 저장소에 접속한다 —
  외부 장애가 머지를 막는 위험은 수용.
  `helm lint --strict`는 `required` 누락을 경고로만 내므로 판정은 `template`.
- Terraform: CI의 스택 탐색 루프가 **한 단계 깊이**(`terraform/*/versions.tf`)만 봐서 `terraform/cluster/kind`가
  **에러 없이 빠진다**. 루프를 재귀 탐색으로 고치고, 로그의 **검증한 스택 수**가 기대값과 같은지 본다
  (0건 가드는 "하나라도 찾았다"만 보므로 일부 누락을 못 잡는다).
- **음성 대조**(새로 건 게이트는 일부러 위반시킨다):
  ⓐ `var.apps`에 `gitops/charts/` 밖 경로 → validation 거부 ⓑ 필수 값 누락 → `helm template` 실패
  ⓒ `KUBECONFIG` 미설정 셸에서 스크립트 실행 → 컨텍스트 가드가 멈춘다.

**수동 관문(실인프라, 단계마다 1회)**

| # | 관문 | 판정 |
| --- | --- | --- |
| ⓪ | health 판정 생존(②부터) | 일부러 실패하는 CR(없는 Secret을 참조하는 CNPG Cluster)이 **Healthy로 표시되지 않는다**. 표시되면 `resource.customizations.health`를 추가 |
| ① | 빈 클러스터 수렴 | **Application 객체 수 == `var.apps` 원소 수**이고 전부 Synced·Healthy. 소요 시간은 실측해 볼트에 둔다 |
| ② | selfHeal | 오퍼레이터 Deployment를 수동 scale → Git 상태로 복원 |
| ③ | 삭제 보호(§4 미확인 판정) | 데이터 없는 `spark-operator`를 `var.apps`에서 잠시 빼고 Deployment·CRD가 남는지 본다 |
| ④ | 기능 스모크(② 단계 뒤) | Spark Connect 수동 기동 → `scripts/spark_connect_smoke.py` 통과, Flink 세션의 Iceberg 읽기 |

관문 ⓪은 관문 ①의 전제다 — CR의 health를 ArgoCD가 판정할 줄 모르면 생성 즉시 Healthy로 보일 수 있어
(미확인) ①이 "Postgres가 안 떴는데 통과"한다.

**비가역 지점**: 기존 `lakehouse` 삭제와 스택 A·B의 첫 `apply`는 실행 전
`reviewer` 보안 체크리스트 + 사용자 승인([risk.md](risk.md) §4).

**자원**: [resource-sizing.md](resource-sizing.md) 배분 표에서 Dagster 행을 빼고 ArgoCD 5개 컴포넌트 행을 더한다.
값은 차트 기본값을 확인해 정하고, 실측은 관문 ①에서 재 볼트에 둔다.

## 8. 단계와 PR

| PR | 범위 | 완료 조건 |
| --- | --- | --- |
| 설계 | 이 문서 + PR1 구현 계획(`docs/plans/argocd-gitops-pr1.md`) | 사용자 리뷰 |
| PR1 | 스택 A·B, appset, ① 오퍼레이터 차트 4종, CI(helm 스텝·재귀 탐색), `k8s-env.sh` 가드, 철거 대상(§3), 문서 갱신 | 정적 게이트 + 관문 ①②③. 머지 시 계획 문서 삭제 |
| PR2 | ② `seaweedfs`·`catalog-postgres` 차트, `k8s-secrets.sh`, `k8s-poc-storage.sh` 철거, 클러스터 교체 | 관문 ⓪①④ |
| PR3 | ③ Airflow — Airflow 이행 미션과 합류 | 별도 설계 |

PR마다 함께 갱신할 단일 출처: [setup.md](setup.md) §3(부트스트랩), [architectures/terraform.md](architectures/terraform.md)(스택 분할),
신규 `architectures/argocd.md`(채택 ✅·대안), [conventions/k8s.md](conventions/k8s.md) §7(packaging은 Helm — 비로소 실재와 일치),
[resource-sizing.md](resource-sizing.md), `CLAUDE.md` 인프라 요약 · `AGENTS.md`.

## 9. 받아들인 결과 · 열린 위험 · 기각

- **Airflow(③)가 서기 전까지 클러스터 안에서 도는 오케스트레이터가 없다.**
  데이터 재적재 경로(`raw_assets.py` → 적재 → dbt)는 Dagster 정의라, 그 사이 재적재가 필요하면
  **호스트 Dagster**(`host-dagster` profile)로 돌린다. 이 경로가 새 클러스터의 SeaweedFS·카탈로그에 붙는지는
  PR2 관문 ④에서 확인한다.
- **수렴 순서가 비결정적**이라 첫 sync에서 일시적 Degraded·재시도 로그가 정상이다 — 관문 ①은 최종 상태로만 판정한다.
- **겹 1의 List 원소 제거 적용 여부 미확인**(§4)은 관문 ③ 전까지 데이터 층 앱 제거 금지로 완화한다.
- **기각·보류**(빠뜨린 것과 구분하려고 적는다):
  AppProject 분리 — 보류. 사용자·저장소가 하나라 격리 대상이 없다. `sourceRepos`를 좁히는 효과는 ③ 단계에서 재판단.
  Sync Windows·Notifications·SSO·RBAC — 기각. 로컬 단일 사용자이고, 읽는 사람 없는 알림은 관측이 아니다.

## 참고 출처

`argocd-study` (자매 저장소)

- `docs/superpowers/specs/2026-10-07-applicationset-image-cd-design.md` §3 <!-- date-ok -->
  — 소유권 표, "Terraform이 ArgoCD를 소유"
- `wiki/argocd-bootstrap.md` — CRD 선행 때문에 appset을 두 번째 `helm_release`로 분리한 근거
- `terraform/platform/charts/appset/templates/applicationset.yaml` — List generator·블록 스타일 syncPolicy
- `terraform/platform/values/argocd.yaml.tftpl` — repo-server probe 타임아웃 교정

외부 1차 출처(A등급, stable 문서)

- **R1** Argo CD, *Sync Options* — §Server-Side Apply · §Skip Dry Run for New Custom Resources Types ·
  §No Prune Resources · §No Resource Deletion. https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
- **R2** Argo CD, *ApplicationSet — Controlling Resource Modification*, §Preserving Resources on Deletion.
  https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Controlling-Resource-Modification/
- **R3** Argo CD, *Helm* (user guide) — Helm 주석 지원 표, `helm.sh/resource-policy: keep`.
  https://argo-cd.readthedocs.io/en/stable/user-guide/helm/
- **R4** cert-manager, Helm chart `values.yaml` — `crds.enabled`·`crds.keep`.
  https://raw.githubusercontent.com/cert-manager/cert-manager/master/deploy/charts/cert-manager/values.yaml
- **R5** Apache Software Foundation, *Release Distribution Policy*. https://infra.apache.org/release-distribution.html
- **R6** CloudNativePG charts, `charts/cloudnative-pg/templates/crds/crds.yaml`·`values.yaml`(`crds.create`).
  https://github.com/cloudnative-pg/charts

교차 검토: `argocd-expert` 스킬(C등급 — 모범사례 목록). §5 앱 내부 wave, §7 관문 ⓪, §9 기각 목록의 계기가 됐고
판단 근거는 위 A등급 출처와 이 저장소 규칙이다.
