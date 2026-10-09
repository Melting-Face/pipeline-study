# Terraform (아키텍처 · 프로젝트 관점)

## 개요

Terraform은 **선언형 인프라 프로비저닝** 도구다. 리소스의 목표 상태를 HCL로 선언하면 실제 상태를
읽어(refresh) 차이를 계산하고(plan) 그만큼만 수렴시킨다(apply). 셸 스크립트와 갈리는 지점은 셋이다 —
**멱등성이 리소스 모델에 내장**되고, **의존 그래프가 실행 순서를 대체**하며, **선언과 실측의 차이를
`plan`이 보여준다**.

- 이 저장소 고정: Terraform **1.15.8** · 스택별 프로바이더는 `versions.tf`에 핀 — 스택 A·B(`cluster/kind`·`platform`)는
  **정확 고정**(`version = "x.y.z"`), 보류 중인 `oci-k3s`만 `~>` 범위다

## 이 프로젝트에서의 위치 — ✅ 채택(스택 A·B, ArgoCD 부트스트랩)

스택은 셋이다. 클라우드 축 [`terraform/oci-k3s/`](oci.md)는 **⏸ 보류**(A1 용량 부족)이고,
이 문서가 다루는 것은 **로컬 K8s 플랫폼**의 두 스택이다.

| 스택 | 경로 | 내용 | `destroy` 시 |
| --- | --- | --- | --- |
| **A. cluster** | `terraform/cluster/kind/` | kind 클러스터 · containerd 레지스트리 설정 · 전용 kubeconfig | 클러스터와 그 안의 PVC 전부 소멸 |
| **B. platform** | `terraform/platform/` | ingress-nginx · argo-cd · ApplicationSet(`var.apps`) | ArgoCD와 그 앱 정의 소멸(앱이 만든 워크로드는 보호 겹이 따로 있다) |
| 그 아래 | `gitops/charts/<app>/` | 오퍼레이터 이하 전부 — **ArgoCD가 소유**, Terraform이 아니다 | — |

지켜지지 않던 **「셸 유지」 결정을 뒤집었다.** 이전 판은 클러스터(A)·데이터(B)를 셸에 남기기로 했다 —
`kind_cluster`가 import를 지원하지 않아 Terraform 채택이 곧 클러스터 재생성이고, 그 비용이 데이터
재적재였기 때문이다. 뒤집은 근거는 사용자 결정 두 가지다([`../argocd-gitops.md`](../argocd-gitops.md) §1):
ⓐ 클러스터를 Terraform으로 **재생성**한다 ⓑ 기존 데이터는 **폐기하고 원천에서 재적재**한다.
재생성 비용을 받아들였으므로 import 불가는 더 이상 장애가 아니다.
⚠️ 근거가 바뀌면 결론도 다시 유도한다 — 이전 판은 비용 수치의 라벨 오류를 교정하고도 결론이 같았고,
그것은 다시 유도했기 때문이지 결론이 안 바뀌어서가 아니다.

분할 축은 여전히 **destroy가 무엇을 파괴하는가(폭발반경)** 이다. 다만 데이터 층은 셸이 아니라
ArgoCD가 소유하는 쪽으로 옮겨 가며(PR2), 그 전까지 `scripts/k8s-poc-storage.sh`가 과도기로 남는다.
Terraform은 **ArgoCD까지** 설치하고 그 위는 `git push`로 수렴한다(`argocd-study`와 같은 경계 — 선택 근거는
[argocd.md](argocd.md)).

### 대안 비교

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| **A·B Terraform + 나머지 ArgoCD** | ✅ 채택 | 클러스터 수명주기를 선언으로 두고, 운영은 push로 수렴 |
| 이전 판: 오퍼레이터만 Terraform(`helm_release`) | 철거 | 빈 클러스터에서 CRD 해석 때문에 2단 apply, 변경마다 `apply` 필요 |
| 셸 유지 + 멱등성 보강 | 🔎 미채택 | 3상태 분기(실행/중지/부재)를 손으로 계속 짜야 한다. 다음 상태 축이 나오면 또 샌다 |

### 실제 구성

```text
terraform/cluster/kind/   스택 A — kind 클러스터 · 레지스트리 설정 · 전용 kubeconfig 출력
terraform/platform/       스택 B — ingress-nginx · argo-cd · appset(두 번째 helm_release)
  charts/appset/          ApplicationSet 하나(List generator)
  values/argocd.yaml.tftpl
gitops/charts/<app>/      ArgoCD가 sync하는 대상(앱 하나 = umbrella 차트 하나)
```

