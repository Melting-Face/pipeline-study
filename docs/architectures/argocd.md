# ArgoCD (아키텍처 · 프로젝트 관점)

## 이 프로젝트에서의 역할

`gitops/`의 선언으로 클러스터 앱을 수렴시킨다.

## 어떻게 도나

- 설치: 스택 B `terraform/platform/argocd.tf`, ApplicationSet은
  `terraform/platform/charts/appset/templates/applicationset.yaml`.
- 앱 목록: `gitops/apps/<app>/config.yaml`(디렉터리 하나 = 앱 하나), 차트는 `gitops/charts/<app>/`.
- 추적 리비전: `terraform/platform/variables.tf`의 `target_revision`.
- Secret은 앱보다 먼저 `scripts/k8s-secrets.sh`가 만든다.

## 개요

Argo CD는 **Git을 원하는 상태의 단일 출처로 삼는 선언형 GitOps 컨트롤러**다. 저장소의 매니페스트(여기서는
Helm 차트)와 클러스터 실상태를 계속 대조해 차이를 수렴시키고(sync), 사람이 수동으로 바꾼 것을
되돌린다(selfHeal). 구성은 application-controller(대조·적용) · repo-server(차트 렌더) · server(UI·API) ·
applicationset-controller(Application 일괄 생성) · redis(캐시) 다섯이다.

- 이 저장소 고정: argo-cd 차트 **10.9.6**, ingress-nginx 차트 **4.15.1** — 자매 저장소 `argocd-study`가
  같은 VM에서 검증한 짝을 그대로 쓴다(상향은 두 저장소 공동 작업).

## 왜 이걸 골랐나 — ✅ 채택

**성공 기준은 「`git push`만으로 클러스터가 수렴한다」** 이다. 빈 클러스터에서 `terraform apply` 2회와
Secret 생성(`scripts/k8s-secrets.sh`) 뒤, 사람의 `kubectl apply` 없이
전 Application이 Synced·Healthy가 되어야 한다.
이전에는 셸·Terraform·수동 `kubectl`이 번갈아 클러스터를 세웠고(순서가 곧 전제), 오퍼레이터를 바꿀 때마다
`apply`가 필요했다. 설계 전문은 [../argocd-gitops.md](../argocd-gitops.md), 스택 분할은 [terraform.md](terraform.md).

### 채택 근거 — 순서 보장을 무엇으로 하는가 (D5)

앱 목록은 **ApplicationSet git files generator**가 `gitops/apps/*/config.yaml`에서 읽고(앱 추가 = 커밋 하나),
앱 사이 순서는 **RollingSync**가 설정 파일의 `tier`(`"0"` 오퍼레이터 → `"1"` 데이터 층 → `"2"` 워크로드)로,
같은 tier 안은 **재시도 수렴**으로, 앱 안 순서는 **sync-wave**로 보장한다.
같은 앱 안에서 웹훅이 준비돼야 받아들여지는 리소스는 `sync-wave: "1"`로 뒤에 둔다(지금 해당 앱은 없다 — 유일한 사례였던
cert-manager 로컬 CA는 B3에서 철거).
처음에는 `argocd-study`와 같은 List generator + 재시도였다 — 바꾼 이유와 수용한 대가(RollingSync가 생성
Application의 자동 sync를 끈다)는 [../argocd-gitops.md](../argocd-gitops.md) §5 「D5 개정 기록」.

