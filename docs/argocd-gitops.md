# ArgoCD GitOps 전환 — 설계

> **상태**: 📝 **설계(spec) — 승인됨**. 구현은 단계별 PR로 한다(§8).
> **동기**: 자매 저장소 `argocd-study`의 GitOps 패턴을 이 저장소의 로컬 K8s 스택에 이식해
> **두 저장소의 운영 방식을 통일**한다(학습·패턴 통일).
> **성공 기준**: **`git push`만으로 클러스터가 수렴한다** — 빈 클러스터는 `terraform apply` 2회 + Secret 생성 뒤
> 사람의 `kubectl apply` 없이 전 Application이 Synced·Healthy.
> **연관**: 현행 스택 분할 [architectures/terraform.md](architectures/terraform.md), 부트스트랩 [setup.md](setup.md) §3,
> K8s 규칙 [conventions/k8s.md](conventions/k8s.md), 자원 [resource-sizing.md](resource-sizing.md),
> 이행 로드맵 [redesign.md](redesign.md).

## 1. 배경 — 무엇을 바꾸나

전환 전 로컬 클러스터 `lakehouse`는 **세 주체가 번갈아** 세웠다(철거 전 [setup.md](setup.md) §3 기준).

| 층 | 전환 전 주체 | 비고 |
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
| D5 | 순서 보장은 **ApplicationSet(git files generator) + RollingSync(tier) + 같은 tier 안 재시도 수렴**(§5) — *개정됨*(§5 「D5 개정 기록」) | 앱 추가 = 커밋 하나, 앱 사이 순서를 선언으로 |
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
  charts/appset/             ApplicationSet 하나(git files generator + RollingSync) — 앱 목록은 갖지 않는다
  values/argocd.yaml.tftpl
gitops/apps/<app>/config.yaml  앱 목록의 정본 — name·namespace·tier·source (아래 §앱 추가·제거)
gitops/charts/<app>/         ArgoCD가 sync하는 대상 — 앱 하나 = umbrella 차트 하나
  cnpg-operator/             ①  cloudnative-pg 차트
  spark-operator/            ①  spark-kubernetes-operator 차트 + 워크로드 정리 RBAC
  flink-operator/            ①  flink-kubernetes-operator 차트(webhook 끔) + 워크로드 RBAC + operator-values
  storage-external/          ②  직접 작성(ExternalName Service — 클러스터 밖 SeaweedFS)
  catalog-postgres/          ②  CNPG Cluster CR
  airflow/                   ③  Airflow umbrella 차트(PR3-B) — 메타 DB는 CNPG 별도 롤·DB, 이미지는 images/airflow/