스택 B가 appset을 **두 번째 `helm_release`로 분리**한 이유는 argo-cd 차트가 CRD를 `crds/`가 아닌
`templates/`에 담아, 한 릴리스에서 CRD와 그 CR을 함께 만들면 Helm이 GVK를 해석하다 실패하기 때문이다.
`kubernetes_manifest`는 plan 시점에 CRD 스키마를 조회해 최초 plan이 죽으므로 쓰지 않는다.
YAML 매니페스트를 `yamldecode`로 감싸 적용하던 방식은 폐기됐다 — 매니페스트가 `gitops/charts/`의
차트 템플릿으로 가고 주석("왜 이 값인가")도 함께 간다.

## 운영 메모

> 이 아래 운영 메모 전체(CRD 폭발반경 · `helm_release` 인수 · 불통 시 `plan` 등)는 철거된
> `terraform/lakehouse-platform` 스택 기준이며, 현행 스택 A·B는 `kubernetes_manifest`를 쓰지 않는다.
> 특히 **CRD 폭발반경**과 **`helm_release` 인수** 두 절은 철거된 스택(`terraform/lakehouse-platform/`,
> 오퍼레이터를 `helm_release`로 설치)에서 얻은 실측이다. 그 스택은 없지만 교훈은 남긴다 — 오퍼레이터
> 삭제가 CRD와 CR을 연쇄 삭제하는 경로는 ArgoCD에서도 같고, 그 방어는 [argocd.md](argocd.md)의 3겹이다.

### 🔴 폭발반경은 스택 경계를 새어 나간다 — CRD

C를 "안전하게 destroy 가능"으로 두려면 **오퍼레이터 uninstall이 CRD를 지우지 않아야 한다.**
CNPG 차트가 CRD를 함께 제거하면 `Cluster` CR이 사라지고 **B의 PVC가 따라간다.**

✅ **일부러 `destroy`를 돌려 확인했다**(버리는 클러스터). (철거된) 스택 C가 통째로 파괴된 직후:

- **PVC 2개의 UID가 불변**이고 `Cluster/catalog-postgres`(스택 B)가 healthy로 남았다
- CNPG 오퍼레이터 Deployment는 **사라졌다**(C의 것이므로 정상)
- `clusters`·`databases` CRD는 **남았다** — 근거는 `helm.sh/resource-policy: keep`
- `Database/dagster` CR이 사라졌는데도 **`dagster` DB와 그 안의 데이터는 살아남았다**
  (`databaseReclaimPolicy: retain`)

> **부정 결과에는 관측 경로 생존을 함께 본다.** "PVC가 살아남았다"는 아무 일도 안 일어난 상태라
> 관측이 죽어도 똑같이 보인다. 그래서 같은 순간에 ① `get pvc`가 응답하고 ② `get database`가
> **정확히 `NotFound`를 보고하며**(=API가 살아 있고 삭제는 실제로 일어났다) ③ DB 안의 행을
> **실제로 `SELECT`해 읽는다**(=볼륨이 마운트돼 데이터가 읽힌다). 셋이 함께여야 성립한다.

이어서 `apply`로 **전량이 단일 단계로 복구**됐다(`-target` 불필요 — CRD가 남아 GVK가 해석된다).
⇒ **2단계 apply는 빈 클러스터 최초 1회 한정**이라는 명제가 닫혔다.
그리고 `ensure: present`는 기존 DB를 **재초기화하지 않고 인수**했다(재생성 후에도 같은 행이 읽혔다).

⚠️ **이 안전은 우리 설정이 아니라 업스트림 차트가 준 것**이다. `keep` 애노테이션은 확인한 차트
버전에 한하므로 **버전을 올릴 때 다시 본다.** 또 destroy 구간에는 오퍼레이터가 없어
**failover·백업·WAL 아카이빙이 죽어 있다** — "안전하다"가 "아무 손실이 없다"는 뜻은 아니다.

### `helm_release` 인수는 `0 to change`에 도달하지 못한다

🔴 **이 문서 초판이 "`plan`이 `0 to change`여야 인수가 끝난 것"이라 적은 것은 틀렸다**
(실측). helm은 릴리스에 **병합된 값만** 저장하고 *어느 저장소에서 받았는지*와
*값이 `--set`으로 왔는지 `--values`로 왔는지*는 저장하지 않는다. 그래서 import 직후
`repository`·`set`·`values`가 **항상 diff로 남는다** — HCL이 틀려서가 아니라 복원 불가능한
설정 전용 속성이기 때문이다. 세 릴리스 모두 같은 형태였다.

**그래서 판정 기준은 둘로 나뉜다.**