### 대안 비교

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| **ApplicationSet(git files) + RollingSync(tier)** | ✅ 채택 | 앱 추가에 `terraform apply` 불필요, 앱 사이 순서를 선언으로. 대가: 생성 Application 자동 sync 해제(수용) |
| ApplicationSet(List, 원소 = Terraform `var.apps`) + 재시도 수렴 | 대체됨 | 앱 추가마다 apply. 순서 비결정 |
| app-of-apps + **앱 간** sync-wave | 🔎 기각 | 순서는 확정적이나 `argocd-study`가 걷어낸 루트 구조로 회귀 |
| Terraform `helm_release`로 오퍼레이터 유지 | 철거 | 빈 클러스터는 CRD 해석 때문에 2단 apply, 변경마다 `apply` |
| AppProject 분리 | 🔎 보류 | 사용자·저장소가 하나라 격리 대상이 없다. `sourceRepos` 제한은 Airflow 단계에서 재판단 |
| Sync Windows·Notifications·SSO·RBAC | 🔎 기각 | 로컬 단일 사용자. 읽는 사람 없는 알림은 관측이 아니다 |
| Secret을 Git에 두기(sealed 등) | 🔎 기각 | 저장소가 public — Git엔 이름만, 값은 `scripts/k8s-secrets.sh`로 수동 생성 |
| Spark Connect·Flink 세션을 ArgoCD로 | 🔎 기각 | 온디맨드 기동·회수를 selfHeal이 되돌린다 |

### 데이터 보호 — 4겹 (요약)

재적재는 공짜가 아니므로 평상시 실수로 잃지 않게 네 겹을 둔다: ⓪ ApplicationSet `applicationsSync: create-update`
(설정 파일 삭제·generator 0건이 Application 삭제로 번지지 않음, R2) ① ApplicationSet
`preserveResourcesOnDeletion`(Application 삭제 방지, R2) ② 상태 리소스의 `Prune=false,Delete=false`(R1)
③ CRD의 `helm.sh/resource-policy: keep`(R3·R6). **Spark·Flink 오퍼레이터는 CRD 어노테이션을 넣을 values
키가 없어 ③이 선언된 공백**이며, sync 시점 prune에는 ①이 닿지 않는다 — 수용 근거와 재검토 트리거는
[../argocd-gitops.md](../argocd-gitops.md) §4·§9에 있다.

## 운영 메모

- **ArgoCD UI**는 `http://argocd.localtest.me:8080`(ingress-nginx는 스택 B가 설치 — ArgoCD가 자기 진입로를
  소유하면 닭-달걀). dex·notifications는 끈다.
- **appset은 두 번째 `helm_release`** — argo-cd 차트의 CRD가 `templates/`에 있어 한 릴리스에 CR을 같이 두면
  Helm이 GVK를 해석하다 실패한다([terraform.md](terraform.md)).
- **Flink 오퍼레이터 차트 저장소는 `archive.apache.org`로 고정**한다 — Apache 정책상 개발이 끝난 버전의 링크는
  `downloads.apache.org`에서 지워질 수 있다(R5).
- repo-server probe `timeoutSeconds: 5`는 VM 공유 시 일시적 지연으로 kubelet이 재시작하는 것을 막는 교정이다.
- 자원 값은 [../resource-sizing.md](../resource-sizing.md) 표(시작값, 실측은 수렴 관문에서).
- 관측: healthcheck·로그는 K8s 기본 경로를 쓰고 ArgoCD 전용 메트릭 수집기는 **두지 않는다**(계측 대상 없는
  수집기 금지 — [../conventions/monitoring.md](../conventions/monitoring.md)).

## 교훈

- [ArgoCD로 클러스터를 수렴시키며 — 앱 목록은 Git이, 순서는 tier가 쥔다](../../wiki/argocd-bootstrap.md)

## 참고

외부 1차 출처(A등급, stable 문서)는 [../references.md](../references.md)에 단일 관리한다 —
R1 Sync Options · R2 ApplicationSet Controlling Resource Modification · R3 Argo CD Helm ·
R4 cert-manager 차트 values(철거된 앱 — 재도입 시 근거) · R5 ASF Release Distribution Policy · R6 CloudNativePG charts.
각 출처가 어느 결정을 받치는지는 [../argocd-gitops.md](../argocd-gitops.md) §참고 출처가 정본이다.
자매 저장소 `argocd-study`의 소유권 표·appset 템플릿·repo-server probe 교정을 재사용했다.