```

| 주체 | 소유 |
| --- | --- |
| Terraform A | kind 클러스터 · 전용 kubeconfig 파일 |
| Terraform B | ingress-nginx · ArgoCD · 부트스트랩 ApplicationSet 1개(앱 목록은 갖지 않는다) |
| ArgoCD | `gitops/apps/`가 선언한 Application과 `gitops/charts/` 하위 전부 |
| GitHub Actions | Airflow 커스텀 이미지 빌드·GHCR 발행(`main` 머지가 트리거, 버전은 values 태그 — [conventions/docker.md §3](conventions/docker.md#3-이미지-발행-ghcr)) |
| 사람 | Secret · 로컬 레지스트리 컨테이너 · Flink 세션 기동/회수 · 러너 이미지 빌드·push · 발행을 부르는 PR 머지 |

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

### 앱 목록 이행 — `var.apps` → `gitops/apps/`

목표는 **앱 추가 = git 커밋 하나**(`terraform apply` 없음)다. Terraform B는 ArgoCD와 부트스트랩 ApplicationSet만 남기고,
앱 목록은 ApplicationSet **git files generator**가 `gitops/apps/*/config.yaml`에서 읽는다. 두 단계로 나눈다.

| 단계 | 내용 | 앱 목록의 정본 |
| --- | --- | --- |
| A1 | 설정 파일 7개 + 검사기(`scripts/gitops-apps-check.sh`) 추가. 차트 검사기의 앱명·네임스페이스 출처를 설정 파일로 옮긴다. **클러스터 동작 변화 없음** | `var.apps`(설정 파일은 CI가 같음을 강제하는 사본) |
| A2 | ApplicationSet을 files generator·RollingSync(tier)로 교체, `var.apps` 제거, 검사기의 `var.apps` 대조(g) 삭제 | `gitops/apps/` |

- **A1이 `main`에 먼저 있어야 A2를 apply한다** — `targetRevision: main`이라 파일이 없으면 generator가 0건을 낸다.
  A2는 여기에 `applicationsSync: create-update`를 더해 **generator 결과에서 빠진 Application을 지우지 않게** 한다.
- 설정 파일 스키마와 검사 항목은 [`scripts/gitops-apps-check.sh`](../scripts/gitops-apps-check.sh) 머리 주석이 정본이다.
  `tier`는 앱 사이 순서(`"0"` 오퍼레이터·외부 저장소 → `"1"` 데이터 층 CR → `"2"` 워크로드), `source.type`은
  `umbrella`(기존 방식) 또는 `upstream`(상류 차트 직접 참조 — 새 앱부터)이다.
- **cutover 판정**: 이름이 같은 Application이 갱신(in-place)인지 재생성인지는 문서에 없어 apply 전후
  `metadata.uid`로 판정했고, **in-place였다**(v3.5.3 — 7개 UID 불변, spec 차이는 `automated.enabled: false`뿐).
  apply가 끝난 직후에는 `tier` 라벨이 아직 없다 — 컨트롤러가 재기동 뒤 reconcile해야 붙으므로,
  사후 확인은 AppSet `status.applicationStatus`가 채워진 뒤에 한다(그 전의 「라벨 없음」은 실패가 아니다).

### 앱 추가·제거

**추가는 커밋 하나다 — `terraform apply`가 필요 없다.**

1. `gitops/apps/<앱>/config.yaml`을 만든다. `name`은 디렉터리 이름과 같게, `tier`는 의존하는 앱보다 큰 값으로.
   - `umbrella`: `gitops/charts/<앱>/`에 umbrella 차트를 함께 둔다(`source.path`).
   - `upstream`: `source.repoURL`·`chart`·`targetRevision`(정확 버전)을 적고 **같은 디렉터리에 `values.yaml`** 을 둔다
     — ApplicationSet이 상류 차트 + 이 저장소 `$values` ref의 multi-source Application을 만든다.
     첫 사례는 [`gitops/apps/trino/`](../gitops/apps/trino/)다. ⚠️ upstream 앱의 `values.yaml`은 `gitops-charts-check`의
     **검사 대상이 아니다**(차트 디렉터리가 없다) — requests/limits·Secret 렌더 0건은 핀 버전으로 `helm template`을
     떠서 손으로 본다(선언된 공백).
   - 최상위에 `path` 키를 두지 않는다 — files generator가 설정 파일 위치로 주입하는 예약 파라미터다.
2. 로컬에서 `scripts/gitops-apps-check.sh`(·umbrella면 `scripts/gitops-charts-check.sh`)를 통과시킨다.
   CI도 같은 검사를 한다. ⚠️ 설정 파일 **하나의 오류가 ApplicationSet 생성 전체를 실패**시킨다
   (`argocd appset generate` 드라이런에서 정상 원소까지 함께 실패) — 이 검사기가 막는 것은 그 파일 하나가 아니라
   **모든 앱의 갱신 정지**다.
3. PR 머지 → ApplicationSet이 `main`의 설정 파일을 읽어 Application을 만들고, RollingSync가 tier 순서로 sync한다.
   새 앱은 머지 수십 초~수 분 뒤 생성·sync된다(Trino 실측). ⚠️ **Healthy는 probe 기준**이다 — Ingress를 내는 앱은
   밖에서 한 번 호출해 본다(Trino는 Healthy인 채 프록시 경유 요청을 406으로 거부했다).

**제거는 두 단계다** — 설정 파일을 지워도 `create-update` 때문에 Application은 **남는다**(의도된 보호).
설정 파일 삭제 PR 머지 뒤 `kubectl -n argocd delete application <앱>`으로 손으로 내린다(비가역 — [risk.md](risk.md) §4 게이트).
하위 리소스는 `preserveResourcesOnDeletion`과 리소스 단위 `Delete=false`(§4)가 따로 지킨다.
⚠️ 생성 Application에는 finalizer가 없어 손 삭제도 **연쇄 삭제가 아니다** — 워크로드까지 내리려면 세 번째 단계로
`app.kubernetes.io/instance=<앱>` 라벨 리소스를 손으로 지운다(별도 비가역 게이트).
「Application이 사라졌다」는 「앱이 내려갔다」가 아니다.

### 걷어내는 것 · 남기는 것

| 대상 | 처리 | 단계 |
| --- | --- | --- |
| `terraform/lakehouse-platform/` | 철거 — 오퍼레이터는 ArgoCD로, Dagster는 미배포 | ① |
| `scripts/k8s-operators.sh` | 철거 — cert-manager·네임스페이스가 차트·`CreateNamespace`로 | ① |
| `gitops/charts/cert-manager/` · `gitops/apps/cert-manager/` | 철거(B3 — 유일한 소비자였던 Flink 웹훅을 끔). 재도입 조건은 [conventions/k8s/cnpg.md](conventions/k8s/cnpg.md)(Barman 플러그인) | — |
| `k8s/dagster/*` · `scripts/k8s-dagster.sh` | **삭제**(git 이력으로 복원 가능). 이미지·코드(`dagster/dockerfile.d`)는 호스트 Dagster용으로 유지 | ① |
| `scripts/k8s-up.sh` | 축소 — podman 머신·레지스트리만 | ① |
| `scripts/k8s-env.sh` | `KUBECONFIG` export + 컨텍스트 가드 추가(§6) | ① |
| `scripts/k8s-poc-storage.sh` | 철거 — 데이터 층은 차트로, Secret은 `scripts/k8s-secrets.sh`로 | ② |
| `k8s/spark/spark-connect-server.yaml` | 철거(B2 — 소비자 0) | — |
| `k8s/flink/*` | 그대로(수동 운영) | — |

## 4. 데이터 보호 — 4겹

데이터는 재적재 가능하다고 정했지만 재적재는 공짜가 아니다(원천 대용량 + dbt 재빌드).
평상시 실수로 잃지 않도록 4겹을 둔다(겹 0은 A2에서 files generator와 함께 추가).

| 겹 | 막는 사고 | 수단 | 근거 |
| --- | --- | --- | --- |
| 0. 앱 목록 | 설정 파일 오삭제·generator 0건이 Application 삭제로 번짐 | ApplicationSet `syncPolicy.applicationsSync: create-update`(생성·갱신만, 삭제 안 함) | R2 |
| 1. 앱 삭제 | Application 삭제 시 하위 리소스 연쇄 삭제 | ApplicationSet `syncPolicy.preserveResourcesOnDeletion: true` | R2 |
| 2. 상태 리소스 | prune·삭제로 CNPG Cluster·SeaweedFS 소실 | 해당 리소스에 `argocd.argoproj.io/sync-options: Prune=false,Delete=false` | R1 |
| 3. CRD | CRD 삭제 → 그 CRD의 CR이 GC로 연쇄 삭제 | `helm.sh/resource-policy: keep` — ArgoCD가 `Delete=false`와 동등하게 지원 | R3·R6 |

- 겹 3의 차트별 상태: **CNPG**는 CRD 템플릿에 keep이 고정 부착돼 있다(R6).
  **Spark·Flink** 오퍼레이터 차트는 CRD를 `crds/` 디렉터리로 싣고 어노테이션을 넣을 values 키가 없어,
  이 둘은 겹 3이 **선언된 공백**이다. 근거: 이 둘의 CR(`SparkApplication`·`FlinkDeployment` 등)은 ArgoCD 밖의
  온디맨드 컴퓨트(D7)이고 상태·데이터가 아니다(데이터는 Iceberg에 있다). 남는 방어는 겹 1뿐인데,
  겹 1은 **Application 삭제**만 막는다 — 업스트림 차트 갱신으로 CRD가 사라지는 식의
  **sync 시점 prune**은 이 둘에서 막는 수단이 없다(§9 열린 위험). 겹 1은 관문 ③에서 확인한다.
  검사기는 `tests/expect.yaml`의 `crd_keep_exempt`에 **CRD 이름을 명시**해 면제하고(새 CRD는 면제되지 않는다)
  통과와 구분해 `(b) 면제 N개`로 출력한다. 업스트림 CRD를 복사해 소유하는 안은 범위 밖이라 기각했다.
  재검토 트리거: 두 오퍼레이터의 CR이 상태·데이터를 담게 되거나, 업스트림 차트가 CRD 어노테이션 키를 추가할 때.
  ArgoCD가 Helm `crds/`를 렌더에 포함하는지는 **확인됐다** —
  Spark·Flink CRD 6종이 클러스터에 존재하고 두 Application이 추적한다.
- ⚠️ keep은 문서상 **`Delete=false`와 동등**이고 `Prune=false`와의 관계는 문서에 없다(R3).
  겹 3은 "삭제 방지"까지만 보증한다.
- ⚠️ **겹 1이 List 원소 제거에도 적용되는지는 문서에 명시가 없다**(R2 — 문서는 Application 삭제만 말한다).
  §7 관문 ③ 관측: `spark-operator` 원소를 빼자 Application만 사라지고 Deployment·CRD·RBAC는 **남았다**.
  단 Application에 `resources-finalizer`가 원래 없어(템플릿이 달지 않는다) 비연쇄 삭제가 기본이므로,
  **이 잔존이 겹 1 덕인지는 가르지 못했다**. 그래서 데이터 층 앱은 여전히 `gitops/apps/`에서 **빼지 않는다**.
  (관측 당시는 List generator였다. 지금은 겹 0이 원소 제거 자체를 Application 삭제로 잇지 않는다.)
- SeaweedFS는 클러스터 밖(스택 A 컨테이너)이라 데이터가 ArgoCD·클러스터 수명과 무관하다.
  `storage-external`은 Service 1개뿐이다.

## 5. sync 정책 — 앱 사이는 tier(RollingSync), tier 안은 재시도, 앱 안은 wave

ApplicationSet template의 `syncPolicy`를 **모든 앱에 같게** 건다. 앱별로 갈리는 것은 설정 파일의
`tier`(순서)와 `source.type`(단일/multi-source)뿐이다.

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

- **앱 사이 — tier(RollingSync)**: ApplicationSet `strategy: RollingSync`가 Application 라벨 `tier`로
  `"0"` → `"1"` → `"2"` 단계를 나누고, 앞 단계가 전부 Healthy가 된 뒤 다음 단계를 sync한다(R7).
  컨트롤러 파라미터 `applicationsetcontroller.enable.progressive.syncs: "true"`(`values/argocd.yaml.tftpl`)가
  없으면 strategy가 **에러 없이 무시**되므로 `terraform test`가 렌더를 고정한다.
- **tier 안 — 재시도 수렴**: 같은 tier의 앱끼리는 순서가 없다 — CRD가 아직 없어 실패한 앱은 재시도로 수렴한다.
  검증은 **최종 상태**(전부 Synced·Healthy)로 판정하고, 소요 시간은 임계값 없이 실측만 남긴다(§7 관문 ①).
  기준선이 없는 값에 임계를 먼저 박지 않는다.
- **앱 안 — sync-wave**: 같은 앱 안에서 웹훅이 준비돼야 받아들여지는 리소스는 wave로 뒤에 둔다.
  해당 리소스에 `argocd.argoproj.io/sync-wave: "1"`을 달면 wave 0(웹훅 본체)이 Healthy가 된 뒤 적용된다 —
  재시도에만 기대는 것보다 확정적이다. 지금 wave를 쓰는 앱은 **없다**(유일한 사례였던 cert-manager 로컬 CA는
  B3에서 철거). 새 앱이 같은 형태인지는 추가 시 `helm template`으로 판단하고, 쓰면 `tests/expect.yaml`의
  `kinds_with_wave`로 고정한다.
- **기각한 대안**: app-of-apps + **앱 간** sync-wave(순서는 확정적이나
  `argocd-study`가 걷어낸 루트 Application 구조로 돌아간다).
- syncPolicy는 **블록 스타일**로 쓴다 — flow 스타일은 영구 drift를 낸다(`argocd-study` appset 템플릿 주석).
- `ignoreDifferences`는 **비워 두고 시작**했고, 반복 OutOfSync가 관측된 필드만 추가한다. 첫(현재 유일한) 항목은
  spark-operator·flink-operator의 영구 OutOfSync 때문이다 — 두 차트의 `crds/` CRD 6종이
  `additionalPrinterColumns[].priority: 0`을 선언하는데 API server가 기본값이라 떨어뜨려 diff가 영구히 남는다.
  범위는 **CRD printer-column `priority`** 하나이고, appset 템플릿이 아니라 **ArgoCD 시스템 수준
  (`argocd-cm`, `terraform/platform/values/argocd.yaml.tftpl`의 `configs.cm`)** 에 둔다 — 전 Application에
  균일하게 적용되고 템플릿은 그대로다. 렌더 여부는 `terraform test`가 확인한다.

### D5 개정 기록 — List generator + 재시도 → files generator + RollingSync

- **이전 결정**: List generator(원소 = Terraform `var.apps`) + 앱 사이 재시도 수렴. Progressive Sync는
  「별도 활성화가 필요한 기능」이라 기각했다.
- **바꾼 이유**: ①앱 추가마다 `terraform apply`가 필요했다 — 목표 구조(Terraform은 ArgoCD까지)와 어긋난다.
  ②ApplicationSet 패턴(generator·multi-source·순서 제어) 학습이 이 저장소의 목적이 됐다.
  「별도 활성화」는 파라미터 1줄 + `terraform test` 셀 1개로 비용이 확정돼 기각 사유가 아니게 됐다.
- **수용한 대가**: RollingSync는 **생성 Application의 자동 sync를 강제로 끈다**(R7). v3.5.3에서는 컨트롤러가
  Application에 **`automated.enabled: false`를 덧붙이는 방식**이다 — `selfHeal`·`prune` 값은 그대로 남아
  `kubectl get`으로는 켜진 것처럼 보이므로 `enabled`를 함께 읽는다. git 변경은 **ApplicationSet 컨트롤러가 sync한다**
  (Trino 추가·값 수정 2회 실측 — `initiatedBy: applicationset-controller`),
  **라이브 drift를 git 상태로 되돌리는 selfHeal은 돌지 않는다** — 관문 ②에서 오퍼레이터 Deployment를 scale 0으로
  내리자 수 분 동안 `OutOfSync`로 남고 복원되지 않았다. drift 복구는 손 sync다
  ([setup/local-k8s.md](setup/local-k8s.md) §운영 — 바꾸기·되돌리기).
  D7(온디맨드 컴퓨트는 ArgoCD 밖)이라 수동 기동·회수가 되돌려지지 않는 쪽은 오히려 마찰이 준다.
- **재검토 조건**: ⓐ RollingSync가 생성 Application의 자동 sync를 보존하는 옵션을 제공할 때
  ⓑ 관문 ②에서 drift 미복원이 실제 장애·데이터 오류로 이어졌을 때
  ⓒ 앱 사이 의존이 tier 3단으로 표현되지 않을 때(같은 tier 안 순서가 재시도로 수렴하지 않는 경우 포함).

### 추적할 리비전

- `var.target_revision`의 **기본값은 `main`**. PR 검증 중에는 `-var target_revision=<기능 브랜치>`로만 넘긴다 —
  `tfvars`에 쓰지 않으므로 머지 뒤 되돌리기를 잊어도 다음 `apply`가 기본값으로 돌아간다.
- 저장소가 public이라 ArgoCD는 **무인증으로 fetch**한다. 레포 자격증명은 두지 않는다.

## 6. 클러스터 · kubeconfig · Secret · 부트스트랩

**클러스터 이름과 포트** — 스택 A 변수 `cluster_name`(기본 `lakehouse`)·호스트 포트(기본 8080/8443).

| 시기 | 클러스터 | 포트 | kubeconfig |
| --- | --- | --- | --- |
| PR1·PR2 검증(종료) | `lakehouse-next`(`-var`) | 8082/8445 | `~/.kube/lakehouse-next.config` |
| 교체 뒤(현행) | `lakehouse`(기본값) | 8080/8443 | `~/.kube/lakehouse.config` |

- 8081/8444는 `argocd-study` 클러스터 몫이다. 검증 중에는 그 클러스터를 **내려** VM 위 동시 클러스터를 최대 2개로 둔다
  (그쪽은 상태 데이터가 없어 Terraform으로 재생성 비용이 낮다). 급소가 CPU 축이다([resource-sizing.md](resource-sizing.md)).
- **교체**: PR2 관문 통과 → 기존 `lakehouse` 삭제(비가역) → `lakehouse-next` 삭제 → 스택 A·B를 기본값으로 `apply`.
  문서·엔드포인트에 박힌 `*.localtest.me:8080`은 그대로 유효하다.

**kubeconfig** — 스택 A가 `~/.kube/<cluster_name>.config`에 쓰고 `kubeconfig_path`·`kube_context`를 출력한다.
스택 B는 같은 값을 **변수 기본값으로 재선언**한다(`argocd-study`의 기반 계약 방식, `terraform_remote_state` 미사용).
provider `config_context`는 고정하고 `kind-` 접두를 검증한다.

- `scripts/k8s-env.sh`가 `KUBECONFIG="${KUBECONFIG_PATH:-$HOME/.kube/${CLUSTER_NAME}.config}"`를 export한다.
  스크립트는 이미 이 파일을 source하므로 따라오고, 사람은 `source scripts/k8s-env.sh` 한 줄로 맞춘다.
- **컨텍스트 가드**: `k8s-env.sh`가 정의하는 `require_cluster_context`를 **호출하는 스크립트가**, 현재 컨텍스트가
  `kind-${CLUSTER_NAME}`이 아니면 **멈춘다**(source만으로는 가드가 돌지 않는다 — 클러스터 생성 전에도 source되기 때문).
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
source scripts/k8s-env.sh                 # KUBECONFIG export(가드는 require_cluster_context 호출 스크립트가 건다)
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
  ⓐ 설정 파일에 `gitops/charts/` 밖 경로 → `scripts/gitops-apps-check.sh` 실패 ⓑ 필수 값 누락 → `helm template` 실패
  ⓒ `KUBECONFIG` 미설정 셸에서 스크립트 실행 → 컨텍스트 가드가 멈춘다.

**수동 관문(실인프라, 단계마다 1회)**

| # | 관문 | 판정 |
| --- | --- | --- |
| ⓪ | health 판정 생존(②부터) | 일부러 실패하는 CR(없는 Secret을 참조하는 CNPG Cluster)이 **Healthy로 표시되지 않는다**. 표시되면 `resource.customizations.health`를 추가 |
| ① | 빈 클러스터 수렴 | **Application 객체 수 == `gitops/apps/*/config.yaml` 수**이고 전부 Synced·Healthy. 소요 시간은 실측해 볼트에 둔다 |
| ② | selfHeal | 오퍼레이터 Deployment를 수동 scale → Git 상태로 복원되는지. RollingSync 아래에서는 **복원되지 않는 것이 기대값**이다(§5) — 관측 뒤 손으로 원복. 전제는 표 아래 |
| ③ | 삭제 보호(§4 미확인 판정) | 데이터 없는 `spark-operator`의 설정 파일을 브랜치에서 빼고 `-var target_revision=<브랜치>`로 apply → Application이 **남는지**(겹 0) |
| A2 | generator 교체(cutover) | apply 전 `argocd appset generate`로 생성 결과를 라이브 spec과 대조, apply 전후 Application `metadata.uid` 동일(재생성 없음) |
| ④ | 기능 스모크(② 단계 뒤) | k8s Trino `dbt debug`(접속 OK)·Iceberg 조회, Flink 세션의 Iceberg 읽기(구 Spark Connect 스모크는 철거) |

관문 ⓪은 관문 ①의 전제다 — CR의 health를 ArgoCD가 판정할 줄 모르면 생성 즉시 Healthy로 보일 수 있어
(미확인) ①이 "Postgres가 안 떴는데 통과"한다.

관문 ②는 **그 오퍼레이터의 잡 CR(`SparkApplication`·`FlinkDeployment` 등)이 0건일 때만** 한다 — 오퍼레이터 자신은
데이터를 갖지 않지만 대용량 데이터를 Iceberg로 정형화하는 잡을 관리하므로, 잡 중에 내리면 그 잡의 관리가 끊긴다.
실행 직전 `kubectl get sparkapplications,flinkdeployments -A`로 확인한다.

**비가역 지점**: 기존 `lakehouse` 삭제와 스택 A·B의 첫 `apply`는 실행 전
`reviewer` 보안 체크리스트 + 사용자 승인([risk.md](risk.md) §4).

**자원**: [resource-sizing.md](resource-sizing.md) 배분 표에서 Dagster 행을 빼고 ArgoCD 5개 컴포넌트 행을 더한다.
값은 차트 기본값을 확인해 정하고, 실측은 관문 ①에서 재 볼트에 둔다.

## 8. 단계와 PR

| PR | 범위 | 완료 조건 |
| --- | --- | --- |
| 설계 | 이 문서 + PR1 구현 계획(PR1 머지와 함께 삭제) | 승인·머지됨 |
| PR1 | 스택 A·B, appset, ① 오퍼레이터 차트 4종, CI(helm 스텝·재귀 탐색), `k8s-env.sh` 가드, 철거 대상(§3), 문서 갱신 | 정적 게이트 + 관문 ①②③. 머지 시 계획 문서 삭제 |
| PR2 | ② `storage-external`·`catalog-postgres` 차트, `k8s-secrets.sh`, `k8s-poc-storage.sh` 철거, 클러스터 교체 | 관문 ⓪①④ |
| PR3-A | Airflow 커스텀 이미지(`images/airflow/`) + GHCR 발행 워크플로 | 태그 발행 후 인증 없이 pull |
| PR3-B | ③ Airflow 차트(CNPG 메타DB) + values 태그 갱신(bump) 브랜치 push | [설계](superpowers/specs/2026-10-11-airflow-argocd-design.md) |

PR마다 함께 갱신할 단일 출처: [setup.md](setup.md) §3·[setup/local-k8s.md](setup/local-k8s.md)(부트스트랩),
[architectures/terraform.md](architectures/terraform.md)(스택 분할),
신규 `architectures/argocd.md`(채택 ✅·대안), [conventions/k8s.md](conventions/k8s.md) §7(packaging은 Helm — 비로소 실재와 일치),
[resource-sizing.md](resource-sizing.md), `CLAUDE.md` 인프라 요약 · `AGENTS.md`.

## 9. 받아들인 결과 · 열린 위험 · 기각

- **Airflow(③)가 서기 전까지 클러스터 안에서 도는 오케스트레이터가 없다.**
  데이터 재적재 경로(`raw_assets.py` → 적재 → dbt)는 Dagster 정의라, 그 사이 재적재가 필요하면
  **호스트 Dagster**(`host-dagster` profile)로 돌린다. 이 경로가 새 클러스터의 SeaweedFS·카탈로그에 붙는지는
  PR2 관문 ④에서 확인한다.
- **수렴 순서가 비결정적**이라 첫 sync에서 일시적 Degraded·재시도 로그가 정상이다 — 관문 ①은 최종 상태로만 판정한다.
- **겹 1의 List 원소 제거 적용 여부 미확인**(§4 — 관문 ③에서 잔존은 관측했으나 기제는 못 갈랐다)은
  데이터 층 앱 제거 금지로 완화한다. 재검토 트리거: 템플릿에 `resources-finalizer`를 다는 변경.
- **Spark·Flink CRD는 sync 시점 prune에 무방비**(§4 겹 3의 선언된 공백) — 겹 1은 Application 삭제만 막으므로
  업스트림 차트 갱신으로 CRD가 템플릿에서 빠지면 prune이 CRD를 지우고 그 CR이 연쇄 삭제될 수 있다.
  현재는 그 CR이 온디맨드 컴퓨트(D7)라 데이터 손실이 아니라 **실행 중 잡 소실**로 끝나는 것을 수용한다.
  차트 버전을 올리는 PR에서는 `helm template` 비교로 CRD 증감을 본다.
  **재검토 트리거**(조건): 두 오퍼레이터의 CR이 상태·데이터를 담게 될 때, 업스트림 차트가 CRD 어노테이션 키를
  추가할 때, 또는 관문 ①·③에서 Helm `crds/`가 prune 대상임이 관측될 때.
- **기각·보류**(빠뜨린 것과 구분하려고 적는다):
  AppProject 분리 — 보류. 사용자·저장소가 하나라 격리 대상이 없다. `sourceRepos`를 좁히는 효과는 ③ 단계에서 재판단.
  Sync Windows·Notifications·SSO·RBAC — 기각. 로컬 단일 사용자이고, 읽는 사람 없는 알림은 관측이 아니다.

## 참고 출처

`argocd-study` (자매 저장소)

- `docs/superpowers/specs/2026-10-07-applicationset-image-cd-design.md` §3 <!-- date-ok -->
  — 소유권 표, "Terraform이 ArgoCD를 소유"
- `wiki/argocd-bootstrap.md` — CRD 선행 때문에 appset을 두 번째 `helm_release`로 분리한 근거
- `terraform/platform/charts/appset/templates/applicationset.yaml` — 블록 스타일 syncPolicy·Helm 이중 템플릿
  이스케이프(이식 당시 List generator)
- `terraform/platform/values/argocd.yaml.tftpl` — repo-server probe 타임아웃 교정

외부 1차 출처(A등급, stable 문서)

- **R1** Argo CD, *Sync Options* — §Server-Side Apply · §Skip Dry Run for New Custom Resources Types ·
  §No Prune Resources · §No Resource Deletion. https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/
- **R2** Argo CD, *ApplicationSet — Controlling Resource Modification*, §Preserving Resources on Deletion ·
  §Managed Applications modification Policies(`create-update`).
  https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Controlling-Resource-Modification/
- **R3** Argo CD, *Helm* (user guide) — Helm 주석 지원 표, `helm.sh/resource-policy: keep`.
  https://argo-cd.readthedocs.io/en/stable/user-guide/helm/
- **R4** cert-manager, Helm chart `values.yaml` — `crds.enabled`·`crds.keep`. cert-manager는 B3에서 철거돼
  현행 서술의 근거가 아니다 — 재도입 시 겹 3 판단의 근거로 남긴다.
  https://raw.githubusercontent.com/cert-manager/cert-manager/master/deploy/charts/cert-manager/values.yaml
- **R5** Apache Software Foundation, *Release Distribution Policy*. https://infra.apache.org/release-distribution.html
- **R6** CloudNativePG charts, `charts/cloudnative-pg/templates/crds/crds.yaml`·`values.yaml`(`crds.create`).
  https://github.com/cloudnative-pg/charts
- **R7** Argo CD, *ApplicationSet — Progressive Syncs* — 활성화 파라미터, RollingSync steps,
  생성 Application의 자동 sync 해제.
  https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Progressive-Syncs/

교차 검토: `argocd-expert` 스킬(C등급 — 모범사례 목록). §5 앱 내부 wave, §7 관문 ⓪, §9 기각 목록의 계기가 됐고
판단 근거는 위 A등급 출처와 이 저장소 규칙이다.