1. **기능 속성에 diff가 없을 것** — `chart`·`version`·`namespace`·`name`. 여기가 움직이면
   진짜로 어긋난 것이다.
2. **렌더 결과가 같을 것** — `helm get manifest <r> -n <ns>`(현재)와
   `helm template …`(제안)을 비교한다. Terraform의 `values` diff는 `metadata`를 통째로
   `known after apply`로 그리느라 **판정에 쓸 수 없다** — 현재 값이 제거되는 것처럼 보인다.

⚠️ **`create_namespace = true`를 먼저 걷어내야 이 판정이 가능하다.** import는 이 플래그를
state에 기록하지 않으므로 `true`면 인수 직후 항상 update-in-place가 계획되고, 그 여파로
**metadata 전체가 `known after apply`로 덮여 진짜 diff가 묻힌다.**
네임스페이스를 셸이 이미 만들었다면 `false`가 사실에 맞다.

실측 대조 결과 — 셋 다 **기능적으로 동일**했다:
CNPG는 EOF 빈 줄 1개, Flink는 후행 빈 줄 2개, Spark은 `helm.sh/hook: test` Pod만 차이였다
(`helm template`은 test 훅을 렌더하지만 릴리스 매니페스트에는 들어가지 않는다).
그 뒤 `apply` 1회로 정합을 맞췄고 **파드 재시작 0**(이름·재시작 횟수·생성시각 불변),
helm revision만 1씩 올랐다. 이후 `plan`은 `No changes`다.

**정합을 맞추는 `apply`를 생략하면 안 된다** — diff를 방치하면 `plan`이 영구히
`3 to change`를 보여주고 **진짜 drift가 그 잡음에 묻힌다**(이 이행의 주목적이 무력화된다).

가장 위험했던 것은 Flink Operator다 — `--set` 8종에 더해 값 파일도 썼는데, 거기에
**공급망 통제**(`user.artifacts.allowed-schemes`에서 https 제거)가 들어 있었다(지금은
`gitops/charts/flink-operator/values.yaml`). 빠뜨리면 차트 기본값으로 되돌아가 [resource-sizing.md](../resource-sizing.md) 배분표가 거짓이 되고
**런타임 외부 jar fetch 경로가 조용히 열린다.**

### 게이트는 `fmt`와 `validate`를 **다른 자리에** 둔다

규약(`conventions/terraform.md` §3)은 둘을 함께 커밋 전 게이트로 요구했으나 집행 수단이
없었고(terraform 훅 0개 — `terraform/oci-k3s/`는 한 번도 검사받은 적이 없다),
넣으려 보니 **같은 자리에 둘 수 없었다.**

- **`fmt` → pre-commit.** 네트워크가 필요 없다.
- **`validate` → CI 잡.** `validate`는 `init`이 선행돼야 하는데 `.terraform/providers`는
  gitignore라 저장소에 없다. 훅에서 `init`을 돌리면 **커밋이 프로바이더 레지스트리
  가용성에 묶인다** — `sqlfluff`의 `templater = "dbt"`에서 이미 겪은 함정이다.
  실측: worktree에서 `validate`가
  `no package for oracle/oci 6.37.0 cached in .terraform/providers`로 실패했다.
- **`plan`은 어디에도 두지 않는다.** 위 §불통 시 `plan` 참조.

CI 잡은 `terraform/*/`를 순회해 스택이 늘어도 자동으로 집고, **0건이면 통과가 아니라 실패**로
떨어뜨린다(디렉터리 구조가 바뀌었을 때 잡이 조용히 무의미해지는 것을 막는다).

### 프로바이더 실측

| 프로바이더 | 버전 | 확인된 것 |
| --- | --- | --- |
| `hashicorp/kubernetes` | 2.38 계열 | CR을 `yamldecode`로 `plan` 통과 · 타입 리소스 apply/destroy 왕복 |
| `hashicorp/helm` | 3.x | 해석·설치. 릴리스 관리는 미검증 |
| `kreuzwerker/docker` | 3.9.0 | **podman 소켓과 실통신** — 실 `kind` 네트워크 ID 회수 |
| `tehcyx/kind` | 0.11.0 | 해석·설치. **import 미지원**(에러 메시지로 확인) |

### 관측 경로를 엉뚱한 도구로 확인한 사례

"클러스터가 불통일 때 `plan`이 도는가"를 죽은 kubeconfig로 시뮬레이션했으나 **판정에 실패했다.**
네 번의 결과가 어느 가설로도 일관되게 설명되지 않았고, 원인은 관측 경로 확인의 대상이 틀린 것이었다 —
**`kubectl`이 그 파일로 실패한다는 것은 확인했지만, Terraform이 그 파일을 읽는다는 것은
확인한 적이 없다.** 도구 A로 검증한 조건을 도구 B에 그대로 적용한 셈이다.

⇒ 그래서 **노드를 실제로 정지시켜 다시 쟀다**(변인이 하나로 준다). 결과는 아래.

### 불통 시 `plan`은 실패한다 — 우회는 절반만 듣는다

실측(`podman stop lakehouse-control-plane` 후). 조용히 오도하지 **않는다** —
`Planning failed`로 에러를 내고 멈춘다. 다만 실패 지점이 **둘**이고 성질이 다르다.

| 축 | 증상 | `-refresh=false` |
| --- | --- | --- |
| state에 있는 리소스의 refresh | `Get .../namespaces/...: connection refused` | ✅ 사라진다 |
| **`kubernetes_manifest`의 GVK 해석** | `Invalid configuration for API client` — `Get .../apis` | ❌ **그대로 남는다** |

🔴 **이 문서가 앞서 적었던 "우회가 있어 설계를 막지 않는다"는 틀렸다.**
`kubernetes_manifest`는 refresh와 무관하게 `/apis`로 GVK를 해석해야 하므로,
**YAML을 `yamldecode`로 적용하는 이 설계에서는 `plan` 자체가 라이브 클러스터를 요구한다.**

실무상 치명적이지는 않다 — 죽은 클러스터에는 어차피 `apply`할 수 없다. 그러나 다음 둘이 따라온다.

- **CI에서 `plan`을 드라이런 게이트로 쓸 수 없다**(클러스터 없는 러너에서 돈다).
  문법·포맷 게이트는 `terraform fmt -check` + `validate`까지이고, 그 둘은 클러스터 없이 돈다.
- 클러스터가 내려간 상태에서 "선언이 뭐였더라"를 `plan`으로 확인할 수 없다. `terraform show`로 본다.

### `scripts/worktree-new.sh`가 붙이는 브랜치 — 축은 넷이다 (해소)

한때 `git worktree add "$DIR" -b "$BRANCH"` 한 줄이라 **항상 새 브랜치를 만들었다.** 이미 있는
브랜치를 다른 worktree로 여는 경로가 없어, 병렬 세션이 서로의 브랜치를 트리에서 밀어내는
상황에서 — 즉 이 스크립트가 존재하는 이유인 그 상황에서 — 정작 규약이 권하는 수단을 못 썼다.

**축을 다 세지 않으면 "분기 하나 넣으면 된다"로 닫힌다.** 그러면 세지 않은 축에서 여전히 raw git
에러가 나서 **고쳤다고 믿는데 안 고쳐진** 상태가 된다. 처음 셋으로 셌으나 실제로는 **넷**이었다.

| 상태 | 필요한 것 | 현재 |
| --- | --- | --- |
| 어디에도 없음 | `-b` | ✅ |
| 로컬에 있고 미점유 | `-b`를 **빼야** 함 | ✅ |
| **다른 worktree가 점유** | 분기가 아니라 **안내** — `git worktree add`가 거부한다 | ✅ |
| **원격에만 있음** | `origin/<B>`를 시작점으로 **명시** | ✅ |

넷째는 처음 세지 못했던 축이다. `-b`로 만들면 원격을 **추적하지 않는 별개 브랜치**가 조용히 생기고
(에러 없음), 시작점을 명시하지 않으면 DWIM이 `--guess-remote`·remote 개수에 따라 환경마다 갈린다.

⚠️ 판정 순서가 규칙이다 — **점유 여부를 먼저** 본다. 로컬 존재 검사를 앞에 두면 셋째 축이 둘째로
흡수돼 다시 raw git 에러가 된다. 원래 코드는 항상 `-b`를 붙였으므로 **둘째와 셋째를 같은 문구**
(`fatal: a branch named … already exists`)로 냈다 — 막히기는 했으나 왜 막혔는지는 알 수 없었다.

맨손 `git worktree add`로 우회하려면 `LINK_ASSETS` 2종
(`.env`·`.claude/settings.local.json`)을 **직접 링크**해야 한다 —
`settings.local.json`이 빠지면 권한 범위가 **조용히** 달라진다.

## 참고

외부 공식 문서는 [`../references.md`](../references.md)에 단일 관리한다 — URL을 여기 복제하지 않는다.
이 문서와 직접 관련된 항목: Terraform(providers · import · state) · Kubernetes/Helm 프로바이더 ·
kind · CloudNativePG. 규칙 정본은 [`../conventions/terraform.md`](../conventions/terraform.md).
